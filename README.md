# RAG 检索召回评测工作区

这是一个面向 RAG / GraphRAG 的小型、可重复执行的检索评测仓库。项目把“原始语料 → 考卷与 Ground Truth → 在线检索 → 指标计算 → 结果归档 → 跨后端分析”放在同一工作区中，用来判断指定知识库能否在 Top-K 结果里找回回答问题所必需的证据。

当前唯一已实现的评测器是 [`评测脚本/retrieval_eval.py`](评测脚本/retrieval_eval.py)，指标版本为 **metrics v2.0**。它支持钉钉 aRAG 和本地 Dify Console 的 hit-testing 接口，并用同一套指标评测普通 Dify、父子分块 Dify 和 Dify GraphRAG。

> [!IMPORTANT]
> 当前脚本评测的是**检索与证据召回**，不是最终回答质量。它不会给 `answer`、`reasoning_path`、拒答、对话记忆或工具调用打分；这些字段和接口样例是后续评测器的准备材料。

## 项目包含什么

```text
.
├── 评测脚本/
│   ├── retrieval_eval.py          # 评测器，当前运行与指标语义的唯一事实来源
│   ├── requirements.txt           # 运行依赖
│   └── requirements-dev.txt       # 运行依赖 + pytest
├── tests/
│   └── test_retrieval_eval.py     # 离线单元测试，HTTP 全部 mock
├── 生成的原始文档语料/
│   └── 2026-07-14-01/             # 10 篇 Markdown 原始语料
├── 评测考试/
│   ├── 考卷/                      # 3 份 JSON 考卷
│   ├── 考试结果-arag/             # aRAG 运行档案
│   ├── 考试结果-dify/             # Dify 普通分块运行档案
│   ├── 考试结果-dify-父子/        # Dify 父子分块运行档案
│   ├── 考试结果-dify-graphrag/    # Dify GraphRAG 运行档案
│   └── 考试结果分析/              # 人工跨后端分析
├── 相关接口调用例子/              # aRAG、Dify、智能体问答原始接口样例
├── 评测相关参考文档/              # 方法论、语料与考卷生成笔记
├── AGENTS.md                      # 开发、测试和安全约定
└── CLAUDE.md                      # 详细架构与指标说明
```

## 当前能力边界

已实现：

- 校验考卷 JSON 的结构、题号、难度、GT 证据和图片 URL 关系。
- 在线调用 aRAG 或 Dify 检索接口。
- 将 aRAG 的 `content[]` 与 Dify 的 `records[].segment` 归一化为统一 Chunk 结构。
- 评估文档覆盖、GT 证据片段覆盖、排序、重复文档、多模态图片来源覆盖和关键词覆盖。
- 按考卷、难度和题型聚合指标，保存 JSON 明细、JSON 汇总和 Markdown 报告。
- 对瞬时网络错误、HTTP 408/429/5xx 重试；鉴权失败时终止整场并保留部分结果。

尚未实现：

- 最终答案正确性、完整性、忠实度或幻觉评分。
- 推理路径质量、引用表述质量、拒答策略、对话记忆和工具调用评分。
- NDCG、LLM-as-a-Judge 或 Agent 过程评测。
- 自动同步或重新入库本地语料到各个知识库。

仓库中的 [`评测相关参考文档`](评测相关参考文档) 描述了更完整的 Retrieval、Ranking、Generation、Citation、Process 和在线反馈闭环；它们代表方法论目标，不等于当前脚本已经实现的功能。

主要说明材料：

