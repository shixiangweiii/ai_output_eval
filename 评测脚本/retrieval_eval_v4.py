# -*- coding: utf-8 -*-
"""RAG 检索评测器 v4.0：配置驱动多后端、分段中立召回、拒答分离度与聚类推断。

与 v3(`retrieval_eval.py`) 的关系：v3 已冻结，仅供历史归档复现；本文件是自包含的
后继实现，不 import v3。`normalize_text` 的语义与 v3 逐字一致，由单测锁定。
"""

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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - 依赖缺失时的引导信息
    sys.stderr.write("缺少依赖 requests，请先安装 评测脚本/requirements.txt\n")
    raise


SCHEMA_VERSION = "4.1"
METRICS_VERSION = "4.1"
BOOTSTRAP_SEED = 20260804
BOOTSTRAP_SAMPLES = 10_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_EXAM_DIR = PROJECT_ROOT / "评测考试" / "考卷-v4-含真多跳题"
RESULTS_ROOT = PROJECT_ROOT / "评测考试"
PROFILE_DIR = SCRIPT_PATH.parent / "后端配置"
DEFAULT_PROFILE = "dify-dataset-api"
PREFLIGHT_RESPONSES = 3
CONTENT_TRUNCATE = 800
RETRYABLE_STATUS_CODES = {408, 429}

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
ALLOWED_ROLES = {"scored", "sanity", "unanswerable"}
EVIDENCE_ROLES = {"scored", "sanity"}
ALLOWED_CLAIM_TYPES = {"anchor", "passage"}
ALLOWED_HOP_ROLES = {"endpoint", "bridge", "supporting"}
CHAIN_TYPE = "cross_doc_chain"
ALLOWED_CLAIM_KINDS = {"text", "asset"}
ALLOWED_AUTH_MODES = {"cookie_csrf", "bearer_env", "header_env", "none"}

NEGATIVE_INFINITY = float("-inf")
INFERENCE_WARNING = (
    "同一 cluster_id 内的题目高度相关；置信区间与 p 值均以 cluster 为聚类单元计算。"
    "独立 cluster 数较少时，推断结果仅供探索性参考。"
)


class RetrievalError(Exception):
    """检索请求失败。"""


class AuthenticationError(RetrievalError):
    """鉴权失败(401/403)。"""


class ResponseSchemaError(RetrievalError):
    """后端响应结构不符合 profile 声明。"""


class ProfileError(ValueError):
    """后端 profile 配置非法。"""


class ExamValidationError(ValueError):
    """考卷校验失败。"""


class ComparisonError(ValueError):
    """多路比较前置条件不满足。"""


class DocumentNameMismatch(RetrievalError):
    """后端返回的文档名与考卷声明完全不匹配。"""

    def __init__(self, message: str, observed: Sequence[str], declared: Sequence[str]) -> None:
        super().__init__(message)
        self.observed = list(observed)
        self.declared = list(declared)


class EvaluationAborted(RetrievalError):
    """评测中止，携带已完成的部分结果。"""

    def __init__(self, message: str, results: List[Dict[str, Any]], status: str) -> None:
        super().__init__(message)
        self.results = results
        self.status = status


# --------------------------------------------------------------------------
# 文本归一（语义与 v3 逐字一致，勿单方面修改）
# --------------------------------------------------------------------------

_DASH_TRANSLATION = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
)
_QUOTE_TRANSLATION = str.maketrans(
    {"“": '"', "”": '"', "‘": "'", "’": "'", "＂": '"'}
)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_DOC_EXTENSION_RE = re.compile(r"\.(md|markdown|txt|pdf|docx?|html?|json)$")


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


def document_key(name: str) -> str:
    """跨产品文档名归一：去路径、去扩展名、NFKC、小写、去空白。

    claim 命中要求文档身份一致；不同 RAG 产品返回的文档名可能带路径、带/不带
    扩展名或大小写不同，直接字符串相等会导致整卷静默零分。
    """
    if not name:
        return ""
    value = unicodedata.normalize("NFKC", str(name)).strip()
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = _DOC_EXTENSION_RE.sub("", value.lower())
    return re.sub(r"\s+", "", value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExamValidationError(f"{label} 必须是非空字符串")
    return value.strip()


def _require_string_list(value: Any, label: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise ExamValidationError(f"{label} 必须是非空字符串数组")
    return [_require_string(item, f"{label}[]") for item in value]


def _require_int_list(value: Any, label: str) -> List[int]:
    if not isinstance(value, list) or not value:
        raise ExamValidationError(f"{label} 必须是非空整数数组")
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise ExamValidationError(f"{label} 只能包含正整数")
    return list(value)


# --------------------------------------------------------------------------
# 后端 profile：产品差异全部收敛在配置里，指标数学与后端无关
# --------------------------------------------------------------------------

class BackendProfile:
    """描述一个 RAG 产品检索接口的调用与响应映射方式。

    刻意不用 dataclass：本仓库既有的 importlib 加载方式不会把模块注册进
    ``sys.modules``，而 ``@dataclass`` 依赖该注册，会在加载期直接抛错。
    """

    __slots__ = (
        "name", "url_template", "auth", "request_template", "field_map",
        "records_path", "method", "auth_env", "token_header", "token_format",
        "cookie_header", "extra_headers", "supports_request_k", "success_path",
        "error_message_paths", "description", "source_path", "source_sha256",
    )

    def __init__(
        self,
        name: str,
        url_template: str,
        auth: str,
        request_template: Dict[str, Any],
        field_map: Dict[str, str],
        records_path: str = "",
        method: str = "POST",
        auth_env: Optional[Dict[str, str]] = None,
        token_header: str = "authorization",
        token_format: str = "Bearer {token}",
        cookie_header: str = "cookie",
        extra_headers: Optional[Dict[str, str]] = None,
        supports_request_k: bool = True,
        success_path: Optional[str] = None,
        error_message_paths: Optional[List[str]] = None,
        description: str = "",
        source_path: Optional[str] = None,
        source_sha256: Optional[str] = None,
    ) -> None:
        self.name = name
        self.url_template = url_template
        self.auth = auth
        self.request_template = request_template
        self.field_map = field_map
        self.records_path = records_path
        self.method = method
        self.auth_env = auth_env or {}
        self.token_header = token_header
        self.token_format = token_format
        self.cookie_header = cookie_header
        self.extra_headers = extra_headers or {}
        self.supports_request_k = supports_request_k
        self.success_path = success_path
        self.error_message_paths = error_message_paths or []
        self.description = description
        self.source_path = source_path
        self.source_sha256 = source_sha256


_PROFILE_REQUIRED = ("name", "url_template", "auth", "request_template", "field_map")
_FIELD_MAP_REQUIRED = ("document_name", "content")


def load_profile(reference: str) -> BackendProfile:
    """按名称或路径加载后端 profile。"""
    candidate = Path(reference).expanduser()
    if not candidate.is_file():
        candidate = PROFILE_DIR / f"{reference}.json"
    if not candidate.is_file():
        available = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))
        raise ProfileError(f"找不到后端 profile: {reference}；内置可选 {available}")
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"无法读取 profile {candidate}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"profile {candidate} 根节点必须是 object")
    for key in _PROFILE_REQUIRED:
        if key not in raw:
            raise ProfileError(f"profile {candidate} 缺少字段: {key}")
    if raw["auth"] not in ALLOWED_AUTH_MODES:
        raise ProfileError(f"profile {candidate} 的 auth 非法: {raw['auth']}")
    field_map = raw["field_map"]
    if not isinstance(field_map, dict):
        raise ProfileError(f"profile {candidate} 的 field_map 必须是 object")
    for key in _FIELD_MAP_REQUIRED:
        if not field_map.get(key):
            raise ProfileError(f"profile {candidate} 的 field_map 缺少: {key}")
    if not isinstance(raw["request_template"], dict):
        raise ProfileError(f"profile {candidate} 的 request_template 必须是 object")
    known = set(BackendProfile.__slots__) - {"source_path", "source_sha256"}
    payload = {key: value for key, value in raw.items() if key in known}
    profile = BackendProfile(**payload)
    profile.source_path = str(candidate)
    profile.source_sha256 = sha256_file(candidate)
    return profile


def _dig(node: Any, path: str) -> Any:
    """按点分路径取值，任一层缺失即返回 None。"""
    if not path:
        return node
    current = node
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _fill_template(node: Any, context: Dict[str, Any]) -> Any:
    """递归替换请求体模板里的占位符，整串占位符保留原始类型。"""
    if isinstance(node, dict):
        return {key: _fill_template(value, context) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill_template(item, context) for item in node]
    if isinstance(node, str):
        stripped = node.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            key = stripped[1:-1]
            if key in context:
                return context[key]
        rendered = node
        for key, value in context.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered
    return node


def build_request(
    profile: BackendProfile, query: str, dataset_id: str, request_k: int
) -> Dict[str, Any]:
    """按 profile 模板构造请求体。"""
    context = {"query": query, "dataset_id": dataset_id, "top_k": int(request_k)}
    payload = _fill_template(copy.deepcopy(profile.request_template), context)
    if not isinstance(payload, dict):
        raise ProfileError("request_template 渲染结果必须是 object")
    return payload


def resolve_url(profile: BackendProfile, dataset_id: str) -> str:
    return profile.url_template.format(dataset_id=dataset_id)


