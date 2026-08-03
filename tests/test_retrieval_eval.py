import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "评测脚本" / "retrieval_eval.py"
SPEC = importlib.util.spec_from_file_location("retrieval_eval", SCRIPT)
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


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("响应体不是 JSON")
        return self._payload


class FakeSession:
    """按顺序返回预设响应；元素是异常时抛出，用于模拟网络故障。"""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def no_backoff(monkeypatch):
    """去掉重试退避，避免单测真实休眠。"""
    slept = []
    monkeypatch.setattr(MODULE.time, "sleep", slept.append)
    return slept


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


def test_normalize_text_folds_markdown_dashes_quotes_and_whitespace():
    assert MODULE.normalize_text("# 标题 ![示意图](assets/a.png)") == "标题assets/a.png"
    assert MODULE.normalize_text("详见 [附录 A](docs/a.md)") == "详见附录adocs/a.md"
    assert MODULE.normalize_text("A—B “C”") == 'a-b"c"'
    assert MODULE.normalize_text("**关键**\n证据\t１２３") == "关键证据123"
    assert MODULE.normalize_text("") == ""
    assert MODULE.normalize_text(None) == ""


def test_span_matching_survives_markdown_emphasis_in_corpus():
    question = scored_question([claim("c1", "doc", "关键结论是十分钟内完成")])
    metrics = MODULE.compute_metrics_at_k(
        question, [chunk(1, "doc", "## 结论\n\n**关键结论是十分钟内完成**\n")], 1
    )
    assert metrics["claim_recall"] == 1.0


def test_parse_arag_response_normalizes_content_and_flags_ranking_anomaly():
    chunks, anomaly = MODULE.parse_arag_response(
        {
            "content": [
                {
                    "top": 1, "relevanceScore": 0.4, "documentName": "a.md",
                    "documentId": "d1", "chunkId": "c1", "content": "文本一",
                },
                {
                    "top": 2, "relevanceScore": 0.9, "documentName": "b.md",
                    "documentId": "d2", "chunkId": "c2", "content": "文本二",
                },
            ]
        }
    )
    assert [item["rank"] for item in chunks] == [1, 2]
    assert [item["document_name"] for item in chunks] == ["a.md", "b.md"]
    assert chunks[0]["chunk_id"] == "c1"
    assert chunks[1]["relevance_score"] == 0.9
    assert anomaly is True


def test_parse_dify_response_maps_records_to_chunks():
    chunks, anomaly = MODULE.parse_dify_response(
        {
            "records": [
                {
                    "score": 0.9,
                    "child_chunks": [
                        {"id": "child-1", "position": 2, "score": 0.91, "content": "命中子块"}
                    ],
                    "segment": {
                        "id": "s1", "position": 3, "content": "片段一",
                        "document_id": "d1", "document": {"name": "a.md", "id": "d1"},
                    },
                },
                {
                    "score": 0.7,
                    "segment": {
                        "id": "s2", "position": 4, "content": "片段二",
                        "document": {"name": "b.md", "id": "d2"},
                    },
                },
            ]
        }
    )
    assert [item["rank"] for item in chunks] == [1, 2]
    assert [item["document_name"] for item in chunks] == ["a.md", "b.md"]
    assert chunks[0]["server_top"] == 3
    assert chunks[0]["chunk_id"] == "s1"
    assert chunks[0]["child_chunk_count"] == 1
    assert chunks[0]["child_chunks"] == [
        {"id": "child-1", "position": 2, "score": 0.91}
    ]
    assert chunks[1]["child_chunk_count"] == 0
    assert chunks[1]["document_id"] == "d2"
    assert anomaly is False

    diagnostics = MODULE.dify_parent_child_diagnostics(chunks)
    assert diagnostics["records_with_child_chunks"] == 1
    assert diagnostics["child_chunk_count"] == 1
    assert diagnostics["records"][0]["parent_chunk_id"] == "s1"
    assert diagnostics["scoring_content"] == "parent segment.content returned by Dify"


def test_malformed_backend_payloads_raise_response_schema_error():
    with pytest.raises(MODULE.ResponseSchemaError, match="content"):
        MODULE.parse_arag_response({"content": "not-a-list"})
    with pytest.raises(MODULE.ResponseSchemaError, match="content"):
        MODULE.parse_arag_response({})
    with pytest.raises(MODULE.ResponseSchemaError, match="records"):
        MODULE.parse_dify_response({"records": {}})
    with pytest.raises(MODULE.ResponseSchemaError, match="segment"):
        MODULE.parse_dify_response({"records": [{"segment": "not-an-object"}]})
    with pytest.raises(MODULE.ResponseSchemaError, match="child_chunks"):
        MODULE.parse_dify_response(
            {"records": [{"segment": {}, "child_chunks": "not-an-array"}]}
        )


