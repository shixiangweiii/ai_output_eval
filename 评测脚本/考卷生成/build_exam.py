# -*- coding: utf-8 -*-
"""把解析后的事实台账投影成 v4 考卷 JSON。

考卷是台账的确定性产物：claims / negative_evidence / evidence_chain / clusters /
question_counts / 语料 SHA-256 全部由本脚本机械生成，作者只在台账里写题面、
难度和引用了哪条事实。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

CORE_PATH = Path(__file__).resolve().parents[1] / "retrieval_eval_v4.py"
CORE_SPEC = importlib.util.spec_from_file_location("retrieval_eval_v4_core", CORE_PATH)
if CORE_SPEC is None or CORE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载评测器: {CORE_PATH}")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = Path(__file__).with_name("事实台账-2026-08-04-02-resolved.json")


class BuildError(ValueError):
    """台账无法投影成合法考卷。"""


def _fact_index(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index = {}
    for fact in ledger["facts"]:
        if fact["fact_id"] in index:
            raise BuildError(f"fact_id 重复: {fact['fact_id']}")
        index[fact["fact_id"]] = fact
    return index


def _resolve_reference(
    facts: Dict[str, Any], reference: Dict[str, Any], label: str
) -> Dict[str, Any]:
    fact = facts.get(reference.get("fact_id"))
    if fact is None:
        raise BuildError(f"{label} 引用了未定义的事实: {reference.get('fact_id')}")
    document = reference.get("document")
    entries = fact["locations"].get(document)
    if not entries:
        raise BuildError(f"{label} 引用的事实 {fact['fact_id']} 在 {document} 没有出现位置")
    if any("span" not in entry for entry in entries):
        raise BuildError(f"{label} 的台账尚未解析 span，请先跑 extract_spans.py")
    return {
        "fact": fact,
        "document": document,
        "section": entries[0].get("section", ""),
        "spans": [entry["span"] for entry in entries],
    }


def build_questions(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = _fact_index(ledger)
    chains = {c["id"]: c for c in ledger.get("chains", [])}
    questions = []
    for spec in ledger["questions"]:
        question_id = spec["id"]
        role = spec["question_role"]
        question: Dict[str, Any] = {
            "id": question_id,
            "question_role": role,
            "cluster_id": spec["cluster_id"],
            "difficulty": spec["difficulty"],
            "primary_type": spec["primary_type"],
            "tags": list(spec.get("tags", [])),
            "question": spec["question"],
            "claims": [],
            "negative_evidence": [],
        }
        if role == "unanswerable":
            question["expected_behavior"] = "no_relevant_evidence"
            question["diagnostic_reason"] = spec["diagnostic_reason"]
            questions.append(question)
            continue

        question["reference_answer"] = spec["reference_answer"]
        for order, reference in enumerate(spec.get("claims", []), 1):
            resolved = _resolve_reference(facts, reference, f"{question_id}.claims[{order - 1}]")
            fact = resolved["fact"]
            claim = {
                "id": f"{question_id}_c{order}",
                "kind": fact.get("kind", "text"),
                "claim_type": reference.get("claim_type", fact.get("claim_type", "anchor")),
                "source_document": resolved["document"],
                "section": resolved["section"],
                "accepted_spans": resolved["spans"],
                "fact_id": fact["fact_id"],
            }
            if spec["primary_type"] == "cross_doc_chain":
                # hop_role 来自事实所在文档在链路里的角色，不由出题人手填
                hop_role = reference.get("hop_role") or fact.get("hop_role")
                if hop_role not in ("endpoint", "bridge", "supporting"):
                    raise BuildError(
                        f"{question_id}.claims[{order - 1}] 的事实 {fact['fact_id']} 缺少 hop_role"
                    )
                claim["hop_role"] = hop_role
            question["claims"].append(claim)
        for order, reference in enumerate(spec.get("negatives", []), 1):
            resolved = _resolve_reference(
                facts, reference, f"{question_id}.negatives[{order - 1}]"
            )
            question["negative_evidence"].append({
                "id": f"{question_id}_n{order}",
                "source_document": resolved["document"],
                "section": resolved["section"],
                "accepted_spans": resolved["spans"],
                "fact_id": resolved["fact"]["fact_id"],
            })
        question["evidence_chain"] = [claim["id"] for claim in question["claims"]]
        if spec["primary_type"] == "cross_doc_chain":
            question["hop_design"] = _build_hop_design(spec, question, chains)
        questions.append(question)
    return questions


def _build_hop_design(
    spec: Dict[str, Any], question: Dict[str, Any], chains: Dict[str, Any]
) -> Dict[str, Any]:
    """从链路声明 + 本题实际用到的 claim 推导 hop_design，杜绝手填出错。"""
    chain_id = spec.get("chain_id")
    chain = chains.get(chain_id)
    if chain is None:
        raise BuildError(f"{spec['id']} 引用了未定义的链路: {chain_id}")

    def _docs(role: str) -> List[str]:
        seen = []
        for claim in question["claims"]:
            if claim.get("hop_role") == role and claim["source_document"] not in seen:
                seen.append(claim["source_document"])
        return seen

    design = {
        "chain_id": chain_id,
        "relation_type": chain["relation_type"],
        "endpoint_entities": list(chain["endpoint_entities"]),
        "endpoint_documents": _docs("endpoint"),
        "bridge_documents": _docs("bridge"),
        "bridge_entities": list(chain["bridge_entities"]),
    }
    supporting = _docs("supporting")
    if supporting:
        design["supporting_documents"] = supporting
    return design


def build_corpus_block(ledger: Dict[str, Any]) -> Dict[str, Any]:
    corpus_dir = PROJECT_ROOT / ledger["corpus_relative_dir"]
    documents = [
        {"name": path.name, "sha256": core.sha256_file(path)}
        for path in sorted(corpus_dir.glob("*.md"))
    ]
    if not documents:
        raise BuildError(f"语料目录没有 Markdown 文件: {corpus_dir}")
    return {
        "snapshot_id": ledger["corpus_snapshot_id"],
        "relative_dir": ledger["corpus_relative_dir"],
        "documents": documents,
    }


def build_exam(ledger: Dict[str, Any]) -> Dict[str, Any]:
    questions = build_questions(ledger)
    roles = Counter(q["question_role"] for q in questions)
    clusters = [
        {
            "id": family["cluster_id"],
            "family": family["id"],
            "axis": family["axis"],
            "documents": family["documents"],
        }
        for family in ledger["families"]
    ]
    # 每条桥接链单独成一个 cluster：聚类 bootstrap 的方差取决于 cluster 数量而非大小，
    # 链路多而小优于兄弟族少而大。
    chains = ledger.get("chains", [])
    clusters.extend(
        {
            "id": chain["cluster_id"],
            "family": chain["family"],
            "axis": "entity_bridge",
            "documents": chain["documents"],
        }
        for chain in chains
    )
    bridge_chains = [
        {
            "id": chain["id"],
            "family": chain["family"],
            "relation_type": chain["relation_type"],
            "path": chain.get("path", ""),
            "endpoint_entities": chain["endpoint_entities"],
            "bridge_entities": chain["bridge_entities"],
            "documents": chain["documents"],
        }
        for chain in chains
    ]
    return {
        "schema_version": core.SCHEMA_VERSION,
        "exam_meta": {
            "exam_id": ledger["exam_id"],
            "title": ledger["exam_title"],
            "generated_date": ledger.get("generated_date")
            or dt.date.today().isoformat(),
            "purpose": ledger["purpose"],
            "retrieval_protocol": ledger["retrieval_protocol"],
            "question_counts": {
                "total": len(questions),
                "scored": roles["scored"],
                "sanity": roles["sanity"],
                "unanswerable": roles["unanswerable"],
                "by_primary_type": dict(sorted(
                    Counter(q["primary_type"] for q in questions).items()
                )),
                "by_difficulty": dict(sorted(
                    Counter(q["difficulty"] for q in questions).items()
                )),
            },
            "clusters": clusters,
            "bridge_chains": bridge_chains,
            "design_constraints": ledger["design_constraints"],
            "corpus": build_corpus_block(ledger),
            "scoring_note": ledger.get("scoring_note", ""),
        },
        "questions": questions,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把解析后的事实台账投影成 v4 考卷")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        ledger = json.loads(Path(args.ledger).expanduser().read_text(encoding="utf-8"))
        exam = build_exam(ledger)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[读取失败] {exc}\n")
        return 2
    except BuildError as exc:
        sys.stderr.write(f"[生成失败] {exc}\n")
        return 2

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(exam, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts = exam["exam_meta"]["question_counts"]
    print(
        f"[生成完成] {counts['total']} 题 (scored={counts['scored']}, "
        f"sanity={counts['sanity']}, unanswerable={counts['unanswerable']})，"
        f"{sum(len(q['claims']) for q in exam['questions'])} claims → {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
