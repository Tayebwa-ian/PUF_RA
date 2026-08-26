"""CLI to condense the agent message board.

Usage:
    python -m scripts.condense [--board PATH] [--archive PATH] [--state PATH] [--apply]

Without ``--apply`` it prints a preview (new board, archive, state) and does
not modify any files. With ``--apply`` it rewrites the board in place and
writes the archive + state files.

The archive file MERGES across runs (never overwriting prior archives): any
existing archive content is preserved and new entries are appended, deduped by
their ``[ID]`` token so repeated runs do not create duplicate archive lines.
STATE.md is a regenerated summary and may be overwritten.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.condense import condense_board

DEFAULT_BOARD = ".kilo/board/BOARD.md"
DEFAULT_ARCHIVE = ".kilo/board/BOARD.archive.md"
DEFAULT_STATE = "STATE.md"

_ARCHIVE_BULLET_RE = re.compile(r"^- \[([^\]]+)\]\s")


def _archive_bullets(text: str) -> list[str]:
    """Return the ``- [ID] ...`` archive lines found in ``text``."""
    out: list[str] = []
    for line in text.splitlines():
        if _ARCHIVE_BULLET_RE.match(line):
            out.append(line)
    return out


def _archive_header(text: str) -> list[str]:
    """Return the lines of ``text`` that precede the first archive bullet."""
    header: list[str] = []
    for line in text.splitlines():
        if _ARCHIVE_BULLET_RE.match(line):
            break
        header.append(line)
    return header


def _unique_eid(eid: str, seen: set[str]) -> str:
    """Return ``eid`` unless it collides with ``seen``; then ``eid#2``, ``eid#3``…"""
    if eid not in seen:
        return eid
    counter = 2
    while "%s#%d" % (eid, counter) in seen:
        counter += 1
    return "%s#%d" % (eid, counter)


def _replace_bullet_eid(line: str, eid: str) -> str:
    """Rewrite the ``[ID]`` token at the start of an archive bullet line."""
    return _ARCHIVE_BULLET_RE.sub("- [%s] " % eid, line, count=1)


def merge_archive(existing: str, new: str) -> str:
    """Merge a prior archive (``existing``) with the freshly built one (``new``).

    Existing entries are preserved verbatim and listed first. New entries are
    appended, with two guarantees:

    * An incoming ``[ID]`` that is identical (same text) to an already-archived
      line is treated as a re-archived duplicate and skipped — so repeated
      ``--apply`` runs never create duplicate archive lines.
    * An incoming ``[ID]`` that is *distinct* (different body) yet collides with
      an already-archived ``[ID]`` is never silently dropped. It is kept with a
      stable disambiguating suffix (``ID#2``, ``ID#3`` …) so both records survive
      distinctly and the previously archived entry is left untouched.
    """
    existing_bullets = _archive_bullets(existing)
    new_bullets = _archive_bullets(new)

    seen_eids: set[str] = set()
    seen_lines: set[str] = set()
    merged: list[str] = []

    for line in existing_bullets:
        m = _ARCHIVE_BULLET_RE.match(line)
        eid = m.group(1) if m else None
        if eid is not None:
            seen_eids.add(eid)
        seen_lines.add(line)
        merged.append(line)

    for line in new_bullets:
        m = _ARCHIVE_BULLET_RE.match(line)
        eid = m.group(1) if m else None
        if eid is not None:
            if line in seen_lines:
                continue
            unique = _unique_eid(eid, seen_eids)
            if unique != eid:
                line = _replace_bullet_eid(line, unique)
            seen_eids.add(eid)
            seen_eids.add(unique)
        seen_lines.add(line)
        merged.append(line)

    header = _archive_header(new)
    return "\n".join(header + merged) + "\n"


def _write_archive(archive_path: str, archive_text: str) -> None:
    path = Path(archive_path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    merged = merge_archive(existing, archive_text)
    path.write_text(merged, encoding="utf-8")


def condense_command(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf condense",
        description="Condense the agent message board (archive resolved, condense open).",
    )
    parser.add_argument("--board", default=DEFAULT_BOARD, help="Board markdown path")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE, help="Archive markdown path")
    parser.add_argument("--state", default=DEFAULT_STATE, help="State markdown path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite board in place and write archive + state files.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    board_path = Path(args.board)
    if not board_path.exists():
        print("Board not found: %s" % board_path, file=sys.stderr)
        return 1

    text = board_path.read_text(encoding="utf-8")
    new_board, archive_text, state_text = condense_board(
        text, archive_path=args.archive
    )

    if not args.apply:
        print("===== NEW BOARD (preview) =====")
        print(new_board)
        print("===== ARCHIVE (preview) =====")
        print(archive_text)
        print("===== STATE (preview) =====")
        print(state_text)
        print("\n(preview only — no files written; use --apply to write)")
        return 0

    board_path.write_text(new_board, encoding="utf-8")
    _write_archive(args.archive, archive_text)
    Path(args.state).write_text(state_text, encoding="utf-8")
    print(
        "Condensed. Board: %s (resolved entries archived to %s); state: %s"
        % (board_path, args.archive, args.state)
    )
    return 0


if __name__ == "__main__":
    sys.exit(condense_command())