| 文档 | 用途 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 仓库范围、目录约定、运行命令、评测合同、语料入库、测试和安全规范 |
| [`CLAUDE.md`](CLAUDE.md) | 双后端架构、四种已评配置、metrics v2.0 语义和跨后端比较注意事项 |
| [`AI评测.md`](评测相关参考文档/AI评测.md) | 用“出考卷”理解 GT、Case 分布、评分标准和全链路评测设计 |
| [`RAG 评测 MVP 流程认知总结`](<评测相关参考文档/RAG 评测 MVP 流程认知总结 (1).md>) | 从语料、评测集、Benchmark 到 Retrieval/Ranking/Generation/Citation/Process 与反馈闭环的方法论 |
| [`生成评测集`](<评测相关参考文档/生成评测集 (1).md>) | 题目难度定义、考卷生成 Prompt、语料生成 Prompt、线上数据和 Agent 类应用评测设想 |
| [`后端对比分析`](评测考试/考试结果分析/后端对比分析-考卷-2026-07-15-02.md) | 第三份考卷上 aRAG、Dify 普通、Dify 父子和 GraphRAG 的正式整卷比较 |

## 快速开始

所有命令均从仓库根目录执行。优先复用 `.venv`；目录不存在时再创建。

```bash
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt
```

先执行不需要凭证和网络的检查：

```bash
.venv/bin/python -m py_compile 评测脚本/retrieval_eval.py
.venv/bin/python -m pytest tests/
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
```

`--validate-only` 默认校验 [`评测考试/考卷`](评测考试/考卷) 下的全部 JSON。校验单份考卷时，`--exam` 的相对路径是相对于 `--exam-dir` 的，因此推荐只传文件名：

```bash
.venv/bin/python 评测脚本/retrieval_eval.py \
  --exam 考卷-2026-07-15-02.json \
  --validate-only
```

如果要传仓库相对路径，可同时使用 `--exam-dir .`；绝对路径则可直接传给 `--exam`。

在线运行时如果省略 `--exam`，脚本会依次运行 `--exam-dir` 下的全部 JSON；为避免误跑多份正式考卷，通常应显式指定文件名。

## 执行在线评测

两个后端都从环境变量读取凭证：

- `RETRIEVAL_COOKIE`
- `RETRIEVAL_XSRF_TOKEN`

不要把真实 Cookie 或 Token 写进脚本、命令文件、报告或 Git 提交。Dify 使用本地 Console 接口 `http://localhost:5001/console/api/datasets/{dataset_id}/hit-testing`，运行前需保证该服务和对应知识库可访问。

### aRAG 冒烟测试

```bash
RETRIEVAL_COOKIE='...' RETRIEVAL_XSRF_TOKEN='...' \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend arag \
  --exam 考卷-2026-07-15-02.json \
  --limit 3 \
  --top-k 5
```

### Dify 普通分块

```bash
RETRIEVAL_COOKIE='...' RETRIEVAL_XSRF_TOKEN='...' \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --exam 考卷-2026-07-15-02.json \
  --top-k 5
```

### Dify 父子分块

父子分块是知识库自身的索引属性，不是独立的 `--backend` 值；必须显式选择对应 dataset，并指定独立归档目录。

```bash
RETRIEVAL_COOKIE='...' RETRIEVAL_XSRF_TOKEN='...' \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --dataset-id 260f9445-6a2c-431f-9daf-6e29f50d0955 \
  --out-dir 评测考试/考试结果-dify-父子 \
  --exam 考卷-2026-07-15-02.json \
  --top-k 5
```

### Dify GraphRAG

`--graph-search` 只改变 Dify 请求体，不会自动改变输出目录。

```bash
RETRIEVAL_COOKIE='...' RETRIEVAL_XSRF_TOKEN='...' \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify \
  --graph-search \
  --dataset-id 0a8b3810-f2dd-4be3-9cc7-ef525095af16 \
  --out-dir 评测考试/考试结果-dify-graphrag \
  --exam 考卷-2026-07-15-02.json \
  --top-k 5
```

当前已有配置如下：

| 配置 | 后端参数 | dataset ID | 默认/约定输出根目录 |
| --- | --- | --- | --- |
| aRAG | `--backend arag` | `844b8ded-9bf4-44b6-bc1a-a5ccee745832` | `评测考试/考试结果-arag` |
| Dify 普通分块 | `--backend dify` | `fc0250d7-5bd0-4625-b373-830c1c83dc18` | `评测考试/考试结果-dify` |
| Dify 父子分块 | `--backend dify --dataset-id ...` | `260f9445-6a2c-431f-9daf-6e29f50d0955` | 手动指定 `评测考试/考试结果-dify-父子` |
| Dify GraphRAG | `--backend dify --graph-search --dataset-id ...` | `0a8b3810-f2dd-4be3-9cc7-ef525095af16` | 手动指定 `评测考试/考试结果-dify-graphrag` |

