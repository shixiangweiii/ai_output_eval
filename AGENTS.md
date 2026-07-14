# Repository Guidelines

## Project Structure & Module Organization

This repository is a compact RAG retrieval-evaluation workspace. `评测脚本/retrieval_eval.py` is the executable evaluator, and `评测脚本/requirements.txt` contains its Python dependency. Source documents live under `生成的原始文档语料/<date>/`; generated exam JSON belongs in `基于语料生成的评测集/考卷-<date>-<sequence>/`. Keep API request/response samples in `相关接口调用例子/` and methodology notes in `评测相关参考文档/`. Runtime reports are written to `评测结果/<exam-id>/<timestamp>/` and should be treated as generated artifacts. Do not commit `.venv/`, `.idea/`, `.qoder/`, or `__pycache__/` content.

## Build, Test, and Development Commands

Run commands from the repository root:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r 评测脚本/requirements.txt
python -m py_compile 评测脚本/retrieval_eval.py
python 评测脚本/retrieval_eval.py --limit 3 --top-k 5
```

The first two commands create the local environment. `py_compile` is the current offline syntax check. The final command performs a small live smoke test; pass `--exam-dir` and `--out-dir` when the built-in absolute paths do not match your checkout. Use `--help` for all evaluator options.

## Coding Style & Naming Conventions

Use Python 3, UTF-8, four-space indentation, type hints, and short docstrings for public functions. Follow the existing style: `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants, and descriptive custom exceptions such as `RetrievalError`. Keep metric calculation pure where possible; isolate HTTP and filesystem work. Preserve Chinese directory names and the established `考卷-YYYY-MM-DD-NN` naming pattern. Format JSON with two-space indentation and `ensure_ascii=False`.

## Testing Guidelines

No automated test suite or coverage threshold is configured yet. For metric or parsing changes, add focused tests under `tests/` using `pytest`, named `test_<behavior>.py`, and mock HTTP responses. At minimum, run the syntax check and a `--limit 3` smoke test. Verify the generated `summary_*.json`, `results_*.json`, and `report_*.md` for schema and metric regressions.

## Commit & Pull Request Guidelines

This checkout has no Git history, so use concise imperative commits, optionally scoped, for example `eval: handle empty retrieval results`. Keep code, corpus, and generated-output changes separate. Pull requests should explain the evaluation scenario, commands run, affected exam or corpus IDs, and metric changes; include a short report excerpt when output changes.

## Security & Configuration

Provide `RETRIEVAL_COOKIE` and `RETRIEVAL_XSRF_TOKEN` through environment variables only. Never add live cookies, tokens, private document content, or unsanitized API responses to commits or review comments.
