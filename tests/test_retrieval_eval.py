from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "评测脚本" / "retrieval_eval.py"
SPEC = importlib.util.spec_from_file_location("retrieval_eval", SCRIPT)
assert SPEC and SPEC.loader
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def gt(*evidence, image_url=None, keywords=None):
    chunks = [
        {"source_document": document, "section": section, "snippet": snippet}
        for document, section, snippet in evidence
    ]
    return {
        "gt_docs": list(dict.fromkeys(item[0] for item in evidence)),
        "gt_chunks": chunks,
        "keyword_match": keywords or [],
        "expected_image_url": image_url,
        "requires_tool": False,
        "is_adversarial": False,
    }


def retrieved(rank, document, content, *, score=1.0, chunk_id=None):
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


def metrics(ground_truth, chunks, top_k=5):
    return evaluator.compute_metrics(
        ground_truth,
        chunks,
        top_k=top_k,
        rank_source="response",
        ranking_anomaly=False,
    )


def question(question_id="q_001", *, image_url=None):
    snippet = f"证据 {image_url}" if image_url else "必需证据"
    return {
        "id": question_id,
        "difficulty": "hard",
        "type": "cross_document",
        "question": "测试问题？",
        "retrieved_chunks": [
            {
                "source_document": "A.md",
                "section": "章节",
                "snippet": snippet,
            }
        ],
        "eval_criteria": {"expected_image_url": image_url} if image_url else {},
    }


def exam_payload(questions=None):
    items = questions or [question()]
    return {
        "exam_meta": {"exam_id": "exam-test", "title": "测试", "total_questions": len(items)},
        "questions": items,
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_correct_document_wrong_section_does_not_score():
    result = metrics(
        gt(("A.md", "目标章节", "真正的证据片段")),
        [retrieved(1, "A.md", "这是同一文档中的错误章节")],
    )

    assert result["doc_recall@k"] == 1.0
    assert result["evidence_recall@k"] == 0.0
    assert result["score"] == 0.0
    assert result["snippet_detail"][0]["matched_rank"] is None


def test_partial_multi_document_and_multi_evidence_recall():
    result = metrics(
        gt(("A.md", "一", "甲证据"), ("B.md", "二", "乙证据")),
        [retrieved(1, "A.md", "前文 甲证据 后文"), retrieved(2, "C.md", "噪声")],
        top_k=2,
    )

    assert result["evidence_recall@k"] == 0.5
    assert result["evidence_precision@k"] == 0.5
    assert result["doc_recall@k"] == 0.5
    assert result["all_evidence_hit@k"] is False
    assert result["score"] == 0.5


def test_keyword_coverage_is_diagnostic_and_does_not_change_score():
    result = metrics(
        gt(("A.md", "一", "完整证据片段"), keywords=["诊断词/别名"]),
        [retrieved(1, "A.md", "只含诊断词，不含完整证据")],
    )

    assert result["keyword_coverage"] == 1.0
    assert result["evidence_recall@k"] == 0.0
    assert result["score"] == 0.0


def test_duplicate_documents_do_not_inflate_unique_precision():
    result = metrics(
        gt(("A.md", "一", "甲证据")),
        [
            retrieved(1, "A.md", "甲证据"),
            retrieved(2, "A.md", "另一个块"),
            retrieved(3, "C.md", "噪声"),
        ],
        top_k=3,
    )

    assert result["unique_doc_precision@k"] == 0.5
    assert result["duplicate_doc_rate@k"] == pytest.approx(0.3333)


def test_image_url_must_be_covered_in_every_required_source():
    url = "https://example.com/chart.png"
    result = metrics(
        gt(
            ("A.md", "图", f"A 图 ![说明]({url})"),
            ("B.md", "图", f"B 图 ![]({url})"),
            image_url=url,
        ),
        [
            retrieved(1, "A.md", f"A 图 ![]({url})"),
            retrieved(2, "B.md", "B 图存在，但图片 URL 丢失"),
        ],
        top_k=2,
    )

    assert result["image_source_coverage"] == 0.5
    assert result["image_hit"] is False
    assert [item["hit"] for item in result["image_source_detail"]] == [True, False]


def test_normalization_handles_markdown_image_alt_dashes_quotes_and_whitespace():
    url = "https://example.com/a.png"
    expected = f'**风险 – 分级** “说明” ![不同 alt]({url})'
    actual = f'风险-分级\n"说明" ![]({url})'

    assert evaluator.normalize_text(expected) == evaluator.normalize_text(actual)
    result = metrics(gt(("A.md", "图", expected)), [retrieved(1, "A.md", actual)])
    assert result["evidence_recall@k"] == 1.0
    assert result["snippet_detail"][0]["match_type"] == "normalized_exact"


def test_top_k_excludes_later_evidence_from_recall_and_mrr():
    result = metrics(
        gt(("A.md", "一", "目标证据")),
        [retrieved(1, "B.md", "噪声"), retrieved(2, "A.md", "目标证据")],
        top_k=1,
    )

    assert result["evidence_recall@k"] == 0.0
    assert result["evidence_mrr@k"] == 0.0
    assert result["doc_mrr@k"] == 0.0


def test_response_and_relevance_ranking_modes_are_auditable():
    response = {
        "success": True,
        "content": [
            {"documentName": "A.md", "chunkId": "a", "top": 8, "relevanceScore": 0.2},
            {"documentName": "B.md", "chunkId": "b", "top": 2, "relevanceScore": 0.9},
            {"documentName": "C.md", "chunkId": "c", "top": 3, "relevanceScore": None},
        ],
    }

    response_order, anomaly = evaluator.parse_retrieved_chunks(response, "response")
    relevance_order, _ = evaluator.parse_retrieved_chunks(response, "relevance")

    assert anomaly is True
    assert [item["document_name"] for item in response_order] == ["A.md", "B.md", "C.md"]
    assert [item["document_name"] for item in relevance_order] == ["B.md", "A.md", "C.md"]
    assert relevance_order[0]["original_index"] == 2
    assert relevance_order[0]["server_top"] == 2
    assert relevance_order[0]["relevance_score"] == 0.9
    audited = metrics(gt(("B.md", "一", "" + "证据")), [dict(relevance_order[0], content="证据")])
    assert audited["retrieved_chunk_detail"][0]["original_index"] == 2


def test_missing_response_content_is_non_retryable_schema_error():
    with pytest.raises(evaluator.ResponseSchemaError, match="content"):
        evaluator.parse_retrieved_chunks({"success": True})


def test_retry_429_and_5xx_then_succeed(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(429, text="rate limit"),
            FakeResponse(503, text="unavailable"),
            FakeResponse(200, {"success": True, "content": []}),
        ]
    )
    sleeps = []
    monkeypatch.setattr(evaluator.time, "sleep", sleeps.append)

    response = evaluator.call_retrieval_api(session, "query", "dataset", 1.0, 3)

    assert response["success"] is True
    assert session.calls == 3
    assert sleeps == [0.5, 1.0]