常用参数：

| 参数 | 作用 | 默认值/注意事项 |
| --- | --- | --- |
| `--exam-dir` | 考卷目录 | `评测考试/考卷` |
| `--exam` | 只运行一份考卷 | 相对路径按 `--exam-dir` 解析 |
| `--backend` | `arag` 或 `dify` | `arag` |
| `--dataset-id` | 覆盖后端默认知识库 | 建议跨配置对比时始终显式记录 |
| `--top-k` | 客户端参与评分的结果窗口 | `5`；不会改变 aRAG 请求，Dify 请求体内部仍固定 `top_k=5` |
| `--graph-search` | 为 Dify 注入 GraphRAG 配置 | 不会自动切换 dataset 或输出目录 |
| `--limit` | 只运行前 N 题 | `0` 表示整卷；部分运行不可当作整卷成绩 |
| `--rank-by` | 使用响应顺序或按相关度重排 | `response`；可选 `relevance` |
| `--sleep` | 题间限流秒数 | `0.5` |
| `--timeout` | 单次请求超时秒数 | `30` |
| `--retries` | 瞬时失败的最大尝试次数 | `3` |
| `--out-dir` | 覆盖结果根目录 | 默认 `评测考试/考试结果-<backend>` |

完整参数以以下命令为准：

```bash
.venv/bin/python 评测脚本/retrieval_eval.py --help
```

> [!NOTE]
> 当前 `--help` 中“Dify 在线检索暂未实现”的一句说明已经过时；运行代码已将 `dify` 列入在线后端并调用 `call_dify_api`。

## 评测流程与计分口径

主流程为：

```text
main
  → discover_exam_files
  → load_and_validate_exam
  → evaluate_exam
  → 后端请求与响应归一化
  → compute_metrics
  → aggregate
  → write_outputs
```

### Ground Truth 如何命中

每条 GT 证据由 `source_document + snippet` 确定。只有同时满足以下条件才算命中：

1. 召回 Chunk 的 `document_name` 与 GT 的 `source_document` 完全相等。
2. GT `snippet` 经过文本归一化后，是该 Chunk 归一化内容的子串。

文本归一化会执行 Unicode NFKC、破折号与引号折叠、Markdown 图片/链接处理、部分 Markdown 符号删除、转小写和去空白。`section` 只用于报告诊断，不直接参与匹配。

### 主分

单题得分：

```text
score = evidence_recall@k = 命中的 GT 片段数 / 该题 GT 片段总数
```

整卷得分：

```text
retrieval_capability_score = 所有已安排题目的单题 score 等权平均 × 100
```

- 普通请求错误按 0 分计入整卷。
- `simple` / `medium` / `hard` 和题型会单独汇总，但不会重新加权总分。
- 鉴权失败会中止整场，写出 `run_status="aborted_auth"` 的部分档案，并将能力分置为 `null`。
- 图片题只有在每个必需 GT 来源文档里都召回预期 URL，才算完整图片命中。

其余指标均是诊断项：

| 指标 | 含义 |
| --- | --- |
| `doc_recall@k` | 必需 GT 文档在 Top-K 中的覆盖率 |
| `unique_doc_precision@k` | Top-K 去重文档中相关文档的比例 |
| `evidence_precision@k` | Top-K Chunk 中实际包含 GT 证据的比例 |
| `evidence_mrr@k` | 第一条证据命中的倒数排名 |
| `all_evidence_hit@k` | 一道题的全部 GT 片段是否都命中 |
| `duplicate_doc_rate@k` | Top-K 中重复文档占比 |
| `image_source_coverage` | 图片 URL 在所有必需来源文档中的覆盖率 |
| `keyword_coverage` | 关键词或同义备选覆盖，仅诊断，不进主分 |
| `ranking_anomaly` | 服务端响应顺序是否与相关度分数单调性冲突 |