def test_transient_failures_are_retried_until_success(no_backoff):
    session = FakeSession(
        MODULE.requests.RequestException("connection reset"),
        FakeResponse(429, text="rate limited"),
        FakeResponse(503, text="unavailable"),
        FakeResponse(200, {"ok": True}),
    )
    assert MODULE._post_json(session, "http://x", {"q": 1}, 1.0, 4) == {"ok": True}
    assert len(session.calls) == 4
    assert no_backoff == [0.5, 1.0, 2.0]


def test_ordinary_4xx_is_not_retried_and_401_aborts_immediately(no_backoff):
    session = FakeSession(FakeResponse(404, text="not found"), FakeResponse(200, {"ok": True}))
    with pytest.raises(MODULE.RetrievalError, match="HTTP 404"):
        MODULE._post_json(session, "http://x", {}, 1.0, 3)
    assert len(session.calls) == 1

    for status in (401, 403):
        session = FakeSession(FakeResponse(status, text="unauthorized"))
        with pytest.raises(MODULE.AuthenticationError, match=str(status)):
            MODULE._post_json(session, "http://x", {}, 1.0, 3)
        assert len(session.calls) == 1
    assert no_backoff == []


def test_retry_budget_is_exhausted_and_bad_bodies_are_schema_errors(no_backoff):
    session = FakeSession(FakeResponse(500, text="boom"), FakeResponse(500, text="boom"))
    with pytest.raises(MODULE.RetrievalError, match="HTTP 500"):
        MODULE._post_json(session, "http://x", {}, 1.0, 2)
    assert len(session.calls) == 2

    with pytest.raises(MODULE.ResponseSchemaError, match="非 JSON"):
        MODULE._post_json(FakeSession(FakeResponse(200, None, "<html>")), "http://x", {}, 1.0, 2)
    with pytest.raises(MODULE.ResponseSchemaError, match="object"):
        MODULE._post_json(FakeSession(FakeResponse(200, ["a"])), "http://x", {}, 1.0, 2)


def test_arag_success_false_is_retrieval_error_and_missing_flag_is_schema_error(no_backoff):
    session = FakeSession(FakeResponse(200, {"success": False, "responseMessage": "boom"}))
    with pytest.raises(MODULE.RetrievalError, match="boom"):
        MODULE.call_arag_api(session, "query", "dataset", 1.0, 1)

    session = FakeSession(FakeResponse(200, {"content": []}))
    with pytest.raises(MODULE.ResponseSchemaError, match="success"):
        MODULE.call_arag_api(session, "query", "dataset", 1.0, 1)


def test_call_dify_api_selects_endpoint_and_forwards_search_method(no_backoff):
    session = FakeSession(FakeResponse(200, {"records": []}))
    MODULE.call_dify_api(
        session, "query", "dataset-id", 1.0, 1, 8, False,
        search_method="coverage_search",
    )
    call = session.calls[0]
    assert call["url"] == MODULE.DIFY_URL.format(dataset_id="dataset-id")
    assert call["payload"]["retrieval_model"]["search_method"] == "coverage_search"
    assert call["payload"]["retrieval_model"]["top_k"] == 8

    session = FakeSession(FakeResponse(200, {"records": []}))
    MODULE.call_dify_api(
        session, "query", "dataset-id", 1.0, 1, 5, False,
        dify_api_mode="dataset-api",
    )
    assert session.calls[0]["url"] == MODULE.DIFY_DATASET_API_URL.format(
        dataset_id="dataset-id"
    )


