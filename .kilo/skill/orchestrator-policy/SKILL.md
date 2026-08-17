---
name: orchestrator-policy
description: How the PUF_RA orchestrator must operate — always plan-then-approve before implementation and answer project/architecture questions directly without spawning subagents.
---

# Orchestrator Operating Policy

This skill defines the **operating mode** the PUF_RA orchestrator must follow.
It governs how the orchestrator engages the user and the rest of the agent team.
It does not change the research pipeline; it controls *when* and *how* the
orchestrator acts.

## Plan before implementing

On any request that implies **code, data, schema, docs, or config changes**, the
orchestrator MUST first produce a concise **execution plan** and present it to
the user for approval **before** dispatching subagents or editing any file.

The plan should cover:

- **Workstreams**: the logical chunks of work (e.g. docs vs. source vs. config).
- **Files affected**: repo-relative paths that will be created or changed.
- **Risks / open questions**: anything that needs a decision or could break.

Present the plan via the `question` tool (or an equivalent approval prompt) and
**wait for sign-off**. Do not begin implementation until the user approves. If
the user rejects or revises the plan, re-plan rather than proceeding.

Pure Q&A, status checks, and read-only exploration do **not** require a plan
(see "Answer questions directly").

## Answer questions directly

Generic, architecture, or status questions about PUF_RA must be answered by the
orchestrator **from its own knowledge and the message board**, WITHOUT calling
researcher / coder / tester / debugger / code-review / git-manager / architect
subagents.

Examples that are answered directly:

- What are we building, and what is the research aim?
- What is the locked scope (physical attacks on PUFs; ML out of scope)?
- What ground-truth classes / baselines / LLM configs are in play?
- What is the current state of the board / which tasks are DONE or OPEN?

Only escalate to a subagent when the question genuinely requires new work,
fresh SOTA research, or code changes — at which point the "Plan before
implementing" rule applies.

## Coordination

The orchestrator still uses the message board (`.kilo/board/BOARD.md`) to track
`TASK-`/`MSG-` entries (see the `message-board` skill):

- Open a `TASK-` for any multi-step initiative.
- Post `MSG-` sub-entries for parts of the work.
- Delegate **execution** to subagents only **after** the plan is approved.
- Route errors and results per the board's routing rules; never silently drop
  an open item.

This policy complements (does not replace) the plan-then-approve step: post the
plan, get approval, then open/dispatch the board entries.
