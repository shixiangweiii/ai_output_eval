# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A RAG retrieval-evaluation workspace. **Every implemented evaluator scores retrieval / evidence recall — whether a knowledge base returns the Ground-Truth evidence needed to answer a query. None of them score final-answer correctness, reasoning quality, refusal behavior, citation faithfulness, conversation memory, or tool calls**, despite the schema carrying `reference_answer`/`evidence_chain` fields for human review. The agent-Q&A samples in `评测考试所测检索召回接口/DEAP智能体问答/` are groundwork for a future answer-quality evaluator that has not been written.

There are **three evaluator scripts**:

- **`评测脚本/retrieval_eval_v4.py`** — the **current** general retrieval evaluator (schema/metrics **4.1**). Config-driven multi-backend adapters (for comparing different RAG *products*), `document_key` normalization plus a pre-flight document-name gate, **five** parallel headline metrics (including entity-bridge multi-hop), cluster-level bootstrap inference, and N-way `--compare` that refuses same-backend config drift.
- **`评测脚本/retrieval_eval.py`** — the **frozen** v3.0 evaluator. Kept only so archived `考试结果-v3-*` runs stay reproducible and so the multihop specialization keeps working. **Do not add new v3 runs.**
- **`评测脚本/entity_bridge_multihop_eval.py`** — a multi-hop *specialization* layered on top of the v3 core (loaded via `importlib`), for "entity-bridge" cross-document chains (endpoint entity → bridge document → endpoint entity, plus image-asset references). Reuses v3's HTTP/normalization/Claim metrics and adds chain-level aggregation with the chain as the statistical clustering unit.

**Why v4 exists.** Two Dify runs on the v3 exam (`考试结果-v3-dify父子` vs `考试结果-v3-非dify父子-纯向量`) showed a 0.1204 recall gap that `--compare` reported at `p=0.0079`. Re-scoring both runs under a matched `score_threshold` collapsed the gap to 0.0278 — **77% of the "result" was a config difference the script never checked**. The same data also showed all 15 "document hit / claim miss" cases were wrong-section retrievals (not chunk-boundary truncation), that 28 of 36 questions scored identically in both runs (14 at ceiling, 7 at floor), and that the char-budget curve was inert because the smallest budget (1000) exceeded every question's entire retrieved context (max 866 chars). v4 plus the v4 exam fix all of these.

Directory names are intentionally Chinese; preserve them. Rough map:
- `评测脚本/retrieval_eval_v4.py` — the current evaluator; `评测脚本/后端配置/*.json` — one profile per RAG product.
- `评测脚本/考卷生成/` — the v4 exam-generation pipeline (fact ledger, corpus synthesizer, span extractor, exam builder, quality gates, difficulty screening). **Never put the ledger or its intermediates inside a corpus dir** (they would get ingested into the KB) **or inside a 考卷 dir** (`discover_exam_files` globs `*.json` and would treat them as exams).
- `评测考试/考卷-v4/考卷-2026-08-04-02.json` — the current v4 exam (56 q: 49 scored + 4 sanity + 3 unanswerable), corpus `2026-08-04-02`. `评测考试/考卷-v4-已废弃/考卷-2026-08-04-01.json` is the superseded 4.0 pilot, kept only so the archived first run's `exam_sha256` stays verifiable — it is **not runnable** under the 4.1 evaluator and must stay out of `考卷-v4/` (which is globbed).
- `生成的原始文档语料/2026-08-04-02/` — v4 corpus, 26 docs: A/B sibling families (2 × 5) + C/D entity-bridge chains (14) + 2 fake-bridge decoys. ASCII filenames, Chinese H1 titles. `2026-08-04-01/` is the 10-doc predecessor.
- `评测脚本/{retrieval_eval,entity_bridge_multihop_eval}.py` — the frozen v3 evaluator and multi-hop specialization.
- `评测考试/考卷-v3/考卷-2026-07-15-03.json` — v3 retrieval exam (40 q: 36 scored + 4 diagnostic), corpus `2026-07-14-01`.
- `评测考试/考卷-多跳/考卷-2026-07-17-01.json` — entity-bridge multi-hop exam, corpus `2026-07-17-多跳-无词面重合` (10 docs + `assets/*.png`).
- `评测考试/考试结果-v3-<backend|比较>/` — v3 run archives (auto-named). `考试结果-v3-dify-new` is a **manual `--out-dir`** for the `coverage_search` (覆盖索引) Dify variant — not auto-named by any `--backend` value.
- `评测考试/考试结果-多跳-<backend|比较>/` — entity-bridge run and comparison archives.
- `生成的原始文档语料/{2026-07-14-01,2026-07-17-多跳-无词面重合}/` — source corpora. **This on-disk corpus must stay in sync with the live retrieval KB**: scoring matches GT spans against chunks returned by a fixed knowledge base, so **editing the corpus changes nothing until it is re-ingested**.
- `评测考试所测检索召回接口/` — manual aRAG/Dify retrieval, DEAP Q&A, and model API examples. These are not pytest tests.
- `评测相关参考文档/` — methodology notes (the "why", not code; they describe a broader evaluation vision than what is implemented).
- `AGENTS.md` — current contributor style, validation, and security conventions.

