import importlib.util
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "评测脚本" / "entity_bridge_multihop_eval.py"
REAL_EXAM = ROOT / "评测考试" / "考卷-多跳" / "考卷-2026-07-17-01.json"
SPEC = importlib.util.spec_from_file_location("entity_bridge_multihop_eval", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def png_bytes(width=1, height=1):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + (b"\x00\x00\x00" * width)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(scanline * height))
        + chunk(b"IEND", b"")
    )


def question(*, asset=False, supporting=False):
    kind = "asset" if asset else "text"
    payload = {
        "id": "q1",
        "scored": True,
        "difficulty": "hard",
        "primary_type": (
            "asset_reference_retrieval" if asset else "cross_doc_chain"
        ),
        "tags": ["entity_bridge"],
        "question": "甲和乙有什么联系？",
        "chain_id": "chain",
        "relation_type": "equity_control",
        "evaluation_focus": "asset_path" if asset else "relation_path",
        "endpoint_entities": ["甲", "乙"],
        "endpoint_documents": ["left.md", "right.md"],
        "bridge_documents": ["bridge.md"],
        "supporting_documents": ["supporting.md"] if supporting else [],
        "claims": [
            {
                "id": "c1",
                "kind": kind,
                "hop_role": "endpoint",
                "source_document": "left.md",
                "section": "s",
                "accepted_spans": ["左端证据片段"],
            },
            {
                "id": "c2",
                "kind": kind,
                "hop_role": "bridge",
                "source_document": "bridge.md",
                "section": "s",
                "accepted_spans": ["中间桥接证据"],
            },
            {
                "id": "c3",
                "kind": kind,
                "hop_role": "endpoint",
                "source_document": "right.md",
                "section": "s",
                "accepted_spans": ["右端证据片段"],
            },
        ],
        "negative_evidence": [],
        "reference_answer": "答案",
        "evidence_chain": ["c1", "c2", "c3"],
    }
    if supporting:
        payload["claims"].append(
            {
                "id": "c4",
                "kind": kind,
                "hop_role": "supporting",
                "source_document": "supporting.md",
                "section": "s",
                "accepted_spans": ["辅助分支证据片段"],
            }
        )
        payload["evidence_chain"].append("c4")
    return payload


def base_metrics(hits, documents):
    definitions = [
        ("c1", "left.md"),
        ("c2", "bridge.md"),
        ("c3", "right.md"),
        ("c4", "supporting.md"),
    ]
    return {
        "claim_detail": [
            {
                "claim_id": claim_id,
                "kind": "text",
                "source_document": document,
                "section": "s",
                "hit": hit,
                "first_rank": index if hit else None,
                "matched_chunk_id": f"chunk-{index}" if hit else None,
                "document_hit_claim_miss": False,
            }
            for index, ((claim_id, document), hit) in enumerate(
                zip(definitions, hits),
                1,
            )
        ],
        "retrieved_documents": documents,
    }


def result(
    question_id,
    metrics=None,
    *,
    status="ok",
    primary_type="cross_doc_chain",
    chain_id="chain",
    supporting=False,
):
    payload = {
        "id": question_id,
        "scored": True,
        "status": status,
        "primary_type": primary_type,
        "chain_id": chain_id,
        "evaluation_focus": "relation_path",
        "supporting_documents": ["supporting.md"] if supporting else [],
    }
    if metrics is not None:
        payload["metrics"] = {"metrics_by_k": {"5": {"entity_bridge": metrics}}}
    return payload


def test_bridge_metrics_identify_endpoint_complete_bridge_missing():
    metrics = MODULE.compute_bridge_metrics_at_k(
        question(), base_metrics((True, False, True), ["left.md", "right.md"])
    )
    assert metrics["endpoint_text_claim_recall"] == 1.0
    assert metrics["bridge_text_claim_recall"] == 0.0
    assert metrics["complete_core_bridge_chain"] is False
    assert metrics["complete_declared_text_chain"] is False
    assert metrics["bridge_only_document_miss"] is True
    assert metrics["path_status"] == "bridge_missing"


def test_supporting_claim_is_required_by_declared_chain():
    metrics = MODULE.compute_bridge_metrics_at_k(
        question(supporting=True),
        base_metrics(
            (True, True, True, False),
            ["left.md", "bridge.md", "right.md"],
        ),
    )
    assert metrics["complete_core_bridge_chain"] is True
    assert metrics["supporting_text_claim_recall"] == 0.0
    assert metrics["complete_declared_text_chain"] is False
    assert metrics["path_status"] == "supporting_missing"