def test_build_headers_requires_both_credentials_and_uses_backend_token_name(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_COOKIE", "cookie-value")
    monkeypatch.delenv("RETRIEVAL_XSRF_TOKEN", raising=False)
    with pytest.raises(MODULE.AuthenticationError, match="RETRIEVAL_XSRF_TOKEN"):
        MODULE.build_headers("arag")

    monkeypatch.setenv("RETRIEVAL_XSRF_TOKEN", "token-value")
    arag = MODULE.build_headers("arag")
    dify = MODULE.build_headers("dify")
    assert arag["Cookie"] == dify["Cookie"] == "cookie-value"
    assert arag["x-xsrf-token"] == "token-value" and "x-csrf-token" not in arag
    assert dify["x-csrf-token"] == "token-value" and "x-xsrf-token" not in dify
    assert "Cookie" not in MODULE.ARAG_HEADERS and "Cookie" not in MODULE.DIFY_HEADERS


def test_dataset_api_headers_require_api_key_and_do_not_use_cookie(monkeypatch):
    monkeypatch.delenv("DIFY_DATASET_API_KEY", raising=False)
    with pytest.raises(MODULE.AuthenticationError, match="DIFY_DATASET_API_KEY"):
        MODULE.build_headers("dify", "dataset-api")

    monkeypatch.setenv("DIFY_DATASET_API_KEY", "dataset-secret")
    headers = MODULE.build_headers("dify", "dataset-api")
    assert headers["Authorization"] == "Bearer dataset-secret"
    assert "Cookie" not in headers
    assert "x-csrf-token" not in headers


def test_dataset_api_manifest_records_transport_without_persisting_key(monkeypatch):
    monkeypatch.setenv("DIFY_DATASET_API_KEY", "dataset-secret")
    monkeypatch.setattr(MODULE, "_git_info", lambda: {"commit": "abc", "dirty": False})
    exam = {
        "exam_meta": {
            "exam_id": "exam",
            "corpus": {"snapshot_id": "s", "relative_dir": "c", "documents": []},
        }
    }
    args = argparse.Namespace(
        backend="dify", dataset_id="dataset", dataset_revision="revision",
        dify_api_mode="dataset-api", dify_search_method="hybrid_search",
        graph_search=False, score_threshold_enabled=True, score_threshold=None,
        request_k=5, eval_k=[1, 3, 5], primary_k=5, char_budgets=[1000],
        raw_content_limit=800,
    )
    manifest = MODULE.build_manifest(
        exam, {"exam_sha256": "hash", "corpus_drift": False}, args,
        "20260804_000000", "full",
    )
    serialized = json.dumps(manifest)
    assert manifest["dify_api_mode"] == "dataset-api"
    assert manifest["auth_mode"] == "bearer_dataset_api_key"
    assert manifest["endpoint_type"] == "dataset_retrieve"
    assert "dataset-secret" not in serialized


def test_backend_defaults_resolve_dataset_id_and_output_root(tmp_path):
    assert MODULE.resolve_dataset_id(
        argparse.Namespace(backend="arag", dataset_id=None)
    ) == MODULE.ARAG_DATASET_ID
    assert MODULE.resolve_dataset_id(
        argparse.Namespace(backend="dify", dataset_id=None)
    ) == MODULE.DIFY_DATASET_ID
    assert MODULE.resolve_dataset_id(
        argparse.Namespace(backend="dify", dataset_id="custom")
    ) == "custom"
    assert MODULE.resolve_output_root(None, "arag") == MODULE.RESULTS_ROOT / "考试结果-v3-arag"
    assert MODULE.resolve_output_root(None, "dify") == MODULE.RESULTS_ROOT / "考试结果-v3-dify"
    assert (
        MODULE.resolve_output_root(None, "dify", "考试结果-多跳")
        == MODULE.RESULTS_ROOT / "考试结果-多跳-dify"
    )
    assert MODULE.resolve_output_root(str(tmp_path), "dify") == tmp_path


def test_default_cli_arguments_are_accepted():
    args = MODULE.parse_args([])
    assert args.backend == "arag"
    assert args.primary_k == 5
    assert args.eval_k == [1, 3, 5, 10]
    assert args.char_budgets == [1000, 2000, 4000]
    assert args.dify_search_method == "hybrid_search"
    assert args.dify_api_mode == "console"
    assert args.graph_search is False


def test_invalid_numeric_cli_arguments_are_rejected():
    for argv in (
        ["--primary-k", "0"],
        ["--primary-k", "7"],
        ["--retries", "-1"],
        ["--limit", "-1"],
        ["--sleep", "-0.5"],
        ["--timeout", "0"],
        ["--eval-k", "1,1,3"],
        ["--eval-k", "0,5"],
        ["--char-budgets", "-100"],
    ):
        with pytest.raises(SystemExit):
            MODULE.parse_args(argv)


def test_dify_only_flags_are_rejected_on_arag_and_coverage_excludes_graph_search():
    for argv in (
        ["--backend", "arag", "--graph-search"],
        ["--backend", "arag", "--dify-api-mode", "dataset-api"],
        ["--backend", "arag", "--dify-search-method", "coverage_search"],
        ["--backend", "arag", "--score-threshold", "0.3"],
        ["--backend", "arag", "--score-threshold-enabled"],
        ["--backend", "dify", "--dify-search-method", "coverage_search", "--graph-search"],
        ["--backend", "dify", "--request-k", "5"],
    ):
        with pytest.raises(SystemExit):
            MODULE.parse_args(argv)
    args = MODULE.parse_args(["--backend", "dify", "--score-threshold", "0.3"])
    assert args.score_threshold_enabled is True
