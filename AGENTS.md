# Repository Guidelines

## Project Scope

This repository is a compact RAG retrieval-evaluation workspace. The implemented evaluator measures whether the knowledge base selected for one run returns the required Ground-Truth evidence in Top-K results. It supports aRAG (DingTalk) and local Dify console hit-testing, but it does **not** currently score final-answer correctness, reasoning quality, refusal behavior, conversation memory, or tool calls. The agent Q&A samples and broader methodology documents are groundwork for future evaluators.

`评测脚本/retrieval_eval.py` is the source of truth for runtime behavior and metrics (currently metrics v2.0). Some older exam metadata, reference documents, and help text describe broader scoring models, fixed difficulty weights, or an unimplemented Dify client; those descriptions are stale when they conflict with the current code.

## Repository Map

- `评测脚本/retrieval_eval.py`: executable evaluator, including validation, backend HTTP clients, response normalization, metrics, aggregation, and report generation.
- `评测脚本/requirements.txt`: runtime dependency (`requests`).
- `评测脚本/requirements-dev.txt`: runtime and test dependencies (`requests`, `pytest`).
- `tests/test_retrieval_eval.py`: unit tests; loads the evaluator from its Chinese path with `importlib` and mocks HTTP.
- `生成的原始文档语料/<date-id>/`: source corpus used to author exams.
- `评测考试/考卷/考卷-YYYY-MM-DD-NN.json`: exam inputs.
- `评测考试/考试结果-arag/<exam-id>/<timestamp>/`: generated aRAG results.
- `评测考试/考试结果-dify*/<exam-id>/<timestamp>/`: generated Dify comparison runs, including ordinary retrieval, parent-child chunking, and GraphRAG archives. The evaluator's automatic Dify default is only `考试结果-dify`; use `--out-dir` for variant-specific roots.
- `评测考试/考试结果分析/`: manually authored cross-backend analyses based on generated result artifacts.
- `相关接口调用例子/arag检索召回/`: raw aRAG retrieval request/response examples.
- `相关接口调用例子/dify检索召回接口/`: Dify ordinary, parent-child, and GraphRAG hit-testing examples.
- `相关接口调用例子/智能体问答/`: agent-Q&A examples for future evaluator work, not current scorer inputs.
- `评测相关参考文档/`: methodology and dataset-authoring notes; these explain intent, not necessarily current code behavior.
- `CLAUDE.md`: detailed architecture and metric notes; keep it consistent with this guide and the code when related behavior changes.

Preserve the Chinese directory names and the `考卷-YYYY-MM-DD-NN` convention. Treat result directories as generated evaluation evidence, not source code. Do not move generated results back into the retired `基于语料生成的评测集/` path.

## Setup and Commands

Run commands from the repository root. Reuse the project virtual environment at `.venv`; create and install it only when it is missing:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt

.venv/bin/python -m py_compile 评测脚本/retrieval_eval.py
.venv/bin/python -m pytest tests/
.venv/bin/python -m pytest tests/test_retrieval_eval.py::test_name -q
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
```

`--validate-only` validates all JSON under `评测考试/考卷/` without credentials or network access. Use `--exam` and `--exam-dir` to narrow or relocate inputs, and `--help` for the complete CLI.

A small live aRAG smoke run requires environment-provided credentials:

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend arag --exam 考卷-2026-07-15-02.json --limit 3 --top-k 5
```

Both `arag` and `dify` have online clients. Dify targets the local console hit-testing endpoint at `localhost:5001` and reuses `RETRIEVAL_COOKIE` / `RETRIEVAL_XSRF_TOKEN` as its Cookie and CSRF credentials. A Dify GraphRAG smoke run should use an explicit variant output root:

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  .venv/bin/python 评测脚本/retrieval_eval.py \
  --backend dify --graph-search --exam 考卷-2026-07-15-02.json \
  --limit 3 --top-k 5 --out-dir 评测考试/考试结果-dify-graphrag
