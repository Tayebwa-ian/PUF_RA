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
from src.rate_limiter import RateLimiter, RateLimitError, DEFAULT_SOURCE_INTERVALS
from src import reference_store, zotero_sync
from src.reference_store import (
    harvest_references,
    resolve_reference_lists,
    get_reference_inventory,
    local_find_paper,
    backfill_abstracts,
    snowball_coverage,
    _openalex_batch_by_dois,
    _normalise_crossref_item,
    _normalise_openalex_work,
    _normalise_s2_reference,
    _find_or_create_ref_paper,
)
from src.snowball import _ensure_source_snowball, normalise_doi


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
    """A fully rate-limited resolve phase keeps going (cross-source fallback) and
    still writes the CSV + finishes the run (no graceful abort).

    Regression for the ``export_unresolved`` parameter shadowing the module-level
    ``export_unresolved`` function (TypeError: 'str' object is not callable),
    which silently skipped the CSV and left ``snowball_runs.finished_at`` NULL. With
    TASK-010 the batch no longer aborts on HTTP 429: every platform is tried, the
    affected DOIs become ``fetch_error`` and the run completes (``aborted == 0``).
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

    assert stats["aborted"] == 0  # cross-source fallback: run continues, never aborts
    assert out_csv.exists()  # run still exported the unresolved rows
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
    """harvest_references keeps going when the resolve tail is fully rate-limited
    (TASK-010 cross-source fallback): the run finishes and still exports the CSV,
    instead of aborting."""
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

    assert stats["aborted"] == 0  # resolve tail continues via cross-source fallback
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


# ---------------------------------------------------------------------------
# Phase 0: abstract capture for snowball-resolved papers
# ---------------------------------------------------------------------------

def test_normalisers_capture_abstract():
    crossref = _normalise_crossref_item(
        {"DOI": "10.1/x", "title": ["X"], "abstract": "<jats:p>Hello <i>world</i></jats:p>"}
    )
    assert crossref["abstract"] == "Hello world"

    openalex = _normalise_openalex_work(
        {"title": "T", "abstract_inverted_index": {"physical": [0], "attack": [1]}}
    )
    assert openalex["abstract"] == "physical attack"

    s2 = _normalise_s2_reference({"title": "T", "abstract": "Plain abstract text"})
    assert s2["abstract"] == "Plain abstract text"


def test_find_or_create_ref_paper_stores_abstract():
    conn = _memory_db()
    source_id = _ensure_source_snowball(conn)
    ref = {
        "doi": "10.5/r",
        "title": "Ref R",
        "authors": "A B",
        "year": 2021,
        "abstract": "Captured abstract text",
        "publication_title": "V",
    }
    pid, is_new = _find_or_create_ref_paper(conn, ref, source_id, None)
    assert is_new is True
    stored = conn.execute("SELECT abstract FROM papers WHERE id=?", (pid,)).fetchone()[0]
    assert stored == "Captured abstract text"


def test_find_or_create_ref_paper_backfills_existing_abstract():
    """Local-found shortcut must NOT drop the freshly resolved abstract."""
    conn = _memory_db()
    source_id = _ensure_source_snowball(conn)
    # paper already in the corpus (e.g. imported from query1) with NO abstract
    existing = _insert_paper(conn, "Known Paper", doi="10.5/known", authors="")
    ref = {
        "doi": "10.5/known",
        "title": "Known Paper",
        "authors": "C. Resolver",
        "year": 2019,
        "abstract": "Resolved abstract text",
        "publication_title": "V",
    }
    pid, is_new = _find_or_create_ref_paper(conn, ref, source_id, None)
    assert pid == existing and is_new is False
    row = conn.execute(
        "SELECT abstract, authors FROM papers WHERE id=?", (existing,)
    ).fetchone()
    assert row["abstract"] == "Resolved abstract text"
    assert row["authors"] == "C. Resolver"  # other empty fields filled too

    # a stored abstract is never overwritten by a later resolution
    ref2 = dict(ref, abstract="Different abstract")
    pid2, is_new2 = _find_or_create_ref_paper(conn, ref2, source_id, None)
    assert pid2 == existing and is_new2 is False
    assert conn.execute(
        "SELECT abstract FROM papers WHERE id=?", (existing,)
    ).fetchone()[0] == "Resolved abstract text"


def test_backfill_abstracts_crossref(monkeypatch):
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper Z", doi="10.9/z")  # abstract defaults to ""

    def fake_get_json(url, rate_limiter=None, retries=None, timeout=15.0, source=None):
        return {
            "message": {
                "DOI": "10.9/z",
                "title": ["Paper Z"],
                "abstract": "<p>Real <b>abstract</b>.</p>",
            }
        }

    monkeypatch.setattr(reference_store, "_get_json", fake_get_json)

    assert backfill_abstracts(conn, source="crossref") == 1
    stored = conn.execute("SELECT abstract FROM papers WHERE id=?", (pid,)).fetchone()[0]
    assert stored == "Real abstract."

    # idempotent: a second run finds no empty abstracts
    assert backfill_abstracts(conn, source="crossref") == 0


def test_backfill_abstracts_openalex_source(monkeypatch):
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper Q", doi="10.8/q")

    def fake_openalex_filter(filter_value, mailto, limiter):
        return [{"abstract_inverted_index": {"neural": [0], "puf": [1]}}], 1

    monkeypatch.setattr(reference_store, "_openalex_filter", fake_openalex_filter)

    assert backfill_abstracts(conn, source="openalex") == 1
    stored = conn.execute("SELECT abstract FROM papers WHERE id=?", (pid,)).fetchone()[0]
    assert stored == "neural puf"


def test_backfill_abstracts_semantic_scholar(monkeypatch):
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper S", doi="10.7/s")

    def fake_get_json(url, rate_limiter=None, retries=None, timeout=15.0, source=None):
        assert "DOI:10.7/s" in url  # routed to the S2 paper endpoint
        return {
            "title": "Paper S",
            "abstract": "Semantic Scholar abstract text.",
            "externalIds": {"DOI": "10.7/S"},
        }

    monkeypatch.setattr(reference_store, "_get_json", fake_get_json)

    assert backfill_abstracts(conn, source="semantic_scholar") == 1
    stored = conn.execute("SELECT abstract FROM papers WHERE id=?", (pid,)).fetchone()[0]
    assert stored == "Semantic Scholar abstract text."

    # alias 's2' is accepted and behaves identically
    monkeypatch.setattr(
        reference_store,
        "_get_json",
        lambda *a, **k: {"title": "T", "abstract": "second", "externalIds": {}},
    )
    monkeypatch.setattr(
        reference_store, "_openalex_resolve_dois", lambda *a, **k: ([], 0)
    )
    conn.execute(
        "INSERT INTO papers (title, authors, year, abstract, publication_title, doi) "
        "VALUES ('Paper S2', 'X', 2020, '', 'V', '10.7/s2')"
    )
    assert backfill_abstracts(conn, source="s2") == 1


def test_backfill_abstracts_s2_no_openalex_fallback(monkeypatch):
    conn = _memory_db()
    _insert_paper(conn, "Paper M", doi="10.6/m")

    # S2 returns no abstract; OpenAlex must NOT be contacted (S2 is standalone).
    monkeypatch.setattr(
        reference_store, "_get_json",
        lambda *a, **k: {"title": "T", "abstract": None, "externalIds": {}},
    )
    openalex_calls = {"n": 0}

    def boom(*a, **k):
        openalex_calls["n"] += 1
        raise AssertionError("S2 backfill must NOT fall back to OpenAlex")

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_openalex_filter", boom)

    assert backfill_abstracts(conn, source="semantic_scholar") == 0
    assert openalex_calls["n"] == 0

    # --no-alternate on crossref also suppresses the OpenAlex retry.
    conn2 = _memory_db()
    _insert_paper(conn2, "Paper C", doi="10.7/c")
    monkeypatch.setattr(
        reference_store, "_get_json",
        lambda *a, **k: {"message": {"DOI": "10.7/c", "title": ["C"]}},  # no abstract
    )
    assert backfill_abstracts(conn2, source="crossref", no_alternate=True) == 0
    assert openalex_calls["n"] == 0


def test_backfill_abstracts_respects_api_budget(monkeypatch):
    conn = _memory_db()
    _insert_paper(conn, "Paper Z", doi="10.9/z")

    def fake_get_json(url, rate_limiter=None, retries=None, timeout=15.0, source=None):
        return {"message": {"DOI": "10.9/z", "title": ["Z"], "abstract": "<p>x</p>"}}

    monkeypatch.setattr(reference_store, "_get_json", fake_get_json)

    # budget 0 -> no calls, nothing updated
    assert backfill_abstracts(conn, source="crossref", max_api_calls=0) == 0


# ---------------------------------------------------------------------------
# Path B: Semantic Scholar as a first-class RESOLVE source
# ---------------------------------------------------------------------------

def test_s2_resolve_standalone(monkeypatch):
    """A DOI reference resolves through S2 alone; OpenAlex is never contacted."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', '10.2/X', "
        "'Paper X', 2019, 'semantic_scholar', 'pending')",
        (seed,),
    )

    def fake_s2(doi, mailto, limiter):
        return {
            "doi": "10.2/x", "title": "Paper X", "authors": "Y. Z",
            "year": 2019, "abstract": "S2 abstract text.",
            "publication_title": "V", "unstructured": "", "pdf_url": None,
        }

    monkeypatch.setattr(reference_store, "_s2_get_work", fake_s2)

    def boom(*a, **k):
        raise AssertionError("OpenAlex must NOT be contacted for S2 resolve")

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)

    stats = resolve_reference_lists(conn, source="semantic_scholar", assured=False)

    row = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    paper = conn.execute(
        "SELECT title, authors, year, abstract FROM papers WHERE doi='10.2/x'"
    ).fetchone()
    assert paper["title"] == "Paper X"
    assert paper["authors"] == "Y. Z"
    assert paper["year"] == 2019
    assert paper["abstract"] == "S2 abstract text."
    assert stats["new_papers"] == 1


