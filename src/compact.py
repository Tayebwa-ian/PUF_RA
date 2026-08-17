"""Compact the agent message board.

The board (`.kilo/board/BOARD.md`) grows verbose over time. ``compact_board``
reduces context for every agent/subagent by:

* archiving **resolved** entries (``Status`` in {DONE, WONT_FIX}) into a
  separate archive section instead of keeping them inline, and
* condensing **open** entries (everything else) by trimming long bodies while
  preserving the ``## [ID]`` header and ``Status`` field.

It is pure and idempotent: no information is discarded (resolved entries move
to the archive text, never deleted), and running it twice yields the same open
set. The header lines and the ``<!-- New entries go above this line. -->``
marker (with anything after it) are preserved intact.
"""

from __future__ import annotations

import re
from typing import Optional

_ENTRY_HEADER_RE = re.compile(r"^##\s+\[([A-Za-z0-9][A-Za-z0-9_-]*)\]\s*(.*?)\s*$")
_FIELD_RE = re.compile(r"^-\s+([A-Za-z][A-Za-z0-9_ ]*):\s*(.*)$")
_STATUS_RE = re.compile(r"^-\s+status\s*:\s*(.+)$", re.IGNORECASE)
_RESOLVED = {"done", "wont_fix", "wontfix", "closed", "resolved"}
_MARKER = "<!-- New entries go above this line. -->"
_MAX_BODY_LINES = 3


def _is_entry_header(line: str) -> bool:
    return bool(re.match(r"^##\s+\[", line))


def _parse_status(block_lines: list[str]) -> str:
    for line in block_lines:
        m = _STATUS_RE.match(line)
        if m:
            return m.group(1).strip()
    return ""


def _classify(status: str) -> str:
    return "resolved" if status.strip().lower() in _RESOLVED else "open"


def _first_body_line(block_lines: list[str]) -> str:
    in_body = False
    for line in block_lines:
        fm = _FIELD_RE.match(line)
        if fm and line.lower().startswith("- body"):
            in_body = True
            rest = fm.group(2).strip()
            if rest and rest != "|":
                return rest
            continue
        if in_body:
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def _split_blocks(lines: list[str]) -> list[tuple[bool, list[str]]]:
    tokens: list[tuple[bool, list[str]]] = []
    current: list[str] = []
    current_is_block = False
    for line in lines:
        if _is_entry_header(line):
            if current:
                tokens.append((current_is_block, current))
            current = [line]
            current_is_block = True
        else:
            current.append(line)
    if current:
        tokens.append((current_is_block, current))
    return tokens


def _trim_block(block_lines: list[str], max_body: int = _MAX_BODY_LINES) -> list[str]:
    out: list[str] = []
    in_body = False
    body_count = 0
    trimmed = 0
    for line in block_lines:
        is_field = bool(_FIELD_RE.match(line))
        if is_field:
            out.append(line)
            in_body = line.lower().startswith("- body")
            continue
        if in_body:
            if body_count < max_body:
                out.append(line)
                body_count += 1
            else:
                trimmed += 1
            continue
        out.append(line)
    if trimmed:
        out.append("  ... (trimmed %d body line(s))" % trimmed)
    return out


def compact_board(
    markdown_text: str, archive_path: Optional[str] = None
) -> tuple[str, str, str]:
    lines = markdown_text.split("\n")

    first_entry_idx = next(
        (i for i, ln in enumerate(lines) if _is_entry_header(ln)), len(lines)
    )
    marker_idx = next((i for i, ln in enumerate(lines) if _MARKER in ln), None)
    if marker_idx is None:
        marker_idx = len(lines)
    if marker_idx < first_entry_idx:
        marker_idx = first_entry_idx

    header_lines = lines[:first_entry_idx]
    entries_lines = lines[first_entry_idx:marker_idx]
    after_lines = lines[marker_idx:]

    archived: list[str] = []
    new_entries: list[str] = []
    for is_block, token in _split_blocks(entries_lines):
        if not is_block:
            new_entries.extend(token)
            continue
        status = _parse_status(token)
        if _classify(status) == "resolved":
            hm = _ENTRY_HEADER_RE.match(token[0])
            eid = hm.group(1) if hm else "?"
            title = hm.group(2) if hm else ""
            summary = _first_body_line(token)
            archived.append("- [%s] %s — %s — %s" % (eid, title, status.upper(), summary))
        else:
            new_entries.extend(_trim_block(token))

    new_board_lines = header_lines + new_entries + after_lines
    new_board_text = "\n".join(new_board_lines)

    archive_text = _build_archive(archived, archive_path)
    state_text = _build_state(lines, archived, archive_path)

    return new_board_text, archive_text, state_text


def _build_archive(archived: list[str], archive_path: Optional[str]) -> str:
    if not archived:
        return "# Board Archive\n\nNo resolved entries to archive.\n"
    target = archive_path or ".kilo/board/BOARD.archive.md"
    lines = [
        "# Board Archive",
        "",
        "Resolved entries archived from the agent message board "
        "(`%s`) by `puf compact`." % target,
        "",
    ]
    lines.extend(archived)
    lines.append("")
    return "\n".join(lines)


def _build_state(
    all_lines: list[str], archived: list[str], archive_path: Optional[str]
) -> str:
    blocks: list[tuple[str, str, str]] = []
    for is_block, token in _split_blocks(all_lines):
        if not is_block:
            continue
        hm = _ENTRY_HEADER_RE.match(token[0])
        if not hm:
            continue
        eid = hm.group(1)
        title = hm.group(2)
        status = _parse_status(token)
        blocks.append((eid, title, status))

    total = len(blocks)
    open_items = [(e, t, s) for (e, t, s) in blocks if _classify(s) == "open"]
    resolved_count = total - len(open_items)
    target = archive_path or ".kilo/board/BOARD.archive.md"

    lines = [
        "# Board State",
        "",
        "_Generated by `puf compact` from the agent message board._",
        "",
        "- Total entries: %d" % total,
        "- Open: %d" % len(open_items),
        "- Resolved: %d" % resolved_count,
        "- Archived this run: %d (see `%s`)" % (len(archived), target),
        "",
        "## Open items",
    ]
    if open_items:
        for eid, title, status in open_items:
            lines.append("- [%s] %s — %s" % (eid, title, status.upper()))
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)
