"""Tests for the analysis module (src/analysis.py) + analysis_runs persistence.

Hermetic and fast: a temporary SQLite database is built with
:func:`src.db_schema.ensure_schema`, seeded with a small corpus, and most tests
exercise the *direct* (SQLite fallback) transport of :class:`AnalysisClient`.

The tests at the bottom additionally drive the **real MCP stdio transport**
(``mcp_mode="mcp"``, a live ``python -m src.mcp_server`` subprocess), which is
what regression-tests the ``CallToolResult`` parsing (snake_case ``is_error`` /
``content`` / ``structured_content``, one text block per row).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src import analysis
from src import mcp_server
from src.db import DEFAULT_DB_PATH, get_connection
from src.db_schema import ensure_schema


def _seed_corpus(conn: sqlite3.Connection) -> None:
    """Insert a small, known corpus: 3 papers across 3 sources + snowball edges."""
    conn.executescript(
        """
        INSERT INTO sources (id, name, description) VALUES
            (1, 'query1:ACM', 'ACM DL'),
            (2, 'query1:IEEE', 'IEEE Xplore'),
            (3, 'snowball', 'Backward snowball');

        INSERT INTO papers (id, title, authors, year, abstract, publication_title,
                            doi, relevance_class)
        VALUES
            (1, 'Side-channel on PUF', 'A', 2020, 'power analysis', 'CHES', '10.0/a', 'in-scope'),
            (2, 'Modeling attack', 'B', 2021, 'ml', 'J', '10.0/b', 'out-of-scope'),
            (3, 'Hybrid attack', 'C', 2020, 'sc+ml', 'J', '10.0/c', 'hybrid');

        INSERT INTO paper_sources (paper_id, source_id) VALUES
            (1, 1), (1, 3), (2, 2), (3, 3);

        INSERT INTO snowball_edges (child_paper_id, parent_paper_id, depth) VALUES
            (2, 1, 1), (3, 1, 1);

        INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, status) VALUES
            (1, 'backward', '10.0/r1', 'resolved'),
            (1, 'backward', '10.0/r2', 'resolved'),
            (2, 'backward', NULL, 'pending'),
            (3, 'backward', NULL, 'unresolved_no_doi');
        """
    )
    conn.commit()


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "analysis.db"
    with get_connection(path) as conn:
        ensure_schema(conn)
        _seed_corpus(conn)
    monkeypatch.setattr(mcp_server, "_db_path", str(path))
    yield path
    monkeypatch.setattr(mcp_server, "_db_path", DEFAULT_DB_PATH)


@pytest.fixture()
def client(db_path):
    return analysis.AnalysisClient(db_path=str(db_path), mcp_mode="direct")


# --- fallback transport: corpus / relevance / snowball stats -----------------


def test_client_uses_direct_fallback(db_path):
    c = analysis.AnalysisClient(db_path=str(db_path), mcp_mode="direct")
    assert c.uses_mcp is False


def test_corpus_by_source(client):
    rows = analysis.corpus_by_source(client)
    by_source = {r["source"]: int(r["n_papers"]) for r in rows}
    assert by_source == {"query1:ACM": 1, "query1:IEEE": 1, "snowball": 2}


def test_corpus_by_method_categories(client):
    result = analysis.corpus_by_method(client)
    assert result["by_category"]["snowball"] == 2
    assert result["by_category"]["database_query"] == 2  # query1:ACM + query1:IEEE
    assert sum(result["by_category"].values()) == 4  # total paper-source links


def test_corpus_by_year(client):
    rows = analysis.corpus_by_year(client)
    by_year = {int(r["year"]): int(r["n_papers"]) for r in rows}
    assert by_year == {2020: 2, 2021: 1}


def test_relevance_distribution(client):
    rows = analysis.relevance_distribution(client)
    by_class = {r["relevance_class"]: int(r["n_papers"]) for r in rows}
    assert by_class == {"in-scope": 1, "out-of-scope": 1, "hybrid": 1}


def test_snowball_status(client):
    stats = analysis.snowball_status(client)
    status = {r["status"]: int(r["n"]) for r in stats["status_counts"]}
    assert status == {"resolved": 2, "pending": 1, "unresolved_no_doi": 1}
    assert stats["edges"] == 2
    assert stats["papers_discovered_via_snowball"] == 2


# --- persistence: store_analysis / list_analysis round-trip ------------------


def test_store_and_list_analysis_via_client(client):
    rid = client.store("unit_test_analysis", {"answer": 42})
    assert isinstance(rid, int)
    listed = client.list_analyses()
    names = [r["name"] for r in listed]
    assert "unit_test_analysis" in names
    row = next(r for r in listed if r["name"] == "unit_test_analysis")
    assert row["id"] == rid
    assert "generated_at" in row


def test_store_analysis_upsert_is_single_row(client):
    client.store("dup", {"v": 1})
    client.store("dup", {"v": 2})
    listed = client.list_analyses()
    assert [r["name"] for r in listed].count("dup") == 1
    # payload survived via the read-only SELECT guard
    payload = client.select("SELECT result_json FROM analysis_runs WHERE name='dup'")
    assert json.loads(payload[0]["result_json"]) == {"v": 2}


def test_store_analysis_roundtrip_via_server_functions(db_path):
    # Direct DB helper path (no client) — mirrors what the MCP tool does.
    rid = mcp_server.store_analysis("svc", '{"ok": true}')
    listed = mcp_server.list_analysis()
    assert any(r["name"] == "svc" and r["id"] == rid for r in listed)


# --- plotting (Agg, no display) ----------------------------------------------


def test_plot_corpus_bars_writes_png(client, tmp_path):
    rows = analysis.corpus_by_source(client)
    path = analysis.plot_corpus_bars(rows, tmp_path, "corpus_test")
    assert Path(path).exists() and Path(path).suffix == ".png"


def test_plot_years_and_snowball_write_png(client, tmp_path):
    yp = analysis.plot_years(analysis.corpus_by_year(client), tmp_path, "years_test")
    sp = analysis.plot_snowball_status(analysis.snowball_status(client), tmp_path, "snow_test")
    assert Path(yp).exists() and Path(sp).exists()


# --- evaluation_summary -------------------------------------------------------


def test_evaluation_summary_no_data_note(tmp_path):
    empty = tmp_path / "empty.db"
    with get_connection(empty) as conn:
        ensure_schema(conn)
    c = analysis.AnalysisClient(db_path=str(empty), mcp_mode="direct")
    summary = analysis.evaluation_summary(c)
    assert summary["available"] is False
    assert "no evaluation data" in summary["note"]


def test_evaluation_summary_returns_metrics_when_seeded(db_path):
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO ground_truth_consensus
                (paper_id, consensus_label, n_annotators, n_agree, method)
            VALUES (1, 'in-scope', 2, 2, 'unanimous'),
                   (2, 'out-of-scope', 2, 2, 'unanimous');
            INSERT INTO eval_runs
                (id, method, model, model_version, prompt_id, temperature, run_index)
            VALUES (1, 'baseline', 'deterministic', '', 'n/a', 0.0, 1);
            INSERT INTO evals (run_id, paper_id, decision, score) VALUES
                (1, 1, 'in-scope', 0.9),
                (1, 2, 'out-of-scope', 0.1);
            """
        )
        conn.commit()
    c = analysis.AnalysisClient(db_path=str(db_path), mcp_mode="direct")
    summary = analysis.evaluation_summary(c)
    assert summary["available"] is True
    assert summary["n_runs"] == 1
    run = summary["runs"][0]
    assert run["run_id"] == 1
    m = run["metrics"]
    assert m["accuracy"] == 1.0
    # Macro-F1 averages over all 3 classes; only 2 classes have samples, so the
    # absent 'hybrid' class contributes F1=0 and the macro is 2/3.
    assert abs(m["macro_f1"] - 2 / 3) < 1e-9
    assert m["kappa"] == 1.0
    assert m["auc_in_scope"] == 1.0
    assert "confusion" in m


