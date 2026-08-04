# -*- coding: utf-8 -*-
"""实体桥接型跨文档多跳检索专项评测。

本脚本复用 retrieval_eval.py 的 aRAG/Dify 请求、响应归一化和基础
Claim 指标，只增加端点、桥接、辅助分支和图片引用的专项校验与聚合。
它评估的是多跳回答所需证据能否闭环，不直接评分最终答案或推理过程。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


CORE_PATH = Path(__file__).with_name("retrieval_eval.py")
CORE_SPEC = importlib.util.spec_from_file_location("entity_bridge_retrieval_core", CORE_PATH)
if CORE_SPEC is None or CORE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载基础评测器: {CORE_PATH}")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)


SPECIALIZED_METRICS_VERSION = "entity-bridge-2.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAM_DIR = PROJECT_ROOT / "评测考试" / "考卷-多跳专项"
RESULTS_ROOT = PROJECT_ROOT / "评测考试"
ALLOWED_HOP_ROLES = {"endpoint", "bridge", "supporting"}
IMAGE_REFERENCE_METRIC_FIELDS = (
    "image_reference_claim_recall",
    "complete_image_reference_chain",
)
EVALUATOR_VERSION_FIELDS = (
    "specialized_metrics_version",
    "core_metrics_version",
)
EVALUATOR_HASH_FIELDS = (
    "specialized_script_sha256",
    "core_script_sha256",
)
ALLOWED_FOCUS = {
    "relation_path",
    "boundary_path",
    "temporal_path",
    "governance_path",
    "workflow_path",
    "evidence_path",
    "hard_negative_path",
    "asset_path",
}


class BridgeExamValidationError(core.ExamValidationError):
    """实体桥接专项考卷不符合约定。"""


class BridgeComparisonError(ValueError):
    """两次专项运行不可配对比较。"""


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeExamValidationError(f"{label} 必须是非空字符串")
    return value.strip()


def _require_string_list(value: Any, label: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise BridgeExamValidationError(f"{label} 必须是非空字符串数组")
    values = [_require_string(item, f"{label}[]") for item in value]
    if len(values) != len(set(values)):
        raise BridgeExamValidationError(f"{label} 不能重复")
    return values


def _read_png_dimensions(path: Path) -> Tuple[int, int]:
    """校验 PNG 块结构与 CRC，并读取 IHDR 尺寸。"""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise BridgeExamValidationError(f"无法读取图片资产 {path}: {exc}") from exc
    if len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise BridgeExamValidationError(f"图片资产不是有效 PNG: {path}")
    offset = 8
    dimensions: Optional[Tuple[int, int]] = None
    saw_idat = False
    saw_iend = False
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(payload):
            raise BridgeExamValidationError(f"PNG 数据块截断: {path}")
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_data = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise BridgeExamValidationError(f"PNG 数据块 CRC 错误: {path}")
        if offset == 8:
            if chunk_type != b"IHDR" or length != 13:
                raise BridgeExamValidationError(f"PNG 首块不是合法 IHDR: {path}")
            dimensions = struct.unpack(">II", chunk_data[:8])
        elif chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            if length != 0:
                raise BridgeExamValidationError(f"PNG IEND 非空: {path}")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end
    if (
        dimensions is None
        or not saw_idat
        or not saw_iend
        or offset != len(payload)
    ):
        raise BridgeExamValidationError(f"PNG 缺少必要数据块或存在尾随数据: {path}")
    return dimensions


def _validate_image_assets(
    corpus: Dict[str, Any],
    corpus_dir: Path,
    allow_corpus_drift: bool,
) -> Tuple[set[str], bool]:
    """校验图片文件清单、哈希和尺寸，并拒绝未纳入快照的 PNG。"""
    assets = corpus.get("image_assets")
    if not isinstance(assets, list) or not assets:
        raise BridgeExamValidationError("corpus.image_assets 必须是非空数组")

    declared: set[str] = set()
    image_drift = False
    for index, item in enumerate(assets):
        label = f"corpus.image_assets[{index}]"
        if not isinstance(item, dict):
            raise BridgeExamValidationError(f"{label} 必须是 object")
        name = _require_string(item.get("name"), f"{label}.name")
        relative_path = Path(name)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.suffix.lower() != ".png"
        ):
            raise BridgeExamValidationError(f"{label}.name 必须是语料目录内 PNG 相对路径")
        if name in declared:
            raise BridgeExamValidationError(f"图片资产重复声明: {name}")
        declared.add(name)
        path = corpus_dir / relative_path
        if not path.is_file():
            raise BridgeExamValidationError(f"图片资产不存在: {path}")
        expected_hash = _require_string(item.get("sha256"), f"{label}.sha256")
        if core.sha256_file(path) != expected_hash:
            image_drift = True
        width = item.get("width")
        height = item.get("height")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or width <= 0
            or not isinstance(height, int)
            or isinstance(height, bool)
            or height <= 0
        ):
            raise BridgeExamValidationError(f"{label}.width/height 必须是正整数")
        if _read_png_dimensions(path) != (width, height):
            image_drift = True
        if item.get("media_type") != "image/png":
            raise BridgeExamValidationError(f"{label}.media_type 必须为 image/png")

    actual = {
        path.relative_to(corpus_dir).as_posix()
        for path in corpus_dir.rglob("*.png")
        if path.is_file()
    }
    if actual != declared:
        raise BridgeExamValidationError(
            "PNG 快照清单与磁盘不一致: "
            f"undeclared={sorted(actual - declared)}, missing={sorted(declared - actual)}"
        )
    if image_drift and not allow_corpus_drift:
        raise BridgeExamValidationError(
            "图片资产 SHA-256 或尺寸漂移；使用 --allow-corpus-drift 可仅作诊断运行"
        )
    return declared, image_drift


def validate_entity_bridge_design(
    exam: Dict[str, Any], allow_corpus_drift: bool = False
) -> Dict[str, Any]:
    """校验专项字段、三文档最小链和桥接文档人名隔离。"""
    meta = exam["exam_meta"]
    design = meta.get("entity_bridge_design")
    if not isinstance(design, dict):
        raise BridgeExamValidationError("exam_meta.entity_bridge_design 必须是 object")
    if design.get("benchmark_kind") != "entity_bridge_multihop":
        raise BridgeExamValidationError(
            "entity_bridge_design.benchmark_kind 必须为 entity_bridge_multihop"
        )

    declared_chains = design.get("chains")
    if not isinstance(declared_chains, list) or not declared_chains:
        raise BridgeExamValidationError("entity_bridge_design.chains 必须是非空数组")
    chain_relations: Dict[str, str] = {}
    chain_bridge_entities: Dict[str, List[str]] = {}
    for item in declared_chains:
        if not isinstance(item, dict):
            raise BridgeExamValidationError("entity_bridge_design.chains 存在非法项")
        chain_id = _require_string(item.get("id"), "entity_bridge_design.chains[].id")
        relation_type = _require_string(
            item.get("relation_type"),
            f"entity_bridge_design.chains[{chain_id}].relation_type",
        )
        if chain_id in chain_relations:
            raise BridgeExamValidationError("entity_bridge_design.chains 存在重复 ID")
        chain_relations[chain_id] = relation_type
        chain_bridge_entities[chain_id] = _require_string_list(
            item.get("bridge_entities"),
            f"entity_bridge_design.chains[{chain_id}].bridge_entities",
        )
    chain_ids = set(chain_relations)

    corpus = meta["corpus"]
    corpus_dir = core.PROJECT_ROOT / corpus["relative_dir"]
    declared_docs = {item["name"] for item in corpus["documents"]}
    corpus_text = {
        name: (corpus_dir / name).read_text(encoding="utf-8") for name in declared_docs
    }
    declared_image_assets, image_drift = _validate_image_assets(
        corpus, corpus_dir, allow_corpus_drift
    )

    focus_counts: Counter[str] = Counter()
    chain_counts: Counter[str] = Counter()
    image_reference_questions = 0
    bridge_claims = 0
    endpoint_claims = 0
    hard_negative_chain_counts: Counter[str] = Counter()

    for question in exam["questions"]:
        question_id = question["id"]
        if not question["scored"]:
            raise BridgeExamValidationError(f"{question_id} 专项考卷不接受非计分题")
        chain_id = _require_string(question.get("chain_id"), f"{question_id}.chain_id")
        if chain_id not in chain_ids:
            raise BridgeExamValidationError(f"{question_id}.chain_id 未在元数据声明")
        relation_type = _require_string(
            question.get("relation_type"), f"{question_id}.relation_type"
        )
        if relation_type not in {"equity_control", "service_contract"}:
            raise BridgeExamValidationError(f"{question_id}.relation_type 不合法")
        if relation_type != chain_relations[chain_id]:
            raise BridgeExamValidationError(
                f"{question_id}.relation_type 与链 {chain_id} 的声明不一致"
            )
        focus = question.get("evaluation_focus")
        if focus not in ALLOWED_FOCUS:
            raise BridgeExamValidationError(f"{question_id}.evaluation_focus 不合法")

        entities = _require_string_list(
            question.get("endpoint_entities"), f"{question_id}.endpoint_entities"
        )
        if len(entities) != 2:
            raise BridgeExamValidationError(
                f"{question_id}.endpoint_entities 必须恰好包含两个端点实体"
            )
        for entity in entities:
            if entity not in question["question"]:
                raise BridgeExamValidationError(
                    f"{question_id}.question 必须显式包含端点实体 {entity}"
                )

        endpoint_docs = _require_string_list(
            question.get("endpoint_documents"), f"{question_id}.endpoint_documents"
        )
        bridge_docs = _require_string_list(
            question.get("bridge_documents"), f"{question_id}.bridge_documents"
        )
        supporting_docs = question.get("supporting_documents", [])
        if not isinstance(supporting_docs, list) or any(
            not isinstance(item, str) or not item.strip() for item in supporting_docs
        ):
            raise BridgeExamValidationError(
                f"{question_id}.supporting_documents 必须是字符串数组"
            )
        if len(endpoint_docs) != 2:
            raise BridgeExamValidationError(
                f"{question_id}.endpoint_documents 必须恰好包含两个文档"
            )
        role_docs = set(endpoint_docs) | set(bridge_docs) | set(supporting_docs)
        if len(role_docs) != len(endpoint_docs) + len(bridge_docs) + len(supporting_docs):
            raise BridgeExamValidationError(f"{question_id} 的文档角色不能重叠")
        unknown_docs = role_docs - declared_docs
        if unknown_docs:
            raise BridgeExamValidationError(
                f"{question_id} 引用了未声明文档: {sorted(unknown_docs)}"
            )
        for bridge_doc in bridge_docs:
            leaked = [entity for entity in entities if entity in corpus_text[bridge_doc]]
            if leaked:
                raise BridgeExamValidationError(
                    f"{question_id} 桥接文档 {bridge_doc} 泄露端点实体: {leaked}"
                )

        if question["primary_type"] == "disambiguation_hard_negative":
            negatives = question.get("negative_evidence", [])
            if not negatives:
                raise BridgeExamValidationError(
                    f"{question_id} hard-negative 题必须声明 negative_evidence"
                )
            shared_entities = chain_bridge_entities[chain_id]
            if not any(
                entity in span
                for negative in negatives
                for span in negative["accepted_spans"]
                for entity in shared_entities
            ):
                raise BridgeExamValidationError(
                    f"{question_id} 的负例必须在证据 span 中共享至少一个核心机构实体"
                )
            hard_negative_chain_counts[chain_id] += 1

        claim_docs: set[str] = set()
        text_docs: set[str] = set()
        asset_docs: set[str] = set()
        roles_seen: Counter[str] = Counter()
        for claim in question["claims"]:
            role = claim.get("hop_role")
            if role not in ALLOWED_HOP_ROLES:
                raise BridgeExamValidationError(
                    f"{question_id}.{claim['id']}.hop_role 必须是 "
                    "endpoint/bridge/supporting"
                )
            source = claim["source_document"]
            expected_role = (
                "endpoint"
                if source in endpoint_docs
                else "bridge"
                if source in bridge_docs
                else "supporting"
                if source in supporting_docs
                else None
            )
            if role != expected_role:
                raise BridgeExamValidationError(
                    f"{question_id}.{claim['id']} 的 hop_role={role} "
                    f"与文档角色 {expected_role} 不一致"
                )
            claim_docs.add(source)
            roles_seen[role] += 1
            if claim["kind"] == "text":
                text_docs.add(source)
            else:
                asset_docs.add(source)
                spans = claim["accepted_spans"]
                if len(spans) != 1 or spans[0] not in declared_image_assets:
                    raise BridgeExamValidationError(
                        f"{question_id}.{claim['id']} 必须精确引用一个已声明图片资产"
                    )

        required_docs = set(endpoint_docs) | set(bridge_docs)
        if len(claim_docs) < 3 or not required_docs.issubset(claim_docs):
            raise BridgeExamValidationError(
                f"{question_id} 必须用 Claim 覆盖两个端点文档和全部桥接文档"
            )
        if question["primary_type"] == "asset_reference_retrieval":
            image_reference_questions += 1
            if text_docs or not role_docs.issubset(asset_docs):
                raise BridgeExamValidationError(
                    f"{question_id} 资产题必须用 asset Claim 覆盖全部链路文档"
                )
        else:
            if not role_docs.issubset(text_docs):
                raise BridgeExamValidationError(
                    f"{question_id} 文本题必须用 text Claim 覆盖端点、桥接和 supporting 文档"
                )

        if roles_seen["endpoint"] < 2 or roles_seen["bridge"] < 1:
            raise BridgeExamValidationError(
                f"{question_id} 至少需要两个端点 Claim 和一个桥接 Claim"
            )

        focus_counts[focus] += 1
        chain_counts[chain_id] += 1
        endpoint_claims += roles_seen["endpoint"]
        bridge_claims += roles_seen["bridge"]

    expected_per_chain = int(design.get("questions_per_chain", 0))
    if expected_per_chain and any(
        chain_counts[chain_id] != expected_per_chain for chain_id in chain_ids
    ):
        raise BridgeExamValidationError(
            f"每条链题数必须为 {expected_per_chain}: {dict(chain_counts)}"
        )
    minimum_image_reference_questions = int(
        design.get("min_image_reference_questions", 0)
    )
    if image_reference_questions < minimum_image_reference_questions:
        raise BridgeExamValidationError(
            "图片引用题数 "
            f"{image_reference_questions} 低于 {minimum_image_reference_questions}"
        )
    if design.get("hard_negative_policy") and any(
        hard_negative_chain_counts[chain_id] < 1 for chain_id in chain_ids
    ):
        raise BridgeExamValidationError(
            "每条实体链至少需要一道共享核心机构实体的 hard-negative 题"
        )
    return {
        "chain_counts": dict(sorted(chain_counts.items())),
        "focus_counts": dict(sorted(focus_counts.items())),
        "image_reference_questions": image_reference_questions,
        "image_asset_count": len(declared_image_assets),
        "image_drift": image_drift,
        "hard_negative_chain_counts": dict(
            sorted(hard_negative_chain_counts.items())
        ),
        "endpoint_claims": endpoint_claims,
        "bridge_claims": bridge_claims,
    }


def load_and_validate_exam(
    path: Path, allow_corpus_drift: bool = False
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """运行基础 v3 校验，再运行实体桥接专项校验。"""
    exam, validation = core.load_and_validate_exam(path, allow_corpus_drift)
    validation = dict(validation)
    validation["entity_bridge"] = validate_entity_bridge_design(
        exam, allow_corpus_drift
    )
    return exam, validation


def _recall(details: Sequence[Dict[str, Any]]) -> Optional[float]:
    if not details:
        return None
    return round(sum(bool(item["hit"]) for item in details) / len(details), 4)


def compute_bridge_metrics_at_k(
    question: Dict[str, Any], base_metrics: Dict[str, Any]
) -> Dict[str, Any]:
    """基于 v3 Claim 明细计算核心桥、完整声明链和图片引用指标。"""
    claims = {claim["id"]: claim for claim in question["claims"]}
    details = []
    for detail in base_metrics["claim_detail"]:
        claim = claims[detail["claim_id"]]
        details.append(
            {
                **detail,
                "hop_role": claim["hop_role"],
                "kind": claim["kind"],
            }
        )

    endpoint_text = [
        item for item in details if item["hop_role"] == "endpoint" and item["kind"] == "text"
    ]
    bridge_text = [
        item for item in details if item["hop_role"] == "bridge" and item["kind"] == "text"
    ]
    supporting_text = [
        item
        for item in details
        if item["hop_role"] == "supporting" and item["kind"] == "text"
    ]
    image_reference_details = [item for item in details if item["kind"] == "asset"]
    core_required_text = endpoint_text + bridge_text
    declared_required_text = core_required_text + supporting_text

    endpoint_docs = set(question["endpoint_documents"])
    bridge_docs = set(question["bridge_documents"])
    retrieved_docs = set(base_metrics["retrieved_documents"])
    endpoint_doc_recall = len(endpoint_docs & retrieved_docs) / len(endpoint_docs)
    bridge_doc_recall = len(bridge_docs & retrieved_docs) / len(bridge_docs)

    endpoint_complete = bool(endpoint_text) and all(item["hit"] for item in endpoint_text)
    bridge_complete = bool(bridge_text) and all(item["hit"] for item in bridge_text)
    supporting_complete = (
        all(item["hit"] for item in supporting_text) if supporting_text else True
    )
    complete_core_bridge_chain = (
        bool(core_required_text) and endpoint_complete and bridge_complete
    )
    complete_declared_text_chain = (
        bool(declared_required_text)
        and complete_core_bridge_chain
        and supporting_complete
    )
    if not declared_required_text:
        path_status = (
            "image_reference_complete"
            if image_reference_details
            and all(item["hit"] for item in image_reference_details)
            else "image_reference_missing"
        )
    elif complete_declared_text_chain:
        path_status = "complete"
    elif complete_core_bridge_chain and not supporting_complete:
        path_status = "supporting_missing"
    elif endpoint_complete and not bridge_complete:
        path_status = "bridge_missing"
    elif bridge_complete and not endpoint_complete:
        path_status = "endpoint_missing"
    else:
        path_status = "multiple_missing"

    return {
        "endpoint_text_claim_recall": _recall(endpoint_text),
        "bridge_text_claim_recall": _recall(bridge_text),
        "supporting_text_claim_recall": _recall(supporting_text),
        "complete_core_bridge_chain": (
            complete_core_bridge_chain if core_required_text else None
        ),
        "complete_declared_text_chain": (
            complete_declared_text_chain if declared_required_text else None
        ),
        "endpoint_document_recall": round(endpoint_doc_recall, 4),
        "bridge_document_recall": round(bridge_doc_recall, 4),
        "bridge_only_document_miss": (
            endpoint_doc_recall == 1.0 and bridge_doc_recall < 1.0
        ),
        "image_reference_claim_recall": _recall(image_reference_details),
        "complete_image_reference_chain": (
            all(item["hit"] for item in image_reference_details)
            if image_reference_details
            else None
        ),
        "path_status": path_status,
        "hop_detail": details,
    }


def augment_results(
    exam: Dict[str, Any], results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """给基础逐题结果附加专项元数据和各 K 指标。"""
    questions = {question["id"]: question for question in exam["questions"]}
    for result in results:
        question = questions[result["id"]]
        result.update(
            {
                "chain_id": question["chain_id"],
                "relation_type": question["relation_type"],
                "evaluation_focus": question["evaluation_focus"],
                "endpoint_entities": question["endpoint_entities"],
                "endpoint_documents": question["endpoint_documents"],
                "bridge_documents": question["bridge_documents"],
                "supporting_documents": question.get("supporting_documents", []),
            }
        )
        metrics = result.get("metrics")
        if not metrics:
            continue
        for by_k in metrics["metrics_by_k"].values():
            by_k["entity_bridge"] = compute_bridge_metrics_at_k(question, by_k)
        counterfactual = result.get("relevance_counterfactual_metrics")
        if counterfactual:
            for by_k in counterfactual["metrics_by_k"].values():
                by_k["entity_bridge"] = compute_bridge_metrics_at_k(question, by_k)
    return results


def _bridge_metric(
    result: Dict[str, Any], k: int, field: str
) -> Optional[float]:
    value = (
        result.get("metrics", {})
        .get("metrics_by_k", {})
        .get(str(k), {})
        .get("entity_bridge", {})
        .get(field)
    )
    if value is None:
        return None
    return float(value)


def _bridge_metric_or_zero(result: Dict[str, Any], k: int, field: str) -> float:
    value = _bridge_metric(result, k, field)
    return value if value is not None else 0.0


def _mean_optional(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 4) if present else None


def _bridge_group(
    results: Sequence[Dict[str, Any]], primary_k: int
) -> Dict[str, Any]:
    text_results = [
        item for item in results if item["primary_type"] != "asset_reference_retrieval"
    ]
    image_reference_results = [
        item for item in results if item["primary_type"] == "asset_reference_retrieval"
    ]
    supporting_results = [
        item for item in text_results if item.get("supporting_documents")
    ]
    return {
        "count": len(results),
        "text_question_count": len(text_results),
        "image_reference_question_count": len(image_reference_results),
        "supporting_text_question_count": len(supporting_results),
        "endpoint_text_claim_recall": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "endpoint_text_claim_recall")
            for item in text_results
        ),
        "bridge_text_claim_recall": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "bridge_text_claim_recall")
            for item in text_results
        ),
        "supporting_text_claim_recall": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "supporting_text_claim_recall")
            for item in supporting_results
        ),
        "complete_core_bridge_chain_rate": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "complete_core_bridge_chain")
            for item in text_results
        ),
        "complete_declared_text_chain_rate": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "complete_declared_text_chain")
            for item in text_results
        ),
        "image_reference_claim_recall": _mean_optional(
            _bridge_metric_or_zero(item, primary_k, "image_reference_claim_recall")
            for item in image_reference_results
        ),
        "complete_image_reference_chain_rate": _mean_optional(
            _bridge_metric_or_zero(
                item, primary_k, "complete_image_reference_chain"
            )
            for item in image_reference_results
        ),
    }


def _chain_macro(by_chain: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """对链内题目先求均值，再对独立链等权聚合。"""
    fields = (
        "endpoint_text_claim_recall",
        "bridge_text_claim_recall",
        "supporting_text_claim_recall",
        "complete_core_bridge_chain_rate",
        "complete_declared_text_chain_rate",
        "image_reference_claim_recall",
        "complete_image_reference_chain_rate",
    )
    result: Dict[str, Any] = {"independent_chain_count": len(by_chain)}
    for offset, field in enumerate(fields):
        values = [
            value
            for group in by_chain.values()
            if (value := group.get(field)) is not None
        ]
        result[f"{field}_chain_count"] = len(values)
        result[field] = _mean_optional(values)
        result[f"{field}_ci95"] = (
            core.bootstrap_ci(values, seed=core.BOOTSTRAP_SEED + 100 + offset)
            if values
            else None
        )
    return result


def aggregate_bridge(
    results: List[Dict[str, Any]],
    base_summary: Dict[str, Any],
    primary_k: int,
    eval_ks: Sequence[int],
) -> Dict[str, Any]:
    """聚合专项主指标，同时保留完整 v3 基础汇总。"""
    completed = base_summary["run_status"] == "completed"
    text_results = [
        item for item in results if item["primary_type"] != "asset_reference_retrieval"
    ]
    image_reference_results = [
        item for item in results if item["primary_type"] == "asset_reference_retrieval"
    ]
    chain_ids = sorted({item["chain_id"] for item in results})
    by_chain = {
        chain_id: _bridge_group(
            [item for item in results if item["chain_id"] == chain_id],
            primary_k,
        )
        for chain_id in chain_ids
    }
    question_macro = _bridge_group(results, primary_k)
    chain_macro = _chain_macro(by_chain)
    for values in (question_macro, chain_macro):
        endpoint_recall = values["endpoint_text_claim_recall"]
        bridge_recall = values["bridge_text_claim_recall"]
        values["endpoint_minus_bridge_gap"] = (
            round(endpoint_recall - bridge_recall, 4)
            if endpoint_recall is not None and bridge_recall is not None
            else None
        )
    headline = None
    if completed:
        headline = {
            "primary_k": primary_k,
            "question_macro": question_macro,
            "chain_macro": chain_macro,
        }

    k_curves = {}
    for k in eval_ks:
        by_chain_at_k = {
            chain_id: _bridge_group(
                [item for item in results if item["chain_id"] == chain_id],
                k,
            )
            for chain_id in chain_ids
        }
        k_curves[str(k)] = {
            "question_macro": _bridge_group(results, k),
            "chain_macro": _chain_macro(by_chain_at_k),
        }

    path_status = Counter()
    for item in results:
        status = (
            item.get("metrics", {})
            .get("metrics_by_k", {})
            .get(str(primary_k), {})
            .get("entity_bridge", {})
            .get("path_status")
        )
        path_status[status or "request_error"] += 1

    focuses = sorted({item["evaluation_focus"] for item in results})
    return {
        "schema_version": core.SCHEMA_VERSION,
        "metrics_version": SPECIALIZED_METRICS_VERSION,
        "run_status": base_summary["run_status"],
        "run_scope": base_summary["run_scope"],
        "comparison_eligible": base_summary["comparison_eligible"],
        "headline": headline,
        "diagnostics": {
            "text_question_count": len(text_results),
            "image_reference_question_count": len(image_reference_results),
            "bridge_only_document_miss_rate": _mean_optional(
                _bridge_metric_or_zero(
                    item, primary_k, "bridge_only_document_miss"
                )
                for item in text_results
            ),
            "endpoint_document_recall": _mean_optional(
                _bridge_metric_or_zero(item, primary_k, "endpoint_document_recall")
                for item in text_results
            ),
            "bridge_document_recall": _mean_optional(
                _bridge_metric_or_zero(item, primary_k, "bridge_document_recall")
                for item in text_results
            ),
            "path_status_counts": dict(sorted(path_status.items())),
            "k_curves": k_curves,
            "independent_chain_count": len(chain_ids),
            "inference_warning": (
                "题目在 chain_id 内相关；显著性推断必须以链为聚类单位。"
            ),
            "by_chain": by_chain,
            "by_focus": {
                focus: _bridge_group(
                    [item for item in results if item["evaluation_focus"] == focus],
                    primary_k,
                )
                for focus in focuses
            },
            "base_v3_headline": base_summary["headline"],
            "base_v3_diagnostics": base_summary["diagnostics"],
        },
    }


def build_report(
    exam: Dict[str, Any],
    summary: Dict[str, Any],
    manifest: Dict[str, Any],
    results: Sequence[Dict[str, Any]],
) -> str:
    """生成实体桥接专项 Markdown 报告。"""
    lines = [
        f"# {exam['exam_meta']['title']} - 实体桥接专项报告",
        "",
        f"- 考卷: {exam['exam_meta']['exam_id']}",
        f"- 后端: {manifest['backend']}",
        f"- Dataset: {manifest['dataset_id']}",
        f"- Dataset Revision: {manifest['dataset_revision']}",
        f"- Dify Search Method: {manifest['dify_search_method']}",
        f"- GraphRAG: {manifest['graph_search']}",
        f"- 运行范围: {summary['run_scope']}",
        f"- 运行状态: {summary['run_status']}",
        f"- 可比较: {summary['comparison_eligible']}",
        "",
        "> 本报告衡量回答多跳问题所需的检索证据是否闭环；不直接评分最终答案、推理文字或引用忠实度。",
        "> 图片指标只衡量 Markdown 中图片引用路径字符串的召回；本地 PNG 文件另由考卷快照校验，不代表后端具备视觉理解能力。",
        "",
        "## 专项主指标",
        "",
    ]
    headline = summary["headline"]
    if headline is None:
        lines.append("运行未完整结束，专项主指标为空。")
    else:
        k = headline["primary_k"]
        question_macro = headline["question_macro"]
        chain_macro = headline["chain_macro"]
        lines.extend(
            [
                "| 指标 | 题目宏平均 | 链级宏平均 | 适用链数 | 链级 Bootstrap CI95 |",
                "|---|---:|---:|---:|---:|",
                f"| Endpoint Text Claim Recall@{k} | {question_macro['endpoint_text_claim_recall']} | {chain_macro['endpoint_text_claim_recall']} | {chain_macro['endpoint_text_claim_recall_chain_count']} | {chain_macro['endpoint_text_claim_recall_ci95']} |",
                f"| Bridge Text Claim Recall@{k} | {question_macro['bridge_text_claim_recall']} | {chain_macro['bridge_text_claim_recall']} | {chain_macro['bridge_text_claim_recall_chain_count']} | {chain_macro['bridge_text_claim_recall_ci95']} |",
                f"| Supporting Text Claim Recall@{k} | {question_macro['supporting_text_claim_recall']} | {chain_macro['supporting_text_claim_recall']} | {chain_macro['supporting_text_claim_recall_chain_count']} | {chain_macro['supporting_text_claim_recall_ci95']} |",
                f"| Endpoint-Bridge Gap@{k} | {question_macro['endpoint_minus_bridge_gap']} | {chain_macro['endpoint_minus_bridge_gap']} | {chain_macro['independent_chain_count']} | - |",
                f"| Complete Core Bridge Chain Rate@{k} | {question_macro['complete_core_bridge_chain_rate']} | {chain_macro['complete_core_bridge_chain_rate']} | {chain_macro['complete_core_bridge_chain_rate_chain_count']} | {chain_macro['complete_core_bridge_chain_rate_ci95']} |",
                f"| Complete Declared Text Chain Rate@{k} | {question_macro['complete_declared_text_chain_rate']} | {chain_macro['complete_declared_text_chain_rate']} | {chain_macro['complete_declared_text_chain_rate_chain_count']} | {chain_macro['complete_declared_text_chain_rate_ci95']} |",
                f"| Image Reference Claim Recall@{k} | {question_macro['image_reference_claim_recall']} | {chain_macro['image_reference_claim_recall']} | {chain_macro['image_reference_claim_recall_chain_count']} | {chain_macro['image_reference_claim_recall_ci95']} |",
                f"| Complete Image Reference Chain Rate@{k} | {question_macro['complete_image_reference_chain_rate']} | {chain_macro['complete_image_reference_chain_rate']} | {chain_macro['complete_image_reference_chain_rate_chain_count']} | {chain_macro['complete_image_reference_chain_rate_ci95']} |",
            ]
        )

    diagnostics = summary["diagnostics"]
    base = diagnostics["base_v3_diagnostics"]
    lines.extend(
        [
            "",
            "## 关键诊断",
            "",
            f"- 请求成功率: {base['request_success_rate']}",
            f"- 独立实体链数量: {diagnostics['independent_chain_count']}",
            f"- 端点文档召回: {diagnostics['endpoint_document_recall']}",
            f"- 桥接文档召回: {diagnostics['bridge_document_recall']}",
            f"- 端点齐全但桥接文档缺失率: {diagnostics['bridge_only_document_miss_rate']}",
            f"- 硬负例侵入率: {base['hard_negative_intrusion_rate']}",
            f"- 路径状态: {diagnostics['path_status_counts']}",
            f"- 统计提示: {diagnostics['inference_warning']}",
            "",
            "## Top-K 曲线",
            "",
            "| K | Q-Endpoint | Q-Bridge | Q-Declared Chain | Chain-Declared Chain | Q-Image Ref | Chain-Image Ref |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for k, values in diagnostics["k_curves"].items():
        question_macro = values["question_macro"]
        chain_macro = values["chain_macro"]
        lines.append(
            f"| {k} | {question_macro['endpoint_text_claim_recall']} | "
            f"{question_macro['bridge_text_claim_recall']} | "
            f"{question_macro['complete_declared_text_chain_rate']} | "
            f"{chain_macro['complete_declared_text_chain_rate']} | "
            f"{question_macro['image_reference_claim_recall']} | "
            f"{chain_macro['image_reference_claim_recall']} |"
        )

    lines.extend(
        [
            "",
            "## 分链结果",
            "",
            "| Chain | 题数 | Endpoint | Bridge | Supporting | Core Chain | Declared Chain | Image Ref |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for chain_id, values in diagnostics["by_chain"].items():
        lines.append(
            f"| {chain_id} | {values['count']} | "
            f"{values['endpoint_text_claim_recall']} | "
            f"{values['bridge_text_claim_recall']} | "
            f"{values['supporting_text_claim_recall']} | "
            f"{values['complete_core_bridge_chain_rate']} | "
            f"{values['complete_declared_text_chain_rate']} | "
            f"{values['image_reference_claim_recall']} |"
        )

    lines.extend(
        [
            "",
            "## 逐题结果",
            "",
            "| ID | Chain | Focus | 状态 | Endpoint | Bridge | Supporting | Core | Declared | Image Ref | 路径状态 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    primary_key = str(manifest["primary_k"])
    for result in results:
        bridge = (
            result.get("metrics", {})
            .get("metrics_by_k", {})
            .get(primary_key, {})
            .get("entity_bridge")
        )
        lines.append(
            f"| {result['id']} | {result['chain_id']} | "
            f"{result['evaluation_focus']} | {result['status']} | "
            f"{bridge['endpoint_text_claim_recall'] if bridge else '-'} | "
            f"{bridge['bridge_text_claim_recall'] if bridge else '-'} | "
            f"{bridge['supporting_text_claim_recall'] if bridge else '-'} | "
            f"{bridge['complete_core_bridge_chain'] if bridge else '-'} | "
            f"{bridge['complete_declared_text_chain'] if bridge else '-'} | "
            f"{bridge['image_reference_claim_recall'] if bridge else '-'} | "
            f"{bridge['path_status'] if bridge else '-'} |"
        )
    if summary["run_scope"] == "smoke":
        lines.extend(["", "> ⚠️ 本次为 --limit 冒烟运行，不可与完整考试直接比较。"])
    if manifest["corpus_drift"]:
        lines.extend(["", "> ⚠️ 本地语料哈希漂移，本结果不可用于正式比较。"])
    if manifest["dataset_revision"] == "unverified":
        lines.extend(
            [
                "",
                "> ⚠️ 未提供 --dataset-revision，无法证明远端知识库与本地快照一致，本结果不可用于正式比较。",
            ]
        )
    return "\n".join(lines) + "\n"


def write_outputs(
    out_root: Path,
    exam: Dict[str, Any],
    validation: Dict[str, Any],
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    timestamp: str,
) -> Path:
    """写出逐题结果、汇总、清单和专项报告。"""
    out_dir = out_root / exam["exam_meta"]["exam_id"] / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": core.SCHEMA_VERSION,
        "metrics_version": SPECIALIZED_METRICS_VERSION,
        "run_status": summary["run_status"],
        "run_scope": summary["run_scope"],
        "comparison_eligible": summary["comparison_eligible"],
        "exam_meta": exam["exam_meta"],
        "manifest": manifest,
        "validation": validation,
        "results": results,
    }
    for name, value in (
        (f"results_{timestamp}.json", payload),
        (f"summary_{timestamp}.json", summary),
        (f"manifest_{timestamp}.json", manifest),
    ):
        (out_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (out_dir / f"report_{timestamp}.md").write_text(
        build_report(exam, summary, manifest, results), encoding="utf-8"
    )
    return out_dir


def apply_comparison_guards(
    summary: Dict[str, Any], manifest: Dict[str, Any]
) -> Dict[str, Any]:
    """把远端语料版本和本地图片快照纳入正式比较资格。"""
    blockers = []
    if not summary.get("comparison_eligible"):
        blockers.append("base_run_not_comparison_eligible")
    if manifest.get("dataset_revision") == "unverified":
        blockers.append("dataset_revision_unverified")
    if manifest.get("corpus_drift"):
        blockers.append("document_corpus_drift")
    if manifest.get("image_drift"):
        blockers.append("image_asset_drift")
    summary["comparison_eligible"] = not blockers
    summary["comparison_blockers"] = blockers
    manifest["comparison_eligible"] = summary["comparison_eligible"]
    manifest["comparison_blockers"] = blockers
    return summary


def _comparison_values(
    payload: Dict[str, Any], field: str
) -> Dict[str, float]:
    primary_k = str(payload["manifest"]["primary_k"])
    image_reference_field = field in IMAGE_REFERENCE_METRIC_FIELDS
    values = {}
    for result in payload["results"]:
        is_asset_question = result["primary_type"] == "asset_reference_retrieval"
        if image_reference_field != is_asset_question:
            continue
        value = (
            result.get("metrics", {})
            .get("metrics_by_k", {})
            .get(primary_k, {})
            .get("entity_bridge", {})
            .get(field)
        )
        values[result["id"]] = float(value) if value is not None else 0.0
    return values


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _values_by_chain(
    values: Dict[str, float], chain_by_question: Dict[str, str]
) -> Dict[str, float]:
    grouped: Dict[str, List[float]] = {}
    for question_id, value in values.items():
        grouped.setdefault(chain_by_question[question_id], []).append(value)
    return {chain_id: _mean(items) for chain_id, items in grouped.items()}


def _evaluator_compatibility(left: Any, right: Any) -> List[str]:
    """指标语义必须一致；脚本文本哈希差异只作为审计提示，不阻断比较。"""
    left = left if isinstance(left, dict) else {}
    right = right if isinstance(right, dict) else {}
    for field in EVALUATOR_VERSION_FIELDS:
        if left.get(field) != right.get(field):
            raise BridgeComparisonError(f"比较字段不一致: evaluator.{field}")
    return [
        f"evaluator.{field} 不同（{left.get(field)} vs {right.get(field)}）；"
        "指标版本一致，仅说明脚本文本发生过变更"
        for field in EVALUATOR_HASH_FIELDS
        if left.get(field) != right.get(field)
    ]


def compare_runs(left_path: Path, right_path: Path) -> Dict[str, Any]:
    """按同题、同链比较 aRAG 与 Dify，并以链作为推断单位。"""
    try:
        left = json.loads(left_path.read_text(encoding="utf-8"))
        right = json.loads(right_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeComparisonError(f"无法读取比较结果: {exc}") from exc
    for name, payload in (("left", left), ("right", right)):
        if payload.get("metrics_version") != SPECIALIZED_METRICS_VERSION:
            raise BridgeComparisonError(f"{name} 不是实体桥接专项结果")
        if payload.get("run_status") != "completed" or payload.get("run_scope") != "full":
            raise BridgeComparisonError(f"{name} 必须是 completed/full 运行")
        if not payload.get("comparison_eligible"):
            raise BridgeComparisonError(f"{name} 标记为不可比较")
        if payload.get("manifest", {}).get("dataset_revision") == "unverified":
            raise BridgeComparisonError(f"{name} 未提供可核验 dataset_revision")
    for field in (
        "exam_id",
        "exam_sha256",
        "corpus",
        "dataset_revision",
        "primary_k",
        "eval_k",
    ):
        if left["manifest"].get(field) != right["manifest"].get(field):
            raise BridgeComparisonError(f"比较字段不一致: {field}")
    evaluator_notes = _evaluator_compatibility(
        left["manifest"].get("evaluator"), right["manifest"].get("evaluator")
    )

    left_ids = [item["id"] for item in left["results"] if item["scored"]]
    right_ids = [item["id"] for item in right["results"] if item["scored"]]
    if left_ids != right_ids:
        raise BridgeComparisonError("计分题 ID 或顺序不一致")
    left_shape = [
        (item["id"], item["chain_id"], item["primary_type"])
        for item in left["results"]
        if item["scored"]
    ]
    right_shape = [
        (item["id"], item["chain_id"], item["primary_type"])
        for item in right["results"]
        if item["scored"]
    ]
    if left_shape != right_shape:
        raise BridgeComparisonError("计分题 chain_id 或 primary_type 不一致")
    chain_by_question = {
        question_id: chain_id for question_id, chain_id, _ in left_shape
    }

    metrics = {}
    paired_ids: Dict[str, List[str]] = {}
    paired_chain_ids: Dict[str, List[str]] = {}
    for offset, field in enumerate(
        (
            "endpoint_text_claim_recall",
            "bridge_text_claim_recall",
            "complete_core_bridge_chain",
            "complete_declared_text_chain",
            "image_reference_claim_recall",
            "complete_image_reference_chain",
        )
    ):
        left_values = _comparison_values(left, field)
        right_values = _comparison_values(right, field)
        if list(left_values) != list(right_values):
            raise BridgeComparisonError(f"{field} 的配对题集合或顺序不一致")
        ids = list(left_values)
        if not ids:
            continue
        differences = [right_values[item] - left_values[item] for item in ids]
        left_chain_values = _values_by_chain(left_values, chain_by_question)
        right_chain_values = _values_by_chain(right_values, chain_by_question)
        chain_ids = list(left_chain_values)
        if chain_ids != list(right_chain_values):
            raise BridgeComparisonError(f"{field} 的配对链集合或顺序不一致")
        chain_differences = [
            right_chain_values[chain_id] - left_chain_values[chain_id]
            for chain_id in chain_ids
        ]
        paired_ids[field] = ids
        paired_chain_ids[field] = chain_ids
        metrics[field] = {
            "num_paired_questions": len(ids),
            "num_paired_chains": len(chain_ids),
            "question_macro": {
                "left_mean": round(_mean([left_values[item] for item in ids]), 4),
                "right_mean": round(_mean([right_values[item] for item in ids]), 4),
                "right_minus_left": round(_mean(differences), 4),
            },
            "chain_macro": {
                "left_mean": round(_mean(list(left_chain_values.values())), 4),
                "right_mean": round(_mean(list(right_chain_values.values())), 4),
                "right_minus_left": round(_mean(chain_differences), 4),
            },
            "clustered_bootstrap_ci95": core.bootstrap_ci(
                chain_differences, seed=core.BOOTSTRAP_SEED + offset
            ),
            "clustered_randomization_pvalue": core.paired_randomization_pvalue(
                chain_differences, seed=core.BOOTSTRAP_SEED + offset
            ),
        }
    return {
        "schema_version": core.SCHEMA_VERSION,
        "metrics_version": SPECIALIZED_METRICS_VERSION,
        "exam_id": left["manifest"]["exam_id"],
        "primary_k": left["manifest"]["primary_k"],
        "left": {
            "path": str(left_path),
            "backend": left["manifest"]["backend"],
            "dataset_id": left["manifest"]["dataset_id"],
        },
        "right": {
            "path": str(right_path),
            "backend": right["manifest"]["backend"],
            "dataset_id": right["manifest"]["dataset_id"],
        },
        "metrics": metrics,
        "paired_question_ids": paired_ids,
        "paired_chain_ids": paired_chain_ids,
        "evaluator_notes": evaluator_notes,
        "inference_unit": "chain_id",
        "inference_warning": (
            "链内题目相关；CI 与随机化检验基于链级差值。独立链较少时仅作探索性解释。"
        ),
    }


def write_comparison(comparison: Dict[str, Any], out_root: Path) -> Path:
    """写出专项配对比较。"""
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_root / comparison["exam_id"] / timestamp
    target.mkdir(parents=True, exist_ok=True)
    (target / f"comparison_{timestamp}.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# {comparison['exam_id']} 实体桥接专项配对比较",
        "",
        f"- Left: {comparison['left']['backend']} / {comparison['left']['dataset_id']}",
        f"- Right: {comparison['right']['backend']} / {comparison['right']['dataset_id']}",
        f"- 推断单位: {comparison['inference_unit']}",
        f"- 统计提示: {comparison['inference_warning']}",
        "",
    ]
    if comparison["evaluator_notes"]:
        lines += [f"> [审计提示] {note}" for note in comparison["evaluator_notes"]]
        lines.append("")
    lines += [
        "| 指标 | 题数 | 链数 | Q Δ | Chain Left | Chain Right | Chain Δ | Cluster CI95 | Cluster p |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in comparison["metrics"].items():
        question_macro = values["question_macro"]
        chain_macro = values["chain_macro"]
        lines.append(
            f"| {name} | {values['num_paired_questions']} | "
            f"{values['num_paired_chains']} | "
            f"{question_macro['right_minus_left']} | "
            f"{chain_macro['left_mean']} | {chain_macro['right_mean']} | "
            f"{chain_macro['right_minus_left']} | "
            f"{values['clustered_bootstrap_ci95']} | "
            f"{values['clustered_randomization_pvalue']} |"
        )
    (target / f"comparison_{timestamp}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return target


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析专项评测命令行，接口参数与 v3 保持一致。"""
    parser = argparse.ArgumentParser(
        description="实体桥接型跨文档多跳检索专项评测"
    )
    parser.add_argument("--exam-dir", default=str(DEFAULT_EXAM_DIR))
    parser.add_argument("--exam", default=None)
    parser.add_argument(
        "--backend", choices=core.SUPPORTED_BACKENDS, default=core.DEFAULT_BACKEND
    )
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="必须显式指定已导入本专项语料的知识库 ID",
    )
    parser.add_argument(
        "--dataset-revision",
        default=None,
        help=(
            "本地语料在远端的共同入库版本标签；aRAG/Dify 配对运行必须提供相同值，"
            "省略时结果不可比较"
        ),
    )
    parser.add_argument("--request-k", type=core.positive_int, default=10)
    parser.add_argument("--eval-k", type=core.int_list, default=[1, 3, 5, 10])
    parser.add_argument("--primary-k", type=core.positive_int, default=5)
    parser.add_argument(
        "--char-budgets", type=core.int_list, default=[1000, 2000, 4000]
    )
    parser.add_argument("--graph-search", action="store_true")
    parser.add_argument(
        "--dify-search-method",
        choices=core.DIFY_SEARCH_METHODS,
        default="hybrid_search",
        help="(Dify) 检索策略；coverage_search 按覆盖索引接口发送请求",
    )
    parser.add_argument("--score-threshold-enabled", action="store_true")
    parser.add_argument("--score-threshold", type=float, default=None)
    parser.add_argument("--limit", type=core.nonnegative_int, default=0)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--sleep", type=core.nonnegative_float, default=0.5)
    parser.add_argument("--timeout", type=core.positive_float, default=30.0)
    parser.add_argument("--retries", type=core.positive_int, default=3)
    parser.add_argument(
        "--raw-content-limit", type=core.positive_int, default=core.CONTENT_TRUNCATE
    )
    parser.add_argument("--allow-corpus-drift", action="store_true")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验考卷、Markdown 和 PNG 快照，不发起网络请求",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("RUN_A", "RUN_B"),
        help="比较两份 completed/full 且题目、语料版本和评测器完全一致的结果",
    )
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
    """运行校验、在线评测或配对比较。"""
    args = parse_args(argv)
    if args.compare:
        try:
            comparison = compare_runs(Path(args.compare[0]), Path(args.compare[1]))
            out_root = (
                Path(args.out_dir).expanduser()
                if args.out_dir
                else RESULTS_ROOT / "考试结果-多跳-比较"
            )
            target = write_comparison(comparison, out_root)
        except BridgeComparisonError as exc:
            sys.stderr.write(f"[比较失败] {exc}\n")
            return 5
        print(f"比较结果已输出到: {target}")
        return 0

    try:
        exam_files = core.discover_exam_files(args.exam_dir, args.exam)
        loaded = [
            (path, *load_and_validate_exam(path, args.allow_corpus_drift))
            for path in exam_files
        ]
    except (FileNotFoundError, core.ExamValidationError) as exc:
        sys.stderr.write(f"[校验失败] {exc}\n")
        return 2

    if args.validate_only:
        for path, exam, validation in loaded:
            bridge = validation["entity_bridge"]
            print(
                f"[校验通过] {path}: {len(exam['questions'])} 题, "
                f"{validation['total_claims']} claims, "
                f"chains={bridge['chain_counts']}, "
                f"image_reference_questions={bridge['image_reference_questions']}, "
                f"image_files={bridge['image_asset_count']}, "
                f"corpus_drift={validation['corpus_drift']}, "
                f"image_drift={bridge['image_drift']}"
            )
        return 0

    if not args.dataset_id:
        sys.stderr.write(
            "[错误] 专项语料必须先入库，并通过 --dataset-id 显式指定对应知识库；"
            "拒绝沿用基础脚本中面向旧语料的默认 Dataset。\n"
        )
        return 2

    try:
        headers = core.build_headers(args.backend)
    except core.AuthenticationError as exc:
        sys.stderr.write(f"[错误] {exc}\n")
        return 3
    session = core.requests.Session()
    session.headers.update(headers)
    if args.backend == "arag":
        print(
            "[警告] aRAG 接口不支持请求侧 K 参数；--request-k 仅作为目标深度记录，"
            "实际以响应返回数量为准。"
        )

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = core.resolve_output_root(args.out_dir, args.backend, "考试结果-多跳")
    for _, exam, validation in loaded:
        run_scope = "smoke" if args.limit else "full"
        manifest = core.build_manifest(exam, validation, args, timestamp, run_scope)
        manifest = copy.deepcopy(manifest)
        manifest.update(
            {
                "metrics_version": SPECIALIZED_METRICS_VERSION,
                "specialization": "entity_bridge_multihop",
                "interface_adapter": "retrieval_eval live implementation",
                "evaluator": {
                    "specialized_metrics_version": SPECIALIZED_METRICS_VERSION,
                    "specialized_script_sha256": core.sha256_file(Path(__file__)),
                    "core_metrics_version": core.METRICS_VERSION,
                    "core_script_sha256": core.sha256_file(CORE_PATH),
                },
                "image_drift": validation["entity_bridge"]["image_drift"],
                "image_reference_semantics": (
                    "retrieved Markdown path string; local PNG hash/dimensions "
                    "validated separately; no visual understanding score"
                ),
                "capability_boundary": (
                    "retrieval evidence closure only; final-answer reasoning is not scored"
                ),
            }
        )
        try:
            results = core.evaluate_exam(exam, session, args)
            run_status = "completed"
        except core.EvaluationAborted as exc:
            results = exc.results
            run_status = "aborted_auth"
        results = augment_results(exam, results)
        base_summary = core.aggregate(
            results,
            args.primary_k,
            args.eval_k,
            args.char_budgets,
            run_status,
            run_scope,
            validation["corpus_drift"],
        )
        summary = aggregate_bridge(
            results, base_summary, args.primary_k, args.eval_k
        )
        summary = apply_comparison_guards(summary, manifest)
        out_dir = write_outputs(
            out_root,
            exam,
            validation,
            summary,
            results,
            manifest,
            timestamp,
        )
        print(f"结果已输出到: {out_dir}")
        if run_status == "aborted_auth":
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
