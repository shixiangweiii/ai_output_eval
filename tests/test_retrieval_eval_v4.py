"""retrieval_eval_v4 单元测试：全部 mock HTTP，无需凭证与网络。"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "评测脚本" / "retrieval_eval_v4.py"
V3_SCRIPT = ROOT / "评测脚本" / "retrieval_eval.py"
FIXTURE_CORPUS = ROOT / "tests" / "fixtures" / "语料-v4单测"
FIXTURE_RELATIVE = "tests/fixtures/语料-v4单测"

SPEC = importlib.util.spec_from_file_location("retrieval_eval_v4", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

V3_SPEC = importlib.util.spec_from_file_location("retrieval_eval_v3_ref", V3_SCRIPT)
assert V3_SPEC and V3_SPEC.loader
V3 = importlib.util.module_from_spec(V3_SPEC)
V3_SPEC.loader.exec_module(V3)


DOC_A = "T1-alpha-2023.md"
DOC_B = "T2-alpha-2025.md"
# 实体桥接三文档链：端点 → 桥接 → 端点，桥接文档不含任一端点人名
DOC_END1 = "T3-yanche-role.md"
DOC_BRIDGE = "T4-liyan-equity.md"
DOC_END2 = "T5-shenyan-role.md"
ALL_DOCS = (DOC_A, DOC_B, DOC_END1, DOC_BRIDGE, DOC_END2)
IMAGE_URL = "https://example.invalid/flow-2023.png"


def _sha(name):
    return hashlib.sha256((FIXTURE_CORPUS / name).read_bytes()).hexdigest()


def _claim(claim_id, doc, section, spans, claim_type="anchor", kind="text", hop_role=None):
    claim = {
        "id": claim_id,
        "kind": kind,
        "claim_type": claim_type,
        "source_document": doc,
        "section": section,
        "accepted_spans": spans,
    }
    if hop_role:
        claim["hop_role"] = hop_role
    return claim


def base_exam():
    """构造一份最小但完整的 v4 考卷（2 篇兄弟语料、2 个 cluster）。"""
    questions = [
        {
            "id": "t_q001",
            "question_role": "sanity",
            "cluster_id": "C-alpha",
            "difficulty": "simple",
            "primary_type": "single_doc_fact",
            "tags": ["sanity"],
            "question": "2023 版对门到球囊时间的上限是多少？",
            "reference_answer": "90 分钟。",
            "claims": [
                _claim(
                    "t_q001_c1", DOC_A, "1. 时限要求",
                    ["门到球囊时间上限为 90 分钟", "90 分钟内完成球囊扩张即视为达标"],
                )
            ],
            "evidence_chain": ["t_q001_c1"],
            "negative_evidence": [],
        },
        {
            "id": "t_q002",
            "question_role": "scored",
            "cluster_id": "C-alpha",
            "difficulty": "hard",
            "primary_type": "cross_doc_compare",
            "tags": ["cross_doc"],
            "question": "两个版本在自动通道准入和抢救时限上分别怎么定的？",
            "reference_answer": "旧版 0.62 与 90 分钟，新版 75 分钟。",
            "claims": [
                _claim(
                    "t_q002_c1", DOC_A, "2. 阈值设置",
                    ["首次评分阈值设置为 0.62", "0.62 以下一律转人工"],
                ),
                _claim(
                    "t_q002_c2", DOC_B, "1. 时限要求",
                    ["门到球囊时间上限为 75 分钟", "75 分钟内完成球囊扩张即视为达标"],
                    claim_type="passage",
                ),
                _claim(
                    "t_q002_c3", DOC_A, "3. 要点速查", [IMAGE_URL],
                    claim_type="passage", kind="asset",
                ),
            ],
            "evidence_chain": ["t_q002_c1", "t_q002_c2", "t_q002_c3"],
            "negative_evidence": [
                {
                    "id": "t_q002_n1",
                    "source_document": DOC_B,
                    "section": "2. 阈值设置",
                    "accepted_spans": ["首次评分阈值设置为 0.71"],
                }
            ],
        },
        {
            "id": "t_q003",
            "question_role": "scored",
            "cluster_id": "C-beta",
            "difficulty": "medium",
            "primary_type": "single_doc_multi_claim",
            "tags": ["single_doc"],
            "question": "新版把自动通道门槛和人工复核比例分别定在什么水平？",
            "reference_answer": "阈值 0.71，复核率目标 12%。",
            "claims": [
                _claim(
                    "t_q003_c1", DOC_B, "2. 阈值设置",
                    ["首次评分阈值设置为 0.71", "0.71 以下一律转人工"],
                ),
                _claim(
                    "t_q003_c2", DOC_B, "4. 变更说明",
                    ["把复核率目标定为 12%", "目标 12% 上下浮动两个百分点"],
                    claim_type="passage",
                ),
            ],
            "evidence_chain": ["t_q003_c1", "t_q003_c2"],
            "negative_evidence": [],
        },
        {
            "id": "t_q004",
            "question_role": "unanswerable",
            "cluster_id": "C-beta",
            "difficulty": "diagnostic",
            "primary_type": "unanswerable_diagnostic",
            "tags": ["no_answer"],
            "question": "请给出今天该院急诊的实时排队人数。",
            "claims": [],
            "negative_evidence": [],
            "expected_behavior": "no_relevant_evidence",
            "diagnostic_reason": "语料不含实时数据。",
        },
        {
            "id": "t_q005",
            "question_role": "scored",
            "cluster_id": "C-bridge",
            "difficulty": "hard",
            "primary_type": "cross_doc_chain",
            "tags": ["multi_hop"],
            "question": "言澈和沈砚之间存在什么间接的职业关联？只说明材料能证明的部分。",
            "reference_answer": "言澈在砺岩精工任职，沈砚在潮生重工任职，而潮生重工已控股砺岩精工。",
            "hop_design": {
                "chain_id": "CH-T1",
                "relation_type": "equity_control",
                "endpoint_entities": ["言澈", "沈砚"],
                "endpoint_documents": [DOC_END1, DOC_END2],
                "bridge_documents": [DOC_BRIDGE],
                "bridge_entities": ["砺岩精工", "潮生重工"],
            },
            "claims": [
                _claim(
                    "t_q005_c1", DOC_END1, "一、任职信息",
                    ["言澈于 2022 年 3 月加入砺岩精工", "砺岩精工为言澈保留了技术职级序列"],
                    hop_role="endpoint",
                ),
                _claim(
                    "t_q005_c2", DOC_BRIDGE, "一、交易结果",
                    ["潮生重工已完成对砺岩精工百分之七十股权的收购",
                     "砺岩精工纳入潮生重工合并报表范围"],
                    claim_type="passage", hop_role="bridge",
                ),
                _claim(
                    "t_q005_c3", DOC_END2, "一、岗位任命",
                    ["沈砚现任潮生重工船队技术副总裁", "该岗位向潮生重工首席运营官汇报"],
                    hop_role="endpoint",
                ),
            ],
            "evidence_chain": ["t_q005_c1", "t_q005_c2", "t_q005_c3"],
            "negative_evidence": [],
        },
    ]
    return {
        "schema_version": "4.1",
        "exam_meta": {
            "exam_id": "考卷-单测-01",
            "title": "v4 单测夹具考卷",
            "retrieval_protocol": {
                "primary_k": 3,
                "eval_ks": [1, 3],
                "request_k": 5,
                "char_budgets": [40, 400],
                "primary_budget": 400,
            },
            "question_counts": {
                "total": 5,
                "scored": 3,
                "sanity": 1,
                "unanswerable": 1,
                "by_primary_type": {
                    "single_doc_fact": 1,
                    "cross_doc_compare": 1,
                    "cross_doc_chain": 1,
                    "single_doc_multi_claim": 1,
                    "unanswerable_diagnostic": 1,
                },
                "by_difficulty": {"simple": 1, "hard": 2, "medium": 1, "diagnostic": 1},
            },
            "clusters": [
                {"id": "C-alpha", "family": "T", "axis": "version", "documents": [DOC_A, DOC_B]},
                {"id": "C-beta", "family": "T", "axis": "version", "documents": [DOC_B]},
                {"id": "C-bridge", "family": "TB", "axis": "entity_bridge",
                 "documents": [DOC_END1, DOC_BRIDGE, DOC_END2]},
            ],
            "bridge_chains": [{
                "id": "CH-T1",
                "family": "TB",
                "relation_type": "equity_control",
                "bridge_entities": ["砺岩精工", "潮生重工"],
                "documents": [DOC_END1, DOC_BRIDGE, DOC_END2],
            }],
            "design_constraints": {
                "min_claims_per_document": 1,
                "max_claim_share_per_document": 1.0,
                "min_low_lexical_overlap_questions": 0,
                "min_hard_negative_questions": 0,
                "min_spans_per_claim": 2,
                "max_span_normalized_len": 40,
                "min_sanity_questions": 1,
                "min_unanswerable_questions": 1,
            },
            "corpus": {
                "snapshot_id": "语料-v4单测",
                "relative_dir": FIXTURE_RELATIVE,
                "documents": [{"name": name, "sha256": _sha(name)} for name in ALL_DOCS],
            },
        },
        "questions": questions,
    }


def write_exam(tmp_path, mutate=None):
    exam = base_exam()
    if mutate:
        mutate(exam)
    path = tmp_path / "考卷-单测-01.json"
    path.write_text(json.dumps(exam, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 归一化与文档名
# --------------------------------------------------------------------------

def test_normalize_text_matches_v3_exactly():
    """v3/v4 两份实现必须逐字一致，否则跨版本 span 匹配口径会悄悄漂移。"""
    samples = [
        (FIXTURE_CORPUS / DOC_A).read_text(encoding="utf-8"),
        (FIXTURE_CORPUS / DOC_B).read_text(encoding="utf-8"),
        "**加粗** 与 `代码` 和 ~~删除~~",
        "全角＂引号＂、破折号 — – ‑ 与 '单引号'",
        "![图](https://a.invalid/x_y.png) 以及 [链接](https://b.invalid/z)",
        "ＡＢＣ１２３　混合   空白\n\t换行",
        "",
    ]
    for sample in samples:
        assert MODULE.normalize_text(sample) == V3.normalize_text(sample)


@pytest.mark.parametrize(
    "raw",
    ["T1-alpha-2023.md", "T1-alpha-2023", "/kb/docs/T1-Alpha-2023.MD", " T1-alpha-2023.txt "],
)
def test_document_key_is_stable_across_product_naming(raw):
    assert MODULE.document_key(raw) == "t1-alpha-2023"


def test_document_key_distinguishes_siblings():
    assert MODULE.document_key(DOC_A) != MODULE.document_key(DOC_B)


# --------------------------------------------------------------------------
# 后端 profile
# --------------------------------------------------------------------------

def test_builtin_profiles_load():
    for name in ("arag", "dify-console", "dify-dataset-api"):
        profile = MODULE.load_profile(name)
        assert profile.name == name
        assert profile.source_sha256


def test_unknown_profile_raises():
    with pytest.raises(MODULE.ProfileError):
        MODULE.load_profile("不存在的产品")


def test_build_request_keeps_placeholder_types():
    profile = MODULE.load_profile("dify-dataset-api")
    payload = MODULE.build_request(profile, "查询", "DS-1", 20)
    assert payload["query"] == "查询"
    assert payload["retrieval_model"]["top_k"] == 20
    assert isinstance(payload["retrieval_model"]["top_k"], int)


def generic_profile(tmp_path):
    """第三种响应形状：扁平数组 + 平铺字段，用于验证适配层不依赖具体产品。"""
    path = tmp_path / "generic.json"
    path.write_text(json.dumps({
        "name": "generic",
        "url_template": "https://api.invalid/{dataset_id}/search",
        "auth": "none",
        "request_template": {"q": "{query}", "size": "{top_k}"},
        "records_path": "data.hits",
        "field_map": {
            "document_name": "file",
            "content": "text",
            "relevance_score": "sim",
            "chunk_id": "id",
        },
    }, ensure_ascii=False), encoding="utf-8")
    return MODULE.load_profile(str(path))


def test_parse_response_three_shapes(tmp_path):
    dify = MODULE.load_profile("dify-dataset-api")
    chunks, _ = MODULE.parse_response(dify, {"records": [
        {"score": 0.9, "segment": {"id": "c1", "document_id": "d1", "position": 2,
                                   "content": "正文", "document": {"name": DOC_A}}}
    ]})
    assert (chunks[0]["document_key"], chunks[0]["relevance_score"], chunks[0]["rank"]) == (
        "t1-alpha-2023", 0.9, 1
    )

    arag = MODULE.load_profile("arag")
    chunks, _ = MODULE.parse_response(arag, {"success": True, "content": [
        {"top": 1, "relevanceScore": 0.8, "documentName": DOC_A, "chunkId": "c",
         "documentId": "d", "content": "正文"}
    ]})
    assert chunks[0]["document_key"] == "t1-alpha-2023"

    generic = generic_profile(tmp_path)
    chunks, _ = MODULE.parse_response(generic, {"data": {"hits": [
        {"file": DOC_B, "text": "正文", "sim": 0.5, "id": "z"}
    ]}})
    assert (chunks[0]["document_key"], chunks[0]["document_id"]) == ("t2-alpha-2025", "")


def test_parse_response_propagates_backend_failure():
    arag = MODULE.load_profile("arag")
    with pytest.raises(MODULE.RetrievalError):
        MODULE.parse_response(arag, {"success": False, "responseMessage": "配额不足"})


def test_parse_response_rejects_wrong_shape():
    dify = MODULE.load_profile("dify-dataset-api")
    with pytest.raises(MODULE.ResponseSchemaError):
        MODULE.parse_response(dify, {"records": {"not": "a list"}})


# --------------------------------------------------------------------------
# 考卷校验
# --------------------------------------------------------------------------

def test_valid_exam_passes(tmp_path):
    exam, validation = MODULE.load_and_validate_exam(write_exam(tmp_path))
    assert validation["role_counts"] == {"sanity": 1, "scored": 3, "unanswerable": 1}
    assert validation["total_claims"] == 9
    assert validation["chain_question_counts"] == {"CH-T1": 1}
    assert validation["protocol"]["primary_k"] == 3
    assert validation["declared_document_keys"][:2] == ["t1-alpha-2023", "t2-alpha-2025"]
    assert not validation["corpus_drift"]


def test_single_span_text_claim_rejected(tmp_path):
    def mutate(exam):
        exam["questions"][0]["claims"][0]["accepted_spans"] = ["门到球囊时间上限为 90 分钟"]
    with pytest.raises(MODULE.ExamValidationError, match="至少需要 2 个 accepted_span"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_ambiguous_span_rejected(tmp_path):
    """span 在源文档里出现多次时定位有歧义，必须拒绝。"""
    def mutate(exam):
        exam["questions"][0]["claims"][0]["accepted_spans"] = [
            "超时需在系统内登记原因并上报质控组备案", "90 分钟内完成球囊扩张即视为达标",
        ]
        # 该句在两篇兄弟语料里都存在，但同文档内仍唯一；改成文档内重复的片段
        exam["questions"][0]["claims"][0]["accepted_spans"][0] = "分钟"
    with pytest.raises(MODULE.ExamValidationError):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_missing_span_rejected(tmp_path):
    def mutate(exam):
        exam["questions"][0]["claims"][0]["accepted_spans"][0] = "语料里并不存在的一句话内容"
    with pytest.raises(MODULE.ExamValidationError, match="span 不存在于"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_undeclared_cluster_rejected(tmp_path):
    def mutate(exam):
        exam["questions"][1]["cluster_id"] = "C-未声明"
    with pytest.raises(MODULE.ExamValidationError, match="cluster_id 未在"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_question_counts_mismatch_rejected(tmp_path):
    def mutate(exam):
        exam["exam_meta"]["question_counts"]["scored"] = 5
    with pytest.raises(MODULE.ExamValidationError, match="question_counts 与实际不一致"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_span_reuse_across_questions_rejected(tmp_path):
    def mutate(exam):
        exam["questions"][2]["claims"][0]["accepted_spans"] = [
            "首次评分阈值设置为 0.71", "0.71 以下一律转人工",
        ]
        exam["questions"][1]["negative_evidence"] = []
        exam["questions"][2]["claims"][1]["accepted_spans"] = [
            "首次评分阈值设置为 0.71", "0.71 以下一律转人工",
        ]
    with pytest.raises(MODULE.ExamValidationError, match="复用了原子证据 span"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_low_lexical_overlap_tag_enforced(tmp_path):
    def mutate(exam):
        exam["questions"][1]["tags"] = ["low_lexical_overlap"]
        exam["questions"][1]["question"] = "首次评分阈值设置为 0.62 是多少？"
    with pytest.raises(MODULE.ExamValidationError, match="low_lexical_overlap 不成立"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_corpus_drift_blocks_run(tmp_path):
    def mutate(exam):
        exam["exam_meta"]["corpus"]["documents"][0]["sha256"] = "0" * 64
    path = write_exam(tmp_path, mutate)
    with pytest.raises(MODULE.ExamValidationError, match="SHA-256 不一致"):
        MODULE.load_and_validate_exam(path)
    _, validation = MODULE.load_and_validate_exam(path, allow_corpus_drift=True)
    assert validation["corpus_drift"] is True


# --------------------------------------------------------------------------
# Claim 匹配
# --------------------------------------------------------------------------

def chunk(doc, content, score=0.9, index=1):
    return {
        "original_index": index,
        "rank": index,
        "server_top": index,
        "relevance_score": score,
        "document_name": doc,
        "document_key": MODULE.document_key(doc),
        "document_id": "d",
        "chunk_id": f"c{index}",
        "content": content,
    }


def load_questions(tmp_path):
    exam, _ = MODULE.load_and_validate_exam(write_exam(tmp_path))
    return {q["id"]: q for q in exam["questions"]}


def test_second_span_can_carry_the_hit(tmp_path):
    """多 span 的意义：命中任一合法证据位置即可，并记录命中的是哪一个。"""
    question = load_questions(tmp_path)["t_q001"]
    chunks = [chunk(DOC_A, "- 时限口径：90 分钟内完成球囊扩张即视为达标")]
    detail = MODULE.compute_metrics_at_k(question, chunks, 3)["claim_detail"][0]
    assert detail["hit"] is True
    assert detail["matched_span_index"] == 1
    assert detail["matched_span"] == "90 分钟内完成球囊扩张即视为达标"


def test_right_span_wrong_document_is_not_a_hit(tmp_path):
    question = load_questions(tmp_path)["t_q001"]
    chunks = [chunk(DOC_B, "门到球囊时间上限为 90 分钟")]
    metrics = MODULE.compute_metrics_at_k(question, chunks, 3)
    assert metrics["claim_recall"] == 0.0
    assert metrics["claim_detail"][0]["document_hit_claim_miss"] is False


def test_document_alias_map_rescues_renamed_documents(tmp_path):
    question = load_questions(tmp_path)["t_q001"]
    renamed = [chunk("知识库/T1_alpha_2023 (1).md", "门到球囊时间上限为 90 分钟")]
    assert MODULE.compute_metrics_at_k(question, renamed, 3)["claim_recall"] == 0.0
    aliased = MODULE.apply_document_aliases(
        copy.deepcopy(renamed), {"知识库/T1_alpha_2023 (1).md": DOC_A}
    )
    assert MODULE.compute_metrics_at_k(question, aliased, 3)["claim_recall"] == 1.0


def test_anchor_and_passage_claims_are_counted_separately(tmp_path):
    question = load_questions(tmp_path)["t_q002"]
    chunks = [
        chunk(DOC_A, "首次评分阈值设置为 0.62，低于该值的申请直接转人工复核", index=1),
        chunk(DOC_B, "门到球囊时间上限为 75 分钟", score=0.8, index=2),
    ]
    metrics = MODULE.compute_metrics_at_k(question, chunks, 3)
    assert metrics["anchor_claims"] == {"count": 1, "hits": 1}
    assert metrics["passage_claims"] == {"count": 2, "hits": 1}
    assert metrics["claim_recall"] == round(2 / 3, 4)


def test_asset_claim_matches_markdown_image_path(tmp_path):
    question = load_questions(tmp_path)["t_q002"]
    chunks = [chunk(DOC_A, f"- 流程图示：![处置流程]({IMAGE_URL})")]
    detail = MODULE.compute_metrics_at_k(question, chunks, 3)["claim_detail"][2]
    assert detail["hit"] is True


def test_hard_negative_intrusion_and_off_target(tmp_path):
    question = load_questions(tmp_path)["t_q002"]
    chunks = [
        chunk(DOC_B, "首次评分阈值设置为 0.71，低于该值的申请直接转人工复核", index=1),
        chunk("Z-无关文档.md", "完全无关的内容", score=0.4, index=2),
    ]
    metrics = MODULE.compute_metrics_at_k(question, chunks, 3)
    assert metrics["hard_negative_intrusion"] is True
    assert metrics["off_target_chunk_rate"] == 0.5


def test_char_budget_binds_and_lowers_recall(tmp_path):
    """分段中立视图：预算耗尽后靠后的证据拿不到，召回必须随之下降。"""
    question = load_questions(tmp_path)["t_q003"]
    chunks = [
        chunk(DOC_B, "填充" * 30 + "首次评分阈值设置为 0.71", index=1),
        chunk(DOC_B, "本版相对上一版收紧了时限，同时把复核率目标定为 12%", score=0.7, index=2),
    ]
    metrics = MODULE.compute_question_metrics(question, chunks, [3], [40, 400], False)
    assert metrics["char_budget_metrics"]["400"]["claim_recall"] == 1.0
    assert metrics["char_budget_metrics"]["40"]["claim_recall"] == 0.0


def test_relevance_counterfactual_handles_empty_and_missing_scores():
    assert MODULE.relevance_counterfactual([]) == []
    scored = [chunk(DOC_A, "a", score=0.2, index=1), chunk(DOC_A, "b", score=0.9, index=2)]
    assert [c["chunk_id"] for c in MODULE.relevance_counterfactual(scored)] == ["c2", "c1"]
    unscored = [chunk(DOC_A, "a", score=None, index=1)]
    assert MODULE.relevance_counterfactual(unscored) is None


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------

def test_mann_whitney_auc_edges():
    inf = float("-inf")
    assert MODULE.mann_whitney_auc([0.9, 0.8], [inf, inf]) == 1.0
    assert MODULE.mann_whitney_auc([inf, inf], [0.9, 0.8]) == 0.0
    assert MODULE.mann_whitney_auc([inf], [inf]) == 0.5
    assert MODULE.mann_whitney_auc([0.5], [0.5]) == 0.5
    assert MODULE.mann_whitney_auc([], [0.1]) is None


def test_clustered_bootstrap_is_deterministic_and_cluster_aware():
    pairs = [("a", 1.0), ("a", 1.0), ("b", 0.0), ("b", 0.0)]
    first = MODULE.clustered_bootstrap_ci(pairs, samples=500, seed=7)
    second = MODULE.clustered_bootstrap_ci(pairs, samples=500, seed=7)
    assert first == second
    assert first[0] == 0.0 and first[1] == 1.0
    # 单一 cluster 无法做聚类推断
    assert MODULE.clustered_bootstrap_ci([("a", 1.0), ("a", 0.0)]) is None


def test_clustered_randomization_flips_whole_clusters():
    assert MODULE.clustered_randomization_pvalue({"a": [0.0], "b": [0.0]}) == 1.0
    pvalue = MODULE.clustered_randomization_pvalue(
        {chr(97 + i): [0.5] for i in range(8)}, samples=2000, seed=3
    )
    assert 0.0 < pvalue < 0.05


def test_holm_adjustment_is_monotone():
    assert MODULE.holm_adjust([]) == []
    adjusted = MODULE.holm_adjust([0.01, 0.04, 0.5])
    assert adjusted == sorted(adjusted)
    assert adjusted[0] == 0.03


# --------------------------------------------------------------------------
# 端到端：假后端
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeSession:
    """按题面关键词返回预置片段的假后端。"""

    def __init__(self, router, status_code=200):
        self.router = router
        self.status_code = status_code
        self.calls = []
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        records = [
            {
                "score": score,
                "segment": {
                    "id": f"seg{index}",
                    "document_id": "doc",
                    "position": index,
                    "content": content,
                    "document": {"name": name},
                },
            }
            for index, (name, content, score) in enumerate(self.router(json["query"]), 1)
        ]
        return FakeResponse({"records": records}, self.status_code)


def run_args(tmp_path, **overrides):
    args = MODULE.parse_args(["--exam", str(write_exam(tmp_path))])
    args.dataset_id = "DS-TEST"
    args.dataset_revision = "语料-v4单测"
    args.sleep = 0
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


CHAIN_CHUNKS = [
    (DOC_END1, "言澈于 2022 年 3 月加入砺岩精工，最初负责水下换能器的一致性测试与出厂复检。", 0.94),
    (DOC_BRIDGE, "潮生重工已完成对砺岩精工百分之七十股权的收购，全部交割条件于公告日满足。", 0.9),
    (DOC_END2, "沈砚现任潮生重工船队技术副总裁，负责远洋船舶的导航设备更新与能效改造。", 0.88),
]


def perfect_router(query):
    if "上限" in query and "2023" in query:
        return [(DOC_A, "门到球囊时间上限为 90 分钟", 0.95)]
    if "自动通道准入" in query:
        return [
            (DOC_A, "首次评分阈值设置为 0.62，低于该值的申请直接转人工复核", 0.93),
            (DOC_B, "- 时限口径：75 分钟内完成球囊扩张即视为达标", 0.88),
            (DOC_A, f"- 流程图示：![处置流程]({IMAGE_URL})", 0.71),
        ]
    if "门槛" in query:
        return [
            (DOC_B, "首次评分阈值设置为 0.71，低于该值的申请直接转人工复核", 0.9),
            (DOC_B, "本版相对上一版收紧了时限，同时把复核率目标定为 12%", 0.86),
        ]
    if "言澈" in query:
        return CHAIN_CHUNKS
    return []


def evaluate(tmp_path, router, alias_map=None, **overrides):
    args = run_args(tmp_path, **overrides)
    exam, validation = MODULE.load_and_validate_exam(Path(args.exam))
    profile = MODULE.load_profile("dify-dataset-api")
    protocol = validation["protocol"]
    results, unmatched = MODULE.evaluate_exam(
        exam, validation, FakeSession(router), profile, args, protocol, alias_map or {}
    )
    return exam, validation, protocol, results, unmatched


def summarize(tmp_path, router, **overrides):
    exam, validation, protocol, results, unmatched = evaluate(tmp_path, router, **overrides)
    summary = MODULE.aggregate(
        results, protocol, "completed", "full", validation["corpus_drift"],
        {"dataset_revision_verified": True, "git_dirty": False}, unmatched,
    )
    return exam, protocol, results, summary


def test_end_to_end_perfect_backend(tmp_path):
    _, _, results, summary = summarize(tmp_path, perfect_router)
    assert [r["status"] for r in results] == ["ok"] * 5
    assert summary["headline"]["query_macro_claim_recall"] == 1.0
    assert summary["headline"]["complete_evidence_chain_rate"] == 1.0
    assert summary["sanity"]["pass_rate"] == 1.0
    # 不可答题返回空、可答题有分数 → 完美分离
    assert summary["headline"]["abstention_auc"] == 1.0
    assert summary["comparison_eligible"] is True
    assert summary["diagnostics"]["unmatched_document_names"] == []


def test_abstention_penalises_noise_on_unanswerable(tmp_path):
    def noisy(query):
        hits = perfect_router(query)
        return hits or [(DOC_A, "无关内容", 0.99)]

    _, _, _, summary = summarize(tmp_path, noisy)
    assert summary["headline"]["abstention_auc"] < 0.5
    assert summary["abstention"]["unanswerable_empty_response_rate"] == 0.0


def test_abstention_unavailable_without_scores(tmp_path):
    """产品不返回相关性分数时，分离度必须显式不可用，而不是默默给个假数。"""
    _, _, protocol, results, unmatched = evaluate(tmp_path, perfect_router)
    for record in results:
        metrics = record.get("metrics") or record.get("unanswerable_metrics")
        if metrics and metrics.get("top_score") is not None:
            metrics["top_score"] = None
    summary = MODULE.aggregate(
        results, protocol, "completed", "full", False,
        {"dataset_revision_verified": True, "git_dirty": False}, unmatched,
    )
    assert summary["abstention"]["auc"] is None
    assert "相关性分数" in summary["abstention"]["unavailable_reason"]


def test_preflight_aborts_when_no_document_name_matches(tmp_path):
    def wrong_names(query):
        return [("完全不同的文档.md", "门到球囊时间上限为 90 分钟", 0.9)]

    with pytest.raises(MODULE.DocumentNameMismatch) as excinfo:
        evaluate(tmp_path, wrong_names)
    assert "完全不同的文档.md" in excinfo.value.observed


def test_preflight_can_be_skipped(tmp_path):
    def wrong_names(query):
        return [("完全不同的文档.md", "内容", 0.9)]

    _, _, _, results, unmatched = evaluate(tmp_path, wrong_names, skip_preflight=True)
    assert len(results) == 5
    assert unmatched == ["完全不同的文档.md"]


def test_unmatched_documents_block_comparison(tmp_path):
    def partly_wrong(query):
        hits = perfect_router(query)
        return hits + [("外来文档.md", "噪声", 0.1)] if hits else []

    exam, validation, protocol, results, unmatched = evaluate(tmp_path, partly_wrong)
    summary = MODULE.aggregate(
        results, protocol, "completed", "full", False,
        {"dataset_revision_verified": True, "git_dirty": False}, unmatched,
    )
    assert summary["comparison_eligible"] is False
    assert "unmatched_document_names" in summary["comparison_blockers"]


def test_unverified_revision_and_dirty_tree_block_comparison(tmp_path):
    exam, validation, protocol, results, unmatched = evaluate(tmp_path, perfect_router)
    summary = MODULE.aggregate(
        results, protocol, "completed", "full", False,
        {"dataset_revision_verified": False, "git_dirty": True}, unmatched,
    )
    assert summary["comparison_eligible"] is False
    assert set(summary["comparison_blockers"]) == {"dataset_revision=unverified", "git_dirty"}


def test_request_error_recorded_without_aborting(tmp_path):
    session_state = {"count": 0}

    def flaky(query):
        session_state["count"] += 1
        if session_state["count"] == 2:
            raise MODULE.RetrievalError("模拟失败")
        return perfect_router(query)

    args = run_args(tmp_path, retries=1)
    exam, validation = MODULE.load_and_validate_exam(Path(args.exam))
    profile = MODULE.load_profile("dify-dataset-api")

    class ErrorSession(FakeSession):
        def post(self, url, json=None, timeout=None):
            if json["query"].startswith("两个版本"):
                return FakeResponse({"message": "boom"}, status_code=400)
            return super().post(url, json=json, timeout=timeout)

    results, _ = MODULE.evaluate_exam(
        exam, validation, ErrorSession(perfect_router), profile, args,
        validation["protocol"], {},
    )
    statuses = {r["id"]: r["status"] for r in results}
    assert statuses["t_q002"] == "request_error"
    assert statuses["t_q003"] == "ok"


def test_auth_error_aborts_with_partial_results(tmp_path):
    args = run_args(tmp_path)
    exam, validation = MODULE.load_and_validate_exam(Path(args.exam))
    profile = MODULE.load_profile("dify-dataset-api")

    class AuthSession(FakeSession):
        def post(self, url, json=None, timeout=None):
            return FakeResponse({"message": "denied"}, status_code=403)

    with pytest.raises(MODULE.EvaluationAborted) as excinfo:
        MODULE.evaluate_exam(
            exam, validation, AuthSession(perfect_router), profile, args,
            validation["protocol"], {},
        )
    assert excinfo.value.status == "aborted_auth"
    assert excinfo.value.results[0]["status"] == "auth_error"


def test_protocol_override_is_recorded(tmp_path, capsys):
    args = run_args(tmp_path, primary_k=1)
    _, validation = MODULE.load_and_validate_exam(Path(args.exam))
    protocol, source = MODULE.resolve_protocol(validation["protocol"], args)
    assert protocol["primary_k"] == 1
    assert source["primary_k"] == "cli_override"
    assert source["request_k"] == "exam"
    assert "被命令行覆盖" in capsys.readouterr().err


def test_outputs_written_and_report_mentions_five_metrics(tmp_path):
    exam, protocol, results, summary = summarize(tmp_path, perfect_router)
    profile = MODULE.load_profile("dify-dataset-api")
    args = run_args(tmp_path)
    manifest = MODULE.build_manifest(
        exam, {"exam_sha256": "x" * 64, "corpus_drift": False}, profile, args,
        protocol, {k: "exam" for k in protocol}, "20260804_000000", "full", {},
    )
    out_dir = MODULE.write_outputs(
        tmp_path / "out", exam, summary, results, manifest, "20260804_000000"
    )
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == [
        "manifest_20260804_000000.json", "report_20260804_000000.md",
        "results_20260804_000000.json", "summary_20260804_000000.json",
    ]
    report = (out_dir / "report_20260804_000000.md").read_text(encoding="utf-8")
    for label in ("Query Macro Claim Recall", "Complete Evidence Chain Rate",
                  "Budget Claim Recall", "Abstention AUC", "Bridge Claim Recall",
                  "哨兵桶", "多跳专区"):
        assert label in report
    assert manifest["evaluator_script_sha256"] == MODULE.sha256_file(SCRIPT)
    assert manifest["backend_profile_sha256"] == profile.source_sha256


# --------------------------------------------------------------------------
# 多路比较
# --------------------------------------------------------------------------

def make_payload(tmp_path, router, profile_name="dify-dataset-api", threshold=None):
    exam, protocol, results, summary = summarize(tmp_path, router)
    request_config = {"query": "<query>", "top_k": protocol["request_k"]}
    if threshold is not None:
        request_config["score_threshold"] = threshold
    return {
        "schema_version": "4.1",
        "run_status": "completed",
        "run_scope": "full",
        "comparison_eligible": True,
        "comparison_blockers": [],
        "headline": summary["headline"],
        "sanity": summary["sanity"],
        "abstention": summary["abstention"],
        "multihop": summary["multihop"],
        "exam_meta": exam["exam_meta"],
        "manifest": {
            "exam_id": "考卷-单测-01",
            "exam_sha256": "a" * 64,
            "corpus": exam["exam_meta"]["corpus"],
            "retrieval_protocol": protocol,
            "backend_profile": profile_name,
            "dataset_id": f"DS-{profile_name}",
            "dataset_revision": "语料-v4单测",
            "evaluator_script_sha256": "b" * 64,
            "request_config": request_config,
        },
        "results": results,
    }


def dump(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def weak_router(query):
    if "门槛" in query:
        return [(DOC_B, "首次评分阈值设置为 0.71，低于该值的申请直接转人工复核", 0.9)]
    return perfect_router(query)


def test_compare_two_runs(tmp_path):
    left = dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router))
    right = dump(tmp_path, "b.json", make_payload(tmp_path, weak_router, "arag"))
    comparison = MODULE.compare_runs([left, right])
    recall = comparison["metrics"]["claim_recall"]
    assert comparison["num_paired_questions"] == 3
    assert recall["pairwise"][0]["right_minus_left"] < 0
    assert recall["ranking"][0].startswith("dify-dataset-api")
    assert comparison["request_config_differs"] is False


def test_compare_rejects_same_profile_with_different_config(tmp_path):
    """v3 的翻车点：同一产品两次跑用了不同 score_threshold，差距被当成能力差距。"""
    left = dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router))
    right = dump(
        tmp_path, "b.json",
        make_payload(tmp_path, weak_router, "dify-dataset-api", threshold=0.55),
    )
    with pytest.raises(MODULE.ComparisonError, match="检索配置不一致"):
        MODULE.compare_runs([left, right])
    comparison = MODULE.compare_runs([left, right], allow_config_diff=True)
    assert comparison["same_profile_config_conflict"] == ["dify-dataset-api"]


def test_compare_allows_different_products_with_different_bodies(tmp_path):
    """跨产品比较时请求体天然不同，不应被当作阻断项。"""
    left = dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router))
    right = dump(tmp_path, "b.json", make_payload(tmp_path, weak_router, "arag", threshold=0.55))
    comparison = MODULE.compare_runs([left, right])
    assert comparison["same_profile_config_conflict"] == []
    assert comparison["backend_profiles_differ"] is True
    assert comparison["request_config_differs"] is True


def test_compare_three_runs_applies_holm(tmp_path):
    paths = [
        dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router)),
        dump(tmp_path, "b.json", make_payload(tmp_path, weak_router, "arag")),
        dump(tmp_path, "c.json", make_payload(tmp_path, weak_router, "dify-console")),
    ]
    comparison = MODULE.compare_runs(paths)
    pairwise = comparison["metrics"]["claim_recall"]["pairwise"]
    assert len(pairwise) == 3
    for item in pairwise:
        assert item["holm_adjusted_pvalue"] >= item["paired_randomization_pvalue"]
    assert len(comparison["metrics"]["claim_recall"]["run_means"]) == 3


def test_compare_excludes_errored_questions_instead_of_scoring_zero(tmp_path):
    left = make_payload(tmp_path, perfect_router)
    right = make_payload(tmp_path, perfect_router, "arag")
    for record in right["results"]:
        if record["id"] == "t_q003":
            record["status"] = "request_error"
            record.pop("metrics", None)
    comparison = MODULE.compare_runs([
        dump(tmp_path, "a.json", left), dump(tmp_path, "b.json", right)
    ])
    assert comparison["excluded_questions"] == ["t_q003"]
    assert comparison["num_paired_questions"] == 2
    assert comparison["metrics"]["claim_recall"]["pairwise"][0]["right_minus_left"] == 0.0


def test_compare_rejects_ineligible_run(tmp_path):
    left = make_payload(tmp_path, perfect_router)
    right = make_payload(tmp_path, weak_router, "arag")
    right["comparison_eligible"] = False
    right["comparison_blockers"] = ["dataset_revision=unverified"]
    with pytest.raises(MODULE.ComparisonError, match="标记为不可比较"):
        MODULE.compare_runs([
            dump(tmp_path, "a.json", left), dump(tmp_path, "b.json", right)
        ])


def test_compare_rejects_protocol_mismatch(tmp_path):
    left = make_payload(tmp_path, perfect_router)
    right = make_payload(tmp_path, weak_router, "arag")
    right["manifest"]["retrieval_protocol"] = dict(
        right["manifest"]["retrieval_protocol"], primary_k=1
    )
    with pytest.raises(MODULE.ComparisonError, match="比较字段不一致"):
        MODULE.compare_runs([
            dump(tmp_path, "a.json", left), dump(tmp_path, "b.json", right)
        ])


def test_write_comparison_flags_same_profile_conflict(tmp_path):
    left = dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router))
    right = dump(
        tmp_path, "b.json",
        make_payload(tmp_path, weak_router, "dify-dataset-api", threshold=0.55),
    )
    comparison = MODULE.compare_runs([left, right], allow_config_diff=True)
    text = next(
        MODULE.write_comparison(comparison, tmp_path / "cmp").glob("comparison_*.md")
    ).read_text(encoding="utf-8")
    assert "检索配置不一致" in text
    assert "Holm p" in text


def test_write_comparison_notes_cross_product_caveat(tmp_path):
    left = dump(tmp_path, "a.json", make_payload(tmp_path, perfect_router))
    right = dump(tmp_path, "b.json", make_payload(tmp_path, weak_router, "arag"))
    comparison = MODULE.compare_runs([left, right])
    text = next(
        MODULE.write_comparison(comparison, tmp_path / "cmp").glob("comparison_*.md")
    ).read_text(encoding="utf-8")
    assert "budget_claim_recall" in text
    assert "不同产品" in text


def test_validate_only_cli(tmp_path, capsys):
    exit_code = MODULE.main(["--exam", str(write_exam(tmp_path)), "--validate-only"])
    assert exit_code == 0
    assert "校验通过" in capsys.readouterr().out


def test_cli_requires_dataset_id(tmp_path, capsys):
    exit_code = MODULE.main(["--exam", str(write_exam(tmp_path))])
    assert exit_code == 2
    assert "--dataset-id" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 实体桥接多跳
# --------------------------------------------------------------------------

def test_bridge_document_leaking_endpoint_entity_is_rejected(tmp_path):
    """名称隔离是"真跳"的唯一硬保证：桥接文档一旦出现端点人名，题面就能直接命中它。"""
    def mutate(exam):
        chain = exam["questions"][4]
        # 把中间实体当成端点：它在桥接文档里出现，隔离随即失效
        chain["question"] = "砺岩精工和沈砚之间存在什么间接的职业关联？"
        chain["hop_design"]["endpoint_entities"] = ["砺岩精工", "沈砚"]
    with pytest.raises(MODULE.ExamValidationError, match="泄露端点实体"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_endpoint_entity_must_appear_in_question(tmp_path):
    def mutate(exam):
        exam["questions"][4]["question"] = "这两个人之间存在什么间接的职业关联？"
    with pytest.raises(MODULE.ExamValidationError, match="未在题面出现"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_hop_role_must_match_declared_document_role(tmp_path):
    def mutate(exam):
        exam["questions"][4]["claims"][1]["hop_role"] = "endpoint"
    with pytest.raises(MODULE.ExamValidationError, match="在 hop_design 里是 bridge"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_chain_question_requires_a_bridge_claim(tmp_path):
    def mutate(exam):
        chain = exam["questions"][4]
        chain["claims"] = [chain["claims"][0], chain["claims"][2]]
        chain["evidence_chain"] = [c["id"] for c in chain["claims"]]
    with pytest.raises(MODULE.ExamValidationError, match="至少需要 1 个 bridge claim"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_chain_question_requires_two_endpoint_claims(tmp_path):
    def mutate(exam):
        chain = exam["questions"][4]
        chain["claims"] = chain["claims"][:2]
        chain["evidence_chain"] = [c["id"] for c in chain["claims"]]
    with pytest.raises(MODULE.ExamValidationError, match="至少需要 2 个 endpoint claim"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_non_chain_question_cannot_declare_hop_fields(tmp_path):
    def mutate(exam):
        exam["questions"][1]["hop_design"] = exam["questions"][4]["hop_design"]
    with pytest.raises(MODULE.ExamValidationError, match="非链式题不应声明 hop_design"):
        MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))


def test_bridge_and_endpoint_recall_are_split(tmp_path):
    question = load_questions(tmp_path)["t_q005"]
    chunks = [chunk(doc, text, score, i) for i, (doc, text, score) in enumerate(CHAIN_CHUNKS, 1)]
    metrics = MODULE.compute_metrics_at_k(question, chunks, 3)
    assert metrics["endpoint_claims"] == {"count": 2, "hits": 2}
    assert metrics["bridge_claims"] == {"count": 1, "hits": 1}
    assert metrics["path_status"] == "complete"
    assert metrics["bridge_only_miss"] is False


def test_missing_bridge_is_the_signature_multihop_failure(tmp_path):
    """两端都找到、连接材料没找到 —— 检索器停在了第一跳。"""
    question = load_questions(tmp_path)["t_q005"]
    endpoints_only = [
        chunk(DOC_END1, CHAIN_CHUNKS[0][1], 0.94, 1),
        chunk(DOC_END2, CHAIN_CHUNKS[2][1], 0.88, 2),
    ]
    metrics = MODULE.compute_metrics_at_k(question, endpoints_only, 3)
    assert metrics["endpoint_claims"]["hits"] == 2
    assert metrics["bridge_claims"]["hits"] == 0
    assert metrics["path_status"] == "bridge_missing"
    assert metrics["bridge_only_miss"] is True


def bridge_missing_router(query):
    if "言澈" in query:
        return [CHAIN_CHUNKS[0], CHAIN_CHUNKS[2]]
    return perfect_router(query)


def test_multihop_block_reports_gap_and_miss_rate(tmp_path):
    _, _, _, summary = summarize(tmp_path, bridge_missing_router)
    multihop = summary["multihop"]
    assert multihop["chain_questions"] == 1
    assert multihop["endpoint_claim_recall"] == 1.0
    assert multihop["bridge_claim_recall"] == 0.0
    assert multihop["bridge_only_miss_rate"] == 1.0
    assert multihop["path_status"] == {"bridge_missing": 1}
    assert multihop["by_chain"]["CH-T1"]["count"] == 1
    assert summary["headline"]["bridge_claim_recall"] == 0.0


def test_multihop_block_empty_when_exam_has_no_chains(tmp_path):
    def mutate(exam):
        exam["questions"] = exam["questions"][:4]
        counts = exam["exam_meta"]["question_counts"]
        counts.update(total=4, scored=2)
        counts["by_primary_type"].pop("cross_doc_chain")
        counts["by_difficulty"]["hard"] = 1
        exam["exam_meta"]["bridge_chains"] = []
        exam["exam_meta"]["design_constraints"]["min_claims_per_document"] = 0
    exam, validation = MODULE.load_and_validate_exam(write_exam(tmp_path, mutate))
    summary = MODULE.aggregate(
        [], validation["protocol"], "completed", "full", False,
        {"dataset_revision_verified": True, "git_dirty": False}, [],
    )
    assert summary["multihop"]["chain_questions"] == 0
    assert summary["headline"]["bridge_claim_recall"] is None