def test_no_alternate_no_openalex_fallback(monkeypatch):
    """--no-alternate: a Crossref miss is NOT retried on OpenAlex."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', '10.6/D', "
        "'Paper D', 2020, 'crossref', 'pending')",
        (seed,),
    )

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "works/10.6/d" in url:  # primary crossref -> 404
            raise HTTPError(url, 404, "not found", {}, None)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    def boom(*a, **k):
        raise AssertionError("OpenAlex must NOT be contacted when --no-alternate")

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)

    stats = resolve_reference_lists(
        conn, source="crossref", no_alternate=True, assured=False
    )

    row = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists"
    ).fetchone()
    assert row["status"] == "fetch_error"
    assert row["resolved_paper_id"] is None


class _RecordingLimiter:
    """A stand-in limiter that records wait_before_call invocations."""

    def __init__(self):
        self.calls = 0
        self.max_retries = 5

    def wait_before_call(self, source=None):
        self.calls += 1

    def backoff_seconds(self, attempt, retry_after_header=None):
        return 0.0

    def note_success(self):
        pass

    def note_failure(self):
        pass


def test_passed_limiter_is_used_not_default(monkeypatch):
    """The supplied limiter is used; no default limiter is constructed."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "ref_title, ref_year, source, status) VALUES (?, 'backward', '10.2/X', "
        "'Paper X', 2019, 'crossref', 'pending')",
        (seed,),
    )

    constructed = []
    orig_rl = reference_store.RateLimiter

    def spy(*a, **k):
        constructed.append((a, k))
        return orig_rl(*a, **k)

    monkeypatch.setattr(reference_store, "RateLimiter", spy)

    limiter = _RecordingLimiter()

    def fake(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "works/10.2/x" in url:
            return _FakeResponse({
                "message": {
                    "DOI": "10.2/X", "title": ["Paper X"],
                    "author": [{"family": "Y", "given": "Z"}],
                    "published": {"date-parts": [[2019]]},
                }
            })
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(snowball, "urlopen", fake)

    resolve_reference_lists(conn, source="crossref", rate_limiter=limiter)

    assert constructed == []  # no default limiter when one is supplied
    assert limiter.calls >= 1  # the supplied limiter was actually paced


def test_cli_run_passes_delay_to_resolve_limiter(monkeypatch):
    """CLI builds RateLimiter(min_interval=delay) and passes it to resolve."""
    import src.db as db_mod
    from cli import snowball as cli_snowball

    captured = {}
    monkeypatch.setattr(db_mod, "get_connection", lambda *a, **k: _memory_db())
    monkeypatch.setattr(reference_store, "harvest_references", lambda *a, **k: {})

    def fake_resolve(conn, **kwargs):
        captured["rate_limiter"] = kwargs.get("rate_limiter")
        captured["no_alternate"] = kwargs.get("no_alternate")
        captured["source"] = kwargs.get("source")
        return {}

    monkeypatch.setattr(reference_store, "resolve_reference_lists", fake_resolve)

    cli_snowball.main(
        ["run", "--resolve-only", "--source", "semantic_scholar",
         "--delay", "2.5", "--no-alternate"]
    )

    assert captured["rate_limiter"] is not None
    assert abs(captured["rate_limiter"].min_interval - 2.5) < 1e-9
    assert captured["no_alternate"] is True
    assert captured["source"] == "semantic_scholar"


def test_backfill_resilient_to_rate_limit(monkeypatch):
    """A throttled S2 fetch for one paper does not abort the batch; commits per paper."""
    conn = _memory_db()
    _insert_paper(conn, "Paper1", doi="10.1/a")
    _insert_paper(conn, "Paper2", doi="10.2/b")

    def fake_get_json(url, rate_limiter=None, retries=None, timeout=15.0, source=None):
        if "10.1/a" in url:
            raise RateLimitError("throttled")
        return {"title": "T", "abstract": "got it", "externalIds": {"DOI": "10.2/b"}}

    monkeypatch.setattr(reference_store, "_get_json", fake_get_json)

    updated = backfill_abstracts(conn, source="semantic_scholar")

    # p1 throttled -> skipped; p2 processed; batch not aborted.
    assert updated == 1
    stored = conn.execute(
        "SELECT abstract FROM papers WHERE doi='10.2/b'"
    ).fetchone()[0]
    assert stored == "got it"


def test_cli_run_passes_delay_and_no_alternate_to_harvest(monkeypatch):
    """`run --source semantic_scholar` (no --resolve-only) threads --delay and
    --no-alternate into harvest_references on the new two-phase path."""
    import src.db as db_mod
    from cli import snowball as cli_snowball

    captured = {}
    monkeypatch.setattr(db_mod, "get_connection", lambda *a, **k: _memory_db())

    def fake_harvest(*args, **kwargs):
        captured["rate_limiter"] = kwargs.get("rate_limiter")
        captured["no_alternate"] = kwargs.get("no_alternate")
        captured["source"] = kwargs.get("source")
        return {}

    monkeypatch.setattr(reference_store, "harvest_references", fake_harvest)
    # resolve_reference_lists must not be reached; short-circuit regardless.
    monkeypatch.setattr(reference_store, "resolve_reference_lists", lambda *a, **k: {})

    cli_snowball.main(
        ["run", "--source", "semantic_scholar", "--delay", "2.5", "--no-alternate"]
    )

    assert captured["rate_limiter"] is not None
    assert abs(captured["rate_limiter"].min_interval - 2.5) < 1e-9
    assert captured["no_alternate"] is True
    assert captured["source"] == "semantic_scholar"


def test_cli_run_maps_s2_source_to_semantic_scholar(monkeypatch):
    """The CLI `_legacy_to_new_source` s2->semantic_scholar mapping reaches the
    resolve call."""
    import src.db as db_mod
    from cli import snowball as cli_snowball

    captured = {}
    monkeypatch.setattr(db_mod, "get_connection", lambda *a, **k: _memory_db())

    def fake_resolve(conn, **kwargs):
        captured["source"] = kwargs.get("source")
        return {}

    monkeypatch.setattr(reference_store, "resolve_reference_lists", fake_resolve)
    monkeypatch.setattr(reference_store, "harvest_references", lambda *a, **k: {})

    cli_snowball.main(
        ["run", "--source", "s2", "--resolve-only", "--delay", "1.0"]
    )

    assert captured["source"] == "semantic_scholar"


# ---------------------------------------------------------------------------
# TASK-010: cross-source fallback on rate-limit (HTTP 429)
# ---------------------------------------------------------------------------

def test_resolve_falls_back_on_ratelimit_crossref(monkeypatch):
    """A rate-limited Crossref resolves via OpenAlex instead of aborting."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'crossref')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("crossref throttled")

    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", boom)
    # keep the OpenAlex batch pre-pass hermetic + a no-op in this unit test
    monkeypatch.setattr(reference_store, "_openalex_filter", lambda *a, **k: ([], 0))

    def fake_oa(dois, mailto, limiter):
        return [{
            "doi": "10.2/x", "title": "Paper X via OpenAlex", "authors": "Y. Z",
            "year": 2019, "publication_title": "V", "pdf_url": None,
        }], 1

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", fake_oa)

    stats = resolve_reference_lists(conn, source="crossref", assured=False)

    row = conn.execute(
        "SELECT status, resolved_paper_id FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    paper = conn.execute(
        "SELECT title, doi FROM papers WHERE id=?", (row["resolved_paper_id"],)
    ).fetchone()
    assert paper["title"] == "Paper X via OpenAlex"
    assert paper["doi"] == "10.2/x"
    assert stats["aborted"] == 0


def test_resolve_falls_back_on_ratelimit_s2(monkeypatch):
    """A rate-limited Semantic Scholar resolves via OpenAlex instead of aborting."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'semantic_scholar')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("s2 throttled")

    monkeypatch.setattr(reference_store, "_s2_resolve_dois", boom)

    def fake_oa(dois, mailto, limiter):
        return [{
            "doi": "10.2/x", "title": "Paper X via OpenAlex", "authors": "Y. Z",
            "year": 2019, "publication_title": "V", "pdf_url": None,
        }], 1

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", fake_oa)

    stats = resolve_reference_lists(conn, source="semantic_scholar", assured=False)

    row = conn.execute(
        "SELECT status, resolved_paper_id FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    paper = conn.execute(
        "SELECT title FROM papers WHERE id=?", (row["resolved_paper_id"],)
    ).fetchone()
    assert paper["title"] == "Paper X via OpenAlex"
    assert stats["aborted"] == 0


def test_no_alternate_ratelimit_does_not_abort(monkeypatch):
    """--no-alternate: a rate-limited source marks fetch_error and the run continues."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'semantic_scholar')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("s2 throttled")

    monkeypatch.setattr(reference_store, "_s2_resolve_dois", boom)

    stats = resolve_reference_lists(
        conn, source="semantic_scholar", no_alternate=True, assured=False
    )

    # Returns normally: no exception escapes, the ref is fetch_error (not pending),
    # and the run is NOT counted as aborted.
    row = conn.execute("SELECT status FROM reference_lists").fetchone()
    assert row["status"] == "fetch_error"
    assert stats["aborted"] != 1


def test_backfill_falls_back_on_ratelimit_s2(monkeypatch):
    """A rate-limited S2 abstract fetch falls back to OpenAlex during backfill."""
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper S", doi="10.7/s")  # abstract defaults to ""

    def boom(*a, **k):
        raise RateLimitError("s2 throttled")

    monkeypatch.setattr(reference_store, "_fetch_abstract_s2", boom)

    def fake_oa(doi, mailto, limiter):
        return "OpenAlex abstract text."

    monkeypatch.setattr(reference_store, "_fetch_abstract_openalex", fake_oa)

    updated = backfill_abstracts(conn, source="semantic_scholar")

    assert updated == 1  # did not abort; abstract filled from OpenAlex
    stored = conn.execute(
        "SELECT abstract FROM papers WHERE id=?", (pid,)
    ).fetchone()[0]
    assert stored == "OpenAlex abstract text."


def test_resolve_phase_completes_on_ratelimit(monkeypatch):
    """When every source is throttled, the resolve phase completes (no abort)."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'crossref')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("throttled")

    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_s2_resolve_dois", boom)
    # keep the OpenAlex batch pre-pass hermetic + a no-op in this unit test
    monkeypatch.setattr(reference_store, "_openalex_filter", lambda *a, **k: ([], 0))

    stats = resolve_reference_lists(conn, source="crossref", assured=False)
    assert stats["aborted"] == 0
    row = conn.execute("SELECT status FROM reference_lists").fetchone()
    assert row["status"] == "fetch_error"


# ---------------------------------------------------------------------------
# Zotero local-library fallback (rate-limit immune, last resort)
# ---------------------------------------------------------------------------

def test_zotero_fallback_on_ratelimit(monkeypatch):
    """When every external API is throttled, Zotero resolves the DOI last."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'semantic_scholar')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("api throttled")

    monkeypatch.setattr(reference_store, "_s2_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", boom)

    def fake_zotero_lookup(doi):
        return {
            "doi": "10.2/x",
            "title": "Paper X via Zotero",
            "authors": "Z. Zotero",
            "year": 2021,
            "abstract": "Zotero cached abstract.",
        }

    monkeypatch.setattr(zotero_sync, "lookup_doi_in_zotero", fake_zotero_lookup)

    stats = resolve_reference_lists(conn, source="semantic_scholar", assured=False)

    row = conn.execute(
        "SELECT status, resolved_paper_id FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    paper = conn.execute(
        "SELECT title, doi FROM papers WHERE id=?", (row["resolved_paper_id"],)
    ).fetchone()
    assert paper["title"] == "Paper X via Zotero"
    assert paper["doi"] == "10.2/x"
    assert stats["aborted"] == 0


def test_zotero_skipped_when_unconfigured(monkeypatch):
    """No Zotero + all APIs throttled -> ref is fetch_error, run still completes."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'semantic_scholar')",
        (seed,),
    )

    def boom(*a, **k):
        raise RateLimitError("api throttled")

    monkeypatch.setattr(reference_store, "_s2_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", boom)
    monkeypatch.setattr(zotero_sync, "lookup_doi_in_zotero", lambda doi: None)

    stats = resolve_reference_lists(conn, source="semantic_scholar", assured=False)

    row = conn.execute("SELECT status FROM reference_lists").fetchone()
    assert row["status"] == "fetch_error"
    assert stats["aborted"] != 1