```

`--backend` selects the request/response adapter, default dataset ID, and default `评测考试/考试结果-<backend>` root. `--graph-search` only alters the Dify request body; it does not rename the output directory. `--dataset-id` and `--out-dir` override their backend-derived defaults. The current `--help` description still says Dify online retrieval is unimplemented, but that sentence is stale; runtime code lists `dify` in `ONLINE_BACKENDS` and calls `call_dify_api`.

## Evaluator Contract

The main pipeline is:

`main` → `discover_exam_files` → `load_and_validate_exam` → backend HTTP call and response parser in `evaluate_exam` → `compute_metrics` → `aggregate` → `write_outputs`.

Each exam question must provide a non-empty `id`, a `difficulty` in `simple|medium|hard`, a non-empty `question`, and one or more `retrieved_chunks`. Each Ground-Truth chunk must include a non-empty `source_document` and `snippet`; `section` is used for diagnostics. `eval_criteria.expected_image_url`, when present, must also occur in at least one GT snippet. `answer` and `reasoning_path` are retained for human review but ignored by the current scorer.

Evidence matching requires exact `source_document` equality plus normalized snippet containment; document name alone is insufficient. A snippet is a hit only when its normalized text is contained in a retrieved chunk from the same document. Text normalization applies NFKC, punctuation/Markdown folding, whitespace removal, and lowercasing.

Per-question `score` equals `evidence_recall@k`. The reported `retrieval_capability_score` is the flat mean of per-question scores across all attempted questions multiplied by 100; request errors count as zero. Difficulty/type breakdowns, keyword coverage, document metrics, image metrics, precision, MRR, and ranking anomalies are diagnostics and do not re-weight the score. Image evidence is fully covered only when the expected URL is retrieved from every required GT source document.

The aRAG adapter normalizes `content[]` and `relevanceScore`; the Dify adapter normalizes `records[].segment` and `score` into the same internal chunk shape. The default `--rank-by response` preserves server order; `--rank-by relevance` sorts by the normalized relevance score. Both modes still report response-order ranking anomalies. Network failures, HTTP 408/429, and 5xx responses are retryable. HTTP 401/403 aborts the whole run, writes any partial results with `run_status="aborted_auth"`, and leaves the capability score null.

## Corpus and Exam Authoring

The on-disk corpus must stay synchronized with the live backend and knowledge base selected by `--backend` and `--dataset-id`. Editing a source document does not affect retrieval until the updated corpus is re-ingested into every backend or indexing configuration being compared. Record corpus changes, dataset IDs, chunking/indexing modes, and re-ingestion dependencies so stale or differently indexed knowledge bases are not misdiagnosed as retrieval regressions.

GT snippets should remain verbatim substrings of their source documents after the evaluator's `normalize_text` pass. `--validate-only` checks JSON structure and field relationships but does not prove that snippets exist in the corpus or live knowledge base; use a focused check that imports the evaluator's own normalization function when authoring or revising exams.

When interpreting runs, distinguish full exams from smoke runs (`--limit`), request/setup failures, stale-corpus failures, indexing-mode differences, and genuine retrieval misses. Reports from partial runs contain a warning and must not be compared directly with full-exam capability scores. Cross-backend analyses should record the exact exam ID, dataset/index configuration, Top-K, ranking mode, GraphRAG flag, and source artifact directories.

## Coding and Testing Conventions

Use Python 3, UTF-8, four-space indentation, type hints, and short docstrings for public functions. Follow `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants, and descriptive exception names such as `RetrievalError`. Keep metric calculation pure where possible and isolate HTTP/filesystem work.

For metric, schema, retry, backend, or output changes, update `tests/test_retrieval_eval.py` in the same change. Mock network calls in tests. At minimum, run the syntax check, the full offline test suite, and `--validate-only`. Run a credentialed `--limit 3` smoke test only when the task requires live verification. For Dify comparison variants, provide the intended `--dataset-id`, `--graph-search`, and/or `--out-dir` explicitly. Then inspect all three generated artifacts:

- `results_<timestamp>.json`: per-question details and truncated raw responses.
- `summary_<timestamp>.json`: aggregate metrics and run status.
- `report_<timestamp>.md`: human-readable summary and evidence diagnostics.

JSON output must use two-space indentation and `ensure_ascii=False`. Keep `metrics_version` and report terminology synchronized when changing metric semantics.

## Commits, Generated Data, and Security

Use concise imperative commits, optionally scoped, for example `eval: handle empty retrieval results`. Keep evaluator code, corpus/exam data, generated evaluation output, and cross-backend analysis in separate commits where practical. Pull requests should state the evaluation scenario, commands run, affected exam/corpus IDs, backend and index mode, Top-K/ranking mode, GraphRAG flag, and any metric changes; include a short report excerpt when outputs change.

Do not commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, `.pytest_cache/`, or `__pycache__/`. Never hardcode or commit live cookies, CSRF/XSRF tokens, private documents, or unsanitized API responses. Supply `RETRIEVAL_COOKIE` and `RETRIEVAL_XSRF_TOKEN` through the process environment only for both backends. Existing raw API examples may contain stale credential-shaped values and must never be treated as reusable secrets.
