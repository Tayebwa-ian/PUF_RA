"""Database connection helpers and common query utilities."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, Optional


DEFAULT_DB_PATH = "results.db"


@contextmanager
def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection with Row factory enabled.

    Usage:
        with get_connection("my.db") as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")   # enforce FK constraints
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(
    conn: sqlite3.Connection,
    query: str,
    params: tuple | dict = (),
) -> list[sqlite3.Row]:
    """Execute a query and return all rows."""
    cursor = conn.execute(query, params)
    return cursor.fetchall()


def execute_one(
    conn: sqlite3.Connection,
    query: str,
    params: tuple | dict = (),
) -> Optional[sqlite3.Row]:
    """Execute a query and return the first row or None."""
    rows = execute(conn, query, params)
    return rows[0] if rows else None


def executemany(
    conn: sqlite3.Connection,
    query: str,
    params_seq: Iterable[tuple | dict],
) -> None:
    """Execute a query for each parameter set."""
    conn.executemany(query, params_seq)
