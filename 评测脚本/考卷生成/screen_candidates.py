# -*- coding: utf-8 -*-
"""用独立基线做冻结前的题目难度校准（item analysis）。

v3 的教训：36 道计分题里 28 道在两次运行中得分完全相同（14 道双满分、7 道双零分），
真正在工作的只有 8 道。天花板题必须在冻结前筛掉。

这里刻意用**不参与后续评测的**零依赖字符二元 BM25，在与具体产品无关的模拟分块上跑，
避免考卷过拟合到某个被测产品。它测的是"这题能不能靠字面重合捞到"——凡是 rank-1
就全中的，要么降级进 sanity 桶，要么重写。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CORE_PATH = Path(__file__).resolve().parents[1] / "retrieval_eval_v4.py"
CORE_SPEC = importlib.util.spec_from_file_location("retrieval_eval_v4_core", CORE_PATH)
if CORE_SPEC is None or CORE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载评测器: {CORE_PATH}")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BM25_K1 = 1.5
BM25_B = 0.75


def bigrams(text: str) -> List[str]:
    """中文场景下字符二元组比分词更稳，且零依赖。"""
    value = core.normalize_text(text)
    return [value[i : i + 2] for i in range(len(value) - 1)] if len(value) > 1 else [value]


def build_chunks(
    corpus: Dict[str, str], window: int, overlap: int
) -> List[Dict[str, Any]]:
    """在归一化文本上做与产品无关的定长滑窗分块。"""
    step = max(1, window - overlap)
    chunks = []
    for name, text in corpus.items():
        normalized = core.normalize_text(text)
        for start in range(0, max(1, len(normalized)), step):
            piece = normalized[start : start + window]
            if not piece:
                continue
            chunks.append({"document": name, "start": start, "text": piece})
            if start + window >= len(normalized):
                break
    return chunks


class BM25:
    def __init__(self, chunks: Sequence[Dict[str, Any]]) -> None:
        self.chunks = list(chunks)
        self.tokens = [bigrams(chunk["text"]) for chunk in self.chunks]
        self.lengths = [len(item) for item in self.tokens]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        self.frequencies = [Counter(item) for item in self.tokens]
        document_frequency: Counter = Counter()
        for counter in self.frequencies:
            document_frequency.update(counter.keys())
        total = len(self.chunks)
        self.idf = {
            token: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for token, count in document_frequency.items()
        }

    def rank(self, query: str, limit: int) -> List[Tuple[int, float]]:
        query_tokens = bigrams(query)
        scores = []
        for index, counter in enumerate(self.frequencies):
            score = 0.0
            length = self.lengths[index] or 1
            for token in query_tokens:
                frequency = counter.get(token)
                if not frequency:
                    continue
                denominator = frequency + BM25_K1 * (
                    1 - BM25_B + BM25_B * length / self.avg_length
                )
                score += self.idf.get(token, 0.0) * frequency * (BM25_K1 + 1) / denominator
            if score > 0:
                scores.append((index, score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:limit]


def screen(exam: Dict[str, Any], window: int, overlap: int, top_k: int) -> Dict[str, Any]:
    corpus_dir = PROJECT_ROOT / exam["exam_meta"]["corpus"]["relative_dir"]
    corpus = {
        item["name"]: (corpus_dir / item["name"]).read_text(encoding="utf-8")
        for item in exam["exam_meta"]["corpus"]["documents"]
    }
    chunks = build_chunks(corpus, window, overlap)
    index = BM25(chunks)

    rows = []
    for question in exam["questions"]:
        if not question["claims"]:
            continue
        ranked = index.rank(question["question"], top_k)
        first_hits = []
        for claim in question["claims"]:
            spans = [core.normalize_text(span) for span in claim["accepted_spans"]]
            hit_rank = None
            for rank, (chunk_index, _) in enumerate(ranked, 1):
                chunk = chunks[chunk_index]
                if chunk["document"] != claim["source_document"]:
                    continue
                if any(span and span in chunk["text"] for span in spans):
                    hit_rank = rank
                    break
            first_hits.append(hit_rank)
        hits = sum(1 for rank in first_hits if rank is not None)
        recall = hits / len(first_hits)
        # G13 桥接不可直达：真多跳要求「题面捞不到桥接文档、但捞得到端点文档」。
        # 桥接一旦能被题面直接召回，就说明不必先读端点拿中间实体，这一跳是假的。
        bridge_reachable, endpoint_unreachable = [], []
        design = question.get("hop_design")
        if design:
            reached = {chunks[i]["document"] for i, _ in ranked}
            bridge_reachable = sorted(reached & set(design["bridge_documents"]))
            endpoint_unreachable = sorted(set(design["endpoint_documents"]) - reached)
        rows.append({
            "bridge_reachable_from_query": bridge_reachable,
            "endpoint_unreachable_from_query": endpoint_unreachable,
            "id": question["id"],
            "question_role": question["question_role"],
            "primary_type": question["primary_type"],
            "difficulty": question["difficulty"],
            "cluster_id": question["cluster_id"],
            "baseline_claim_recall": round(recall, 4),
            "first_hit_ranks": first_hits,
            "low_lexical_overlap": "low_lexical_overlap" in question["tags"],
            "trivial": bool(recall == 1.0 and all(
                rank is not None and rank <= 3 for rank in first_hits
            )),
            "floor": recall == 0.0,
        })

    scored = [row for row in rows if row["question_role"] == "scored"]
    chain_rows = [row for row in rows if row["primary_type"] == "cross_doc_chain"]
    return {
        "baseline": f"char-bigram BM25 / window={window} / overlap={overlap} / top_k={top_k}",
        "chain_questions": len(chain_rows),
        "g13_bridge_directly_reachable": [
            {"id": r["id"], "documents": r["bridge_reachable_from_query"]}
            for r in chain_rows if r["bridge_reachable_from_query"]
        ],
        "g13_endpoint_unreachable": [
            {"id": r["id"], "documents": r["endpoint_unreachable_from_query"]}
            for r in chain_rows if r["endpoint_unreachable_from_query"]
        ],
        "num_questions": len(rows),
        "mean_baseline_recall": round(
            sum(r["baseline_claim_recall"] for r in scored) / len(scored), 4
        ) if scored else 0.0,
        "trivial_scored_questions": [r["id"] for r in scored if r["trivial"]],
        # 词法基线在两类题上拿 0 都是设计意图：low_lexical_overlap 题就是要逼语义检索，
        # cross_doc_chain 题的桥接文档按定义就无法被题面直接召回。只有这两类之外仍然
        # 零分的题，才值得怀疑是题目本身有缺陷。
        "floor_by_design": [
            r["id"] for r in scored
            if r["floor"] and (r["low_lexical_overlap"] or r["primary_type"] == "cross_doc_chain")
        ],
        "floor_suspicious": [
            r["id"] for r in scored
            if r["floor"] and not r["low_lexical_overlap"]
            and r["primary_type"] != "cross_doc_chain"
        ],
        "mean_recall_low_lexical_overlap": round(
            sum(r["baseline_claim_recall"] for r in scored if r["low_lexical_overlap"])
            / max(1, sum(1 for r in scored if r["low_lexical_overlap"])), 4
        ),
        "mean_recall_plain": round(
            sum(r["baseline_claim_recall"] for r in scored if not r["low_lexical_overlap"])
            / max(1, sum(1 for r in scored if not r["low_lexical_overlap"])), 4
        ),
        "by_type": {
            name: round(
                sum(r["baseline_claim_recall"] for r in scored if r["primary_type"] == name)
                / max(1, sum(1 for r in scored if r["primary_type"] == name)), 4
            )
            for name in sorted({r["primary_type"] for r in scored})
        },
        "questions": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="用独立词法基线筛天花板题")
    parser.add_argument("--exam", required=True)
    parser.add_argument("--window", type=int, default=200)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--check-bridge-unreachable", action="store_true",
        help="G13：要求链式题的桥接文档无法被题面直接召回、端点文档必须能召回",
    )
    parser.add_argument(
        "--max-trivial-share", type=float, default=0.2,
        help="计分题中允许的天花板题占比上限，超过则返回非零退出码",
    )
    args = parser.parse_args(argv)

    try:
        exam = json.loads(Path(args.exam).expanduser().read_text(encoding="utf-8"))
        report = screen(exam, args.window, args.overlap, args.top_k)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(f"[筛查失败] {exc}\n")
        return 2

    scored_total = sum(1 for r in report["questions"] if r["question_role"] == "scored")
    trivial = len(report["trivial_scored_questions"])
    share = trivial / scored_total if scored_total else 0.0
    print(f"[基线] {report['baseline']}")
    print(f"[基线召回] 计分题平均 {report['mean_baseline_recall']}；分类型 {report['by_type']}")
    print(f"[天花板题] {trivial}/{scored_total} ({share:.1%}) → {report['trivial_scored_questions']}")
    print(
        f"[词面重合分层] low_lexical_overlap 题均值 {report['mean_recall_low_lexical_overlap']}"
        f" vs 其余 {report['mean_recall_plain']}（差距越大说明标签越名副其实）"
    )
    print(f"[零分-符合设计] {len(report['floor_by_design'])} 道低词面重合题，词法基线抓不到属预期")
    print(f"[零分-可疑] {report['floor_suspicious'] or '无'}（未打低词面重合标签却仍零分，需人工复核）")

    reachable = report["g13_bridge_directly_reachable"]
    unreachable = report["g13_endpoint_unreachable"]
    if report["chain_questions"]:
        print(f"[G13 多跳可证伪性] 链式题 {report['chain_questions']} 道")
        print(f"   桥接被题面直接召回（应为空）: {reachable or '无'}")
        print(f"   端点题面捞不到（应为空）: {unreachable or '无'}")

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[明细] {out_path}")

    failed = False
    if share > args.max_trivial_share:
        sys.stderr.write(
            f"[区分度不足] 天花板题占比 {share:.1%} 超过 {args.max_trivial_share:.0%}，"
            "这些题需要降级进 sanity 桶或重写\n"
        )
        failed = True
    if args.check_bridge_unreachable and (reachable or unreachable):
        sys.stderr.write(
            "[G13 未通过] 多跳设计不成立："
            + (f"桥接可被题面直接召回 {reachable}；" if reachable else "")
            + (f"端点题面捞不到 {unreachable}" if unreachable else "")
            + "\n"
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
