---
name: implement
description: Standards and workflow for the coder subagent when implementing features in the PUF_RA repo — typing, tests, repo conventions, and how to report on the message board.
---

# Implement (Coder) — Standards

The `coder` subagent builds features and writes new code. It is the
implementation engine; the `debugger` handles failure-fixing. Use this skill
when the orchestrator assigns a `TASK-`/`MSG-` (or an `ARCH` recommendation) to
the `coder`.

## Engineering standards (senior-level)

- **Typing**: annotate every signature. No `Any` where a real type fits.
- **Naming**: descriptive `snake_case`; functions do one thing.
- **Structure**: single-responsibility modules; clear public interfaces;
  explicit errors (raise specific exceptions, don't swallow).
- **DRY/SOLID**: only where it earns clarity; never abstract prematurely.
- **Repo conventions**: mirror `src/`/`cli/` siblings; `snake_case`; **no
  unnecessary comments** (the repo avoids comments unless asked); keep
  `docs/` in sync if behavior changes.

## Repo orientation

- Schema: `src/db_schema.py` + `database_struct.sql`.
- Ingestion: `src/bibtex_parser.py`, `src/bibtex_importer.py`,
  `src/csv_importer.py`, `src/db.py`.
- Intelligence: `src/relevance.py` (keyword+BM25), `src/screening.py` (LLM),
  `src/snowball.py` (backward search).
- CLI: `cli/*.py`; entry point `puf` in `pyproject.toml`.

## Workflow

1. Read the assigned entry on `.kilo/board/BOARD.md` and any design note.
2. Read surrounding code to fit in cleanly.
3. Implement scoped to the feature; prefer adding over rewriting.
4. Write/extend tests (`tests/test_*.py`) and run them:
   `python -m pytest -q tests/test_<area>.py`.
5. Post a `CODER` entry: `Status: DONE`, `Result:` = files added/changed +
   tests added (repo-relative `:line`). If blocked, `Status: BLOCKED`.
6. Do not commit (the `git-manager` does, after review).

## Red lines

- No edits outside the feature scope; report unrelated issues as `INFO`/`BUG`.
- Untested code is not done. Self-verify before reporting.