def test_image_reference_chain_is_scored_separately_from_text_chain():
    metrics = MODULE.compute_bridge_metrics_at_k(
        question(asset=True),
        base_metrics((True, True, True), ["left.md", "bridge.md", "right.md"]),
    )
    assert metrics["endpoint_text_claim_recall"] is None
    assert metrics["bridge_text_claim_recall"] is None
    assert metrics["complete_core_bridge_chain"] is None
    assert metrics["complete_declared_text_chain"] is None
    assert metrics["image_reference_claim_recall"] == 1.0
    assert metrics["complete_image_reference_chain"] is True
    assert metrics["path_status"] == "image_reference_complete"


def test_aggregate_counts_request_errors_as_zero():
    complete = {
        "endpoint_text_claim_recall": 1.0,
        "bridge_text_claim_recall": 1.0,
        "supporting_text_claim_recall": None,
        "complete_core_bridge_chain": True,
        "complete_declared_text_chain": True,
        "endpoint_document_recall": 1.0,
        "bridge_document_recall": 1.0,
        "bridge_only_document_miss": False,
        "image_reference_claim_recall": None,
        "complete_image_reference_chain": None,
        "path_status": "complete",
    }
    results = [
        result("q1", complete),
        result("q2", status="request_error"),
    ]
    base_summary = {
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "headline": {},
        "diagnostics": {
            "request_success_rate": 0.5,
            "hard_negative_intrusion_rate": None,
        },
    }
    summary = MODULE.aggregate_bridge(results, base_summary, 5, [5])
    question_macro = summary["headline"]["question_macro"]
    assert question_macro["endpoint_text_claim_recall"] == 0.5
    assert question_macro["bridge_text_claim_recall"] == 0.5
    assert question_macro["complete_declared_text_chain_rate"] == 0.5
    assert summary["diagnostics"]["path_status_counts"] == {
        "complete": 1,
        "request_error": 1,
    }


def test_chain_macro_does_not_treat_paraphrases_as_independent_chains():
    complete = {
        "endpoint_text_claim_recall": 1.0,
        "bridge_text_claim_recall": 1.0,
        "supporting_text_claim_recall": None,
        "complete_core_bridge_chain": True,
        "complete_declared_text_chain": True,
        "endpoint_document_recall": 1.0,
        "bridge_document_recall": 1.0,
        "bridge_only_document_miss": False,
        "image_reference_claim_recall": None,
        "complete_image_reference_chain": None,
        "path_status": "complete",
    }
    missing = {
        **complete,
        "endpoint_text_claim_recall": 0.0,
        "bridge_text_claim_recall": 0.0,
        "complete_core_bridge_chain": False,
        "complete_declared_text_chain": False,
        "endpoint_document_recall": 0.0,
        "bridge_document_recall": 0.0,
        "path_status": "multiple_missing",
    }
    results = [
        result("q1", complete, chain_id="chain-a"),
        result("q2", complete, chain_id="chain-a"),
        result("q3", missing, chain_id="chain-b"),
    ]
    base_summary = {
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "headline": {},
        "diagnostics": {
            "request_success_rate": 1.0,
            "hard_negative_intrusion_rate": None,
        },
    }
    summary = MODULE.aggregate_bridge(results, base_summary, 5, [5])
    assert summary["headline"]["question_macro"]["endpoint_text_claim_recall"] == 0.6667
    assert summary["headline"]["chain_macro"]["endpoint_text_claim_recall"] == 0.5
    assert summary["headline"]["chain_macro"]["independent_chain_count"] == 2


def test_report_names_image_reference_and_chain_level_statistics():
    complete = {
        "endpoint_text_claim_recall": 1.0,
        "bridge_text_claim_recall": 1.0,
        "supporting_text_claim_recall": None,
        "complete_core_bridge_chain": True,
        "complete_declared_text_chain": True,
        "endpoint_document_recall": 1.0,
        "bridge_document_recall": 1.0,
        "bridge_only_document_miss": False,
        "image_reference_claim_recall": None,
        "complete_image_reference_chain": None,
        "path_status": "complete",
    }
    results = [result("q1", complete)]
    base_summary = {
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "headline": {},
        "diagnostics": {
            "request_success_rate": 1.0,
            "hard_negative_intrusion_rate": None,
        },
    }
    summary = MODULE.aggregate_bridge(results, base_summary, 5, [5])
    report = MODULE.build_report(
        {"exam_meta": {"title": "测试", "exam_id": "exam"}},
        summary,
        {
            "backend": "arag",
            "dataset_id": "dataset",
            "dataset_revision": "revision",
            "graph_search": False,
            "primary_k": 5,
            "corpus_drift": False,
        },
        results,
    )
    assert "Complete Declared Text Chain Rate@5" in report
    assert "Image Reference Claim Recall@5" in report
    assert "图片引用路径字符串" in report
    assert "链级 Bootstrap CI95" in report


