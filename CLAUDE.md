# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A RAG evaluation workspace. The only implemented evaluator so far scores **retrieval recall / chunking** — i.e. whether a single knowledge base returns the required Ground-Truth chunks for a query. It deliberately does **not** score answer correctness, reasoning, refusal behavior, or tool calls. Interface samples for agent Q&A (`相关接口调用例子/智能体问答/`) exist as groundwork for a future answer-quality evaluator, but no such script is written yet.

Directory names are intentionally Chinese; preserve them. Rough map:
- `评测脚本/retrieval_eval.py` — the evaluator (metrics **v2.0**).
- `基于语料生成的评测集/考卷/考卷-YYYY-MM-DD-NN.json` — exam files ("考卷"), the input; newest is `考卷-2026-07-15-02` (25 all-hard questions). `基于语料生成的评测集/考试结果-<backend>/` — generated output, split per retrieval backend (`考试结果-arag` = DingTalk/aRAG runs; `考试结果-dify` = placeholder for the Dify backend). Treat as artifacts.
- `生成的原始文档语料/<date>/` — source corpus the exams are built from. **This on-disk corpus must stay in sync with the live retrieval KB** (`DATASET_ID` in `retrieval_eval.py`): scoring matches GT snippets against chunks returned by that one fixed knowledge base, so **editing the corpus changes nothing until it is re-ingested**. Docs 05/06/09 were expanded for `考卷-2026-07-15-02` (a shared risk-management image reused across 04/05/06, plus precision/recall paragraphs) and that corpus has been re-ingested.
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

# Live run — requires credentials as env vars, hits the DingTalk retrieval API.
# --backend {arag,dify} (default arag) picks the output root 考试结果-<backend>;
# only arag has an online retrieval client, so a live --backend dify run exits 4.
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --backend arag --limit 3 --top-k 5   # smoke run → 考试结果-arag
```

`tests/test_retrieval_eval.py` loads the evaluator via `importlib` from its Chinese path and mocks all HTTP, so the suite needs no credentials. `--backend {arag,dify}` selects the output root `考试结果-<backend>` (default `arag`); `--out-dir` overrides that per-backend default; `--exam`/`--exam-dir` override the input paths; `--help` lists everything.

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
- **Image evidence** (`expected_image_url`) must be covered in *every* GT source document to count as a full hit. The URL is matched through the **same `normalize_text` pass** as text snippets (not a raw substring), so `image_source_coverage` and that URL's `evidence_recall` always agree and survive case/Markdown differences introduced at ingestion.
- **Authoring a hard 考卷:** the retriever scores near-perfect on 1–2-doc questions but reliably drops the 3rd document (or an image's 3rd source) out of Top-K once required GT spans ≥3 docs — concentrate difficulty there, and keep query wording low-overlap with the GT snippets. Any snippet you add must be a verbatim substring of its source doc *after* `normalize_text`; `--validate-only` does **not** check this, so verify it with a script that reuses the evaluator's own `normalize_text`.

### Ranking and the retrieval API
- `--rank-by response` (default) trusts the server's returned order; `--rank-by relevance` re-sorts by `relevanceScore`. Either way `ranking_anomaly` is flagged when the server's response order is not monotonically non-increasing in `relevanceScore` — a signal that server order and score disagree.
- `call_retrieval_api` retries **only** transient failures (network errors, 408, 429, 5xx) with exponential backoff. A `401/403` raises `AuthenticationError`, which aborts the *entire* run via `EvaluationAborted`, still writing partial results with `run_status="aborted_auth"` and a null capability score. Other per-question errors are recorded as a 0-score result and the run continues.

### Outputs
`write_outputs` writes three files to `<out-dir>/<exam_id>/<timestamp>/` (where `<out-dir>` defaults to `考试结果-<backend>`): `results_*.json` (per-question detail + truncated raw responses), `summary_*.json` (aggregates), `report_*.md` (human-readable, includes per-question hit/miss evidence diagnosis; a partial run — e.g. `--limit`, where the evaluated count differs from `exam_meta.total_questions` — prints a "部分运行" banner so the capability score isn't mistaken for a full-exam score). All JSON is `ensure_ascii=False`, 2-space indent.

## Conventions specific to this repo
- Exam ids and files follow `考卷-YYYY-MM-DD-NN`.
- Keep metric math pure; isolate HTTP and filesystem I/O (the file already separates these).
- Commit code, corpus, and generated output as **separate** changes; don't commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, or `__pycache__/`.