def _env_value(name: str, label: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AuthenticationError(f"缺少环境变量 {name}（{label}）")
    return value


def build_headers(profile: BackendProfile) -> Dict[str, str]:
    """按 profile 声明的鉴权方式组装请求头；凭证只从环境变量读取。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "user-agent": "retrieval-eval-v4",
    }
    headers.update(profile.extra_headers)
    if profile.auth == "none":
        return headers
    if profile.auth == "cookie_csrf":
        cookie_env = profile.auth_env.get("cookie")
        token_env = profile.auth_env.get("token")
        if not cookie_env or not token_env:
            raise ProfileError("cookie_csrf 需要 auth_env.cookie 与 auth_env.token")
        headers[profile.cookie_header] = _env_value(cookie_env, "会话 Cookie")
        headers[profile.token_header] = _env_value(token_env, "CSRF Token")
        return headers
    if profile.auth == "bearer_env":
        token_env = profile.auth_env.get("token")
        if not token_env:
            raise ProfileError("bearer_env 需要 auth_env.token")
        token = _env_value(token_env, "API Key")
        headers[profile.token_header] = profile.token_format.format(token=token)
        return headers
    for header_name, env_name in profile.auth_env.items():
        headers[header_name] = _env_value(env_name, f"请求头 {header_name}")
    return headers


def _ranking_anomaly(chunks: Sequence[Dict[str, Any]]) -> bool:
    scores = [item["relevance_score"] for item in chunks if item["relevance_score"] is not None]
    return any(current > previous for previous, current in zip(scores, scores[1:]))


def _numeric(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finalize_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for rank, chunk in enumerate(chunks, 1):
        chunk["rank"] = rank
    return chunks


def _optional_str(record: Dict[str, Any], field_map: Dict[str, str], key: str) -> str:
    path = field_map.get(key)
    if not path:
        return ""
    value = _dig(record, path)
    return str(value) if value is not None else ""


def parse_response(
    profile: BackendProfile, response: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], bool]:
    """按 profile 的 field_map 把任意产品的响应归一成统一 Chunk 结构。"""
    if profile.success_path is not None:
        flag = _dig(response, profile.success_path)
        if not isinstance(flag, bool):
            raise ResponseSchemaError(f"响应 {profile.success_path} 必须是布尔值")
        if not flag:
            reasons = [str(_dig(response, path)) for path in profile.error_message_paths]
            reasons = [item for item in reasons if item and item != "None"]
            raise RetrievalError(f"后端返回失败: {reasons or 'unknown'}")
    records = _dig(response, profile.records_path)
    if records is None and not profile.records_path:
        records = response
    if not isinstance(records, list):
        raise ResponseSchemaError(
            f"响应 {profile.records_path or '<root>'} 必须是数组"
        )
    field_map = profile.field_map
    chunks: List[Dict[str, Any]] = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ResponseSchemaError(f"响应记录[{index - 1}] 必须是 object")
        name = str(_dig(record, field_map["document_name"]) or "")
        chunks.append(
            {
                "original_index": index,
                "server_top": _dig(record, field_map["server_top"]) if field_map.get("server_top") else None,
                "relevance_score": _numeric(
                    _dig(record, field_map["relevance_score"])
                    if field_map.get("relevance_score")
                    else None
                ),
                "document_name": name,
                "document_key": document_key(name),
                "document_id": _optional_str(record, field_map, "document_id"),
                "chunk_id": _optional_str(record, field_map, "chunk_id"),
                "content": str(_dig(record, field_map["content"]) or ""),
            }
        )
    return _finalize_chunks(chunks), _ranking_anomaly(chunks)


def apply_document_aliases(
    chunks: Sequence[Dict[str, Any]], alias_map: Dict[str, str]
) -> List[Dict[str, Any]]:
    """把产品返回的文档名按别名表映射后重新计算 document_key。"""
    for chunk in chunks:
        raw = chunk["document_name"]
        mapped = alias_map.get(raw) or alias_map.get(document_key(raw))
        chunk["document_key"] = document_key(mapped or raw)
    return list(chunks)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

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


def call_backend(
    session: "requests.Session",
    profile: BackendProfile,
    query: str,
    dataset_id: str,
    timeout: float,
    retries: int,
    request_k: int,
) -> Dict[str, Any]:
    return _post_json(
        session,
        resolve_url(profile, dataset_id),
        build_request(profile, query, dataset_id, request_k),
        timeout,
        retries,
    )


# --------------------------------------------------------------------------
# 考卷校验
# --------------------------------------------------------------------------

def _protocol(meta: Dict[str, Any]) -> Dict[str, Any]:
    protocol = meta.get("retrieval_protocol")
    if not isinstance(protocol, dict):
        raise ExamValidationError("exam_meta.retrieval_protocol 必须是 object")
    primary_k = protocol.get("primary_k")
    request_k = protocol.get("request_k")
    primary_budget = protocol.get("primary_budget")
    eval_ks = _require_int_list(protocol.get("eval_ks"), "retrieval_protocol.eval_ks")
    char_budgets = _require_int_list(
        protocol.get("char_budgets"), "retrieval_protocol.char_budgets"
    )
    for label, value in (("primary_k", primary_k), ("request_k", request_k),
                         ("primary_budget", primary_budget)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ExamValidationError(f"retrieval_protocol.{label} 必须是正整数")
    if primary_k not in eval_ks:
        raise ExamValidationError("retrieval_protocol.primary_k 必须包含在 eval_ks 中")
    if max(eval_ks) > request_k:
        raise ExamValidationError("retrieval_protocol.request_k 不能小于 eval_ks 最大值")
    if primary_budget not in char_budgets:
        raise ExamValidationError(
            "retrieval_protocol.primary_budget 必须包含在 char_budgets 中"
        )
    return {
        "primary_k": primary_k,
        "eval_ks": sorted(set(eval_ks)),
        "request_k": request_k,
        "char_budgets": sorted(set(char_budgets)),
        "primary_budget": primary_budget,
    }


def _declared_counts(meta: Dict[str, Any]) -> Dict[str, Any]:
    counts = meta.get("question_counts")
    if not isinstance(counts, dict):
        raise ExamValidationError("exam_meta.question_counts 必须是 object")
    for name in ("total", "scored", "sanity", "unanswerable"):
        if not isinstance(counts.get(name), int) or counts[name] < 0:
            raise ExamValidationError(f"question_counts.{name} 必须是非负整数")
    for name in ("by_primary_type", "by_difficulty"):
        if not isinstance(counts.get(name), dict):
            raise ExamValidationError(f"question_counts.{name} 必须是 object")
    return counts


def _load_corpus(meta: Dict[str, Any], allow_corpus_drift: bool) -> Tuple[Dict[str, str], Dict[str, str], List[str], bool]:
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
    declared = corpus.get("documents")
    if not isinstance(declared, list) or not declared:
        raise ExamValidationError("corpus.documents 必须是非空数组")

    document_text: Dict[str, str] = {}
    key_to_name: Dict[str, str] = {}
    warnings: List[str] = []
    drift = False
    for index, item in enumerate(declared):
        if not isinstance(item, dict):
            raise ExamValidationError(f"corpus.documents[{index}] 必须是 object")
        name = _require_string(item.get("name"), f"corpus.documents[{index}].name")
        expected = _require_string(item.get("sha256"), f"corpus.documents[{index}].sha256")
        if name in document_text:
            raise ExamValidationError(f"语料文件重复声明: {name}")
        key = document_key(name)
        if key in key_to_name:
            raise ExamValidationError(
                f"语料文件归一后重名，跨产品无法区分: {name} 与 {key_to_name[key]}"
            )
        doc_path = corpus_dir / name
        if not doc_path.is_file():
            raise ExamValidationError(f"语料文件不存在: {doc_path}")
        actual = sha256_file(doc_path)
        if actual != expected:
            drift = True
            warnings.append(f"{name} SHA-256 漂移: expected={expected}, actual={actual}")
        document_text[name] = normalize_text(doc_path.read_text(encoding="utf-8"))
        key_to_name[key] = name
    if drift and not allow_corpus_drift:
        raise ExamValidationError(
            "语料快照 SHA-256 不一致；使用 --allow-corpus-drift 可仅作诊断运行"
        )
    return document_text, key_to_name, warnings, drift


def _validate_hop_design(
    question: Dict[str, Any],
    question_id: str,
    query: str,
    document_text: Dict[str, str],
    chain_ids: set[str],
) -> Dict[str, str]:
    """校验实体桥接多跳设计，返回「文档 → hop_role」映射。

    **名称隔离是"真跳"的唯一硬保证**：桥接文档一旦出现端点实体，题面就能把它直接
    捞出来，这一跳就名存实亡——检索器不必先读端点文档拿到中间实体。v4 最初那 9 道
    `cross_doc_chain` 就是因为缺这条约束而退化成了近似副本的并行召回。
    """
    design = question.get("hop_design")
    if not isinstance(design, dict):
        raise ExamValidationError(f"{question_id} 链式题必须声明 hop_design")
    label = f"{question_id}.hop_design"
    chain_id = _require_string(design.get("chain_id"), f"{label}.chain_id")
    if chain_id not in chain_ids:
        raise ExamValidationError(f"{label}.chain_id 未在 exam_meta.bridge_chains 声明: {chain_id}")
    entities = _require_string_list(design.get("endpoint_entities"), f"{label}.endpoint_entities")
    if len(entities) != 2:
        raise ExamValidationError(f"{label}.endpoint_entities 必须恰好包含两个端点实体")
    endpoints = _require_string_list(design.get("endpoint_documents"), f"{label}.endpoint_documents")
    if len(endpoints) != 2:
        raise ExamValidationError(f"{label}.endpoint_documents 必须恰好包含两个文档")
    bridges = _require_string_list(design.get("bridge_documents"), f"{label}.bridge_documents")
    supporting = design.get("supporting_documents", [])
    if not isinstance(supporting, list) or any(not isinstance(x, str) for x in supporting):
        raise ExamValidationError(f"{label}.supporting_documents 必须是字符串数组")

    role_map: Dict[str, str] = {}
    for name, hop_role in (
        [(d, "endpoint") for d in endpoints]
        + [(d, "bridge") for d in bridges]
        + [(d, "supporting") for d in supporting]
    ):
        if name in role_map:
            raise ExamValidationError(f"{label} 的文档角色不能重叠: {name}")
        if name not in document_text:
            raise ExamValidationError(f"{label} 引用了未声明语料: {name}")
        role_map[name] = hop_role

    normalized_query = normalize_text(query)
    for entity in entities:
        if normalize_text(entity) not in normalized_query:
            raise ExamValidationError(
                f"{question_id} 端点实体 {entity!r} 未在题面出现，检索器无从下手"
            )
    for bridge in bridges:
        leaked = [e for e in entities if normalize_text(e) in document_text[bridge]]
        if leaked:
            raise ExamValidationError(
                f"{question_id} 桥接文档 {bridge} 泄露端点实体 {leaked}；"
                "题面可直接命中该文档，这一跳不成立"
            )
    return role_map


def load_and_validate_exam(
    path: Path, allow_corpus_drift: bool = False
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取 v4 考卷并执行结构、语料哈希、span 存在性与唯一性校验。"""
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
    protocol = _protocol(meta)
    counts = _declared_counts(meta)
    document_text, key_to_name, warnings, corpus_drift = _load_corpus(meta, allow_corpus_drift)

    clusters = meta.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise ExamValidationError("exam_meta.clusters 必须是非空数组")
    cluster_ids: set[str] = set()
    for index, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            raise ExamValidationError(f"clusters[{index}] 必须是 object")
        cluster_id = _require_string(cluster.get("id"), f"clusters[{index}].id")
        if cluster_id in cluster_ids:
            raise ExamValidationError(f"cluster id 重复: {cluster_id}")
        cluster_ids.add(cluster_id)
        _require_string(cluster.get("family"), f"clusters[{index}].family")
        _require_string(cluster.get("axis"), f"clusters[{index}].axis")
        for name in _require_string_list(
            cluster.get("documents"), f"clusters[{index}].documents"
        ):
            if name not in document_text:
                raise ExamValidationError(f"clusters[{index}] 引用了未声明语料: {name}")

    chain_ids: set[str] = set()
    bridge_chains = meta.get("bridge_chains", [])
    if not isinstance(bridge_chains, list):
        raise ExamValidationError("exam_meta.bridge_chains 必须是数组")
    for index, chain in enumerate(bridge_chains):
        if not isinstance(chain, dict):
            raise ExamValidationError(f"bridge_chains[{index}] 必须是 object")
        chain_id = _require_string(chain.get("id"), f"bridge_chains[{index}].id")
        if chain_id in chain_ids:
            raise ExamValidationError(f"bridge_chains id 重复: {chain_id}")
        chain_ids.add(chain_id)
        _require_string(chain.get("relation_type"), f"bridge_chains[{index}].relation_type")
        _require_string_list(chain.get("bridge_entities"), f"bridge_chains[{index}].bridge_entities")
        for name in _require_string_list(
            chain.get("documents"), f"bridge_chains[{index}].documents"
        ):
            if name not in document_text:
                raise ExamValidationError(f"bridge_chains[{index}] 引用了未声明语料: {name}")

    design = meta.get("design_constraints")
    if not isinstance(design, dict):
        raise ExamValidationError("exam_meta.design_constraints 必须是 object")
    min_claims = int(design.get("min_claims_per_document", 0))
    max_share = float(design.get("max_claim_share_per_document", 1.0))
    min_low_overlap = int(design.get("min_low_lexical_overlap_questions", 0))
    min_hard_negative = int(design.get("min_hard_negative_questions", 0))
    min_spans = int(design.get("min_spans_per_claim", 1))
    max_span_len = int(design.get("max_span_normalized_len", 120))
    min_sanity = int(design.get("min_sanity_questions", 0))
    min_unanswerable = int(design.get("min_unanswerable_questions", 0))

    seen_question_ids: set[str] = set()
    seen_claim_ids: set[str] = set()
    seen_spans: set[Tuple[str, str]] = set()
    type_counts: Counter[str] = Counter()
    difficulty_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    doc_claim_counts: Counter[str] = Counter()
    cluster_question_counts: Counter[str] = Counter()
    chain_question_counts: Counter[str] = Counter()
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
        role = question.get("question_role")
        if role not in ALLOWED_ROLES:
            raise ExamValidationError(
                f"{question_id}.question_role 必须是 {sorted(ALLOWED_ROLES)} 之一"
            )
        difficulty = question.get("difficulty")
        primary_type = question.get("primary_type")
        if difficulty not in ALLOWED_DIFFICULTIES:
            raise ExamValidationError(f"{question_id}.difficulty 不合法")
        if primary_type not in ALLOWED_TYPES:
            raise ExamValidationError(f"{question_id}.primary_type 不合法")
        query = _require_string(question.get("question"), f"{question_id}.question")
        cluster_id = _require_string(question.get("cluster_id"), f"{question_id}.cluster_id")
        if cluster_id not in cluster_ids:
            raise ExamValidationError(f"{question_id}.cluster_id 未在 exam_meta.clusters 声明")
        tags = question.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ExamValidationError(f"{question_id}.tags 必须是字符串数组")
        if len(tags) != len(set(tags)):
            raise ExamValidationError(f"{question_id}.tags 不能重复")
        claims = question.get("claims")
        negatives = question.get("negative_evidence", [])
        if not isinstance(claims, list) or not isinstance(negatives, list):
            raise ExamValidationError(f"{question_id}.claims/negative_evidence 必须是数组")

        role_counts[role] += 1
        type_counts[primary_type] += 1
        difficulty_counts[difficulty] += 1
        cluster_question_counts[cluster_id] += 1
        claim_ids_for_question: List[str] = []
        spans_for_question: List[str] = []
        positive_keys: set[Tuple[str, str]] = set()

        if role in EVIDENCE_ROLES:
            if difficulty == "diagnostic" or primary_type == "unanswerable_diagnostic":
                raise ExamValidationError(f"{question_id} 计分/哨兵题不能使用 diagnostic 类型")
            if not claims:
                raise ExamValidationError(f"{question_id} 计分/哨兵题必须包含 claims")
            _require_string(question.get("reference_answer"), f"{question_id}.reference_answer")
        else:
            if difficulty != "diagnostic" or primary_type != "unanswerable_diagnostic":
                raise ExamValidationError(f"{question_id} 不可答题必须使用 diagnostic 类型")
            if claims:
                raise ExamValidationError(f"{question_id} 不可答题 claims 必须为空")
            if question.get("expected_behavior") != "no_relevant_evidence":
                raise ExamValidationError(
                    f"{question_id}.expected_behavior 必须为 no_relevant_evidence"
                )
            _require_string(question.get("diagnostic_reason"), f"{question_id}.diagnostic_reason")

        hop_roles: Dict[str, str] = {}
        if primary_type == CHAIN_TYPE:
            if role not in EVIDENCE_ROLES:
                raise ExamValidationError(f"{question_id} 链式题必须是计分或哨兵题")
            hop_roles = _validate_hop_design(
                question, question_id, query, document_text, chain_ids
            )
            chain_question_counts[question["hop_design"]["chain_id"]] += 1
        elif question.get("hop_design") is not None:
            raise ExamValidationError(
                f"{question_id} 非链式题不应声明 hop_design"
            )
        hop_role_counts: Counter[str] = Counter()

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
            if kind not in ALLOWED_CLAIM_KINDS:
                raise ExamValidationError(f"{claim_label}.kind 必须是 text/asset")
            claim_type = claim.get("claim_type")
            if claim_type not in ALLOWED_CLAIM_TYPES:
                raise ExamValidationError(f"{claim_label}.claim_type 必须是 anchor/passage")
            source = _require_string(claim.get("source_document"), f"{claim_label}.source_document")
            if source not in document_text:
                raise ExamValidationError(f"{claim_label} 引用了未声明语料: {source}")
            hop_role = claim.get("hop_role")
            if primary_type == CHAIN_TYPE:
                if hop_role not in ALLOWED_HOP_ROLES:
                    raise ExamValidationError(
                        f"{claim_label}.hop_role 必须是 {sorted(ALLOWED_HOP_ROLES)} 之一"
                    )
                expected = hop_roles.get(source)
                if expected is None:
                    raise ExamValidationError(
                        f"{claim_label} 的来源文档 {source} 未在 hop_design 里声明角色"
                    )
                if hop_role != expected:
                    raise ExamValidationError(
                        f"{claim_label}.hop_role={hop_role}，但 {source} 在 hop_design 里是 {expected}"
                    )
                hop_role_counts[hop_role] += 1
            elif hop_role is not None:
                raise ExamValidationError(f"{claim_label} 非链式题不应声明 hop_role")
            _require_string(claim.get("section"), f"{claim_label}.section")
            spans = _require_string_list(claim.get("accepted_spans"), f"{claim_label}.accepted_spans")
            if kind == "text" and len(spans) < min_spans:
                raise ExamValidationError(
                    f"{claim_label} 文本 claim 至少需要 {min_spans} 个 accepted_span，实际 {len(spans)}"
                )
            for span in spans:
                normalized_span = normalize_text(span)
                if kind == "text" and not 8 <= len(normalized_span) <= max_span_len:
                    raise ExamValidationError(
                        f"{claim_label} 文本 span 归一化长度必须在 8–{max_span_len}: {span}"
                    )
                occurrences = document_text[source].count(normalized_span)
                if occurrences == 0:
                    raise ExamValidationError(f"{claim_label} span 不存在于 {source}: {span}")
                if kind == "text" and occurrences > 1:
                    raise ExamValidationError(
                        f"{claim_label} span 在 {source} 中出现 {occurrences} 次，定位有歧义: {span}"
                    )
                span_key = (source, normalized_span)
                if span_key in seen_spans:
                    raise ExamValidationError(f"复用了原子证据 span: {source} / {span}")
                seen_spans.add(span_key)
                positive_keys.add(span_key)
                spans_for_question.append(span)
            doc_claim_counts[source] += 1
            total_claims += 1

        if primary_type == CHAIN_TYPE:
            if hop_role_counts["endpoint"] < 2:
                raise ExamValidationError(
                    f"{question_id} 链式题至少需要 2 个 endpoint claim（两端各一），"
                    f"实际 {hop_role_counts['endpoint']}"
                )
            if hop_role_counts["bridge"] < 1:
                raise ExamValidationError(
                    f"{question_id} 链式题至少需要 1 个 bridge claim，否则没有跳"
                )

        if role in EVIDENCE_ROLES:
            evidence_chain = question.get("evidence_chain")
            if not isinstance(evidence_chain, list) or evidence_chain != claim_ids_for_question:
                raise ExamValidationError(
                    f"{question_id}.evidence_chain 必须按顺序完整列出本题 claim ID"
                )

        negative_keys: set[Tuple[str, str]] = set()
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
            for span in _require_string_list(
                negative.get("accepted_spans"), f"{negative_label}.accepted_spans"
            ):
                normalized_span = normalize_text(span)
                if normalized_span not in document_text[source]:
                    raise ExamValidationError(f"{negative_label} span 不存在于 {source}: {span}")
                negative_keys.add((source, normalized_span))
        if positive_keys & negative_keys:
            raise ExamValidationError(f"{question_id} 的正向和负向证据发生重叠")
        if role in EVIDENCE_ROLES and negatives:
            hard_negative_count += 1

        if "low_lexical_overlap" in tags:
            if role not in EVIDENCE_ROLES:
                raise ExamValidationError(f"{question_id} 不可答题不使用 low_lexical_overlap 标签")
            maximum = max(
                (char_ngram_jaccard(query, span) for span in spans_for_question), default=0.0
            )
            if maximum > 0.25:
                raise ExamValidationError(
                    f"{question_id} low_lexical_overlap 不成立，最大 char-3 Jaccard={maximum:.4f}"
                )
            low_overlap_count += 1

    actual_counts = {
        "total": len(questions),
        "scored": role_counts["scored"],
        "sanity": role_counts["sanity"],
        "unanswerable": role_counts["unanswerable"],
        "by_primary_type": dict(sorted(type_counts.items())),
        "by_difficulty": dict(sorted(difficulty_counts.items())),
    }
    expected_counts = {
        "total": counts["total"],
        "scored": counts["scored"],
        "sanity": counts["sanity"],
        "unanswerable": counts["unanswerable"],
        "by_primary_type": dict(sorted(counts["by_primary_type"].items())),
        "by_difficulty": dict(sorted(counts["by_difficulty"].items())),
    }
    if actual_counts != expected_counts:
        raise ExamValidationError(
            f"question_counts 与实际不一致: declared={expected_counts}, actual={actual_counts}"
        )
    for name in document_text:
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
        raise ExamValidationError(f"硬负例题数 {hard_negative_count} 低于 {min_hard_negative}")
    if role_counts["sanity"] < min_sanity:
        raise ExamValidationError(
            f"sanity 题数 {role_counts['sanity']} 低于 {min_sanity}"
        )
    if role_counts["unanswerable"] < min_unanswerable:
        raise ExamValidationError(
            f"不可答题数 {role_counts['unanswerable']} 低于 {min_unanswerable}"
        )

    validation = {
        "exam_sha256": sha256_file(path),
        "corpus_drift": corpus_drift,
        "warnings": warnings,
        "protocol": protocol,
        "total_claims": total_claims,
        "document_claim_counts": dict(sorted(doc_claim_counts.items())),
        "declared_document_keys": sorted(key_to_name),
        "cluster_question_counts": dict(sorted(cluster_question_counts.items())),
        "chain_question_counts": dict(sorted(chain_question_counts.items())),
        "low_lexical_overlap_questions": low_overlap_count,
        "hard_negative_questions": hard_negative_count,
        "role_counts": dict(sorted(role_counts.items())),
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


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------

def _chunk_key(chunk: Dict[str, Any]) -> str:
    return chunk.get("document_key") or document_key(chunk.get("document_name", ""))


def _claim_detail(
    question: Dict[str, Any], chunks: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized_chunks = [normalize_text(chunk["content"]) for chunk in chunks]
    chunk_keys = [_chunk_key(chunk) for chunk in chunks]
    details = []
    for claim in question["claims"]:
        source_key = document_key(claim["source_document"])
        accepted = [(index, normalize_text(span)) for index, span in enumerate(claim["accepted_spans"])]
        match = None
        matched_span_index = None
        for chunk, content, key in zip(chunks, normalized_chunks, chunk_keys):
            if key != source_key:
                continue
            hit = next((idx for idx, span in accepted if span and span in content), None)
            if hit is not None:
                match = chunk
                matched_span_index = hit
                break
        source_present = any(key == source_key for key in chunk_keys)
        details.append(
            {
                "claim_id": claim["id"],
                "kind": claim["kind"],
                "claim_type": claim["claim_type"],
                "hop_role": claim.get("hop_role"),
                "source_document": claim["source_document"],
                "section": claim["section"],
                "hit": match is not None,
                "first_rank": match["rank"] if match else None,
                "matched_chunk_id": match["chunk_id"] if match else None,
                "matched_span_index": matched_span_index,
                "matched_span": (
                    claim["accepted_spans"][matched_span_index]
                    if matched_span_index is not None
                    else None
                ),
                "document_hit_claim_miss": source_present and match is None,
            }
        )
    return details


def _negative_detail(
    question: Dict[str, Any], chunks: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized_chunks = [normalize_text(chunk["content"]) for chunk in chunks]
    chunk_keys = [_chunk_key(chunk) for chunk in chunks]
    details = []
    for negative in question.get("negative_evidence", []):
        source_key = document_key(negative["source_document"])
        accepted = [normalize_text(span) for span in negative["accepted_spans"]]
        match = None
        for chunk, content, key in zip(chunks, normalized_chunks, chunk_keys):
            if key == source_key and any(span and span in content for span in accepted):
                match = chunk
                break
        details.append(
            {
                "negative_id": negative["id"],
                "source_document": negative["source_document"],
                "hit": match is not None,
                "first_rank": match["rank"] if match else None,
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
    gt_keys = list(dict.fromkeys(document_key(c["source_document"]) for c in question["claims"]))
    top_keys = [_chunk_key(chunk) for chunk in top_chunks]
    unique_keys = list(dict.fromkeys(top_keys))
    doc_hits = sum(key in set(unique_keys) for key in gt_keys)
    off_target = sum(1 for key in top_keys if key not in set(gt_keys))
    asset_claims = [item for item in claim_detail if item["kind"] == "asset"]

    def _by_type(name: str) -> Dict[str, int]:
        subset = [item for item in claim_detail if item["claim_type"] == name]
        return {"count": len(subset), "hits": sum(bool(item["hit"]) for item in subset)}

    def _by_hop(name: str) -> Dict[str, int]:
        subset = [item for item in claim_detail if item["hop_role"] == name]
        return {"count": len(subset), "hits": sum(bool(item["hit"]) for item in subset)}

    endpoint, bridge, supporting = _by_hop("endpoint"), _by_hop("bridge"), _by_hop("supporting")
    # 只有真正带桥接的题才有"路径"可言；bridge_only_miss 是多跳失败的标志性形态：
    # 两端都找到了，但连接两端的那份材料没找到 —— 检索器停在了第一跳。
    path_status = None
    bridge_only_miss = False
    if bridge["count"]:
        endpoint_ok = endpoint["hits"] == endpoint["count"]
        bridge_ok = bridge["hits"] == bridge["count"]
        supporting_ok = supporting["hits"] == supporting["count"]
        bridge_only_miss = endpoint_ok and not bridge_ok
        if endpoint_ok and bridge_ok and supporting_ok:
            path_status = "complete"
        elif endpoint_ok and bridge_ok:
            path_status = "supporting_missing"
        elif endpoint_ok:
            path_status = "bridge_missing"
        elif bridge_ok:
            path_status = "endpoint_missing"
        else:
            path_status = "multiple_missing"

    return {
        "k": k,
        "claim_count": claim_count,
        "claim_hits": claim_hits,
        "claim_recall": round(claim_recall, 4),
        "complete_evidence_chain": bool(claim_count and claim_hits == claim_count),
        "novel_claim_rank_score": round(novel_score, 4),
        "gt_document_count": len(gt_keys),
        "document_recall": round(doc_hits / len(gt_keys), 4) if gt_keys else None,
        "retrieved_documents": [chunk["document_name"] for chunk in top_chunks],
        "duplicate_document_rate": round(
            (len(top_keys) - len(unique_keys)) / len(top_keys), 4
        ) if top_keys else 0.0,
        "off_target_chunk_rate": round(off_target / len(top_keys), 4) if top_keys else 0.0,
        "response_depth_insufficient": len(retrieved) < k,
        "hard_negative_intrusion": any(item["hit"] for item in negative_detail),
        "negative_evidence_count": len(negative_detail),
        "asset_claim_count": len(asset_claims),
        "asset_claim_hits": sum(bool(item["hit"]) for item in asset_claims),
        "anchor_claims": _by_type("anchor"),
        "passage_claims": _by_type("passage"),
        "endpoint_claims": endpoint,
        "bridge_claims": bridge,
        "supporting_claims": supporting,
        "path_status": path_status,
        "bridge_only_miss": bridge_only_miss,
        "claim_detail": claim_detail,
        "negative_detail": negative_detail,
    }


def truncate_chunks_to_char_budget(
    chunks: Sequence[Dict[str, Any]], budget: int
) -> List[Dict[str, Any]]:
    """按归一化字符预算沿排名顺序填充上下文，得到对分段中立的视图。"""
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


def top_score(chunks: Sequence[Dict[str, Any]]) -> Optional[float]:
    return chunks[0]["relevance_score"] if chunks else None


def compute_question_metrics(
    question: Dict[str, Any],
    retrieved: Sequence[Dict[str, Any]],
    eval_ks: Sequence[int],
    char_budgets: Sequence[int],
    ranking_anomaly: bool,
) -> Dict[str, Any]:
    metrics_by_k = {str(k): compute_metrics_at_k(question, retrieved, k) for k in eval_ks}
    char_metrics = {}
    for budget in char_budgets:
        budget_chunks = truncate_chunks_to_char_budget(retrieved, budget)
        details = _claim_detail(question, budget_chunks)
        hits = sum(bool(item["hit"]) for item in details)
        char_metrics[str(budget)] = {
            "claim_hits": hits,
            "claim_count": len(details),
            "claim_recall": round(hits / len(details), 4) if details else 0.0,
            "consumed_chars": sum(
                len(normalize_text(chunk["content"])) for chunk in budget_chunks
            ),
        }
    return {
        "ranking_anomaly": ranking_anomaly,
        "response_depth": len(retrieved),
        "top_score": top_score(retrieved),
        "metrics_by_k": metrics_by_k,
        "char_budget_metrics": char_metrics,
        "retrieved_chunk_detail": [
            {
                "rank": chunk["rank"],
                "original_index": chunk["original_index"],
                "server_top": chunk["server_top"],
                "relevance_score": chunk["relevance_score"],
                "document_name": chunk["document_name"],
                "document_key": _chunk_key(chunk),
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "normalized_char_count": len(normalize_text(chunk["content"])),
            }
            for chunk in retrieved
        ],
    }


def unanswerable_metrics(retrieved: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    keys = [_chunk_key(chunk) for chunk in retrieved]
    unique = list(dict.fromkeys(keys))
    return {
        "returned_count": len(retrieved),
        "top_score": top_score(retrieved),
        "retrieved_documents": [chunk["document_name"] for chunk in retrieved],
        "duplicate_document_rate": round((len(keys) - len(unique)) / len(keys), 4) if keys else 0.0,
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


def relevance_counterfactual(
    chunks: Sequence[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """按 relevance_score 重排。空响应视为恒等重排，而非让整个指标不可用。"""
    if not chunks:
        return []
    if any(chunk.get("relevance_score") is None for chunk in chunks):
        return None
    ordered = [copy.deepcopy(chunk) for chunk in chunks]
    ordered.sort(key=lambda chunk: (-chunk["relevance_score"], chunk["original_index"]))
    return _finalize_chunks(ordered)


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------

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


def _group_by_cluster(pairs: Sequence[Tuple[str, float]]) -> Dict[str, List[float]]:
    groups: Dict[str, List[float]] = defaultdict(list)
    for cluster_id, value in pairs:
        groups[cluster_id].append(float(value))
    return dict(groups)


def clustered_bootstrap_ci(
    pairs: Sequence[Tuple[str, float]],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Optional[List[float]]:
    """以 cluster 为重采样单元的 bootstrap 95% CI。"""
    if not pairs:
        return None
    groups = _group_by_cluster(pairs)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    size = len(keys)
    means = []
    for _ in range(samples):
        pooled: List[float] = []
        for _ in range(size):
            pooled.extend(groups[keys[rng.randrange(size)]])
        means.append(_mean(pooled))
    means.sort()
    return [round(_percentile(means, 0.025), 4), round(_percentile(means, 0.975), 4)]


def mann_whitney_auc(
    positive: Sequence[float], negative: Sequence[float]
) -> Optional[float]:
    """AUC = P(可答题得分 > 不可答题得分) + 0.5 * P(相等)。"""
    if not positive or not negative:
        return None
    wins = 0.0
    for high in positive:
        for low in negative:
            if high > low:
                wins += 1.0
            elif high == low:
                wins += 0.5
    return round(wins / (len(positive) * len(negative)), 4)


def clustered_auc_ci(
    positive: Sequence[Tuple[str, float]],
    negative: Sequence[Tuple[str, float]],
    samples: int = BOOTSTRAP_SAMPLES // 5,
    seed: int = BOOTSTRAP_SEED,
) -> Optional[List[float]]:
    """两组各自按 cluster 重采样后重算 AUC 的 95% CI。"""
    if not positive or not negative:
        return None
    pos_groups = _group_by_cluster(positive)
    neg_groups = _group_by_cluster(negative)
    pos_keys, neg_keys = sorted(pos_groups), sorted(neg_groups)
    rng = random.Random(seed)
    values = []
    for _ in range(samples):
        pos_sample: List[float] = []
        for _ in range(len(pos_keys)):
            pos_sample.extend(pos_groups[pos_keys[rng.randrange(len(pos_keys))]])
        neg_sample: List[float] = []
        for _ in range(len(neg_keys)):
            neg_sample.extend(neg_groups[neg_keys[rng.randrange(len(neg_keys))]])
        auc = mann_whitney_auc(pos_sample, neg_sample)
        if auc is not None:
            values.append(auc)
    if len(values) < samples // 2:
        return None
    values.sort()
    return [round(_percentile(values, 0.025), 4), round(_percentile(values, 0.975), 4)]


def clustered_randomization_pvalue(
    cluster_deltas: Dict[str, List[float]],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    """整簇同号翻转的配对随机化检验。"""
    keys = sorted(cluster_deltas)
    pooled_all = [value for key in keys for value in cluster_deltas[key]]
    if not pooled_all or all(value == 0 for value in pooled_all):
        return 1.0
    observed = abs(_mean(pooled_all))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(samples):
        pooled: List[float] = []
        for key in keys:
            sign = 1.0 if rng.random() < 0.5 else -1.0
            pooled.extend(sign * value for value in cluster_deltas[key])
        extreme += abs(_mean(pooled)) >= observed
    return round((extreme + 1) / (samples + 1), 4)


def holm_adjust(pvalues: Sequence[float]) -> List[float]:
    """Holm-Bonferroni 校正，返回与输入同序的校正后 p 值。"""
    count = len(pvalues)
    if count == 0:
        return []
    order = sorted(range(count), key=lambda index: pvalues[index])
    adjusted = [0.0] * count
    running = 0.0
    for position, index in enumerate(order):
        value = (count - position) * pvalues[index]
        running = max(running, min(1.0, value))
        adjusted[index] = round(running, 4)
    return adjusted


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

def _metric_value(result: Dict[str, Any], k: int, field_name: str) -> float:
    metrics = result.get("metrics")
    if not metrics:
        return 0.0
    value = metrics["metrics_by_k"][str(k)].get(field_name)
    return float(value) if value is not None else 0.0


def _budget_value(result: Dict[str, Any], budget: int) -> float:
    metrics = result.get("metrics")
    if not metrics:
        return 0.0
    return float(metrics["char_budget_metrics"][str(budget)]["claim_recall"])


def _summarize_group(results: Sequence[Dict[str, Any]], primary_k: int, budget: int) -> Dict[str, Any]:
    return {
        "count": len(results),
        "claim_recall": round(_mean([_metric_value(r, primary_k, "claim_recall") for r in results]), 4),
        "complete_evidence_chain_rate": round(
            _mean([_metric_value(r, primary_k, "complete_evidence_chain") for r in results]), 4
        ),
        "budget_claim_recall": round(_mean([_budget_value(r, budget) for r in results]), 4),
        "novel_claim_rank_score": round(
            _mean([_metric_value(r, primary_k, "novel_claim_rank_score") for r in results]), 4
        ),
    }


def _abstention_score(result: Dict[str, Any]) -> Optional[float]:
    """不可答题/可答题用于分离度比较的单值：空响应记为 -inf。"""
    metrics = result.get("metrics") or result.get("unanswerable_metrics")
    if not metrics:
        return None
    depth = metrics.get("response_depth", metrics.get("returned_count", 0))
    if not depth:
        return NEGATIVE_INFINITY
    score = metrics.get("top_score")
    return float(score) if score is not None else None


def _abstention_block(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    answerable = [r for r in results if r["question_role"] in EVIDENCE_ROLES and r["status"] == "ok"]
    unanswerable = [r for r in results if r["question_role"] == "unanswerable" and r["status"] == "ok"]
    pos = [(r["cluster_id"], _abstention_score(r)) for r in answerable]
    neg = [(r["cluster_id"], _abstention_score(r)) for r in unanswerable]
    unavailable = any(value is None for _, value in pos + neg)
    block: Dict[str, Any] = {
        "answerable_questions": len(pos),
        "unanswerable_questions": len(neg),
        "answerable_empty_response_rate": round(
            _mean([1.0 if value == NEGATIVE_INFINITY else 0.0 for _, value in pos]), 4
        ) if pos else None,
        "unanswerable_empty_response_rate": round(
            _mean([1.0 if value == NEGATIVE_INFINITY else 0.0 for _, value in neg]), 4
        ) if neg else None,
    }
    if unavailable or not pos or not neg:
        block["auc"] = None
        block["auc_ci95"] = None
        block["unavailable_reason"] = (
            "后端未返回相关性分数，无法计算分离度" if unavailable else "缺少可答或不可答样本"
        )
        return block
    block["auc"] = mann_whitney_auc([v for _, v in pos], [v for _, v in neg])
    block["auc_ci95"] = clustered_auc_ci(pos, neg, seed=BOOTSTRAP_SEED + 3)
    block["unavailable_reason"] = None
    return block


def _hop_ratio(result: Dict[str, Any], primary_k: int, field_name: str) -> float:
    metrics = result.get("metrics")
    if not metrics:
        return 0.0
    block = metrics["metrics_by_k"][str(primary_k)][field_name]
    return block["hits"] / block["count"] if block["count"] else 0.0


def _multihop_block(results: Sequence[Dict[str, Any]], primary_k: int) -> Dict[str, Any]:
    """实体桥接多跳的专项统计。

    ``bridge_claim_recall`` 与 ``endpoint_claim_recall`` 的**差值**才是"能不能做真
    多跳"的读数：两者接近说明桥接文档本来就能被题面直接召回，那这一跳是假的。
    """
    chain = [
        item for item in results
        if item["question_role"] in EVIDENCE_ROLES and item.get("chain_id")
    ]
    if not chain:
        return {
            "chain_questions": 0,
            "bridge_claim_recall": None,
            "bridge_claim_recall_ci95": None,
            "endpoint_claim_recall": None,
            "bridge_only_miss_rate": None,
            "path_status": {},
            "by_chain": {},
        }
    bridge_pairs = [(i["cluster_id"], _hop_ratio(i, primary_k, "bridge_claims")) for i in chain]
    endpoint_pairs = [(i["cluster_id"], _hop_ratio(i, primary_k, "endpoint_claims")) for i in chain]
    evaluated = [i for i in chain if i.get("metrics")]
    statuses: Counter[str] = Counter()
    misses = []
    for item in evaluated:
        primary = item["metrics"]["metrics_by_k"][str(primary_k)]
        statuses[primary["path_status"] or "no_bridge_claim"] += 1
        misses.append(1.0 if primary["bridge_only_miss"] else 0.0)
    by_chain = {}
    for chain_id in sorted({i["chain_id"] for i in chain}):
        subset = [i for i in chain if i["chain_id"] == chain_id]
        by_chain[chain_id] = {
            "count": len(subset),
            "bridge_claim_recall": round(
                _mean([_hop_ratio(i, primary_k, "bridge_claims") for i in subset]), 4
            ),
            "endpoint_claim_recall": round(
                _mean([_hop_ratio(i, primary_k, "endpoint_claims") for i in subset]), 4
            ),
            "claim_recall": round(
                _mean([_metric_value(i, primary_k, "claim_recall") for i in subset]), 4
            ),
        }
    return {
        "chain_questions": len(chain),
        "bridge_claim_recall": round(_mean([v for _, v in bridge_pairs]), 4),
        "bridge_claim_recall_ci95": clustered_bootstrap_ci(
            bridge_pairs, seed=BOOTSTRAP_SEED + 4
        ),
        "endpoint_claim_recall": round(_mean([v for _, v in endpoint_pairs]), 4),
        "bridge_only_miss_rate": round(_mean(misses), 4) if misses else None,
        "path_status": dict(sorted(statuses.items())),
        "by_chain": by_chain,
    }


def aggregate(
    results: List[Dict[str, Any]],
    protocol: Dict[str, Any],
    run_status: str,
    run_scope: str,
    corpus_drift: bool,
    comparability: Dict[str, Any],
    unmatched_documents: Sequence[str],
) -> Dict[str, Any]:
    primary_k = protocol["primary_k"]
    eval_ks = protocol["eval_ks"]
    budgets = protocol["char_budgets"]
    primary_budget = protocol["primary_budget"]

    scored = [item for item in results if item["question_role"] == "scored"]
    sanity = [item for item in results if item["question_role"] == "sanity"]
    unanswerable = [item for item in results if item["question_role"] == "unanswerable"]
    successful = [item for item in results if item["status"] == "ok"]
    scored_ok = [item for item in scored if item.get("metrics")]
    completed = run_status == "completed"

    recall_pairs = [(i["cluster_id"], _metric_value(i, primary_k, "claim_recall")) for i in scored]
    chain_pairs = [(i["cluster_id"], _metric_value(i, primary_k, "complete_evidence_chain")) for i in scored]
    budget_pairs = [(i["cluster_id"], _budget_value(i, primary_budget)) for i in scored]
    abstention = _abstention_block(results)
    multihop = _multihop_block(results, primary_k)

    headline = None
    if completed:
        headline = {
            "primary_k": primary_k,
            "primary_budget": primary_budget,
            "query_macro_claim_recall": round(_mean([v for _, v in recall_pairs]), 4),
            "query_macro_claim_recall_ci95": clustered_bootstrap_ci(recall_pairs),
            "complete_evidence_chain_rate": round(_mean([v for _, v in chain_pairs]), 4),
            "complete_evidence_chain_rate_ci95": clustered_bootstrap_ci(
                chain_pairs, seed=BOOTSTRAP_SEED + 1
            ),
            "budget_claim_recall": round(_mean([v for _, v in budget_pairs]), 4),
            "budget_claim_recall_ci95": clustered_bootstrap_ci(
                budget_pairs, seed=BOOTSTRAP_SEED + 2
            ),
            "abstention_auc": abstention["auc"],
            "abstention_auc_ci95": abstention["auc_ci95"],
            "bridge_claim_recall": multihop["bridge_claim_recall"],
            "bridge_claim_recall_ci95": multihop["bridge_claim_recall_ci95"],
            "inference_unit": "cluster_id",
            "inference_warning": INFERENCE_WARNING,
        }

    k_curves = {
        str(k): {
            "query_macro_claim_recall": round(
                _mean([_metric_value(i, k, "claim_recall") for i in scored]), 4
            ),
            "complete_evidence_chain_rate": round(
                _mean([_metric_value(i, k, "complete_evidence_chain") for i in scored]), 4
            ),
            "novel_claim_rank_score": round(
                _mean([_metric_value(i, k, "novel_claim_rank_score") for i in scored]), 4
            ),
        }
        for k in eval_ks
    }
    char_curves = {
        str(budget): {
            "query_macro_claim_recall": round(
                _mean([_budget_value(i, budget) for i in scored]), 4
            )
        }
        for budget in budgets
    }

    total_claims = sum(item["claim_count"] for item in scored)
    total_hits = sum(
        int((item.get("metrics") or {}).get("metrics_by_k", {}).get(str(primary_k), {}).get("claim_hits", 0))
        for item in scored
    )
    anchor_count = anchor_hits = passage_count = passage_hits = 0
    for item in scored_ok:
        primary = item["metrics"]["metrics_by_k"][str(primary_k)]
        anchor_count += primary["anchor_claims"]["count"]
        anchor_hits += primary["anchor_claims"]["hits"]
        passage_count += primary["passage_claims"]["count"]
        passage_hits += primary["passage_claims"]["hits"]

    negative_questions = [i for i in scored_ok if i.get("negative_evidence_count", 0) > 0]
    asset_claims = sum(item.get("asset_claim_count", 0) for item in scored)
    asset_hits = sum(
        int((item.get("metrics") or {}).get("metrics_by_k", {}).get(str(primary_k), {}).get("asset_claim_hits", 0))
        for item in scored
    )
    latency_values = [float(i["latency_ms"]) for i in successful if i.get("latency_ms") is not None]
    depths = [int(i["metrics"]["response_depth"]) for i in scored_ok]
    review_queue = []
    for item in scored_ok:
        for detail in item["metrics"]["metrics_by_k"][str(primary_k)]["claim_detail"]:
            if detail["document_hit_claim_miss"]:
                review_queue.append(
                    {
                        "question_id": item["id"],
                        "claim_id": detail["claim_id"],
                        "claim_type": detail["claim_type"],
                        "source_document": detail["source_document"],
                        "section": detail["section"],
                    }
                )

    counterfactual_results = []
    for item in scored:
        copied = dict(item)
        copied["metrics"] = item.get("relevance_counterfactual_metrics")
        counterfactual_results.append(copied)
    counterfactual = None
    if scored_ok and all(i.get("relevance_counterfactual_metrics") is not None for i in scored_ok):
        counterfactual = {
            "query_macro_claim_recall": round(
                _mean([_metric_value(i, primary_k, "claim_recall") for i in counterfactual_results]), 4
            ),
            "complete_evidence_chain_rate": round(
                _mean([_metric_value(i, primary_k, "complete_evidence_chain") for i in counterfactual_results]), 4
            ),
            "budget_claim_recall": round(
                _mean([_budget_value(i, primary_budget) for i in counterfactual_results]), 4
            ),
        }

    sanity_ok = [i for i in sanity if i.get("metrics")]
    sanity_failed = [
        i["id"] for i in sanity_ok
        if _metric_value(i, primary_k, "complete_evidence_chain") != 1.0
    ]
    sanity_block = {
        "count": len(sanity),
        "evaluated": len(sanity_ok),
        "pass_rate": round(
            (len(sanity_ok) - len(sanity_failed)) / len(sanity_ok), 4
        ) if sanity_ok else None,
        "claim_recall": round(
            _mean([_metric_value(i, primary_k, "claim_recall") for i in sanity]), 4
        ) if sanity else None,
        "failed_question_ids": sanity_failed,
    }

    def _grouped(key: str) -> Dict[str, Any]:
        names = sorted({item[key] for item in scored})
        return {
            name: _summarize_group([i for i in scored if i[key] == name], primary_k, primary_budget)
            for name in names
        }

    comparison_eligible = (
        completed
        and run_scope == "full"
        and not corpus_drift
        and comparability["dataset_revision_verified"]
        and not comparability["git_dirty"]
        and not unmatched_documents
    )
    blockers = []
    if not completed:
        blockers.append(f"run_status={run_status}")
    if run_scope != "full":
        blockers.append("run_scope=smoke")
    if corpus_drift:
        blockers.append("corpus_drift")
    if not comparability["dataset_revision_verified"]:
        blockers.append("dataset_revision=unverified")
    if comparability["git_dirty"]:
        blockers.append("git_dirty")
    if unmatched_documents:
        blockers.append("unmatched_document_names")

    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "run_status": run_status,
        "run_scope": run_scope,
        "comparison_eligible": comparison_eligible,
        "comparison_blockers": blockers,
        "headline": headline,
        "sanity": sanity_block,
        "abstention": abstention,
        "multihop": multihop,
        "diagnostics": {
            "num_questions": len(results),
            "num_scored_questions": len(scored),
            "num_sanity_questions": len(sanity),
            "num_unanswerable_questions": len(unanswerable),
            "num_successful_requests": len(successful),
            "request_success_rate": round(len(successful) / len(results), 4) if results else 0.0,
            "error_counts": dict(sorted(Counter(
                i["status"] for i in results if i["status"] != "ok"
            ).items())),
            "unmatched_document_names": list(unmatched_documents),
            "claim_micro_recall": round(total_hits / total_claims, 4) if total_claims else 0.0,
            "anchor_claim_recall": round(anchor_hits / anchor_count, 4) if anchor_count else None,
            "passage_claim_recall": round(passage_hits / passage_count, 4) if passage_count else None,
            "mean_document_recall": round(
                _mean([_metric_value(i, primary_k, "document_recall") for i in scored]), 4
            ),
            "mean_duplicate_document_rate": round(
                _mean([_metric_value(i, primary_k, "duplicate_document_rate") for i in scored]), 4
            ),
            "mean_off_target_chunk_rate": round(
                _mean([_metric_value(i, primary_k, "off_target_chunk_rate") for i in scored]), 4
            ),
            "hard_negative_intrusion_rate": round(
                _mean([_metric_value(i, primary_k, "hard_negative_intrusion") for i in negative_questions]), 4
            ) if negative_questions else None,
            "hard_negative_questions_evaluated": len(negative_questions),
            "asset_source_coverage": round(asset_hits / asset_claims, 4) if asset_claims else None,
            "response_depth_insufficient_rate": round(
                _mean([_metric_value(i, primary_k, "response_depth_insufficient") for i in scored]), 4
            ),
            "response_depth": {
                "min": min(depths) if depths else None,
                "median": round(statistics.median(depths), 2) if depths else None,
                "max": max(depths) if depths else None,
            },
            "ranking_anomaly_count": sum(
                bool((i.get("metrics") or {}).get("ranking_anomaly")) for i in scored
            ),
            "document_hit_claim_miss_review_queue": review_queue,
            "latency_ms": {
                "p50": round(statistics.median(latency_values), 2) if latency_values else None,
                "p95": round(_percentile(sorted(latency_values), 0.95), 2) if latency_values else None,
            },
            "k_curves": k_curves,
            "char_budget_curves": char_curves,
            "relevance_counterfactual": counterfactual,
            "by_type": _grouped("primary_type"),
            "by_difficulty": _grouped("difficulty"),
            "by_cluster": _grouped("cluster_id"),
            "by_tag": {
                tag: _summarize_group(
                    [i for i in scored if tag in i["tags"]], primary_k, primary_budget
                )
                for tag in sorted({tag for i in scored for tag in i["tags"]})
            },
            "by_gt_document_count": {
                str(count): _summarize_group(
                    [i for i in scored if i["gt_document_count"] == count], primary_k, primary_budget
                )
                for count in sorted({i["gt_document_count"] for i in scored})
            },
            "unanswerable_questions": [
                {
                    "id": i["id"],
                    "cluster_id": i["cluster_id"],
                    "status": i["status"],
                    "metrics": i.get("unanswerable_metrics"),
                }
                for i in unanswerable
            ],
        },
    }


# --------------------------------------------------------------------------
# 运行
# --------------------------------------------------------------------------

def _truncate_response(
    response: Dict[str, Any], profile: BackendProfile, limit: int
) -> Dict[str, Any]:
    """按 profile 的 records/content 路径截断原始响应，避免结果文件膨胀。"""
    output = copy.deepcopy(response)
    records = _dig(output, profile.records_path)
    if records is None and not profile.records_path:
        records = output
    if not isinstance(records, list):
        return output
    content_path = profile.field_map["content"]
    parts = content_path.split(".")
    for record in records:
        node = record
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and isinstance(node.get(parts[-1]), str):
            node[parts[-1]] = node[parts[-1]][:limit]
    return output


def evaluate_exam(
    exam: Dict[str, Any],
    validation: Dict[str, Any],
    session: "requests.Session",
    profile: BackendProfile,
    args: argparse.Namespace,
    protocol: Dict[str, Any],
    alias_map: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    questions = exam["questions"][: args.limit or None]
    declared_keys = set(validation["declared_document_keys"])
    results: List[Dict[str, Any]] = []
    unmatched: Dict[str, None] = {}
    responses_seen = 0
    matched_any = False

    for index, question in enumerate(questions, 1):
        claims = question.get("claims", [])
        record: Dict[str, Any] = {
            "id": question["id"],
            "question_role": question["question_role"],
            "cluster_id": question["cluster_id"],
            "chain_id": (question.get("hop_design") or {}).get("chain_id"),
            "difficulty": question["difficulty"],
            "primary_type": question["primary_type"],
            "tags": question["tags"],
            "question": question["question"],
            "claim_count": len(claims),
            "negative_evidence_count": len(question.get("negative_evidence", [])),
            "asset_claim_count": sum(claim["kind"] == "asset" for claim in claims),
            "gt_document_count": len({claim["source_document"] for claim in claims}),
            "latency_ms": None,
        }
        print(f"  ({index}/{len(questions)}) {question['id']} 调用 {profile.name}...", flush=True)
        started = time.perf_counter()
        try:
            response = call_backend(
                session,
                profile,
                question["question"],
                args.dataset_id,
                args.timeout,
                args.retries,
                protocol["request_k"],
            )
            chunks, anomaly = parse_response(profile, response)
            chunks = apply_document_aliases(chunks, alias_map)
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

            for chunk in chunks:
                key = _chunk_key(chunk)
                if key in declared_keys:
                    matched_any = True
                else:
                    unmatched.setdefault(chunk["document_name"], None)
            if chunks:
                responses_seen += 1

            if question["question_role"] in EVIDENCE_ROLES:
                record["metrics"] = compute_question_metrics(
                    question, chunks, protocol["eval_ks"], protocol["char_budgets"], anomaly
                )
                reordered = relevance_counterfactual(chunks)
                record["relevance_counterfactual_metrics"] = (
                    compute_question_metrics(
                        question, reordered, protocol["eval_ks"], protocol["char_budgets"], anomaly
                    )
                    if reordered is not None
                    else None
                )
            else:
                record["unanswerable_metrics"] = unanswerable_metrics(chunks)
            record["raw_response"] = _truncate_response(response, profile, args.raw_content_limit)
            record["status"] = "ok"
        except AuthenticationError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "auth_error"
            record["error"] = str(exc)
            results.append(record)
            raise EvaluationAborted(str(exc), results, "aborted_auth") from exc
        except ResponseSchemaError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "response_schema_error"
            record["error"] = str(exc)
        except RetrievalError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["status"] = "request_error"
            record["error"] = str(exc)
        results.append(record)

        if (
            not args.skip_preflight
            and not matched_any
            and responses_seen >= PREFLIGHT_RESPONSES
        ):
            raise DocumentNameMismatch(
                "后端返回的文档名与考卷声明完全不匹配，本次评测无效。"
                f" 已观察到: {sorted(unmatched)[:5]}；考卷声明: {sorted(declared_keys)[:5]}。"
                " 请用 --document-alias-map 提供映射，或确认知识库灌的是同一份语料。",
                sorted(unmatched),
                sorted(declared_keys),
            )
        if index < len(questions) and args.sleep > 0:
            time.sleep(args.sleep)
    return results, sorted(unmatched)


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
    exam: Dict[str, Any],
    validation: Dict[str, Any],
    profile: BackendProfile,
    args: argparse.Namespace,
    protocol: Dict[str, Any],
    protocol_source: Dict[str, str],
    timestamp: str,
    run_scope: str,
    alias_map: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "timestamp": timestamp,
        "timezone": str(dt.datetime.now().astimezone().tzinfo),
        "exam_id": exam["exam_meta"]["exam_id"],
        "exam_sha256": validation["exam_sha256"],
        "corpus": exam["exam_meta"]["corpus"],
        "corpus_drift": validation["corpus_drift"],
        "backend_profile": profile.name,
        "backend_profile_path": profile.source_path,
        "backend_profile_sha256": profile.source_sha256,
        "auth_mode": profile.auth,
        "endpoint": profile.url_template,
        "dataset_id": args.dataset_id,
        "dataset_revision": args.dataset_revision or "unverified",
        "request_config": build_request(profile, "<query>", args.dataset_id, protocol["request_k"]),
        "request_k_support": "applied" if profile.supports_request_k else "unsupported_by_backend",
        "document_alias_map": alias_map,
        "retrieval_protocol": protocol,
        "retrieval_protocol_source": protocol_source,
        "ranking_policy": "production_response_order",
        "run_scope": run_scope,
        "raw_content_limit": args.raw_content_limit,
        "python_version": platform.python_version(),
        "evaluator_script_sha256": sha256_file(SCRIPT_PATH),
        "git": _git_info(),
        "security": "credentials and request headers are never persisted",
    }


def build_report(
    exam: Dict[str, Any], summary: Dict[str, Any], manifest: Dict[str, Any],
    results: Sequence[Dict[str, Any]],
) -> str:
    protocol = manifest["retrieval_protocol"]
    lines = [
        f"# {exam['exam_meta']['title']} - 检索评测报告 (v4)",
        "",
        f"- 考卷: {manifest['exam_id']}",
        f"- 后端 profile: {manifest['backend_profile']}",
        f"- Dataset: {manifest['dataset_id']}",
        f"- Dataset Revision: {manifest['dataset_revision']}",
        f"- 鉴权方式: {manifest['auth_mode']}",
        f"- 口径: primary_k={protocol['primary_k']} / request_k={protocol['request_k']} / "
        f"primary_budget={protocol['primary_budget']}",
        f"- 运行范围: {summary['run_scope']}",
        f"- 运行状态: {summary['run_status']}",
        f"- 可配对比较: {summary['comparison_eligible']}"
        + (f"（阻断: {', '.join(summary['comparison_blockers'])}）" if summary["comparison_blockers"] else ""),
        "",
        "## 五项主指标（并列，不加权汇总）",
        "",
    ]
    headline = summary["headline"]
    if headline is None:
        lines.append("运行未完成，主指标为空。")
    else:
        k, budget = headline["primary_k"], headline["primary_budget"]
        lines.extend([
            "| 指标 | 值 | 95% CI |",
            "|---|---:|---:|",
            f"| Query Macro Claim Recall@{k} | {headline['query_macro_claim_recall']} | {headline['query_macro_claim_recall_ci95']} |",
            f"| Complete Evidence Chain Rate@{k} | {headline['complete_evidence_chain_rate']} | {headline['complete_evidence_chain_rate_ci95']} |",
            f"| Budget Claim Recall@{budget}字 (分段中立) | {headline['budget_claim_recall']} | {headline['budget_claim_recall_ci95']} |",
            f"| Abstention AUC (拒答分离度) | {headline['abstention_auc']} | {headline['abstention_auc_ci95']} |",
            f"| Bridge Claim Recall@{k} (多跳桥接) | {headline['bridge_claim_recall']} | {headline['bridge_claim_recall_ci95']} |",
            "",
            f"> 推断单元: {headline['inference_unit']}。{headline['inference_warning']}",
        ])
    sanity = summary["sanity"]
    abstention = summary["abstention"]
    multihop = summary["multihop"]
    diagnostics = summary["diagnostics"]
    if multihop["chain_questions"]:
        gap = None
        if multihop["endpoint_claim_recall"] is not None:
            gap = round(multihop["endpoint_claim_recall"] - multihop["bridge_claim_recall"], 4)
        lines.extend([
            "",
            "## 多跳专区（实体桥接）",
            "",
            f"- 链式题数: {multihop['chain_questions']}",
            f"- 端点召回 / 桥接召回: {multihop['endpoint_claim_recall']} / {multihop['bridge_claim_recall']}"
            + (f"（落差 {gap}）" if gap is not None else ""),
            f"- 仅桥接缺失率: {multihop['bridge_only_miss_rate']}"
            "（两端都找到、连接材料没找到——多跳失败的标志性形态）",
            f"- 路径状态分布: {multihop['path_status']}",
            "",
            "| Chain | 题数 | 端点召回 | 桥接召回 | 整体召回 |",
            "|---|---:|---:|---:|---:|",
        ])
        for chain_id, values in multihop["by_chain"].items():
            lines.append(
                f"| {chain_id} | {values['count']} | {values['endpoint_claim_recall']} | "
                f"{values['bridge_claim_recall']} | {values['claim_recall']} |"
            )
        lines.append("")
        if gap is None:
            note = "> 桥接召回不可用。"
        elif gap < 0.1:
            note = (
                "> ⚠️ 端点召回与桥接召回接近，说明桥接文档本来就能被题面直接召回，"
                "这一跳没有真正发生——应回到考卷侧检查名称隔离与 G13。"
            )
        else:
            note = (
                f"> 端点召回显著高于桥接召回（落差 {gap}），说明多跳设计成立："
                "检索器能找到两端，但接不上中间那份材料。"
            )
        lines.append(note)
    lines.extend([
        "",
        "## 哨兵桶（不进主指标）",
        "",
        f"- 通过率: {sanity['pass_rate']}（{sanity['evaluated']} 题）",
        f"- 未通过题号: {sanity['failed_question_ids'] or '无'}",
        "",
        "## 拒答行为",
        "",
        f"- 可答题空响应率: {abstention['answerable_empty_response_rate']}",
        f"- 不可答题空响应率: {abstention['unanswerable_empty_response_rate']}",
        f"- 分离度不可用原因: {abstention['unavailable_reason'] or '无'}",
        "",
        "## 关键诊断",
        "",
        f"- 请求成功率: {diagnostics['request_success_rate']}",
        f"- 错误类型: {diagnostics['error_counts']}",
        f"- 未匹配文档名: {diagnostics['unmatched_document_names'] or '无'}",
        f"- Claim 微平均召回: {diagnostics['claim_micro_recall']}",
        f"- anchor / passage 子召回: {diagnostics['anchor_claim_recall']} / {diagnostics['passage_claim_recall']}",
        f"- 平均文档召回: {diagnostics['mean_document_recall']}",
        f"- 平均离题块率: {diagnostics['mean_off_target_chunk_rate']}",
        f"- 平均重复文档率: {diagnostics['mean_duplicate_document_rate']}",
        f"- 硬负例侵入率: {diagnostics['hard_negative_intrusion_rate']}",
        f"- 资产来源覆盖: {diagnostics['asset_source_coverage']}",
        f"- 响应深度 min/median/max: {diagnostics['response_depth']['min']} / "
        f"{diagnostics['response_depth']['median']} / {diagnostics['response_depth']['max']}",
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
        "## 字符预算曲线（分段中立视图）",
        "",
        "| 归一化字符预算 | Claim Recall |",
        "|---:|---:|",
    ])
    for budget, values in diagnostics["char_budget_curves"].items():
        lines.append(f"| {budget} | {values['query_macro_claim_recall']} |")
    lines.extend([
        "",
        "## 逐题结果",
        "",
        "| ID | 角色 | 类型 | Cluster | 状态 | Recall | Chain | Budget |",
        "|---|---|---|---|---|---:|---:|---:|",
    ])
    primary_key = str(protocol["primary_k"])
    budget_key = str(protocol["primary_budget"])
    for result in results:
        metrics = (result.get("metrics") or {}).get("metrics_by_k", {}).get(primary_key)
        budget_metrics = (result.get("metrics") or {}).get("char_budget_metrics", {}).get(budget_key)
        lines.append(
            f"| {result['id']} | {result['question_role']} | {result['primary_type']} | "
            f"{result['cluster_id']} | {result['status']} | "
            f"{metrics['claim_recall'] if metrics else '-'} | "
            f"{metrics['complete_evidence_chain'] if metrics else '-'} | "
            f"{budget_metrics['claim_recall'] if budget_metrics else '-'} |"
        )
    if summary["run_scope"] == "smoke":
        lines.extend(["", "> ⚠️ 本次为 --limit 冒烟运行，不可与完整考试直接比较。"])
    if manifest["corpus_drift"]:
        lines.extend(["", "> ⚠️ 本地语料哈希漂移，本结果不可用于正式比较。"])
    if diagnostics["unmatched_document_names"]:
        lines.extend([
            "",
            "> ⚠️ 存在未匹配的文档名，部分 claim 可能因文档身份对不上而被判未命中。",
        ])
    return "\n".join(lines) + "\n"


def write_outputs(
    out_root: Path, exam: Dict[str, Any], summary: Dict[str, Any],
    results: List[Dict[str, Any]], manifest: Dict[str, Any], timestamp: str,
) -> Path:
    out_dir = out_root / exam["exam_meta"]["exam_id"] / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "run_status": summary["run_status"],
        "run_scope": summary["run_scope"],
        "comparison_eligible": summary["comparison_eligible"],
        "comparison_blockers": summary["comparison_blockers"],
        "headline": summary["headline"],
        "sanity": summary["sanity"],
        "abstention": summary["abstention"],
        "multihop": summary["multihop"],
        "exam_meta": exam["exam_meta"],
        "manifest": manifest,
        "results": results,
    }
    for name, value in (
        (f"results_{timestamp}.json", payload),
        (f"summary_{timestamp}.json", summary),
        (f"manifest_{timestamp}.json", manifest),
    ):
        (out_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (out_dir / f"report_{timestamp}.md").write_text(
        build_report(exam, summary, manifest, results), encoding="utf-8"
    )
    return out_dir


# --------------------------------------------------------------------------
# N 路比较
# --------------------------------------------------------------------------

HEADLINE_PAIRED_FIELDS = ("claim_recall", "complete_evidence_chain", "budget_claim_recall")


def _run_labels(payloads: Sequence[Dict[str, Any]]) -> List[str]:
    """给每个运行生成唯一短标签，重名时补序号。"""
    labels = []
    for payload in payloads:
        manifest = payload["manifest"]
        labels.append(f"{manifest['backend_profile']}@{str(manifest['dataset_id'])[:8]}")
    seen: Counter[str] = Counter(labels)
    used: Counter[str] = Counter()
    unique = []
    for label in labels:
        if seen[label] > 1:
            used[label] += 1
            unique.append(f"{label}#{used[label]}")
        else:
            unique.append(label)
    return unique


def _paired_values(payload: Dict[str, Any], field_name: str) -> Dict[str, Optional[float]]:
    protocol = payload["manifest"]["retrieval_protocol"]
    primary_k = str(protocol["primary_k"])
    budget = str(protocol["primary_budget"])
    values: Dict[str, Optional[float]] = {}
    for result in payload["results"]:
        if result["question_role"] != "scored":
            continue
        if result["status"] != "ok" or not result.get("metrics"):
            values[result["id"]] = None
            continue
        if field_name == "budget_claim_recall":
            values[result["id"]] = float(result["metrics"]["char_budget_metrics"][budget]["claim_recall"])
        else:
            values[result["id"]] = float(result["metrics"]["metrics_by_k"][primary_k][field_name])
    return values


def compare_runs(paths: Sequence[Path], allow_config_diff: bool = False) -> Dict[str, Any]:
    if len(paths) < 2:
        raise ComparisonError("至少需要两个运行结果")
    payloads = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComparisonError(f"无法读取比较结果 {path}: {exc}") from exc
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ComparisonError(f"{path} 不是 v4 结果")
        if payload.get("run_status") != "completed" or payload.get("run_scope") != "full":
            raise ComparisonError(f"{path} 必须是 completed/full 运行")
        if not payload.get("comparison_eligible"):
            raise ComparisonError(
                f"{path} 标记为不可比较: {payload.get('comparison_blockers')}"
            )
        payload["__path"] = str(path)
        payloads.append(payload)

    base = payloads[0]["manifest"]
    for payload in payloads[1:]:
        manifest = payload["manifest"]
        for field_name in ("exam_id", "exam_sha256", "corpus", "retrieval_protocol"):
            if manifest.get(field_name) != base.get(field_name):
                raise ComparisonError(f"比较字段不一致: {field_name}")

    base_ids = [r["id"] for r in payloads[0]["results"] if r["question_role"] == "scored"]
    for payload in payloads[1:]:
        ids = [r["id"] for r in payload["results"] if r["question_role"] == "scored"]
        if ids != base_ids:
            raise ComparisonError("计分题 ID 或顺序不一致")

    # 跨产品比较时请求体天然不同，这是被测对象本身的差异，不构成阻断；
    # 真正会把结论带偏的是「同一产品、不同检索配置」——v3 就栽在这里
    # (score_threshold 一个 null 一个 0.55，77% 的差距来自配置而非能力)。
    profiles = [p["manifest"]["backend_profile"] for p in payloads]
    configs = [
        json.dumps(p["manifest"]["request_config"], ensure_ascii=False, sort_keys=True)
        for p in payloads
    ]
    by_profile: Dict[str, set] = defaultdict(set)
    for profile_name, config in zip(profiles, configs):
        by_profile[profile_name].add(config)
    conflicting = sorted(name for name, values in by_profile.items() if len(values) > 1)
    if conflicting and not allow_config_diff:
        raise ComparisonError(
            f"同一后端 {conflicting} 的多次运行检索配置不一致，差异会被误读成检索能力差距。"
            " 确认要继续请加 --allow-config-diff。配置摘要: "
            + " | ".join(sorted(set(configs)))
        )
    config_diff = len(set(configs)) > 1
    profiles_differ = len(set(profiles)) > 1

    clusters = {
        r["id"]: r["cluster_id"]
        for r in payloads[0]["results"] if r["question_role"] == "scored"
    }
    per_field_values = {
        field_name: [_paired_values(p, field_name) for p in payloads]
        for field_name in HEADLINE_PAIRED_FIELDS
    }
    excluded = sorted({
        question_id
        for values_list in per_field_values.values()
        for values in values_list
        for question_id, value in values.items()
        if value is None
    })
    paired_ids = [qid for qid in base_ids if qid not in excluded]
    if not paired_ids:
        raise ComparisonError("没有在所有运行中都成功的计分题")

    labels = _run_labels(payloads)
    metrics: Dict[str, Any] = {}
    for offset, field_name in enumerate(HEADLINE_PAIRED_FIELDS):
        values_list = per_field_values[field_name]
        means = [round(_mean([values[qid] for qid in paired_ids]), 4) for values in values_list]
        pairwise = []
        raw_pvalues = []
        for left in range(len(payloads)):
            for right in range(left + 1, len(payloads)):
                deltas: Dict[str, List[float]] = defaultdict(list)
                for qid in paired_ids:
                    deltas[clusters[qid]].append(
                        values_list[right][qid] - values_list[left][qid]
                    )
                flat = [(cluster, value) for cluster, items in deltas.items() for value in items]
                pvalue = clustered_randomization_pvalue(dict(deltas), seed=BOOTSTRAP_SEED + offset)
                raw_pvalues.append(pvalue)
                pairwise.append({
                    "left": labels[left],
                    "right": labels[right],
                    "right_minus_left": round(_mean([v for _, v in flat]), 4),
                    "paired_bootstrap_ci95": clustered_bootstrap_ci(
                        flat, seed=BOOTSTRAP_SEED + offset
                    ),
                    "paired_randomization_pvalue": pvalue,
                })
        for item, adjusted in zip(pairwise, holm_adjust(raw_pvalues)):
            item["holm_adjusted_pvalue"] = adjusted
        metrics[field_name] = {
            "run_means": dict(zip(labels, means)),
            "ranking": [
                labels[index] for index in sorted(
                    range(len(means)), key=lambda i: means[i], reverse=True
                )
            ],
            "pairwise": pairwise,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
        "exam_id": base["exam_id"],
        "retrieval_protocol": base["retrieval_protocol"],
        "inference_unit": "cluster_id",
        "inference_warning": INFERENCE_WARNING,
        "runs": [
            {
                "label": label,
                "path": payload["__path"],
                "backend_profile": payload["manifest"]["backend_profile"],
                "dataset_id": payload["manifest"]["dataset_id"],
                "dataset_revision": payload["manifest"]["dataset_revision"],
                "evaluator_script_sha256": payload["manifest"]["evaluator_script_sha256"],
                "abstention_auc": (payload.get("abstention") or {}).get("auc"),
                "sanity_pass_rate": (payload.get("sanity") or {}).get("pass_rate"),
            }
            for label, payload in zip(labels, payloads)
        ],
        "request_config_differs": config_diff,
        "backend_profiles_differ": profiles_differ,
        "same_profile_config_conflict": conflicting,
        "request_configs": {
            label: payload["manifest"]["request_config"]
            for label, payload in zip(labels, payloads)
        },
        "num_paired_questions": len(paired_ids),
        "excluded_questions": excluded,
        "metrics": metrics,
    }


def write_comparison(comparison: Dict[str, Any], out_dir: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_dir / comparison["exam_id"] / timestamp
    target.mkdir(parents=True, exist_ok=True)
    (target / f"comparison_{timestamp}.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# {comparison['exam_id']} 多路比较 (v4)",
        "",
        f"- 配对题数: {comparison['num_paired_questions']}",
        f"- 剔除题数: {len(comparison['excluded_questions'])} {comparison['excluded_questions'] or ''}",
        f"- 推断单元: {comparison['inference_unit']}",
        "",
        "## 参与比较的运行",
        "",
        "| 标签 | profile | dataset | revision | 哨兵通过率 | 拒答 AUC |",
        "|---|---|---|---|---:|---:|",
    ]
    for run in comparison["runs"]:
        lines.append(
            f"| {run['label']} | {run['backend_profile']} | {run['dataset_id']} | "
            f"{run['dataset_revision']} | {run['sanity_pass_rate']} | {run['abstention_auc']} |"
        )
    if comparison["same_profile_config_conflict"]:
        lines.extend([
            "",
            f"> 🚨 **同一后端 {comparison['same_profile_config_conflict']} 的多次运行检索配置不一致**，"
            "下表差异很可能主要来自配置而非检索能力，不可作为结论。",
        ])
    elif comparison["backend_profiles_differ"]:
        lines.extend([
            "",
            "> ⚠️ 参与比较的是不同产品，请求体本就不同（配置见 JSON 的 `request_configs`）。"
            "差异包含产品自带的检索策略，不是纯粹的排序能力差异；"
            "请同时看 `budget_claim_recall`（对分段中立）再下结论。",
        ])
    for field_name, block in comparison["metrics"].items():
        lines.extend([
            "",
            f"## {field_name}",
            "",
            "| 运行 | 均值 |",
            "|---|---:|",
        ])
        for label, value in block["run_means"].items():
            lines.append(f"| {label} | {value} |")
        lines.extend([
            "",
            "| Left | Right | Right-Left | 95% CI | p | Holm p |",
            "|---|---|---:|---:|---:|---:|",
        ])
        for item in block["pairwise"]:
            lines.append(
                f"| {item['left']} | {item['right']} | {item['right_minus_left']} | "
                f"{item['paired_bootstrap_ci95']} | {item['paired_randomization_pvalue']} | "
                f"{item['holm_adjusted_pvalue']} |"
            )
    lines.extend(["", f"> {comparison['inference_warning']}"])
    (target / f"comparison_{timestamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

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
        raise argparse.ArgumentTypeError("必须是正数")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负数")
    return parsed


def int_list(value: str) -> List[int]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    if not items:
        raise argparse.ArgumentTypeError("列表不能为空")
    return [positive_int(item) for item in items]


def resolve_protocol(
    exam_protocol: Dict[str, Any], args: argparse.Namespace
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """考卷声明为权威值；CLI 显式指定时覆盖并记录来源。"""
    protocol = dict(exam_protocol)
    source = {key: "exam" for key in protocol}
    overrides = {
        "primary_k": args.primary_k,
        "request_k": args.request_k,
        "primary_budget": args.primary_budget,
        "eval_ks": args.eval_k,
        "char_budgets": args.char_budgets,
    }
    for key, value in overrides.items():
        if value is None:
            continue
        normalized = sorted(set(value)) if isinstance(value, list) else value
        if normalized != protocol[key]:
            sys.stderr.write(
                f"[警告] {key} 被命令行覆盖: 考卷={protocol[key]} → 运行={normalized}；"
                "与其他运行比较时口径必须一致。\n"
            )
            source[key] = "cli_override"
        protocol[key] = normalized
    if protocol["primary_k"] not in protocol["eval_ks"]:
        raise ExamValidationError("primary_k 必须包含在 eval_ks 中")
    if max(protocol["eval_ks"]) > protocol["request_k"]:
        raise ExamValidationError("request_k 不能小于 eval_ks 最大值")
    if protocol["primary_budget"] not in protocol["char_budgets"]:
        raise ExamValidationError("primary_budget 必须包含在 char_budgets 中")
    return protocol, source


def load_alias_map(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    candidate = Path(path).expanduser()
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExamValidationError(f"无法读取文档别名表 {candidate}: {exc}") from exc
    if not isinstance(raw, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in raw.items()
    ):
        raise ExamValidationError("文档别名表必须是 string → string 的 object")
    return raw


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 检索评测脚本 v4.0")
    parser.add_argument("--exam-dir", default=str(DEFAULT_EXAM_DIR))
    parser.add_argument("--exam", default=None)
    parser.add_argument(
        "--backend-profile", default=DEFAULT_PROFILE,
        help="后端 profile 名称或 JSON 路径；内置见 评测脚本/后端配置/",
    )
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--dataset-revision", default=None)
    parser.add_argument("--document-alias-map", default=None)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--primary-k", type=positive_int, default=None)
    parser.add_argument("--request-k", type=positive_int, default=None)
    parser.add_argument("--primary-budget", type=positive_int, default=None)
    parser.add_argument("--eval-k", type=int_list, default=None)
    parser.add_argument("--char-budgets", type=int_list, default=None)
    parser.add_argument("--limit", type=nonnegative_int, default=0)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--sleep", type=nonnegative_float, default=0.5)
    parser.add_argument("--timeout", type=positive_float, default=30.0)
    parser.add_argument("--retries", type=positive_int, default=3)
    parser.add_argument("--raw-content-limit", type=positive_int, default=CONTENT_TRUNCATE)
    parser.add_argument("--allow-corpus-drift", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--compare", nargs="+", metavar="RUN")
    parser.add_argument("--allow-config-diff", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.compare:
        try:
            comparison = compare_runs(
                [Path(item) for item in args.compare], args.allow_config_diff
            )
            out_root = (
                Path(args.out_dir).expanduser() if args.out_dir
                else RESULTS_ROOT / "考试结果-v4-比较"
            )
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
                f"[校验通过] {path}: {len(exam['questions'])} 题 "
                f"(scored={validation['role_counts'].get('scored', 0)}, "
                f"sanity={validation['role_counts'].get('sanity', 0)}, "
                f"unanswerable={validation['role_counts'].get('unanswerable', 0)}), "
                f"{validation['total_claims']} claims, "
                f"clusters={len(validation['cluster_question_counts'])}, "
                f"corpus_drift={validation['corpus_drift']}"
            )
            for warning in validation["warnings"]:
                print(f"  [警告] {warning}")
        return 0

    if not args.dataset_id:
        sys.stderr.write("[校验失败] 必须显式提供 --dataset-id\n")
        return 2

    try:
        profile = load_profile(args.backend_profile)
        alias_map = load_alias_map(args.document_alias_map)
        headers = build_headers(profile)
    except ProfileError as exc:
        sys.stderr.write(f"[配置错误] {exc}\n")
        return 2
    except ExamValidationError as exc:
        sys.stderr.write(f"[校验失败] {exc}\n")
        return 2
    except AuthenticationError as exc:
        sys.stderr.write(f"[错误] {exc}\n")
        return 3

    if not profile.supports_request_k:
        print(
            f"[警告] profile {profile.name} 不支持请求侧 K；request_k 仅作为目标深度记录，"
            "实际以响应返回数量为准。"
        )

    session = requests.Session()
    session.headers.update(headers)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = (
        Path(args.out_dir).expanduser() if args.out_dir
        else RESULTS_ROOT / f"考试结果-v4-{profile.name}"
    )

    for path, exam, validation in loaded:
        try:
            protocol, protocol_source = resolve_protocol(validation["protocol"], args)
        except ExamValidationError as exc:
            sys.stderr.write(f"[校验失败] {exc}\n")
            return 2
        run_scope = "smoke" if args.limit else "full"
        manifest = build_manifest(
            exam, validation, profile, args, protocol, protocol_source,
            timestamp, run_scope, alias_map,
        )
        unmatched: List[str] = []
        try:
            results, unmatched = evaluate_exam(
                exam, validation, session, profile, args, protocol, alias_map
            )
            run_status = "completed"
        except DocumentNameMismatch as exc:
            sys.stderr.write(f"[文档名不匹配] {exc}\n")
            return 4
        except EvaluationAborted as exc:
            results, run_status = exc.results, exc.status
        comparability = {
            "dataset_revision_verified": bool(args.dataset_revision),
            "git_dirty": bool(manifest["git"].get("dirty")),
        }
        summary = aggregate(
            results, protocol, run_status, run_scope,
            validation["corpus_drift"], comparability, unmatched,
        )
        out_dir = write_outputs(out_root, exam, summary, results, manifest, timestamp)
        print(f"结果已输出到: {out_dir}")
        if run_status == "aborted_auth":
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