def test_specialized_validator_rejects_endpoint_name_in_bridge_document(
    tmp_path, monkeypatch
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "left.md").write_text("甲在左侧机构任职。", encoding="utf-8")
    (corpus / "right.md").write_text("乙在右侧机构任职。", encoding="utf-8")
    (corpus / "bridge.md").write_text(
        "左侧机构收购右侧机构，材料错误泄露甲。", encoding="utf-8"
    )
    image = corpus / "asset.png"
    image.write_bytes(png_bytes())
    monkeypatch.setattr(MODULE.core, "PROJECT_ROOT", tmp_path)
    exam = {
        "exam_meta": {
            "corpus": {
                "relative_dir": "corpus",
                "documents": [
                    {"name": "left.md"},
                    {"name": "right.md"},
                    {"name": "bridge.md"},
                ],
                "image_assets": [
                    {
                        "name": "asset.png",
                        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "media_type": "image/png",
                        "width": 1,
                        "height": 1,
                    }
                ],
            },
            "entity_bridge_design": {
                "benchmark_kind": "entity_bridge_multihop",
                "questions_per_chain": 1,
                "min_image_reference_questions": 0,
                "chains": [
                    {
                        "id": "chain",
                        "relation_type": "equity_control",
                        "bridge_entities": ["左侧机构", "右侧机构"],
                    }
                ],
            },
        },
        "questions": [question()],
    }
    with pytest.raises(MODULE.BridgeExamValidationError, match="泄露端点实体"):
        MODULE.validate_entity_bridge_design(exam)


def test_image_snapshot_rejects_hash_drift(tmp_path):
    image = tmp_path / "asset.png"
    image.write_bytes(png_bytes())
    corpus = {
        "image_assets": [
            {
                "name": "asset.png",
                "sha256": "0" * 64,
                "media_type": "image/png",
                "width": 1,
                "height": 1,
            }
        ]
    }
    with pytest.raises(MODULE.BridgeExamValidationError, match="图片资产 SHA-256"):
        MODULE._validate_image_assets(corpus, tmp_path, False)


def test_comparison_guards_require_verified_dataset_revision():
    summary = {"comparison_eligible": True}
    manifest = {
        "dataset_revision": "unverified",
        "corpus_drift": False,
        "image_drift": False,
    }
    MODULE.apply_comparison_guards(summary, manifest)
    assert summary["comparison_eligible"] is False
    assert summary["comparison_blockers"] == ["dataset_revision_unverified"]


def test_validator_rejects_declared_chain_without_questions():
    exam = json.loads(REAL_EXAM.read_text(encoding="utf-8"))
    exam["exam_meta"]["entity_bridge_design"]["chains"].append(
        {
            "id": "unused-chain",
            "theme": "未使用",
            "path": "甲→机构甲→机构乙←乙",
            "relation_type": "equity_control",
            "bridge_entities": ["机构甲", "机构乙"],
        }
    )
    with pytest.raises(MODULE.BridgeExamValidationError, match="每条链题数"):
        MODULE.validate_entity_bridge_design(exam)


def test_validator_rejects_question_relation_mismatch():
    exam = json.loads(REAL_EXAM.read_text(encoding="utf-8"))
    exam["questions"][0]["relation_type"] = "service_contract"
    with pytest.raises(MODULE.BridgeExamValidationError, match="声明不一致"):
        MODULE.validate_entity_bridge_design(exam)


def test_validator_requires_same_entity_hard_negative():
    exam = json.loads(REAL_EXAM.read_text(encoding="utf-8"))
    hard_negative = next(
        item for item in exam["questions"] if item["id"] == "bridge_q007"
    )
    hard_negative["negative_evidence"][0]["accepted_spans"] = [
        "双方没有发生股权交易，也没有设立合资公司。"
    ]
    with pytest.raises(MODULE.BridgeExamValidationError, match="共享至少一个"):
        MODULE.validate_entity_bridge_design(exam)


