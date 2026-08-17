---
name: run-tests
description: How to run the PUF_RA pytest suite, interpret failures, and report minimal results to the shared message board. Use when verifying code, establishing a baseline, or confirming a fix.
---

# Run Tests (PUF_RA)

The repo is a Python project with `pytest` configured in `pyproject.toml`
(`testpaths = ["tests"]`, `test_*.py`). A `.venv` is present.

## Commands

```bash
# Full suite (preferred baseline)
python -m pytest -q

# Single file / test
python -m pytest -q tests/test_import.py
python -m pytest -q "tests/test_import.py::test_import_bibtex_dedup"

# If pytest missing from PATH
.venv/bin/python -m pytest -q     # linux/mac
.venv/Scripts/python -m pytest -q  # windows
uv run pytest -q                  # if uv is set up
```

## Interpreting output

- The final summary line is the source of truth: `25 passed` or
  `2 failed, 23 passed`.
- For each failure, capture ONLY the assertion and the 1–2 lines that explain
  it (file:line + the `assert`/exception). Do not paste the whole traceback.
- Record the exact failing test identifiers (e.g.
  `tests/test_import.py::test_import_bibtex_dedup`) so the debugger can target
  them without re-running.

## Reporting to the board

Post a `TEST` entry to `.kilo/board/BOARD.md` (see `message-board` skill):

```markdown
## [MSG-NNN] pytest baseline
- Type: TEST
- From: tester
- To: orchestrator
- Status: DONE
- Priority: normal
- Created: YYYY-MM-DD
- Updated: YYYY-MM-DD
- Body: |
  2 failed, 23 passed.
  - tests/test_import.py::test_import_bibtex_dedup  (AssertionError: ...)
- Result: RED — 2 failures, see above.
```

If green, say so explicitly: `Result: GREEN — 25 passed.`
