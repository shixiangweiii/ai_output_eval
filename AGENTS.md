# Repository Guidelines

## Project Scope

This repository is a compact RAG retrieval-evaluation workspace. The active evaluator measures whether the selected knowledge base returns the Ground-Truth Claims required by each question. It does **not** score final-answer correctness, reasoning quality, refusal behavior, conversation memory, or tool calls.

`评测脚本/retrieval_eval_v4.py` is the source of truth for the current general evaluator and metrics v4.1; it is self-contained and used for horizontally evaluating different RAG products. `评测脚本/retrieval_eval.py` (v3.0) is **frozen**: keep it working so archived `考试结果-v3-*` runs stay reproducible and so the multihop specialization keeps loading it as `core`, but do not add new v3 runs. `评测脚本/entity_bridge_multihop_eval.py` is a specialized layer over the v3 core that adds entity-bridge, chain-level, and image-reference diagnostics. The former metrics-v2 evaluator and its retired `评测考试/考卷/` inputs are no longer part of the active repository.

`评测脚本/考卷生成/` holds the v4 exam-generation pipeline. The exam is a deterministic projection of a fact ledger; spans are derived from the written corpus by script, never hand-picked. Keep the ledger and its intermediates out of both corpus directories (they would be ingested into the knowledge base) and exam directories (`discover_exam_files` globs `*.json`).

## Repository Map

- `评测脚本/retrieval_eval_v4.py`: current Claim-based evaluator — profile-driven backends, document-name gate, five headline metrics, entity-bridge multi-hop, cluster inference, N-way comparison.
- `评测脚本/后端配置/*.json`: one backend profile per RAG product; credentials are referenced by environment-variable name only.
- `评测脚本/考卷生成/`: `synthesize_corpus.py`, `bridge_chains.py`, `extract_spans.py`, `build_exam.py`, `audit_exam.py`, `screen_candidates.py`, plus the fact ledger and its resolved form.
- `评测脚本/retrieval_eval.py`: frozen v3 evaluator, still the `core` dependency of the multihop specialization.
- `评测脚本/entity_bridge_multihop_eval.py`: entity-bridge multi-hop specialization built on `retrieval_eval.py`.
- `评测脚本/requirements.txt`: evaluator runtime dependency (`requests`).
- `评测脚本/requirements-dev.txt`: runtime and offline test dependencies.
- `评测脚本/requirements-examples.txt`: optional `openai` dependency for manual model API examples.
- `tests/test_retrieval_eval_v4.py`: v4 evaluator unit tests; all HTTP is mocked.
- `tests/test_exam_builder.py`: exam-generation pipeline tests (span derivation, ledger projection, quality gates, difficulty baseline).
- `tests/fixtures/语料-v4单测/`: five-document fixture corpus for both suites above — two sibling documents plus a three-document entity-bridge chain.
- `tests/test_retrieval_eval.py`: frozen v3 evaluator unit tests; all HTTP is mocked.
- `tests/test_entity_bridge_multihop_eval.py`: specialization tests, including the real multi-hop exam and local PNG validation.
- `生成的原始文档语料/2026-08-04-02-真多跳/`: v4 corpus — 26 documents: two sibling families (2 × 5), four entity-bridge chains (14), two fake-bridge decoys. ASCII filenames, Chinese H1 titles.
- `评测考试/考卷-v4-含真多跳题/`: v4 exams (schema 4.1). Superseded exams must be kept out of this directory, which is globbed, and never edited — an archived exam's `exam_sha256` is recorded in the run manifests. The 4.0 pilot exam and its single run were deleted outright in `a1a9b62`; only its fact ledgers remain under `评测脚本/考卷生成/`.
- `生成的原始文档语料/2026-07-14-01/`: 10-document corpus used by the frozen v3 exam.
- `生成的原始文档语料/2026-07-17-多跳-无词面重合/`: 10-document entity-bridge corpus plus PNG assets.
- `评测考试/考卷-v3/`: general Claim-based exams.
- `评测考试/考卷-多跳专项/`: entity-bridge specialized exams.
- `评测考试/考试结果-v3-<backend>/`: generated general-evaluator artifacts.
- `评测考试/考试结果-v4-<考卷+配置>/`: generated v4 artifacts; archives are named after the exam and retrieval configuration, not the profile.
- `评测考试/考试结果-多跳-<backend>/`: generated specialized artifacts.
- `评测考试所测检索召回接口/`: manual retrieval, agent-Q&A, and model API examples; these are not pytest tests.
- `评测相关参考文档/`: methodology and historical design notes. Historical v2 descriptions are context, not current runtime behavior.

