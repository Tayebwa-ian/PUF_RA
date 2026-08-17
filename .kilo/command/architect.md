---
description: Request a design evaluation and improvement recommendations via the architect subagent. Pass a focus area (e.g. /architect "relevance engine"), or omit to review the whole pipeline.
agent: orchestrator
---
Review the design for: $ARGUMENTS

Use the `architecture-review` skill. Delegate to the **architect** subagent via
the Task tool. Scope: the focus area in $ARGUMENTS, or a full-pipeline review if
empty. The architect reads `.kilo/board/BOARD.md` (including `RESEARCH`
findings from the researcher), evaluates the design, and posts `ARCH`
recommendations with `To: orchestrator`.

Triage the `ARCH` entries: approve and schedule the top `delegatable: yes`
recommendations as `TASK-` for the **coder** (or **debugger**), defer the rest,
and flag any needing user sign-off. Report the recommendations and what you
scheduled, referencing board IDs.