def test_network_timeout_retries_and_normal_4xx_does_not(monkeypatch):
    monkeypatch.setattr(evaluator.time, "sleep", lambda _seconds: None)
    timeout_session = FakeSession(
        [requests.Timeout("slow"), FakeResponse(200, {"success": True, "content": []})]
    )
    assert evaluator.call_retrieval_api(timeout_session, "q", "d", 1.0, 2)["success"]
    assert timeout_session.calls == 2

    bad_request_session = FakeSession([FakeResponse(400, text="bad request")])
    with pytest.raises(evaluator.RetrievalError, match="HTTP 400"):
        evaluator.call_retrieval_api(bad_request_session, "q", "d", 1.0, 3)
    assert bad_request_session.calls == 1


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_error_terminates_without_retry(status):
    session = FakeSession([FakeResponse(status, text="expired")])
    with pytest.raises(evaluator.AuthenticationError):
        evaluator.call_retrieval_api(session, "q", "d", 1.0, 3)
    assert session.calls == 1


def test_authentication_abort_preserves_partial_results_without_valid_score():
    questions = [question("q_001"), question("q_002"), question("q_003")]
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "content": [
                        {"documentName": "A.md", "chunkId": "a", "content": "必需证据"}
                    ],
                },
            ),
            FakeResponse(401, text="expired"),
        ]
    )
    args = SimpleNamespace(
        limit=0,
        dataset_id="dataset",
        timeout=1.0,
        retries=1,
        rank_by="response",
        top_k=5,
        sleep=0.0,
    )

    with pytest.raises(evaluator.EvaluationAborted) as caught:
        evaluator.evaluate_exam(exam_payload(questions), session, args)

    error = caught.value
    assert error.total_questions == 3
    assert [item["status"] for item in error.partial_results] == ["ok", "auth_error"]
    summary = evaluator.aggregate(
        error.partial_results,
        "response",
        run_status="aborted_auth",
        total_expected=error.total_questions,
    )
    assert summary["run_status"] == "aborted_auth"
    assert summary["overall"]["end_to_end_score"] is None
    assert summary["retrieval_capability_score"] is None


def test_eighteen_failed_hard_questions_make_total_score_40_not_80():
    results = [
        {"id": f"q_{index:03d}", "difficulty": "hard", "type": "complex", "status": "error"}
        for index in range(1, 19)
    ]
    successful_metrics = {
        "score": 1.0,
        "evidence_recall@k": 1.0,
        "evidence_precision@k": 1.0,
        "doc_recall@k": 1.0,
        "unique_doc_precision@k": 1.0,
        "evidence_mrr@k": 1.0,
        "all_evidence_hit@k": True,
        "ranking_anomaly": False,
    }
    results.extend(
        {
            "id": f"q_{index:03d}",
            "difficulty": "medium",
            "type": "complex",
            "status": "ok",
            "metrics": dict(successful_metrics),
        }
        for index in range(19, 31)
    )

    summary = evaluator.aggregate(results, "response")

    assert summary["overall"]["end_to_end_score"] == 0.4
    assert summary["retrieval_capability_score"] == 40.0
    assert summary["overall"]["quality_score_on_success"] == 1.0


