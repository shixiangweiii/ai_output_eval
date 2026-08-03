# RAG 检索评测工作区

这是一个面向知识库检索能力的轻量评测仓库。它回答的问题是：**面对一组问题，检索后端能否在 Top-K 结果中返回组成答案所必需的证据？**

当前脚本只评测检索与证据闭环，不评测最终答案是否正确、推理是否合理、是否拒答、会话记忆或工具调用。考卷中的参考答案和证据链主要用于人工核验与定义 Ground Truth。

## 当前结构

```text
ai_output_eval/
├── 评测脚本/
│   ├── retrieval_eval.py                 # 通用 Claim 评测核心，metrics v3.0
│   ├── entity_bridge_multihop_eval.py    # 实体桥接多跳专项评测
│   ├── requirements.txt                  # 运行依赖
│   ├── requirements-dev.txt              # 离线测试依赖
│   └── requirements-examples.txt         # 手工模型接口示例依赖
├── tests/
│   ├── test_retrieval_eval.py
│   └── test_entity_bridge_multihop_eval.py
├── 生成的原始文档语料/
│   ├── 2026-07-14-01/                    # 通用 v3 考卷语料，10 篇
│   └── 2026-07-17-多跳-无词面重合/       # 多跳语料，10 篇 + 10 张 PNG
├── 评测考试/
│   ├── 考卷-v3/                          # 通用 Claim 考卷
│   ├── 考卷-多跳/                        # 实体桥接多跳考卷
│   ├── 考试结果-v3-<backend>/            # 运行时自动创建，产物不入库
│   └── 考试结果-多跳-<backend>/          # 同上，多跳专项产物
├── 评测考试所测检索召回接口/
│   ├── ARAG检索召回/
│   ├── Dify检索召回测试/
│   ├── DEAP智能体问答/
│   └── 大模型接口调用示例/                # 手工联网示例，不参与 pytest
└── 评测相关参考文档/                      # 方法论与历史设计资料
```

旧 metrics-v2 脚本、`评测考试/考卷/` 及对应历史结果已退出当前运行链路。现在 [`评测脚本/retrieval_eval.py`](评测脚本/retrieval_eval.py) 是通用评测行为的事实来源。

结果目录由脚本按 `--backend` 或 `--out-dir` 在首次运行时创建；仓库不携带历史运行档案，需要基线时请自行跑一次整卷。

## 两个评测入口

### 通用检索评测

[`评测脚本/retrieval_eval.py`](评测脚本/retrieval_eval.py) 使用 metrics v3.0。它以原子 Claim 为 Ground Truth，支持：

- aRAG 与 Dify 响应归一化。
- Top-K Claim、证据链和排序指标。
- 文档召回、难负例、重复文档、字符预算和延迟诊断。
- 语料 SHA-256 与 accepted span 存在性校验。
- 可复现实验 Manifest。
- 配对 bootstrap 置信区间与随机化检验。

默认考卷目录为 `评测考试/考卷-v3/`，默认输出目录为 `评测考试/考试结果-v3-<backend>/`。

### 实体桥接多跳专项

[`评测脚本/entity_bridge_multihop_eval.py`](评测脚本/entity_bridge_multihop_eval.py) 复用通用核心的请求、归一化和 Claim 指标，另外评估：

- 端点文档、桥接文档和辅助文档的召回。
- 核心桥接路径是否闭环。
- 图片引用路径是否被检索到。
- 按 `chain_id` 聚类的链级汇总与配对比较。

默认考卷目录为 `评测考试/考卷-多跳/`，默认输出目录为 `评测考试/考试结果-多跳-<backend>/`。正式联网运行必须显式提供已导入多跳语料的 `--dataset-id`。

## 环境准备

从仓库根目录执行，复用项目内 `.venv`：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt
```

仅运行手工大模型接口示例时，额外安装：

```bash
.venv/bin/python -m pip install -r 评测脚本/requirements-examples.txt
```

## 离线检查

以下命令不读取凭证，也不访问网络：

```bash
.venv/bin/python -m py_compile \
  评测脚本/retrieval_eval.py \
  评测脚本/entity_bridge_multihop_eval.py

.venv/bin/python -m pytest tests/ -q

.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
.venv/bin/python 评测脚本/entity_bridge_multihop_eval.py --validate-only
```

通用校验会检查 JSON Schema、语料文件集合、SHA-256 和每个 accepted span 是否存在于对应文档。多跳校验还会核对链设计、PNG 文件集合、PNG CRC、尺寸和图片声明哈希。

这些离线检查只能证明本地结构、指标代码和考卷快照一致，不能证明真实后端可访问、鉴权有效或在线检索质量达标。

## 运行通用评测

### aRAG 冒烟运行

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend arag \
  --exam 评测考试/考卷-v3/考卷-2026-07-15-03.json \
  --limit 3 \
  --primary-k 5
```

### Dify 普通/混合检索

本地 Dify Console 需要可通过 `localhost:5001` 访问：

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --dataset-id <dataset-id> \
  --dataset-revision <revision> \
  --dify-search-method hybrid_search \
  --request-k 10 \
  --primary-k 5
