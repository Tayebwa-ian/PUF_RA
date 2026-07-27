"""BibTeX importer — import parsed BibTeX entries directly into SQLite.

Handles:
  - DOI-based deduplication (upsert semantics)
  - Junction table population (paper_queries, paper_sources)
  - Source and query registration
  - Normalisation of authors, keywords, etc.

Usage:
    from src.bibtex_importer import import_bibtex

    with get_connection("puf.db") as conn:
        import_bibtex(
            conn,
            entries=parsed_entries,
            query_ids=[1, 2],
            source_names=["ACM"],
        )
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.db import get_connection
from src.db_schema import create_schema


# Canonical separator for multi-value text fields (authors, keywords).
NORMALIZED_SEP = "; "


def _normalize_authors(raw: str) -> str:
    """Normalize author names from BibTeX format to 'First Last'.

    Splits on ' and ', flips 'Last, First' → 'First Last'.
    """
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(" and ") if p.strip()]
    normalized = []
    for part in parts:
        if "," in part:
            last, first = part.split(",", 1)
            normalized.append(f"{first.strip()} {last.strip()}")
        else:
            normalized.append(part)
    return NORMALIZED_SEP.join(normalized)


def _normalize_keywords(raw: str) -> str | None:
    """Normalize keywords field. Returns None if empty."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return NORMALIZED_SEP.join(parts) if parts else None


def _ensure_source(conn: sqlite3.Connection, name: str) -> int:
    """Get or create a source row and return its id."""
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cursor = conn.execute("INSERT INTO sources (name) VALUES (?)", (name,))
    return cursor.lastrowid


def _ensure_query(conn: sqlite3.Connection, platform: str, query_text: str) -> int:
    """Get or create a query row and return its id."""
    row = conn.execute(
        "SELECT id FROM queries WHERE platform = ? AND query_text = ?",
        (platform, query_text),
    ).fetchone()
    if row:
        return row[0]
    cursor = conn.execute(
        "INSERT INTO queries (platform, query_text) VALUES (?, ?)",
        (platform, query_text),
    )
    return cursor.lastrowid


def _upsert_paper(conn: sqlite3.Connection, entry: dict[str, Any]) -> int:
    """Insert or update a paper row based on DOI.

    If a paper with the same DOI exists, update its fields.
    If DOI is missing, attempt to insert (may create duplicates).

    Returns the paper id.
    """
    doi = entry.get("doi") or None

    # Try to find existing paper by DOI
    existing_id = None
    if doi:
        row = conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()
        if row:
            existing_id = row[0]

    paper_data = {
        "title": entry.get("title", "").strip(),
        "authors": _normalize_authors(entry.get("author", "")),
        "year": _to_int(entry.get("year")),
        "abstract": entry.get("abstract", "").strip(),
        "publication_title": _publication_title(entry),
        "doi": doi,
        "keywords": _normalize_keywords(entry.get("keywords")),
    }

    if existing_id:
        # Update existing paper
        conn.execute(
            """
            UPDATE papers
            SET title = :title,
                authors = :authors,
                year = :year,
                abstract = :abstract,
                publication_title = :publication_title,
                doi = :doi,
                keywords = :keywords,
                updated_at = current_timestamp
            WHERE id = :id
            """,
            {**paper_data, "id": existing_id},
        )
        return existing_id
    else:
        cursor = conn.execute(
            """
            INSERT INTO papers (title, authors, year, abstract, publication_title, doi, keywords)
            VALUES (:title, :authors, :year, :abstract, :publication_title, :doi, :keywords)
            """,
            paper_data,
        )
        return cursor.lastrowid


def _to_int(value: Any) -> int:
    """Convert value to int, raising ValueError if not possible."""
    if value is None:
        raise ValueError("Missing year")
    return int(str(value).strip())


def _publication_title(entry: dict[str, Any]) -> str:
    """Derive publication_title from entry_type-specific fields."""
    entry_type = entry.get("entry_type", "")
    if entry_type in ("article",):
        return entry.get("journal", "").strip()
    elif entry_type in ("inproceedings", "proceedings"):
        return entry.get("booktitle", "").strip()
    elif entry_type in ("inbook",):
        return entry.get("booktitle", entry.get("publisher", "")).strip()
    return entry.get("journal", entry.get("booktitle", "")).strip()


def _link_paper_to_query(conn: sqlite3.Connection, paper_id: int, query_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO paper_queries (paper_id, query_id) VALUES (?, ?)",
        (paper_id, query_id),
    )


def _link_paper_to_source(conn: sqlite3.Connection, paper_id: int, source_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
        (paper_id, source_id),
    )


def import_bibtex(
    entries: list[dict[str, Any]],
    query_ids: list[int],
    source_names: list[str],
    db_path: str = "results.db",
    platform: str = "unknown",
    query_text: str = "",
) -> tuple[int, int]:
    """Import BibTeX entries into SQLite.

    Args:
        entries: Parsed BibTeX entries (from src.bibtex_parser.parse_bibtex).
        query_ids: List of query IDs to associate papers with. If a query ID
            does not exist, a new query row is created with the given platform
            and query_text.
        source_names: List of source names (e.g. ['ACM', 'IEEE']).
        db_path: Path to SQLite database.
        platform: Platform name for query registration.
        query_text: Query text for query registration.

    Returns:
        Tuple of (imported_count, skipped_count).
    """
    with get_connection(db_path) as conn:
        create_schema(conn)

        source_ids = [_ensure_source(conn, name) for name in source_names]

        # Ensure queries exist; create missing ones using platform/query_text
        if not query_ids and query_text:
            query_ids = [_ensure_query(conn, platform, query_text)]
        else:
            resolved_query_ids = []
            for qid in query_ids:
                row = conn.execute("SELECT id FROM queries WHERE id = ?", (qid,)).fetchone()
                if row:
                    resolved_query_ids.append(qid)
                elif query_text:
                    new_id = _ensure_query(conn, platform, query_text)
                    resolved_query_ids.append(new_id)
                else:
                    print(f"Warning: query_id {qid} not found and no query_text provided; skipping.")
            query_ids = resolved_query_ids

        imported = 0
        skipped = 0

        for entry in entries:
            try:
                paper_id = _upsert_paper(conn, entry)
            except (ValueError, KeyError) as exc:
                print(f"Skipping entry {entry.get('cite_key', '?')}: {exc}")
                skipped += 1
                continue

            for qid in query_ids:
                _link_paper_to_query(conn, paper_id, qid)
            for sid in source_ids:
                _link_paper_to_source(conn, paper_id, sid)

            imported += 1

    return imported, skipped