`answer` 和 `reasoning_path` 只供人工复核。旧考卷元数据中出现的“答案正确性约 50%”“简单/中等/困难固定 40/40/20 加权”等描述，不适用于 metrics v2.0。

## 原始语料

当前语料批次位于 [`生成的原始文档语料/2026-07-14-01`](生成的原始文档语料/2026-07-14-01)，共 10 篇 Markdown 文档。语料刻意混合真实世界文档风格、别名与缩写、跨章节依赖、新旧版本冲突、Hard Negative、数据解读陷阱和图片 URL，以制造不同检索难度。

| 编号 | 文档 | 领域与主要评测点 |
| --- | --- | --- |
| 01 | 急性冠脉综合征院内诊疗路径 | 医疗路径、时间窗、版本替代、特殊人群与跨章节决策 |
| 02 | 2 型糖尿病胰岛素治疗与血糖管理科普手册 | 医疗科普、术语解释、剂量个体化、低血糖处置 |
| 03 | 颅脑 MRI 检查在 Chiari 畸形诊断中的应用 | 影像规范、MRI/CT 消歧、量化标准和多跳临床判断 |
| 04 | 口服抗凝药物用药安全警戒简报 | 药物警戒、VKA/NOAC 消歧、相互作用与统计口径变化 |
| 05 | 量化交易策略研发内部笔记 | 金融、均线与成交量、过拟合、跨市场差异和模型指标 |
| 06 | 消费信贷智能风控体系设计说明书 | 风控架构、评分卡、机器学习、召回/精确率与合规 |
| 07 | 上市公司财报分析实务 | 三张表联动、现金流、跨期对比与财务造假识别 |
| 08 | 一网通办政务服务平台业务办理指南 | 政务流程、并联审批、数据共享、版本边界和电子证照 |
| 09 | 在线教育平台自适应学习系统技术白皮书 | 知识图谱、推荐链路、效果归因、冷启动和算法伦理 |
| 10 | 协同文档 SaaS“云笺”多人实时协作 PRD | OT/CRDT 消歧、冲突处理、权限、验收与版本演化 |

本地文件只是语料事实来源，并不等于在线知识库内容。修改语料后，必须把同一版本重新入库到每个待比较的 backend/dataset，否则“未命中”可能只是知识库未同步或索引模式不同。

第三份考卷生成前，05、06、09 三篇文档曾扩充模型精确率/召回率内容，05 还复用了风险管理图片。`考卷-2026-07-15-02` 的 q_005 和 q_012 依赖这些扩充内容；在新 dataset 上执行前尤其需要确认已重新入库。

## 考卷

考卷位于 [`评测考试/考卷`](评测考试/考卷)，命名约定为 `考卷-YYYY-MM-DD-NN.json`。

| 考卷 | 题数 | 难度分布 S/M/H | GT 来源文档数分布 | GT 片段 | 图片题 | 设计重点 |
| --- | ---: | --- | --- | ---: | ---: | --- |
| `考卷-2026-07-14-01` | 20 | 8 / 8 / 4 | 1篇×16、2篇×3、3篇×1 | 35 | 2 | 基线卷：单 Chunk、跨章节、跨文档、版本冲突和多模态 |
| `考卷-2026-07-15-01` | 30 | 3 / 9 / 18 | 1篇×13、2篇×10、3篇×7 | 70 | 4 | 高难综合卷：提高跨文档、多跳、消歧、对抗和同图复用比例 |
| `考卷-2026-07-15-02` | 25 | 0 / 0 / 25 | 1篇×2、2篇×5、3篇×17、4篇×1 | 69 | 4 | 超高难压测卷：低词面重合、3～4 文档召回、多模态与范围边界 |

每道题的主要字段：

