"""Ingest real BibTeX citation exports into the SQLite study database.

This script walks the citation-data directory (``cititations_data`` by
default), parses every ``*.bib`` file per query sub-directory (``query1``,
``query2``, ...), and ingests each paper.

Deduplication happens on **DOI** and **normalized title**: a paper that
appears in more than one query sub-directory or platform is stored exactly
once. Provenance is recorded with method-level granularity:

* ``sources`` gets one row per discovery method named ``"{query_label}:{platform}"``
  (e.g. ``query1:ACM``, ``query2:IEEE``).
* ``paper_sources`` links each paper to every method that discovered it.
* ``queries`` / ``paper_queries`` keep the platform + query-text traceability.

Re-running is idempotent: no duplicate papers or provenance links are
created.

The module is import-safe (no side effects on import); use ``main`` or the
individual functions.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Optional

from src.bibtex_importer import (
    _ensure_query,
    _ensure_source,
    _link_paper_to_query,
    _link_paper_to_source,
    _normalize_authors,
    _publication_title,
    _to_int,
    _upsert_paper,
)
from src.bibtex_parser import parse_bibtex
from src.db import get_connection
from src.db_schema import create_schema


# Map the short platform token (derived from the file name) to the human
# platform label used in the `queries` table.
QUERY_PLATFORM_LABELS = {
    "ACM": "ACM Digital Library",
    "IEEE": "IEEE Xplore",
}


def normalize_title(title: str) -> str:
    """Collapse internal whitespace, strip, and lowercase a title."""
    if not title:
        return ""
    return " ".join(title.split()).strip().lower()


def find_existing_paper(
    conn: Any, doi: Optional[str], norm_title: str
) -> Optional[int]:
    """Return the id of an existing paper matching the given identity.

    Precedence:
      * If ``doi`` is truthy, match ``papers.doi = ?``.
      * Else if ``norm_title`` is truthy, match ``lower(papers.title) = ?``,
        but ONLY when at least one side lacks a DOI. Two entries that both
        carry *different* non-null DOIs are never merged, even if the titles
        are identical.

    Returns the paper id or ``None``.
    """
    # Prefer a DOI match. The v2 papers.doi column may be NULL, so two entries
    # that both carry *different* non-null DOIs are never merged on title.
    if doi:
        row = conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()
        if row:
            return row[0]

    if norm_title:
        if doi:
            # Incoming entry has a DOI that did not match above, so any existing
            # row carrying a *different* non-null DOI must NOT merge on title
            # alone. Only merge a title match whose row has no DOI (or,
            # defensively, the same DOI).
            row = conn.execute(
                "SELECT id FROM papers WHERE lower(title) = ? "
                "AND (doi IS NULL OR doi = ?)",
                (norm_title, doi),
            ).fetchone()
        else:
            # Incoming entry has no DOI: merge on title regardless of the
            # existing row's DOI, so a paper that appeared once WITH a DOI and
            # once WITHOUT one stays a single row (no double insert).
            row = conn.execute(
                "SELECT id FROM papers WHERE lower(title) = ?", (norm_title,)
            ).fetchone()
        if row:
            return row["id"]

    return None


def _ensure_source_with_desc(conn: Any, name: str, description: str) -> int:
    """Get-or-create a source row and set its description if missing."""
    source_id = _ensure_source(conn, name)
    conn.execute(
        "UPDATE sources SET description = ? WHERE id = ? AND "
        "(description IS NULL OR description = '')",
        (description, source_id),
    )
    return source_id


def _link_if_new(conn: Any, paper_id: int, source_id: int, query_id: int) -> bool:
    """Link the paper to its source/query provenance.

    Returns ``True`` if at least one *new* provenance link was created.
    """
    new_link = False

    existing = conn.execute(
        "SELECT 1 FROM paper_sources WHERE paper_id = ? AND source_id = ?",
        (paper_id, source_id),
    ).fetchone()
    if not existing:
        _link_paper_to_source(conn, paper_id, source_id)
        new_link = True

    existing_q = conn.execute(
        "SELECT 1 FROM paper_queries WHERE paper_id = ? AND query_id = ?",
        (paper_id, query_id),
    ).fetchone()
    if not existing_q:
        _link_paper_to_query(conn, paper_id, query_id)
        new_link = True

    return new_link


def _fill_missing_fields(conn: Any, paper_id: int, entry: dict[str, Any]) -> None:
    """Populate empty fields of an existing paper from ``entry``."""
    row = conn.execute(
        "SELECT title, authors, year, abstract FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if not row:
        return

    updates: dict[str, Any] = {}
    if not row["title"] and entry.get("title"):
        updates["title"] = entry["title"].strip()
    if not row["authors"] and entry.get("author"):
        updates["authors"] = _normalize_authors(entry["author"])
    if row["year"] is None and entry.get("year"):
        updates["year"] = _to_int(entry["year"])
    if not row["abstract"] and entry.get("abstract"):
        updates["abstract"] = entry["abstract"].strip()

    if not updates:
        return
    set_clause = ", ".join(f"{key} = :{key}" for key in updates)
    updates["id"] = paper_id
    conn.execute(
        f"UPDATE papers SET {set_clause}, updated_at = current_timestamp "
        f"WHERE id = :id",
        updates,
    )


def ingest_entries(
    conn: Any, entries: list[dict[str, Any]], query_label: str, platform: str
) -> dict[str, int]:
    """Ingest a list of parsed BibTeX entries for one query/platform.

    Args:
        conn: SQLite connection.
        entries: Parsed entries (from ``parse_bibtex``).
        query_label: Sub-directory name, e.g. ``query1``.
        platform: Short platform token, e.g. ``ACM`` or ``IEEE``.

    Returns:
        A stats dict with ``inserted``, ``deduped`` and ``linked_existing``.
    """
    stats = {"inserted": 0, "deduped": 0, "linked_existing": 0}

    query_platform = QUERY_PLATFORM_LABELS.get(platform, platform)
    source_name = f"{query_label}:{platform}"
    source_desc = f"Search {query_label} via {platform}"

    source_id = _ensure_source_with_desc(conn, source_name, source_desc)
    query_id = _ensure_query(conn, query_platform, query_label)

    for entry in entries:
        doi = (entry.get("doi") or "").strip() or None
        norm_title = normalize_title(entry.get("title", ""))

        existing_id = find_existing_paper(conn, doi, norm_title)

        if existing_id is not None:
            _fill_missing_fields(conn, existing_id, entry)
            paper_id = existing_id
            stats["deduped"] += 1
            if _link_if_new(conn, paper_id, source_id, query_id):
                stats["linked_existing"] += 1
        else:
            paper_id = _upsert_paper(conn, entry)
            _link_paper_to_source(conn, paper_id, source_id)
            _link_paper_to_query(conn, paper_id, query_id)
            stats["inserted"] += 1

    return stats


def _detect_platform(filename: str) -> Optional[str]:
    """Detect the platform token from a BibTeX file name."""
    lower = filename.lower()
    if "acm" in lower:
        return "ACM"
    if "ieee" in lower:
        return "IEEE"
    return None


def ingest_data_dir(conn: Any, data_dir: str | os.PathLike) -> dict[str, Any]:
    """Walk ``data_dir`` and ingest every query sub-directory's BibTeX files.

    Any directory whose name starts with ``query`` (case-insensitive) is
    treated as a query sub-directory; each ``*.bib`` file inside is parsed
    and ingested with the sub-directory name as the ``query_label``.

    Returns an aggregate stats dict.
    """
    create_schema(conn)

    data_path = Path(data_dir)
    totals: dict[str, Any] = {
        "files_processed": 0,
        "entries_parsed": 0,
        "inserted": 0,
        "deduped": 0,
        "linked_existing": 0,
    }

    query_dirs = sorted(
        p for p in data_path.iterdir()
        if p.is_dir() and p.name.lower().startswith("query")
    )

    for query_dir in query_dirs:
        query_label = query_dir.name
        for bib_file in sorted(query_dir.glob("*.bib")):
            platform = _detect_platform(bib_file.name)
            if platform is None:
                print(
                    f"Warning: skipping {bib_file}: cannot detect ACM/IEEE "
                    f"platform from file name; expected 'acm' or 'ieee'."
                )
                continue

            text = bib_file.read_text(encoding="utf-8", errors="replace")
            entries = parse_bibtex(text)
            totals["files_processed"] += 1
            totals["entries_parsed"] += len(entries)

            stats = ingest_entries(conn, entries, query_label, platform)
            totals["inserted"] += stats["inserted"]
            totals["deduped"] += stats["deduped"]
            totals["linked_existing"] += stats["linked_existing"]

    return totals


def _print_summary(totals: dict[str, Any], db_path: str) -> None:
    """Print a clear ingestion summary and per-method source counts."""
    print("=" * 60)
    print("Citation ingestion summary")
    print("=" * 60)
    print(f"Database            : {db_path}")
    print(f"Files processed     : {totals['files_processed']}")
    print(f"Entries parsed      : {totals['entries_parsed']}")
    print(f"Papers inserted     : {totals['inserted']}")
    print(f"Entries deduped     : {totals['deduped']}")
    print(f"Provenance links add: {totals['linked_existing']} (to existing papers)")

    with get_connection(db_path) as conn:
        paper_count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        print(f"Total papers in DB : {paper_count}")
        print("Method sources:")
        for row in conn.execute(
            "SELECT s.name, COUNT(ps.paper_id) AS n "
            "FROM sources s LEFT JOIN paper_sources ps ON ps.source_id = s.id "
            "GROUP BY s.name ORDER BY s.name"
        ):
            print(f"  - {row['name']:<20} {row['n']} papers")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest BibTeX citation exports into the study database."
    )
    parser.add_argument(
        "--db", default="results.db", help="Path to the SQLite database."
    )
    parser.add_argument(
        "--data-dir",
        default="cititations_data",
        help="Root directory containing query1/, query2/, ... sub-dirs.",
    )
    args = parser.parse_args()

    with get_connection(args.db) as conn:
        totals = ingest_data_dir(conn, args.data_dir)
        conn.commit()

    _print_summary(totals, args.db)


if __name__ == "__main__":
    main()
