# -*- coding: utf-8 -*-
"""单知识库检索召回评测脚本（metrics v2.0）。

以考卷中的 ``source_document + snippet`` 作为必需证据，评估 Top-K
检索结果的文档覆盖、证据覆盖、排序和多模态图片来源覆盖。脚本不评估答案
正确性、推理过程、拒答行为或工具调用行为。

正式评测必须通过环境变量提供 ``RETRIEVAL_COOKIE`` 和
``RETRIEVAL_XSRF_TOKEN``。使用 ``--validate-only`` 可在无凭证、无网络的
情况下校验考卷。
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError:
    sys.stderr.write("缺少依赖 requests，请先执行: pip install -r requirements.txt\n")
    raise


METRICS_VERSION = "2.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAM_DIR = PROJECT_ROOT / "基于语料生成的评测集" / "考卷"
DEFAULT_OUT_DIR = PROJECT_ROOT / "基于语料生成的评测集" / "考试结果"

BASE_URL = (
    "https://deap.dingtalk.com/deapHttpProxy/studio2/proxy/dataset/fe/api/v2/"
    "dataset/manage/retrieval_test.json"
)
DATASET_ID = "844b8ded-9bf4-44b6-bc1a-a5ccee745832"
CONTENT_TRUNCATE = 800
ALLOWED_DIFFICULTIES = {"simple", "medium", "hard"}
RETRYABLE_STATUS_CODES = {408, 429}

BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "content-type": "application/json",
    "origin": "https://deap.dingtalk.com",
    "referer": "https://deap.dingtalk.com/fe/index",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


class RetrievalError(Exception):
    """单题检索失败，可记录为该题 0 分。"""


class AuthenticationError(RetrievalError):
    """凭证缺失或失效，必须终止整场评测。"""


class ResponseSchemaError(RetrievalError):
    """接口响应结构不符合预期。"""


class ExamValidationError(ValueError):
    """考卷结构或字段不合法。"""


class EvaluationAborted(AuthenticationError):
    """评测过程中鉴权失效，并携带已经完成的部分结果。"""

    def __init__(
        self,
        message: str,
        partial_results: List[Dict[str, Any]],
        total_questions: int,
    ) -> None:
        super().__init__(message)
        self.partial_results = partial_results
        self.total_questions = total_questions


def build_headers() -> Dict[str, str]:
    """从环境变量构造请求头；缺少凭证时拒绝正式评测。"""
    cookie = os.environ.get("RETRIEVAL_COOKIE", "").strip()
    xsrf = os.environ.get("RETRIEVAL_XSRF_TOKEN", "").strip()
    missing = []
    if not cookie:
        missing.append("RETRIEVAL_COOKIE")
    if not xsrf:
        missing.append("RETRIEVAL_XSRF_TOKEN")
    if missing:
        raise AuthenticationError(
            "缺少检索接口凭证环境变量: " + ", ".join(missing)
        )
    headers = dict(BASE_HEADERS)
    headers["Cookie"] = cookie
    headers["x-xsrf-token"] = xsrf
    return headers


def _backoff(attempt: int) -> float:
    return 0.5 * (2 ** (attempt - 1))


def call_retrieval_api(
    session: "requests.Session",
    query: str,
    dataset_id: str,
    timeout: float,
    retries: int,
) -> Dict[str, Any]:
    """调用检索接口，仅重试网络异常、408、429 和 5xx。"""
    payload = {"datasetId": dataset_id, "query": query}
    last_error = "未知错误"

    for attempt in range(1, retries + 1):
        try:
            response = session.post(BASE_URL, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"网络异常: {exc}"
            retryable = True
        else:
            status = response.status_code
            if status in (401, 403):
                raise AuthenticationError(
                    f"鉴权失败(HTTP {status})，请更新 RETRIEVAL_COOKIE / "
                    "RETRIEVAL_XSRF_TOKEN"
                )
            retryable = status in RETRYABLE_STATUS_CODES or status >= 500
            if status != 200:
                message = response.text[:200].replace("\n", " ")
                last_error = f"HTTP {status}: {message}"
                if not retryable:
                    raise RetrievalError(last_error)
            else:
                try:
                    data = response.json()
                except ValueError as exc:
                    snippet = response.text[:200].replace("\n", " ")
                    raise ResponseSchemaError(
                        f"返回非 JSON，可能登录态失效或接口异常: {snippet}"
                    ) from exc
                if not isinstance(data, dict):
                    raise ResponseSchemaError("接口响应根节点必须是 JSON object")
                if not isinstance(data.get("success"), bool):
                    raise ResponseSchemaError("接口响应缺少布尔字段 success")
                return data

        if attempt < retries and retryable:
            time.sleep(_backoff(attempt))
        elif retryable:
            break

    raise RetrievalError(last_error)


def _numeric_score(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def parse_retrieved_chunks(
    response: Dict[str, Any],
    rank_by: str = "response",
) -> Tuple[List[Dict[str, Any]], bool]:
    """解析 Chunk，返回排序后列表及响应顺序相关度异常标记。"""
    if "content" not in response:
        raise ResponseSchemaError("接口响应缺少 content 字段")
    content = response["content"]
    if not isinstance(content, list):
        raise ResponseSchemaError("接口响应 content 必须是数组")

    chunks: List[Dict[str, Any]] = []
    for index, item in enumerate(content, 1):
        if not isinstance(item, dict):
            raise ResponseSchemaError(f"content[{index - 1}] 必须是 object")
        chunks.append(
            {
                "original_index": index,
                "server_top": item.get("top"),
                "relevance_score": _numeric_score(item.get("relevanceScore")),
                "document_name": str(item.get("documentName") or ""),
                "document_id": str(item.get("documentId") or ""),
                "chunk_id": str(item.get("chunkId") or ""),
                "content": str(item.get("content") or ""),
            }
        )

    response_scores = [
        chunk["relevance_score"]
        for chunk in chunks
        if chunk["relevance_score"] is not None
    ]
    ranking_anomaly = any(
        current > previous
        for previous, current in zip(response_scores, response_scores[1:])
    )

    if rank_by == "relevance":
        chunks.sort(
            key=lambda chunk: (
                chunk["relevance_score"] is None,
                -(chunk["relevance_score"] or 0.0),
                chunk["original_index"],
            )
        )
    elif rank_by != "response":
        raise ValueError(f"不支持的 rank_by: {rank_by}")

    for rank, chunk in enumerate(chunks, 1):
        chunk["rank"] = rank
    return chunks, ranking_anomaly


_DASH_TRANSLATION = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
)
_QUOTE_TRANSLATION = str.maketrans(
    {"“": '"', "”": '"', "‘": "'", "’": "'", "＂": '"'}
)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def normalize_text(text: str) -> str:
    """为确定性证据匹配做 Unicode 与 Markdown 归一化。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.translate(_DASH_TRANSLATION).translate(_QUOTE_TRANSLATION)
    normalized = _IMAGE_RE.sub(lambda match: f" {match.group(1)} ", normalized)
    normalized = _LINK_RE.sub(
        lambda match: f" {match.group(1)} {match.group(2)} ", normalized
    )
    normalized = re.sub(r"[*_`~#>]", "", normalized)
    normalized = normalized.lower()
    return re.sub(r"\s+", "", normalized)


