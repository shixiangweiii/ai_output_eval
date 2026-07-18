# -*- coding: utf-8 -*-
"""RAG 检索评测 v3：原子证据、证据链、新颖性排序与可复现实验清单。"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError:
    sys.stderr.write("缺少依赖 requests，请先安装 评测脚本/requirements.txt\n")
    raise


SCHEMA_VERSION = "3.0"
METRICS_VERSION = "3.0"
BOOTSTRAP_SEED = 20260715
BOOTSTRAP_SAMPLES = 10_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAM_DIR = PROJECT_ROOT / "评测考试" / "考卷-v3"
RESULTS_ROOT = PROJECT_ROOT / "评测考试"
DEFAULT_BACKEND = "arag"
SUPPORTED_BACKENDS = ("arag", "dify")
ALLOWED_DIFFICULTIES = {"simple", "medium", "hard", "diagnostic"}
ALLOWED_TYPES = {
    "single_doc_fact",
    "single_doc_multi_claim",
    "cross_doc_compare",
    "cross_doc_chain",
    "disambiguation_hard_negative",
    "asset_reference_retrieval",
    "unanswerable_diagnostic",
}
RETRYABLE_STATUS_CODES = {408, 429}
CONTENT_TRUNCATE = 800

ARAG_URL = (
    "https://deap.dingtalk.com/deapHttpProxy/studio2/proxy/dataset/fe/api/v2/"
    "dataset/manage/retrieval_test.json"
)
ARAG_DATASET_ID = "844b8ded-9bf4-44b6-bc1a-a5ccee745832"
DIFY_URL = "http://localhost:5001/console/api/datasets/{dataset_id}/hit-testing"
DIFY_DATASET_ID = "fc0250d7-5bd0-4625-b373-830c1c83dc18"

ARAG_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://deap.dingtalk.com",
    "referer": "https://deap.dingtalk.com/fe/index",
    "user-agent": "Mozilla/5.0 retrieval-eval-v3",
}
DIFY_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "http://localhost:3000",
    "referer": "http://localhost:3000/",
    "user-agent": "Mozilla/5.0 retrieval-eval-v3",
}
DIFY_RETRIEVAL_MODEL = {
    "search_method": "hybrid_search",
    "reranking_enable": True,
    "reranking_mode": "reranking_model",
    "reranking_model": {
        "reranking_provider_name": "langgenius/tongyi/tongyi",
        "reranking_model_name": "qwen3-rerank",
    },
    "weights": {
        "weight_type": "customized",
        "keyword_setting": {"keyword_weight": 0.3},
        "vector_setting": {
            "vector_weight": 0.7,
            "embedding_model_name": "",
            "embedding_provider_name": "",
        },
    },
    "top_k": 10,
    "score_threshold_enabled": False,
    "score_threshold": 0.5,
    "graph_search": None,
}
DIFY_GRAPH_SEARCH = {"enabled": True, "top_k": None, "weight": 0.5, "mode": "mix"}
DIFY_SEARCH_METHODS = ("hybrid_search", "coverage_search")


class RetrievalError(Exception):
    """单题检索失败。"""


class AuthenticationError(RetrievalError):
    """鉴权失败，必须终止整场评测。"""


class ResponseSchemaError(RetrievalError):
    """后端响应结构不符合约定。"""


class ExamValidationError(ValueError):
    """v3 考卷或语料快照校验失败。"""


class ComparisonError(ValueError):
    """两次运行不满足配对比较条件。"""


class EvaluationAborted(AuthenticationError):
    """携带部分结果的鉴权中止。"""

    def __init__(self, message: str, results: List[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.results = results


_DASH_TRANSLATION = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
)
_QUOTE_TRANSLATION = str.maketrans(
    {"“": '"', "”": '"', "‘": "'", "’": "'", "＂": '"'}
)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def normalize_text(text: str) -> str:
    """执行确定性的 Unicode、Markdown、标点和空白归一化。"""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = value.translate(_DASH_TRANSLATION).translate(_QUOTE_TRANSLATION)
    value = _IMAGE_RE.sub(lambda match: f" {match.group(1)} ", value)
    value = _LINK_RE.sub(lambda match: f" {match.group(1)} {match.group(2)} ", value)
    value = re.sub(r"[*_`~#>]", "", value).lower()
    return re.sub(r"\s+", "", value)


def char_ngrams(text: str, n: int = 3) -> set[str]:
    """返回归一化文本的字符 n-gram 集。"""
    value = normalize_text(text)
    if not value:
        return set()
    if len(value) < n:
        return {value}
    return {value[index : index + n] for index in range(len(value) - n + 1)}


def char_ngram_jaccard(left: str, right: str, n: int = 3) -> float:
    """计算字符 n-gram Jaccard。"""
    left_set, right_set = char_ngrams(left, n), char_ngrams(right, n)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExamValidationError(f"{label} 必须是非空字符串")
    return value.strip()


def _require_string_list(value: Any, label: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise ExamValidationError(f"{label} 必须是非空字符串数组")
    return [_require_string(item, f"{label}[]") for item in value]


def _declared_counts(meta: Dict[str, Any]) -> Dict[str, Any]:
    counts = meta.get("question_counts")
    if not isinstance(counts, dict):
        raise ExamValidationError("exam_meta.question_counts 必须是 object")
    for field in ("total", "scored", "diagnostic"):
        if not isinstance(counts.get(field), int) or counts[field] < 0:
            raise ExamValidationError(f"question_counts.{field} 必须是非负整数")
    for field in ("by_primary_type", "by_difficulty"):
        if not isinstance(counts.get(field), dict):
            raise ExamValidationError(f"question_counts.{field} 必须是 object")
    return counts


def load_and_validate_exam(
    path: Path, allow_corpus_drift: bool = False
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取 v3 考卷并执行结构、语料哈希和 span 存在性校验。"""
    try:
        exam = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExamValidationError(f"无法读取考卷 {path}: {exc}") from exc
    if not isinstance(exam, dict) or exam.get("schema_version") != SCHEMA_VERSION:
        raise ExamValidationError(f"{path} 必须声明 schema_version={SCHEMA_VERSION}")
    meta = exam.get("exam_meta")
    questions = exam.get("questions")
    if not isinstance(meta, dict) or not isinstance(questions, list) or not questions:
        raise ExamValidationError("exam_meta 必须是 object，questions 必须是非空数组")
    _require_string(meta.get("exam_id"), "exam_meta.exam_id")
    counts = _declared_counts(meta)

    corpus = meta.get("corpus")
    if not isinstance(corpus, dict):
        raise ExamValidationError("exam_meta.corpus 必须是 object")
    _require_string(corpus.get("snapshot_id"), "corpus.snapshot_id")
    relative_dir = _require_string(corpus.get("relative_dir"), "corpus.relative_dir")
    relative_path = Path(relative_dir)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ExamValidationError("corpus.relative_dir 必须是项目内相对路径")
    corpus_dir = PROJECT_ROOT / relative_path
    if not corpus_dir.is_dir():
        raise ExamValidationError(f"语料目录不存在: {corpus_dir}")
    declared_docs = corpus.get("documents")
    if not isinstance(declared_docs, list) or not declared_docs:
        raise ExamValidationError("corpus.documents 必须是非空数组")

    document_paths: Dict[str, Path] = {}
    document_text: Dict[str, str] = {}
    warnings: List[str] = []
    corpus_drift = False
    for index, item in enumerate(declared_docs):
        if not isinstance(item, dict):
            raise ExamValidationError(f"corpus.documents[{index}] 必须是 object")
        name = _require_string(item.get("name"), f"corpus.documents[{index}].name")
        expected_hash = _require_string(
            item.get("sha256"), f"corpus.documents[{index}].sha256"
        )
        if name in document_paths:
            raise ExamValidationError(f"语料文件重复声明: {name}")
        doc_path = corpus_dir / name
        if not doc_path.is_file():
            raise ExamValidationError(f"语料文件不存在: {doc_path}")
        actual_hash = sha256_file(doc_path)
        if actual_hash != expected_hash:
            corpus_drift = True
            warnings.append(
                f"{name} SHA-256 漂移: expected={expected_hash}, actual={actual_hash}"
            )
        document_paths[name] = doc_path
        document_text[name] = normalize_text(doc_path.read_text(encoding="utf-8"))
    if corpus_drift and not allow_corpus_drift:
        raise ExamValidationError("语料快照 SHA-256 不一致；使用 --allow-corpus-drift 可仅作诊断运行")

    seen_question_ids: set[str] = set()
    seen_claim_ids: set[str] = set()
    seen_positive_spans: set[Tuple[str, str]] = set()
    type_counts: Counter[str] = Counter()
    difficulty_counts: Counter[str] = Counter()
    doc_claim_counts: Counter[str] = Counter()
    scored_count = 0
    diagnostic_count = 0
    low_overlap_count = 0
    hard_negative_count = 0
    total_claims = 0

    for index, question in enumerate(questions):
        label = f"questions[{index}]"
        if not isinstance(question, dict):
            raise ExamValidationError(f"{label} 必须是 object")
        question_id = _require_string(question.get("id"), f"{label}.id")
        if question_id in seen_question_ids:
            raise ExamValidationError(f"题号重复: {question_id}")
        seen_question_ids.add(question_id)
        if not isinstance(question.get("scored"), bool):
            raise ExamValidationError(f"{question_id}.scored 必须是 bool")
        scored = question["scored"]
        difficulty = question.get("difficulty")
        primary_type = question.get("primary_type")
        if difficulty not in ALLOWED_DIFFICULTIES:
            raise ExamValidationError(f"{question_id}.difficulty 不合法")
        if primary_type not in ALLOWED_TYPES:
            raise ExamValidationError(f"{question_id}.primary_type 不合法")
        query = _require_string(question.get("question"), f"{question_id}.question")
        tags = question.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ExamValidationError(f"{question_id}.tags 必须是字符串数组")
        if len(tags) != len(set(tags)):
            raise ExamValidationError(f"{question_id}.tags 不能重复")
        claims = question.get("claims")
        negatives = question.get("negative_evidence", [])
        if not isinstance(claims, list) or not isinstance(negatives, list):
            raise ExamValidationError(f"{question_id}.claims/negative_evidence 必须是数组")

        type_counts[primary_type] += 1
        difficulty_counts[difficulty] += 1
        positive_for_question: set[Tuple[str, str]] = set()
        claim_ids_for_question: List[str] = []
        normalized_claim_spans: List[str] = []

        if scored:
            scored_count += 1
            if difficulty == "diagnostic" or primary_type == "unanswerable_diagnostic":
                raise ExamValidationError(f"{question_id} 计分题不能使用 diagnostic 类型")
            if not claims:
                raise ExamValidationError(f"{question_id} 计分题必须包含 claims")
            _require_string(question.get("reference_answer"), f"{question_id}.reference_answer")
        else:
            diagnostic_count += 1
            if difficulty != "diagnostic" or primary_type != "unanswerable_diagnostic":
                raise ExamValidationError(f"{question_id} 非计分题必须使用 diagnostic 类型")
            if claims:
                raise ExamValidationError(f"{question_id} 诊断题 claims 必须为空")
            if question.get("expected_behavior") != "no_relevant_evidence":
                raise ExamValidationError(f"{question_id}.expected_behavior 必须为 no_relevant_evidence")
            _require_string(question.get("diagnostic_reason"), f"{question_id}.diagnostic_reason")

        for claim_index, claim in enumerate(claims):
            claim_label = f"{question_id}.claims[{claim_index}]"
            if not isinstance(claim, dict):
                raise ExamValidationError(f"{claim_label} 必须是 object")
            claim_id = _require_string(claim.get("id"), f"{claim_label}.id")
            if claim_id in seen_claim_ids:
                raise ExamValidationError(f"claim ID 重复: {claim_id}")
            seen_claim_ids.add(claim_id)
            claim_ids_for_question.append(claim_id)
            kind = claim.get("kind")
            if kind not in {"text", "asset"}:
                raise ExamValidationError(f"{claim_label}.kind 必须是 text/asset")
            source = _require_string(
                claim.get("source_document"), f"{claim_label}.source_document"
            )
            if source not in document_text:
                raise ExamValidationError(f"{claim_label} 引用了未声明语料: {source}")
            _require_string(claim.get("section"), f"{claim_label}.section")
            spans = _require_string_list(claim.get("accepted_spans"), f"{claim_label}.accepted_spans")
            for span in spans:
                normalized_span = normalize_text(span)
                if kind == "text" and not 8 <= len(normalized_span) <= 120:
                    raise ExamValidationError(
                        f"{claim_label} 文本 span 归一化长度必须在 8–120: {span}"
                    )
                if normalized_span not in document_text[source]:
                    raise ExamValidationError(f"{claim_label} span 不存在于 {source}: {span}")
                span_key = (source, normalized_span)
                if span_key in seen_positive_spans:
                    raise ExamValidationError(f"正式题复用了原子证据 span: {source} / {span}")
                positive_for_question.add(span_key)
                seen_positive_spans.add(span_key)
                normalized_claim_spans.append(span)
            doc_claim_counts[source] += 1
            total_claims += 1

        if scored:
            evidence_chain = question.get("evidence_chain")
            if not isinstance(evidence_chain, list) or evidence_chain != claim_ids_for_question:
                raise ExamValidationError(
                    f"{question_id}.evidence_chain 必须按顺序完整列出本题 claim ID"
                )

        negative_for_question: set[Tuple[str, str]] = set()
        for negative_index, negative in enumerate(negatives):
            negative_label = f"{question_id}.negative_evidence[{negative_index}]"
            if not isinstance(negative, dict):
                raise ExamValidationError(f"{negative_label} 必须是 object")
            _require_string(negative.get("id"), f"{negative_label}.id")
            source = _require_string(
                negative.get("source_document"), f"{negative_label}.source_document"
            )
            if source not in document_text:
                raise ExamValidationError(f"{negative_label} 引用了未声明语料: {source}")
            _require_string(negative.get("section"), f"{negative_label}.section")
            spans = _require_string_list(
                negative.get("accepted_spans"), f"{negative_label}.accepted_spans"
            )
            for span in spans:
                normalized_span = normalize_text(span)
                if normalized_span not in document_text[source]:
                    raise ExamValidationError(f"{negative_label} span 不存在于 {source}: {span}")
                negative_for_question.add((source, normalized_span))
        if positive_for_question & negative_for_question:
            raise ExamValidationError(f"{question_id} 的正向和负向证据发生重叠")
        if scored and negatives:
            hard_negative_count += 1

        if "low_lexical_overlap" in tags:
            if not scored:
                raise ExamValidationError(f"{question_id} 诊断题不使用 low_lexical_overlap 标签")
            maximum = max(
                (char_ngram_jaccard(query, span) for span in normalized_claim_spans),
                default=0.0,
            )
            if maximum > 0.25:
                raise ExamValidationError(
                    f"{question_id} low_lexical_overlap 不成立，最大 char-3 Jaccard={maximum:.4f}"
                )
            low_overlap_count += 1

    actual_counts = {
        "total": len(questions),
        "scored": scored_count,
        "diagnostic": diagnostic_count,
        "by_primary_type": dict(sorted(type_counts.items())),
        "by_difficulty": dict(sorted(difficulty_counts.items())),
    }
    expected_counts = {
        "total": counts["total"],
        "scored": counts["scored"],
        "diagnostic": counts["diagnostic"],
        "by_primary_type": dict(sorted(counts["by_primary_type"].items())),
        "by_difficulty": dict(sorted(counts["by_difficulty"].items())),
    }
    if actual_counts != expected_counts:
        raise ExamValidationError(
            "question_counts 与实际不一致: "
            f"declared={expected_counts}, actual={actual_counts}"
        )
    design = meta.get("design_constraints")
    if not isinstance(design, dict):
        raise ExamValidationError("exam_meta.design_constraints 必须是 object")
    min_claims = int(design.get("min_claims_per_document", 0))
    max_share = float(design.get("max_claim_share_per_document", 1.0))
    min_low_overlap = int(design.get("min_low_lexical_overlap_questions", 0))
    min_hard_negative = int(design.get("min_hard_negative_questions", 0))
    for name in document_paths:
        if doc_claim_counts[name] < min_claims:
            raise ExamValidationError(
                f"{name} 仅贡献 {doc_claim_counts[name]} 个 claim，低于 {min_claims}"
            )
        if total_claims and doc_claim_counts[name] / total_claims > max_share:
            raise ExamValidationError(
                f"{name} claim 占比 {doc_claim_counts[name] / total_claims:.4f} 超限"
            )
    if low_overlap_count < min_low_overlap:
        raise ExamValidationError(
            f"low_lexical_overlap 题数 {low_overlap_count} 低于 {min_low_overlap}"
        )
    if hard_negative_count < min_hard_negative:
        raise ExamValidationError(
            f"硬负例题数 {hard_negative_count} 低于 {min_hard_negative}"
        )

    validation = {
        "exam_sha256": sha256_file(path),
        "corpus_dir": str(corpus_dir),
        "corpus_drift": corpus_drift,
        "warnings": warnings,
        "total_claims": total_claims,
        "document_claim_counts": dict(sorted(doc_claim_counts.items())),
        "low_lexical_overlap_questions": low_overlap_count,
        "hard_negative_questions": hard_negative_count,
    }
    return exam, validation


