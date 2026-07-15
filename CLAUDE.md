# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A RAG evaluation workspace. The only implemented evaluator scores **retrieval recall / chunking** — i.e. whether a single knowledge base returns the required Ground-Truth evidence for a query. It deliberately does **not** score answer correctness, reasoning, refusal behavior, or tool calls. Interface samples for agent Q&A (`相关接口调用例子/智能体问答/`) exist as groundwork for a future answer-quality evaluator, but no such script is written yet.

The evaluator now drives **two online retrieval backends** through one metric pipeline: `arag` (DingTalk aRAG single-KB retrieval) and `dify` (a local Dify console *hit-testing* endpoint). The four configurations benchmarked so far — aRAG, plain Dify, Dify parent-child chunking, and Dify+GraphRAG — are all produced by this one script (see **Backends** below).

Directory names are intentionally Chinese; preserve them. Rough map:
- `评测脚本/retrieval_eval.py` — the evaluator (metrics **v2.0**); one file, both backends.
- `评测考试/考卷/考卷-YYYY-MM-DD-NN.json` — exam files ("考卷"), the input. Counts: `考卷-2026-07-14-01` (20), `考卷-2026-07-15-01` (30), newest `考卷-2026-07-15-02` (25, all-hard).
- `评测考试/考试结果-<backend>/<exam-id>/<timestamp>/` — generated output. `--backend` only ever writes `考试结果-arag` or `考试结果-dify`. The `考试结果-dify-父子` and `考试结果-dify-graphrag` roots hold Dify config variants and are **manual `--out-dir` targets**, not auto-named by any `--backend` value. Treat all as artifacts.
- `评测考试/考试结果分析/后端对比分析-*.md` — human-written cross-backend comparison reports (read for the "which backend wins and why", including the scoring caveat below).
- `生成的原始文档语料/2026-07-14-01/` — source corpus (docs 01–10) the exams are built from. **This on-disk corpus must stay in sync with the live retrieval KB**: scoring matches GT snippets against chunks returned by a fixed knowledge base, so **editing the corpus changes nothing until it is re-ingested**. Docs 05/06/09 were expanded for `考卷-2026-07-15-02` (a shared risk-management image reused across 04/05/06, plus precision/recall paragraphs) and the aRAG corpus has been re-ingested.
- `相关接口调用例子/{arag检索召回,dify检索召回接口,智能体问答}/` — raw curl request/response samples for the live APIs (`dify检索召回接口/` has plain / parent-child / graphrag variants).
- `评测相关参考文档/` — methodology notes (the "why", not code).
- `AGENTS.md` — contributor style/PR conventions; read it before committing. (Its line calling a live `--backend dify` run "exit 4" is stale — dify is now a working online backend.)

## Commands

Run from the repo root. The checked-in `.venv/` is empty of dependencies — install first.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r 评测脚本/requirements-dev.txt   # requests + pytest; requirements.txt is requests only

python -m py_compile 评测脚本/retrieval_eval.py                 # offline syntax check
python -m pytest tests/                                          # full unit suite (no network/creds)
python -m pytest tests/test_retrieval_eval.py::test_name -q      # single test
python 评测脚本/retrieval_eval.py --validate-only               # validate all exams, no creds/network
```

`tests/test_retrieval_eval.py` loads the evaluator via `importlib` from its Chinese path and mocks all HTTP, so the suite needs no credentials. `--exam`/`--exam-dir` override the input paths; `--out-dir` overrides the per-backend output root; `--help` lists everything.

Credentials are **only** ever passed through `RETRIEVAL_COOKIE` / `RETRIEVAL_XSRF_TOKEN` (both backends read the same pair; aRAG sends them as `Cookie` + `x-xsrf-token` to DingTalk, Dify as `Cookie` + `x-csrf-token` to the local console). Never paste live cookies, tokens, or private corpus/API content into code, commits, or reports — the curl samples in `相关接口调用例子/` already contain stale example cookies; do not treat them as reusable.

## Backends — how the four benchmarked configs are produced

Both `arag` and `dify` are in `ONLINE_BACKENDS`, so both run live. `--backend` only sets the default output root and header/endpoint style; the actual KB is `--dataset-id` (defaults to the backend's built-in id). Parent-child vs. plain chunking is a **property of the Dify dataset**, selected purely by `--dataset-id`; GraphRAG is the `--graph-search` flag (Dify-only, injects `retrieval_model.graph_search`). The output dir must be steered with `--out-dir` for the two Dify variants, since `--backend dify` alone writes to `考试结果-dify`.

| Config | Command (add creds + `--exam …`) | dataset_id | Output root |
| --- | --- | --- | --- |
| aRAG | `--backend arag` | `844b8ded-…745832` (built-in) | `考试结果-arag` |
| Dify (plain) | `--backend dify` | `fc0250d7-…83dc18` (built-in) | `考试结果-dify` |
| Dify parent-child | `--backend dify --dataset-id 260f9445-…6e29f50d0955 --out-dir 评测考试/考试结果-dify-父子` | that id | `考试结果-dify-父子` |
| Dify GraphRAG | `--backend dify --graph-search --dataset-id 0a8b3810-…ef525095af16 --out-dir 评测考试/考试结果-dify-graphrag` | that id | `考试结果-dify-graphrag` |

```bash
# aRAG smoke run (3 questions) → 考试结果-arag
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --backend arag \
  --exam 评测考试/考卷/考卷-2026-07-15-02.json --limit 3 --top-k 5

