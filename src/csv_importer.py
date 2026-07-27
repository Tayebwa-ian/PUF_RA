"""CSV importer — import papers from CSV files into SQLite.

Refactored from the original run_csv_to_db.py with the following changes:
  - Uses the new v2 schema (no query_id on papers; uses paper_queries)
  - Supports source registration and linking
  - Reuses normalisation helpers from bibtex_importer

Usage:
    from src.csv_importer import import_csv

    import_csv(
        csv_path="papers.csv",
        fmt="acm",
        db_path="puf.db",
        query_ids=[1, 2],
        source_names=["ACM"],
    )
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Any

from src.db import get_connection
from src.db_schema import create_schema
from src.bibtex_importer import _normalize_authors, _normalize_keywords


# ---------------------------------------------------------------------------
# Field normalisation helpers
# ---------------------------------------------------------------------------

NORMALIZED_SEP = "; "


def require(value: Any, field_name: str, row_num: int, optional: bool = False):
    """Return a stripped non-empty string, or raise ValueError.

    If `optional` is True, an empty/missing value returns None (SQL NULL)
    instead of raising.
    """
    if value is None or str(value).strip() == "":
        if optional:
            return None
        raise ValueError(
            f"Empty value for field '{field_name}' in row {row_num}."
        )
    return str(value).strip()


def to_year(value: Any, field_name: str, row_num: int) -> int:
    s = require(value, field_name, row_num)
    try:
        return int(s)
    except ValueError as exc:
        raise ValueError(
            f"Field '{field_name}' in row {row_num} is not an integer: {s!r}"
        ) from exc


def normalize_multi(
    value: Any,
    field_name: str,
    row_num: int,
    sep: str,
    item_transform=None,
    optional: bool = False,
):
    """Split `value` on `sep`, strip each part, drop empties, optionally
    transform each part, and rejoin with NORMALIZED_SEP."""
    if optional and (value is None or str(value).strip() == ""):
        return None
    raw = require(value, field_name, row_num)
    parts = [p.strip() for p in raw.split(sep) if p.strip()]
    if not parts:
        if optional:
            return None
        raise ValueError(
            f"Field '{field_name}' in row {row_num} has no values after splitting."
        )
    if item_transform is not None:
        parts = [item_transform(p) for p in parts]
    return NORMALIZED_SEP.join(parts)


# ---------------------------------------------------------------------------
# Format-specific mappers
# ---------------------------------------------------------------------------

def map_acm_row(row: dict[str, str], row_num: int) -> dict[str, Any]:
    """Map an ACM CSV DictRow to a normalised paper dict."""
    entry_type = require(row.get("entry_type"), "entry_type", row_num).lower()

    if entry_type == "article":
        publication_title = require(row.get("journal"), "journal", row_num)
    elif entry_type == "inproceedings":
        publication_title = require(row.get("booktitle"), "booktitle", row_num)
    else:
        raise ValueError(
            f"Unsupported entry_type {entry_type!r} in row {row_num}. "
            f"Expected 'article' or 'inproceedings'."
        )

    return {
        "title": require(row.get("title"), "title", row_num),
        "authors": normalize_multi(row.get("author"), "author", row_num, " and ", _normalize_authors),
        "year": to_year(row.get("year"), "year", row_num),
        "abstract": require(row.get("abstract"), "abstract", row_num),
        "doi": require(row.get("doi"), "doi", row_num, optional=True),
        "publication_title": publication_title,
        "keywords": normalize_multi(row.get("keywords"), "keywords", row_num, ",", optional=True),
    }


def map_ieee_row(row: dict[str, str], row_num: int) -> dict[str, Any]:
    """Map an IEEE CSV DictRow to a normalised paper dict."""
    return {
        "title": require(row.get("Document Title"), "Document Title", row_num),
        "authors": normalize_multi(row.get("Authors"), "Authors", row_num, ";", _normalize_authors),
        "year": to_year(row.get("Publication Year"), "Publication Year", row_num),
        "abstract": require(row.get("Abstract"), "Abstract", row_num),
        "doi": require(row.get("DOI"), "DOI", row_num, optional=True),
        "publication_title": require(row.get("Publication Title"), "Publication Title", row_num),
        "keywords": normalize_multi(row.get("Author Keywords"), "Author Keywords", row_num, ";", optional=True),
    }


PARSERS = {
    "acm": map_acm_row,
    "ieee": map_ieee_row,
}

INSERT_SQL = """
INSERT INTO papers (title, authors, year, abstract, doi, publication_title, keywords)
VALUES (:title, :authors, :year, :abstract, :doi, :publication_title, :keywords)
"""


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def import_csv(
    csv_path: Path,
    db_path: Path,
    fmt: str,
    query_ids: list[int],
    source_names: list[str],
) -> tuple[int, int]:
    """Import a CSV of papers into the SQLite database.

    Args:
        csv_path: Path to the CSV file.
        db_path: Path to the SQLite database.
        fmt: CSV layout ('acm' or 'ieee').
        query_ids: Query IDs to associate imported papers with.
        source_names: Source names to associate imported papers with.

    Returns:
        Tuple of (imported_count, skipped_count).
    """
    mapper = PARSERS[fmt]

    papers: list[dict[str, Any]] = []
    skipped = 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for n, row in enumerate(reader, start=2):
            try:
                papers.append(mapper(row, n))
            except ValueError as exc:
                print(f"Skipping row {n}: {exc}", file=sys.stderr)
                skipped += 1

    if not papers:
        return 0, skipped

    with get_connection(db_path) as conn:
        create_schema(conn)
        source_ids = [_ensure_source(conn, name) for name in source_names]

        for paper in papers:
            # Upsert paper by DOI
            paper_id = _upsert_paper(conn, paper)
            for qid in query_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_queries (paper_id, query_id) VALUES (?, ?)",
                    (paper_id, qid),
                )
            for sid in source_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
                    (paper_id, sid),
                )

    return len(papers), skipped


# Duplicated from bibtex_importer to avoid circular imports.
# In a larger refactor these would be in a shared utils module.
from src.bibtex_importer import _ensure_source, _ensure_query, _upsert_paper  # noqa: E402


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a CSV of papers into the SQLite database."
    )
    parser.add_argument("csv", type=Path, help="Path to the CSV file.")
    parser.add_argument(
        "format",
        choices=sorted(PARSERS),
        help="CSV layout to parse.",
    )
    parser.add_argument("db", type=Path, help="Path to the SQLite database.")
    parser.add_argument(
        "query_ids",
        type=int,
        nargs="+",
        help="Query IDs to associate imported papers with.",
    )
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        default=[],
        help="Source name (repeatable). Default: 'unknown'.",
    )
    args = parser.parse_args()

    if not args.sources:
        args.sources = ["unknown"]

    try:
        imported, skipped = import_csv(
            args.csv, args.db, args.format, args.query_ids, args.sources
        )
    except sqlite3.Error as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        sys.exit(1)

    summary = f"Imported {imported} paper(s) from {args.csv}"
    if skipped:
        summary += f" ({skipped} row(s) skipped)"
    print(summary + ".", file=sys.stderr)


if __name__ == "__main__":
    main()