def test_backfill_falls_back_to_zotero(monkeypatch):
    """A rate-limited abstract fetch falls back to Zotero during backfill."""
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper Z", doi="10.7/z")  # abstract defaults to ""

    def boom(*a, **k):
        raise RateLimitError("api throttled")

    monkeypatch.setattr(reference_store, "_fetch_abstract_crossref", boom)
    monkeypatch.setattr(reference_store, "_fetch_abstract_openalex", boom)
    monkeypatch.setattr(reference_store, "_fetch_abstract_s2", boom)
    monkeypatch.setattr(
        zotero_sync, "fetch_abstract_via_zotero",
        lambda doi, mailto=None, limiter=None: "Zotero abstract text.",
    )
    # keep the OpenAlex batch pre-pass hermetic + a no-op in this unit test
    monkeypatch.setattr(reference_store, "_openalex_filter", lambda *a, **k: ([], 0))

    updated = backfill_abstracts(conn, source="crossref")

    assert updated == 1
    stored = conn.execute(
        "SELECT abstract FROM papers WHERE id=?", (pid,)
    ).fetchone()[0]
    assert stored == "Zotero abstract text."


def test_lookup_doi_in_zotero_direct():
    """lookup_doi_in_zotero extracts metadata from a fake Zotero client."""

    class _FakeZotero:
        def items(self, query, qmode="everything"):
            return [{
                "data": {
                    "DOI": "10.1234/direct",
                    "title": "Direct Zotero Paper",
                    "creators": [
                        {"creatorType": "author", "given": "Ada", "family": "Lovelace"},
                        {"creatorType": "author", "given": "Alan", "family": "Turing"},
                    ],
                    "date": "2019-05-01",
                    "abstractNote": "A directly fetched abstract.",
                }
            }]

    result = zotero_sync.lookup_doi_in_zotero("10.1234/direct", zotero=_FakeZotero())
    assert result is not None
    assert result["doi"] == "10.1234/direct"
    assert result["title"] == "Direct Zotero Paper"
    assert result["authors"] == "Ada Lovelace; Alan Turing"
    assert result["year"] == 2019
    assert result["abstract"] == "A directly fetched abstract."


