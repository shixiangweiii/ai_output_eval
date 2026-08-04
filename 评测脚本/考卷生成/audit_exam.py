# -*- coding: utf-8 -*-
"""考卷质量闸门：在评测器校验之外，加严到"高质量问题 + 高质量答案"的标准。

评测器的 ``load_and_validate_exam`` 保证的是"结构合法、span 存在且唯一"；本脚本
额外保证的是"题目之间独立、证据真的能判别、答案与证据一致、答案没写在题面里"。
两者一起跑才算过关。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

CORE_PATH = Path(__file__).resolve().parents[1] / "retrieval_eval_v4.py"
CORE_SPEC = importlib.util.spec_from_file_location("retrieval_eval_v4_core", CORE_PATH)
if CORE_SPEC is None or CORE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载评测器: {CORE_PATH}")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASCII_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_QUERY_SPAN_JACCARD = 0.4
# 链式题各 claim 的证据必须彼此不同：参考多跳考卷实测 0.030，v4 初版假多跳题是 0.783
MAX_CHAIN_SPAN_JACCARD = 0.35


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _corpus(exam: Dict[str, Any]) -> Dict[str, str]:
    corpus_dir = PROJECT_ROOT / exam["exam_meta"]["corpus"]["relative_dir"]
    return {
        item["name"]: (corpus_dir / item["name"]).read_text(encoding="utf-8")
        for item in exam["exam_meta"]["corpus"]["documents"]
    }


def _fact_values(ledger: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    if not ledger:
        return {}
    return {fact["fact_id"]: fact.get("values", {}) for fact in ledger["facts"]}


def _fact_family(ledger: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not ledger:
        return {}
    return {fact["fact_id"]: fact.get("family", "") for fact in ledger["facts"]}


def gate_span_uniqueness(exam, corpus, _ledger) -> List[str]:
    """G1：每个 span 在其源文档内恰好出现一次（asset 放宽为告警）。"""
    problems = []
    for question in exam["questions"]:
        for claim in question["claims"]:
            normalized_doc = core.normalize_text(corpus[claim["source_document"]])
            for span in claim["accepted_spans"]:
                count = normalized_doc.count(core.normalize_text(span))
                if count != 1 and claim["kind"] == "text":
                    problems.append(
                        f"G1 {claim['id']}: span 在 {claim['source_document']} 出现 {count} 次: {span!r}"
                    )
    return problems


def gate_span_separation(exam, corpus, _ledger, minimum: int) -> List[str]:
    """G2：同一文档内不同题目的 span 必须拉开距离，否则一个块同时命中多题。"""
    positions: Dict[str, List[Any]] = defaultdict(list)
    for question in exam["questions"]:
        for claim in question["claims"]:
            text = corpus[claim["source_document"]]
            for span in claim["accepted_spans"]:
                index = text.find(span)
                if index >= 0:
                    positions[claim["source_document"]].append((index, question["id"]))
    problems = []
    for document, items in positions.items():
        items.sort()
        for (left_pos, left_q), (right_pos, right_q) in zip(items, items[1:]):
            if left_q != right_q and right_pos - left_pos < minimum:
                problems.append(
                    f"G2 {document}: {left_q} 与 {right_q} 的 span 相距 "
                    f"{right_pos - left_pos} 字，低于 {minimum}"
                )
    return problems


def gate_discriminating_value(exam, _corpus, ledger) -> List[str]:
    """G3：GT span 必须承载该事实的判别值，不能是兄弟文档共享的样板句。"""
    values = _fact_values(ledger)
    if not values:
        return []
    problems = []
    for question in exam["questions"]:
        for claim in question["claims"]:
            if claim["kind"] != "text":
                continue
            value = values.get(claim.get("fact_id"), {}).get(claim["source_document"])
            if not value:
                continue
            needle = core.normalize_text(value)
            if not any(needle in core.normalize_text(span) for span in claim["accepted_spans"]):
                problems.append(
                    f"G3 {claim['id']}: 没有任何 span 含判别值 {value!r}，无法区分兄弟文档"
                )
    return problems


def gate_negative_is_a_real_distractor(exam, _corpus, ledger) -> List[str]:
    """G4：硬负例不能来自正例文档，且同一事实下取值必须不同。

    多跳题的正当负例来自"假桥接"文档（另一个族），因此这里不再要求负例与正例同族——
    那条约束只适用于兄弟文档式的消歧题，对实体桥接链会误伤。
    """
    values = _fact_values(ledger)
    if not values:
        return []
    problems = []
    for question in exam["questions"]:
        positive_docs = {c["source_document"] for c in question["claims"]}
        for negative in question.get("negative_evidence", []):
            fact_id = negative.get("fact_id")
            document = negative["source_document"]
            if document in positive_docs:
                problems.append(f"G4 {negative['id']}: 负例与正例来自同一文档 {document}")
            fact_values = values.get(fact_id, {})
            negative_value = fact_values.get(document)
            positive_values = {
                fact_values.get(doc) for doc in positive_docs if doc in fact_values
            }
            if negative_value and negative_value in positive_values:
                problems.append(
                    f"G4 {negative['id']}: 负例取值 {negative_value!r} 与正例相同，不构成干扰"
                )
    return problems


def gate_bridge_name_isolation(exam, corpus, _ledger) -> List[str]:
    """G10：桥接文档不得出现任何端点实体，否则题面能直接命中它，这一跳就是假的。"""
    problems = []
    for question in exam["questions"]:
        design = question.get("hop_design")
        if not design:
            continue
        entities = design["endpoint_entities"]
        for bridge in design["bridge_documents"]:
            leaked = [
                e for e in entities
                if core.normalize_text(e) in core.normalize_text(corpus[bridge])
            ]
            if leaked:
                problems.append(
                    f"G10 {question['id']}: 桥接文档 {bridge} 泄露端点实体 {leaked}"
                )
    return problems


def gate_chain_evidence_is_heterogeneous(exam, _corpus, _ledger, ceiling: float) -> List[str]:
    """G11：链式题各 claim 的证据必须彼此不同。

    v4 最初那 9 道假多跳题的 span 互相似度高达 0.783（同一句话只换了数字），一次语义
    匹配即可全部命中；参考多跳考卷是 0.030。互相似度过高就说明根本不需要跳。
    """
    problems = []
    for question in exam["questions"]:
        if question["primary_type"] != "cross_doc_chain":
            continue
        firsts = [c["accepted_spans"][0] for c in question["claims"]]
        pairs = [
            (core.char_ngram_jaccard(a, b), a, b)
            for index, a in enumerate(firsts) for b in firsts[index + 1:]
        ]
        worst = max(pairs, default=(0.0, "", ""))
        if worst[0] > ceiling:
            problems.append(
                f"G11 {question['id']}: 两条证据字面重合度 {worst[0]:.3f} 超过 {ceiling}"
                f"，实为并行召回而非多跳（{worst[1][:18]}… / {worst[2][:18]}…）"
            )
    return problems


def gate_hop_role_consistency(exam, _corpus, _ledger) -> List[str]:
    """G12：claim 的 hop_role 必须与其文档在 hop_design 里的角色一致。"""
    problems = []
    for question in exam["questions"]:
        design = question.get("hop_design")
        if not design:
            continue
        role_map = {}
        for name in design["endpoint_documents"]:
            role_map[name] = "endpoint"
        for name in design["bridge_documents"]:
            role_map[name] = "bridge"
        for name in design.get("supporting_documents", []):
            role_map[name] = "supporting"
        counts: Counter = Counter()
        for claim in question["claims"]:
            expected = role_map.get(claim["source_document"])
            actual = claim.get("hop_role")
            if expected is None:
                problems.append(
                    f"G12 {claim['id']}: {claim['source_document']} 未在 hop_design 声明角色"
                )
            elif actual != expected:
                problems.append(
                    f"G12 {claim['id']}: hop_role={actual}，但文档角色是 {expected}"
                )
            counts[actual] += 1
        if counts["endpoint"] < 2 or counts["bridge"] < 1:
            problems.append(
                f"G12 {question['id']}: 链式题需要 ≥2 个端点 claim 与 ≥1 个桥接 claim，"
                f"实际 endpoint={counts['endpoint']} bridge={counts['bridge']}"
            )
    return problems


def gate_reference_answer_covers_claims(exam, _corpus, ledger) -> List[str]:
    """G5：参考答案必须包含每个 claim 的判别值——答案与证据一致。"""
    values = _fact_values(ledger)
    if not values:
        return []
    problems = []
    for question in exam["questions"]:
        answer = core.normalize_text(question.get("reference_answer", ""))
        if not answer:
            continue
        for claim in question["claims"]:
            if claim["kind"] == "asset":
                continue  # 资产 claim 的"取值"是图片 URL，不应要求写进参考答案
            value = values.get(claim.get("fact_id"), {}).get(claim["source_document"])
            if value and core.normalize_text(value) not in answer:
                problems.append(
                    f"G5 {question['id']}: 参考答案未覆盖 {claim['id']} 的判别值 {value!r}"
                )
    return problems


def gate_answer_not_leaked_in_question(exam, _corpus, ledger) -> List[str]:
    """G6：题面不得直接写出答案的判别值。"""
    values = _fact_values(ledger)
    if not values:
        return []
    problems = []
    for question in exam["questions"]:
        query = core.normalize_text(question["question"])
        for claim in question["claims"]:
            if claim["kind"] == "asset":
                continue
            value = values.get(claim.get("fact_id"), {}).get(claim["source_document"])
            if value and len(core.normalize_text(value)) >= 2 and core.normalize_text(value) in query:
                problems.append(
                    f"G6 {question['id']}: 题面里出现了答案判别值 {value!r}"
                )
    return problems


def gate_query_span_overlap(exam, _corpus, _ledger) -> List[str]:
    """G7：每题至少要有一条证据不是靠字面重合就能捞到的。"""
    problems = []
    for question in exam["questions"]:
        if not question["claims"]:
            continue
        overlaps = [
            core.char_ngram_jaccard(question["question"], span)
            for claim in question["claims"]
            for span in claim["accepted_spans"]
        ]
        if min(overlaps) > MAX_QUERY_SPAN_JACCARD:
            problems.append(
                f"G7 {question['id']}: 全部 span 与题面字面重合度过高"
                f"（最小 Jaccard={min(overlaps):.3f}）"
            )
    return problems


def gate_document_balance(exam, _corpus, _ledger) -> List[str]:
    """G8：claim 覆盖均衡 + 文件名 ASCII（跨产品文档名可控）。"""
    design = exam["exam_meta"]["design_constraints"]
    counts: Counter = Counter()
    for question in exam["questions"]:
        for claim in question["claims"]:
            counts[claim["source_document"]] += 1
    total = sum(counts.values())
    problems = []
    for item in exam["exam_meta"]["corpus"]["documents"]:
        name = item["name"]
        if not ASCII_NAME_RE.match(name):
            problems.append(f"G8 {name}: 文件名含非 ASCII 字符，跨产品文档名对齐有风险")
        if counts[name] < design["min_claims_per_document"]:
            problems.append(
                f"G8 {name}: 仅 {counts[name]} 个 claim，低于 {design['min_claims_per_document']}"
            )
        if total and counts[name] / total > design["max_claim_share_per_document"]:
            problems.append(
                f"G8 {name}: claim 占比 {counts[name] / total:.4f} 超过 "
                f"{design['max_claim_share_per_document']}"
            )
    return problems


def gate_cluster_size(exam, _corpus, _ledger, minimum: int) -> List[str]:
    """G9：每个 cluster 需要足够题数，聚类 bootstrap 才有意义。"""
    counts = Counter(
        q["cluster_id"] for q in exam["questions"] if q["question_role"] == "scored"
    )
    problems = []
    for cluster in exam["exam_meta"]["clusters"]:
        if counts[cluster["id"]] < minimum:
            problems.append(
                f"G9 {cluster['id']}: 只有 {counts[cluster['id']]} 道计分题，低于 {minimum}"
            )
    if len(counts) < 2:
        problems.append("G9: 计分题只落在一个 cluster 上，无法做聚类推断")
    return problems


def audit(
    exam: Dict[str, Any], ledger: Optional[Dict[str, Any]],
    min_separation: int, min_cluster_questions: int,
    max_chain_span_overlap: float = MAX_CHAIN_SPAN_JACCARD,
) -> List[str]:
    corpus = _corpus(exam)
    problems: List[str] = []
    problems += gate_span_uniqueness(exam, corpus, ledger)
    problems += gate_span_separation(exam, corpus, ledger, min_separation)
    problems += gate_discriminating_value(exam, corpus, ledger)
    problems += gate_negative_is_a_real_distractor(exam, corpus, ledger)
    problems += gate_reference_answer_covers_claims(exam, corpus, ledger)
    problems += gate_answer_not_leaked_in_question(exam, corpus, ledger)
    problems += gate_query_span_overlap(exam, corpus, ledger)
    problems += gate_document_balance(exam, corpus, ledger)
    problems += gate_cluster_size(exam, corpus, ledger, min_cluster_questions)
    problems += gate_bridge_name_isolation(exam, corpus, ledger)
    problems += gate_chain_evidence_is_heterogeneous(exam, corpus, ledger, max_chain_span_overlap)
    problems += gate_hop_role_consistency(exam, corpus, ledger)
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="v4 考卷质量闸门 G1–G12")
    parser.add_argument("--exam", required=True)
    parser.add_argument("--ledger", default=None, help="提供后可启用 G3–G6 判别值类检查")
    parser.add_argument("--min-separation", type=int, default=300)
    parser.add_argument("--min-cluster-questions", type=int, default=3)
    parser.add_argument("--max-chain-span-overlap", type=float, default=MAX_CHAIN_SPAN_JACCARD)
    args = parser.parse_args(argv)

    try:
        exam = _load(Path(args.exam).expanduser())
        ledger = _load(Path(args.ledger).expanduser()) if args.ledger else None
        problems = audit(
            exam, ledger, args.min_separation, args.min_cluster_questions,
            args.max_chain_span_overlap,
        )
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(f"[闸门执行失败] {exc}\n")
        return 2

    if not ledger:
        print("  [提示] 未提供 --ledger，已跳过 G3–G6（判别值相关检查）")
    for problem in problems:
        print(f"  {problem}")
    if problems:
        sys.stderr.write(f"[闸门未通过] 共 {len(problems)} 项\n")
        return 1
    print(f"[闸门通过] {len(exam['questions'])} 题全部满足 G1–G12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
