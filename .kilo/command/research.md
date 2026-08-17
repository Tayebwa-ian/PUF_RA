---
description: Run a SOTA research pass via the researcher subagent. Pass a topic (e.g. /research "hybrid relevance ranking for systematic literature reviews").
agent: orchestrator
---
Run a research pass on: $ARGUMENTS

Use the `research` skill. Delegate to the **researcher** subagent via the Task
tool. Scope: the topic in $ARGUMENTS (or a general pipeline-improvement survey
if empty). The researcher surveys credible sources, compares to the current
code, and posts `RESEARCH` entries to `.kilo/board/BOARD.md` with
`To: architect`.

After findings land, dispatch the **architect** to turn high-value findings into
`ARCH` recommendations, then (if the user wants) schedule the top
recommendation as a `TASK-` for the **coder**. Report the key findings and any
resulting recommendations, referencing board IDs.