```json
{
  "id": "q_001",
  "difficulty": "simple | medium | hard",
  "type": "single_chunk_recall",
  "question": "提交给检索后端的 query",
  "retrieved_chunks": [
    {
      "source_document": "语料文件名.md",
      "section": "人工诊断用章节",
      "snippet": "必须召回的原文证据"
    }
  ],
  "answer": "供人工复核的标准答案",
  "reasoning_path": "供人工复核的证据依赖链",
  "eval_criteria": {
    "keyword_match": ["诊断关键词", "同义词A/同义词B"],
    "expected_image_url": null,
    "requires_tool": false,
    "is_adversarial": false
  }
}
```

新增或修改考卷时应遵守：

- `id` 非空且整卷唯一，`difficulty` 只能是 `simple|medium|hard`。
- `retrieved_chunks` 至少一条，`source_document` 与 `snippet` 必须非空。
- GT `snippet` 应在对应语料中保持逐字可验证，并能通过评测器自己的 `normalize_text` 归一化后匹配。
- `expected_image_url` 必须出现在至少一个 GT `snippet` 中。
- `exam_meta.total_questions` 必须与实际题数一致。
- `--validate-only` 只检查 JSON 结构和字段关系，不会证明 snippet 真存在于本地语料或在线知识库。

## 输出与考试记录

每次运行写入：

```text
<out-dir>/<exam_id>/<YYYYMMDD_HHMMSS>/
├── results_<timestamp>.json   # 每题状态、指标、证据诊断和截断后的原始响应
├── summary_<timestamp>.json   # 整体、难度和题型聚合
└── report_<timestamp>.md      # 便于人工阅读的汇总与逐题证据诊断
```

截至 2026-07-15，仓库保留了以下 11 次运行档案：

| 时间/目录 | 后端或配置 | 考卷 | 已评测 | 状态 | 能力分 | 备注 |
| --- | --- | --- | ---: | --- | ---: | --- |
| `20260715_004658` | aRAG | 07-14-01 | 20 | completed | 94.33 | 未标注 metrics version 的历史旧口径，不与 v2.0 直接横比 |
| `20260715_012433` | aRAG | 07-15-01 | 30 | completed | 86.67 | metrics v2.0 正式整卷 |
| `20260715_111710_冒烟` | aRAG | 07-15-02 | 1 | completed | 66.67 | 部分运行，仅代表 q_001 |
| `20260715_111727` | aRAG | 07-15-02 | 25 | completed | 75.00 | metrics v2.0 正式整卷 |
| `20260715_144436` | Dify 普通 | 07-15-02 | 1 | completed | 100.00 | 部分运行，仅代表 q_001 |
| `20260715_144453` | Dify 普通 | 07-15-02 | 25 | completed | 60.33 | metrics v2.0 正式整卷 |
| `20260715_145104` | Dify 父子 | 07-15-02 | 1 | completed | 66.67 | 部分运行，仅代表 q_001 |
| `20260715_145118` | Dify 父子 | 07-15-02 | 25 | completed | 57.00 | metrics v2.0 正式整卷 |
| `20260715_151522` | Dify GraphRAG | 07-15-02 | 1 | completed | 100.00 | 部分运行，仅代表 q_001 |
| `20260715_151545` | Dify GraphRAG | 07-15-02 | 13 次尝试/12 题有分 | aborted_auth | — | q_013 鉴权失败，整场能力分为 `null` |
| `20260715_151925` | Dify GraphRAG | 07-15-02 | 25 | completed | 67.00 | 刷新凭证后的 metrics v2.0 正式整卷 |

第三份考卷四个可比正式整卷的结果：

| 配置 | 能力分 / Evidence Recall@5 | Document Recall@5 | Evidence Precision@5 | 图片来源覆盖 | 排名异常题数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| aRAG | 75.00 / 0.750 | 0.777 | 0.386 | 0.750 | 25 |
| Dify 普通 | 60.33 / 0.603 | 0.863 | 0.320 | 1.000 | 0 |
| Dify 父子 | 57.00 / 0.570 | 0.807 | 0.316 | 1.000 | 0 |
| Dify GraphRAG | 67.00 / 0.670 | 0.883 | 0.360 | 1.000 | 0 |

