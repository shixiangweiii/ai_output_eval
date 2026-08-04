# RAG 检索评测工作区

这是一个面向知识库检索能力的轻量评测仓库。它回答的问题是：**面对一组问题，检索后端能否在 Top-K 结果中返回组成答案所必需的证据？**

当前脚本只评测检索与证据闭环，不评测最终答案是否正确、推理是否合理、是否拒答、会话记忆或工具调用。考卷中的参考答案和证据链主要用于人工核验与定义 Ground Truth。

## 当前结构

```text
ai_output_eval/
├── 评测脚本/
│   ├── retrieval_eval_v4.py               # 当前评测器，schema/metrics 4.1
│   ├── 后端配置/*.json                     # 每个 RAG 产品一份 profile
│   ├── 考卷生成/                           # 考卷生成流水线（台账→语料→考卷→闸门）
│   ├── retrieval_eval.py                  # 已冻结的 v3.0 评测器
│   ├── entity_bridge_multihop_eval.py     # v3 时代的实体桥接多跳专项
│   └── requirements*.txt                  # 运行 / 测试 / 手工示例依赖
├── tests/
│   ├── test_retrieval_eval_v4.py          # 当前评测器单测
│   ├── test_exam_builder.py               # 生成流水线单测
│   ├── fixtures/语料-v4单测/               # 5 篇夹具语料（2 兄弟 + 3 篇桥接链）
│   ├── test_retrieval_eval.py             # 冻结的 v3 单测
│   └── test_entity_bridge_multihop_eval.py
├── 生成的原始文档语料/
│   ├── 2026-08-04-02-真多跳/              # 当前 v4 语料，26 篇
│   ├── 2026-08-04-01-非真正多跳/          # v4 前身，10 篇（链式题实为并行召回）
│   ├── 2026-07-14-01/                     # v3 考卷语料，10 篇
│   └── 2026-07-17-多跳-无词面重合/         # 多跳专项语料，10 篇 + 10 张 PNG
├── 评测考试/
│   ├── 考卷-v4-含真多跳题/                 # 当前考卷（schema 4.1）
│   ├── 考卷-v4-已废弃/                     # 被取代的考卷，勿改（哈希被运行记录引用）
│   ├── 考卷-v3/                            # 冻结的 v3 考卷
│   ├── 考卷-多跳专项/                       # 实体桥接多跳考卷
│   └── 考试结果-*/                         # 历次运行归档
├── 评测考试所测检索召回接口/                 # 手工接口材料，不参与 pytest
└── 评测相关参考文档/                        # 方法论与历史设计资料
```

**目录名承载结论**（`-真多跳` / `-非真正多跳` / `-已废弃`）。重命名目录时，改台账的 `corpus_relative_dir` 后重建考卷，不要手改考卷 JSON。

## 三个评测脚本

### 当前：通用检索评测 v4.1

[`评测脚本/retrieval_eval_v4.py`](评测脚本/retrieval_eval_v4.py) 是当前评测行为的事实来源，自包含、用于**横向评测不同 RAG 产品**：

- **配置驱动的后端适配层**：接新产品只写一份 JSON profile，不改代码。
- **文档名闸门**：按归一化 key 匹配文档；若返回的文档名与考卷声明完全对不上，直接 exit 4 中止——否则该产品会静默全盘零分。
- **五项并列主指标**（见下），全部按 `cluster_id` 做聚类 bootstrap。
- **N 路比较**：同一 profile 的多次运行若检索配置不同，直接拒绝。

默认考卷目录 `评测考试/考卷-v4-含真多跳题/`。

### 已冻结：v3.0 通用评测

[`评测脚本/retrieval_eval.py`](评测脚本/retrieval_eval.py) 保留只为两件事：让 `考试结果-v3-*` 归档仍可复现，以及作为多跳专项的 `core` 依赖。**不要新增 v3 运行。**

