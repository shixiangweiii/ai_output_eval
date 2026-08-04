"""考卷生成流水线单测：span 反向抽取与台账投影直接决定 GT 正确性，必须锁住。"""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GEN_DIR = ROOT / "评测脚本" / "考卷生成"
FIXTURE_RELATIVE = "tests/fixtures/语料-v4单测"
FIXTURE_CORPUS = ROOT / FIXTURE_RELATIVE
DOC_A = "T1-alpha-2023.md"
DOC_B = "T2-alpha-2025.md"
DOC_END1 = "T3-yanche-role.md"
DOC_BRIDGE = "T4-liyan-equity.md"
DOC_END2 = "T5-shenyan-role.md"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"gen_{name}", GEN_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXTRACT = _load("extract_spans")
BUILD = _load("build_exam")
AUDIT = _load("audit_exam")
SCREEN = _load("screen_candidates")
core = EXTRACT.core

RAW_A = (FIXTURE_CORPUS / DOC_A).read_text(encoding="utf-8")
NORM_A = core.normalize_text(RAW_A)


# --------------------------------------------------------------------------
# derive_span：span 由脚本从语料导出，不是人工誊抄
# --------------------------------------------------------------------------

def test_derive_span_expands_locator_into_readable_span():
    span = EXTRACT.derive_span(RAW_A, NORM_A, "门到球囊时间上限为 90 分钟")
    assert "90 分钟" in span
    assert 12 <= len(core.normalize_text(span)) <= 40
    assert NORM_A.count(core.normalize_text(span)) == 1


def test_derive_span_grows_short_locator_to_minimum_length():
    span = EXTRACT.derive_span(RAW_A, NORM_A, "上报质控组备案")
    assert len(core.normalize_text(span)) >= 12
    assert "上报质控组备案" in span


def test_derive_span_rejects_ambiguous_locator():
    with pytest.raises(EXTRACT.LedgerError, match="出现 2 次"):
        EXTRACT.derive_span(RAW_A, NORM_A, "分钟")


def test_derive_span_rejects_missing_locator():
    with pytest.raises(EXTRACT.LedgerError, match="不存在于语料"):
        EXTRACT.derive_span(RAW_A, NORM_A, "语料里没有这句话")


def test_derive_span_rejects_overlong_locator():
    long_locator = RAW_A[RAW_A.index("门到球囊") : RAW_A.index("门到球囊") + 60]
    with pytest.raises(EXTRACT.LedgerError, match="已超过"):
        EXTRACT.derive_span(RAW_A, NORM_A, long_locator)


def test_derived_spans_survive_evaluator_normalization():
    """导出的 span 必须能通过评测器的"归一化后是源文档子串"校验。"""
    for locator in ("门到球囊时间上限为 90 分钟", "首次评分阈值设置为 0.62"):
        span = EXTRACT.derive_span(RAW_A, NORM_A, locator)
        assert core.normalize_text(span) in NORM_A


# --------------------------------------------------------------------------
# 台账 → 考卷投影
# --------------------------------------------------------------------------