Preserve the Chinese directory names and the `考卷-YYYY-MM-DD-NN` naming convention. Treat result directories as generated evidence, not source code.

## Setup and Commands

Run from the repository root and reuse `.venv`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 评测脚本/requirements-dev.txt

.venv/bin/python -m py_compile 评测脚本/*.py 评测脚本/考卷生成/*.py
.venv/bin/python -m pytest tests/ -q
.venv/bin/python 评测脚本/retrieval_eval_v4.py --validate-only --exam-dir 评测考试/考卷-v4-含真多跳题
.venv/bin/python 评测脚本/retrieval_eval.py --validate-only
.venv/bin/python 评测脚本/entity_bridge_multihop_eval.py --validate-only
```

The validation commands require no credentials or network access. v4 validates `评测考试/考卷-v4-含真多跳题/`; the frozen v3 evaluator validates `评测考试/考卷-v3/`; the specialization validates `评测考试/考卷-多跳专项/`, its corpus snapshot, and all declared PNG assets.

Rebuilding the v4 corpus and exam is fully offline and deterministic:

```bash
.venv/bin/python 评测脚本/考卷生成/synthesize_corpus.py --facts-out 评测脚本/考卷生成/事实台账-facts-02.json
.venv/bin/python 评测脚本/考卷生成/extract_spans.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02.json --facts 评测脚本/考卷生成/事实台账-facts-02.json
.venv/bin/python 评测脚本/考卷生成/build_exam.py --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json --out 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json
.venv/bin/python 评测脚本/考卷生成/audit_exam.py --exam 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json --ledger 评测脚本/考卷生成/事实台账-2026-08-04-02-resolved.json
.venv/bin/python 评测脚本/考卷生成/screen_candidates.py --exam 评测考试/考卷-v4-含真多跳题/考卷-2026-08-04-02.json --check-bridge-unreachable
```

Any corpus regeneration changes SHA-256 values: rebuild the exam and re-ingest the knowledge base before running live.

Manual model API examples are isolated from pytest. Install their optional dependency only when needed:

```bash
.venv/bin/python -m pip install -r 评测脚本/requirements-examples.txt
```

## Evaluator Contract

The general pipeline is:

`main` → `discover_exam_files` → `load_and_validate_exam` → backend request and normalization in `evaluate_exam` → Claim metrics → `aggregate` → `write_outputs`.

Each scored question carries atomic `claims`. A Claim is a hit only when a normalized `accepted_span` is contained in a retrieved chunk whose `document_name` exactly equals the Claim's `source_document`. Document-name recall without span recall is diagnostic only.

v4 matches Claims on a normalized document key (basename, no extension, lowercased) rather than raw string equality, and aborts with exit 4 when no returned document name matches the declared corpus — without that gate a product whose naming differs silently scores zero everywhere.

The five co-equal v4 headline metrics are Query Macro Claim Recall, Complete Evidence Chain Rate, Budget Claim Recall (chunking-neutral), Abstention AUC, and Bridge Claim Recall (entity-bridge multi-hop). v3's headline was the first two plus Novel Claim Rank Score, which v4 demotes to diagnostics.

For multi-hop, read `bridge_claim_recall` against `endpoint_claim_recall`: if the two are close, the bridge document was reachable from the question alone and the hop was never real. `bridge_only_miss_rate` isolates the signature failure — both endpoints found, connecting document missed. Validation hard-fails when a bridge document contains an endpoint entity; that name isolation is the only thing that makes the hop unavoidable.

There is no composite total and no final-answer score; never average the headline metrics together. Because substring matching inherently rewards larger chunks, any cross-product conclusion must cite Budget Claim Recall alongside Claim Recall. Keep request-side K distinct from client-side evaluation windows. Report document recall, off-target chunk rate, hard-negative intrusion, duplicate-document rate, anchor/passage sub-recall, k curves, char-budget curves, and latency as diagnostics rather than re-weighting the headline metrics. `sanity` questions are a pass/fail bucket outside the headline; `unanswerable` questions feed only Abstention AUC.

v4 clusters statistical inference by `cluster_id`. `--compare` accepts N runs; it rejects same-profile runs whose `request_config` differs, and excludes rather than zero-scores questions that errored in any run.

The general evaluator validates exam schema, corpus SHA-256 values, and accepted-span existence before any live request. `--allow-corpus-drift` is diagnostic-only and makes comparison evidence weaker.

The entity-bridge specialization adds endpoint, bridge, supporting-document, image-reference, and complete-path metrics. Statistical comparison clusters questions by `chain_id`; with few independent chains, treat confidence intervals and p-values as exploratory.

## Backends and Outputs

In v4 a backend is a JSON profile under `评测脚本/后端配置/`, selected with `--backend-profile <name|path>`. Adding a RAG product means writing a profile — URL template, auth mode, request-body template, response field paths — not changing code. Keep all metric math backend-agnostic.

The frozen v3 evaluator still selects backends with `--backend arag|dify`, `--dify-api-mode console|dataset-api`, `--dify-search-method hybrid_search|coverage_search`, and the Dify-only `--graph-search`.

v4 runs default to `评测考试/考试结果-v4-<profile>` and comparisons to `评测考试/考试结果-v4-比较`. v3 runs default to `评测考试/考试结果-v3-<backend>`; specialized runs to `评测考试/考试结果-多跳-<backend>`. Use `--out-dir` for named Dataset/index variants.

Each completed general or specialized run writes:

- `results_<timestamp>.json`
- `summary_<timestamp>.json`
- `manifest_<timestamp>.json`
- `report_<timestamp>.md`

JSON output must use two-space indentation and `ensure_ascii=False`. Keep `metrics_version`, manifest fields, and report terminology synchronized when semantics change.

## Corpus and Comparison Discipline

The local corpus must match the live Dataset selected by `--backend` and `--dataset-id`. Editing local documents has no effect until the corpus is re-ingested. Record Dataset revision, request configuration, exam/corpus hashes, chunking/indexing mode, and re-ingestion state.

Only call a comparison strict A/B when both runs are complete/full and match on exam hash, corpus snapshot, evaluation parameters, Dataset/index revision, and scored-question order. Dataset drift makes the result a descriptive cross-index comparison.

This is not theoretical. Two archived v3 Dify runs differed by 0.1204 Query Macro Claim Recall at `p=0.0079`; re-scoring both under a matched `score_threshold` left only 0.0278. Roughly 77% of that "result" was a retrieval-configuration difference the v3 comparison never inspected. Before attributing any gap to retrieval quality, diff the request configurations.

## Coding and Testing Conventions

Use Python 3, UTF-8, four-space indentation, type hints, and short public-function docstrings. Use `snake_case` for functions and variables and `UPPER_SNAKE_CASE` for constants. Keep metric calculation pure where possible and isolate HTTP/filesystem work.

For metric, schema, retry, backend, comparison, or output changes, update the matching test file in the same change. At minimum run syntax compilation, the full offline test suite, and all three `--validate-only` commands. Run credentialed smoke tests only when the task explicitly requires live verification.

`retrieval_eval_v4.normalize_text` must stay byte-identical to the v3 implementation; a unit test enforces this. If one changes, change both in the same commit or archived comparisons become meaningless. Note that `BackendProfile` deliberately avoids `@dataclass`: this repository loads modules through `importlib` without registering them in `sys.modules`, which `@dataclass` cannot tolerate.

For exam or corpus changes, also run `audit_exam.py` and `screen_candidates.py --check-bridge-unreachable`. Corpus and exam files are generated artifacts — regenerate them through the pipeline rather than editing by hand, so spans stay machine-derived.

When adding or editing multi-hop chains, never let chain documents share filler prose verbatim: identical padding creates spurious lexical similarity that drags bridge documents into the question's own results and breaks G13. Give each document filler that names its own subject.

## Security and Generated Files

Never hardcode or commit cookies, CSRF/XSRF tokens, API keys, private documents, or unsanitized responses. Pass aRAG/Dify Console credentials through `RETRIEVAL_COOKIE` and `RETRIEVAL_XSRF_TOKEN`, Dify Dataset API credentials through `DIFY_DATASET_API_KEY`, and model-example credentials through `DASHSCOPE_API_KEY`. Existing interface examples may contain stale credential-shaped values and must not be treated as reusable secrets.

Do not commit `.venv/`, IDE metadata, `.pytest_cache/`, `__pycache__/`, `.DS_Store`, or generated bytecode.