def keyword_group_hit(group: str, normalized_content: str) -> bool:
    """关键词组以 / 表示 OR；关键词仅作为诊断信号。"""
    alternatives = [item.strip() for item in str(group).split("/") if item.strip()]
    return any(
        (candidate := normalize_text(alternative))
        and candidate in normalized_content
        for alternative in alternatives
    )


def extract_gt(question: Dict[str, Any]) -> Dict[str, Any]:
    gt_chunks = question["retrieved_chunks"]
    gt_docs = list(dict.fromkeys(chunk["source_document"] for chunk in gt_chunks))
    criteria = question.get("eval_criteria") or {}
    return {
        "gt_docs": gt_docs,
        "gt_chunks": gt_chunks,
        "keyword_match": criteria.get("keyword_match") or [],
        "expected_image_url": criteria.get("expected_image_url"),
        "requires_tool": bool(criteria.get("requires_tool", False)),
        "is_adversarial": bool(criteria.get("is_adversarial", False)),
    }


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def _round(value: float) -> float:
    return round(value, 4)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _chunk_matches_evidence(
    chunk: Dict[str, Any],
    normalized_evidence: Sequence[Tuple[str, str]],
) -> bool:
    chunk_content = normalize_text(chunk["content"])
    return any(
        source_document == chunk["document_name"]
        and snippet
        and snippet in chunk_content
        for source_document, snippet in normalized_evidence
    )