## Commands

Run from the repo root. The checked-in `.venv/` is empty of dependencies — install first.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r 评测脚本/requirements-dev.txt   # requests + pytest; requirements.txt is requests only

python -m py_compile 评测脚本/*.py 评测脚本/考卷生成/*.py       # offline syntax check
python -m pytest tests/                                          # full unit suite (no network/creds)
python -m pytest tests/test_retrieval_eval_v4.py::test_name -q   # single test
python 评测脚本/retrieval_eval_v4.py --validate-only --exam-dir 评测考试/考卷-v4   # validate v4 exam
python 评测脚本/retrieval_eval.py --validate-only               # validate v3 exams, no creds/network
python 评测脚本/entity_bridge_multihop_eval.py --validate-only   # validate multihop exam + PNG assets
```

Regenerating the v4 corpus + exam (fully deterministic, offline):

```bash
python 评测脚本/考卷生成/synthesize_corpus.py --facts-out 评测脚本/考卷生成/事实台账-facts-02.json
python 评测脚本/考卷生成/extract_spans.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02.json --facts 评测脚本/考卷生成/事实台账-facts-02.json
python 评测脚本/考卷生成/build_exam.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json --out 评测考试/考卷-v4/考卷-2026-08-04-02.json
python 评测脚本/考卷生成/audit_exam.py --exam 评测考试/考卷-v4/考卷-2026-08-04-02.json --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json
python 评测脚本/考卷生成/screen_candidates.py --exam 评测考试/考卷-v4/考卷-2026-08-04-02.json --check-bridge-unreachable --out 评测脚本/考卷生成/难度校准-2026-08-04-02.json
```

Regenerating the corpus changes its SHA-256, so the exam must be rebuilt **and the KB re-ingested** before any live run.

`tests/` has two suites: `test_retrieval_eval.py` and `test_entity_bridge_multihop_eval.py`. Both mock HTTP, so no credentials are needed; the entity-bridge suite uses the real multi-hop exam and local corpus/PNG assets. Manual GLM/Kimi API examples live under `评测考试所测检索召回接口/大模型接口调用示例/` and are intentionally outside pytest.

## Backends — arag + dify, three Dify retrieval modes

`arag` and `dify` are both live (`SUPPORTED_BACKENDS = ("arag","dify")`). `--backend` selects the response adapter and default dataset. Dify transport is explicit: `--dify-api-mode console` uses Console hit-testing with Cookie/CSRF, while `--dify-api-mode dataset-api` uses `/v1/datasets/<id>/retrieve` with a Bearer key from `DIFY_DATASET_API_KEY`. Retrieval strategy remains `--dify-search-method hybrid_search|coverage_search` plus the optional `--graph-search` flag.

| Config | Command (add creds + `--exam …`) | dataset_id | Dify mode | Output root |
| --- | --- | --- | --- | --- |
| aRAG | `--backend arag` | `844b8ded-…745832` (built-in) | n/a | `考试结果-v3-arag` |
| Dify hybrid (plain) | `--backend dify` | `fc0250d7-…83dc18` (built-in) | `hybrid_search` | `考试结果-v3-dify` |
| Dify 覆盖索引 (coverage) | `--backend dify --dify-search-method coverage_search --dataset-id c3ed4fcc-… --out-dir 评测考试/考试结果-v3-dify-new` | that id | `coverage_search` | `考试结果-v3-dify-new` (manual) |
| Dify GraphRAG | `--backend dify --graph-search --dataset-id 0a8b3810-… --out-dir 评测考试/考试结果-v3-dify-graphrag` | that id | `hybrid_search` + graph | `考试结果-v3-dify-graphrag` (manual) |
| Dify parent-child | `--backend dify --dataset-id 260f9445-… --out-dir 评测考试/考试结果-v3-dify-父子` | that id | `hybrid_search` | `考试结果-v3-dify-父子` (manual) |

`coverage_search` ("覆盖索引") is new in v3: `build_dify_request` sends `reranking_mode=null`, `weights=null`, `graph_search=null` for that mode (no reranking/weight override). `--graph-search` is **Dify-only and mutually exclusive with `coverage_search`** (parser error otherwise). Parent-child vs plain chunking is a property of the Dify dataset, selected purely by `--dataset-id`.

```bash
# aRAG smoke run (3 q) → 考试结果-v3-arag
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --backend arag \
  --exam 评测考试/考卷-v3/考卷-2026-07-15-03.json --limit 3 --primary-k 5

# Dify coverage_search full run → 考试结果-v3-dify-new (needs local Dify console at localhost:5001)
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/retrieval_eval.py --backend dify --dify-search-method coverage_search \
  --dataset-id c3ed4fcc-... --out-dir 评测考试/考试结果-v3-dify-new \
  --exam 评测考试/考卷-v3/考卷-2026-07-15-03.json --dataset-revision <rev>

# entity-bridge multihop run (requires explicit --dataset-id; refuses built-in defaults)
RETRIEVAL_COOKIE=... RETRIEVAL_XSRF_TOKEN=... \
  python 评测脚本/entity_bridge_multihop_eval.py --backend dify \
  --dataset-id <kb-id-with-multihop-corpus> --dataset-revision <rev> \
  --exam 评测考试/考卷-多跳/考卷-2026-07-17-01.json

# paired comparison of two completed/full runs → 考试结果-v3-比较 (or 考试结果-多跳-比较)
python 评测脚本/retrieval_eval.py --compare <RUN_A_results.json> <RUN_B_results.json>
```

`--dataset-revision` is a free-text label you supply to assert the remote KB was ingested from the same local snapshot; **omitting it marks the run not-comparison-eligible** (the multihop `--compare` rejects `unverified` outright). `--request-k` (default 10) is sent to Dify as `retrieval_model.top_k` (Dify-only; aRAG ignores it — request-side K is unsupported by aRAG, which returns whatever it returns). `--eval-k`/`--primary-k` (defaults `[1,3,5,10]`/`5`) are the **client-side evaluation windows**; `--char-budgets` (default `[1000,2000,4000]`) are normalized-character truncation budgets. Comparing backends fairly means holding `--primary-k`/`--eval-k`/`--char-budgets` constant.

Credentials are environment-only: aRAG and Dify Console use `RETRIEVAL_COOKIE` / `RETRIEVAL_XSRF_TOKEN`; Dify Dataset API uses `DIFY_DATASET_API_KEY`. Keys and request headers must never be written to manifests, results, logs, commits, or reports.

## How the v4 evaluator works (retrieval_eval_v4.py)

Self-contained (does **not** import v3). `normalize_text` is byte-identical to v3's and a unit test asserts that — the two must never drift, or span-matching semantics silently diverge. `BackendProfile` is a plain class, not a `@dataclass`, because this repo's `importlib` loading pattern doesn't register modules in `sys.modules` and `@dataclass` fails under it.

### Backend profiles — one JSON per RAG product
`评测脚本/后端配置/{arag,dify-console,dify-dataset-api}.json`; external products via `--backend-profile <path>`. A profile declares `url_template`, `auth` (`cookie_csrf`/`bearer_env`/`header_env`/`none`, credentials named as env vars only), `request_template` (`{query}`/`{top_k}`/`{dataset_id}` placeholders keep their type when they are the whole string), `records_path`, and a `field_map` of dotted paths. **All metric math stays backend-agnostic** — adding a product is a config file, not code.

### Document-name gate (the cross-product killer)
Claim matching compares `document_key(name)` — basename, no extension, NFKC, lowercased, whitespace-stripped — not raw equality. `--document-alias-map` handles products that rename. After `PREFLIGHT_RESPONSES` (3) non-empty responses, if **zero** returned keys match the declared corpus, the run aborts with **exit 4**. Without this, a product whose names differ by `.md` scores zero on the whole exam and nothing says why.

### Five parallel headline metrics (never averaged together)
`query_macro_claim_recall@primary_k`, `complete_evidence_chain_rate@primary_k`, **`budget_claim_recall@primary_budget`** (top-k truncated to a normalized-char budget — the only chunking-neutral read, and mandatory when comparing products because substring matching inherently rewards bigger chunks), **`abstention_auc`** (Mann-Whitney AUC of answerable vs unanswerable top-1 score; empty response = `-inf`; threshold-free, so nobody can game it by tuning a cutoff), and **`bridge_claim_recall@primary_k`** (recall restricted to `hop_role == "bridge"` claims — the single read for real multi-hop). All CIs use **cluster-level** bootstrap (`cluster_id`). `sanity` questions form their own pass/fail bucket and never enter the headline. `novel_claim_rank_score` is demoted to diagnostics; `off_target_chunk_rate@k` is a new zero-annotation precision axis.

### Entity-bridge multi-hop (the `multihop` block)
**The gap between `endpoint_claim_recall` and `bridge_claim_recall` is the read for "can this product actually hop"** — if they are close, the bridge document was reachable from the query alone and the hop was never real. `bridge_only_miss_rate` counts the signature failure (both endpoints found, connecting document missed); `path_status` splits questions into `complete`/`bridge_missing`/`endpoint_missing`/`multiple_missing`/`supporting_missing`; `by_chain` breaks everything down per chain.

### Comparison guards
`comparison_eligible` also requires a verified `dataset_revision`, a clean git tree, and no unmatched document names. `--compare` takes **N** runs. Different products having different request bodies is expected and allowed; **the same `backend_profile` appearing twice with different `request_config` is a hard error** (exit 5, override with `--allow-config-diff`) — that is exactly the v3 trap. Questions that errored in any run are **excluded**, not scored 0. Pairwise deltas get cluster-clustered bootstrap CIs, randomization p-values, and Holm-adjusted p. Manifest records `evaluator_script_sha256` + `backend_profile_sha256`.

### v4 exam schema (4.1) deltas from v3
`question_role` (`scored`/`sanity`/`unanswerable`) replaces the `scored` bool; every question needs a declared `cluster_id`; every text claim needs **≥2 `accepted_spans`** each 12–40 normalized chars and **occurring exactly once** in its source document; claims carry `claim_type` (`anchor`/`passage`) for split sub-recall; `exam_meta.retrieval_protocol` (`primary_k`/`eval_ks`/`request_k`/`char_budgets`/`primary_budget`) is **authoritative** — CLI flags override it but print a warning and are recorded as `cli_override`.

**4.1 adds the multi-hop contract.** `cross_doc_chain` questions must carry `hop_design` (`chain_id`, `relation_type`, exactly 2 `endpoint_entities` that appear verbatim in the question, `endpoint_documents`, `bridge_documents`, `bridge_entities`) and every one of their claims must carry `hop_role` (`endpoint`/`bridge`/`supporting`) matching its document's declared role; ≥2 endpoint claims and ≥1 bridge claim are required. `exam_meta.bridge_chains[]` declares the chains. **Validation hard-fails if any bridge document contains an endpoint entity** — that is the one guarantee that makes the hop real. Non-chain questions must not declare these fields.

## The v4 exam-generation pipeline (评测脚本/考卷生成/)

The exam is a deterministic projection of a **fact ledger**; the author never hand-picks a span. Order: `synthesize_corpus.py` (template + value table → sibling corpus + ledger `facts`段) → `extract_spans.py` (derives each span **from the written corpus** by expanding a short `locator` to a sentence fragment, then verifying length and in-document uniqueness) → `build_exam.py` (ledger → exam JSON, filling claims/negatives/evidence_chain/counts/corpus SHA) → `audit_exam.py` (gates G1–G9) → `--validate-only` → `screen_candidates.py`.

`audit_exam.py` gates beyond the validator: span uniqueness (G1), ≥300-char separation between different questions' spans in one document so a chunk can't hit two questions (G2), GT span must carry the fact's discriminating value (G3), hard negatives must not come from a positive document and must carry a different value (G4), `reference_answer` must contain every claim's value (G5), the question must **not** (G6), at least one span per question must be lexically distant from the query (G7), claim balance + ASCII filenames (G8), cluster sizes (G9, default ≥3 — clustered bootstrap variance depends on the *number* of clusters, not their size), bridge name isolation (G10), **chain evidence heterogeneity (G11: pairwise char-3 Jaccard between different claims' first spans ≤ 0.35)**, and hop_role consistency (G12). Asset claims are exempt from G3/G5/G6 (their "value" is an image URL).

**G11 exists because v4's first `cross_doc_chain` questions were not multi-hop at all.** Their three "hops" were the same sentence with a different number in three sibling documents — inter-claim span similarity 0.783 against the reference exam's 0.030 — so one semantic match retrieved all three. They scored 0.889, higher than single-document disambiguation at 0.438, which is exactly backwards and falsified the design. The corpus had **zero** cross-document references, so no chain was structurally possible. The current chains measure 0.063.

**G13 lives in `screen_candidates.py` (`--check-bridge-unreachable`) and is the executable falsifiability test**: run the lexical baseline on the question text alone; the bridge documents must **not** appear in the top-10 and the endpoint documents **must**. A reachable bridge means the hop can be skipped. It caught two reachable bridges and two unreachable endpoints on the first build — the cause was that every chain document shared the same filler paragraphs, creating spurious lexical similarity, so the filler now carries each document's own subject name.

`screen_candidates.py` runs a dependency-free char-bigram BM25 over product-agnostic 200/50 sliding chunks — deliberately **not** one of the systems under test, so the exam can't overfit to them. It separates `floor_by_design` (low-lexical-overlap questions a lexical baseline *should* miss) from `floor_suspicious` (untagged zero-scorers, which need human review). Current pilot: mean baseline recall 0.5048, 1/35 ceiling questions (2.9%), low-overlap questions 0.391 vs the rest 0.722, zero suspicious floors.

## How the v3 evaluator works (retrieval_eval.py)

One file. `main` → `discover_exam_files` → `load_and_validate_exam` (strict schema + corpus hash + span-existence check) → `evaluate_exam` (per-question API call, dispatched by backend) → `compute_metrics_at_k` (per k) + `compute_question_metrics` → `aggregate` → `write_outputs`. Backend differences are isolated to `build_headers`, `_post_json`/`call_arag_api`/`call_dify_api`, and `parse_arag_response`/`parse_dify_response` — the last normalizes both response shapes (aRAG `content[]`, Dify `records[].segment`) into one Chunk dict, so **all metric math is backend-agnostic**. (`relevance_counterfactual` re-sorts chunks by `relevance_score` and recomputes, to measure what trusting score-over-server-order would yield.)

### v3 Exam schema (the contract)
Each question carries Ground Truth as **atomic `claims`**, not `retrieved_chunks`:
- `claims[]` — the required evidence: `{id, kind: text|asset, source_document, section, accepted_spans[]}`. Each `accepted_span` must be a **verbatim substring of its source doc after `normalize_text`**, normalized length 8–120 for text. A `(source_document, normalized_span)` pair must **not be reused** across scored questions (`claim_reuse_policy`).
- `negative_evidence[]` — hard negatives (same shape, no `kind`); positive and negative spans must not overlap.
- `evidence_chain` — must list this question's claim IDs, in order (used for human review / chain completeness, not re-weighting).
- `scored` bool — scored questions can't be `diagnostic`; unscored questions must be `unanswerable_diagnostic` with `expected_behavior: "no_relevant_evidence"` and empty `claims` (these 4 diagnostic questions don't enter the headline metrics).
- `difficulty` ∈ `simple|medium|hard|diagnostic`; `primary_type` ∈ 7 types (`single_doc_fact`, `single_doc_multi_claim`, `cross_doc_compare`, `cross_doc_chain`, `disambiguation_hard_negative`, `asset_reference_retrieval`, `unanswerable_diagnostic`); `tags[]` (e.g. `low_lexical_overlap`, enforced: max char-3gram Jaccard between query and spans ≤ 0.25).
- `exam_meta.question_counts` must match actual; `exam_meta.design_constraints` (`min_claims_per_document`, `max_claim_share_per_document`, `min_low_lexical_overlap_questions`, `min_hard_negative_questions`) are enforced.
- `exam_meta.corpus.documents[].sha256` — **SHA-256-pinned**; drift blocks the run unless `--allow-corpus-drift` (diagnostic-only).

`load_and_validate_exam` enforces all of this **before any network call**, including verifying that every `accepted_span` survives `normalize_text` and exists as a substring of its source document.

### Scoring model — read this before touching metrics
- **Claim match is the core primitive.** A `claim` counts as hit only if one of its `accepted_spans` (as a normalized substring) appears inside a retrieved chunk whose `document_name` equals the claim's `source_document`. Right document + wrong section = not a hit. Matching runs through `normalize_text` (NFKC, dash/quote folding, Markdown image/link handling, symbol stripping, whitespace removal, lowercase), so spans and corpus text must survive that normalization identically.
- **Three headline metrics at `--primary-k`** (default 5), each with a bootstrap 95% CI (10 000 resamples, fixed seed `BOOTSTRAP_SEED=20260715`): `query_macro_claim_recall` (mean per-question claim recall), `complete_evidence_chain_rate` (fraction of questions where *all* claims hit), `novel_claim_rank_score` (mean of `1/log2(first_hit_rank+1)` over claims — a DCG-like novelty rank score). Headline is only emitted if `run_status="completed"`.
- **`claim_recall@k` = hit claims / total claims** is the per-question primitive; `claim_micro_recall` is the micro-average. `document_recall@k` (chunking-neutral), `duplicate_document_rate`, `hard_negative_intrusion`, `asset_source_coverage`, `response_depth`, `ranking_anomaly`, latency p50/p95, and `by_type`/`by_difficulty`/`by_tag`/`by_gt_document_count` are **diagnostics** — they do not re-weight the headline.
- **Char-budget curves** (`char_budget_metrics`): retrieved chunks are truncated to a normalized-character budget and claim recall is recomputed — a context-window-aware view that is chunking-neutral. **K-curves** (`k_curves`) run all metrics across `--eval-k`.
- **`relevance_counterfactual`**: re-sorts chunks by `relevance_score` and recomputes the three headline metrics, quantifying the cost of trusting server order over score order.
- **Errored question** = recorded as a 0-score result, run continues. **`AuthenticationError` (401/403)** raises `EvaluationAborted`, writes partial results with `run_status="aborted_auth"` and a null headline. `_post_json` retries only transient failures (network errors, 408, 429, 5xx) with exponential backoff.
- **`comparison_eligible`** = `completed` + `run_scope="full"` + no corpus drift. Smoke runs (`--limit`) print a `> ⚠️ …冒烟运行` banner so partial scores aren't mistaken for full-exam.

### Outputs and manifest
`write_outputs` writes four files to `<out-dir>/<exam_id>/<timestamp>/`: `results_*.json` (per-question detail + truncated raw responses), `summary_*.json` (headline + diagnostics), `manifest_*.json` (**reproducibility manifest**: git commit/dirty, exam_sha256, corpus, backend, dataset_id, `dataset_revision`, `request_config`, eval params, python version; "credentials and request headers are never persisted"), and `report_*.md` (human-readable; smoke/corpus-drift banners). `build_manifest` records `request_k_support: "applied"` for Dify / `"unsupported_by_backend"` for aRAG. All JSON is `ensure_ascii=False`, 2-space indent. Exit codes: 0 ok, 2 validation/missing-dataset-id, 3 auth, 5 comparison failure.

### Paired comparison (`--compare RUN_A RUN_B`)
`compare_runs` loads two `results_*.json`, requires both be `completed`/`full`/`comparison_eligible`, with matching `exam_id`/`exam_sha256`/`corpus`/`primary_k`/`eval_k` and the same scored question IDs in order. For each of the three headline fields it reports `left_mean`/`right_mean`/`right_minus_left`, a **paired bootstrap 95% CI** on per-question deltas, and a **paired randomization p-value** (seed-stable). Output: `comparison_*.json` + `.md` under `考试结果-v3-比较` (override with `--out-dir`).

## How the entity-bridge specialization works (entity_bridge_multihop_eval.py)

A thin layer that loads `retrieval_eval.py` as `core` via `importlib` and reuses its HTTP, `normalize_text`, and Claim metrics. It adds: `validate_entity_bridge_design` (specialized schema), `compute_bridge_metrics_at_k` (chain metrics), `aggregate_bridge` (chain-level aggregation), and its own `compare_runs`/`write_comparison`. `main` calls `core.evaluate_exam`/`core.aggregate` then `augment_results`/`aggregate_bridge`/`apply_comparison_guards`.

### Extra schema (on top of v3)
- `exam_meta.entity_bridge_design`: `benchmark_kind="entity_bridge_multihop"`, `chains[]` (`id`, `relation_type`, `bridge_entities`), `questions_per_chain`, `min_image_reference_questions`, `hard_negative_policy`.
- Per question: `chain_id`, `relation_type` (`equity_control`|`service_contract`, must match the chain's), `evaluation_focus` (`relation_path`/`boundary_path`/`temporal_path`/`governance_path`/`workflow_path`/`evidence_path`/`hard_negative_path`/`asset_path`), `endpoint_entities` (exactly 2, each must appear verbatim in `question`), `endpoint_documents`/`bridge_documents`/`supporting_documents` (roles cannot overlap), and each `claim.hop_role` (`endpoint`|`bridge`|`supporting`, must match the document's role).
- **Name isolation**: a `bridge_document` must **not** contain either endpoint entity's name (enforced) — bridges connect endpoints *without* mentioning them.
- **PNG image assets**: `corpus.image_assets[]` declares `{name, sha256, media_type:"image/png", width, height}`; the declared set must **exactly** match every `*.png` on disk. Each PNG is parsed (`_read_png_dimensions`): IHDR/IDAT/IEND structure, per-chunk **CRC32**, and dimensions must match the declaration. Asset claims must reference exactly one declared image. Asset/image-reference metrics measure the **Markdown path string** being retrieved — the manifest records `image_reference_semantics: "retrieved Markdown path string; local PNG hash/dimensions validated separately; no visual understanding score"`.
- Each question must cover ≥3 docs (both endpoints + all bridges), with ≥2 endpoint claims and ≥1 bridge claim. `hard_negative` questions must share a core institution entity in a negative span.

### Metrics and aggregation
Per-k bridge metrics: `endpoint_text_claim_recall`, `bridge_text_claim_recall`, `supporting_text_claim_recall`, `complete_core_bridge_chain`, `complete_declared_text_chain` (core + supporting), `image_reference_claim_recall`, `complete_image_reference_chain`, `endpoint`/`bridge_document_recall`, `bridge_only_document_miss`, and `path_status` (`complete`/`bridge_missing`/`endpoint_missing`/`supporting_missing`/`multiple_missing`/`image_reference_*`). `aggregate_bridge` reports both a **question macro** and a **chain macro** (mean per chain, then equal-weight over independent chains) with chain-level bootstrap CIs. **`inference_warning`: questions within a `chain_id` are correlated; significance inference must use the chain as the clustering unit — with few independent chains, treat CIs/p-values as exploratory.**

### Stricter comparison
`apply_comparison_guards` sets `comparison_eligible = false` if any of: base run not eligible, `dataset_revision == "unverified"`, document corpus drift, or image-asset drift. The multihop `--compare` additionally requires both runs' `dataset_revision` to be verified and matching, matching `evaluator` **metrics versions** (`specialized_metrics_version`/`core_metrics_version`), and matching `chain_id`/`primary_type` shape. A difference in `specialized_script_sha256`/`core_script_sha256` alone does **not** block the comparison — it is surfaced as an `evaluator_notes` audit line in the comparison JSON/Markdown, so a cosmetic script edit (rename, docstring, User-Agent) does not invalidate an older archived run. Its `compare_runs` reports **clustered** (by chain) bootstrap CI + randomization p-value on chain-level deltas, with `inference_unit: "chain_id"`. Output: `考试结果-多跳-比较`. The multihop script **refuses to run live without `--dataset-id`** (exit 2); point it at a KB that has the multi-hop corpus ingested.

## Cross-backend comparison caveat

`claim_recall` is substring-based: a span hits only if it appears inside a returned chunk from the right document, so the metric is still **chunk-boundary-sensitive** (a backend whose chunks split differently can miss a span that another backend's chunk contains whole). That is exactly why `document_recall@k` and the `char_budget` curves are reported alongside the headline. The v3 exam `考卷-2026-07-15-03` is a regression exam on the existing 10 documents, not a blind benchmark; corpus pinning and paired comparison improve auditability but do not turn it into an unseen benchmark.

## Conventions specific to this repo
- Exam ids and files follow `考卷-YYYY-MM-DD-NN`; v4 exams live under `考卷-v4/`, v3 under `考卷-v3/`, multihop under `考卷-多跳/`.
- v4 corpus filenames are **ASCII** (`A1-anticoag-2023.md`) with the Chinese title as the document's H1 — Chinese filenames risk not round-tripping through some products' document-name field, which would zero out the whole exam. Directory names stay Chinese.
- v4 output roots are `考试结果-v4-<profile>` (auto) and `考试结果-v4-比较`; always pass `--out-dir` when a product needs its own archive.
- Keep metric math pure and backend-agnostic; isolate HTTP and filesystem I/O. Any new backend's differences go inside the header/call/parse trio.
- Python 3, UTF-8, four-space indent, type hints, `snake_case` functions, `UPPER_SNAKE` constants, descriptive exception names. JSON output: `ensure_ascii=False`, 2-space indent; keep `metrics_version`/report terminology synced when metric semantics change.
- For metric/schema/retry/backend/output changes, update the matching test file (`test_retrieval_eval_v4.py` / `test_exam_builder.py` / `test_retrieval_eval.py` / `test_entity_bridge_multihop_eval.py`) in the same change, and run py_compile + `pytest tests/` + all three `--validate-only` commands.
- `tests/fixtures/语料-v4单测/` is a 5-document fixture corpus for the v4 and generator suites (2 siblings + a 3-document bridge chain). Editing it changes SHA-256s that tests compute at runtime, but the derived spans in the fixtures are hand-written — re-run `pytest tests/` after any edit.
- Commit code, corpus, and generated output as **separate** changes; don't commit `.venv/`, `.idea/`, `.qoder/`, `.claude/`, `.pytest_cache/`, or `__pycache__/`.
- Keep manual online examples outside `tests/`; importing a test module must never trigger a live request.
