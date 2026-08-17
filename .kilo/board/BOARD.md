# Agent Message Board

Shared coordination surface for the PUF_RA agentic coding team. The
**orchestrator** is the single router: it reads OPEN/IN_PROGRESS items and
dispatches them to the right specialist subagent via the Task tool. Subagents
post their results here so that *every* agent (and the user) has a single
source of truth for what is happening, what broke, and who owns it.

## Conventions

- Append new entries at the **bottom** of the file (chronological log).
- Every entry is a `## [ID] TITLE` block. IDs are zero-padded running numbers
  (MSG-001, MSG-002, ...). Use a `TASK-` prefix for top-level user requests
  and `MSG-` for sub-items.
- Required fields per entry: `Type`, `From`, `To`, `Status`, `Priority`,
  `Created`, `Updated`, `Body`.
- Statuses: `OPEN`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `WONT_FIX`.
- Types: `COORD` (orchestration), `TEST`, `BUG`, `REVIEW`, `GIT`, `CODER`
  (implementation), `RESEARCH` (SOTA findings), `ARCH` (design
  recommendation), `INFO`.
- Keep entries short. Link files with repo-relative paths. Paste only the
  relevant error snippet, never whole logs.
- When a subagent finishes, it sets `Status: DONE` and writes a one-line
  `Result:`. The orchestrator closes the parent `TASK-` when all children are
  `DONE` or `WONT_FIX`.

## Routing rules

| Type     | Owner subagent | Triggered by                                                  |
|----------|----------------|---------------------------------------------------------------|
| TEST     | tester         | baseline checks, verify a fix, regression after edit         |
| BUG      | debugger       | any failing test, exception, or lint error                    |
| REVIEW   | code-review    | changed files ready for review                                |
| GIT      | git-manager    | reviewed & green changes ready to commit                      |
| CODER    | coder          | feature implementation assigned by the orchestrator          |
| RESEARCH | researcher     | SOTA survey requested by the orchestrator                     |
| ARCH     | architect      | design evaluation; consumes RESEARCH, yields delegatable recs |

The orchestrator forwards automatically:
- `TEST` fails -> `BUG` (debugger); `BUG` fix re-verified by `tester`;
  green `TEST` -> `REVIEW` (code-review); approved `REVIEW` -> `GIT`
  (git-manager).
- `RESEARCH` findings -> `ARCH` (architect consumes them).
- `ARCH` recommendation (`delegatable: yes`) -> new `TASK-` for `coder`
  (new code) or `debugger` (fix).

---

<!-- New entries go above this line. -->


## [MSG-000] Board initialized
- Type: INFO
- From: orchestrator
- To: ALL
- Status: DONE
- Priority: normal
- Created: 2026-08-16T09:00:00+02:00
- Updated: 2026-08-16T09:00:00+02:00
- Body: Message board created. Agents post here; orchestrator routes.
- Result: Board ready.
