---
name: condense
description: Summarize and reduce the agent message board by archiving resolved entries and condensing open ones, plus writing a lean STATE.md. Use when the board is long or before context-heavy sessions. (Formerly 'compact'.)
---

# Condense the Message Board

The shared board (`.kilo/board/BOARD.md`) accumulates `TASK-`/`MSG-` entries
and grows verbose over time. Every agent/subagent must read it, so a long board
wastes context. This skill compresses it without losing information.

## What it does

- **Archives resolved entries** (`Status: DONE` or `WONT_FIX`): removes them
  from the inline board and appends a one-line summary to the archive file
  (default `.kilo/board/BOARD.archive.md`). Format:
  `- [ID] TITLE — STATUS — <first body line>`.
- **Condenses open entries** (everything else: `OPEN`, `IN_PROGRESS`,
  `BLOCKED`): keeps the `## [ID]` header and `Status`, but trims long `Body`
  blocks down to the first few lines.
- **Preserves the header and the `<!-- New entries go above this line. -->`
  marker plus everything after it** (the live log tail) intact.
- **Writes `STATE.md`** (repo root) with counts (total / open / resolved) and a
  bullet list of open items with their IDs, titles, and statuses.
- It is **pure and idempotent**: resolved entries move to the archive text
  (never deleted); running it twice yields the same open set.

## When to run

- When `.kilo/board/BOARD.md` is long (many resolved entries) or before any
  context-heavy session / agent handoff to shrink what must be read.
- Periodically as part of housekeeping once a batch of work is `DONE`.

## Usage

Preview only (prints new board, archive, and state; writes nothing):

    python -m scripts.condense

Apply (rewrites the board in place; writes archive + state files):

    python -m scripts.condense --apply

Options:

    --board PATH    board markdown (default .kilo/board/BOARD.md)
    --archive PATH  archive markdown (default .kilo/board/BOARD.archive.md)
    --state PATH    state markdown (default STATE.md)
    --apply         actually write files (omit for a safe preview)

The same logic is also reachable via the `puf` entry point:

    puf condense            # preview
    puf condense --apply    # apply

## Notes

- Never deletes information: resolved entries are preserved in the archive.
- Entries after the marker line are kept verbatim (the live tail), even if
  resolved, so the most recent activity is always visible.
- After `--apply`, archived entries no longer appear inline; consult
  `BOARD.archive.md` for their summaries.