def make_ledger():
    return {
        "ledger_version": "test",
        "exam_id": "考卷-单测-生成",
        "exam_title": "生成流水线单测考卷",
        "purpose": "单测夹具",
        "corpus_snapshot_id": "语料-v4单测",
        "corpus_relative_dir": FIXTURE_RELATIVE,
        "retrieval_protocol": {
            "primary_k": 3, "eval_ks": [1, 3], "request_k": 5,
            "char_budgets": [40, 400], "primary_budget": 400,
        },
        "design_constraints": {
            "min_claims_per_document": 1, "max_claim_share_per_document": 1.0,
            "min_low_lexical_overlap_questions": 0, "min_hard_negative_questions": 0,
            "min_spans_per_claim": 2, "max_span_normalized_len": 40,
            "min_sanity_questions": 1, "min_unanswerable_questions": 1,
        },
        "families": [
            {"id": "T", "cluster_id": "C-T", "axis": "version",
             "title": "夹具族", "documents": [DOC_A, DOC_B]},
            {"id": "T2", "cluster_id": "C-T2", "axis": "version",
             "title": "夹具族二", "documents": [DOC_B]},
        ],
        "chains": [{
            "id": "CH-T1", "family": "TB", "cluster_id": "C-bridge",
            "relation_type": "equity_control", "path": "言澈→砺岩精工←潮生重工←沈砚",
            "endpoint_entities": ["言澈", "沈砚"],
            "bridge_entities": ["砺岩精工", "潮生重工"],
            "endpoint_documents": [DOC_END1, DOC_END2],
            "bridge_documents": [DOC_BRIDGE],
            "documents": [DOC_END1, DOC_BRIDGE, DOC_END2],
        }],
        "facts": [
            {
                "fact_id": "F-T-TIME", "family": "T", "cluster_id": "C-T",
                "kind": "text", "claim_type": "anchor", "dimension": "门到球囊时间上限",
                "values": {DOC_A: "90 分钟", DOC_B: "75 分钟"},
                "locations": {
                    DOC_A: [
                        {"section": "1. 时限要求", "locator": "门到球囊时间上限为 90 分钟"},
                        {"section": "3. 要点速查", "locator": "90 分钟内完成球囊扩张即视为达标"},
                    ],
                    DOC_B: [
                        {"section": "1. 时限要求", "locator": "门到球囊时间上限为 75 分钟"},
                        {"section": "3. 要点速查", "locator": "75 分钟内完成球囊扩张即视为达标"},
                    ],
                },
            },
            {
                "fact_id": "F-T-SCORE", "family": "T", "cluster_id": "C-T",
                "kind": "text", "claim_type": "passage", "dimension": "首次评分阈值",
                "values": {DOC_A: "0.62", DOC_B: "0.71"},
                "locations": {
                    DOC_A: [
                        {"section": "2. 阈值设置", "locator": "首次评分阈值设置为 0.62"},
                        {"section": "3. 要点速查", "locator": "0.62 以下一律转人工"},
                    ],
                    DOC_B: [
                        {"section": "2. 阈值设置", "locator": "首次评分阈值设置为 0.71"},
                        {"section": "3. 要点速查", "locator": "0.71 以下一律转人工"},
                    ],
                },
            },
            {
                "fact_id": "F-T-YANCHE", "family": "TB", "cluster_id": "C-bridge",
                "chain_id": "CH-T1", "hop_role": "endpoint",
                "kind": "text", "claim_type": "anchor", "dimension": "言澈任职",
                "values": {DOC_END1: "砺岩精工"},
                "locations": {DOC_END1: [
                    {"section": "一、任职信息", "locator": "言澈于 2022 年 3 月加入砺岩精工"},
                    {"section": "二、职级安排", "locator": "砺岩精工为言澈保留了技术职级序列"},
                ]},
            },
            {
                "fact_id": "F-T-EQUITY", "family": "TB", "cluster_id": "C-bridge",
                "chain_id": "CH-T1", "hop_role": "bridge",
                "kind": "text", "claim_type": "passage", "dimension": "控股关系",
                "values": {DOC_BRIDGE: "潮生重工"},
                "locations": {DOC_BRIDGE: [
                    {"section": "一、交易结果", "locator": "潮生重工已完成对砺岩精工百分之七十股权的收购"},
                    {"section": "二、交割后的治理安排", "locator": "砺岩精工纳入潮生重工合并报表范围"},
                ]},
            },
            {
                "fact_id": "F-T-SHENYAN", "family": "TB", "cluster_id": "C-bridge",
                "chain_id": "CH-T1", "hop_role": "endpoint",
                "kind": "text", "claim_type": "anchor", "dimension": "沈砚任职",
                "values": {DOC_END2: "潮生重工"},
                "locations": {DOC_END2: [
                    {"section": "一、岗位任命", "locator": "沈砚现任潮生重工船队技术副总裁"},
                    {"section": "二、汇报关系", "locator": "该岗位向潮生重工首席运营官汇报"},
                ]},
            },
        ],
        "questions": [
            {
                "id": "g_q001", "question_role": "sanity", "cluster_id": "C-T",
                "difficulty": "simple", "primary_type": "single_doc_fact",
                "tags": ["sanity"], "question": "旧版对抢救时限怎么定的？",
                "reference_answer": "旧版为 90 分钟。",
                "claims": [{"fact_id": "F-T-TIME", "document": DOC_A}],
            },
            {
                "id": "g_q002", "question_role": "scored", "cluster_id": "C-T",
                "difficulty": "hard", "primary_type": "cross_doc_compare",
                "tags": ["cross_doc"], "question": "两版在自动通道门槛上差多少？",
                "reference_answer": "旧版 0.62，新版 0.71。",
                "claims": [
                    {"fact_id": "F-T-SCORE", "document": DOC_A},
                    {"fact_id": "F-T-SCORE", "document": DOC_B},
                ],
            },
            {
                "id": "g_q004", "question_role": "scored", "cluster_id": "C-T2",
                "difficulty": "hard", "primary_type": "disambiguation_hard_negative",
                "tags": ["hard_negative"], "question": "新版对抢救时限怎么定的？",
                "reference_answer": "新版为 75 分钟。",
                "claims": [{"fact_id": "F-T-TIME", "document": DOC_B}],
                "negatives": [{"fact_id": "F-T-SCORE", "document": DOC_A}],
            },
            {
                "id": "g_q005", "question_role": "scored", "cluster_id": "C-bridge",
                "chain_id": "CH-T1", "difficulty": "hard", "primary_type": "cross_doc_chain",
                "tags": ["multi_hop"],
                "question": "言澈和沈砚之间存在什么间接的职业关联？",
                "reference_answer": "言澈在砺岩精工任职，沈砚在潮生重工任职，潮生重工已控股砺岩精工。",
                "claims": [
                    {"fact_id": "F-T-YANCHE", "document": DOC_END1},
                    {"fact_id": "F-T-EQUITY", "document": DOC_BRIDGE},
                    {"fact_id": "F-T-SHENYAN", "document": DOC_END2},
                ],
            },
            {
                "id": "g_q003", "question_role": "unanswerable", "cluster_id": "C-T",
                "difficulty": "diagnostic", "primary_type": "unanswerable_diagnostic",
                "tags": ["no_answer"], "question": "今天急诊排队多少人？",
                "diagnostic_reason": "语料不含实时数据。",
            },
        ],
    }