def test_dify_request_contract_is_reused_without_mutating_base_constant():
    payload = MODULE.core.build_dify_request(
        "甲和乙有什么联系？",
        10,
        True,
        score_threshold_enabled=True,
        score_threshold=None,
    )
    assert payload["query"] == "甲和乙有什么联系？"
    assert payload["attachment_ids"] == []
    assert payload["retrieval_model"]["top_k"] == 10
    assert payload["retrieval_model"]["graph_search"]["enabled"] is True
    assert payload["retrieval_model"]["score_threshold_enabled"] is True
    assert MODULE.core.DIFY_RETRIEVAL_MODEL["top_k"] == 10


def test_real_specialized_exam_validates():
    exam, validation = MODULE.load_and_validate_exam(REAL_EXAM)
    assert len(exam["questions"]) == 24
    assert validation["total_claims"] == 75
    assert validation["corpus_drift"] is False
    assert validation["entity_bridge"]["image_drift"] is False
    assert validation["entity_bridge"]["image_asset_count"] == 10
    assert validation["entity_bridge"]["chain_counts"] == {
        "agri_equity": 8,
        "marine_equity": 8,
        "museum_service": 8,
    }
    assert validation["entity_bridge"]["hard_negative_chain_counts"] == {
        "agri_equity": 1,
        "marine_equity": 1,
        "museum_service": 1,
    }


def comparison_payload(items):
    results = []
    for question_id, chain_id, value in items:
        entity_bridge = {
            "endpoint_text_claim_recall": value,
            "bridge_text_claim_recall": value,
            "complete_core_bridge_chain": bool(value),
            "complete_declared_text_chain": bool(value),
            "image_reference_claim_recall": None,
            "complete_image_reference_chain": None,
        }
        results.append(
            {
                "id": question_id,
                "scored": True,
                "primary_type": "cross_doc_chain",
                "chain_id": chain_id,
                "metrics": {"metrics_by_k": {"5": {"entity_bridge": entity_bridge}}},
            }
        )
    return {
        "metrics_version": MODULE.SPECIALIZED_METRICS_VERSION,
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "manifest": {
            "exam_id": "exam",
            "exam_sha256": "exam-hash",
            "corpus": {"snapshot_id": "snapshot"},
            "dataset_revision": "ingestion-v1",
            "primary_k": 5,
            "eval_k": [5],
            "evaluator": {"version": MODULE.SPECIALIZED_METRICS_VERSION},
            "backend": "arag",
            "dataset_id": "dataset",
        },
        "results": results,
    }


def test_compare_rejects_missing_or_reordered_questions(tmp_path):
    left = comparison_payload([("q1", "a", 1.0), ("q2", "b", 0.0)])
    right = comparison_payload([("q2", "b", 0.0)])
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")
    with pytest.raises(MODULE.BridgeComparisonError, match="ID 或顺序"):
        MODULE.compare_runs(left_path, right_path)


def test_compare_rejects_dataset_revision_or_evaluator_drift(tmp_path):
    left = comparison_payload([("q1", "a", 1.0)])
    right = comparison_payload([("q1", "a", 1.0)])
    right["manifest"]["dataset_revision"] = "ingestion-v2"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")
    with pytest.raises(MODULE.BridgeComparisonError, match="dataset_revision"):
        MODULE.compare_runs(left_path, right_path)

    right["manifest"]["dataset_revision"] = "ingestion-v1"
    right["manifest"]["evaluator"] = {"version": "different"}
    right_path.write_text(json.dumps(right), encoding="utf-8")
    with pytest.raises(MODULE.BridgeComparisonError, match="evaluator"):
        MODULE.compare_runs(left_path, right_path)


def test_compare_uses_chain_cluster_for_inference(tmp_path):
    left = comparison_payload(
        [("q1", "chain-a", 0.0), ("q2", "chain-a", 0.0), ("q3", "chain-b", 0.0)]
    )
    right = comparison_payload(
        [("q1", "chain-a", 1.0), ("q2", "chain-a", 1.0), ("q3", "chain-b", 0.0)]
    )
    right["manifest"]["backend"] = "dify"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")
    comparison = MODULE.compare_runs(left_path, right_path)
    metric = comparison["metrics"]["endpoint_text_claim_recall"]
    assert metric["num_paired_questions"] == 3
    assert metric["num_paired_chains"] == 2
    assert metric["question_macro"]["right_minus_left"] == 0.6667
    assert metric["chain_macro"]["right_minus_left"] == 0.5
    assert comparison["inference_unit"] == "chain_id"
