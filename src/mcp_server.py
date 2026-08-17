"""MCP server exposing the PUF_RA SQLite database over stdio.

The server lets an MCP client (Claude Desktop, an IDE, a custom agent, ...)
inspect and extend the literature database that the rest of the pipeline
writes to (schema in :mod:`src.db_schema`).

Exposed tools
-------------
- ``list_papers``          recent papers (summary columns)
- ``get_paper``            full row by id
- ``get_paper_by_doi``     full row by DOI
- ``search_papers``        case-insensitive LIKE on title/abstract/authors
- ``get_paper_provenance`` sources + queries + snowball parents/children
- ``execute_select``       read-only ad-hoc SELECT (guarded)
- ``insert_paper``         insert (dedup by DOI) + link source names

Transport
---------
Two interchangeable back-ends are provided:

1. The official ``mcp`` package when it is installed. ``mcp>=2`` renamed
   ``FastMCP`` to :class:`mcp.server.mcpserver.MCPServer`; both names are
   probed so either generation works.
2. A dependency-free fallback that speaks MCP JSON-RPC 2.0 over stdio using
   only the standard library (``json``/``sys``). It implements ``initialize``,
   ``tools/list`` and ``tools/call``, so the server stays usable on an
   offline machine where ``pip install mcp`` is not possible.

Both back-ends dispatch to the very same plain Python functions below, which
makes the tools directly unit-testable without starting a process.

Usage:
    python -m src.mcp_server --db results.db
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Optional

try:  # normal package import (python -m src.mcp_server, pytest)
    from src.db import DEFAULT_DB_PATH, get_connection
except ImportError:  # direct script execution (python src/mcp_server.py)
    from db import DEFAULT_DB_PATH, get_connection  # type: ignore[no-redef]


SERVER_NAME = "puf-ra-db"
SERVER_VERSION = "0.1.0"

# MCP protocol revision advertised by the stdlib fallback back-end.
PROTOCOL_VERSION = "2024-11-05"

# Columns returned by the "summary" tools (list_papers / search_papers).
SUMMARY_COLUMNS = "id, title, year, doi, publication_title"


# ---------------------------------------------------------------------------
# Database path (module level so the tool signatures stay client friendly)
# ---------------------------------------------------------------------------

_db_path: str = DEFAULT_DB_PATH


def set_db_path(path: str | Path) -> None:
    """Point every tool at ``path``. Called by :func:`run` and by tests."""
    global _db_path
    _db_path = str(path)


def get_db_path() -> str:
    """Return the database path the tools currently operate on."""
    return _db_path


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Read-only SQL guard
# ---------------------------------------------------------------------------

# Statement keywords that must never appear in an ad-hoc query. ``SELECT`` and
# ``WITH`` (read-only CTE) are the only accepted leading keywords.
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "pragma", "attach", "detach", "vacuum", "reindex", "analyze",
    "begin", "commit", "rollback", "savepoint", "grant", "revoke",
)

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)

# Line (--) and block (/* */) comments are stripped before validation so that
# a comment can neither hide a second statement nor smuggle a keyword.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Single- and double-quoted string literals are stripped before the forbidden
# keyword scan so that a forbidden word appearing *inside* a literal (e.g. a
# search for "side-channel update") is treated as data, not as a mutating
# statement. Escaped quotes ('' and "") are handled.
_SINGLE_QUOTE_LITERAL_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_DOUBLE_QUOTE_LITERAL_RE = re.compile(r'"(?:""|[^"])*"', re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    return _LINE_COMMENT_RE.sub(" ", _BLOCK_COMMENT_RE.sub(" ", sql))


def _strip_sql_literals(sql: str) -> str:
    """Replace quoted string literals with a single space."""
    return _SINGLE_QUOTE_LITERAL_RE.sub(
        " ", _DOUBLE_QUOTE_LITERAL_RE.sub(" ", sql)
    )


def assert_select_only(sql: str) -> str:
    """Validate that ``sql`` is a single read-only SELECT statement.

    Args:
        sql: Candidate SQL string.

    Returns:
        The stripped statement (without a trailing semicolon).

    Raises:
        TypeError: If ``sql`` is not a string.
        ValueError: If the statement is empty, chained with ``;``, does not
            start with SELECT/WITH, or contains a mutating keyword.
    """
    if not isinstance(sql, str):
        raise TypeError("sql must be a string")

    statement = _strip_sql_comments(sql).strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()

    # Scan a *literals-stripped* copy so a forbidden keyword or a ';' that
    # appears inside a string literal (e.g. a search for "side-channel update")
    # is treated as data, not as a mutating statement. The original statement
    # (with its literals intact) is what actually gets executed.
    scanned = _strip_sql_literals(statement)

    if not scanned:
        raise ValueError("Rejected: empty SQL statement.")
    if ";" in scanned:
        raise ValueError("Rejected: only a single statement is allowed (no ';' chaining).")

    first_word = scanned.split(None, 1)[0].lower()
    if first_word not in ("select", "with"):
        raise ValueError(
            f"Rejected: only SELECT queries are allowed, got '{first_word.upper()}'."
        )

    match = _FORBIDDEN_RE.search(scanned)
    if match:
        raise ValueError(
            f"Rejected: read-only guard forbids '{match.group(1).upper()}'."
        )
    return statement


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def list_papers(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """List the most recently added papers.

    Args:
        limit: Maximum number of rows (1-500).
        offset: Number of rows to skip, for paging.

    Returns:
        Summary dicts with id, title, year, doi and publication_title.
    """
    limit = _validate_limit(limit)
    offset = _validate_non_negative_int(offset, "offset")
    with get_connection(_db_path) as conn:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM papers "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_paper(paper_id: int) -> Optional[dict[str, Any]]:
    """Return the complete paper row for ``paper_id`` (or None if absent)."""
    paper_id = _validate_int(paper_id, "paper_id")
    with get_connection(_db_path) as conn:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return dict(row) if row is not None else None


def get_paper_by_doi(doi: str) -> Optional[dict[str, Any]]:
    """Return the complete paper row for ``doi`` (or None if absent)."""
    if not isinstance(doi, str) or not doi.strip():
        raise ValueError("doi must be a non-empty string")
    with get_connection(_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi.strip(),)
        ).fetchone()
    return dict(row) if row is not None else None


def search_papers(text: str, limit: int = 50) -> list[dict[str, Any]]:
    """Case-insensitive substring search over title, abstract and authors.

    Args:
        text: Substring to look for (LIKE ``%text%``).
        limit: Maximum number of rows (1-500).

    Returns:
        Summary dicts, newest first.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    limit = _validate_limit(limit)
    # SQLite LIKE is case-insensitive for ASCII; lower() both sides so that
    # the match also holds for mixed-case non-ASCII input.
    pattern = f"%{text.strip().lower()}%"
    with get_connection(_db_path) as conn:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM papers "
            "WHERE lower(title) LIKE ? OR lower(abstract) LIKE ? "
            "   OR lower(authors) LIKE ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (pattern, pattern, pattern, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_paper_provenance(paper_id: int) -> dict[str, Any]:
    """Return where a paper came from: sources, queries and snowball edges.

    Args:
        paper_id: Paper primary key.

    Returns:
        Dict with ``paper_id``, ``found`` (bool), ``title``, ``sources``,
        ``queries``, ``snowball_parents`` and ``snowball_children``.
    """
    paper_id = _validate_int(paper_id, "paper_id")
    with get_connection(_db_path) as conn:
        paper = conn.execute(
            "SELECT id, title, year, doi FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()

        sources = conn.execute(
            "SELECT s.id, s.name, s.description "
            "FROM paper_sources ps JOIN sources s ON s.id = ps.source_id "
            "WHERE ps.paper_id = ? ORDER BY s.name",
            (paper_id,),
        ).fetchall()

        queries = conn.execute(
            "SELECT q.id, q.platform, q.query_text, q.run_at "
            "FROM paper_queries pq JOIN queries q ON q.id = pq.query_id "
            "WHERE pq.paper_id = ? ORDER BY q.id",
            (paper_id,),
        ).fetchall()

        parents = conn.execute(
            "SELECT e.parent_paper_id AS paper_id, p.title, e.depth, e.discovered_at "
            "FROM snowball_edges e LEFT JOIN papers p ON p.id = e.parent_paper_id "
            "WHERE e.child_paper_id = ? ORDER BY e.parent_paper_id",
            (paper_id,),
        ).fetchall()

        children = conn.execute(
            "SELECT e.child_paper_id AS paper_id, p.title, e.depth, e.discovered_at "
            "FROM snowball_edges e LEFT JOIN papers p ON p.id = e.child_paper_id "
            "WHERE e.parent_paper_id = ? ORDER BY e.child_paper_id",
            (paper_id,),
        ).fetchall()

    return {
        "paper_id": paper_id,
        "found": paper is not None,
        "title": paper["title"] if paper is not None else None,
        "sources": _rows_to_dicts(sources),
        "queries": _rows_to_dicts(queries),
        "snowball_parents": _rows_to_dicts(parents),
        "snowball_children": _rows_to_dicts(children),
    }


def execute_select(sql: str) -> list[dict[str, Any]]:
    """Run an ad-hoc **read-only** SELECT and return the rows as dicts.

    The statement is validated by :func:`assert_select_only` and executed on a
    connection opened in SQLite read-only mode, so a write can neither be
    parsed nor performed.

    Raises:
        ValueError: If the statement is not a single read-only SELECT.
    """
    statement = assert_select_only(sql)
    # Defence in depth: mode=ro makes the whole connection incapable of writing.
    uri = f"file:{Path(_db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(statement).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(rows)


def insert_paper(
    title: str,
    authors: str,
    year: int,
    abstract: str,
    publication_title: str,
    doi: Optional[str] = None,
    keywords: Optional[str] = None,
    source_names: Optional[list[str]] = None,
) -> int:
    """Insert a paper (deduplicated by DOI) and link it to source names.

    Args:
        title: Paper title.
        authors: Author string as stored by the importers.
        year: Publication year (must be an int).
        abstract: Abstract text.
        publication_title: Journal/conference name.
        doi: Optional DOI; when it already exists the existing row is reused.
        keywords: Optional keyword string.
        source_names: Source names to link via ``paper_sources``; missing
            sources are created.

    Returns:
        The id of the inserted (or pre-existing) paper.

    Raises:
        TypeError: If a field has the wrong type.
        ValueError: If a required text field is empty.
    """
    title = _validate_text(title, "title")
    authors = _validate_text(authors, "authors")
    abstract = _validate_text(abstract, "abstract", allow_empty=True)
    publication_title = _validate_text(publication_title, "publication_title")

    # bool is a subclass of int; reject it explicitly.
    if isinstance(year, bool) or not isinstance(year, int):
        raise TypeError(f"year must be an int, got {type(year).__name__}")

    if doi is not None:
        if not isinstance(doi, str):
            raise TypeError(f"doi must be a str or None, got {type(doi).__name__}")
        doi = doi.strip() or None
    if keywords is not None and not isinstance(keywords, str):
        raise TypeError(f"keywords must be a str or None, got {type(keywords).__name__}")

    names = _validate_source_names(source_names)

    with get_connection(_db_path) as conn:
        paper_id: Optional[int] = None
        if doi is not None:
            existing = conn.execute(
                "SELECT id FROM papers WHERE doi = ?", (doi,)
            ).fetchone()
            if existing is not None:
                paper_id = int(existing["id"])

        if paper_id is None:
            cursor = conn.execute(
                "INSERT INTO papers "
                "(title, authors, year, abstract, publication_title, doi, keywords) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, authors, year, abstract, publication_title, doi, keywords),
            )
            paper_id = int(cursor.lastrowid)

        for name in names:
            conn.execute("INSERT OR IGNORE INTO sources (name) VALUES (?)", (name,))
            source_id = conn.execute(
                "SELECT id FROM sources WHERE name = ?", (name,)
            ).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
                (paper_id, source_id),
            )
    return paper_id