def compute_metrics(
    gt: Dict[str, Any],
    retrieved: List[Dict[str, Any]],
    top_k: int,
    rank_source: str,
    ranking_anomaly: bool,
) -> Dict[str, Any]:
    """计算单题 v2.0 文档、证据、排序和图片来源指标。"""
    top_chunks = retrieved[:top_k]
    gt_docs: List[str] = gt["gt_docs"]
    top_docs = [chunk["document_name"] for chunk in top_chunks]
    unique_top_docs = _unique(top_docs)

    hit_docs = [doc for doc in gt_docs if doc in set(unique_top_docs)]
    doc_recall = len(hit_docs) / len(gt_docs)
    relevant_unique_docs = sum(doc in set(gt_docs) for doc in unique_top_docs)
    unique_doc_precision = (
        relevant_unique_docs / len(unique_top_docs) if unique_top_docs else 0.0
    )
    doc_hit_at_1 = bool(top_docs and top_docs[0] in gt_docs)
    doc_hit_at_k = bool(hit_docs)
    all_docs_hit_at_k = len(hit_docs) == len(gt_docs)
    doc_mrr_at_k = 0.0
    for chunk in top_chunks:
        if chunk["document_name"] in gt_docs:
            doc_mrr_at_k = 1.0 / chunk["rank"]
            break

    duplicate_doc_rate = (
        (len(top_docs) - len(unique_top_docs)) / len(top_docs) if top_docs else 0.0
    )

    normalized_evidence = [
        (chunk["source_document"], normalize_text(chunk["snippet"]))
        for chunk in gt["gt_chunks"]
    ]
    snippet_detail: List[Dict[str, Any]] = []
    for gt_chunk, (source_document, normalized_snippet) in zip(
        gt["gt_chunks"], normalized_evidence
    ):
        matched_chunk: Optional[Dict[str, Any]] = None
        for chunk in top_chunks:
            if (
                chunk["document_name"] == source_document
                and normalized_snippet
                and normalized_snippet in normalize_text(chunk["content"])
            ):
                matched_chunk = chunk
                break
        snippet_detail.append(
            {
                "source_document": source_document,
                "section": gt_chunk.get("section", ""),
                "snippet": gt_chunk["snippet"],
                "hit": matched_chunk is not None,
                "matched_rank": matched_chunk["rank"] if matched_chunk else None,
                "matched_chunk_id": matched_chunk["chunk_id"] if matched_chunk else None,
                "match_type": "normalized_exact" if matched_chunk else None,
            }
        )

    evidence_hits = sum(detail["hit"] for detail in snippet_detail)
    evidence_count = len(snippet_detail)
    evidence_recall = evidence_hits / evidence_count
    chunk_relevance = [
        _chunk_matches_evidence(chunk, normalized_evidence) for chunk in top_chunks
    ]
    evidence_precision = (
        sum(chunk_relevance) / len(top_chunks) if top_chunks else 0.0
    )
    evidence_hit_at_1 = bool(chunk_relevance and chunk_relevance[0])
    evidence_hit_at_k = any(chunk_relevance)
    all_evidence_hit_at_k = evidence_hits == evidence_count
    evidence_mrr_at_k = 0.0
    for chunk, relevant in zip(top_chunks, chunk_relevance):
        if relevant:
            evidence_mrr_at_k = 1.0 / chunk["rank"]
            break

    normalized_top_content = normalize_text(
        "".join(chunk["content"] for chunk in top_chunks)
    )
    keyword_detail = []
    keyword_hits = 0
    for group in gt["keyword_match"]:
        hit = keyword_group_hit(group, normalized_top_content)
        keyword_detail.append({"keyword": group, "hit": hit})
        keyword_hits += int(hit)
    keyword_coverage: Optional[float] = None
    if gt["keyword_match"]:
        keyword_coverage = keyword_hits / len(gt["keyword_match"])

    expected_image_url = gt["expected_image_url"]
    image_source_detail: List[Dict[str, Any]] = []
    image_source_coverage: Optional[float] = None
    image_hit: Optional[bool] = None
    if expected_image_url:
        required_image_docs = gt_docs
        for source_document in required_image_docs:
            matched = next(
                (
                    chunk
                    for chunk in top_chunks
                    if chunk["document_name"] == source_document
                    and expected_image_url in chunk["content"]
                ),
                None,
            )
            image_source_detail.append(
                {
                    "source_document": source_document,
                    "hit": matched is not None,
                    "matched_rank": matched["rank"] if matched else None,
                    "matched_chunk_id": matched["chunk_id"] if matched else None,
                }
            )
        image_source_coverage = (
            sum(detail["hit"] for detail in image_source_detail)
            / len(image_source_detail)
        )
        image_hit = image_source_coverage == 1.0

    score = evidence_recall
    return {
        "metrics_version": METRICS_VERSION,
        "rank_source": rank_source,
        "ranking_anomaly": ranking_anomaly,
        "top_k": top_k,
        "gt_docs": gt_docs,
        "gt_evidence_count": evidence_count,
        "retrieved_docs_topk": top_docs,
        "retrieved_docs_all": [chunk["document_name"] for chunk in retrieved],
        "retrieved_chunk_detail": [
            {
                "rank": chunk["rank"],
                "original_index": chunk["original_index"],
                "server_top": chunk["server_top"],
                "relevance_score": chunk["relevance_score"],
                "document_name": chunk["document_name"],
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
            }
            for chunk in retrieved
        ],
        "doc_recall@k": _round(doc_recall),
        "unique_doc_precision@k": _round(unique_doc_precision),
        "doc_hit@1": doc_hit_at_1,
        "doc_hit@k": doc_hit_at_k,
        "all_docs_hit@k": all_docs_hit_at_k,
        "doc_mrr@k": _round(doc_mrr_at_k),
        "duplicate_doc_rate@k": _round(duplicate_doc_rate),
        "evidence_recall@k": _round(evidence_recall),
        "evidence_precision@k": _round(evidence_precision),
        "evidence_hit@1": evidence_hit_at_1,
        "evidence_hit@k": evidence_hit_at_k,
        "all_evidence_hit@k": all_evidence_hit_at_k,
        "evidence_mrr@k": _round(evidence_mrr_at_k),
        "snippet_detail": snippet_detail,
        "keyword_coverage": _round(keyword_coverage) if keyword_coverage is not None else None,
        "keyword_detail": keyword_detail,
        "expected_image_url": expected_image_url,
        "image_source_coverage": (
            _round(image_source_coverage)
            if image_source_coverage is not None
            else None
        ),
        "image_source_detail": image_source_detail,
        "image_hit": image_hit,
        "is_adversarial": gt["is_adversarial"],
        "requires_tool": gt["requires_tool"],
        "score": _round(score),
    }


