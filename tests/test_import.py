"""Tests for importers."""

import sqlite3

import pytest

from src.bibtex_importer import import_bibtex
from src.csv_importer import import_csv, map_acm_row, map_ieee_row
from src.db_schema import create_schema


BIB_ENTRIES = [
    {
        "entry_type": "article",
        "cite_key": "smith2023",
        "author": "Smith, John and Doe, Jane",
        "title": "A Study on PUFs",
        "journal": "IEEE Transactions on Hardware Security",
        "year": "2023",
        "doi": "10.1234/example",
        "abstract": "This paper studies physical unclonable functions.",
        "keywords": "PUF, hardware security",
    },
    {
        "entry_type": "inproceedings",
        "cite_key": "doe2024",
        "author": "Doe, Jane",
        "title": "Side-Channel Attacks on Arbiter PUFs",
        "booktitle": "CHES 2024",
        "year": "2024",
        "doi": "10.5678/example2",
        "abstract": "We present side-channel attacks on arbiter PUFs using power analysis.",
        "keywords": "PUF, side-channel",
    },
]


def _make_conn_with_queries(query_ids: list[int], db_path: str) -> sqlite3.Connection:
    """Create a DB with schema and query rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    for qid in query_ids:
        conn.execute(
            "INSERT INTO queries (id, platform, query_text) VALUES (?, ?, ?)",
            (qid, f"Platform{qid}", f"query{qid}"),
        )
    conn.commit()
    return conn


def test_import_bibtex():
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        with _make_conn_with_queries([1, 2], db_path) as conn:
            pass  # just create schema + queries
        imported, skipped = import_bibtex(
            entries=BIB_ENTRIES,
            query_ids=[1, 2],
            source_names=["ACM", "IEEE"],
            db_path=db_path,
        )
    finally:
        import os
        os.unlink(db_path)
    assert imported == 2
    assert skipped == 0


def test_import_bibtex_dedup():
    entries = BIB_ENTRIES + [BIB_ENTRIES[0]]  # duplicate DOI
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        with _make_conn_with_queries([1], db_path) as conn:
            pass
        imported, skipped = import_bibtex(
            entries=entries,
            query_ids=[1],
            source_names=["ACM"],
            db_path=db_path,
        )
        # Verify dedup: only 2 unique papers exist despite 3 entries processed
        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        count = check.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        check.close()
        assert count == 2
    finally:
        import os
        os.unlink(db_path)


def test_import_bibtex_missing_year():
    entries = [{"entry_type": "article", "cite_key": "bad", "author": "X", "title": "T"}]
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        imported, skipped = import_bibtex(entries, [1], ["ACM"], db_path=":memory:")
    assert skipped == 1
    assert imported == 0


def test_map_acm_row():
    row = {
        "entry_type": "article",
        "author": "Smith, John",
        "title": "Test Paper",
        "year": "2023",
        "journal": "IEEE J.",
        "abstract": "An abstract.",
        "doi": "10.1/test",
        "keywords": "PUF, security",
    }
    result = map_acm_row(row, 1)
    assert result["title"] == "Test Paper"
    assert result["authors"] == "John Smith"
    assert result["year"] == 2023
    assert result["doi"] == "10.1/test"


def test_map_ieee_row():
    row = {
        "Document Title": "Test Paper",
        "Authors": "Smith, John; Doe, Jane",
        "Publication Year": "2023",
        "Abstract": "An abstract.",
        "DOI": "10.1/test",
        "Publication Title": "IEEE J.",
        "Author Keywords": "PUF; security",
    }
    result = map_ieee_row(row, 1)
    assert result["title"] == "Test Paper"
    assert result["authors"] == "John Smith; Jane Doe"
    assert result["year"] == 2023
    assert result["doi"] == "10.1/test"