def test_run_analysis_end_to_end(client, tmp_path):
    summary = analysis.run_analysis(
        "e2e", client, kinds=("corpus", "relevance", "snowball"), out_dir=tmp_path
    )
    assert summary["name"] == "e2e"
    assert Path(summary["json"]).exists()
    assert len(summary["plots"]) == 3  # corpus, years, snowball
    # persisted via store_analysis
    listed = client.list_analyses()
    assert "e2e" in [r["name"] for r in listed]
    # stored JSON parses back to the stats
    payload = client.select("SELECT result_json FROM analysis_runs WHERE name='e2e'")
    stored = json.loads(payload[0]["result_json"])
    assert "corpus_by_source" in stored
    assert "relevance_distribution" in stored


# --- MCP stdio transport (real subprocess) -----------------------------------


@pytest.fixture()
def mcp_client(db_path):
    """A client bound to the REAL MCP stdio server (skips when mcp is absent)."""
    pytest.importorskip("mcp")
    client = analysis.AnalysisClient(db_path=str(db_path), mcp_mode="mcp")
    try:
        yield client
    finally:
        client.close()


def test_mcp_transport_select_returns_all_rows(mcp_client):
    """A multi-row SELECT over MCP must return EVERY row, not just the first.

    The SDK emits one ``TextContent`` block per list item and exposes the fields
    as ``is_error`` / ``structured_content`` (snake_case), so reading
    ``content[0].text`` / ``structuredContent`` silently truncated the result to
    a single row.
    """
    assert mcp_client.uses_mcp is True
    rows = analysis.corpus_by_source(mcp_client)
    assert len(rows) == 3
    by_source = {r["source"]: int(r["n_papers"]) for r in rows}
    assert by_source == {"query1:ACM": 1, "query1:IEEE": 1, "snowball": 2}
    # the stat builder that consumed the truncated payload before
    method_stats = analysis.corpus_by_method(mcp_client)
    assert method_stats["by_category"] == {"database_query": 2, "snowball": 2}
    assert method_stats["n_papers_distinct"] == 3
    assert method_stats["n_multi_method_papers"] == 1


