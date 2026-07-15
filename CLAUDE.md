# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A RAG evaluation workspace. The only implemented evaluator so far scores **retrieval recall / chunking** — i.e. whether a single knowledge base returns the required Ground-Truth chunks for a query. It deliberately does **not** score answer correctness, reasoning, refusal behavior, or tool calls. Interface samples for agent Q&A (`相关接口调用例子/智能体问答/`) exist as groundwork for a future answer-quality evaluator, but no such script is written yet.

Directory names are intentionally Chinese; preserve them. Rough map:
- `评测脚本/retrieval_eval.py` — the evaluator (metrics **v2.0**).
- `基于语料生成的评测集/考卷/*.json` — exam files ("考卷"), the input. `基于语料生成的评测集/考试结果/` — generated output (treat as artifacts).
- `生成的原始文档语料/<date>/` — source corpus the exams are built from.
- `相关接口调用例子/` — raw curl request/response samples for the live APIs.
- `评测相关参考文档/` — methodology notes (the "why", not code).
- `AGENTS.md` — contributor style/PR conventions; read it before committing.

## Commands

Run from the repo root. The checked-in `.venv/` is empty of dependencies — install first.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r 评测脚本/requirements-dev.txt   # requests + pytest; requirements.txt is requests only

python -m py_compile 评测脚本/retrieval_eval.py                 # offline syntax check
python -m pytest tests/                                          # full unit suite (no network/creds)
python -m pytest tests/test_retrieval_eval.py::test_name -q      # single test
python 评测脚本/retrieval_eval.py --validate-only               # validate all exams, no creds/network

# Live run — requires credentials as env vars, hits the DingTalk retrieval API:
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --limit 3 --top-k 5          # small smoke run
```

`tests/test_retrieval_eval.py` loads the evaluator via `importlib` from its Chinese path and mocks all HTTP, so the suite needs no credentials. `--exam`/`--exam-dir`/`--out-dir` override the built-in paths; `--help` lists everything.

Credentials are **only** ever passed through `RETRIEVAL_COOKIE` / `RETRIEVAL_XSRF_TOKEN`. Never paste live cookies, tokens, or private corpus/API content into code, commits, or reports — the curl samples in `相关接口调用例子/` already contain stale example cookies; do not treat them as reusable.

## How the evaluator works (retrieval_eval.py)

The whole pipeline lives in one file. `main` → `discover_exam_files` → `load_and_validate_exam` (strict schema check) → `evaluate_exam` (per-question API call) → `compute_metrics` (per question) → `aggregate` (per exam) → `write_outputs`.

### Exam schema (the contract that ties everything together)
Each question carries the Ground Truth inline:
- `retrieved_chunks[]` — the **required** evidence: `{source_document, section, snippet}`. `snippet` must be a verbatim substring of the corpus. This is what scoring matches against.
- `eval_criteria` — `keyword_match` (diagnostic only), `expected_image_url`, `requires_tool`, `is_adversarial`.
- `answer` / `reasoning_path` — present for human review, **ignored by the script**.

`load_and_validate_exam` enforces this schema and will reject the exam before any network call (duplicate ids, `total_questions` mismatch, bad difficulty, an `expected_image_url` not present in any GT snippet, etc.). When editing the schema, update the validator, `compute_metrics`, and the tests together.

### Scoring model — read this before touching metrics
- **Evidence match is the core primitive.** A GT `snippet` counts as hit only if it appears (as a substring) inside a retrieved chunk whose `document_name` equals the GT `source_document`. Right document + wrong section = **not** a hit. Matching runs through `normalize_text` (NFKC, dash/quote folding, Markdown stripping, whitespace removal, lowercase), so exam snippets and corpus text must survive that normalization identically.
- **Per-question `score` = `evidence_recall@k`.** Nothing else feeds the score. `keyword_coverage` and all the `doc_*` / `image_*` / ranking metrics are diagnostics.
- **`retrieval_capability_score` (0–100) is a flat micro-average of per-question `score` over _all_ questions**, then ×100 in `aggregate`. A question that errored counts as 0 (this is `end_to_end_score`); `quality_score_on_success` is the mean over only successful questions. `by_difficulty` / `by_type` are reported but do **not** re-weight the capability score. (Note: some exam `exam_meta` still carries a stale note about a fixed 40/40/20 difficulty weighting — the v2.0 code does not apply it.)
- **Image evidence** (`expected_image_url`) must be covered in *every* GT source document to count as a full hit.

### Ranking and the retrieval API
- `--rank-by response` (default) trusts the server's returned order; `--rank-by relevance` re-sorts by `relevanceScore`. Either way `ranking_anomaly` is flagged when the server's response order is not monotonically non-increasing in `relevanceScore` — a signal that server order and score disagree.
- `call_retrieval_api` retries **only** transient failures (network errors, 408, 429, 5xx) with exponential backoff. A `401/403` raises `AuthenticationError`, which aborts the *entire* run via `EvaluationAborted`, still writing partial results with `run_status="aborted_auth"` and a null capability score. Other per-question errors are recorded as a 0-score result and the run continues.

### Outputs
`write_outputs` writes three files to `<out-dir>/<exam_id>/<timestamp>/`: `results_*.json` (per-question detail + truncated raw responses), `summary_*.json` (aggregates), `report_*.md` (human-readable, includes per-question hit/miss evidence diagnosis). All JSON is `ensure_ascii=False`, 2-space indent.

## Conventions specific to this repo
- Exam ids and files follow `考卷-YYYY-MM-DD-NN`.
- Keep metric math pure; isolate HTTP and filesystem I/O (the file already separates these).
- Commit code, corpus, and generated output as **separate** changes; don't commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, or `__pycache__/`.