# ---------------------------------------------------------------------------
# TASK-013: source-aware pacing + OpenAlex batched multi-DOI lookup
# ---------------------------------------------------------------------------

def test_openalex_batch_backfill_fills_abstracts(monkeypatch):
    """Batched OpenAlex lookup fills most abstracts in ceil(n/50) HTTP calls.

    Proves batching (not per-DOI): the underlying ``_openalex_filter`` is hit once
    per 50 DOIs, and ``backfill_abstracts`` fills every paper via the pre-pass.
    """
    conn = _memory_db()
    n = 120
    for i in range(n):
        _insert_paper(conn, f"P{i}", doi=f"10.{i}/p{i}")

    filter_calls = {"n": 0}

    def fake_openalex_filter(filter_value, mailto, limiter):
        filter_calls["n"] += 1
        # the batch passes "doi:d1|d2|..."; one work per doi in the chunk
        chunk = filter_value.split("doi:")[1].split("|")
        works = [
            {
                "doi": dd,
                "title": "T",
                "authors": "",
                "year": 2020,
                "publication_title": "",
                "pdf_url": None,
                "abstract_inverted_index": {"abs": [0], "tract": [1]},
            }
            for dd in chunk
        ]
        return works, 1

    monkeypatch.setattr(reference_store, "_openalex_filter", fake_openalex_filter)

    updated = backfill_abstracts(conn, source="openalex")

    assert updated == n
    assert filter_calls["n"] == (n + 49) // 50  # ceil(n/50)
    # every paper now has the reconstructed abstract
    empty = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE abstract IS NULL OR abstract = ''"
    ).fetchone()[0]
    assert empty == 0


