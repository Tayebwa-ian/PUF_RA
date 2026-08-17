---
description: Implement a feature or write new code in the PUF_RA repo via the coder subagent. Pass a description (e.g. /code "add per-topic BM25 weight learning with tests").
agent: orchestrator
---
Implement the following via the coder subagent: $ARGUMENTS

Use the `implement` skill. Delegate to the **coder** subagent via the Task tool.
Scope: the feature described in $ARGUMENTS. The coder reads the relevant design
note on `.kilo/board/BOARD.md` (open a `TASK-NNN` first if none exists),
implements scoped changes in `src/`/`cli/`, ships tests, and self-verifies.

After the coder reports, run the standard pipeline: **tester** to verify green,
**debugger** only if red, **code-review** for the `REVIEW`, then **git-manager**
to commit (push only if asked). Report files added/changed and test status,
referencing board IDs. If an `ARCH` recommendation from the architect applies,
prefer delegating that design rather than improvising.
