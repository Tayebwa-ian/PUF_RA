---
name: architecture-review
description: How the architect subagent evaluates the PUF_RA pipeline design against standards and research, and posts delegatable ARCH recommendations to the message board.
---

# Architecture Review (Architect)

The `architect` subagent evaluates the *design* of the PUF_RA pipeline and
posts concrete, delegatable improvements. It is read-only and consumes
`RESEARCH` findings. Use this skill when the orchestrator opens an `ARCH`
request (or as a continuous review pass).

## What to evaluate

- **Data model**: `database_struct.sql` / `src/db_schema.py` — normalization,
  junction tables, constraints, migration path.
- **Module boundaries**: cohesion/coupling across `src/`; clear public APIs.
- **End-to-end flow**: import → dedup → relevance → screening → snowball;
  failure modes, idempotency, re-runnability.
- **Extensibility**: new sources, new models, new ranking methods.
- **Agent topology**: is the orchestrator a clean single router? Any
  concurrent-edit risk? Board protocol sound?
- **SOTA alignment**: compare to `RESEARCH` entries from the researcher.

## How to post

Post `ARCH` entries to `.kilo/board/BOARD.md` (see `message-board` skill):

```markdown
## [MSG-NNN] arch: learn per-topic relevance weights
- Type: ARCH
- From: architect
- To: orchestrator
- Status: DONE
- Priority: high
- Created: YYYY-MM-DD
- Updated: YYYY-MM-DD
- Body: |
  severity: med
  proposal: replace fixed category weights with per-query learned weights.
  affected: src/relevance.py, tests/test_relevance.py
  benefit: +relevance precision, adaptable to new topics
  tradeoff: more params; needs tuning data (use labeled screening decisions)
  delegatable: yes
  draft-task: "Implement per-query weight tuning in relevance.py with tests"
- Result: 1 recommendation; orchestrator to schedule.
```

## Rules

- Read-only: never edit. Advise; `coder`/`debugger` implement.
- Every recommendation is concrete and evidence-linked (cite `RESEARCH`/standard).
- Mark `delegatable: yes` with a draft `TASK-`, or flag items needing user
  sign-off.
- Few high-leverage changes beat a long wishlist. Separate "do now" from
  "backlog".
