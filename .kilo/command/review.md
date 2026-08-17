---
description: Review changed files in the PUF_RA repo via the code-review subagent. Pass files/paths to scope (e.g. /review src/db.py cli/import.py), or omit to review the working-tree diff vs main.
agent: orchestrator
---
Review the changes described by: $ARGUMENTS

Use the `review-checklist` skill. Delegate to the **code-review** subagent via
the Task tool. Scope to the files in $ARGUMENTS, or to `git diff main --stat`
if none given. Have the reviewer post a `REVIEW` entry to
`.kilo/board/BOARD.md` with a verdict (APPROVED / CHANGES_REQUESTED) and
per-issue severity + file:line. If CHANGES_REQUESTED with blocking issues, open
a `BUG` for the **debugger** and re-verify with the **tester** before
committing. Report the verdict and any blocking issues concisely, referencing
the board entry.