### v3 时代的实体桥接多跳专项

[`评测脚本/entity_bridge_multihop_eval.py`](评测脚本/entity_bridge_multihop_eval.py) 复用 v3 核心，评估端点/桥接/辅助文档召回、桥接路径闭环、图片引用路径，并按 `chain_id` 聚类。默认考卷目录 `评测考试/考卷-多跳专项/`；正式联网运行必须显式提供 `--dataset-id`。

它的**实体桥接 + 名称隔离**设计后来被移植进了 v4，成为 v4 的 `cross_doc_chain` 题型。

## 环境准备

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt
```

手工大模型接口示例另需 `.venv/bin/python -m pip install -r 评测脚本/requirements-examples.txt`。

## 离线检查

以下命令不读取凭证，也不访问网络：

```bash
.venv/bin/python -m py_compile 评测脚本/*.py 评测脚本/考卷生成/*.py
.venv/bin/python -m pytest tests/ -q
.venv/bin/python 评测脚本/retrieval_eval_v4.py --validate-only --exam-dir 评测考试/考卷-v4-含真多跳题
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
.venv/bin/python 评测脚本/entity_bridge_multihop_eval.py --validate-only
```

校验会检查 Schema、语料 SHA-256、每个 accepted span 是否存在于对应文档且**在文档内唯一**，以及多跳题的**桥接文档是否泄露端点实体**。

这些离线检查只能证明本地结构、指标代码和考卷快照一致，不能证明真实后端可访问、鉴权有效或在线检索质量达标。

## 运行评测

后端由 profile 选择，凭证一律走环境变量：

```bash
DIFY_DATASET_API_KEY=... \
  .venv/bin/python 评测脚本/retrieval_eval_v4.py \
  --backend-profile dify-hybrid-parentchild \
  --dataset-id <dataset-id> \
  --dataset-revision 2026-08-04-02 \
  --exam "$(pwd)/评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json" \
  --out-dir 评测考试/考试结果-v4-<配置名>
```

aRAG 用 Cookie/CSRF：

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval_v4.py \
  --backend-profile arag --dataset-id <dataset-id> ...
```

`--exam` 若用相对路径会相对 `--exam-dir` 解析，建议传绝对路径。K 与字符预算由考卷的 `retrieval_protocol` 决定，命令行覆盖会告警并记进 manifest。**`--out-dir` 建议按"配置"而非"产品"命名**——比较闸门关心的是配置。

## 重建语料与考卷

全程离线、确定性：

```bash
.venv/bin/python 评测脚本/考卷生成/synthesize_corpus.py --facts-out 评测脚本/考卷生成/事实台账-facts-02.json
.venv/bin/python 评测脚本/考卷生成/extract_spans.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02.json --facts 评测脚本/考卷生成/事实台账-facts-02.json
.venv/bin/python 评测脚本/考卷生成/build_exam.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json --out 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json
.venv/bin/python 评测脚本/考卷生成/audit_exam.py --exam 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json
.venv/bin/python 评测脚本/考卷生成/screen_candidates.py --exam 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json --check-bridge-unreachable
```

考卷是**事实台账的确定性投影**：作者只写题面和引用了哪条事实，`accepted_spans` 由脚本从写好的语料里反向抽取，不是人工挑句子。任何语料重建都会改变 SHA-256，必须重建考卷**并重新灌库**。

## 指标如何理解

### Claim 命中规则

一个 Claim 只有同时满足以下条件才算命中：

1. 检索块所属文档的归一化 key 与 Claim 的 `source_document` 一致（去路径、去扩展名、小写）。
2. 至少一个 `accepted_span` 经统一规范化后是该块内容的子串。
3. 该块位于被评估的 Top-K 窗口内。

"返回了正确文档但没返回正确片段"不算命中，会进 `document_hit_claim_miss` 复核队列。

### 五项并列主指标

| 指标 | 含义 |
|---|---|
| Query Macro Claim Recall@k | 逐题 Claim 召回的宏平均 |
| Complete Evidence Chain Rate@k | 全部 Claim 命中的题占比 |
| **Budget Claim Recall@字符预算** | 固定归一化字符预算下的召回——**唯一对分段中立的读数** |
| **Abstention AUC** | 可答题与不可答题 top-1 分数的分离度，与阈值配置无关 |
| **Bridge Claim Recall@k** | 只统计桥接 claim 的召回——**能否做真多跳的单一读数** |

**这五个数并列，绝不加权汇总成总分。** 量纲也不同：AUC 的随机基线是 0.5，不是 0。

因为子串匹配天然奖励大块分段，任何跨产品结论都必须同时引用 Budget Claim Recall。判断多跳是否真的发生，看 `endpoint_claim_recall` 与 `bridge_claim_recall` 的**落差**：两者接近说明桥接文档本来就能被题面直接召回，这一跳是假的。

### 请求深度与计分窗口

- `request_k`：向后端请求多少条；aRAG 不支持请求侧 K，返回多少算多少。
- `eval_ks` / `primary_k`：客户端评估窗口，与请求深度无关。
- 一次请求返回 20 条，不代表正式分数按 Top-20 计算。

## 结果文件

每次运行写入 `<out-dir>/<exam-id>/<timestamp>/`：`results_*.json`（逐题结果 + 截断后的原始响应）、`summary_*.json`（主指标、多跳专区、分组指标、K 曲线）、`manifest_*.json`（Git、考卷/语料哈希、**评测器脚本哈希**、profile 哈希、dataset revision、请求配置）、`report_*.md`。

冒烟运行、鉴权中止、语料漂移、文档名不匹配都会显式标记。凭证与请求头永不落盘。

## 比较不同产品

```bash
.venv/bin/python 评测脚本/retrieval_eval_v4.py --compare <结果A.json> <结果B.json> [<结果C.json> ...]
```

比较要求所有运行都是完成的整卷运行，且匹配考卷哈希、语料、检索协议和计分题顺序。此外：

- **同一 profile 的多次运行若 `request_config` 不同，直接报错拒绝**（可用 `--allow-config-diff` 强制）。不同产品请求体本就不同，不构成阻断，但报告会提示差异包含产品自带策略。
- `dataset_revision` 未验证、语料漂移、Git 工作区不干净、存在未匹配文档名，任一成立即标记为不可比较。
- 任一运行出错的题目会被**剔除**，而不是记 0 分。

这条闸门有实际教训：v3 时期两次 Dify 运行相差 0.1204、`p=0.0079`，把 `score_threshold` 对齐后只剩 0.0278——**77% 的"结论"来自配置差异**。

## 接口调用示例

[`评测考试所测检索召回接口`](评测考试所测检索召回接口) 保存手工接口材料（aRAG、Dify、DEAP 智能体问答、GLM/Kimi 示例）。这些文件不属于 pytest，不会在离线测试中联网。

## 安全与维护

- aRAG 与 Dify Console 凭证走 `RETRIEVAL_COOKIE` / `RETRIEVAL_XSRF_TOKEN`，Dify Dataset API 走 `DIFY_DATASET_API_KEY`，模型示例走 `DASHSCOPE_API_KEY`。后端 profile 里只写环境变量名。
- 不要提交 Cookie、Token、API Key、私有文档或未脱敏响应。
- 修改本地语料后必须重新灌库，否则本地文件变化不影响检索结果。
- `retrieval_eval_v4.normalize_text` 必须与 v3 实现逐字一致（有单测锁定）；两者若要改，同一次提交里一起改。
- 改指标、Schema、后端、比较规则或输出格式时同步改对应测试，至少跑编译、完整 pytest 和三套 `--validate-only`；改考卷或语料还要跑 `audit_exam.py` 与 `screen_candidates.py --check-bridge-unreachable`。
