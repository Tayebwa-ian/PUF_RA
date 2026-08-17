"""Tests for the MCP database server (src/mcp_server.py).

These exercise the tool *functions* directly against a temporary SQLite
database. The server process is never started and no network is used.
"""

import shutil
import sqlite3

import pytest

from src import mcp_server
from src.db import DEFAULT_DB_PATH
from src.db_schema import create_schema


SEED_SQL = """
INSERT INTO papers (id, title, authors, year, abstract, publication_title, doi, keywords)
VALUES (1, 'Side-Channel Attacks on Arbiter PUFs', 'Doe, Jane', 2024,
        'Power analysis of PUFs.', 'CHES 2024', '10.1/side', 'PUF, side-channel');
INSERT INTO papers (id, title, authors, year, abstract, publication_title, doi, keywords)
VALUES (2, 'A Modeling Study', 'Smith, John', 2023,
        'Machine learning attack.', 'Journal X', '10.2/model', 'PUF');
INSERT INTO sources (id, name, description) VALUES (1, 'IEEE', 'IEEE Xplore');
INSERT INTO paper_sources (paper_id, source_id) VALUES (1, 1);
INSERT INTO queries (id, platform, query_text) VALUES (1, 'IEEE Xplore', 'PUF side-channel');
INSERT INTO paper_queries (paper_id, query_id) VALUES (1, 1);
INSERT INTO snowball_edges (child_paper_id, parent_paper_id, depth) VALUES (2, 1, 1);
"""


@pytest.fixture(scope="module")
def template_db(tmp_path_factory):
    """Build the seed database once: two papers, a source + link, a query
    link and a snowball edge (1 -> 2).
    """
    path = tmp_path_factory.mktemp("mcp_template") / "template.db"
    conn = sqlite3.connect(path)
    try:
        # Test-only: skip fsync, this throwaway DB never needs to survive a crash.
        conn.execute("PRAGMA synchronous=OFF")
        create_schema(conn)
        conn.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def db(tmp_path, template_db):
    """Give each test its own copy of the seed DB and point the tools at it."""
    db_path = tmp_path / "results.db"
    shutil.copy(template_db, db_path)
    mcp_server.set_db_path(str(db_path))
    yield db_path
    mcp_server.set_db_path(DEFAULT_DB_PATH)


def test_list_papers(db):
    papers = mcp_server.list_papers()
    assert len(papers) == 2
    assert set(papers[0].keys()) == {"id", "title", "year", "doi", "publication_title"}
    titles = {p["title"] for p in papers}
    assert "Side-Channel Attacks on Arbiter PUFs" in titles


def test_list_papers_limit_offset(db):
    first = mcp_server.list_papers(limit=1, offset=0)
    second = mcp_server.list_papers(limit=1, offset=1)
    assert len(first) == 1 and len(second) == 1
    assert first[0]["id"] != second[0]["id"]


def test_get_paper_and_missing(db):
    paper = mcp_server.get_paper(1)
    assert paper is not None
    assert paper["title"] == "Side-Channel Attacks on Arbiter PUFs"
    assert paper["abstract"] == "Power analysis of PUFs."
    assert mcp_server.get_paper(999) is None


def test_get_paper_by_doi(db):
    paper = mcp_server.get_paper_by_doi("10.1/side")
    assert paper is not None
    assert paper["id"] == 1
    assert mcp_server.get_paper_by_doi("10.9/missing") is None


def test_search_papers_case_insensitive(db):
    hits = mcp_server.search_papers("SIDE-CHANNEL")
    assert len(hits) == 1
    assert hits[0]["id"] == 1
    # matches on abstract too
    assert len(mcp_server.search_papers("machine learning")) == 1
    # matches on authors
    assert len(mcp_server.search_papers("smith")) == 1


def test_get_paper_provenance(db):
    prov = mcp_server.get_paper_provenance(1)
    assert prov["found"] is True
    assert prov["paper_id"] == 1
    assert [s["name"] for s in prov["sources"]] == ["IEEE"]
    assert [q["platform"] for q in prov["queries"]] == ["IEEE Xplore"]
    # paper 1 is the parent of paper 2
    assert [c["paper_id"] for c in prov["snowball_children"]] == [2]
    assert prov["snowball_parents"] == []

    child_prov = mcp_server.get_paper_provenance(2)
    assert [p["paper_id"] for p in child_prov["snowball_parents"]] == [1]
    assert child_prov["snowball_children"] == []


def test_get_paper_provenance_missing(db):
    prov = mcp_server.get_paper_provenance(999)
    assert prov["found"] is False
    assert prov["sources"] == []


def test_execute_select_allows_select(db):
    rows = mcp_server.execute_select("SELECT id, title FROM papers ORDER BY id")
    assert [r["id"] for r in rows] == [1, 2]


def test_execute_select_allows_cte(db):
    rows = mcp_server.execute_select(
        "WITH t AS (SELECT id FROM papers WHERE year = 2024) SELECT COUNT(*) AS n FROM t"
    )
    assert rows == [{"n": 1}]


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE papers",
        "DELETE FROM papers",
        "UPDATE papers SET title = 'x' WHERE id = 1",
        "INSERT INTO papers (title) VALUES ('x')",
        "PRAGMA table_info(papers)",
        "SELECT 1; DROP TABLE papers",
        "ATTACH DATABASE 'evil.db' AS evil",
        "",
    ],
)
def test_execute_select_rejects_non_select(db, bad_sql):
    with pytest.raises((ValueError, TypeError)):
        mcp_server.execute_select(bad_sql)