def resolved_ledger():
    resolved, _ = EXTRACT.resolve(make_ledger())
    return resolved


def test_resolve_fills_every_location_with_a_span():
    resolved = resolved_ledger()
    for fact in resolved["facts"]:
        for entries in fact["locations"].values():
            assert len(entries) == 2
            for entry in entries:
                assert entry["span"]
                assert 12 <= entry["normalized_length"] <= 40


def test_resolve_requires_two_locations_for_text_facts():
    ledger = make_ledger()
    ledger["facts"][0]["locations"][DOC_A].pop()
    with pytest.raises(EXTRACT.LedgerError, match="至少 2 处"):
        EXTRACT.resolve(ledger)


def test_resolve_allows_single_location_for_asset_facts():
    ledger = make_ledger()
    ledger["facts"].append({
        "fact_id": "F-T-IMG", "family": "T", "cluster_id": "C-T",
        "kind": "asset", "claim_type": "passage", "dimension": "流程图",
        "values": {DOC_A: "https://example.invalid/flow-2023.png"},
        "locations": {DOC_A: [
            {"section": "3. 要点速查", "locator": "https://example.invalid/flow-2023.png"}
        ]},
    })
    resolved, _ = EXTRACT.resolve(ledger)
    asset = [f for f in resolved["facts"] if f["fact_id"] == "F-T-IMG"][0]
    # 资产 span 就是 URL 本身，不做句子扩展也不受长度区间限制
    assert asset["locations"][DOC_A][0]["span"].endswith("flow-2023.png")


