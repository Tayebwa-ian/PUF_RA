---
description: Compact (condense) the agent message board — archive resolved entries, condense open items, and write STATE.md. Pass `--apply` to write, or omit for a preview. Formerly `/compact`.
agent: orchestrator
---
Condense the agent message board for: $ARGUMENTS

Use the `condense` skill. This is the user-facing way to run the condense
feature, backed by `scripts/condense.py` / `puf condense`.

Run:

    /condense [--apply]

Behavior:
- **Without `--apply`** it previews: prints the new board, the archive, and the
  `STATE.md` content, but writes nothing.
- **With `--apply`** it rewrites the board in place, appends resolved entries to
  the archive (`.kilo/board/BOARD.archive.md`, merged/deduped across runs so no
  data is lost), and writes `STATE.md` at the repo root.

What it does (no data loss):
1. Archives resolved entries (`Status: DONE` / `WONT_FIX`) into a one-line
   summary in the archive file.
2. Condenses open entries (trims long bodies; keeps the `## [ID]` header and
   `Status`).
3. Preserves the header and the `<!-- New entries go above this line. -->` marker
   plus everything after it (the live tail) intact.
4. Writes `STATE.md` with counts and the open-item list.

Note: this command was formerly called `compact`; the underlying module/CLI are
now `condense` (`puf condense`).