def test_execute_select_does_not_mutate(db):
    # a rejected statement must not have touched the table
    with pytest.raises(ValueError):
        mcp_server.execute_select("DROP TABLE papers")
    assert len(mcp_server.list_papers()) == 2


def test_insert_paper_and_link_sources(db):
    new_id = mcp_server.insert_paper(
        title="Fault Injection on PUFs",
        authors="Roe, Rick",
        year=2025,
        abstract="Laser fault injection.",
        publication_title="FDTC 2025",
        doi="10.3/fault",
        keywords="PUF, fault",
        source_names=["IEEE", "snowball"],
    )
    assert isinstance(new_id, int)
    paper = mcp_server.get_paper(new_id)
    assert paper["title"] == "Fault Injection on PUFs"

    prov = mcp_server.get_paper_provenance(new_id)
    assert {s["name"] for s in prov["sources"]} == {"IEEE", "snowball"}


def test_insert_paper_dedup_by_doi(db):
    same_id = mcp_server.insert_paper(
        title="Duplicate",
        authors="X",
        year=2024,
        abstract="dup",
        publication_title="J",
        doi="10.1/side",  # already exists as paper 1
        source_names=["snowball"],
    )
    assert same_id == 1
    # no new paper row was created
    assert len(mcp_server.list_papers()) == 2
    # but the source link was still added
    prov = mcp_server.get_paper_provenance(1)
    assert "snowball" in {s["name"] for s in prov["sources"]}


def test_insert_paper_rejects_bad_year(db):
    with pytest.raises(TypeError):
        mcp_server.insert_paper(
            title="Bad Year",
            authors="X",
            year="2024",  # not an int
            abstract="a",
            publication_title="J",
        )


def test_insert_paper_rejects_empty_title(db):
    with pytest.raises(ValueError):
        mcp_server.insert_paper(
            title="   ",
            authors="X",
            year=2024,
            abstract="a",
            publication_title="J",
        )


# --- transport-agnostic wiring (still no process/network) -------------------

def test_tool_registry_matches_spec():
    names = {spec["name"] for spec in mcp_server.TOOL_SPECS}
    assert names == {
        "list_papers", "get_paper", "get_paper_by_doi", "search_papers",
        "get_paper_provenance", "execute_select", "insert_paper",
        "store_analysis", "list_analysis",
    }


def test_stdlib_tools_list_and_call(db):
    listed = mcp_server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert len(listed["result"]["tools"]) == len(mcp_server.TOOL_SPECS)

    called = mcp_server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_paper_by_doi", "arguments": {"doi": "10.1/side"}},
        }
    )
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["result"]["id"] == 1


def test_stdlib_tools_call_reports_guard_error(db):
    called = mcp_server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "execute_select", "arguments": {"sql": "DROP TABLE papers"}},
        }
    )
    assert called["result"]["isError"] is True



def test_execute_select_allows_keyword_inside_string_literal(db):
    # A forbidden word smuggled inside a string literal must NOT trip the
    # read-only guard; only real mutating statements are rejected.
    rows = mcp_server.execute_select(
        "SELECT * FROM papers WHERE abstract LIKE '%update%'"
    )
    assert isinstance(rows, list)
    with pytest.raises(ValueError):
        mcp_server.execute_select("DROP TABLE papers")



def test_store_analysis_and_list_analysis(db):
    # First run: insert a new analysis row.
    rid = mcp_server.store_analysis(
        name="corpus_summary",
        result_json='{"papers": 2, "sources": 1}',
    )
    assert isinstance(rid, int)
    listed = mcp_server.list_analysis()
    assert any(r["name"] == "corpus_summary" for r in listed)
    row = next(r for r in listed if r["name"] == "corpus_summary")
    assert row["id"] == rid
    assert "generated_at" in row

    # Second run with the same name: upsert (INSERT OR REPLACE) keeps one row.
    rid2 = mcp_server.store_analysis(
        name="corpus_summary",
        result_json='{"papers": 3, "sources": 2}',
    )
    listed2 = mcp_server.list_analysis()
    names = [r["name"] for r in listed2]
    assert names.count("corpus_summary") == 1
    # INSERT OR REPLACE retires the old row and mints a fresh autoincrement id,
    # but the name still identifies exactly one (updated) row.
    assert any(r["name"] == "corpus_summary" and r["id"] == rid2 for r in listed2)

    # The persisted payload is retrievable via the read-only SELECT guard.
    payload = mcp_server.execute_select(
        "SELECT result_json FROM analysis_runs WHERE name = 'corpus_summary'"
    )
    assert payload[0]["result_json"] == '{"papers": 3, "sources": 2}'


def test_store_analysis_rejects_bad_args(db):
    with pytest.raises(TypeError):
        mcp_server.store_analysis(name="x", result_json=123)  # not a str
    with pytest.raises(ValueError):
        mcp_server.store_analysis(name="   ", result_json="{}")  # empty name


def test_stdlib_store_and_list_analysis(db):
    called = mcp_server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "store_analysis",
                "arguments": {"name": "std_tool", "result_json": "{\"ok\": true}"},
            },
        }
    )
    assert called["result"]["isError"] is False
    rid = called["result"]["structuredContent"]["result"]
    assert isinstance(rid, int)

    listed = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
         "params": {"name": "list_analysis", "arguments": {}}}
    )
    assert listed["result"]["isError"] is False
    names = [r["name"] for r in listed["result"]["structuredContent"]["result"]]
    assert "std_tool" in names