def test_build_exam_expands_claims_and_evidence_chain():
    exam = BUILD.build_exam(resolved_ledger())
    assert exam["schema_version"] == "4.1"
    counts = exam["exam_meta"]["question_counts"]
    assert (counts["total"], counts["scored"], counts["sanity"], counts["unanswerable"]) == (5, 3, 1, 1)

    compare = [q for q in exam["questions"] if q["id"] == "g_q002"][0]
    assert compare["evidence_chain"] == ["g_q002_c1", "g_q002_c2"]
    assert [c["source_document"] for c in compare["claims"]] == [DOC_A, DOC_B]
    assert all(len(c["accepted_spans"]) == 2 for c in compare["claims"])

    disambiguation = [q for q in exam["questions"] if q["id"] == "g_q004"][0]
    assert disambiguation["negative_evidence"][0]["source_document"] == DOC_A
    assert disambiguation["claims"][0]["source_document"] == DOC_B

    diagnostic = [q for q in exam["questions"] if q["id"] == "g_q003"][0]
    assert diagnostic["claims"] == []
    assert diagnostic["expected_behavior"] == "no_relevant_evidence"


def test_hop_design_is_derived_from_claims_not_hand_written():
    exam = BUILD.build_exam(resolved_ledger())
    chain = [q for q in exam["questions"] if q["id"] == "g_q005"][0]
    design = chain["hop_design"]
    assert design["chain_id"] == "CH-T1"
    assert design["endpoint_entities"] == ["言澈", "沈砚"]
    assert design["endpoint_documents"] == [DOC_END1, DOC_END2]
    assert design["bridge_documents"] == [DOC_BRIDGE]
    assert [c["hop_role"] for c in chain["claims"]] == ["endpoint", "bridge", "endpoint"]
    # 非链式题不带多跳字段
    assert "hop_design" not in [q for q in exam["questions"] if q["id"] == "g_q002"][0]
    assert exam["exam_meta"]["bridge_chains"][0]["id"] == "CH-T1"
    assert {c["id"] for c in exam["exam_meta"]["clusters"]} >= {"C-T", "C-T2", "C-bridge"}


def test_gate_catches_homogeneous_chain_evidence():
    """G11：链式题各 claim 的证据雷同，说明是并行召回而非多跳。"""
    def mutate(exam, _ledger):
        chain = [q for q in exam["questions"] if q["id"] == "g_q005"][0]
        # 把桥接证据换成与端点证据几乎相同的句子
        chain["claims"][1]["accepted_spans"] = list(chain["claims"][0]["accepted_spans"])
    assert any(p.startswith("G11") for p in audited(mutate))


def test_gate_catches_bridge_leaking_endpoint_entity():
    def mutate(exam, _ledger):
        chain = [q for q in exam["questions"] if q["id"] == "g_q005"][0]
        chain["hop_design"]["endpoint_entities"] = ["砺岩精工", "沈砚"]
    assert any(p.startswith("G10") for p in audited(mutate))


def test_gate_catches_hop_role_mismatch():
    def mutate(exam, _ledger):
        chain = [q for q in exam["questions"] if q["id"] == "g_q005"][0]
        chain["claims"][1]["hop_role"] = "endpoint"
    assert any(p.startswith("G12") for p in audited(mutate))


def test_screen_flags_bridge_reachable_from_query():
    """G13：桥接文档若能被题面直接召回，这一跳就是假的。"""
    exam = BUILD.build_exam(resolved_ledger())
    chain = [q for q in exam["questions"] if q["id"] == "g_q005"][0]
    report = SCREEN.screen(exam, window=200, overlap=50, top_k=10)
    assert report["chain_questions"] == 1
    # 把题面换成直指桥接文档内容的问法，桥接立刻变得可直达
    chain["question"] = "潮生重工对砺岩精工的股权收购完成了吗？言澈和沈砚是否知情？"
    leaked = SCREEN.screen(exam, window=200, overlap=50, top_k=10)
    assert leaked["g13_bridge_directly_reachable"]