详细逐题比较见 [`评测考试/考试结果分析/后端对比分析-考卷-2026-07-15-02.md`](评测考试/考试结果分析/后端对比分析-考卷-2026-07-15-02.md)。解读总分时必须同时看 Document Recall：GT 片段与 aRAG 的切分边界更接近，因此逐字证据主分对 aRAG 有结构性优势；如果只比较不依赖 Chunk 边界的文档覆盖，GraphRAG 在这次正式整卷中最高。

## 已知的历史信息差异

仓库经过“初始评测 → 第三次高难考试 → 多后端支持 → 结果分目录 → 目录重组”的快速演进，部分旧文档或档案保留了当时口径：

- `retrieval_eval.py` 是当前运行行为和指标定义的事实来源。
- 第一份历史报告仍写“难度加权”，且 summary 没有 `metrics_version`；它应作为历史证据保留，不应用当前 v2.0 口径倒推。
- 前两份考卷的 `exam_meta.corpus_dir` 仍是旧 Desktop 绝对路径；实际语料以本仓库 `生成的原始文档语料/2026-07-14-01` 为准。
- 旧考卷元数据和方法论文档描述了答案、推理、引用、拒答或固定难度权重，但当前脚本不计算这些项目。
- 跨后端分析报告第 8 节仍保留已经退役的 `基于语料生成的评测集/` 路径；实际档案均在 `评测考试/考试结果-*`。
- CLI 帮助文本仍称 Dify 在线检索未实现；该句已过时，代码与测试均已有 Dify 在线客户端。

## Git 演进摘要

| 提交 | 备注 | 项目演进 |
| --- | --- | --- |
| `b23d35e` | `init to #000000` | 建立语料、前两份考卷、aRAG 评测器、测试、参考资料和首批结果 |
| `7456bff` | `第三次考试` | 扩充 05/06/09 语料，新增 25 题全困难考卷并改进 v2.0 评测 |
| `3503ca0` | `考试结果` | 归档第三次考试的冒烟与正式 aRAG 结果，并开始按后端区分目录 |
| `253ceb9` | `修改评测脚本支持评测不同的接口，输出评测报告也区分不同的目录` | 引入后端选择、dataset 与按后端输出的基础能力 |
| `d55a4c9` | `目录调整` | 迁移到当前目录结构，加入 Dify 普通/父子/GraphRAG 接口样例、运行档案和跨后端分析 |
| `764324d` | `更新 claude.md` | 对齐 Dify 在线支持、图检索、指标和测试说明 |
| `b2289ed` | `更新 AGENTS.md` | 对齐仓库范围、命令、评测合同、语料入库和安全约定 |

## 开发与验证约定

修改指标、Schema、重试、后端或输出格式时，应同步更新 [`tests/test_retrieval_eval.py`](tests/test_retrieval_eval.py)，并至少执行：

```bash
.venv/bin/python -m py_compile 评测脚本/retrieval_eval.py
.venv/bin/python -m pytest tests/
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
```

在线验证只在需要时运行小规模 `--limit 3` 冒烟。正式跨后端比较必须记录考卷 ID、dataset ID、索引/分块模式、Top-K、`rank_by`、GraphRAG 开关和结果目录，且不能把部分运行与整卷成绩直接比较。

结果 JSON 使用 UTF-8、两空格缩进和 `ensure_ascii=False`。代码、语料/考卷、生成结果和人工分析尽量分别提交；不要提交 `.venv/`、`__pycache__/`、缓存目录或任何真实凭证。

## 接口样例与安全

[`相关接口调用例子`](相关接口调用例子) 保存了 aRAG 检索、Dify 普通/父子/GraphRAG hit-testing，以及未来智能体问答评测所需的请求/响应样例。它们适合用来理解接口形态和响应归一化，不是可直接复用的生产脚本。

部分历史样例可能带有已经失效但形似凭证的 Cookie、CSRF/XSRF Token 或原始响应。不要复制、提交、传播或尝试复用这些值；正式运行只使用当前进程环境变量中的凭证。
