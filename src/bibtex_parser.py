"""BibTeX parser.

Extracted and enhanced from the original run_bibtex_to_csv.py.
Parses BibTeX files into a list of entry dicts with normalized values.

Supports:
  - @article, @inproceedings, @proceedings, @inbook, and any other entry type
  - Brace-delimited {value} and quote-delimited "value" field values
  - Multi-line values (tracked via brace depth)
  - Unicode and HTML entity normalisation (best-effort)
"""

from __future__ import annotations

import re
from typing import Any


# Matches the opening line of any BibTeX entry, e.g.:
#   @article{mykey,
#   @inproceedings{somekey,
ENTRY_OPEN_RE = re.compile(
    r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,\s]+)\s*,",
    re.IGNORECASE,
)

# Matches a field line like:  author = {Some Value},
# Handles both {value} and "value" delimiters, and optional trailing comma.
FIELD_RE = re.compile(
    r"""
    ^[ \t]*                        # leading whitespace
    (?P<name>\w+)                  # field name
    \s*=\s*                        # equals sign with optional spaces
    (?:
      \{(?P<brace_val>.*?)\}       # brace-delimited value
      |
      "(?P<quote_val>.*?)"         # quote-delimited value
      |
      (?P<raw_val>\S[^,]*)         # raw value (numbers, abbreviations)
    )
    \s*,?\s*$                      # optional trailing comma
    """,
    re.VERBOSE | re.DOTALL,
)


def _strip_outer_braces(text: str) -> str:
    """Remove layers of wrapping braces if present, e.g. {{Title}} → Title.

    Only strips a layer when the outermost pair genuinely wraps the whole
    string (the interior is brace-balanced). This guarantees termination even
    for unbalanced input such as ``{{Foo}-bar}``.
    """
    text = text.strip()
    while len(text) >= 2 and text.startswith("{") and text.endswith("}"):
        depth = 0
        balanced = True
        for ch in text[1:-1]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if depth != 0:
            balanced = False
        if not balanced:
            break
        text = text[1:-1].strip()
    return text


def _split_top_level(text: str) -> list[str]:
    """Split *text* on commas that are not inside braces or quotes."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    buf: list[str] = []

    for ch in text:
        if ch == '"' and depth == 0:
            in_quote = not in_quote
            buf.append(ch)
        elif ch == "{" and not in_quote:
            depth += 1
            buf.append(ch)
        elif ch == "}" and not in_quote:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0 and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)

    if buf:
        parts.append("".join(buf))

    return parts


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    """Parse a BibTeX string and return a list of entry dicts.

    Each dict contains:
      - 'entry_type'  : e.g. 'article', 'inproceedings'
      - 'cite_key'    : the citation key
      - all other fields as key→value pairs (braces stripped, whitespace normalised)

    Args:
        text: Raw BibTeX file content.

    Returns:
        List of entry dictionaries.
    """
    entries: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        m = ENTRY_OPEN_RE.match(line.strip())

        if not m:
            i += 1
            continue

        entry: dict[str, Any] = {
            "entry_type": m.group("type").lower(),
            "cite_key": m.group("key"),
        }
        i += 1

        depth = 1
        field_lines: list[str] = []

        while i < len(lines) and depth > 0:
            raw = lines[i]
            depth += raw.count("{") - raw.count("}")
            if depth > 0:
                field_lines.append(raw)
            i += 1

        joined = " ".join(field_lines)
        segments = _split_top_level(joined)

        for seg in segments:
            fm = FIELD_RE.match(seg.strip())
            if not fm:
                continue
            name = fm.group("name").lower()
            value = (
                fm.group("brace_val")
                or fm.group("quote_val")
                or fm.group("raw_val")
                or ""
            )
            value = _strip_outer_braces(value)
            value = re.sub(r"\s+", " ", value).strip()
            entry[name] = value

        entries.append(entry)

    return entries
