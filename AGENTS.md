# Repository Guidelines

## Project Scope

This repository is a compact RAG retrieval-evaluation workspace. The active evaluator measures whether the selected knowledge base returns the Ground-Truth Claims required by each question. It does **not** score final-answer correctness, reasoning quality, refusal behavior, conversation memory, or tool calls.

`评测脚本/retrieval_eval.py` is the source of truth for the current general evaluator and metrics v3.0. `评测脚本/entity_bridge_multihop_eval.py` is a specialized layer that reuses the general evaluator and adds entity-bridge, chain-level, and image-reference diagnostics. The former metrics-v2 evaluator and its retired `评测考试/考卷/` inputs are no longer part of the active repository.

## Repository Map

- `评测脚本/retrieval_eval.py`: general Claim-based evaluator, backend clients, validation, metrics, comparison, and artifact generation.
- `评测脚本/entity_bridge_multihop_eval.py`: entity-bridge multi-hop specialization built on `retrieval_eval.py`.
- `评测脚本/requirements.txt`: evaluator runtime dependency (`requests`).
- `评测脚本/requirements-dev.txt`: runtime and offline test dependencies.
- `评测脚本/requirements-examples.txt`: optional `openai` dependency for manual model API examples.
- `tests/test_retrieval_eval.py`: general evaluator unit tests; all HTTP is mocked.
- `tests/test_entity_bridge_multihop_eval.py`: specialization tests, including the real multi-hop exam and local PNG validation.
- `生成的原始文档语料/2026-07-14-01/`: 10-document corpus used by the general v3 exam.
- `生成的原始文档语料/2026-07-17-多跳-无词面重合/`: 10-document entity-bridge corpus plus PNG assets.
- `评测考试/考卷-v3/`: general Claim-based exams.
- `评测考试/考卷-多跳/`: entity-bridge specialized exams.
- `评测考试/考试结果-v3-<backend>/`: generated general-evaluator artifacts.
- `评测考试/考试结果-多跳-<backend>/`: generated specialized artifacts.
- `评测考试所测检索召回接口/`: manual retrieval, agent-Q&A, and model API examples; these are not pytest tests.
- `评测相关参考文档/`: methodology and historical design notes. Historical v2 descriptions are context, not current runtime behavior.

Preserve the Chinese directory names and the `考卷-YYYY-MM-DD-NN` naming convention. Treat result directories as generated evidence, not source code.

## Setup and Commands

Run from the repository root and reuse `.venv`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt

.venv/bin/python -m py_compile \
  评测脚本/retrieval_eval.py \
  评测脚本/entity_bridge_multihop_eval.py
.venv/bin/python -m pytest tests/ -q
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
.venv/bin/python 评测脚本/entity_bridge_multihop_eval.py --validate-only
```

The validation commands require no credentials or network access. The general evaluator validates `评测考试/考卷-v3/`; the specialization validates `评测考试/考卷-多跳/`, its corpus snapshot, and all declared PNG assets.

Manual model API examples are isolated from pytest. Install their optional dependency only when needed:

```bash
.venv/bin/python -m pip install -r 评测脚本/requirements-examples.txt
```

## Evaluator Contract

The general pipeline is:

`main` → `discover_exam_files` → `load_and_validate_exam` → backend request and normalization in `evaluate_exam` → Claim metrics → `aggregate` → `write_outputs`.

Each scored question carries atomic `claims`. A Claim is a hit only when a normalized `accepted_span` is contained in a retrieved chunk whose `document_name` exactly equals the Claim's `source_document`. Document-name recall without span recall is diagnostic only.

The three co-equal headline metrics at `--primary-k` are:

- Query Macro Claim Recall.
- Complete Evidence Chain Rate.
- Novel Claim Rank Score.

There is no composite total and no final-answer score. Keep `--request-k` distinct from client-side `--primary-k` and `--eval-k`. Report document recall, hard-negative intrusion, duplicate-document rate, char-budget curves, and latency as diagnostics rather than re-weighting the headline metrics.

The general evaluator validates exam schema, corpus SHA-256 values, and accepted-span existence before any live request. `--allow-corpus-drift` is diagnostic-only and makes comparison evidence weaker.

The entity-bridge specialization adds endpoint, bridge, supporting-document, image-reference, and complete-path metrics. Statistical comparison clusters questions by `chain_id`; with few independent chains, treat confidence intervals and p-values as exploratory.

## Backends and Outputs

Both `arag` and `dify` have online clients. Dify transport is selected with `--dify-api-mode console|dataset-api`: Console mode uses Cookie/CSRF, while Dataset API mode uses a Bearer key from `DIFY_DATASET_API_KEY`. Retrieval strategy is selected with `--dify-search-method hybrid_search|coverage_search`; `--graph-search` is Dify-only and mutually exclusive with `coverage_search`. Parent-child chunking is a Dataset property selected with `--dataset-id`.

General runs default to `评测考试/考试结果-v3-<backend>`. Specialized runs default to `评测考试/考试结果-多跳-<backend>`. Use `--out-dir` for named Dataset/index variants.

Each completed general or specialized run writes:

- `results_<timestamp>.json`
- `summary_<timestamp>.json`
- `manifest_<timestamp>.json`
- `report_<timestamp>.md`

JSON output must use two-space indentation and `ensure_ascii=False`. Keep `metrics_version`, manifest fields, and report terminology synchronized when semantics change.

## Corpus and Comparison Discipline

The local corpus must match the live Dataset selected by `--backend` and `--dataset-id`. Editing local documents has no effect until the corpus is re-ingested. Record Dataset revision, request configuration, exam/corpus hashes, chunking/indexing mode, and re-ingestion state.

Only call a comparison strict A/B when both runs are complete/full and match on exam hash, corpus snapshot, evaluation parameters, Dataset/index revision, and scored-question order. Dataset drift makes the result a descriptive cross-index comparison.

## Coding and Testing Conventions

Use Python 3, UTF-8, four-space indentation, type hints, and short public-function docstrings. Use `snake_case` for functions and variables and `UPPER_SNAKE_CASE` for constants. Keep metric calculation pure where possible and isolate HTTP/filesystem work.

For metric, schema, retry, backend, comparison, or output changes, update the matching test file in the same change. At minimum run syntax compilation, the full offline test suite, and both `--validate-only` commands. Run credentialed smoke tests only when the task explicitly requires live verification.

## Security and Generated Files

Never hardcode or commit cookies, CSRF/XSRF tokens, API keys, private documents, or unsanitized responses. Pass aRAG/Dify Console credentials through `RETRIEVAL_COOKIE` and `RETRIEVAL_XSRF_TOKEN`, Dify Dataset API credentials through `DIFY_DATASET_API_KEY`, and model-example credentials through `DASHSCOPE_API_KEY`. Existing interface examples may contain stale credential-shaped values and must not be treated as reusable secrets.

Do not commit `.venv/`, IDE metadata, `.pytest_cache/`, `__pycache__/`, `.DS_Store`, or generated bytecode.
