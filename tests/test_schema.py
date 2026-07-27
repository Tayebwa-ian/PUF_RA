"""Tests for database schema and migration."""

import sqlite3
import tempfile

import pytest

from src.db_schema import (
    create_schema,
    drop_all_tables,
    migrate_from_v1,
    table_exists,
)


def _make_v1_db() -> sqlite3.Connection:
    """Create a v1 database with the legacy schema and sample data."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT DEFAULT current_timestamp,
            platform TEXT NOT NULL,
            query_text TEXT NOT NULL
        );
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            year INTEGER NOT NULL,
            abstract TEXT NOT NULL,
            publication_title TEXT NOT NULL,
            doi TEXT,
            keywords TEXT,
            human_decision TEXT,
            FOREIGN KEY (query_id) REFERENCES queries(id)
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT DEFAULT current_timestamp,
            model TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            misc TEXT NOT NULL
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            criterion TEXT NOT NULL,
            justification TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            excerpt_verified BOOLEAN NOT NULL,
            tokens_used INT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(id),
            FOREIGN KEY (paper_id) REFERENCES papers(id)
        );
        INSERT INTO queries (platform, query_text) VALUES ('IEEE Xplore', 'PUF attacks');
        INSERT INTO queries (platform, query_text) VALUES ('ACM', 'side-channel PUF');
        INSERT INTO papers (query_id, title, authors, year, abstract, publication_title, doi, keywords, human_decision)
            VALUES (1, 'Paper A', 'Smith, John', 2023, 'Abstract A', 'Journal A', '10.1/a', 'PUF', 'REVIEW');
        INSERT INTO papers (query_id, title, authors, year, abstract, publication_title, doi, keywords, human_decision)
            VALUES (2, 'Paper B', 'Doe, Jane', 2024, 'Abstract B', 'Journal B', '10.1/b', 'side-channel', 'EXCLUDE');
        INSERT INTO runs (model, prompt_text, misc) VALUES ('gpt-4', 'prompt', '{}');
        INSERT INTO decisions (run_id, paper_id, decision, criterion, justification, excerpt, excerpt_verified, tokens_used)
            VALUES (1, 1, 'REVIEW', 'topic', 'good', 'Abstract A', 1, 100);
    """)
    conn.commit()
    return conn


def test_create_schema_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    expected_tables = [
        "sources", "queries", "papers", "paper_queries",
        "paper_sources", "snowball_edges", "relevance_evals",
        "runs", "decisions",
    ]
    for table in expected_tables:
        assert table_exists(conn, table), f"Table {table} should exist"


def test_drop_all_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    drop_all_tables(conn)
    expected_tables = [
        "sources", "queries", "papers", "paper_queries",
        "paper_sources", "snowball_edges", "relevance_evals",
        "runs", "decisions",
    ]
    for table in expected_tables:
        assert not table_exists(conn, table), f"Table {table} should be dropped"


def test_migrate_from_v1():
    conn = _make_v1_db()
    migrate_from_v1(conn, dry_run=False)

    # v2 tables exist
    assert table_exists(conn, "sources")
    assert table_exists(conn, "paper_sources")
    assert table_exists(conn, "snowball_edges")
    assert table_exists(conn, "relevance_evals")

    # Papers migrated
    count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert count == 2

    # Queries migrated
    count = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
    assert count == 2

    # paper_queries populated
    pq_count = conn.execute("SELECT COUNT(*) FROM paper_queries").fetchone()[0]
    assert pq_count == 2

    # paper_sources populated (inferred from platform)
    ps_count = conn.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0]
    assert ps_count >= 2

    # Runs and decisions migrated
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_migrate_from_v1_dry_run():
    conn = _make_v1_db()
    # Should not raise
    migrate_from_v1(conn, dry_run=True)