```

Dify Dataset API 使用 Bearer Dataset API Key，不需要 Console Cookie/CSRF：

```bash
DIFY_DATASET_API_KEY=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --dify-api-mode dataset-api \
  --dataset-id <dataset-id> \
  --dataset-revision <revision> \
  --dify-search-method hybrid_search \
  --request-k 5 \
  --eval-k 1,3,5 \
  --primary-k 5 \
  --score-threshold-enabled
```

父子索引响应中的父块 `segment.content` 是正式计分上下文；`child_chunks` 的 ID、位置和分数作为逐题诊断保存。

### Dify Coverage Search

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --dataset-id <coverage-dataset-id> \
  --dataset-revision <revision> \
  --dify-search-method coverage_search \
  --request-k 10 \
  --primary-k 5 \
  --out-dir 评测考试/考试结果-v3-dify-coverage
```

`coverage_search` 不能与 `--graph-search` 同时使用。父子分块不是独立 backend，而是 Dataset 的切分属性，应通过 `--dataset-id` 选择并用 `--out-dir` 单独归档。

## 运行多跳专项

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/entity_bridge_multihop_eval.py \
  --backend dify \
  --dataset-id <multihop-dataset-id> \
  --dataset-revision <revision> \
  --exam 评测考试/考卷-多跳/考卷-2026-07-17-01.json \
  --request-k 10 \
  --primary-k 5
```

多跳脚本拒绝在未提供 `--dataset-id` 时进行真实运行，避免误用通用语料的内置 Dataset。

## 指标如何理解

### Claim 命中规则

每个计分问题包含一个或多个 `claims`。一个 Claim 只有同时满足以下条件才算命中：

1. 检索块的 `document_name` 与 Claim 的 `source_document` 完全相等。
2. 至少一个 `accepted_span` 经过统一文本规范化后，是该检索块内容的子串。
3. 该检索块位于被评估的 Top-K 窗口内。

因此，“返回了正确文档但没有返回正确片段”不会被算作 Claim 命中；Document Recall 应作为独立诊断一起阅读。

### 三个并列主指标

在 `--primary-k`（默认 5）处报告：

- **Query Macro Claim Recall**：先计算每题 Claim Recall，再对计分题等权平均。
- **Complete Evidence Chain Rate**：所有必需 Claims 都命中的问题比例。
- **Novel Claim Rank Score**：越早首次找到新 Claim，得分越高。

这三个指标没有合成为“总分”。它们评估的是检索证据，不是最终答案正确率。

### 请求深度与计分窗口

- `--request-k`：向支持该参数的后端请求多少条结果；Dify 会应用，aRAG 当前不支持请求侧 K。
- `--eval-k`：输出哪些 Top-K 曲线，默认 `1,3,5,10`。
- `--primary-k`：正式主指标使用哪个 Top-K，默认 5。

一次请求返回 10 条并不意味着正式分数按 Top-10 计算。

## 结果文件

每次运行写入 `<out-dir>/<exam-id>/<timestamp>/`：

- `results_<timestamp>.json`：逐题结果、命中/漏召回证据和截断后的原始响应。
- `summary_<timestamp>.json`：主指标、分组指标、K 曲线和运行状态。
- `manifest_<timestamp>.json`：Git、考卷与语料哈希、Dataset revision、请求配置和 Python 版本。
- `report_<timestamp>.md`：便于人工阅读的摘要与诊断。

冒烟运行、鉴权中止、语料漂移和请求错误都会在运行状态或报告中显式标记。不要把 `--limit` 的部分运行与整卷正式结果直接比较。

## 配对比较

```bash
.venv/bin/python 评测脚本/retrieval_eval.py \
  --compare <左侧-results.json> <右侧-results.json>
```

比较要求两次运行均为完成的整卷运行，并匹配考卷、语料、计分窗口和题目顺序。`dataset_revision` 用来记录远端知识库快照；Dataset 或索引发生变化时，应将结果描述为跨索引比较，而不是严格 A/B。

多跳专项比较使用：

```bash
.venv/bin/python 评测脚本/entity_bridge_multihop_eval.py \
  --compare <左侧-results.json> <右侧-results.json>
```

它还要求验证过且一致的 Dataset revision，并以 `chain_id` 作为推断单位。当前独立链数量较少，置信区间和显著性检验应视为探索性证据。

## 接口调用示例

[`评测考试所测检索召回接口`](评测考试所测检索召回接口) 保存手工接口材料：

- aRAG 检索接口格式。
- Dify 检索测试材料。
- DEAP 智能体问答请求和返回格式。
- GLM、Kimi 的 OpenAI 兼容接口示例。

这些文件不属于 pytest，不会在离线测试中联网。手工模型示例使用 `DASHSCOPE_API_KEY`，且只有直接执行脚本时才会发起请求。

## 安全与维护

- aRAG 和 Dify Console 凭证通过 `RETRIEVAL_COOKIE`、`RETRIEVAL_XSRF_TOKEN` 传入；Dify Dataset API Key 通过 `DIFY_DATASET_API_KEY` 传入。
- 模型示例凭证仅通过 `DASHSCOPE_API_KEY` 传入。
- 不要提交 Cookie、Token、API Key、私有文档或未脱敏响应。
- 修改本地语料后，必须重新导入对应后端；否则本地文件变化不会影响检索结果。
- 修改指标、Schema、后端、比较规则或输出格式时，同步修改对应测试。
- 至少运行编译、完整 pytest 和两套 `--validate-only`。