def discover_exam_files(exam_dir: str, exam: Optional[str]) -> List[Path]:
    root = Path(exam_dir).expanduser()
    if exam:
        candidate = Path(exam).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.is_file():
            raise FileNotFoundError(f"考卷不存在: {candidate}")
        return [candidate]
    if not root.is_dir():
        raise FileNotFoundError(f"考卷目录不存在: {root}")
    files = sorted(root.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"考卷目录没有 JSON: {root}")
    return files


def _credentials() -> Tuple[str, str]:
    cookie = os.environ.get("RETRIEVAL_COOKIE", "").strip()
    token = os.environ.get("RETRIEVAL_XSRF_TOKEN", "").strip()
    missing = [name for name, value in (("RETRIEVAL_COOKIE", cookie), ("RETRIEVAL_XSRF_TOKEN", token)) if not value]
    if missing:
        raise AuthenticationError("缺少检索接口凭证环境变量: " + ", ".join(missing))
    return cookie, token


def build_headers(backend: str) -> Dict[str, str]:
    cookie, token = _credentials()
    headers = dict(DIFY_HEADERS if backend == "dify" else ARAG_HEADERS)
    headers["Cookie"] = cookie
    headers["x-csrf-token" if backend == "dify" else "x-xsrf-token"] = token
    return headers


