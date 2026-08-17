"""Tests for src.reference_store (two-phase, local-first snowball).

Hermetic: no real network. External HTTP is mocked via unittest.mock on
``src.snowball.urlopen`` (which ``reference_store`` uses through ``_get_json``).
"""

import json
import sqlite3
from unittest import mock
from urllib.error import HTTPError

import pytest

import src.snowball as snowball
from src.db_schema import create_schema
from src.rate_limiter import RateLimiter
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
            # seed lookup + batch DOI resolution both return the citing works
            return _FakeResponse({"results": [citing1, citing2]})
        if "cites" in url:
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


# ---------------------------------------------------------------------------
# D4: assured retrieval — title fallback, multi-source retry, status accounting
# ---------------------------------------------------------------------------

def test_doi_less_reference_resolved_by_title(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "title.search" in url:
            return _FakeResponse({
                "results": [{
                    "id": "https://openalex.org/W900",
                    "doi": "10.9/T",
                    "title": "Some Paper Title",
                    "publication_year": 2021,
                    "authorships": [{"author": {"display_name": "T. Itled"}}],
                    "primary_location": {"source": {"display_name": "Journal"}},
                    "best_oa_location": {"pdf_url": "http://oa/t.pdf"},
                }]
            })
        raise AssertionError(f"unexpected urlopen URL: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', NULL, "
        "'Some Paper Title', 2021, 'crossref', 'pending')",
        (seed,),
    )

    stats = resolve_reference_lists(conn, source="crossref", assured=True)

    rows = conn.execute(
        "SELECT ref_doi, ref_title, resolved_paper_id, status FROM reference_lists"
    ).fetchall()
    row = rows[0]
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    # a paper was inserted for the DOI-less-but-titled reference
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
    # status_counts is present and consistent
    assert stats["status_counts"]["resolved"] == 1


def test_doid_reference_retries_alternate_source(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "works/10.6/d" in url:  # primary crossref -> 404
            raise HTTPError(url, 404, "not found", {}, None)
        if "filter=doi" in url:  # alternate openalex (filter=doi:) -> success
            return _FakeResponse({
                "results": [{
                    "id": "https://openalex.org/W6",
                    "doi": "10.6/D",
                    "title": "Paper D",
                    "publication_year": 2020,
                    "authorships": [],
                    "primary_location": {"source": {"display_name": "Journal"}},
                    "best_oa_location": {},
                }]
            })
        raise AssertionError(f"unexpected urlopen URL: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', '10.6/D', "
        "'Paper D', 2020, 'crossref', 'pending')",
        (seed,),
    )

    stats = resolve_reference_lists(conn, source="crossref", assured=True)

    row = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    # primary (crossref) 404 + alternate (openalex) 1 = 1 api call counted
    assert stats["api_calls"] == 1
    assert stats["new_papers"] == 1


def test_status_column_tracked(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "works/10.1/l" in url:  # the DOI ref resolves via crossref
            return _FakeResponse({
                "message": {
                    "DOI": "10.1/L",
                    "title": ["Local Paper"],
                    "author": [{"family": "L", "given": "M"}],
                    "published": {"date-parts": [[2020]]},
                }
            })
        if "title.search" in url or "query.bibliographic" in url:
            # the titled-but-unfindable ref has no confident match
            if "title.search" in url:
                return _FakeResponse({"results": []})
            return _FakeResponse({"message": {"items": []}})
        raise AssertionError(f"unexpected urlopen URL: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    # DOI ref -> should resolve
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', '10.1/L', "
        "'Local Paper', 2020, 'crossref', 'pending')",
        (seed,),
    )
    # DOI-less WITH title but no confident match -> unresolved_title_failed
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_unstructured, ref_year, source, status) VALUES "
        "(?, 'backward', NULL, 'Unfindable Title Here', 'Unfindable Title Here', "
        "2019, 'crossref', 'pending')",
        (seed,),
    )
    # DOI-less WITHOUT any title -> unresolved_no_doi
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_unstructured, ref_year, source, status) VALUES "
        "(?, 'backward', NULL, NULL, 'no-title-row', NULL, 'crossref', 'pending')",
        (seed,),
    )

    resolve_reference_lists(conn, source="crossref", assured=True)

    ALLOWED = {"pending", "resolved", "unresolved_no_doi",
               "unresolved_title_failed", "fetch_error"}
    rows = conn.execute(
        "SELECT ref_doi, ref_title, status FROM reference_lists"
    ).fetchall()
    assert len(rows) == 3  # nothing silently dropped
    statuses = {r["status"] for r in rows}
    assert statuses.issubset(ALLOWED)
    by_title = {r["ref_title"]: r["status"] for r in rows}
    assert by_title["Local Paper"] == "resolved"
    assert by_title["Unfindable Title Here"] == "unresolved_title_failed"
    assert by_title[None] == "unresolved_no_doi"


def test_verify_retrieval_backfills(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    target = _insert_paper(conn, "Target P", doi="10.7/V")

    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, source, status) VALUES (?, 'backward', '10.7/V', "
        "'Target P', 'crossref', 'pending')",
        (seed,),
    )
    # a second pending DOI'd ref that will NOT be found
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, source, status) VALUES (?, 'backward', '10.8/W', "
        "'Missing', 'crossref', 'pending')",
        (seed,),
    )

    result = reference_store.verify_retrieval(conn)

    row = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists WHERE ref_doi = '10.7/V'"
    ).fetchone()
    assert row["resolved_paper_id"] == target
    assert row["status"] == "resolved"
    assert result["backfilled"] == 1
    assert len(result["still_missing"]) == 1
    assert result["still_missing"][0]["ref_doi"] == "10.8/W"