# Dify GraphRAG full run → 考试结果-dify-graphrag (needs a running local Dify console)
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --backend dify --graph-search \
  --dataset-id 0a8b3810-... --out-dir 评测考试/考试结果-dify-graphrag \
  --exam 评测考试/考卷/考卷-2026-07-15-02.json
```

`SUPPORTED_BACKENDS` (the `--backend` choices) currently equals `ONLINE_BACKENDS`, so the "backend has no online client → exit 4" branch in `main` is a guard for a *future* backend and is unreachable today. The Dify endpoint is a `localhost` console URL, so a live Dify run needs that console reachable.

## How the evaluator works (retrieval_eval.py)

The whole pipeline lives in one file. `main` → `discover_exam_files` → `load_and_validate_exam` (strict schema check) → `evaluate_exam` (per-question API call, dispatched by backend) → `compute_metrics` (per question) → `aggregate` (per exam) → `write_outputs`. Backend differences are isolated to three pairs: `build_headers`/`build_dify_headers`, `call_retrieval_api`/`call_dify_api`, and `parse_retrieved_chunks`/`parse_dify_response` — the last normalizes both response shapes (aRAG `content[]`, Dify `records[].segment`) into one Chunk dict, so **all metric math is backend-agnostic**.

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
- **`--top-k` is the client-side evaluation window**, not a server retrieval-count knob: neither request payload sends it (the Dify request body pins `retrieval_model.top_k = 5`), so `--top-k` slices whatever the server returned before scoring. Comparing backends fairly means holding it constant.
- **Image evidence** (`expected_image_url`) must be covered in *every* GT source document to count as a full hit. The URL is matched through the **same `normalize_text` pass** as text snippets (not a raw substring), so `image_source_coverage` and that URL's `evidence_recall` always agree and survive case/Markdown differences introduced at ingestion.
- **Authoring a hard 考卷:** the retriever scores near-perfect on 1–2-doc questions but reliably drops the 3rd document (or an image's 3rd source) out of Top-K once required GT spans ≥3 docs — concentrate difficulty there, and keep query wording low-overlap with the GT snippets. Any snippet you add must be a verbatim substring of its source doc *after* `normalize_text`; `--validate-only` does **not** check this, so verify it with a script that reuses the evaluator's own `normalize_text`.

### Ranking and the retrieval API
- `--rank-by response` (default) trusts the server's returned order; `--rank-by relevance` re-sorts by `relevanceScore`. Either way `ranking_anomaly` is flagged when the server's response order is not monotonically non-increasing in relevance score — a signal that server order and score disagree. (aRAG trips this on nearly every question; Dify, being reranked, does not — a diagnostic difference, not a score difference.)
- `call_retrieval_api` / `call_dify_api` retry **only** transient failures (network errors, 408, 429, 5xx) with exponential backoff. A `401/403` raises `AuthenticationError`, which aborts the *entire* run via `EvaluationAborted`, still writing partial results with `run_status="aborted_auth"` and a null capability score. Other per-question errors are recorded as a 0-score result and the run continues.

### Outputs
`write_outputs` writes three files to `<out-dir>/<exam_id>/<timestamp>/`: `results_*.json` (per-question detail + truncated raw responses), `summary_*.json` (aggregates), `report_*.md` (human-readable, includes per-question hit/miss evidence diagnosis; a partial run — e.g. `--limit`, where the evaluated count differs from `exam_meta.total_questions` — prints a "部分运行" banner so the capability score isn't mistaken for a full-exam score). All JSON is `ensure_ascii=False`, 2-space indent.

## Cross-backend comparison caveat

The headline `retrieval_capability_score` rewards **verbatim** snippet recall, and the GT snippets are authored to match **aRAG's** chunk boundaries — so the metric is structurally biased toward aRAG (it can win the total while having the *lowest* Document Recall of the four). When comparing backends, read `Document Recall@5` (chunking-neutral) alongside the total, and don't present the aRAG lead as "best retrieval" without that qualifier. This reasoning and the numbers live in `评测考试/考试结果分析/后端对比分析-考卷-2026-07-15-02.md`.

## Conventions specific to this repo
- Exam ids and files follow `考卷-YYYY-MM-DD-NN`.
- Keep metric math pure and backend-agnostic; isolate HTTP and filesystem I/O, and keep any new backend's differences inside the header/call/parse trio.
- Commit code, corpus, and generated output as **separate** changes; don't commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, or `__pycache__/`.
