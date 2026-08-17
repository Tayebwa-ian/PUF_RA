"""Tests for the forward, data-preserving migration framework."""

import sqlite3

from src.db_schema import (
    apply_migrations,
    create_schema,
    ensure_schema,
    get_applied_versions,
    get_schema_version,
    table_exists,
)


def test_schema_migrations_table_exists():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    assert table_exists(conn, "schema_migrations")
    conn.close()


def test_apply_migrations_records_versions():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    applied = apply_migrations(conn)
    assert applied, "at least one migration should be applied"
    assert get_schema_version(conn) > 0
    rows = conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert rows, "schema_migrations should have rows"
    versions = {r[0] for r in rows}
    assert 1 in versions
    # The example migration actually added the column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    assert "notes" in cols
    conn.close()


def test_apply_migrations_idempotent():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    first = apply_migrations(conn)
    version_after_first = get_schema_version(conn)
    # Second call should apply nothing new and not error.
    second = apply_migrations(conn)
    assert second == []
    assert get_schema_version(conn) == version_after_first
    # Column exists exactly once (membership check).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    assert "notes" in cols
    conn.close()


def test_migration_preserves_data():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute(
        "INSERT INTO papers (title, authors, year, abstract, publication_title) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Paper A", "Smith", 2023, "Abstract A", "Journal A"),
    )
    paper_id = conn.execute("SELECT id FROM papers").fetchone()[0]

    # Run migrations (adds a column) -- must not touch existing data.
    apply_migrations(conn)

    row = conn.execute(
        "SELECT id, title, authors, year, abstract, publication_title, notes "
        "FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == paper_id
    assert row[1] == "Paper A"
    assert row[2] == "Smith"
    assert row[3] == 2023
    assert row[4] == "Abstract A"
    assert row[5] == "Journal A"
    assert row[6] is None  # newly added column, NULL for existing rows
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    conn.close()


def test_ensure_schema_applies_migrations():
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    assert table_exists(conn, "schema_migrations")
    assert get_schema_version(conn) > 0
    conn.close()


def test_get_applied_versions_empty():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    assert get_applied_versions(conn) == set()
    conn.close()
