"""Schema definition, creation, and migration utilities.

This module holds the canonical CREATE TABLE statements and provides
helpers to create the schema on a fresh database or migrate an existing
database from the legacy schema (v1) to the current schema (v2).

Usage:
    from src.db_schema import create_schema, migrate_from_v1

    with get_connection("new.db") as conn:
        create_schema(conn)

    with get_connection("old_results.db") as conn:
        migrate_from_v1(conn, dry_run=False)
"""

from __future__ import annotations

import sqlite3
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical CREATE TABLE statements (v2)
# ---------------------------------------------------------------------------

CREATE_SOURCES = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);
"""

CREATE_QUERIES = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    platform TEXT NOT NULL,
    query_text TEXT NOT NULL
);
"""

CREATE_PAPERS = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER NOT NULL,
    abstract TEXT NOT NULL,
    publication_title TEXT NOT NULL,
    doi TEXT UNIQUE,
    keywords TEXT,
    is_relevant BOOLEAN DEFAULT NULL,
    relevance_score REAL,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    updated_at TEXT NOT NULL DEFAULT current_timestamp
);
"""

CREATE_PAPER_QUERIES = """
CREATE TABLE IF NOT EXISTS paper_queries (
    paper_id INTEGER NOT NULL,
    query_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, query_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
);
"""

CREATE_PAPER_SOURCES = """
CREATE TABLE IF NOT EXISTS paper_sources (
    paper_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, source_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
"""

CREATE_SNOWBALL_EDGES = """
CREATE TABLE IF NOT EXISTS snowball_edges (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    child_paper_id INTEGER NOT NULL,
    parent_paper_id INTEGER NOT NULL,
    depth INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (child_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
"""

CREATE_RELEVANCE_EVALS = """
CREATE TABLE IF NOT EXISTS relevance_evals (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    score REAL NOT NULL,
    is_relevant BOOLEAN NOT NULL,
    threshold REAL NOT NULL,
    details TEXT,
    evaluated_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
"""

CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    model TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    misc TEXT NOT NULL
);
"""

CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
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
"""

CREATE_GROUND_TRUTH = """
CREATE TABLE IF NOT EXISTS ground_truth (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    annotator_id TEXT NOT NULL,
    label TEXT NOT NULL
        CHECK (label IN ('in-scope', 'out-of-scope', 'hybrid')),
    confidence REAL,
    rationale TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (paper_id, annotator_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
"""

CREATE_GROUND_TRUTH_CONSENSUS = """
CREATE TABLE IF NOT EXISTS ground_truth_consensus (
    paper_id INTEGER NOT NULL PRIMARY KEY,
    consensus_label TEXT NOT NULL
        CHECK (consensus_label IN ('in-scope', 'out-of-scope', 'hybrid', 'disagree')),
    n_annotators INTEGER NOT NULL,
    n_agree INTEGER NOT NULL,
    method TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
"""

CREATE_EVAL_RUNS = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL,
    model TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    prompt_id TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.0,
    run_index INTEGER NOT NULL DEFAULT 1,
    config_hash TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (method, model, model_version, prompt_id, temperature, run_index)
);
"""

CREATE_EVALS = """
CREATE TABLE IF NOT EXISTS evals (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('in-scope', 'out-of-scope', 'hybrid')),
    score REAL,
    confidence REAL,
    rationale TEXT,
    matched_keywords TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (run_id, paper_id),
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
"""

CREATE_LLM_JUDGE = """
CREATE TABLE IF NOT EXISTS llm_judge (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    eval_id INTEGER NOT NULL,
    judge_model TEXT NOT NULL,
    judge_prompt_id TEXT NOT NULL DEFAULT '',
    score REAL,
    verdict TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (eval_id, judge_model, judge_prompt_id),
    FOREIGN KEY (eval_id) REFERENCES evals(id) ON DELETE CASCADE
);
"""

# Ordered list of CREATE TABLE statements.
# The order respects foreign key dependencies.
SCHEMA_STATEMENTS = [
    CREATE_SOURCES,
    CREATE_QUERIES,
    CREATE_PAPERS,
    CREATE_PAPER_QUERIES,
    CREATE_PAPER_SOURCES,
    CREATE_SNOWBALL_EDGES,
    CREATE_RELEVANCE_EVALS,
    CREATE_RUNS,
    CREATE_DECISIONS,
    CREATE_GROUND_TRUTH,
    CREATE_GROUND_TRUTH_CONSENSUS,
    CREATE_EVAL_RUNS,
    CREATE_EVALS,
    CREATE_LLM_JUDGE,
]


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables in the database if they do not already exist."""
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.commit()


def drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop all PUF_RA tables. Use with caution."""
    tables = [
        "llm_judge",
        "evals",
        "eval_runs",
        "ground_truth_consensus",
        "ground_truth",
        "decisions",
        "runs",
        "relevance_evals",
        "snowball_edges",
        "paper_sources",
        "paper_queries",
        "papers",
        "queries",
        "sources",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if the given table exists in the database."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Migration from v1 (legacy schema)
# ---------------------------------------------------------------------------
# The v1 schema had: queries, papers (with query_id FK), runs, decisions.
# The v2 schema replaces the single query_id with junction tables and adds
# new tables (sources, paper_sources, snowball_edges, relevance_evals).
#
# Migration strategy:
#   1. Create all v2 tables.
#   2. Copy data from v1 tables into v2 tables.
#   3. Populate junctions from legacy data.
#   4. Leave old query_id column in papers for reference (do not drop to
#      avoid data loss if something goes wrong).

def migrate_from_v1(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """Migrate a v1 database to v2 schema.

    Args:
        conn: Connection to the v1 database.
        dry_run: If True, print actions without executing them.

    Raises:
        RuntimeError: If the database does not appear to be v1.
    """
    if not table_exists(conn, "papers"):
        raise RuntimeError("Expected v1 database with 'papers' table; none found.")

    # Check for v1-specific column (query_id on papers)
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(papers)").fetchall()
    }
    if "query_id" not in columns:
        raise RuntimeError(
            "papers table does not have 'query_id' column; "
            "database may already be v2 or an unknown schema."
        )

    if dry_run:
        print("[DRY RUN] Would migrate v1 -> v2")
        return

    # Step 1: Create v2 tables
    create_schema(conn)

    # Step 2: Ensure default sources exist
    default_sources = [
        ("ACM", "ACM Digital Library"),
        ("IEEE", "IEEE Xplore"),
        ("Springer", "Springer Link"),
        ("Semantic Scholar", "Semantic Scholar API"),
        ("snowball", "Backward snowball search"),
    ]
    for name, desc in default_sources:
        conn.execute(
            "INSERT OR IGNORE INTO sources (name, description) VALUES (?, ?)",
            (name, desc),
        )

    # Step 3: Migrate queries (unchanged structure)
    for row in conn.execute("SELECT id, run_at, platform, query_text FROM queries"):
        conn.execute(
            "INSERT OR IGNORE INTO queries (id, run_at, platform, query_text) VALUES (?, ?, ?, ?)",
            (row[0], row[1], row[2], row[3]),
        )

    # Step 4: Migrate papers (drop legacy query_id, keep everything else)
    paper_cols = [
        "id", "title", "authors", "year", "abstract",
        "publication_title", "doi", "keywords", "human_decision",
    ]
    cols_str = ", ".join(paper_cols)
    placeholders = ", ".join("?" for _ in paper_cols)
    for row in conn.execute(f"SELECT {cols_str} FROM papers"):
        conn.execute(
            f"INSERT OR IGNORE INTO papers ({cols_str}) VALUES ({placeholders})",
            row,
        )

    # Step 5: Populate paper_queries from legacy query_id
    for paper_id, query_id in conn.execute("SELECT id, query_id FROM papers WHERE query_id IS NOT NULL"):
        conn.execute(
            "INSERT OR IGNORE INTO paper_queries (paper_id, query_id) VALUES (?, ?)",
            (paper_id, query_id),
        )

    # Step 6: Infer sources from query platform names
    _migrate_sources_from_queries(conn)

    # Step 7: Copy runs and decisions (structure unchanged)
    for row in conn.execute("SELECT id, run_at, model, prompt_text, misc FROM runs"):
        conn.execute(
            "INSERT OR IGNORE INTO runs (id, run_at, model, prompt_text, misc) VALUES (?, ?, ?, ?, ?)",
            (row[0], row[1], row[2], row[3], row[4]),
        )
    for row in conn.execute(
        "SELECT id, run_id, paper_id, decision, criterion, justification, excerpt, excerpt_verified, tokens_used FROM decisions"
    ):
        conn.execute(
            "INSERT OR IGNORE INTO decisions (id, run_id, paper_id, decision, criterion, justification, excerpt, excerpt_verified, tokens_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    conn.commit()


def _migrate_sources_from_queries(conn: sqlite3.Connection) -> None:
    """Heuristically create source entries and paper_sources links based on query platform names."""
    # Map platform name fragments to source names
    platform_to_source = {
        "acm": "ACM",
        "ieee": "IEEE",
        "springer": "Springer",
        "semantic": "Semantic Scholar",
        "scholar": "Semantic Scholar",
    }
    for row in conn.execute("SELECT DISTINCT platform FROM queries"):
        platform = row[0].lower()
        source_name = None
        for key, name in platform_to_source.items():
            if key in platform:
                source_name = name
                break
        if source_name is None:
            source_name = platform.title()

        # Ensure source exists
        conn.execute(
            "INSERT OR IGNORE INTO sources (name) VALUES (?)",
            (source_name,),
        )
        source_id = conn.execute(
            "SELECT id FROM sources WHERE name = ?", (source_name,)
        ).fetchone()[0]

        # Link all papers from queries of this platform to the source
        for q_row in conn.execute(
            "SELECT id FROM queries WHERE lower(platform) = ?", (platform,)
        ):
            query_id = q_row[0]
            for pq_row in conn.execute(
                "SELECT paper_id FROM paper_queries WHERE query_id = ?", (query_id,)
            ):
                conn.execute(
                    "INSERT OR IGNORE INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
                    (pq_row[0], source_id),
                )
