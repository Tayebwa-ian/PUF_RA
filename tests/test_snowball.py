"""Tests for snowball module."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock
from urllib.error import HTTPError

import pytest

import src.snowball as snowball
from src.db_schema import create_schema
from src.rate_limiter import RateLimiter, RateLimitError, parse_retry_after
from src.snowball import (
    _create_snowball_edge,
    _ensure_source_snowball,
    _find_or_create_paper,
    _get_json,
    _get_seed_papers,
    _normalise_reference,
    _resolve_seeds,
    find_existing_paper_id,
    run_snowball,
)


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    return conn


def _insert_paper(conn, title, doi=None, year=2020, abstract="", authors="A. Author"):
    cursor = conn.execute(
        """
        INSERT INTO papers (title, authors, year, abstract, publication_title, doi)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, authors, year, abstract, "Venue", doi),
    )
    return cursor.lastrowid


def _source_names(conn, paper_id):
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT s.name FROM sources s
            JOIN paper_sources ps ON ps.source_id = s.id
            WHERE ps.paper_id = ?
            """,
            (paper_id,),
        )
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


# ---------------------------------------------------------------------------
# reference normalisation
# ---------------------------------------------------------------------------

def test_normalise_reference_with_doi_and_title():
    ref = {
        "DOI": "10.1234/test",
        "title": "A Paper on PUFs",
        "authors": [{"name": "Smith, John"}],
        "year": 2023,
        "abstract": "An abstract.",
    }
    result = _normalise_reference(ref)
    assert result is not None
    assert result["doi"] == "10.1234/test"
    assert result["title"] == "A Paper on PUFs"
    assert result["authors"] == "Smith, John"
    assert result["year"] == 2023


def test_normalise_reference_missing_title_and_doi():
    ref = {"authors": [{"name": "Smith, John"}]}
    result = _normalise_reference(ref)
    assert result is None


def test_normalise_reference_list_title():
    ref = {"DOI": "10.1234/test", "title": ["Title One", "Title Two"], "year": 2023}
    result = _normalise_reference(ref)
    assert result["title"] == "Title One"


def test_ensure_source_snowball():
    conn = _memory_db()
    sid1 = _ensure_source_snowball(conn)
    sid2 = _ensure_source_snowball(conn)
    assert sid1 == sid2
    assert sid1 > 0


# ---------------------------------------------------------------------------
# seed selection (v2 schema: provenance lives in paper_queries)
# ---------------------------------------------------------------------------

def test_get_seed_papers_uses_paper_queries():
    conn = _memory_db()
    query_id = conn.execute(
        "INSERT INTO queries (platform, query_text) VALUES (?, ?)",
        ("ACM Digital Library", "puf AND side-channel"),
    ).lastrowid
    first = _insert_paper(conn, "Seed One", doi="10.1/one")
    second = _insert_paper(conn, "Seed Two")
    unrelated = _insert_paper(conn, "Not A Seed", doi="10.1/three")
    for paper_id in (first, second):
        conn.execute(
            "INSERT INTO paper_queries (paper_id, query_id) VALUES (?, ?)",
            (paper_id, query_id),
        )

    seeds = _get_seed_papers(conn, [query_id])

    assert seeds == [first, second]
    assert unrelated not in seeds


def test_get_seed_papers_empty_query_ids():
    conn = _memory_db()
    _insert_paper(conn, "Any Paper")
    assert _get_seed_papers(conn, []) == []


def test_resolve_seeds_precedence():
    conn = _memory_db()
    query_id = conn.execute(
        "INSERT INTO queries (platform, query_text) VALUES (?, ?)", ("ACM", "q")
    ).lastrowid
    first = _insert_paper(conn, "One")
    second = _insert_paper(conn, "Two")
    conn.execute(
        "INSERT INTO paper_queries (paper_id, query_id) VALUES (?, ?)", (first, query_id)
    )

    assert _resolve_seeds(conn, [second], [query_id]) == [second]
    assert _resolve_seeds(conn, None, [query_id]) == [first]
    assert _resolve_seeds(conn, None, None) == [first, second]


# ---------------------------------------------------------------------------
# rate limiter
# ---------------------------------------------------------------------------

def test_rate_limiter_backoff_retry_after():
    limiter = RateLimiter(min_interval=0.0, max_wait=60.0, backoff_base=2.0, jitter=0.5)

    assert limiter.backoff_seconds(0, "5") == 5.0

    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
    from_date = limiter.backoff_seconds(0, future)
    assert isinstance(from_date, float)
    assert 0.0 < from_date <= limiter.max_wait

    exponential = limiter.backoff_seconds(2, None)
    assert exponential > 2.0 ** 2 - 1e-9
    assert exponential <= limiter.max_wait + limiter.jitter


def test_rate_limiter_backoff_caps_at_max_wait():
    limiter = RateLimiter(min_interval=0.0, max_wait=3.0, jitter=0.0)
    assert limiter.backoff_seconds(0, "1000") == 3.0
    assert limiter.backoff_seconds(10, None) == 3.0


def test_parse_retry_after_variants():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("0") is None
    assert parse_retry_after("2.5") == 2.5
    past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=30))
    assert parse_retry_after(past) is None


def test_rate_limiter_raises_after_max_retries():
    limiter = RateLimiter(min_interval=0.0, max_retries=2)
    limiter.note_failure()
    with pytest.raises(RateLimitError):
        limiter.note_failure()
    limiter.note_success()
    assert limiter.consecutive_failures == 0


def test_rate_limiter_paces_calls():
    limiter = RateLimiter(min_interval=0.05, jitter=0.0)
    started = __import__("time").monotonic()
    limiter.wait_before_call()
    limiter.wait_before_call()
    assert __import__("time").monotonic() - started >= 0.05
    assert limiter.total_calls == 2


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def test_get_json_retries_on_429_then_succeeds(monkeypatch):
    limiter = RateLimiter(min_interval=0.0, max_wait=0.0, jitter=0.0)
    rate_limited = HTTPError(
        "https://api.example/x", 429, "Too Many Requests", {"Retry-After": "0"}, None
    )
    fake_urlopen = mock.Mock(side_effect=[rate_limited, _FakeResponse({"data": [1]})])
    monkeypatch.setattr(snowball, "urlopen", fake_urlopen)

    payload = _get_json("https://api.example/x", rate_limiter=limiter)

    assert payload == {"data": [1]}
    assert fake_urlopen.call_count == 2
    assert limiter.consecutive_failures == 0


def test_get_json_reraises_non_retryable_http_error(monkeypatch):
    limiter = RateLimiter(min_interval=0.0, max_wait=0.0, jitter=0.0)
    not_found = HTTPError("https://api.example/x", 404, "Not Found", {}, None)
    monkeypatch.setattr(snowball, "urlopen", mock.Mock(side_effect=not_found))

    with pytest.raises(HTTPError):
        _get_json("https://api.example/x", rate_limiter=limiter)


def test_get_json_raises_rate_limit_error_when_exhausted(monkeypatch):
    limiter = RateLimiter(min_interval=0.0, max_retries=3, max_wait=0.0, jitter=0.0)
    monkeypatch.setattr(
        snowball, "urlopen", mock.Mock(side_effect=OSError("network unreachable"))
    )

    with pytest.raises(RateLimitError):
        _get_json("https://api.example/x", rate_limiter=limiter)


# ---------------------------------------------------------------------------
# dedup + provenance
# ---------------------------------------------------------------------------

def test_find_existing_paper_id_doi_and_title():
    conn = _memory_db()
    with_doi = _insert_paper(conn, "Fault Injection on PUFs", doi="10.1109/ABC.2020")
    without_doi = _insert_paper(conn, "Side-Channel   Analysis of PUFs")

    assert find_existing_paper_id(conn, "https://doi.org/10.1109/abc.2020", "") == with_doi
    assert find_existing_paper_id(conn, None, "side-channel analysis of pufs") == without_doi
    assert find_existing_paper_id(conn, None, "  SIDE-CHANNEL ANALYSIS of PUFs ") == without_doi
    assert find_existing_paper_id(conn, None, "Unknown Paper") is None
    assert find_existing_paper_id(conn, "10.9/other", "Fault Injection on PUFs") is None


def test_find_or_create_paper_links_source_without_duplicating():
    conn = _memory_db()
    snowball_source = _ensure_source_snowball(conn)
    existing = _insert_paper(conn, "Laser Fault Injection on Arbiter PUFs")

    ref = {
        "doi": "10.1109/NEW.2021",
        "title": "laser fault injection on   arbiter pufs",
        "authors": "B. Author",
        "year": 2021,
        "abstract": "Text.",
        "publication_title": "HOST",
    }
    paper_id = _find_or_create_paper(conn, ref, snowball_source)

    assert paper_id == existing
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    assert _source_names(conn, existing) == {"snowball"}
    assert conn.execute("SELECT doi FROM papers WHERE id = ?", (existing,)).fetchone()[0] == (
        "10.1109/NEW.2021"
    )


def test_create_snowball_edge_skips_self_and_duplicates():
    conn = _memory_db()
    parent = _insert_paper(conn, "Parent")
    child = _insert_paper(conn, "Child")

    assert _create_snowball_edge(conn, child, parent, 1) is True
    assert _create_snowball_edge(conn, child, parent, 1) is False
    assert _create_snowball_edge(conn, parent, parent, 1) is False
    assert conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0] == 1


def test_snowball_dedup_links_snowball_source(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed Survey of PUFs", doi="10.1109/SEED.2019")
    known = _insert_paper(conn, "Photonic Emission Analysis of PUFs")
    acm_source = conn.execute(
        "INSERT INTO sources (name, description) VALUES (?, ?)",
        ("query1:ACM", "Search query1 via ACM"),
    ).lastrowid
    conn.execute(
        "INSERT INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
        (known, acm_source),
    )

    references = [
        {
            "title": "photonic   EMISSION analysis of pufs",
            "authors": [{"name": "C. Coder"}],
            "year": 2016,
            "abstract": "Existing paper reached via snowball.",
        },
        {
            "title": "A Brand New Reference",
            "externalIds": {"DOI": "10.1145/BRAND.NEW"},
            "authors": [{"given": "Dana", "family": "Dev"}],
            "year": 2022,
        },
    ]
    monkeypatch.setattr(
        snowball, "_fetch_references", lambda *args, **kwargs: (references, 1)
    )

    stats = run_snowball(
        conn,
        seed_paper_ids=[seed],
        depth=1,
        max_refs_per_paper=5,
        auto_relevance=False,
        rate_limiter=RateLimiter(min_interval=0.0),
    )

    assert stats["discovered"] == 2
    assert stats["new"] == 1
    assert stats["linked"] == 1
    assert stats["edges"] == 2
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3
    assert _source_names(conn, known) == {"query1:ACM", "snowball"}

    new_id = conn.execute(
        "SELECT id FROM papers WHERE doi = ?", ("10.1145/BRAND.NEW",)
    ).fetchone()[0]
    assert _source_names(conn, new_id) == {"snowball"}
    edges = {
        (row[0], row[1])
        for row in conn.execute("SELECT parent_paper_id, child_paper_id FROM snowball_edges")
    }
    assert edges == {(seed, known), (seed, new_id)}


def test_run_snowball_is_idempotent(monkeypatch):
    conn = _memory_db()
    seed = _insert_paper(conn, "Seed", doi="10.1/seed")
    references = [{"title": "Referenced Work", "DOI": "10.1/ref", "year": 2018}]
    monkeypatch.setattr(
        snowball, "_fetch_references", lambda *args, **kwargs: (references, 1)
    )
    limiter = RateLimiter(min_interval=0.0)

    first = run_snowball(
        conn,
        seed_paper_ids=[seed],
        auto_relevance=False,
        rate_limiter=limiter,
        skip_expanded=False,
    )
    second = run_snowball(
        conn,
        seed_paper_ids=[seed],
        auto_relevance=False,
        rate_limiter=limiter,
        skip_expanded=False,
    )

    assert first["new"] == 1
    assert second["new"] == 0
    assert second["linked"] == 1
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0] == 1


def test_run_snowball_skips_already_expanded_seeds(monkeypatch):
    conn = _memory_db()
    done = _insert_paper(conn, "Already Expanded", doi="10.1/done")
    fresh = _insert_paper(conn, "Untouched Seed", doi="10.1/fresh")
    child = _insert_paper(conn, "Old Child", doi="10.1/child")
    _create_snowball_edge(conn, child, done, 1)

    fetched = []

    def _fake_fetch(title, doi, source, max_refs, limiter, budget):
        fetched.append(doi)
        return [], 1

    monkeypatch.setattr(snowball, "_fetch_references", _fake_fetch)

    stats = run_snowball(
        conn,
        seed_paper_ids=[done, fresh],
        auto_relevance=False,
        rate_limiter=RateLimiter(min_interval=0.0),
    )

    assert fetched == ["10.1/fresh"]
    assert stats["skipped"] == 1
    assert stats["seeds"] == 2


def test_run_snowball_respects_api_call_budget(monkeypatch):
    conn = _memory_db()
    seeds = [_insert_paper(conn, f"Seed {i}", doi=f"10.1/seed{i}") for i in range(5)]
    calls = []

    def _fake_fetch(title, doi, source, max_refs, limiter, budget):
        calls.append(doi)
        return [{"title": f"Ref of {title}", "year": 2019}], 1

    monkeypatch.setattr(snowball, "_fetch_references", _fake_fetch)

    stats = run_snowball(
        conn,
        seed_paper_ids=seeds,
        max_api_calls=2,
        auto_relevance=False,
        rate_limiter=RateLimiter(min_interval=0.0),
    )

    assert len(calls) == 2
    assert stats["processed"] == 2
    assert stats["api_calls"] == 2


def test_run_snowball_stops_gracefully_when_api_unavailable(monkeypatch):
    conn = _memory_db()
    seeds = [_insert_paper(conn, f"Seed {i}", doi=f"10.1/seed{i}") for i in range(3)]

    def _boom(*args, **kwargs):
        raise RateLimitError("network unreachable")

    monkeypatch.setattr(snowball, "_fetch_references", _boom)

    stats = run_snowball(
        conn,
        seed_paper_ids=seeds,
        auto_relevance=False,
        rate_limiter=RateLimiter(min_interval=0.0),
    )

    assert stats["aborted"] == 1
    assert stats["new"] == 0
    assert stats["discovered"] == 0
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3


def test_run_snowball_without_seeds_returns_zero_stats():
    conn = _memory_db()
    stats = run_snowball(conn, auto_relevance=False, rate_limiter=RateLimiter(min_interval=0.0))
    assert stats["seeds"] == 0
    assert stats["new"] == 0
