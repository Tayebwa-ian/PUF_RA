---
description: Debug a failing test or error in the PUF_RA repo via the debugger subagent. Pass a test path, error description, or board MSG id (e.g. /debug tests/test_import.py::test_import_bibtex_dedup). If omitted, debug the latest failing TEST entry on the board.
agent: orchestrator
---
Debug the issue described by: $ARGUMENTS

Use the `debug-workflow` skill. Delegate to the **debugger** subagent via the
Task tool. Scope:
- If $ARGUMENTS names a test/error, reproduce and fix it.
- If empty, read `.kilo/board/BOARD.md` and take the latest RED `TEST` (or open
  `BUG`) entry as the target — do NOT re-run the suite just to re-read the
  error the tester already captured.

Have the debugger post a `BUG`/result entry with the files touched
(repo-relative, with :line) and the tests now passing. Then re-verify with the
**tester**. Report the fix and verification status concisely, referencing the
board entry.
