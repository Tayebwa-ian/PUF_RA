#!/usr/bin/env python3
"""DEPRECATED: Use `puf import bibtex` from the CLI instead.

This script is retained for backward compatibility only.
It will be removed in a future release.
See cli/import.py for the replacement.
"""

import warnings
warnings.warn(
    "run_bibtex_to_csv.py is deprecated. Use 'puf import bibtex' from the CLI.",
    DeprecationWarning,
    stacklevel=2,
)

# ... rest of original code ...
"""
bibtex_to_csv.py — Parse BibTeX files and export entries to a CSV.

Supports @article and @inproceedings (and any other entry type).
Fields missing from a given entry are left blank in the output.

Usage:
    python bibtex_to_csv.py input.bib                  # writes input.csv
    python bibtex_to_csv.py input.bib -o output.csv    # custom output path
    python bibtex_to_csv.py *.bib -o combined.csv      # merge multiple files
"""

import argparse
import csv
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

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
    """Remove one layer of wrapping braces if present, e.g. {{Title}} → {Title}."""
    text = text.strip()
    while text.startswith("{") and text.endswith("}"):
        # Make sure it's a matching outer pair, not {a} and {b}
        depth = 0
        for i, ch in enumerate(text):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0:
                if i == len(text) - 1:
                    text = text[1:-1].strip()
                    break
                else:
                    break  # outer braces don't span the whole string
        else:
            break
    return text


def parse_bibtex(text: str) -> list[dict]:
    """
    Parse a BibTeX string and return a list of entry dicts.

    Each dict contains:
      - 'entry_type'  : e.g. 'article', 'inproceedings'
      - 'cite_key'    : the citation key
      - all other fields as key→value pairs (braces stripped, whitespace normalised)
    """
    entries: list[dict] = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        m = ENTRY_OPEN_RE.match(line.strip())

        if not m:
            i += 1
            continue

        entry: dict = {
            "entry_type": m.group("type").lower(),
            "cite_key": m.group("key"),
        }
        i += 1

        # Collect lines until the closing brace of the entry.
        # We track brace depth to handle nested braces in values.
        depth = 1  # one opening brace consumed by the @type{ line
        field_lines: list[str] = []

        while i < len(lines) and depth > 0:
            raw = lines[i]
            # Count braces (skip escaped ones — rare in BibTeX but possible)
            depth += raw.count("{") - raw.count("}")
            if depth > 0:
                field_lines.append(raw)
            i += 1

        # Parse collected field lines.
        # Multi-line values (e.g. long abstracts) are joined before matching.
        joined = " ".join(field_lines)
        # Split on the pattern  },\n  field = { or  },  field = {
        # so multi-line fields are still isolated.
        # Strategy: split by top-level commas only (outside braces).
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
            # Normalise internal whitespace (collapse newlines / extra spaces)
            value = re.sub(r"\s+", " ", value).strip()
            entry[name] = value

        entries.append(entry)

    return entries


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


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

# These columns are always written first (if present), in this order.
PRIORITY_COLUMNS = ["entry_type", "cite_key", "author", "title", "year", "journal",
                    "booktitle", "volume", "number", "pages", "publisher",
                    "doi", "url", "abstract"]


def entries_to_csv(entries: list[dict], output_path: Path) -> None:
    """Write *entries* to a CSV at *output_path*."""
    if not entries:
        print("No entries found — nothing to write.", file=sys.stderr)
        return

    # Collect all field names across all entries
    all_keys: set[str] = set()
    for e in entries:
        all_keys.update(e.keys())

    # Build column order: priority cols first, then everything else alphabetically
    ordered = [c for c in PRIORITY_COLUMNS if c in all_keys]
    extras = sorted(all_keys - set(ordered))
    columns = ordered + extras

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({col: entry.get(col, "") for col in columns})

    print(f"Wrote {len(entries)} entries → {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert one or more BibTeX files to a single CSV.",
    )
    parser.add_argument(
        "bibtex_files",
        nargs="+",
        metavar="FILE.bib",
        help="Input BibTeX file(s).",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="OUTPUT.csv",
        help="Output CSV path. Defaults to the name of the first input file.",
    )
    args = parser.parse_args()

    all_entries: list[dict] = []
    for bib_path_str in args.bibtex_files:
        bib_path = Path(bib_path_str)
        if not bib_path.exists():
            print(f"Warning: {bib_path} not found — skipping.", file=sys.stderr)
            continue
        text = bib_path.read_text(encoding="utf-8", errors="replace")
        entries = parse_bibtex(text)
        print(f"  {bib_path}: {len(entries)} entries parsed")
        all_entries.extend(entries)

    if not all_entries:
        print("No entries parsed from any input file.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(args.bibtex_files[0]).with_suffix(".csv")

    entries_to_csv(all_entries, out_path)


if __name__ == "__main__":
    main()