def _post_json(
    session: "requests.Session",
    url: str,
    payload: Dict[str, Any],
    timeout: float,
    retries: int,
) -> Dict[str, Any]:
    last_error = "未知错误"
    for attempt in range(1, retries + 1):
        retryable = False
        try:
            response = session.post(url, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"网络异常: {exc}"
            retryable = True
        else:
            status = response.status_code
            if status in (401, 403):
                raise AuthenticationError(f"鉴权失败(HTTP {status})")
            retryable = status in RETRYABLE_STATUS_CODES or status >= 500
            if status != 200:
                last_error = f"HTTP {status}: {response.text[:200].replace(chr(10), ' ')}"
                if not retryable:
                    raise RetrievalError(last_error)
            else:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ResponseSchemaError("后端返回非 JSON") from exc
                if not isinstance(data, dict):
                    raise ResponseSchemaError("后端响应根节点必须是 object")
                return data
        if retryable and attempt < retries:
            time.sleep(0.5 * (2 ** (attempt - 1)))
    raise RetrievalError(last_error)


def call_arag_api(
    session: "requests.Session",
    query: str,
    dataset_id: str,
    timeout: float,
    retries: int,
) -> Dict[str, Any]:
    data = _post_json(
        session, ARAG_URL, {"datasetId": dataset_id, "query": query}, timeout, retries
    )
    if not isinstance(data.get("success"), bool):
        raise ResponseSchemaError("aRAG 响应缺少布尔 success")
    if not data["success"]:
        raise RetrievalError(
            "aRAG success=false: "
            f"{data.get('responseMessage') or data.get('responseCode') or 'unknown'}"
        )
    return data


def build_dify_request(
    query: str,
    request_k: int,
    graph_search: bool,
    score_threshold_enabled: bool = False,
    score_threshold: Optional[float] = None,
    search_method: str = "hybrid_search",
) -> Dict[str, Any]:
    if search_method not in DIFY_SEARCH_METHODS:
        raise ValueError(f"不支持的 Dify 检索策略: {search_method}")
    model = copy.deepcopy(DIFY_RETRIEVAL_MODEL)
    model["top_k"] = request_k
    model["score_threshold_enabled"] = score_threshold_enabled
    model["score_threshold"] = score_threshold
    if search_method == "coverage_search":
        model["search_method"] = "coverage_search"
        model["reranking_mode"] = None
        model["weights"] = None
        model["graph_search"] = None
    elif graph_search:
        model["graph_search"] = copy.deepcopy(DIFY_GRAPH_SEARCH)
    return {"query": query, "attachment_ids": [], "retrieval_model": model}


def call_dify_api(
    session: "requests.Session",
    query: str,
    dataset_id: str,
    timeout: float,
    retries: int,
    request_k: int,
    graph_search: bool,
    score_threshold_enabled: bool = False,
    score_threshold: Optional[float] = None,
    search_method: str = "hybrid_search",
) -> Dict[str, Any]:
    return _post_json(
        session,
        DIFY_URL.format(dataset_id=dataset_id),
        build_dify_request(
            query,
            request_k,
            graph_search,
            score_threshold_enabled,
            score_threshold,
            search_method,
        ),
        timeout,
        retries,
    )


def _numeric(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ranking_anomaly(chunks: Sequence[Dict[str, Any]]) -> bool:
    scores = [chunk["relevance_score"] for chunk in chunks if chunk["relevance_score"] is not None]
    return any(current > previous for previous, current in zip(scores, scores[1:]))


def _finalize_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for rank, chunk in enumerate(chunks, 1):
        chunk["rank"] = rank
    return chunks


def parse_arag_response(response: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    content = response.get("content")
    if not isinstance(content, list):
        raise ResponseSchemaError("aRAG 响应 content 必须是数组")
    chunks = []
    for index, item in enumerate(content, 1):
        if not isinstance(item, dict):
            raise ResponseSchemaError(f"aRAG content[{index - 1}] 必须是 object")
        chunks.append(
            {
                "original_index": index,
                "server_top": item.get("top"),
                "relevance_score": _numeric(item.get("relevanceScore")),
                "document_name": str(item.get("documentName") or ""),
                "document_id": str(item.get("documentId") or ""),
                "chunk_id": str(item.get("chunkId") or ""),
                "content": str(item.get("content") or ""),
            }
        )
    anomaly = _ranking_anomaly(chunks)
    return _finalize_chunks(chunks), anomaly


def parse_dify_response(response: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    records = response.get("records")
    if not isinstance(records, list):
        raise ResponseSchemaError("Dify 响应 records 必须是数组")
    chunks = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or not isinstance(record.get("segment"), dict):
            raise ResponseSchemaError(f"Dify records[{index - 1}].segment 必须是 object")
        segment = record["segment"]
        document = segment.get("document") if isinstance(segment.get("document"), dict) else {}
        chunks.append(
            {
                "original_index": index,
                "server_top": segment.get("position"),
                "relevance_score": _numeric(record.get("score")),
                "document_name": str(document.get("name") or ""),
                "document_id": str(segment.get("document_id") or document.get("id") or ""),
                "chunk_id": str(segment.get("id") or ""),
                "content": str(segment.get("content") or ""),
            }
        )
    anomaly = _ranking_anomaly(chunks)
    return _finalize_chunks(chunks), anomaly


def relevance_counterfactual(chunks: Sequence[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    if not chunks or any(chunk.get("relevance_score") is None for chunk in chunks):
        return None
    ordered = [copy.deepcopy(chunk) for chunk in chunks]
    ordered.sort(key=lambda chunk: (-chunk["relevance_score"], chunk["original_index"]))
    return _finalize_chunks(ordered)


def _claim_detail(
    question: Dict[str, Any], chunks: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized_chunks = [normalize_text(chunk["content"]) for chunk in chunks]
    details = []
    for claim in question["claims"]:
        accepted = [normalize_text(span) for span in claim["accepted_spans"]]
        match = None
        for chunk, content in zip(chunks, normalized_chunks):
            if chunk["document_name"] == claim["source_document"] and any(
                span and span in content for span in accepted
            ):
                match = chunk
                break
        source_present = any(
            chunk["document_name"] == claim["source_document"] for chunk in chunks
        )
        details.append(
            {
                "claim_id": claim["id"],
                "kind": claim["kind"],
                "source_document": claim["source_document"],
                "section": claim["section"],
                "hit": match is not None,
                "first_rank": match["rank"] if match else None,
                "matched_chunk_id": match["chunk_id"] if match else None,
                "document_hit_claim_miss": source_present and match is None,
            }
        )
    return details


def _negative_detail(
    question: Dict[str, Any], chunks: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized_chunks = [normalize_text(chunk["content"]) for chunk in chunks]
    details = []
    for negative in question.get("negative_evidence", []):
        accepted = [normalize_text(span) for span in negative["accepted_spans"]]
        match = None
        for chunk, content in zip(chunks, normalized_chunks):
            if chunk["document_name"] == negative["source_document"] and any(
                span and span in content for span in accepted
            ):
                match = chunk
                break
        details.append(
            {
                "negative_id": negative["id"],
                "source_document": negative["source_document"],
                "hit": match is not None,
                "first_rank": match["rank"] if match else None,
                "matched_chunk_id": match["chunk_id"] if match else None,
            }
        )
    return details


def compute_metrics_at_k(
    question: Dict[str, Any], retrieved: Sequence[Dict[str, Any]], k: int
) -> Dict[str, Any]:
    top_chunks = list(retrieved[:k])
    claim_detail = _claim_detail(question, top_chunks)
    negative_detail = _negative_detail(question, top_chunks)
    claim_count = len(claim_detail)
    claim_hits = sum(bool(item["hit"]) for item in claim_detail)
    claim_recall = claim_hits / claim_count if claim_count else 0.0
    complete_chain = bool(claim_count and claim_hits == claim_count)
    novel_score = (
        sum(
            1.0 / math.log2(item["first_rank"] + 1)
            for item in claim_detail
            if item["first_rank"] is not None
        )
        / claim_count
        if claim_count
        else 0.0
    )
    gt_docs = list(dict.fromkeys(claim["source_document"] for claim in question["claims"]))
    top_docs = [chunk["document_name"] for chunk in top_chunks]
    unique_docs = list(dict.fromkeys(top_docs))
    doc_hits = sum(doc in set(unique_docs) for doc in gt_docs)
    asset_claims = [item for item in claim_detail if item["kind"] == "asset"]
    return {
        "k": k,
        "claim_count": claim_count,
        "claim_hits": claim_hits,
        "claim_recall": round(claim_recall, 4),
        "complete_evidence_chain": complete_chain,
        "novel_claim_rank_score": round(novel_score, 4),
        "gt_document_count": len(gt_docs),
        "document_recall": round(doc_hits / len(gt_docs), 4) if gt_docs else None,
        "retrieved_documents": top_docs,
        "duplicate_document_rate": round(
            (len(top_docs) - len(unique_docs)) / len(top_docs), 4
        ) if top_docs else 0.0,
        "response_depth_insufficient": len(retrieved) < k,
        "hard_negative_intrusion": any(item["hit"] for item in negative_detail),
        "negative_evidence_count": len(negative_detail),
        "asset_claim_count": len(asset_claims),
        "asset_claim_hits": sum(bool(item["hit"]) for item in asset_claims),
        "claim_detail": claim_detail,
        "negative_detail": negative_detail,
    }


def _truncate_chunks_to_char_budget(
    chunks: Sequence[Dict[str, Any]], budget: int
) -> List[Dict[str, Any]]:
    remaining = budget
    output = []
    for chunk in chunks:
        if remaining <= 0:
            break
        normalized = normalize_text(chunk["content"])
        partial = normalized[:remaining]
        copied = dict(chunk)
        copied["content"] = partial
        output.append(copied)
        remaining -= len(partial)
    return output


def compute_question_metrics(
    question: Dict[str, Any],
    retrieved: Sequence[Dict[str, Any]],
    eval_ks: Sequence[int],
    char_budgets: Sequence[int],
    ranking_anomaly: bool,
) -> Dict[str, Any]:
    metrics_by_k = {
        str(k): compute_metrics_at_k(question, retrieved, k) for k in eval_ks
    }
    char_metrics = {}
    for budget in char_budgets:
        budget_chunks = _truncate_chunks_to_char_budget(retrieved, budget)
        details = _claim_detail(question, budget_chunks)
        hits = sum(bool(item["hit"]) for item in details)
        char_metrics[str(budget)] = {
            "claim_hits": hits,
            "claim_count": len(details),
            "claim_recall": round(hits / len(details), 4) if details else 0.0,
            "consumed_chars": sum(len(normalize_text(chunk["content"])) for chunk in budget_chunks),
        }
    return {
        "ranking_anomaly": ranking_anomaly,
        "response_depth": len(retrieved),
        "metrics_by_k": metrics_by_k,
        "char_budget_metrics": char_metrics,
        "retrieved_chunk_detail": [
            {
                "rank": chunk["rank"],
                "original_index": chunk["original_index"],
                "server_top": chunk["server_top"],
                "relevance_score": chunk["relevance_score"],
                "document_name": chunk["document_name"],
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "normalized_char_count": len(normalize_text(chunk["content"])),
            }
            for chunk in retrieved
        ],
    }


def diagnostic_metrics(retrieved: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    docs = [chunk["document_name"] for chunk in retrieved]
    unique_docs = list(dict.fromkeys(docs))
    scores = [chunk["relevance_score"] for chunk in retrieved if chunk["relevance_score"] is not None]
    return {
        "returned_count": len(retrieved),
        "highest_relevance_score": max(scores) if scores else None,
        "retrieved_documents": docs,
        "duplicate_document_rate": round(
            (len(docs) - len(unique_docs)) / len(docs), 4
        ) if docs else 0.0,
        "top_chunks": [
            {
                "rank": chunk["rank"],
                "document_name": chunk["document_name"],
                "chunk_id": chunk["chunk_id"],
                "relevance_score": chunk["relevance_score"],
            }
            for chunk in retrieved[:10]
        ],
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def bootstrap_ci(
    values: Sequence[float], samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED
) -> Optional[List[float]]:
    if not values:
        return None
    rng = random.Random(seed)
    size = len(values)
    means = sorted(
        _mean([values[rng.randrange(size)] for _ in range(size)]) for _ in range(samples)
    )
    return [round(_percentile(means, 0.025), 4), round(_percentile(means, 0.975), 4)]


def _metric_value(result: Dict[str, Any], k: int, field: str) -> float:
    metrics = result.get("metrics")
    if not metrics:
        return 0.0
    value = metrics["metrics_by_k"][str(k)].get(field)
    return float(value) if value is not None else 0.0


def _summarize_group(results: Sequence[Dict[str, Any]], primary_k: int) -> Dict[str, Any]:
    return {
        "count": len(results),
        "claim_recall": round(_mean([_metric_value(item, primary_k, "claim_recall") for item in results]), 4),
        "complete_evidence_chain_rate": round(_mean([_metric_value(item, primary_k, "complete_evidence_chain") for item in results]), 4),
        "novel_claim_rank_score": round(_mean([_metric_value(item, primary_k, "novel_claim_rank_score") for item in results]), 4),
    }


def aggregate(
    results: List[Dict[str, Any]],
    primary_k: int,
    eval_ks: Sequence[int],
    char_budgets: Sequence[int],
    run_status: str,
    run_scope: str,
    corpus_drift: bool,
) -> Dict[str, Any]:
    scored = [item for item in results if item["scored"]]
    diagnostic = [item for item in results if not item["scored"]]
    successful = [item for item in results if item.get("status") == "ok"]
    scored_successful = [item for item in scored if item.get("metrics")]
    completed = run_status == "completed"

    recall_values = [_metric_value(item, primary_k, "claim_recall") for item in scored]
    chain_values = [_metric_value(item, primary_k, "complete_evidence_chain") for item in scored]
    rank_values = [_metric_value(item, primary_k, "novel_claim_rank_score") for item in scored]
    total_claims = sum(item["claim_count"] for item in scored)
    total_hits = sum(
        int(item.get("metrics", {}).get("metrics_by_k", {}).get(str(primary_k), {}).get("claim_hits", 0))
        for item in scored
    )

    headline = None
    if completed:
        headline = {
            "primary_k": primary_k,
            "query_macro_claim_recall": round(_mean(recall_values), 4),
            "query_macro_claim_recall_ci95": bootstrap_ci(recall_values),
            "complete_evidence_chain_rate": round(_mean(chain_values), 4),
            "complete_evidence_chain_rate_ci95": bootstrap_ci(chain_values, seed=BOOTSTRAP_SEED + 1),
            "novel_claim_rank_score": round(_mean(rank_values), 4),
            "novel_claim_rank_score_ci95": bootstrap_ci(rank_values, seed=BOOTSTRAP_SEED + 2),
        }

    k_curves = {}
    for k in eval_ks:
        k_curves[str(k)] = {
            "query_macro_claim_recall": round(_mean([_metric_value(item, k, "claim_recall") for item in scored]), 4),
            "complete_evidence_chain_rate": round(_mean([_metric_value(item, k, "complete_evidence_chain") for item in scored]), 4),
            "novel_claim_rank_score": round(_mean([_metric_value(item, k, "novel_claim_rank_score") for item in scored]), 4),
        }
    char_curves = {}
    for budget in char_budgets:
        values = [
            float(item.get("metrics", {}).get("char_budget_metrics", {}).get(str(budget), {}).get("claim_recall", 0.0))
            for item in scored
        ]
        char_curves[str(budget)] = {"query_macro_claim_recall": round(_mean(values), 4)}

    negative_questions = [
        item for item in scored_successful if item.get("negative_evidence_count", 0) > 0
    ]
    asset_claims = sum(item.get("asset_claim_count", 0) for item in scored)
    asset_hits = sum(
        int(item.get("metrics", {}).get("metrics_by_k", {}).get(str(primary_k), {}).get("asset_claim_hits", 0))
        for item in scored
    )
    response_depth_flags = [
        _metric_value(item, primary_k, "response_depth_insufficient") for item in scored
    ]
    latency_values = [float(item["latency_ms"]) for item in successful]
    response_depths = [
        int(item["metrics"]["response_depth"])
        for item in scored_successful
    ]
    error_counts = Counter(
        item["status"] for item in results if item.get("status") != "ok"
    )
    manual_review_queue = []
    for item in scored_successful:
        primary_metrics = item["metrics"]["metrics_by_k"][str(primary_k)]
        for detail in primary_metrics["claim_detail"]:
            if detail["document_hit_claim_miss"]:
                manual_review_queue.append(
                    {
                        "question_id": item["id"],
                        "claim_id": detail["claim_id"],
                        "source_document": detail["source_document"],
                    }
                )

    by_type = {
        name: _summarize_group([item for item in scored if item["primary_type"] == name], primary_k)
        for name in sorted({item["primary_type"] for item in scored})
    }
    by_difficulty = {
        name: _summarize_group([item for item in scored if item["difficulty"] == name], primary_k)
        for name in ("simple", "medium", "hard")
    }
    tags = sorted({tag for item in scored for tag in item["tags"]})
    by_tag = {
        tag: _summarize_group([item for item in scored if tag in item["tags"]], primary_k)
        for tag in tags
    }
    by_doc_count = {
        str(count): _summarize_group([item for item in scored if item["gt_document_count"] == count], primary_k)
        for count in sorted({item["gt_document_count"] for item in scored})
    }

    counterfactual_results = []
    for item in scored:
        copied = dict(item)
        copied["metrics"] = item.get("relevance_counterfactual_metrics")
        counterfactual_results.append(copied)
    counterfactual_available = all(
        item.get("relevance_counterfactual_metrics") is not None
        for item in scored_successful
    ) and bool(scored_successful)
    counterfactual = None
    if counterfactual_available:
        counterfactual = {
            "query_macro_claim_recall": round(
                _mean([_metric_value(item, primary_k, "claim_recall") for item in counterfactual_results]), 4
            ),
            "complete_evidence_chain_rate": round(
                _mean([_metric_value(item, primary_k, "complete_evidence_chain") for item in counterfactual_results]), 4
            ),
            "novel_claim_rank_score": round(
                _mean([_metric_value(item, primary_k, "novel_claim_rank_score") for item in counterfactual_results]), 4
            ),
        }

    comparison_eligible = completed and run_scope == "full" and not corpus_drift
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "run_status": run_status,
        "run_scope": run_scope,
        "comparison_eligible": comparison_eligible,
        "headline": headline,
        "diagnostics": {
            "num_questions": len(results),
            "num_scored_questions": len(scored),
            "num_diagnostic_questions": len(diagnostic),
            "num_successful_requests": len(successful),
            "request_success_rate": round(len(successful) / len(results), 4) if results else 0.0,
            "error_counts": dict(sorted(error_counts.items())),
            "claim_micro_recall": round(total_hits / total_claims, 4) if total_claims else 0.0,
            "mean_document_recall": round(_mean([_metric_value(item, primary_k, "document_recall") for item in scored]), 4),
            "mean_duplicate_document_rate": round(
                _mean([_metric_value(item, primary_k, "duplicate_document_rate") for item in scored]), 4
            ),
            "hard_negative_intrusion_rate": round(
                _mean([_metric_value(item, primary_k, "hard_negative_intrusion") for item in negative_questions]), 4
            ) if negative_questions else None,
            "hard_negative_questions_evaluated": len(negative_questions),
            "asset_source_coverage": round(asset_hits / asset_claims, 4) if asset_claims else None,
            "response_depth_insufficient_rate": round(_mean(response_depth_flags), 4),
            "response_depth": {
                "min": min(response_depths) if response_depths else None,
                "median": round(statistics.median(response_depths), 2) if response_depths else None,
                "max": max(response_depths) if response_depths else None,
            },
            "ranking_anomaly_count": sum(bool(item.get("metrics", {}).get("ranking_anomaly")) for item in scored),
            "document_hit_claim_miss_review_queue": manual_review_queue,
            "latency_ms": {
                "p50": round(statistics.median(latency_values), 2) if latency_values else None,
                "p95": round(_percentile(sorted(latency_values), 0.95), 2) if latency_values else None,
            },
            "k_curves": k_curves,
            "char_budget_curves": char_curves,
            "relevance_counterfactual": counterfactual,
            "by_type": by_type,
            "by_difficulty": by_difficulty,
            "by_tag": by_tag,
            "by_gt_document_count": by_doc_count,
            "diagnostic_questions": [
                {
                    "id": item["id"],
                    "status": item["status"],
                    "metrics": item.get("diagnostic_metrics"),
                }
                for item in diagnostic
            ],
        },
    }


def _truncate_response(response: Dict[str, Any], limit: int) -> Dict[str, Any]:
    output = copy.deepcopy(response)
    for item in output.get("content", []) or []:
        if isinstance(item, dict) and isinstance(item.get("content"), str):
            item["content"] = item["content"][:limit]
    for record in output.get("records", []) or []:
        segment = record.get("segment") if isinstance(record, dict) else None
        if isinstance(segment, dict) and isinstance(segment.get("content"), str):
            segment["content"] = segment["content"][:limit]
    return output


def evaluate_exam(
    exam: Dict[str, Any], session: "requests.Session", args: argparse.Namespace
) -> List[Dict[str, Any]]:
    questions = exam["questions"][: args.limit or None]
    dataset_id = args.dataset_id or (DIFY_DATASET_ID if args.backend == "dify" else ARAG_DATASET_ID)
    results = []
    for index, question in enumerate(questions, 1):
        record: Dict[str, Any] = {
            "id": question["id"],
            "scored": question["scored"],
            "difficulty": question["difficulty"],
            "primary_type": question["primary_type"],
            "tags": question["tags"],
            "question": question["question"],
            "claim_count": len(question["claims"]),
            "negative_evidence_count": len(question.get("negative_evidence", [])),
            "asset_claim_count": sum(claim["kind"] == "asset" for claim in question["claims"]),
            "gt_document_count": len({claim["source_document"] for claim in question["claims"]}),
        }
        print(f"  ({index}/{len(questions)}) {question['id']} 调用 {args.backend}...", flush=True)
        started = time.perf_counter()
        try:
            if args.backend == "dify":
                response = call_dify_api(
                    session,
                    question["question"],
                    dataset_id,
                    args.timeout,
                    args.retries,
                    args.request_k,
                    args.graph_search,
                    args.score_threshold_enabled,
                    args.score_threshold,
                    getattr(args, "dify_search_method", "hybrid_search"),
                )
                chunks, anomaly = parse_dify_response(response)
            else:
                response = call_arag_api(
                    session, question["question"], dataset_id, args.timeout, args.retries
                )
                chunks, anomaly = parse_arag_response(response)
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            if question["scored"]:
                record["metrics"] = compute_question_metrics(
                    question, chunks, args.eval_k, args.char_budgets, anomaly
                )
                reordered = relevance_counterfactual(chunks)
                record["relevance_counterfactual_metrics"] = (
                    compute_question_metrics(
                        question, reordered, args.eval_k, args.char_budgets, anomaly
                    )
                    if reordered is not None
                    else None
                )
            else:
                record["diagnostic_metrics"] = diagnostic_metrics(chunks)
            record["raw_response"] = _truncate_response(response, args.raw_content_limit)
            record["status"] = "ok"
        except AuthenticationError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "auth_error"
            record["error"] = str(exc)
            results.append(record)
            raise EvaluationAborted(str(exc), results) from exc
        except ResponseSchemaError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "response_schema_error"
            record["error"] = str(exc)
        except RetrievalError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "request_error"
            record["error"] = str(exc)
        results.append(record)
        if index < len(questions) and args.sleep > 0:
            time.sleep(args.sleep)
    return results


def _git_info() -> Dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
            text=True, check=True, timeout=5,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True,
            text=True, check=True, timeout=5,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
    return {"commit": commit, "dirty": dirty}


def build_manifest(
    exam: Dict[str, Any], validation: Dict[str, Any], args: argparse.Namespace,
    timestamp: str, run_scope: str,
) -> Dict[str, Any]:
    dataset_id = args.dataset_id or (DIFY_DATASET_ID if args.backend == "dify" else ARAG_DATASET_ID)
    request_config: Dict[str, Any]
    if args.backend == "dify":
        request_config = build_dify_request(
            "<query>",
            args.request_k,
            args.graph_search,
            args.score_threshold_enabled,
            args.score_threshold,
            getattr(args, "dify_search_method", "hybrid_search"),
        )["retrieval_model"]
        request_k_support = "applied"
    else:
        request_config = {"payload_fields": ["datasetId", "query"]}
        request_k_support = "unsupported_by_backend"
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "timestamp": timestamp,
        "timezone": str(dt.datetime.now().astimezone().tzinfo),
        "exam_id": exam["exam_meta"]["exam_id"],
        "exam_sha256": validation["exam_sha256"],
        "corpus": exam["exam_meta"]["corpus"],
        "corpus_drift": validation["corpus_drift"],
        "backend": args.backend,
        "dataset_id": dataset_id,
        "dataset_revision": args.dataset_revision or "unverified",
        "graph_search": args.graph_search,
        "dify_search_method": getattr(args, "dify_search_method", "hybrid_search"),
        "request_k": args.request_k,
        "request_k_support": request_k_support,
        "request_config": request_config,
        "ranking_policy": "production_response_order",
        "eval_k": args.eval_k,
        "primary_k": args.primary_k,
        "char_budgets": args.char_budgets,
        "run_scope": run_scope,
        "raw_content_limit": args.raw_content_limit,
        "python_version": platform.python_version(),
        "git": _git_info(),
        "security": "credentials and request headers are never persisted",
    }


def build_report(
    exam: Dict[str, Any], summary: Dict[str, Any], manifest: Dict[str, Any],
    results: Sequence[Dict[str, Any]],
) -> str:
    lines = [
        f"# {exam['exam_meta']['title']} - 检索评测报告",
        "",
        f"- 考卷: {exam['exam_meta']['exam_id']}",
        f"- 后端: {manifest['backend']}",
        f"- Dataset: {manifest['dataset_id']}",
        f"- 运行范围: {summary['run_scope']}",
        f"- 运行状态: {summary['run_status']}",
        f"- 可配对比较: {summary['comparison_eligible']}",
        f"- 正式排名口径: 生产响应顺序",
        "",
        "## 三项主指标",
        "",
    ]
    headline = summary["headline"]
    if headline is None:
        lines.append("鉴权中止，主指标为空。")
    else:
        k = headline["primary_k"]
        lines.extend([
            "| 指标 | 值 | 95% CI |",
            "|---|---:|---:|",
            f"| Query Macro Claim Recall@{k} | {headline['query_macro_claim_recall']} | {headline['query_macro_claim_recall_ci95']} |",
            f"| Complete Evidence Chain Rate@{k} | {headline['complete_evidence_chain_rate']} | {headline['complete_evidence_chain_rate_ci95']} |",
            f"| Novel Claim Rank Score@{k} | {headline['novel_claim_rank_score']} | {headline['novel_claim_rank_score_ci95']} |",
        ])
    diagnostics = summary["diagnostics"]
    lines.extend([
        "",
        "## 关键诊断",
        "",
        f"- 请求成功率: {diagnostics['request_success_rate']}",
        f"- 错误类型: {diagnostics['error_counts']}",
        f"- Claim 微平均召回: {diagnostics['claim_micro_recall']}",
        f"- 平均文档召回: {diagnostics['mean_document_recall']}",
        f"- 平均重复文档率: {diagnostics['mean_duplicate_document_rate']}",
        f"- 硬负例侵入率: {diagnostics['hard_negative_intrusion_rate']}",
        f"- 资产来源覆盖: {diagnostics['asset_source_coverage']}",
        f"- 响应深度不足率: {diagnostics['response_depth_insufficient_rate']}",
        f"- 响应深度 min/median/max: {diagnostics['response_depth']['min']} / {diagnostics['response_depth']['median']} / {diagnostics['response_depth']['max']}",
        f"- 文档命中但 Claim 未命中待复核数: {len(diagnostics['document_hit_claim_miss_review_queue'])}",
        f"- 延迟 p50/p95(ms): {diagnostics['latency_ms']['p50']} / {diagnostics['latency_ms']['p95']}",
        "",
        "## Top-K 曲线",
        "",
        "| K | Claim Recall | Complete Chain | Novel Rank |",
        "|---:|---:|---:|---:|",
    ])
    for k, values in diagnostics["k_curves"].items():
        lines.append(
            f"| {k} | {values['query_macro_claim_recall']} | "
            f"{values['complete_evidence_chain_rate']} | {values['novel_claim_rank_score']} |"
        )
    lines.extend([
        "",
        "## 字符预算曲线",
        "",
        "| 归一化字符预算 | Query Macro Claim Recall |",
        "|---:|---:|",
    ])
    for budget, values in diagnostics["char_budget_curves"].items():
        lines.append(f"| {budget} | {values['query_macro_claim_recall']} |")
    lines.extend(["", "## 逐题结果", "", "| ID | 类型 | 状态 | Recall | Chain | Novel Rank |", "|---|---|---|---:|---:|---:|"])
    primary_key = str(manifest["primary_k"])
    for result in results:
        metrics = result.get("metrics", {}).get("metrics_by_k", {}).get(primary_key)
        lines.append(
            f"| {result['id']} | {result['primary_type']} | {result['status']} | "
            f"{metrics['claim_recall'] if metrics else '-'} | "
            f"{metrics['complete_evidence_chain'] if metrics else '-'} | "
            f"{metrics['novel_claim_rank_score'] if metrics else '-'} |"
        )
    if summary["run_scope"] == "smoke":
        lines.extend(["", "> ⚠️ 本次为 --limit 冒烟运行，不可与完整考试直接比较。"])
    if manifest["corpus_drift"]:
        lines.extend(["", "> ⚠️ 本地语料哈希漂移，本结果不可用于正式比较。"])
    return "\n".join(lines) + "\n"


def write_outputs(
    out_root: Path, exam: Dict[str, Any], validation: Dict[str, Any],
    summary: Dict[str, Any], results: List[Dict[str, Any]], manifest: Dict[str, Any],
    timestamp: str,
) -> Path:
    out_dir = out_root / exam["exam_meta"]["exam_id"] / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "run_status": summary["run_status"],
        "run_scope": summary["run_scope"],
        "comparison_eligible": summary["comparison_eligible"],
        "exam_meta": exam["exam_meta"],
        "manifest": manifest,
        "results": results,
    }
    files = {
        f"results_{timestamp}.json": payload,
        f"summary_{timestamp}.json": summary,
        f"manifest_{timestamp}.json": manifest,
    }
    for name, value in files.items():
        (out_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (out_dir / f"report_{timestamp}.md").write_text(
        build_report(exam, summary, manifest, results), encoding="utf-8"
    )
    return out_dir


def _paired_values(payload: Dict[str, Any], primary_k: int, field: str) -> Dict[str, float]:
    values = {}
    for result in payload["results"]:
        if not result["scored"]:
            continue
        metrics = result.get("metrics")
        values[result["id"]] = (
            float(metrics["metrics_by_k"][str(primary_k)][field]) if metrics else 0.0
        )
    return values


def paired_randomization_pvalue(
    differences: Sequence[float], samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    if not differences or all(value == 0 for value in differences):
        return 1.0
    observed = abs(_mean(differences))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(samples):
        randomized = _mean([value if rng.random() < 0.5 else -value for value in differences])
        extreme += abs(randomized) >= observed
    return round((extreme + 1) / (samples + 1), 4)


def compare_runs(left_path: Path, right_path: Path) -> Dict[str, Any]:
    try:
        left = json.loads(left_path.read_text(encoding="utf-8"))
        right = json.loads(right_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"无法读取比较结果: {exc}") from exc
    for name, payload in (("left", left), ("right", right)):
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ComparisonError(f"{name} 不是 v3 结果")
        if payload.get("run_status") != "completed" or payload.get("run_scope") != "full":
            raise ComparisonError(f"{name} 必须是 completed/full 运行")
        if not payload.get("comparison_eligible"):
            raise ComparisonError(f"{name} 标记为不可比较")
    left_manifest, right_manifest = left["manifest"], right["manifest"]
    compatibility_fields = ("exam_id", "exam_sha256", "corpus", "primary_k", "eval_k")
    for field in compatibility_fields:
        if left_manifest.get(field) != right_manifest.get(field):
            raise ComparisonError(f"比较字段不一致: {field}")
    left_ids = [item["id"] for item in left["results"] if item["scored"]]
    right_ids = [item["id"] for item in right["results"] if item["scored"]]
    if left_ids != right_ids:
        raise ComparisonError("计分题 ID 或顺序不一致")
    primary_k = int(left_manifest["primary_k"])
    fields = (
        "claim_recall",
        "complete_evidence_chain",
        "novel_claim_rank_score",
    )
    metrics = {}
    values_by_field: Dict[str, Tuple[Dict[str, float], Dict[str, float]]] = {}
    for offset, field in enumerate(fields):
        left_values = _paired_values(left, primary_k, field)
        right_values = _paired_values(right, primary_k, field)
        values_by_field[field] = (left_values, right_values)
        differences = [right_values[item] - left_values[item] for item in left_ids]
        metrics[field] = {
            "left_mean": round(_mean(list(left_values.values())), 4),
            "right_mean": round(_mean(list(right_values.values())), 4),
            "right_minus_left": round(_mean(differences), 4),
            "paired_bootstrap_ci95": bootstrap_ci(
                differences, seed=BOOTSTRAP_SEED + offset
            ),
            "paired_randomization_pvalue": paired_randomization_pvalue(
                differences, seed=BOOTSTRAP_SEED + offset
            ),
        }
    paired_question_deltas = []
    for question_id in left_ids:
        paired_question_deltas.append(
            {
                "question_id": question_id,
                **{
                    field: round(
                        values_by_field[field][1][question_id]
                        - values_by_field[field][0][question_id],
                        4,
                    )
                    for field in fields
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "exam_id": left_manifest["exam_id"],
        "primary_k": primary_k,
        "left": {"path": str(left_path), "backend": left_manifest["backend"], "dataset_id": left_manifest["dataset_id"]},
        "right": {"path": str(right_path), "backend": right_manifest["backend"], "dataset_id": right_manifest["dataset_id"]},
        "num_paired_questions": len(left_ids),
        "metrics": metrics,
        "paired_question_deltas": paired_question_deltas,
    }


def write_comparison(comparison: Dict[str, Any], out_dir: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_dir / comparison["exam_id"] / timestamp
    target.mkdir(parents=True, exist_ok=True)
    (target / f"comparison_{timestamp}.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# {comparison['exam_id']} 配对比较",
        "",
        f"- 配对题数: {comparison['num_paired_questions']}",
        f"- Left: {comparison['left']['backend']} / {comparison['left']['dataset_id']}",
        f"- Right: {comparison['right']['backend']} / {comparison['right']['dataset_id']}",
        "",
        "| 指标 | Left | Right | Right-Left | 95% CI | p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in comparison["metrics"].items():
        lines.append(
            f"| {name} | {values['left_mean']} | {values['right_mean']} | "
            f"{values['right_minus_left']} | {values['paired_bootstrap_ci95']} | "
            f"{values['paired_randomization_pvalue']} |"
        )
    (target / f"comparison_{timestamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
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


def int_list(value: str) -> List[int]:
    try:
        parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是逗号分隔的正整数") from exc
    if not parsed or any(item <= 0 for item in parsed) or len(parsed) != len(set(parsed)):
        raise argparse.ArgumentTypeError("必须是不重复的正整数列表")
    return sorted(parsed)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 检索评测脚本 v3.0")
    parser.add_argument("--exam-dir", default=str(DEFAULT_EXAM_DIR))
    parser.add_argument("--exam", default=None)
    parser.add_argument("--backend", choices=SUPPORTED_BACKENDS, default=DEFAULT_BACKEND)
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--dataset-revision", default=None)
    parser.add_argument("--request-k", type=positive_int, default=10)
    parser.add_argument("--eval-k", type=int_list, default=[1, 3, 5, 10])
    parser.add_argument("--primary-k", type=positive_int, default=5)
    parser.add_argument("--char-budgets", type=int_list, default=[1000, 2000, 4000])
    parser.add_argument("--graph-search", action="store_true")
    parser.add_argument(
        "--dify-search-method",
        choices=DIFY_SEARCH_METHODS,
        default="hybrid_search",
        help="(Dify) 检索策略；coverage_search 会按覆盖索引接口发送 null weights/graph_search",
    )
    parser.add_argument(
        "--score-threshold-enabled",
        action="store_true",
        help="(Dify) 启用请求体 score_threshold_enabled；未提供阈值时发送 null",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=None,
        help="(Dify) 分数阈值；提供该值时自动启用阈值",
    )
    parser.add_argument("--limit", type=nonnegative_int, default=0)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--sleep", type=nonnegative_float, default=0.5)
    parser.add_argument("--timeout", type=positive_float, default=30.0)
    parser.add_argument("--retries", type=positive_int, default=3)
    parser.add_argument("--raw-content-limit", type=positive_int, default=CONTENT_TRUNCATE)
    parser.add_argument("--allow-corpus-drift", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"))
    args = parser.parse_args(argv)
    if args.primary_k not in args.eval_k:
        parser.error("--primary-k 必须包含在 --eval-k 中")
    if args.backend == "dify" and max(args.eval_k) > args.request_k:
        parser.error("Dify 的 --request-k 不能小于 --eval-k 最大值")
    if args.graph_search and args.backend != "dify":
        parser.error("--graph-search 仅支持 Dify")
    if args.dify_search_method != "hybrid_search" and args.backend != "dify":
        parser.error("--dify-search-method 仅支持 Dify")
    if args.dify_search_method == "coverage_search" and args.graph_search:
        parser.error("coverage_search 不能与 --graph-search 同时使用")
    if args.score_threshold is not None:
        if args.backend != "dify":
            parser.error("--score-threshold 仅支持 Dify")
        args.score_threshold_enabled = True
    if args.score_threshold_enabled and args.backend != "dify":
        parser.error("--score-threshold-enabled 仅支持 Dify")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.compare:
        try:
            comparison = compare_runs(Path(args.compare[0]), Path(args.compare[1]))
            out_root = Path(args.out_dir).expanduser() if args.out_dir else RESULTS_ROOT / "考试结果-v3-比较"
            target = write_comparison(comparison, out_root)
        except ComparisonError as exc:
            sys.stderr.write(f"[比较失败] {exc}\n")
            return 5
        print(f"比较结果已输出到: {target}")
        return 0

    try:
        exam_files = discover_exam_files(args.exam_dir, args.exam)
        loaded = [
            (path, *load_and_validate_exam(path, args.allow_corpus_drift))
            for path in exam_files
        ]
    except (FileNotFoundError, ExamValidationError) as exc:
        sys.stderr.write(f"[校验失败] {exc}\n")
        return 2
    if args.validate_only:
        for path, exam, validation in loaded:
            print(
                f"[校验通过] {path}: {len(exam['questions'])} 题, "
                f"{validation['total_claims']} claims, corpus_drift={validation['corpus_drift']}"
            )
            for warning in validation["warnings"]:
                print(f"  [警告] {warning}")
        return 0

    try:
        headers = build_headers(args.backend)
    except AuthenticationError as exc:
        sys.stderr.write(f"[错误] {exc}\n")
        return 3
    session = requests.Session()
    session.headers.update(headers)
    if args.backend == "arag":
        print(
            "[警告] aRAG 接口不支持请求侧 K 参数；--request-k 仅作为目标深度记录，"
            "实际以响应返回数量为准。"
        )
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out_dir).expanduser() if args.out_dir else RESULTS_ROOT / f"考试结果-v3-{args.backend}"

    for path, exam, validation in loaded:
        run_scope = "smoke" if args.limit else "full"
        manifest = build_manifest(exam, validation, args, timestamp, run_scope)
        try:
            results = evaluate_exam(exam, session, args)
            run_status = "completed"
        except EvaluationAborted as exc:
            results = exc.results
            run_status = "aborted_auth"
        summary = aggregate(
            results,
            args.primary_k,
            args.eval_k,
            args.char_budgets,
            run_status,
            run_scope,
            validation["corpus_drift"],
        )
        out_dir = write_outputs(
            out_root, exam, validation, summary, results, manifest, timestamp
        )
        print(f"结果已输出到: {out_dir}")
        if run_status == "aborted_auth":
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