def test_mcp_transport_store_and_list_analysis_roundtrip(mcp_client):
    rid = mcp_client.store("mcp_roundtrip", {"answer": 42})
    assert isinstance(rid, int)
    listed = mcp_client.list_analyses()
    assert any(r["name"] == "mcp_roundtrip" and r["id"] == rid for r in listed)
    payload = mcp_client.select(
        "SELECT result_json FROM analysis_runs WHERE name='mcp_roundtrip'"
    )
    assert json.loads(payload[0]["result_json"]) == {"answer": 42}


def test_mcp_transport_rejection_raises_valueerror(mcp_client):
    """A guard rejection must surface as ValueError, not a JSONDecodeError."""
    with pytest.raises(ValueError):
        mcp_client.select("DROP TABLE papers")


def test_mcp_transport_provenance_and_single_session(mcp_client):
    prov = mcp_client.provenance(1)
    assert prov["found"] is True
    assert {s["name"] for s in prov["sources"]} == {"query1:ACM", "snowball"}
    mix = analysis.provenance_mix(mcp_client, sample_size=2)
    assert mix["sample_size"] == 2
    assert mix["papers"][0]["paper_id"] == 1
    # one long-lived session for the client's lifetime (no subprocess per query)
    session = mcp_client._mcp
    for _ in range(3):
        mcp_client.select("SELECT 1 AS one")
    assert mcp_client._mcp is session and session.alive


def test_parse_tool_result_handles_wire_and_model_shapes():
    """Unit-level guard for the CallToolResult parsing rules."""

    class _Text:
        def __init__(self, text):
            self.text = text

    class _Result:
        def __init__(self, content, is_error=False, structured_content=None):
            self.content = content
            self.is_error = is_error
            self.structured_content = structured_content

    # one text block per row (official SDK shape for a list return)
    rows = analysis.parse_tool_result(
        "execute_select", _Result([_Text('{"a": 1}'), _Text('{"a": 2}')])
    )
    assert rows == [{"a": 1}, {"a": 2}]
    # structured_content wins when present (stdlib fallback back-end)
    assert analysis.parse_tool_result(
        "execute_select",
        _Result([_Text("[]")], structured_content={"result": [{"a": 3}]}),
    ) == [{"a": 3}]
    # store_analysis returns the bare row id as text
    assert analysis.parse_tool_result("store_analysis", _Result([_Text("7")])) == 7
    # is_error -> ValueError
    with pytest.raises(ValueError):
        analysis.parse_tool_result("execute_select", _Result([_Text("boom")], is_error=True))
    # in-band guard rejection -> ValueError (never a JSONDecodeError)
    with pytest.raises(ValueError):
        analysis.parse_tool_result(
            "execute_select", _Result([_Text("Rejected: only SELECT queries are allowed")])
        )