def _metric_or_zero(result: Dict[str, Any], field: str) -> float:
    metrics = result.get("metrics")
    if not metrics:
        return 0.0
    value = metrics.get(field)
    return float(value) if value is not None else 0.0


def _summarize_group(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(results)
    successful = [result for result in results if result.get("metrics")]
    return {
        "count": count,
        "num_scored": len(successful),
        "request_success_rate": _round(len(successful) / count) if count else 0.0,
        "mean_score": _round(_mean([_metric_or_zero(r, "score") for r in results])),
        "evidence_recall@k": _round(
            _mean([_metric_or_zero(r, "evidence_recall@k") for r in results])
        ),
        "doc_recall@k": _round(
            _mean([_metric_or_zero(r, "doc_recall@k") for r in results])
        ),
    }


def aggregate(
    results: List[Dict[str, Any]],
    rank_source: str,
    run_status: str = "completed",
    total_expected: Optional[int] = None,
) -> Dict[str, Any]:
    """按全部题目微平均；普通异常按 0 分计入。"""
    expected = total_expected if total_expected is not None else len(results)
    attempted = len(results)
    successful = [result for result in results if result.get("metrics")]
    error_count = attempted - len(successful)
    all_scores = [_metric_or_zero(result, "score") for result in results]
    if expected > attempted:
        all_scores.extend([0.0] * (expected - attempted))
    end_to_end_score = _mean(all_scores)
    quality_score = _mean([float(result["metrics"]["score"]) for result in successful])

    image_results = [result for result in results if result.get("has_expected_image")]
    ranking_anomalies = sum(
        bool(result.get("metrics", {}).get("ranking_anomaly")) for result in results
    )

    completed = run_status == "completed"
    overall = {
        "num_questions": expected,
        "num_attempted": attempted,
        "num_scored": len(successful),
        "num_errors": error_count,
        "request_success_rate": _round(len(successful) / attempted) if attempted else 0.0,
        "end_to_end_score": _round(end_to_end_score) if completed else None,
        "quality_score_on_success": _round(quality_score),
        "evidence_recall@k": _round(
            _mean([_metric_or_zero(r, "evidence_recall@k") for r in results])
        ),
        "evidence_precision@k": _round(
            _mean([_metric_or_zero(r, "evidence_precision@k") for r in results])
        ),
        "doc_recall@k": _round(
            _mean([_metric_or_zero(r, "doc_recall@k") for r in results])
        ),
        "unique_doc_precision@k": _round(
            _mean([_metric_or_zero(r, "unique_doc_precision@k") for r in results])
        ),
        "evidence_mrr@k": _round(
            _mean([_metric_or_zero(r, "evidence_mrr@k") for r in results])
        ),
        "all_evidence_hit@k": _round(
            _mean([float(_metric_or_zero(r, "all_evidence_hit@k")) for r in results])
        ),
        "ranking_anomaly_count": ranking_anomalies,
        "image_source_coverage": (
            _round(
                _mean(
                    [
                        _metric_or_zero(result, "image_source_coverage")
                        for result in image_results
                    ]
                )
            )
            if image_results
            else None
        ),
    }

    by_difficulty = {
        difficulty: _summarize_group(
            [result for result in results if result.get("difficulty") == difficulty]
        )
        for difficulty in ("simple", "medium", "hard")
    }
    grouped_types: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        grouped_types.setdefault(result.get("type") or "unknown", []).append(result)
    by_type = {
        question_type: _summarize_group(group)
        for question_type, group in grouped_types.items()
    }

    capability_score: Optional[float]
    if completed:
        capability_score = round(end_to_end_score * 100, 2)
    else:
        capability_score = None
    return {
        "metrics_version": METRICS_VERSION,
        "run_status": run_status,
        "rank_source": rank_source,
        "overall": overall,
        "by_difficulty": by_difficulty,
        "by_type": by_type,
        "retrieval_capability_score": capability_score,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _report_excerpt(value: Any, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    compact = compact.replace("|", "\\|")
    return compact if len(compact) <= limit else compact[:limit] + "…"


def build_markdown_report(
    exam_meta: Dict[str, Any],
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
    top_k: int,
) -> str:
    lines: List[str] = []
    overall = summary["overall"]
    lines.append(f"# 检索召回评测报告 - {exam_meta.get('exam_id', '-')}")
    lines.append("")
    lines.append(f"- Metrics Version: {METRICS_VERSION}")
    lines.append(f"- 运行状态: {summary['run_status']}")
    lines.append(f"- 排名依据: {summary['rank_source']}")
    lines.append(f"- 考卷标题: {exam_meta.get('title', '-')}")
    lines.append(f"- 评测时间: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Top-K: {top_k}")
    lines.append(
        f"- 题目数: {overall['num_questions']}（尝试 {overall['num_attempted']}，"
        f"成功 {overall['num_scored']}，异常 {overall['num_errors']}）"
    )
    lines.append(
        "- 说明: 主分为 GT 证据片段 Recall@K 的全题微平均；普通请求异常按 0 分。"
        "本报告不评答案、推理、拒答或工具调用行为。"
    )
    lines.append("")

    lines.extend(
        [
            "## 总览",
            "",
            "| 指标 | 数值 |",
            "| --- | --- |",
            f"| 检索能力得分(0-100) | {_fmt(summary['retrieval_capability_score'])} |",
            f"| 端到端得分 | {overall['end_to_end_score']} |",
            f"| 成功请求质量分 | {overall['quality_score_on_success']} |",
            f"| 请求成功率 | {overall['request_success_rate']} |",
            f"| Evidence Recall@{top_k} | {overall['evidence_recall@k']} |",
            f"| Evidence Precision@{top_k} | {overall['evidence_precision@k']} |",
            f"| Document Recall@{top_k} | {overall['doc_recall@k']} |",
            f"| Unique Document Precision@{top_k} | {overall['unique_doc_precision@k']} |",
            f"| Evidence MRR@{top_k} | {overall['evidence_mrr@k']} |",
            f"| All Evidence Hit@{top_k} | {overall['all_evidence_hit@k']} |",
            f"| 图片来源覆盖 | {_fmt(overall['image_source_coverage'])} |",
            f"| 排名异常题数 | {overall['ranking_anomaly_count']} |",
            "",
        ]
    )

    lines.extend(
        [
            "## 分难度",
            "",
            "| 难度 | 题数 | 成功率 | 平均得分 | Evidence Recall@K | Doc Recall@K |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for difficulty in ("simple", "medium", "hard"):
        item = summary["by_difficulty"][difficulty]
        lines.append(
            f"| {difficulty} | {item['count']} | {item['request_success_rate']} | "
            f"{item['mean_score']} | {item['evidence_recall@k']} | "
            f"{item['doc_recall@k']} |"
        )
    lines.append("")

    lines.extend(
        [
            "## 分类型",
            "",
            "| 类型 | 题数 | 成功率 | 平均得分 | Evidence Recall@K | Doc Recall@K |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for question_type, item in summary["by_type"].items():
        lines.append(
            f"| {question_type} | {item['count']} | {item['request_success_rate']} | "
            f"{item['mean_score']} | {item['evidence_recall@k']} | "
            f"{item['doc_recall@k']} |"
        )
    lines.append("")

    lines.extend(
        [
            "## 逐题明细",
            "",
            "| ID | 难度 | 类型 | Evidence R@K | Doc R@K | All Evidence | "
            "Evidence MRR | 图片来源 | 重复文档率 | 排名异常 | 得分 | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for result in results:
        metrics = result.get("metrics")
        if not metrics:
            lines.append(
                f"| {result['id']} | {result.get('difficulty', '-')} | "
                f"{result.get('type', '-')} | - | - | - | - | - | - | - | 0 | "
                f"{result.get('status', 'error')} |"
            )
            continue
        lines.append(
            f"| {result['id']} | {result.get('difficulty', '-')} | "
            f"{result.get('type', '-')} | {metrics['evidence_recall@k']} | "
            f"{metrics['doc_recall@k']} | {_fmt(metrics['all_evidence_hit@k'])} | "
            f"{metrics['evidence_mrr@k']} | {_fmt(metrics['image_source_coverage'])} | "
            f"{metrics['duplicate_doc_rate@k']} | {_fmt(metrics['ranking_anomaly'])} | "
            f"{metrics['score']} | ok |"
        )
    lines.append("")

    lines.extend(["## 逐题证据诊断", ""])
    for result in results:
        lines.append(
            f"### {result['id']} [{result.get('difficulty', '-')}/"
            f"{result.get('type', '-')}]"
        )
        lines.append("")
        lines.append(f"- 问题: {result.get('question', '')}")
        if result.get("error"):
            lines.append(f"- 异常: {result['error']}")
            lines.append("")
            continue
        metrics = result["metrics"]
        lines.append(f"- GT 文档: {metrics['gt_docs']}")
        lines.append(f"- Top-{top_k} 文档: {metrics['retrieved_docs_topk']}")
        lines.append(f"- 排名异常: {_fmt(metrics['ranking_anomaly'])}")
        lines.append(
            f"- 图片来源覆盖: {_fmt(metrics['image_source_coverage'])}"
        )
        if metrics["image_source_detail"]:
            lines.append("- 图片来源明细:")
            for detail in metrics["image_source_detail"]:
                lines.append(
                    f"  - {detail['source_document']}: "
                    f"{'命中' if detail['hit'] else '未命中'} "
                    f"(rank={_fmt(detail['matched_rank'])}, "
                    f"chunk={_fmt(detail['matched_chunk_id'])})"
                )
        matched = [detail for detail in metrics["snippet_detail"] if detail["hit"]]
        missing = [detail for detail in metrics["snippet_detail"] if not detail["hit"]]
        if matched:
            lines.append("- 已命中证据:")
            for detail in matched:
                lines.append(
                    f"  - {detail['source_document']} :: {detail['section']} "
                    f"(rank={detail['matched_rank']}, chunk={detail['matched_chunk_id']}, "
                    f"match={detail['match_type']}) — {_report_excerpt(detail['snippet'])}"
                )
        if missing:
            lines.append("- 未命中证据:")
            for detail in missing:
                lines.append(
                    f"  - {detail['source_document']} :: {detail['section']} — "
                    f"{_report_excerpt(detail['snippet'])}"
                )
        lines.append("")
    return "\n".join(lines)


def build_console_summary(exam_id: str, summary: Dict[str, Any], top_k: int) -> str:
    overall = summary["overall"]
    return " | ".join(
        [
            f"[{exam_id}] v{METRICS_VERSION}",
            f"检索能力得分={_fmt(summary['retrieval_capability_score'])}",
            f"Evidence Recall@{top_k}={overall['evidence_recall@k']}",
            f"Doc Recall@{top_k}={overall['doc_recall@k']}",
            f"成功率={overall['request_success_rate']}",
            f"已评分={overall['num_scored']}/{overall['num_questions']}",
        ]
    )


def discover_exam_files(exam_dir: str, exam: Optional[str]) -> List[Path]:
    root = Path(exam_dir).expanduser()
    if exam:
        candidate = Path(exam).expanduser()
        path = candidate if candidate.is_absolute() else root / candidate
        if not path.is_file():
            raise FileNotFoundError(f"指定考卷不存在: {path}")
        return [path]
    if not root.is_dir():
        raise FileNotFoundError(f"考卷目录不存在: {root}")
    files = sorted(path for path in root.iterdir() if path.suffix == ".json")
    if not files:
        raise FileNotFoundError(f"考卷目录下未找到 .json 考卷: {root}")
    return files


def load_and_validate_exam(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            exam = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ExamValidationError(f"考卷 JSON 无效 {path}: {exc}") from exc
    if not isinstance(exam, dict):
        raise ExamValidationError(f"考卷根节点必须是 object: {path}")
    questions = exam.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ExamValidationError(f"questions 必须是非空数组: {path}")
    meta = exam.get("exam_meta")
    if meta is not None and not isinstance(meta, dict):
        raise ExamValidationError(f"exam_meta 必须是 object: {path}")
    if isinstance(meta, dict) and meta.get("total_questions") is not None:
        if meta["total_questions"] != len(questions):
            raise ExamValidationError(
                f"total_questions={meta['total_questions']} 与实际题数 "
                f"{len(questions)} 不一致: {path}"
            )

    seen_ids = set()
    for index, question in enumerate(questions, 1):
        label = f"questions[{index - 1}]"
        if not isinstance(question, dict):
            raise ExamValidationError(f"{label} 必须是 object")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ExamValidationError(f"{label}.id 必须是非空字符串")
        if question_id in seen_ids:
            raise ExamValidationError(f"题号重复: {question_id}")
        seen_ids.add(question_id)
        if question.get("difficulty") not in ALLOWED_DIFFICULTIES:
            raise ExamValidationError(
                f"{question_id}.difficulty 必须是 simple/medium/hard"
            )
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise ExamValidationError(f"{question_id}.question 必须是非空字符串")
        gt_chunks = question.get("retrieved_chunks")
        if not isinstance(gt_chunks, list) or not gt_chunks:
            raise ExamValidationError(
                f"{question_id}.retrieved_chunks 必须是非空数组"
            )
        for chunk_index, chunk in enumerate(gt_chunks):
            if not isinstance(chunk, dict):
                raise ExamValidationError(
                    f"{question_id}.retrieved_chunks[{chunk_index}] 必须是 object"
                )
            for field in ("source_document", "snippet"):
                value = chunk.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ExamValidationError(
                        f"{question_id}.retrieved_chunks[{chunk_index}].{field} "
                        "必须是非空字符串"
                    )
        criteria = question.get("eval_criteria") or {}
        if not isinstance(criteria, dict):
            raise ExamValidationError(f"{question_id}.eval_criteria 必须是 object")
        if (
            "expected_image_url" in criteria
            and criteria["expected_image_url"] is not None
        ):
            expected_url = criteria["expected_image_url"]
            if not isinstance(expected_url, str) or not expected_url.strip():
                raise ExamValidationError(
                    f"{question_id}.expected_image_url 必须是非空字符串"
                )
            if not any(expected_url in chunk["snippet"] for chunk in gt_chunks):
                raise ExamValidationError(
                    f"{question_id}.expected_image_url 未出现在任何 GT snippet 中"
                )
    return exam


def truncate_content(response: Dict[str, Any]) -> Dict[str, Any]:
    output = copy.deepcopy(response)
    for item in output.get("content", []) or []:
        content = item.get("content")
        if isinstance(content, str) and len(content) > CONTENT_TRUNCATE:
            item["content"] = content[:CONTENT_TRUNCATE] + "...<truncated>"
    return output


def evaluate_exam(
    exam: Dict[str, Any],
    session: "requests.Session",
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    exam_meta = exam.get("exam_meta") or {}
    questions = exam["questions"]
    if args.limit:
        questions = questions[: args.limit]

    results: List[Dict[str, Any]] = []
    total = len(questions)
    for index, question in enumerate(questions, 1):
        criteria = question.get("eval_criteria") or {}
        record: Dict[str, Any] = {
            "id": question["id"],
            "difficulty": question["difficulty"],
            "type": question.get("type") or "unknown",
            "question": question["question"],
            "has_expected_image": bool(criteria.get("expected_image_url")),
        }
        print(f"  ({index}/{total}) {question['id']} 调用检索接口...", flush=True)
        try:
            response = call_retrieval_api(
                session,
                question["question"],
                args.dataset_id,
                args.timeout,
                args.retries,
            )
            if not response["success"]:
                record["status"] = "api_error"
                record["error"] = (
                    "接口返回 success=false: "
                    f"{response.get('responseMessage') or response.get('responseCode')}"
                )
            else:
                retrieved, ranking_anomaly = parse_retrieved_chunks(
                    response, args.rank_by
                )
                record["metrics"] = compute_metrics(
                    extract_gt(question),
                    retrieved,
                    args.top_k,
                    args.rank_by,
                    ranking_anomaly,
                )
                record["raw_response"] = truncate_content(response)
                record["status"] = "ok"
        except AuthenticationError as exc:
            record["status"] = "auth_error"
            record["error"] = str(exc)
            results.append(record)
            raise EvaluationAborted(str(exc), results, total) from exc
        except ResponseSchemaError as exc:
            record["status"] = "response_schema_error"
            record["error"] = str(exc)
        except RetrievalError as exc:
            record["status"] = "request_error"
            record["error"] = str(exc)
        results.append(record)

        if index < total and args.sleep > 0:
            time.sleep(args.sleep)
    return exam_meta, results


def write_outputs(
    out_root: str,
    exam_meta: Dict[str, Any],
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
    top_k: int,
    timestamp: str,
) -> Path:
    exam_id = str(exam_meta.get("exam_id") or "exam")
    out_dir = Path(out_root).expanduser() / exam_id / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    results_payload = {
        "metrics_version": METRICS_VERSION,
        "run_status": summary["run_status"],
        "exam_meta": exam_meta,
        "results": results,
    }
    with (out_dir / f"results_{timestamp}.json").open("w", encoding="utf-8") as handle:
        json.dump(results_payload, handle, ensure_ascii=False, indent=2)
    with (out_dir / f"summary_{timestamp}.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    report = build_markdown_report(exam_meta, summary, results, top_k)
    with (out_dir / f"report_{timestamp}.md").open("w", encoding="utf-8") as handle:
        handle.write(report)
    return out_dir


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须大于等于 0")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须大于等于 0")
    return parsed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="单知识库检索召回评测脚本 v2.0")
    parser.add_argument("--exam-dir", default=str(DEFAULT_EXAM_DIR), help="考卷目录")
    parser.add_argument("--exam", default=None, help="指定单份考卷文件")
    parser.add_argument("--dataset-id", default=DATASET_ID, help="知识库 ID")
    parser.add_argument("--top-k", type=positive_int, default=5, help="评测 Top-K")
    parser.add_argument("--limit", type=nonnegative_int, default=0, help="只跑前 N 题")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="结果输出根目录")
    parser.add_argument("--sleep", type=nonnegative_float, default=0.5, help="题间限流秒数")
    parser.add_argument("--timeout", type=positive_float, default=30.0, help="单次请求超时秒数")
    parser.add_argument("--retries", type=positive_int, default=3, help="失败重试次数")
    parser.add_argument(
        "--rank-by",
        choices=("response", "relevance"),
        default="response",
        help="排名依据，默认使用服务端响应顺序",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="仅校验考卷，不读取凭证、不调用接口",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        exam_files = discover_exam_files(args.exam_dir, args.exam)
        loaded_exams = [(path, load_and_validate_exam(path)) for path in exam_files]
    except (FileNotFoundError, ExamValidationError) as exc:
        sys.stderr.write(f"[错误] {exc}\n")
        return 2

    if args.validate_only:
        for path, exam in loaded_exams:
            print(f"[校验通过] {path}：{len(exam['questions'])} 题")
        return 0

    try:
        headers = build_headers()
    except AuthenticationError as exc:
        sys.stderr.write(f"[错误] {exc}\n")
        return 3

    session = requests.Session()
    session.headers.update(headers)
    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(
        f"共发现 {len(loaded_exams)} 份考卷，Top-K={args.top_k}，"
        f"rank_by={args.rank_by}\n"
    )

    for path, exam in loaded_exams:
        print(f"评测考卷: {path.name}")
        try:
            exam_meta, results = evaluate_exam(exam, session, args)
        except EvaluationAborted as exc:
            exam_meta = exam.get("exam_meta") or {}
            summary = aggregate(
                exc.partial_results,
                args.rank_by,
                run_status="aborted_auth",
                total_expected=exc.total_questions,
            )
            out_dir = write_outputs(
                args.out_dir,
                exam_meta,
                summary,
                exc.partial_results,
                args.top_k,
                timestamp,
            )
            sys.stderr.write(f"[错误] 评测因鉴权失败中止: {exc}\n")
            sys.stderr.write(f"[信息] 部分结果已输出到: {out_dir}\n")
            return 3

        summary = aggregate(
            results,
            args.rank_by,
            run_status="completed",
            total_expected=len(results),
        )
        out_dir = write_outputs(
            args.out_dir,
            exam_meta,
            summary,
            results,
            args.top_k,
            timestamp,
        )
        exam_id = str(exam_meta.get("exam_id") or path.name)
        print("\n" + build_console_summary(exam_id, summary, args.top_k))
        print(f"结果已输出到: {out_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