def test_per_source_pacing(monkeypatch):
    """Source-aware pacing: OpenAlex/Crossref ~0.05s, S2 ~0.6s, default 1.0s."""
    import src.rate_limiter as rl

    sleeps = []
    monkeypatch.setattr(rl.time, "sleep", lambda s: sleeps.append(s))
    # freeze the monotonic clock so each call is "immediate" -> sleeps the interval
    monkeypatch.setattr(rl.time, "monotonic", lambda: 0.0)

    limiter = RateLimiter(
        min_interval=1.0,
        per_source_intervals={"crossref": 0.05, "openalex": 0.05, "semantic_scholar": 0.6},
    )
    limiter.wait_before_call(source="crossref")   # prime: no sleep (no prior call)
    limiter.wait_before_call(source="crossref")   # 0.05 (fast source)
    limiter.wait_before_call(source="semantic_scholar")  # 0.6 (slow source)
    limiter.wait_before_call()                    # default min_interval 1.0

    # the prime call sleeps nothing; the three subsequent calls pace per source
    assert sleeps == [0.05, 0.6, 1.0]


def test_batch_resolve_prefers_openalex(monkeypatch):
    """The OpenAlex batch pre-pass resolves DOIs without the per-DOI Crossref call."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'openalex')", (seed,),
    )
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.3/Y', 'openalex')", (seed,),
    )

    def fake_batch(dois, mailto, limiter):
        works = []
        for d in dois:
            nd = normalise_doi(d)
            works.append({
                "doi": nd, "title": f"Paper {nd}", "authors": "A. U.",
                "year": 2021, "publication_title": "V", "pdf_url": None,
                "abstract": "",
            })
        return works, 1

    monkeypatch.setattr(reference_store, "_openalex_batch_by_dois", fake_batch)

    crossref_calls = {"n": 0}
    orig = reference_store._crossref_resolve_dois

    def spy(dois, mailto, limiter):
        crossref_calls["n"] += 1
        return orig(dois, mailto, limiter)

    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", spy)

    stats = resolve_reference_lists(conn, source="openalex", assured=False)

    # Crossref was never contacted for these DOIs -- the batch pre-pass handled them.
    assert crossref_calls["n"] == 0
    rows = conn.execute(
        "SELECT resolved_paper_id, status FROM reference_lists"
    ).fetchall()
    assert all(r["status"] == "resolved" for r in rows)
    assert stats["new_papers"] == 2
    assert stats["edges"] == 2


def test_coverage_report(monkeypatch):
    """snowball_coverage counts totals and resolved-but-missing metadata."""
    conn = _memory_db()
    _insert_paper(conn, "Has Both", doi="10.1/a", authors="X")
    # give it a real abstract so it is NOT counted as missing
    conn.execute(
        "UPDATE papers SET abstract = 'complete abstract' WHERE doi = '10.1/a'"
    )
    _insert_paper(conn, "No Abstract", doi="10.2/b", authors="Y")  # abstract default ""
    target = _insert_paper(conn, "Resolved Missing Abs", doi="10.3/c", authors="Z")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "resolved_paper_id, status) VALUES (?, 'backward', '10.3/c', ?, 'resolved')",
        (target, target),
    )

    cov = snowball_coverage(conn)

    assert cov["papers_total"] == 3
    assert cov["papers_missing_abstract"] == 2  # No Abstract + Resolved Missing Abs
    assert cov["papers_missing_title"] == 0
    assert cov["resolved_missing_abstract"] == 1
    assert cov["resolved_missing_title"] == 0


def test_backfill_falls_back_when_batch_throttled(monkeypatch):
    """A throttled OpenAlex batch defers to the per-DOI chain (no crash)."""
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper Z", doi="10.9/z")  # abstract defaults to ""

    def boom(dois, mailto, limiter):
        raise RateLimitError("openalex batch throttled")

    monkeypatch.setattr(reference_store, "_openalex_batch_by_dois", boom)
    monkeypatch.setattr(
        reference_store, "_fetch_abstract_crossref",
        lambda doi, mailto, limiter: "Recovered abstract text.",
    )

    updated = backfill_abstracts(conn, source="crossref")

    assert updated == 1
    stored = conn.execute(
        "SELECT abstract FROM papers WHERE id=?", (pid,)
    ).fetchone()[0]
    assert stored == "Recovered abstract text."



def test_no_batch_skips_openalex_prepass(monkeypatch):
    """--no-batch (use_batch=False): the OpenAlex batched pre-pass is skipped
    entirely and a per-DOI Crossref fetch still fills the abstract.

    Mirrors ``test_backfill_falls_back_when_batch_throttled`` but proves the
    pre-pass is NOT even attempted when ``use_batch`` is False.
    """
    conn = _memory_db()
    pid = _insert_paper(conn, "Paper Z", doi="10.9/z")  # abstract defaults to ""

    batch_calls = {"n": 0}

    def boom(dois, mailto, limiter):
        batch_calls["n"] += 1
        raise AssertionError("OpenAlex batch pre-pass must NOT run when use_batch=False")

    monkeypatch.setattr(reference_store, "_openalex_batch_by_dois", boom)
    monkeypatch.setattr(
        reference_store, "_fetch_abstract_crossref",
        lambda doi, mailto, limiter: "Per-DOI abstract text.",
    )

    updated = backfill_abstracts(conn, source="crossref", use_batch=False)

    assert batch_calls["n"] == 0  # pre-pass never invoked
    assert updated == 1
    stored = conn.execute(
        "SELECT abstract FROM papers WHERE id=?", (pid,)
    ).fetchone()[0]
    assert stored == "Per-DOI abstract text."


def test_stats_coverage_no_closed_db():
    """snowball_coverage runs against a live in-memory connection and returns
    the expected keys (guards the stats subcommand closed-db regression)."""
    conn = _memory_db()
    _insert_paper(conn, "Has Abstract", doi="10.1/a", authors="X")
    conn.execute("UPDATE papers SET abstract = 'complete' WHERE doi = '10.1/a'")
    _insert_paper(conn, "No Abstract", doi="10.2/b", authors="Y")
    target = _insert_paper(conn, "Resolved Missing Abs", doi="10.3/c", authors="Z")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, "
        "resolved_paper_id, status) VALUES (?, 'backward', '10.3/c', ?, 'resolved')",
        (target, target),
    )

    cov = snowball_coverage(conn)

    assert set(cov.keys()) == {
        "papers_total", "papers_missing_abstract", "papers_missing_title",
        "resolved_missing_abstract", "resolved_missing_title",
    }
    assert cov["papers_total"] == 3
    assert cov["papers_missing_abstract"] == 2  # No Abstract + Resolved Missing Abs
    assert cov["resolved_missing_abstract"] == 1
    assert cov["papers_missing_title"] == 0
    assert cov["resolved_missing_title"] == 0


# ---------------------------------------------------------------------------
# TASK-014: Zotero-before-S2 ordering + batched Zotero lookup pre-passes
# ---------------------------------------------------------------------------

def test_chain_zotero_before_s2():
    """Zotero now precedes Semantic Scholar in the fallback chains (faster,
    rate-limit-immune local lookup before the slow S2 endpoint)."""
    for source in ("crossref", "openalex"):
        chain = reference_store._SOURCE_RATELIMIT_CHAIN[source]
        assert chain.index("zotero") < chain.index("semantic_scholar")


def test_zotero_batch_prepass_fills(monkeypatch):
    """The Zotero batch pre-pass links DOIs from the local library with NO S2 call."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'semantic_scholar')", (seed,),
    )

    # Bulk Zotero lookup returns the cached work; S2 must NOT be contacted.
    def fake_zotero_batch(dois):
        return [{
            "doi": "10.2/x", "title": "Paper X via Zotero", "authors": "Z. Zotero",
            "year": 2021, "abstract": "Zotero cached abstract.",
            "publication_title": "", "unstructured": "", "pdf_url": None,
        }]

    monkeypatch.setattr(zotero_sync, "lookup_doi_in_zotero_batch", fake_zotero_batch)

    # If any external resolver were reached, the test would crash loudly.
    def boom(*a, **k):
        raise AssertionError("no external resolver should be contacted")
    monkeypatch.setattr(reference_store, "_s2_get_work", boom)
    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", boom)
    monkeypatch.setattr(reference_store, "_crossref_resolve_dois", boom)

    stats = resolve_reference_lists(conn, source="semantic_scholar", assured=False)

    row = conn.execute(
        "SELECT status, resolved_paper_id FROM reference_lists"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_paper_id"] is not None
    paper = conn.execute(
        "SELECT title, doi FROM papers WHERE id=?", (row["resolved_paper_id"],)
    ).fetchone()
    assert paper["title"] == "Paper X via Zotero"
    assert paper["doi"] == "10.2/x"
    assert stats["aborted"] == 0


def test_no_batch_disables_both_prepasses(monkeypatch):
    """--no-batch: neither the OpenAlex nor the Zotero batch pre-pass runs, yet a
    per-DOI resolve still fills the DOI."""
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed A", doi="10.1/A")
    conn.execute(
        "INSERT INTO reference_lists (parent_paper_id, direction, ref_doi, source) "
        "VALUES (?, 'backward', '10.2/X', 'openalex')", (seed,),
    )

    batch_calls = {"openalex": 0, "zotero": 0}

    def boom_openalex(*a, **k):
        batch_calls["openalex"] += 1
        raise AssertionError("OpenAlex batch pre-pass must NOT run with --no-batch")

    def boom_zotero(*a, **k):
        batch_calls["zotero"] += 1
        raise AssertionError("Zotero batch pre-pass must NOT run with --no-batch")

    monkeypatch.setattr(reference_store, "_openalex_batch_by_dois", boom_openalex)
    monkeypatch.setattr(reference_store, "_zotero_batch_by_dois", boom_zotero)

    # per-DOI OpenAlex (first in chain) fills the DOI.
    def fake_oa(dois, mailto, limiter):
        return [{
            "doi": "10.2/x", "title": "Paper X via OpenAlex", "authors": "Y. Z",
            "year": 2019, "publication_title": "V", "pdf_url": None,
        }], 1

    monkeypatch.setattr(reference_store, "_openalex_resolve_dois", fake_oa)

    stats = resolve_reference_lists(conn, source="openalex", use_batch=False, assured=False)

    assert batch_calls["openalex"] == 0
    assert batch_calls["zotero"] == 0
    row = conn.execute("SELECT status FROM reference_lists").fetchone()
    assert row["status"] == "resolved"
    assert stats["new_papers"] == 1