# ---------------------------------------------------------------------------
# Argument validation helpers
# ---------------------------------------------------------------------------

MAX_LIMIT = 500


def _validate_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _validate_non_negative_int(value: Any, name: str) -> int:
    value = _validate_int(value, name)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _validate_limit(limit: Any) -> int:
    limit = _validate_int(limit, "limit")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, MAX_LIMIT)


def _validate_text(value: Any, name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _validate_source_names(source_names: Any) -> list[str]:
    if source_names is None:
        return []
    if isinstance(source_names, str) or not isinstance(source_names, (list, tuple)):
        raise TypeError("source_names must be a list of strings")
    names: list[str] = []
    for name in source_names:
        if not isinstance(name, str):
            raise TypeError("source_names must be a list of strings")
        cleaned = name.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


# ---------------------------------------------------------------------------
# Tool registry (shared by both transport back-ends)
# ---------------------------------------------------------------------------

def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_INT = {"type": "integer"}
_STR = {"type": "string"}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_papers",
        "handler": list_papers,
        "description": "List the most recently added papers (id, title, year, doi, publication_title).",
        "inputSchema": _schema(
            {
                "limit": {**_INT, "default": 50, "minimum": 1, "maximum": MAX_LIMIT},
                "offset": {**_INT, "default": 0, "minimum": 0},
            },
            [],
        ),
    },
    {
        "name": "get_paper",
        "handler": get_paper,
        "description": "Get the full paper row for a paper id (null when unknown).",
        "inputSchema": _schema({"paper_id": _INT}, ["paper_id"]),
    },
    {
        "name": "get_paper_by_doi",
        "handler": get_paper_by_doi,
        "description": "Get the full paper row for a DOI (null when unknown).",
        "inputSchema": _schema({"doi": _STR}, ["doi"]),
    },
    {
        "name": "search_papers",
        "handler": search_papers,
        "description": "Case-insensitive substring search over title, abstract and authors.",
        "inputSchema": _schema(
            {
                "text": _STR,
                "limit": {**_INT, "default": 50, "minimum": 1, "maximum": MAX_LIMIT},
            },
            ["text"],
        ),
    },
    {
        "name": "get_paper_provenance",
        "handler": get_paper_provenance,
        "description": (
            "Provenance of a paper: originating sources, the queries that returned "
            "it, and its snowball parents/children."
        ),
        "inputSchema": _schema({"paper_id": _INT}, ["paper_id"]),
    },
    {
        "name": "execute_select",
        "handler": execute_select,
        "description": (
            "Run a single read-only SELECT statement and return the rows. "
            "Mutating statements and ';' chaining are rejected."
        ),
        "inputSchema": _schema({"sql": _STR}, ["sql"]),
    },
    {
        "name": "insert_paper",
        "handler": insert_paper,
        "description": (
            "Insert a paper (deduplicated by DOI) and link it to the given source "
            "names; returns the paper id."
        ),
        "inputSchema": _schema(
            {
                "title": _STR,
                "authors": _STR,
                "year": _INT,
                "abstract": _STR,
                "publication_title": _STR,
                "doi": {"type": ["string", "null"], "default": None},
                "keywords": {"type": ["string", "null"], "default": None},
                "source_names": {
                    "type": "array",
                    "items": _STR,
                    "default": [],
                },
            },
            ["title", "authors", "year", "abstract", "publication_title"],
        ),
    },
]

TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    spec["name"]: spec["handler"] for spec in TOOL_SPECS
}


def call_tool(name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
    """Dispatch a tool by name with keyword ``arguments``."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    return handler(**(arguments or {}))


# ---------------------------------------------------------------------------
# Back-end 1: official mcp package (MCPServer in mcp>=2, FastMCP in mcp 1.x)
# ---------------------------------------------------------------------------

def _load_mcp_server_class() -> Optional[Callable[..., Any]]:
    """Return the FastMCP-style server class, or None when mcp is absent."""
    try:  # mcp >= 2.0 (FastMCP was renamed MCPServer)
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:
        pass
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ImportError:
        return None


def build_mcp_server() -> Any:
    """Build an ``mcp`` server instance with all tools registered.

    Raises:
        RuntimeError: If the ``mcp`` package is not installed.
    """
    server_cls = _load_mcp_server_class()
    if server_cls is None:
        raise RuntimeError("The 'mcp' package is not installed.")
    server = server_cls(SERVER_NAME, version=SERVER_VERSION)
    for spec in TOOL_SPECS:
        server.tool(name=spec["name"], description=spec["description"])(spec["handler"])
    return server


# ---------------------------------------------------------------------------
# Back-end 2: stdlib-only MCP JSON-RPC 2.0 over stdio
# ---------------------------------------------------------------------------

# JSON-RPC error codes used by the fallback back-end.
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_result(payload: Any) -> dict[str, Any]:
    """Wrap a Python value in an MCP CallToolResult."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str, indent=2)}],
        "structuredContent": {"result": payload},
        "isError": False,
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle_message(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle one JSON-RPC message; returns the response or None for notifications."""
    if not isinstance(message, dict):
        return _error(None, _INVALID_REQUEST, "Request must be a JSON object")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    {
                        "name": spec["name"],
                        "description": spec["description"],
                        "inputSchema": spec["inputSchema"],
                    }
                    for spec in TOOL_SPECS
                ]
            },
        )

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, _INVALID_PARAMS, "arguments must be an object")
        if name not in TOOL_HANDLERS:
            return _error(request_id, _INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            payload = call_tool(name, arguments)
        except (TypeError, ValueError, sqlite3.Error) as exc:
            # Tool-level failures are reported in-band so the client can react.
            return _result(request_id, _tool_error(f"{type(exc).__name__}: {exc}"))
        except Exception as exc:  # pragma: no cover - unexpected server bug
            return _error(request_id, _INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        return _result(request_id, _tool_result(payload))

    if method == "ping":
        return _result(request_id, {})

    if is_notification or (isinstance(method, str) and method.startswith("notifications/")):
        return None

    return _error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")


def serve_stdio(stdin: Any = None, stdout: Any = None) -> None:
    """Serve MCP over newline-delimited JSON-RPC on stdio (stdlib fallback)."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[dict[str, Any]] = _error(
                None, _INVALID_REQUEST, f"Parse error: {exc}"
            )
        else:
            response = handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run(db_path: str | Path = DEFAULT_DB_PATH, prefer_mcp: bool = True) -> None:
    """Run the MCP server on stdio against ``db_path``.

    Uses the official ``mcp`` package when it is importable and falls back to
    the stdlib JSON-RPC implementation otherwise.
    """
    set_db_path(db_path)
    if prefer_mcp and _load_mcp_server_class() is not None:
        build_mcp_server().run(transport="stdio")
        return
    serve_stdio()


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="MCP server for the PUF_RA SQLite database")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument(
        "--stdlib",
        action="store_true",
        help="Force the dependency-free JSON-RPC back-end even if 'mcp' is installed",
    )
    args = parser.parse_args(argv)
    run(args.db, prefer_mcp=not args.stdlib)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
