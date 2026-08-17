"""Tests for src.reference_store (two-phase, local-first snowball).

Hermetic: no real network. External HTTP is mocked via unittest.mock on
``src.snowball.urlopen`` (which ``reference_store`` uses through ``_get_json``).
"""

import json
import sqlite3
from unittest import mock

import pytest

import src.snowball as snowball
from src.db_schema import create_schema
from src import reference_store, zotero_sync
from src.reference_store import (
    harvest_references,
    resolve_reference_lists,
    get_reference_inventory,
    local_find_paper,
)


def _memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    return conn


def _insert_paper(conn, title, doi=None, year=2020, authors="A. Author"):
    cur = conn.execute(
        """
        INSERT INTO papers (title, authors, year, abstract, publication_title, doi)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, authors, year, "", "Venue", doi),
    )
    return cur.lastrowid


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _make_urlopen(routing):
    """Build a fake urlopen that routes by URL substring -> payload dict."""

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for needle, payload in routing:
            if needle in url:
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected urlopen URL: {url}")

    return fake


# ---------------------------------------------------------------------------
# local-first resolution
# ---------------------------------------------------------------------------

def test_local_first_resolves_without_api(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    ref = _insert_paper(conn, "Ref R", doi="10.2/R")

    fake_urlopen = mock.Mock()
    monkeypatch.setattr(snowball, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        reference_store,
        "_fetch_seed_reference_list",
        lambda *a, **k: (
            [{"doi": "10.2/R", "title": "Ref R", "year": 2020, "authors": "X", "unstructured": ""}],
            0,
        ),
    )

    stats = harvest_references(conn, [seed], direction="backward", source="crossref")

    assert fake_urlopen.call_count == 0
    rows = get_reference_inventory(conn, parent_paper_id=seed)
    assert len(rows) == 1
    assert rows[0]["resolved_paper_id"] == ref
    edge = conn.execute(
        "SELECT 1 FROM snowball_edges WHERE child_paper_id=? AND parent_paper_id=?",
        (ref, seed),
    ).fetchone()
    assert edge is not None
    assert stats["edges"] == 1
    assert stats["resolved_local"] == 1


# ---------------------------------------------------------------------------
# backward crossref: inventory completeness + batch resolution
# ---------------------------------------------------------------------------

def test_harvest_backward_crossref_stores_inventory_and_resolves(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    routing = [
        (
            "/works/10.2/x",
            {
                "message": {
                    "DOI": "10.2/X",
                    "title": ["Paper X"],
                    "author": [{"family": "Y", "given": "Z"}],
                    "published": {"date-parts": [[2019]]},
                    "container-title": ["Venue"],
                    "URL": "http://example/x",
                }
            },
        ),
        (
            "/works/10.1/A",
            {
                "message": {
                    "reference": [
                        {
                            "DOI": "10.2/X",
                            "article-title": "Paper X",
                            "year": 2019,
                            "author": [{"family": "Y", "given": "Z"}],
                        },
                        {"unstructured": "Some unstructured reference string"},
                    ]
                }
            },
        ),
    ]
    monkeypatch.setattr(snowball, "urlopen", _make_urlopen(routing))

    stats = harvest_references(conn, [seed], direction="backward", source="crossref")

    rows = get_reference_inventory(conn, parent_paper_id=seed)
    assert len(rows) == 2  # inventory completeness: ALL references stored
    dois = {r["ref_doi"] for r in rows}
    assert "10.2/x" in dois
    unresolved = [r for r in rows if r["resolved_paper_id"] is None]
    assert len(unresolved) == 1  # the unstructured-only ref stays unresolved
    assert unresolved[0]["ref_unstructured"] == "Some unstructured reference string"

    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2  # seed + X
    assert stats["new_papers"] == 1
    assert stats["edges"] == 1


# ---------------------------------------------------------------------------
# batch resolution (second phase)
# ---------------------------------------------------------------------------

def test_batch_resolution(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) VALUES (?, 'backward', '10.2/X', 'crossref')",
        (seed,),
    )
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) VALUES (?, 'backward', '10.3/Y', 'crossref')",
        (seed,),
    )

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "10.2/x" in url:
            return _FakeResponse({
                "message": {
                    "DOI": "10.2/X",
                    "title": ["Paper X"],
                    "author": [{"family": "Y", "given": "Z"}],
                    "published": {"date-parts": [[2019]]},
                }
            })
        if "10.3/y" in url:
            return _FakeResponse({
                "message": {
                    "DOI": "10.3/Y",
                    "title": ["Paper Y"],
                    "author": [{"family": "W", "given": "V"}],
                    "published": {"date-parts": [[2020]]},
                }
            })
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    stats = resolve_reference_lists(conn, source="crossref")

    assert stats["api_calls"] == 2  # Crossref resolves one DOI per polite request
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3
    resolved = conn.execute(
        "SELECT COUNT(*) FROM reference_lists WHERE resolved_paper_id IS NOT NULL"
    ).fetchone()[0]
    assert resolved == 2


# ---------------------------------------------------------------------------
# forward openalex
# ---------------------------------------------------------------------------

def test_forward_openalex(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    citing1 = {
        "id": "https://openalex.org/W200",
        "doi": "10.4/C1",
        "title": "Citing One",
        "publication_year": 2021,
        "authorships": [{"author": {"display_name": "C. Ited"}}],
        "primary_location": {"source": {"display_name": "Journal"}},
        "best_oa_location": {"pdf_url": "http://oa/c1.pdf"},
    }
    citing2 = {
        "id": "https://openalex.org/W201",
        "doi": "10.5/C2",
        "title": "Citing Two",
        "publication_year": 2022,
        "authorships": [],
        "primary_location": {"source": {"display_name": "Journal"}},
        "best_oa_location": {"pdf_url": "http://oa/c2.pdf"},
    }

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "filter=doi" in url:
            return _FakeResponse({"results": [{"id": "https://openalex.org/W123"}]})
        if "cites" in url and "W123" in url:
            return _FakeResponse({"results": [citing1, citing2]})
        if "filter=ids.openalex" in url:
            return _FakeResponse({"results": [citing1, citing2]})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    stats = harvest_references(
        conn, [seed], direction="forward", source="openalex", mailto="me@example.com"
    )

    rows = get_reference_inventory(conn, parent_paper_id=seed)
    assert [r["direction"] for r in rows] == ["forward", "forward"]
    assert all(r["resolved_paper_id"] is not None for r in rows)
    assert stats["edges"] == 2
    assert stats["new_papers"] == 2


# ---------------------------------------------------------------------------
# zotero push (mocked)
# ---------------------------------------------------------------------------

class _FakeZotero:
    def __init__(self):
        self.create_items_calls = []
        self.collection_names_return = {}

    def collection_names(self):
        return self.collection_names_return

    def create_collections(self, specs):
        return [{"key": "COLL1"}]

    def create_items(self, templates, collection=None):
        self.create_items_calls.append(list(templates))
        return [{"key": f"K{i}"} for i in range(len(templates))]


def test_zotero_push_mocked(monkeypatch):
    conn = _memory_db()
    p1 = _insert_paper(conn, "P1", doi="10.1/A")
    p2 = _insert_paper(conn, "P2", doi="10.2/B")
    p3 = _insert_paper(conn, "P3", doi="10.3/C")
    p4 = _insert_paper(conn, "P4", doi="10.4/D")
    p5 = _insert_paper(conn, "P5", doi="10.5/E")
    p6 = _insert_paper(conn, "No DOI")  # should be skipped

    fake = _FakeZotero()

    n = zotero_sync.push_dois_to_zotero(
        conn, paper_ids=[p1, p2, p3, p4, p5], zotero=fake, batch_size=2
    )

    assert n == 5
    assert len(fake.create_items_calls) == 3  # 5 items in batches of 2 -> 2,2,1
    for call in fake.create_items_calls:
        assert len(call) <= 2
    total = sum(len(c) for c in fake.create_items_calls)
    assert total == 5


def test_zotero_push_graceful_when_unavailable(monkeypatch):
    conn = _memory_db()
    _insert_paper(conn, "P1", doi="10.1/A")

    monkeypatch.setattr(zotero_sync, "Zotero", None)

    n = zotero_sync.push_dois_to_zotero(conn, paper_ids=None, zotero=None)
    assert n == 0


def test_local_find_paper_precedence(monkeypatch):
    conn = _memory_db()
    _insert_paper(conn, "Known", doi="10.9/K")
    assert local_find_paper(conn, "10.9/k", "known") is not None
    assert local_find_paper(conn, None, "unknown title") is None
