---
description: Run a full multi-step coding task through the orchestrator pipeline — baseline test, debug if red, verify, code review, then commit. Pass the task description (e.g. /orchestrate "add CSV column_mapping validation and tests").
agent: orchestrator
---
Execute the following task through the orchestrator pipeline: $ARGUMENTS

Drive the standard workflow (see the orchestrator agent prompt and the
`message-board` skill):

1. Open a `TASK-NNN` COORD entry on `.kilo/board/BOARD.md` describing the goal.
2. **Baseline** — dispatch the **tester** to run `python -m pytest -q`.
3. **Fix** — if RED, open a `BUG` for the **debugger** with the minimal
   failure snippet and have it fix the code.
4. **Verify** — re-dispatch the **tester** until GREEN.
5. **Review** — open a `REVIEW` for the changed files; dispatch **code-review**.
   Route blocking issues back to the **debugger** and re-verify.
6. **Commit** — when APPROVED and GREEN, open a `GIT` entry and dispatch
   **git-manager** to commit (push only if explicitly requested).
7. Close the `TASK-NNN` DONE with a one-line summary.

Do not commit or push unless step 6 is reached. Report the outcome and any
BLOCKED items, referencing board IDs.
