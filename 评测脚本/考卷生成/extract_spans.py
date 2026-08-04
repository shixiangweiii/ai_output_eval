# -*- coding: utf-8 -*-
"""从真实语料反向抽取 accepted_span，回填事实台账。

设计要点：台账里作者只写 ``locator``（一个短的判别性标记，通常就是那个会在兄弟
文档之间分叉的取值加一两个上下文词），**span 由本脚本从语料里机械导出**——先定位
到所在句子，再以 locator 为中心裁到长度区间内，最后校验归一化后在该文档内唯一。

这样做的目的是消除 v3 最大的缺陷：那版考卷里 73 个 claim 全部只有 1 个人工挑的
span，"检索到的章节语义上能回答但判 0 分"占了失分的一半。span 由脚本导出、且每条
事实在文档内必须出现 ≥2 次，才能保证"合法证据位置被枚举完整"。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CORE_PATH = Path(__file__).resolve().parents[1] / "retrieval_eval_v4.py"
CORE_SPEC = importlib.util.spec_from_file_location("retrieval_eval_v4_core", CORE_PATH)
if CORE_SPEC is None or CORE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载评测器: {CORE_PATH}")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = Path(__file__).with_name("事实台账-2026-08-04-02.json")
SENTENCE_BOUNDARIES = "。！？\n"
STRIP_CHARS = " \t-*#>·—|"
MIN_SPAN_LEN = 12
MAX_SPAN_LEN = 40


class LedgerError(ValueError):
    """台账与语料不一致。"""


def _boundary_left(text: str, index: int) -> int:
    positions = [text.rfind(ch, 0, index) for ch in SENTENCE_BOUNDARIES]
    return max(positions) + 1


def _boundary_right(text: str, index: int) -> int:
    positions = [text.find(ch, index) for ch in SENTENCE_BOUNDARIES]
    positions = [p for p in positions if p != -1]
    return min(positions) if positions else len(text)


def derive_span(
    text: str,
    normalized_doc: str,
    locator: str,
    min_len: int = MIN_SPAN_LEN,
    max_len: int = MAX_SPAN_LEN,
) -> str:
    """把 locator 扩展成一个可读、长度合规、且文档内唯一的 span。"""
    occurrences = text.count(locator)
    if occurrences == 0:
        raise LedgerError(f"locator 不存在于语料: {locator!r}")
    if occurrences > 1:
        raise LedgerError(f"locator 在语料中出现 {occurrences} 次，无法定位: {locator!r}")
    index = text.index(locator)
    sentence_start = _boundary_left(text, index)
    sentence_end = _boundary_right(text, index + len(locator))
    sentence = text[sentence_start:sentence_end]

    left = index - sentence_start
    right = left + len(locator)
    if len(core.normalize_text(sentence[left:right])) > max_len:
        raise LedgerError(f"locator 本身已超过 {max_len} 个归一化字符: {locator!r}")

    def candidate(lo: int, hi: int) -> str:
        return sentence[lo:hi].strip(STRIP_CHARS)

    def acceptable(span: str) -> bool:
        normalized = core.normalize_text(span)
        return (
            min_len <= len(normalized) <= max_len
            and normalized_doc.count(normalized) == 1
        )

    # 先向右扩到句末，再向左扩到句首；每一步都优先取最短的合规结果
    grow_right, grow_left = right, left
    while True:
        span = candidate(grow_left, grow_right)
        if acceptable(span):
            return span
        if len(core.normalize_text(candidate(grow_left, grow_right))) > max_len:
            break
        if grow_right < len(sentence):
            grow_right += 1
            continue
        if grow_left > 0:
            grow_left -= 1
            continue
        break
    raise LedgerError(
        f"无法为 locator 导出长度在 {min_len}–{max_len} 且文档内唯一的 span: {locator!r}"
        f"（所在句: {sentence.strip()[:60]}）"
    )


def load_ledger(path: Path, facts_path: Optional[Path] = None) -> Dict[str, Any]:
    """读取台账；facts/families 段可以来自 synthesize_corpus.py 的机器产物。"""
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f"无法读取台账 {path}: {exc}") from exc
    if facts_path is not None:
        try:
            generated = json.loads(facts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"无法读取 facts 段 {facts_path}: {exc}") from exc
        ledger.setdefault("families", generated["families"])
        ledger.setdefault("facts", generated["facts"])
        if "chains" in generated:
            ledger.setdefault("chains", generated["chains"])
    for key in ("ledger_version", "corpus_relative_dir", "families", "facts", "questions"):
        if key not in ledger:
            raise LedgerError(f"台账缺少字段: {key}")
    return ledger


def load_corpus(relative_dir: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    corpus_dir = PROJECT_ROOT / relative_dir
    if not corpus_dir.is_dir():
        raise LedgerError(f"语料目录不存在: {corpus_dir}")
    raw: Dict[str, str] = {}
    normalized: Dict[str, str] = {}
    for path in sorted(corpus_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        raw[path.name] = text
        normalized[path.name] = core.normalize_text(text)
    if not raw:
        raise LedgerError(f"语料目录没有 Markdown 文件: {corpus_dir}")
    return raw, normalized


def resolve(ledger: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """把台账里所有 locator 解析成 span，返回解析后的台账与告警。"""
    raw, normalized = load_corpus(ledger["corpus_relative_dir"])
    warnings: List[str] = []
    resolved = json.loads(json.dumps(ledger, ensure_ascii=False))
    seen: Dict[Tuple[str, str], str] = {}

    for fact in resolved["facts"]:
        fact_id = fact["fact_id"]
        locations = fact.get("locations") or {}
        if not locations:
            raise LedgerError(f"{fact_id} 没有声明任何 location")
        is_asset = fact.get("kind") == "asset"
        for document, entries in locations.items():
            if document not in raw:
                raise LedgerError(f"{fact_id} 引用了不存在的语料: {document}")
            if len(entries) < 2 and not is_asset:
                raise LedgerError(
                    f"{fact_id} 在 {document} 只声明了 {len(entries)} 处出现位置；"
                    "每条事实需要至少 2 处，多 span 才有意义"
                )
            value = (fact.get("values") or {}).get(document)
            for entry in entries:
                locator = entry.get("locator")
                if not locator:
                    raise LedgerError(f"{fact_id}/{document} 的 location 缺少 locator")
                try:
                    # 资产类事实的 span 就是图片 URL 本身：不做句子扩展，也不受长度区间限制
                    span = locator if is_asset else derive_span(
                        raw[document], normalized[document], locator
                    )
                    if is_asset and raw[document].count(locator) != 1:
                        raise LedgerError(
                            f"资产 locator 在文档中出现 {raw[document].count(locator)} 次: {locator!r}"
                        )
                except LedgerError as exc:
                    raise LedgerError(f"[{fact_id} / {document}] {exc}") from exc
                key = (document, core.normalize_text(span))
                if key in seen:
                    raise LedgerError(
                        f"{fact_id} 在 {document} 导出的 span 与 {seen[key]} 重复: {span!r}"
                    )
                seen[key] = fact_id
                if entry.get("section") and entry["section"] not in raw[document]:
                    warnings.append(
                        f"{fact_id}/{document} 的 section {entry['section']!r} 未在文中出现"
                    )
                if value and core.normalize_text(value) not in core.normalize_text(span):
                    warnings.append(
                        f"{fact_id}/{document} 导出的 span 未包含判别值 {value!r}: {span!r}"
                    )
                entry["span"] = span
                entry["normalized_length"] = len(core.normalize_text(span))
    return resolved, warnings


def span_separation_report(resolved: Dict[str, Any], minimum: int) -> List[str]:
    """检查同一文档内不同事实的 span 间距，防止一个块同时命中多题。"""
    raw, _ = load_corpus(resolved["corpus_relative_dir"])
    positions: Dict[str, List[Tuple[int, str]]] = {}
    for fact in resolved["facts"]:
        for document, entries in fact["locations"].items():
            for entry in entries:
                positions.setdefault(document, []).append(
                    (raw[document].index(entry["span"]), fact["fact_id"])
                )
    problems = []
    for document, items in positions.items():
        items.sort()
        for (left_pos, left_id), (right_pos, right_id) in zip(items, items[1:]):
            if left_id != right_id and right_pos - left_pos < minimum:
                problems.append(
                    f"{document}: {left_id} 与 {right_id} 的 span 相距 {right_pos - left_pos} 字"
                    f"，低于 {minimum}"
                )
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从语料反向抽取 span 并回填事实台账")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument(
        "--facts", default=None,
        help="facts/families 段的来源 JSON（synthesize_corpus.py --facts-out 的产物）",
    )
    parser.add_argument("--out", default=None, help="默认写到 <ledger>-resolved.json")
    parser.add_argument("--min-separation", type=int, default=300)
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger).expanduser()
    try:
        ledger = load_ledger(
            ledger_path, Path(args.facts).expanduser() if args.facts else None
        )
        resolved, warnings = resolve(ledger)
        problems = span_separation_report(resolved, args.min_separation)
    except LedgerError as exc:
        sys.stderr.write(f"[台账解析失败] {exc}\n")
        return 2

    for warning in warnings:
        print(f"  [警告] {warning}")
    for problem in problems:
        print(f"  [间距不足] {problem}")

    out_path = Path(args.out) if args.out else ledger_path.with_name(
        ledger_path.stem + "-resolved.json"
    )
    out_path.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(
        len(entries) for fact in resolved["facts"] for entries in fact["locations"].values()
    )
    print(
        f"[解析完成] {len(resolved['facts'])} 条事实 / {total} 个 span → {out_path}"
        + (f"；{len(problems)} 处间距不足" if problems else "")
    )
    return 3 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