def test_exam_validation_defaults_report_and_output_schema(tmp_path):
    exam_file = tmp_path / "exam.json"
    exam_file.write_text(json.dumps(exam_payload(), ensure_ascii=False), encoding="utf-8")
    loaded = evaluator.load_and_validate_exam(exam_file)
    args = evaluator.parse_args([])

    assert Path(args.exam_dir) == ROOT / "基于语料生成的评测集" / "考卷"
    assert Path(args.out_dir) == ROOT / "基于语料生成的评测集" / "考试结果"
    assert args.rank_by == "response"

    result_metrics = metrics(gt(("A.md", "章节", "必需证据")), [retrieved(1, "A.md", "必需证据")])
    results = [
        {
            "id": "q_001",
            "difficulty": "hard",
            "type": "cross_document",
            "question": "测试问题？",
            "has_expected_image": False,
            "status": "ok",
            "metrics": result_metrics,
        }
    ]
    summary = evaluator.aggregate(results, "response")
    report = evaluator.build_markdown_report(loaded["exam_meta"], summary, results, 5)
    assert "未命中证据" not in report
    assert "Evidence Recall@5" in report
    assert "重复文档率" in report
    assert "排名异常" in report

    out_dir = evaluator.write_outputs(
        str(tmp_path / "results"), loaded["exam_meta"], summary, results, 5, "20260715_000000"
    )
    output_names = {path.name for path in out_dir.iterdir()}
    assert output_names == {
        "results_20260715_000000.json",
        "summary_20260715_000000.json",
        "report_20260715_000000.md",
    }
    result_payload = json.loads((out_dir / "results_20260715_000000.json").read_text())
    assert result_payload["metrics_version"] == "2.0"
    assert json.loads((out_dir / "summary_20260715_000000.json").read_text())["metrics_version"] == "2.0"


def test_report_lists_missing_evidence_and_per_source_image_coverage():
    url = "https://example.com/chart.png"
    result_metrics = metrics(
        gt(
            ("A.md", "图一", f"甲证据 ![]({url})"),
            ("B.md", "图二", f"乙证据 ![]({url})"),
            image_url=url,
        ),
        [retrieved(1, "A.md", f"甲证据 ![]({url})")],
    )
    results = [
        {
            "id": "q_001",
            "difficulty": "hard",
            "type": "multimodal",
            "question": "图片问题？",
            "has_expected_image": True,
            "status": "ok",
            "metrics": result_metrics,
        }
    ]
    report = evaluator.build_markdown_report(
        {"exam_id": "exam", "title": "测试"},
        evaluator.aggregate(results, "response"),
        results,
        5,
    )

    assert "未命中证据" in report
    assert "乙证据" in report
    assert "B.md: 未命中" in report
    assert "A.md: 命中" in report


def test_exam_validation_rejects_duplicate_ids_and_unreferenced_image(tmp_path):
    duplicate = exam_payload([question("q_001"), question("q_001")])
    duplicate_file = tmp_path / "duplicate.json"
    duplicate_file.write_text(json.dumps(duplicate, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(evaluator.ExamValidationError, match="题号重复"):
        evaluator.load_and_validate_exam(duplicate_file)

    invalid_image = exam_payload([question(image_url="https://example.com/a.png")])
    invalid_image["questions"][0]["retrieved_chunks"][0]["snippet"] = "无图片"
    invalid_file = tmp_path / "invalid-image.json"
    invalid_file.write_text(json.dumps(invalid_image, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(evaluator.ExamValidationError, match="未出现在任何 GT snippet"):
        evaluator.load_and_validate_exam(invalid_file)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--top-k", "0"],
        ["--limit", "-1"],
        ["--timeout", "0"],
        ["--retries", "0"],
        ["--sleep", "-0.1"],
    ],
)
def test_invalid_numeric_cli_arguments_are_rejected(arguments):
    with pytest.raises(SystemExit):
        evaluator.parse_args(arguments)


def test_validate_only_needs_no_credentials_or_network(tmp_path, monkeypatch):
    exam_file = tmp_path / "exam.json"
    exam_file.write_text(json.dumps(exam_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("RETRIEVAL_COOKIE", raising=False)
    monkeypatch.delenv("RETRIEVAL_XSRF_TOKEN", raising=False)

    assert evaluator.main(["--validate-only", "--exam-dir", str(tmp_path), "--exam", exam_file.name]) == 0


def test_formal_run_requires_both_credentials(tmp_path, monkeypatch):
    exam_file = tmp_path / "exam.json"
    exam_file.write_text(json.dumps(exam_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("RETRIEVAL_COOKIE", "cookie")
    monkeypatch.delenv("RETRIEVAL_XSRF_TOKEN", raising=False)

    assert evaluator.main(["--exam-dir", str(tmp_path), "--exam", exam_file.name]) == 3