def test_unresolved_exported(monkeypatch, tmp_path):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    resolved_pid = _insert_paper(conn, "Resolved P", doi="10.2/R")

    # one already-resolved row (must NOT appear in the export)
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, resolved_paper_id, status) VALUES "
        "(?, 'backward', '10.2/R', 'Resolved P', 2020, 'crossref', ?, 'resolved')",
        (seed, resolved_pid),
    )
    # two genuinely-unresolvable DOI-less rows (no title)
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_unstructured, source, status) VALUES "
        "(?, 'backward', NULL, NULL, 'u1', 'crossref', 'pending')",
        (seed,),
    )
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_unstructured, source, status) VALUES "
        "(?, 'backward', NULL, NULL, 'u2', 'crossref', 'pending')",
        (seed,),
    )

    out_csv = str(tmp_path / "unresolved.csv")
    resolve_reference_lists(conn, source="crossref", assured=True,
                            export_path=out_csv)

    import csv as _csv
    with open(out_csv, newline="", encoding="utf-8") as fh:
        reader = list(_csv.DictReader(fh))
    assert len(reader) == 2  # only the two unresolved rows
    assert all(r["status"] == "unresolved_no_doi" for r in reader)
    assert "ref_doi" in reader[0]
    assert "reason" in reader[0]


# ---------------------------------------------------------------------------
# D4 regression (BUG-003): graceful RateLimitError stop + local status write
# ---------------------------------------------------------------------------

def _rate_limited_urlopen(req, *a, **k):
    """Always answer with a retryable 429 so the limiter raises RateLimitError."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    raise HTTPError(url, 429, "too many requests", {}, None)


def _instant_limiter():
    """A limiter that gives up immediately and never actually sleeps."""
    return RateLimiter(min_interval=0.0, max_retries=1, max_wait=0.0,
                       backoff_base=1.0, jitter=0.0)


def _pending_doi_ref(conn, seed, doi, title):
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', ?, ?, "
        "2020, 'crossref', 'pending')",
        (seed, doi, title),
    )


def test_rate_limit_exports_unresolved_and_finishes_run(monkeypatch, tmp_path):
    """A RateLimitError during resolution must still write the CSV + finish the run.

    Regression for the ``export_unresolved`` parameter shadowing the module-level
    ``export_unresolved`` function (TypeError: 'str' object is not callable),
    which silently skipped the CSV and left ``snowball_runs.finished_at`` NULL.
    """
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    _pending_doi_ref(conn, seed, "10.2/X", "Paper X")
    _pending_doi_ref(conn, seed, "10.3/Y", "Paper Y")

    monkeypatch.setattr(snowball, "urlopen", _rate_limited_urlopen)
    monkeypatch.setattr(snowball.time, "sleep", lambda *_a, **_k: None)

    out_csv = tmp_path / "unresolved.csv"
    stats = resolve_reference_lists(
        conn, source="crossref", rate_limiter=_instant_limiter(),
        assured=True, export_path=str(out_csv),
    )

    assert stats["aborted"] == 1
    assert out_csv.exists()  # graceful stop still exported the unresolved rows
    import csv as _csv
    with open(out_csv, newline="", encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    assert {r["ref_doi"] for r in rows} == {"10.2/X", "10.3/Y"}
    assert stats["exported_unresolved"] == 2
    run = conn.execute(
        "SELECT finished_at FROM snowball_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["finished_at"] is not None  # _finish_run ran (no TypeError)


def test_harvest_rate_limit_exports_unresolved_and_finishes_run(monkeypatch, tmp_path):
    """Same graceful-stop guarantee on the harvest_references resolve tail."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")

    monkeypatch.setattr(
        reference_store,
        "_fetch_seed_reference_list",
        lambda *a, **k: (
            [{"doi": "10.2/X", "title": "Paper X", "year": 2019, "authors": "",
              "unstructured": ""}],
            0,
        ),
    )
    monkeypatch.setattr(snowball, "urlopen", _rate_limited_urlopen)
    monkeypatch.setattr(snowball.time, "sleep", lambda *_a, **_k: None)

    out_csv = tmp_path / "unresolved_harvest.csv"
    stats = harvest_references(
        conn, [seed], direction="backward", source="crossref",
        rate_limiter=_instant_limiter(), assured=True, export_path=str(out_csv),
    )

    assert stats["aborted"] == 1
    assert out_csv.exists()
    import csv as _csv
    with open(out_csv, newline="", encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    assert [r["ref_doi"] for r in rows] == ["10.2/x"]
    run = conn.execute(
        "SELECT finished_at FROM snowball_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["finished_at"] is not None


def test_local_resolution_sets_status_resolved_without_resolve_phase(monkeypatch):
    """A locally-resolved reference is 'resolved' at link time, not left 'pending'.

    ``resolve=False`` (``--harvest-only``) never runs ``_sync_resolved_status``,
    so the status must be written by the local-first link itself.
    """
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    ref = _insert_paper(conn, "Ref R", doi="10.2/R")

    fake_urlopen = mock.Mock()
    monkeypatch.setattr(snowball, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        reference_store,
        "_fetch_seed_reference_list",
        lambda *a, **k: (
            [{"doi": "10.2/R", "title": "Ref R", "year": 2020, "authors": "X",
              "unstructured": ""}],
            0,
        ),
    )

    harvest_references(
        conn, [seed], direction="backward", source="crossref", resolve=False
    )

    assert fake_urlopen.call_count == 0
    row = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists"
    ).fetchone()
    assert row["resolved_paper_id"] == ref
    assert row["status"] == "resolved"
    # re-harvesting the same reference stays resolved (idempotent link)
    harvest_references(
        conn, [seed], direction="backward", source="crossref", resolve=False
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM reference_lists WHERE status = 'resolved'"
    ).fetchone()[0] == 1
