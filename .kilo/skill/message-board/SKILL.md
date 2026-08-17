---
name: message-board
description: Protocol for the shared agent message board used by the PUF_RA orchestrator and its subagents to coordinate and share errors, test results, and review notes.
---

# Message Board Protocol

The PUF_RA agentic team coordinates through a single file-based message board
at `.kilo/board/BOARD.md`. This skill defines how to read, post, and route
messages so that information (especially errors) flows automatically between
the orchestrator and its subagents.

## When to use

- Before starting any multi-step work, the orchestrator opens a `TASK-` entry.
- Any subagent that discovers a problem or produces a result writes a `MSG-`
  entry and updates the parent `TASK-`.
- Errors observed by one agent MUST be posted so the responsible agent can pick
  them up without being re-told by the orchestrator.

## How to read the board

1. Open `.kilo/board/BOARD.md`.
2. Scan entries from the bottom up for items where `To` includes you or `ALL`
   and `Status` is `OPEN` or `IN_PROGRESS`.
3. Never delete entries. Update in place: change `Status`, append to `Body`,
   set `Updated` and `Result`.

## How to post

Append a new block above the `<!-- New entries go above this line. -->`
marker. Use the next sequential ID (check the highest existing `MSG-`/`TASK-`
number). Minimal template:

```markdown
## [MSG-NNN] short-title
- Type: TEST | BUG | REVIEW | GIT | CODER | RESEARCH | ARCH | COORD | INFO
- From: <your-agent-name>
- To: <target-agent> | ALL
- Status: OPEN | IN_PROGRESS | BLOCKED | DONE | WONT_FIX
- Priority: high | normal | low
- Created: YYYY-MM-DD
- Updated: YYYY-MM-DD
- Body: |
  What happened, with the minimal relevant snippet and repo-relative file paths.
- Result: one-line outcome (when DONE)
```

## Routing (automatic forwarding)

The orchestrator enforces this pipeline. If you are the orchestrator, apply it:

1. A `TEST` entry that reports failures -> open a `BUG` entry assigned to
   `debugger` and link the failing output. (test errors -> debug)
2. A `BUG` fix -> reopen/verify with the `tester` (green required).
3. Green `TEST` on changed code -> open a `REVIEW` entry for `code-review`.
4. Approved `REVIEW` -> open a `GIT` entry for `git-manager`.
5. `RESEARCH` findings -> open an `ARCH` entry for `architect` (it consumes
   the findings and produces design recommendations).
6. `ARCH` recommendation marked `delegatable: yes` -> open a `TASK-` for
   `coder` (new code) or `debugger` (fix); `ARCH` needing sign-off -> ask user.
7. `BLOCKED` items stay with the orchestrator, which decides next action.

## Rules of thumb

- Post the *minimal* error snippet, never a full log.
- Always reference files with repo-relative paths (e.g. `src/db.py:120`).
- If you cannot act, set `Status: BLOCKED` and say why; do not silently drop it.
- One issue per message; split multi-problem reports into separate entries.
