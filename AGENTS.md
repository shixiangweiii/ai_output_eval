# Repository Guidelines

## Project Scope

This repository is a compact RAG retrieval-evaluation workspace. The implemented evaluator measures whether a single knowledge base returns the required Ground-Truth evidence in Top-K results. It does **not** currently score final-answer correctness, reasoning quality, refusal behavior, conversation memory, or tool calls; the agent Q&A samples and broader methodology documents are groundwork for future evaluators.

`评测脚本/retrieval_eval.py` is the source of truth for runtime behavior and metrics (currently metrics v2.0). Some older exam metadata and reference documents describe broader scoring models or fixed difficulty weights that the current script does not implement.

## Repository Map

- `评测脚本/retrieval_eval.py`: executable evaluator, including validation, HTTP calls, metrics, aggregation, and report generation.
- `评测脚本/requirements.txt`: runtime dependency (`requests`).
- `评测脚本/requirements-dev.txt`: runtime and test dependencies (`requests`, `pytest`).
- `tests/test_retrieval_eval.py`: unit tests; loads the evaluator from its Chinese path with `importlib` and mocks HTTP.
- `生成的原始文档语料/<date>/`: source corpus used to author exams.
- `基于语料生成的评测集/考卷/考卷-YYYY-MM-DD-NN.json`: exam inputs.
- `基于语料生成的评测集/考试结果-<backend>/<exam-id>/<timestamp>/`: generated results, summaries, and Markdown reports.
- `相关接口调用例子/`: raw retrieval and agent-Q&A request/response examples; use them only to understand API shape.
- `评测相关参考文档/`: methodology and dataset-authoring notes; these explain intent, not necessarily current code behavior.
- `CLAUDE.md`: detailed architecture and metric notes that should stay consistent with this guide and the code.

Preserve the Chinese directory names and the `考卷-YYYY-MM-DD-NN` convention. Treat result directories as generated evaluation evidence, not source code.

## Setup and Commands

Run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r 评测脚本/requirements-dev.txt

python -m py_compile 评测脚本/retrieval_eval.py
python -m pytest tests/
python -m pytest tests/test_retrieval_eval.py::test_name -q
python 评测脚本/retrieval_eval.py --validate-only
```

`--validate-only` validates all exam JSON without credentials or network access. Use `--exam` and `--exam-dir` to narrow or relocate inputs, and `--help` for the complete CLI.

A small live aRAG smoke run requires environment-provided credentials:

```bash
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py \
  --backend arag --exam 考卷-2026-07-15-02.json --limit 3 --top-k 5
```

Supported backend labels are `arag` and `dify`, and the selected label controls the default `考试结果-<backend>` output root. Only `arag` currently has an online client. A live `--backend dify` run intentionally exits with code 4; `dify` can currently be used only for offline validation or manual result organization. `--out-dir` overrides the backend-derived output root.

## Evaluator Contract

The main pipeline is:

`main` → `discover_exam_files` → `load_and_validate_exam` → `evaluate_exam` → `compute_metrics` → `aggregate` → `write_outputs`.

Each exam question must provide a non-empty `id`, a `difficulty` in `simple|medium|hard`, a non-empty `question`, and one or more `retrieved_chunks`. Each Ground-Truth chunk must include a non-empty `source_document` and `snippet`; `section` is used for diagnostics. `eval_criteria.expected_image_url`, when present, must also occur in at least one GT snippet. `answer` and `reasoning_path` are retained for human review but ignored by the current scorer.

Evidence matching requires exact `source_document` equality plus normalized snippet containment; document name alone is insufficient. A snippet is a hit only when its normalized text is contained in a retrieved chunk from the same document. Text normalization applies NFKC, punctuation/Markdown folding, whitespace removal, and lowercasing.

Per-question `score` equals `evidence_recall@k`. The reported `retrieval_capability_score` is the flat mean of per-question scores across all attempted questions multiplied by 100; request errors count as zero. Difficulty/type breakdowns, keyword coverage, document metrics, image metrics, precision, MRR, and ranking anomalies are diagnostics and do not re-weight the score. Image evidence is fully covered only when the expected URL is retrieved from every required GT source document.

The default `--rank-by response` preserves server order; `--rank-by relevance` sorts by `relevanceScore`. Both modes still report response-order ranking anomalies. Network failures, HTTP 408/429, and 5xx responses are retryable. HTTP 401/403 aborts the whole run, writes any partial results with `run_status="aborted_auth"`, and leaves the capability score null.

## Corpus and Exam Authoring

The on-disk corpus must stay synchronized with the live knowledge base selected by `--dataset-id`. Editing a source document does not affect retrieval until the updated corpus is re-ingested. Record corpus changes and re-ingestion dependencies in exam metadata so a stale knowledge base is not misdiagnosed as a retrieval regression.

GT snippets should remain verbatim substrings of their source documents after the evaluator's `normalize_text` pass. `--validate-only` checks JSON structure and field relationships but does not prove that snippets exist in the corpus or live knowledge base; use a focused check that imports the evaluator's own normalization function when authoring or revising exams.

When interpreting runs, distinguish full exams from smoke runs (`--limit`), request/setup failures, stale-corpus failures, and genuine retrieval misses. Reports from partial runs contain a warning and must not be compared directly with full-exam capability scores.

## Coding and Testing Conventions

Use Python 3, UTF-8, four-space indentation, type hints, and short docstrings for public functions. Follow `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants, and descriptive exception names such as `RetrievalError`. Keep metric calculation pure where possible and isolate HTTP/filesystem work.

For metric, schema, retry, backend, or output changes, update `tests/test_retrieval_eval.py` in the same change. Mock network calls in tests. At minimum, run the syntax check, the full offline test suite, and `--validate-only`. Run a credentialed `--limit 3` smoke test only when the task requires live verification, then inspect all three generated artifacts:

- `results_<timestamp>.json`: per-question details and truncated raw responses.
- `summary_<timestamp>.json`: aggregate metrics and run status.
- `report_<timestamp>.md`: human-readable summary and evidence diagnostics.

JSON output must use two-space indentation and `ensure_ascii=False`. Keep `metrics_version` and report terminology synchronized when changing metric semantics.

## Commits, Generated Data, and Security

Use concise imperative commits, optionally scoped, for example `eval: handle empty retrieval results`. Keep evaluator code, corpus/exam data, and generated evaluation output in separate commits where practical. Pull requests should state the evaluation scenario, commands run, affected exam/corpus IDs, backend, Top-K/ranking mode, and any metric changes; include a short report excerpt when outputs change.

Do not commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, or `__pycache__/`. Never hardcode or commit live cookies, XSRF tokens, private documents, or unsanitized API responses. Supply `RETRIEVAL_COOKIE` and `RETRIEVAL_XSRF_TOKEN` through the process environment only. Existing raw API examples may contain stale credential-shaped values and must never be treated as reusable secrets.