def test_built_exam_passes_evaluator_validation(tmp_path):
    """生成器与评测器共用同一套契约：投影出来的考卷必须能直接过校验。"""
    exam = BUILD.build_exam(resolved_ledger())
    path = tmp_path / "考卷-单测-生成.json"
    path.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding="utf-8")
    _, validation = core.load_and_validate_exam(path)
    assert validation["total_claims"] == 7
    assert validation["role_counts"] == {"sanity": 1, "scored": 3, "unanswerable": 1}


def test_build_exam_rejects_unknown_fact_reference():
    ledger = resolved_ledger()
    ledger["questions"][0]["claims"][0]["fact_id"] = "F-T-不存在"
    with pytest.raises(BUILD.BuildError, match="未定义的事实"):
        BUILD.build_exam(ledger)


def test_build_exam_rejects_unresolved_ledger():
    with pytest.raises(BUILD.BuildError):
        BUILD.build_exam(make_ledger())


# --------------------------------------------------------------------------
# 质量闸门确实能抓到缺陷
# --------------------------------------------------------------------------

def audited(mutate=None):
    ledger = resolved_ledger()
    exam = BUILD.build_exam(ledger)
    if mutate:
        mutate(exam, ledger)
    # 夹具语料只有两篇短文，间距阈值按其尺度缩放；真实考卷用默认的 300
    return AUDIT.audit(exam, ledger, min_separation=20, min_cluster_questions=1)


def test_clean_exam_passes_all_gates():
    assert audited() == []


def test_gate_catches_answer_leaked_into_question():
    def mutate(exam, _ledger):
        exam["questions"][1]["question"] = "自动通道门槛是不是 0.62？"  # 题面泄漏答案
    assert any(p.startswith("G6") for p in audited(mutate))


def test_gate_catches_reference_answer_missing_a_value():
    def mutate(exam, _ledger):
        exam["questions"][1]["reference_answer"] = "两版不一样。"
    assert any(p.startswith("G5") for p in audited(mutate))


def test_gate_catches_negative_sharing_positive_document():
    def mutate(exam, _ledger):
        target = [q for q in exam["questions"] if q["id"] == "g_q004"][0]
        target["negative_evidence"][0]["source_document"] = DOC_B
    assert any(p.startswith("G4") for p in audited(mutate))


def test_gate_catches_non_ascii_document_name():
    """跨产品评测里中文文件名有对不上的风险，G8 必须拦住。"""
    exam = BUILD.build_exam(resolved_ledger())
    exam["exam_meta"]["corpus"]["documents"][0]["name"] = "中文名.md"
    problems = AUDIT.gate_document_balance(exam, None, None)
    assert any("非 ASCII" in p for p in problems)


# --------------------------------------------------------------------------
# 难度校准基线
# --------------------------------------------------------------------------

def test_screen_flags_trivial_and_separates_low_overlap(tmp_path):
    exam = BUILD.build_exam(resolved_ledger())
    report = SCREEN.screen(exam, window=200, overlap=50, top_k=10)
    assert report["num_questions"] == 4  # 不可答题没有 claims，不参与筛查
    assert {row["id"] for row in report["questions"]} == {"g_q001", "g_q002", "g_q004", "g_q005"}
    # 未打 low_lexical_overlap 却零分的题才可疑；本夹具题面与 span 高度重合，不应出现
    assert report["floor_suspicious"] == []
    assert report["trivial_scored_questions"] == ["g_q002", "g_q004"]


def test_bm25_ranks_the_right_document_first():
    exam = BUILD.build_exam(resolved_ledger())
    corpus = {
        DOC_A: (FIXTURE_CORPUS / DOC_A).read_text(encoding="utf-8"),
        DOC_B: (FIXTURE_CORPUS / DOC_B).read_text(encoding="utf-8"),
    }
    chunks = SCREEN.build_chunks(corpus, 200, 50)
    ranked = SCREEN.BM25(chunks).rank("门到球囊时间上限为 90 分钟", 3)
    assert chunks[ranked[0][0]]["document"] == DOC_A
