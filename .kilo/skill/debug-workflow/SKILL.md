---
name: debug-workflow
description: Repeatable workflow for reproducing and fixing test failures, exceptions, and lint errors in the PUF_RA repo, and reporting the fix to the shared message board.
---

# Debug Workflow (PUF_RA)

Use this skill when the orchestrator assigns you a `BUG` (typically forwarded
from a `TEST` failure or a `REVIEW` issue).

## Steps

1. **Read the board.** Open `.kilo/board/BOARD.md` and locate the `BUG` (or
   `REVIEW` issue) assigned to `debugger`. Take the minimal error snippet from
   the `TEST`/`REVIEW` entry. Do NOT re-run the full suite just to re-read an
   error the tester already captured.
2. **Reproduce narrowly.** Run the single failing test, or the smallest script
   that triggers the exception:
   ```bash
   python -m pytest -q "tests/test_import.py::test_import_bibtex_dedup"
   ```
3. **Locate root cause.** Read the relevant files under `src/`, `cli/`,
   `tests/`. Match the repo's Python style (typed, no unnecessary comments,
   existing naming). Pay attention to:
   - DB schema / foreign keys / dedup logic (`src/db.py`, `src/db_schema.py`)
   - BibTeX/CSV parsing & import (`src/bibtex_parser.py`,
     `src/bibtex_importer.py`, `src/csv_importer.py`)
   - Relevance / BM25 math (`src/relevance.py`)
   - Screening / snowball API calls (`src/screening.py`, `src/snowball.py`)
4. **Fix minimally.** Smallest correct change; do not refactor unrelated code.
5. **Verify locally.** Run the specific failing test(s) yourself to confirm
   green before reporting back.
6. **Report.** Update the board: set the `BUG` to `DONE` with `Result:`
   naming the files touched (repo-relative, with `:line`) and the tests now
   passing. If unfixable, set `Status: BLOCKED` with the blocker.

## Guardrails

- Only touch files related to the bug.
- Never commit — `git-manager` commits after review.
- If the real bug is a bad test, post an `INFO` note on the board and let the
  orchestrator decide; do not silently rewrite tests.
- Always state which tests now pass so the orchestrator can re-verify with the
  `tester`.
