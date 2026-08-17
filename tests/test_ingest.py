"""Hermetic tests for scripts.ingest_citations.

These exercise cross-query DOI/title deduplication, method-level provenance,
and idempotency without touching the real results.db.
"""

import os
import sqlite3
import tempfile

import pytest

from src.db import get_connection
from src.db_schema import create_schema

import scripts.ingest_citations as ic


def _write_bib(directory: str, filename: str, body: str) -> None:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def _make_corpus(root: str) -> None:
    """Create query1/query2 subdirs with small ACM .bib files under root."""
    os.makedirs(os.path.join(root, "query1"), exist_ok=True)
    os.makedirs(os.path.join(root, "query2"), exist_ok=True)

    _write_bib(
        os.path.join(root, "query1"),
        "acm_test.bib",
        "@article{paperA,\n"
        "  title = {Paper A},\n"
        "  author = {Author, One},\n"
        "  year = {2020},\n"
        "  doi = {10.1000/A}\n"
        "}\n",
    )
    _write_bib(
        os.path.join(root, "query2"),
        "acm_test.bib",
        "@article{paperA2,\n"
        "  title = {Paper A},\n"
        "  author = {Author, One},\n"
        "  year = {2020},\n"
        "  doi = {10.1000/A}\n"
        "}\n"
        "@article{paperB,\n"
        "  title = {Paper B},\n"
        "  author = {Author, Two},\n"
        "  year = {2021},\n"
        "  doi = {10.1000/B}\n"
        "}\n",
    )


def _source_names(conn: sqlite3.Connection, paper_id: int) -> set:
    return {
        row[0]
        for row in conn.execute(
            "SELECT s.name FROM sources s "
            "JOIN paper_sources ps ON ps.source_id = s.id "
            "WHERE ps.paper_id = ?",
            (paper_id,),
        )
    }


def test_cross_query_doi_dedup_and_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        _make_corpus(tmp)
        db_path = os.path.join(tmp, "test.db")
        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)

            count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            assert count == 2, f"expected 2 distinct papers, got {count}"

            # Find the paper with the shared DOI and confirm 2 method links.
            paper_id = conn.execute(
                "SELECT id FROM papers WHERE doi = ?", ("10.1000/A",)
            ).fetchone()[0]
            names = _source_names(conn, paper_id)
            assert names == {"query1:ACM", "query2:ACM"}, names


def test_idempotency():
    with tempfile.TemporaryDirectory() as tmp:
        q1 = os.path.join(tmp, "query1")
        os.makedirs(q1)
        _write_bib(
            q1,
            "acm_test.bib",
            "@article{x,\n"
            "  title = {Paper X},\n"
            "  author = {Author, X},\n"
            "  year = {2020},\n"
            "  doi = {10.0/X}\n"
            "}\n",
        )
        db_path = os.path.join(tmp, "test.db")

        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)
            first = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

        # Re-run on the same database.
        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)
            second = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

        assert first == 1
        assert second == first, "re-running created duplicate papers"


def test_title_dedup_no_doi():
    with tempfile.TemporaryDirectory() as tmp:
        q1 = os.path.join(tmp, "query1")
        q2 = os.path.join(tmp, "query2")
        os.makedirs(q1)
        os.makedirs(q2)
        _write_bib(
            q1,
            "acm_t.bib",
            "@article{t1,\n"
            "  title = {Same   Title Here},\n"
            "  author = {A, B},\n"
            "  year = {2020}\n"
            "}\n",
        )
        _write_bib(
            q2,
            "acm_t.bib",
            "@article{t2,\n"
            "  title = {same title here},\n"
            "  author = {C, D},\n"
            "  year = {2021}\n"
            "}\n",
        )
        db_path = os.path.join(tmp, "test.db")
        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)
            count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            assert count == 1, f"expected 1 paper after title dedup, got {count}"

            paper_id = conn.execute(
                "SELECT id FROM papers WHERE lower(title) = ?",
                (ic.normalize_title("Same Title Here"),),
            ).fetchone()[0]
            names = _source_names(conn, paper_id)
            assert names == {"query1:ACM", "query2:ACM"}, names


def test_normalize_title():
    assert ic.normalize_title("  Foo   BAR\nbaz ") == "foo bar baz"
    assert ic.normalize_title("") == ""


def test_find_existing_never_merges_different_dois():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        with get_connection(db_path) as conn:
            create_schema(conn)
            cur = conn.execute(
                "INSERT INTO papers (title, authors, year, abstract, "
                "publication_title, doi, keywords) VALUES "
                "('Title One', 'A', 2020, '', '', '10.1/X', NULL)"
            )
            pid = cur.lastrowid
            # Same normalized title but a DIFFERENT non-null DOI must not merge.
            assert ic.find_existing_paper(conn, "10.9/Y", ic.normalize_title("Title One")) is None
            # Same DOI must resolve to the existing paper.
            assert ic.find_existing_paper(conn, "10.1/X", "") == pid
            # Same title, no DOI on either side, merges.
            assert ic.find_existing_paper(conn, None, ic.normalize_title("Title One")) == pid



def test_doi_then_title_only_merges_no_double_insert():
    """A paper present once WITH a DOI and once WITHOUT must be one row.

    This guards requirement 3 (dedup by DOI AND title): the title-only entry
    must fall back to a normalized-title match and merge instead of being
    inserted a second time.
    """
    with tempfile.TemporaryDirectory() as tmp:
        q1 = os.path.join(tmp, "query1")
        q2 = os.path.join(tmp, "query2")
        os.makedirs(q1)
        os.makedirs(q2)
        _write_bib(
            q1,
            "acm_d.bib",
            "@article{d1,\n"
            "  title = {A Physical Attack Paper},\n"
            "  author = {A, B},\n"
            "  year = {2020},\n"
            "  doi = {10.test/x}\n"
            "}\n",
        )
        _write_bib(
            q2,
            "acm_d.bib",
            "@article{d2,\n"
            "  title = {A Physical Attack Paper},\n"
            "  author = {C, D},\n"
            "  year = {2021}\n"
            "}\n",
        )
        db_path = os.path.join(tmp, "test.db")
        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)
            count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            assert count == 1, f"expected 1 paper after title fallback, got {count}"

            paper_id = conn.execute(
                "SELECT id FROM papers WHERE doi = ?", ("10.test/x",)
            ).fetchone()[0]
            names = _source_names(conn, paper_id)
            assert names == {"query1:ACM", "query2:ACM"}, names


def test_different_dois_same_title_not_merged():
    """Two entries with the SAME title but DIFFERENT DOIs stay separate rows."""
    with tempfile.TemporaryDirectory() as tmp:
        q1 = os.path.join(tmp, "query1")
        q2 = os.path.join(tmp, "query2")
        os.makedirs(q1)
        os.makedirs(q2)
        _write_bib(
            q1,
            "acm_dd.bib",
            "@article{dd1,\n"
            "  title = {Identical Title},\n"
            "  author = {A, B},\n"
            "  year = {2020},\n"
            "  doi = {10.1/one}\n"
            "}\n",
        )
        _write_bib(
            q2,
            "acm_dd.bib",
            "@article{dd2,\n"
            "  title = {Identical Title},\n"
            "  author = {C, D},\n"
            "  year = {2021},\n"
            "  doi = {10.1/two}\n"
            "}\n",
        )
        db_path = os.path.join(tmp, "test.db")
        with get_connection(db_path) as conn:
            ic.ingest_data_dir(conn, tmp)
            count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            assert count == 2, f"expected 2 papers (different DOIs), got {count}"
