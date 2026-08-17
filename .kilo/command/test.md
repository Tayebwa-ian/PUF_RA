---
description: Run the PUF_RA test suite via the tester subagent and report pass/fail. Pass a test path to scope it (e.g. /test tests/test_import.py::test_import_bibtex_dedup), or omit for the full suite.
agent: orchestrator
---
Run the standard testing pipeline for: $ARGUMENTS

Use the `run-tests` skill. Delegate to the **tester** subagent via the Task
tool to run `python -m pytest -q` (scoped to $ARGUMENTS if provided). Have the
tester post a `TEST` entry to `.kilo/board/BOARD.md` and report the summary
back. If the result is RED, follow the orchestrator routing rules: open a `BUG`
for the **debugger** with the minimal failure snippet, verify the fix with the
**tester**, then proceed to review/commit only if the user asked for the full
pipeline. Report the final status concisely, referencing the board entry.
