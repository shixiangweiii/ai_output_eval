import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "评测脚本" / "retrieval_eval_v3.py"
SPEC = importlib.util.spec_from_file_location("retrieval_eval_v3", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def chunk(rank, document, content, score=1.0, chunk_id=None):
    return {
        "rank": rank,
        "original_index": rank,
        "server_top": rank,
        "relevance_score": score,
        "document_name": document,
        "document_id": f"doc-{document}",
        "chunk_id": chunk_id or f"chunk-{rank}",
        "content": content,
    }


def claim(claim_id, document, *spans, kind="text"):
    return {
        "id": claim_id,
        "kind": kind,
        "source_document": document,
        "section": "section",
        "accepted_spans": list(spans),
    }


def scored_question(claims, negatives=None):
    return {
        "id": "q",
        "scored": True,
        "difficulty": "medium",
        "primary_type": "single_doc_multi_claim",
        "tags": [],
        "question": "query",
        "claims": claims,
        "negative_evidence": negatives or [],
        "reference_answer": "answer",
        "evidence_chain": [item["id"] for item in claims],
    }


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_exam(tmp_path, *, span="唯一原子证据片段", tags=None):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    document = corpus_dir / "doc.md"
    document.write_text(f"# 标题\n\n这里包含{span}，用于测试。\n", encoding="utf-8")
    question = {
        "id": "q1",
        "scored": True,
        "difficulty": "simple",
        "primary_type": "single_doc_fact",
        "tags": tags or [],
        "question": "完全不同的查询措辞",
        "claims": [claim("q1-c1", "doc.md", span)],
        "negative_evidence": [],
        "reference_answer": "答案",
        "evidence_chain": ["q1-c1"],
    }
    exam = {
        "schema_version": "3.0",
        "exam_meta": {
            "exam_id": "exam",
            "title": "test",
            "corpus": {
                "snapshot_id": "snapshot",
                "relative_dir": "corpus",
                "documents": [{"name": "doc.md", "sha256": sha256(document)}],
            },
            "question_counts": {
                "total": 1,
                "scored": 1,
                "diagnostic": 0,
                "by_primary_type": {"single_doc_fact": 1},
                "by_difficulty": {"simple": 1},
            },
            "design_constraints": {
                "min_claims_per_document": 1,
                "max_claim_share_per_document": 1.0,
                "min_low_lexical_overlap_questions": int(bool(tags)),
                "min_hard_negative_questions": 0,
            },
        },
        "questions": [question],
    }
    exam_path = tmp_path / "exam.json"
    exam_path.write_text(json.dumps(exam, ensure_ascii=False), encoding="utf-8")
    return exam_path, document


def test_validate_exam_checks_corpus_and_spans(tmp_path, monkeypatch):
    exam_path, _ = make_exam(tmp_path, tags=["low_lexical_overlap"])
    monkeypatch.setattr(MODULE, "PROJECT_ROOT", tmp_path)
    exam, validation = MODULE.load_and_validate_exam(exam_path)
    assert exam["schema_version"] == "3.0"
    assert validation["total_claims"] == 1
    assert validation["corpus_drift"] is False


def test_validate_exam_blocks_hash_drift_unless_overridden(tmp_path, monkeypatch):
    exam_path, document = make_exam(tmp_path)
    document.write_text(document.read_text(encoding="utf-8") + "漂移", encoding="utf-8")
    monkeypatch.setattr(MODULE, "PROJECT_ROOT", tmp_path)
    with pytest.raises(MODULE.ExamValidationError, match="SHA-256"):
        MODULE.load_and_validate_exam(exam_path)
    _, validation = MODULE.load_and_validate_exam(exam_path, allow_corpus_drift=True)
    assert validation["corpus_drift"] is True
    assert validation["warnings"]


def test_validate_exam_rejects_missing_span(tmp_path, monkeypatch):
    exam_path, _ = make_exam(tmp_path)
    payload = json.loads(exam_path.read_text(encoding="utf-8"))
    payload["questions"][0]["claims"][0]["accepted_spans"] = ["并不存在的原子证据"]
    exam_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(MODULE, "PROJECT_ROOT", tmp_path)
    with pytest.raises(MODULE.ExamValidationError, match="span 不存在"):
        MODULE.load_and_validate_exam(exam_path)


def test_validate_exam_enforces_low_lexical_overlap(tmp_path, monkeypatch):
    span = "这是一条非常明确且足够长的原子证据"
    exam_path, _ = make_exam(tmp_path, span=span, tags=["low_lexical_overlap"])
    payload = json.loads(exam_path.read_text(encoding="utf-8"))
    payload["questions"][0]["question"] = span
    exam_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(MODULE, "PROJECT_ROOT", tmp_path)
    with pytest.raises(MODULE.ExamValidationError, match="low_lexical_overlap 不成立"):
        MODULE.load_and_validate_exam(exam_path)


def test_exact_match_accepts_alternative_span_but_requires_document():
    question = scored_question([claim("c1", "doc-a", "第一种说法", "第二种说法")])
    metrics = MODULE.compute_metrics_at_k(
        question, [chunk(1, "doc-a", "文本包含第二种说法")], 1
    )
    assert metrics["claim_recall"] == 1.0
    wrong_doc = MODULE.compute_metrics_at_k(
        question, [chunk(1, "doc-b", "文本包含第二种说法")], 1
    )
    assert wrong_doc["claim_recall"] == 0.0


def test_one_chunk_can_hit_multiple_claims_and_complete_chain():
    question = scored_question(
        [claim("c1", "doc", "证据甲内容"), claim("c2", "doc", "证据乙内容")]
    )
    metrics = MODULE.compute_metrics_at_k(
        question, [chunk(1, "doc", "证据甲内容和证据乙内容同时出现")], 1
    )
    assert metrics["claim_hits"] == 2
    assert metrics["complete_evidence_chain"] is True
    assert metrics["novel_claim_rank_score"] == 1.0


def test_duplicate_chunks_do_not_repeat_novel_claim_gain():
    question = scored_question(
        [claim("c1", "doc", "证据甲内容"), claim("c2", "doc", "证据乙内容")]
    )
    chunks = [
        chunk(1, "doc", "证据甲内容"),
        chunk(2, "doc", "重复的证据甲内容"),
        chunk(3, "doc", "证据乙内容"),
    ]
    metrics = MODULE.compute_metrics_at_k(question, chunks, 3)
    assert metrics["claim_recall"] == 1.0
    assert metrics["novel_claim_rank_score"] == 0.75
    assert metrics["duplicate_document_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_aggregate_distinguishes_query_macro_micro_and_chain_rate():
    q1 = scored_question([claim("q1-c1", "a", "命中证据一")])
    q1["id"] = "q1"
    q2 = scored_question(
        [
            claim("q2-c1", "b", "命中证据二"),
            claim("q2-c2", "b", "缺失证据三"),
            claim("q2-c3", "b", "缺失证据四"),
        ]
    )
    q2["id"] = "q2"
    m1 = MODULE.compute_question_metrics(q1, [chunk(1, "a", "命中证据一")], [5], [100], False)
    m2 = MODULE.compute_question_metrics(q2, [chunk(1, "b", "命中证据二")], [5], [100], False)
    results = [
        {
            "id": "q1", "scored": True, "status": "ok", "metrics": m1,
            "claim_count": 1, "difficulty": "simple", "primary_type": "single_doc_fact",
            "tags": [], "gt_document_count": 1, "negative_evidence_count": 0,
            "asset_claim_count": 0, "latency_ms": 1,
        },
        {
            "id": "q2", "scored": True, "status": "ok", "metrics": m2,
            "claim_count": 3, "difficulty": "hard", "primary_type": "single_doc_multi_claim",
            "tags": [], "gt_document_count": 1, "negative_evidence_count": 0,
            "asset_claim_count": 0, "latency_ms": 2,
        },
    ]
    summary = MODULE.aggregate(results, 5, [5], [100], "completed", "full", False)
    assert summary["headline"]["query_macro_claim_recall"] == 0.6666
    assert summary["diagnostics"]["claim_micro_recall"] == 0.5
    assert summary["headline"]["complete_evidence_chain_rate"] == 0.5
    assert summary["diagnostics"]["error_counts"] == {}
    assert "mean_duplicate_document_rate" in summary["diagnostics"]


def test_char_budget_curve_changes_when_evidence_is_late():
    question = scored_question([claim("c1", "doc", "目标证据片段")])
    chunks = [
        chunk(1, "noise", "前置噪声内容很长"),
        chunk(2, "doc", "这里包含目标证据片段"),
    ]
    metrics = MODULE.compute_question_metrics(question, chunks, [1, 2], [5, 50], False)
    assert metrics["metrics_by_k"]["1"]["claim_recall"] == 0.0
    assert metrics["metrics_by_k"]["2"]["claim_recall"] == 1.0
    assert metrics["char_budget_metrics"]["5"]["claim_recall"] == 0.0
    assert metrics["char_budget_metrics"]["50"]["claim_recall"] == 1.0


def test_hard_negative_intrusion_is_explicit():
    negative = {
        "id": "n1",
        "source_document": "wrong",
        "section": "old",
        "accepted_spans": ["已经废弃的旧口径"],
    }
    question = scored_question([claim("c1", "right", "当前有效证据")], [negative])
    metrics = MODULE.compute_metrics_at_k(
        question,
        [chunk(1, "wrong", "已经废弃的旧口径"), chunk(2, "right", "当前有效证据")],
        2,
    )
    assert metrics["claim_recall"] == 1.0
    assert metrics["hard_negative_intrusion"] is True


def test_dify_request_k_is_applied_without_mutating_constant():
    payload = MODULE.build_dify_request(
        "query", 17, True, score_threshold_enabled=True, score_threshold=None
    )
    assert payload["retrieval_model"]["top_k"] == 17
    assert payload["retrieval_model"]["graph_search"]["enabled"] is True
    assert payload["retrieval_model"]["score_threshold_enabled"] is True
    assert payload["retrieval_model"]["score_threshold"] is None
    assert MODULE.DIFY_RETRIEVAL_MODEL["top_k"] == 10


def test_dify_coverage_search_request_matches_reference_shape():
    payload = MODULE.build_dify_request(
        "query",
        10,
        False,
        score_threshold_enabled=True,
        score_threshold=None,
        search_method="coverage_search",
    )
    model = payload["retrieval_model"]
    assert model["search_method"] == "coverage_search"
    assert model["reranking_enable"] is True
    assert model["reranking_mode"] is None
    assert model["weights"] is None
    assert model["top_k"] == 10
    assert model["score_threshold_enabled"] is True
    assert model["score_threshold"] is None
    assert model["graph_search"] is None
    assert MODULE.DIFY_RETRIEVAL_MODEL["search_method"] == "hybrid_search"


def test_arag_manifest_marks_request_k_unsupported_and_contains_no_credentials(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_COOKIE", "secret-cookie")
    monkeypatch.setenv("RETRIEVAL_XSRF_TOKEN", "secret-token")
    monkeypatch.setattr(MODULE, "_git_info", lambda: {"commit": "abc", "dirty": False})
    exam = {
        "exam_meta": {
            "exam_id": "exam",
            "corpus": {"snapshot_id": "s", "relative_dir": "c", "documents": []},
        }
    }
    args = argparse.Namespace(
        backend="arag", dataset_id="dataset", dataset_revision=None, graph_search=False,
        score_threshold_enabled=False, score_threshold=None,
        request_k=10, eval_k=[1, 5, 10], primary_k=5, char_budgets=[1000],
        raw_content_limit=800,
    )
    manifest = MODULE.build_manifest(
        exam, {"exam_sha256": "hash", "corpus_drift": False}, args,
        "20260715_000000", "full",
    )
    serialized = json.dumps(manifest)
    assert manifest["request_k_support"] == "unsupported_by_backend"
    assert "secret-cookie" not in serialized
    assert "secret-token" not in serialized


def test_authentication_error_aborts_with_partial_result():
    class Response:
        status_code = 401
        text = "unauthorized"

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    args = argparse.Namespace(
        backend="arag", dataset_id="dataset", timeout=1, retries=1, request_k=10,
        graph_search=False, score_threshold_enabled=False, score_threshold=None,
        eval_k=[5], char_budgets=[100], raw_content_limit=100,
        limit=0, sleep=0,
    )
    exam = {"questions": [scored_question([claim("c1", "doc", "有效证据片段")])]}
    with pytest.raises(MODULE.EvaluationAborted) as caught:
        MODULE.evaluate_exam(exam, Session(), args)
    assert caught.value.results[0]["status"] == "auth_error"


def test_aborted_run_writes_four_partial_artifacts(tmp_path):
    results = [
        {
            "id": "q", "scored": True, "status": "auth_error",
            "difficulty": "hard", "primary_type": "single_doc_fact", "tags": [],
            "question": "query", "claim_count": 1, "negative_evidence_count": 0,
            "asset_claim_count": 0, "gt_document_count": 1, "latency_ms": 1,
            "error": "authentication failed",
        }
    ]
    summary = MODULE.aggregate(
        results, 5, [5], [100], "aborted_auth", "full", False
    )
    exam = {
        "exam_meta": {
            "exam_id": "exam", "title": "test",
            "corpus": {"snapshot_id": "s", "documents": []},
        }
    }
    manifest = {
        "backend": "arag", "dataset_id": "dataset", "primary_k": 5,
        "corpus_drift": False,
    }
    output = MODULE.write_outputs(
        tmp_path, exam, {"exam_sha256": "hash"}, summary, results, manifest,
        "20260715_000000",
    )
    names = {path.name for path in output.iterdir()}
    assert names == {
        "results_20260715_000000.json",
        "summary_20260715_000000.json",
        "manifest_20260715_000000.json",
        "report_20260715_000000.md",
    }
    assert json.loads(
        (output / "summary_20260715_000000.json").read_text(encoding="utf-8")
    )["headline"] is None


def test_bootstrap_is_reproducible():
    first = MODULE.bootstrap_ci([0.0, 0.5, 1.0], samples=200, seed=7)
    second = MODULE.bootstrap_ci([0.0, 0.5, 1.0], samples=200, seed=7)
    assert first == second


def result_payload(backend, values, *, exam_hash="hash"):
    results = []
    for index, value in enumerate(values, 1):
        results.append(
            {
                "id": f"q{index}",
                "scored": True,
                "metrics": {
                    "metrics_by_k": {
                        "5": {
                            "claim_recall": value,
                            "complete_evidence_chain": value == 1.0,
                            "novel_claim_rank_score": value,
                        }
                    }
                },
            }
        )
    return {
        "schema_version": "3.0",
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "manifest": {
            "exam_id": "exam",
            "exam_sha256": exam_hash,
            "corpus": {"snapshot_id": "s", "documents": [{"name": "d", "sha256": "h"}]},
            "primary_k": 5,
            "eval_k": [1, 3, 5, 10],
            "backend": backend,
            "dataset_id": backend,
        },
        "results": results,
    }


def test_compare_runs_is_paired_and_rejects_incompatible_exam(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(json.dumps(result_payload("arag", [0.0, 1.0])), encoding="utf-8")
    right.write_text(json.dumps(result_payload("dify", [1.0, 1.0])), encoding="utf-8")
    comparison = MODULE.compare_runs(left, right)
    assert comparison["num_paired_questions"] == 2
    assert comparison["metrics"]["claim_recall"]["right_minus_left"] == 0.5
    assert comparison["paired_question_deltas"][0]["claim_recall"] == 1.0

    incompatible = result_payload("dify", [1.0, 1.0], exam_hash="other")
    right.write_text(json.dumps(incompatible), encoding="utf-8")
    with pytest.raises(MODULE.ComparisonError, match="exam_sha256"):
        MODULE.compare_runs(left, right)
