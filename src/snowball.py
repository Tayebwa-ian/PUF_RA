"""Snowball / backward search module.

Expands the literature review by following the reference chains of papers
already in the corpus. Uses the Semantic Scholar API (primary) or the Crossref
API and records every discovery with full provenance:

* papers are deduplicated on DOI (case-insensitive) and on the normalised
  title, so a paper found by ``query1``, ``query2`` *and* snowballing is a
  single row that simply accumulates ``paper_sources`` links;
* the ``snowball`` source is linked for every reference that is reached;
* ``snowball_edges`` records the parent -> child traversal at each depth.

All HTTP traffic goes through :class:`src.rate_limiter.RateLimiter`, which
paces calls, honours ``Retry-After`` and backs off exponentially with jitter.

This module is the shared-helper layer for the snowball feature: it exposes the
low-level HTTP client (:func:`_get_json`), the reference normalisation helpers
(:func:`_normalise_reference`, :func:`normalise_doi`, :func:`normalise_title`),
the Semantic Scholar backward client (:func:`s2_get_references`), the seed
selection helper (:func:`_get_seed_papers`), the deduplication helper
(:func:`find_existing_paper_id`) and the paper/source/edge persistence helpers
(:func:`_ensure_source_snowball`, :func:`_find_or_create_paper`,
:func:`_update_paper_if_needed`, :func:`_create_snowball_edge`). The single
implementation of the snowball pipeline lives in :mod:`src.reference_store`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.rate_limiter import RateLimiter, RateLimitError

__all__ = [
    "RateLimiter",
    "RateLimitError",
    "find_existing_paper_id",
    "s2_get_references",
]


#: Semantic Scholar base URL (free tier, no auth required)
S2_BASE = "https://api.semanticscholar.org/graph/v1"
#: Crossref base URL
CROSSREF_BASE = "https://api.crossref.org/works"
#: Fields requested from Semantic Scholar for every paper/reference
S2_FIELDS = "title,authors,year,abstract,externalIds,publicationVenue"
#: Socket timeout for a single HTTP request, in seconds
REQUEST_TIMEOUT = 15.0
#: User agent sent with every request
USER_AGENT = "PUF-RA-Pipeline/2.0"
#: HTTP status codes that are worth retrying after a backoff
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _retry_after_header(exc: HTTPError) -> Optional[str]:
    """Extract the ``Retry-After`` header from an HTTPError, if present."""
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter("Retry-After")
    if value is None:
        value = getter("retry-after")
    return None if value is None else str(value)


def _get_json(
    url: str,
    rate_limiter: Optional[RateLimiter] = None,
    retries: Optional[int] = None,
    timeout: float = REQUEST_TIMEOUT,
    source: Optional[str] = None,
) -> dict[str, Any]:
    """GET *url* and parse the JSON response using smart rate limiting.

    Args:
        url: Absolute URL to fetch.
        rate_limiter: Shared limiter; a private default is used when omitted.
        retries: Override for the limiter's ``max_retries`` attempt budget.
        timeout: Per-request socket timeout in seconds.

    Returns:
        The parsed JSON object.

    Raises:
        RateLimitError: If every attempt failed (rate limit or network error).
        HTTPError: For non-retryable HTTP errors (e.g. 404).
    """
    limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    attempts = limiter.max_retries if retries is None else max(1, int(retries))
    last_error: Optional[Exception] = None

    for attempt in range(attempts):
        limiter.wait_before_call(source=source)
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS:
                raise
            wait = limiter.backoff_seconds(attempt, _retry_after_header(exc))
            print(f"  HTTP {exc.code} from API; backing off {wait:.1f}s")
            time.sleep(wait)
            last_error = exc
            limiter.note_failure()
            continue
        except (URLError, TimeoutError, OSError) as exc:
            wait = limiter.backoff_seconds(attempt, None)
            print(f"  Request failed ({exc}); retrying in {wait:.1f}s")
            time.sleep(wait)
            last_error = exc
            limiter.note_failure()
            continue
        limiter.note_success()
        return payload

    raise RateLimitError(f"Failed after {attempts} attempts: {url} ({last_error})")


def _post_json(
    url: str,
    body: dict[str, Any],
    rate_limiter: Optional[RateLimiter] = None,
    retries: Optional[int] = None,
    timeout: float = REQUEST_TIMEOUT,
    source: Optional[str] = None,
) -> dict[str, Any]:
    """POST *body* as JSON to *url* and parse the JSON response with smart pacing.

    Mirrors :func:`_get_json` but issues an HTTP ``POST`` with a JSON body (used
    by Semantic Scholar's ``/paper/batch`` endpoint). Paces via the shared
    :class:`RateLimiter`, retries on :data:`RETRYABLE_STATUS` and raises
    :class:`RateLimitError` once the attempt budget is exhausted.

    Args:
        url: Absolute URL to fetch.
        body: JSON-serialisable request body.
        rate_limiter: Shared limiter; a private default is used when omitted.
        retries: Override for the limiter's ``max_retries`` attempt budget.
        timeout: Per-request socket timeout in seconds.
        source: Source key used for pace/backoff accounting.

    Returns:
        The parsed JSON object.

    Raises:
        RateLimitError: If every attempt failed (rate limit or network error).
        HTTPError: For non-retryable HTTP errors (e.g. 400).
    """
    limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    attempts = limiter.max_retries if retries is None else max(1, int(retries))
    last_error: Optional[Exception] = None

    payload_bytes = json.dumps(body).encode("utf-8")
    for attempt in range(attempts):
        limiter.wait_before_call(source=source)
        request = Request(
            url,
            data=payload_bytes,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS:
                raise
            wait = limiter.backoff_seconds(attempt, _retry_after_header(exc))
            print(f"  HTTP {exc.code} from API; backing off {wait:.1f}s")
            time.sleep(wait)
            last_error = exc
            limiter.note_failure()
            continue
        except (URLError, TimeoutError, OSError) as exc:
            wait = limiter.backoff_seconds(attempt, None)
            print(f"  Request failed ({exc}); retrying in {wait:.1f}s")
            time.sleep(wait)
            last_error = exc
            limiter.note_failure()
            continue
        limiter.note_success()
        return parsed

    raise RateLimitError(f"Failed after {attempts} attempts: {url} ({last_error})")


# ---------------------------------------------------------------------------
# Semantic Scholar client
# ---------------------------------------------------------------------------



def s2_get_references(
    paper_id: str, limit: int = 20, rate_limiter: Optional[RateLimiter] = None
) -> list[dict[str, Any]]:
    """Fetch references for a Semantic Scholar paper.

    Args:
        paper_id: Semantic Scholar id, ``DOI:10.x/y`` or ``CorpusId:1234``.
        limit: Maximum number of references to return.
        rate_limiter: Shared limiter for pacing/backoff.

    Returns:
        List of reference dicts.
    """
    url = (
        f"{S2_BASE}/paper/{quote(paper_id, safe=':/')}/references"
        f"?fields={S2_FIELDS}&limit={limit}"
    )
    data = _get_json(url, rate_limiter=rate_limiter, source="semantic_scholar")
    references = []
    for entry in data.get("data") or []:
        if not isinstance(entry, dict):
            continue
        cited = entry.get("citedPaper", entry)
        if isinstance(cited, dict):
            references.append(cited)
    return references


# ---------------------------------------------------------------------------
# Crossref client
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Reference normalisation
# ---------------------------------------------------------------------------

def _normalise_reference(ref: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract and normalise metadata from a reference dict.

    Returns a dict with 'doi', 'title', 'authors', 'year', 'abstract'
    or None if the reference is unusable (no title and no DOI).
    """
    doi = ref.get("DOI") or (ref.get("externalIds") or {}).get("DOI")
    title = ref.get("title") or ref.get("Title") or ref.get("article-title")
    if isinstance(title, list):
        title = title[0] if title else ""
    title = (title or "").strip()

    if not title and not doi:
        return None

    authors_raw = ref.get("authors") or ref.get("author") or []
    authors_list = []
    if isinstance(authors_raw, str):
        authors_raw = [{"name": authors_raw}]
    for author in authors_raw:
        if not isinstance(author, dict):
            continue
        name = author.get("name") or (
            f"{author.get('given', '')} {author.get('family', '')}"
        )
        if name and name.strip():
            authors_list.append(name.strip())
    authors = "; ".join(authors_list)

    year = ref.get("year") or (ref.get("published") or {}).get(
        "date-parts", [[None]]
    )[0][0]
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None

    abstract = ref.get("abstract") or ""
    if isinstance(abstract, list):
        abstract = " ".join(abstract)
    abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    venue = ref.get("publicationVenue") or {}
    publication_title = (
        ref.get("publication_title")
        or (venue.get("name") if isinstance(venue, dict) else "")
        or ref.get("journal-title")
        or ref.get("venue")
        or ""
    )

    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "publication_title": publication_title,
    }


# ---------------------------------------------------------------------------
# Identity / deduplication helpers
# ---------------------------------------------------------------------------

#: SQL expression that strips all whitespace from a lowercased title
_TITLE_SQUASH_SQL = (
    "replace(replace(replace(replace(lower(title), ' ', ''), "
    "char(9), ''), char(10), ''), char(13), '')"
)

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalise_doi(doi: Optional[str]) -> Optional[str]:
    """Return a comparable DOI: resolver prefix stripped, lowercased."""
    if not doi:
        return None
    text = str(doi).strip()
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().lower()
    return text or None


def normalise_title(title: Optional[str]) -> str:
    """Return a comparable title: whitespace-collapsed, lowercased."""
    return " ".join((title or "").split()).lower()


def _squash_title(title: Optional[str]) -> str:
    """Return a title with *all* whitespace removed, lowercased."""
    return re.sub(r"\s+", "", title or "").lower()


def _match_title(title: Optional[str]) -> str:
    """Return a tolerant matching key for a title.

    Unicode-NFKC-normalised, lowercased, with every character that is not an
    alphanumeric or whitespace stripped, then whitespace collapsed. This is a
    *separate* key from :func:`normalise_title` (which only lowercases and
    collapses whitespace and is used by SQL dedup), so that punctuation, accents
    and HTML entities no longer prevent fuzzy title matches.
    """
    text = unicodedata.normalize("NFKC", title or "")
    # Decompose any remaining accented letters (NFKC re-composes them) and drop
    # the combining marks so e.g. "Café" collapses to "cafe".
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    text = re.sub(r"[^0-9a-z\s]", "", text.lower())
    return " ".join(text.split())


def find_existing_paper_id(
    conn: sqlite3.Connection, doi: Optional[str], title: Optional[str]
) -> Optional[int]:
    """Return the id of the paper matching *doi* (preferred) or *title*.

    DOI matching is case-insensitive. Title matching is whitespace- and
    case-insensitive, but two papers carrying *different* DOIs are never
    merged on title alone.
    """
    norm_doi = normalise_doi(doi)
    if norm_doi:
        row = conn.execute(
            "SELECT id FROM papers WHERE lower(doi) = ?", (norm_doi,)
        ).fetchone()
        if row:
            return row[0]

    norm_title = normalise_title(title)
    if not norm_title:
        return None

    candidates = conn.execute(
        "SELECT id, doi FROM papers WHERE lower(title) = ?", (norm_title,)
    ).fetchall()
    if not candidates:
        candidates = conn.execute(
            f"SELECT id, doi FROM papers WHERE {_TITLE_SQUASH_SQL} = ?",
            (_squash_title(title),),
        ).fetchall()

    for row in candidates:
        existing_doi = normalise_doi(row[1])
        if norm_doi and existing_doi and existing_doi != norm_doi:
            continue
        return row[0]
    return None


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def _ensure_source_snowball(conn: sqlite3.Connection) -> int:
    """Get or create the 'snowball' source and return its id."""
    row = conn.execute("SELECT id FROM sources WHERE name = 'snowball'").fetchone()
    if row:
        return row[0]
    cursor = conn.execute(
        "INSERT INTO sources (name, description) VALUES (?, ?)",
        ("snowball", "Backward snowball search"),
    )
    return cursor.lastrowid


def _find_or_create_paper(
    conn: sqlite3.Connection,
    ref: dict[str, Any],
    source_id: int,
) -> int:
    """Return the id of the paper for *ref*, creating it only if unknown.

    An already-known paper (e.g. imported from ``query1``/``query2``) is never
    re-inserted: it keeps its id, gains the missing metadata and is linked to
    *source_id* so ``paper_sources`` accumulates every discovery method.
    """
    doi = ref.get("doi")
    title = (ref.get("title") or "").strip()

    paper_id = find_existing_paper_id(conn, doi, title)
    if paper_id is not None:
        _update_paper_if_needed(conn, paper_id, ref)
        _link_source(conn, paper_id, source_id)
        return paper_id

    cursor = conn.execute(
        """
        INSERT INTO papers (title, authors, year, abstract, publication_title, doi, keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            ref.get("authors") or "",
            ref.get("year") or 0,
            ref.get("abstract") or "",
            ref.get("publication_title") or "",
            doi or None,
            None,
        ),
    )
    paper_id = int(cursor.lastrowid)
    _link_source(conn, paper_id, source_id)
    return paper_id


def _update_paper_if_needed(
    conn: sqlite3.Connection, paper_id: int, ref: dict[str, Any]
) -> None:
    """Update paper fields if they are missing or empty."""
    row = conn.execute(
        "SELECT abstract, authors, year, doi FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if not row:
        return

    abstract, authors, year, doi = row[0], row[1], row[2], row[3]
    updates: dict[str, Any] = {}
    if not abstract and ref.get("abstract"):
        updates["abstract"] = ref["abstract"]
    if not authors and ref.get("authors"):
        updates["authors"] = ref["authors"]
    if not year and ref.get("year"):
        updates["year"] = ref["year"]
    if not doi and ref.get("doi"):
        updates["doi"] = ref["doi"]

    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [paper_id]
    try:
        conn.execute(
            f"UPDATE papers SET {set_clause}, updated_at = current_timestamp WHERE id = ?",
            values,
        )
    except sqlite3.IntegrityError:
        updates.pop("doi", None)
        if not updates:
            return
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE papers SET {set_clause}, updated_at = current_timestamp WHERE id = ?",
            list(updates.values()) + [paper_id],
        )


def _link_source(conn: sqlite3.Connection, paper_id: int, source_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO paper_sources (paper_id, source_id) VALUES (?, ?)",
        (paper_id, source_id),
    )


def _create_snowball_edge(
    conn: sqlite3.Connection,
    child_paper_id: int,
    parent_paper_id: int,
    depth: int = 1,
) -> bool:
    """Create a parent -> child snowball edge, skipping duplicates and cycles.

    Returns True when a new edge row was written.
    """
    if child_paper_id == parent_paper_id:
        return False
    existing = conn.execute(
        """
        SELECT 1 FROM snowball_edges
        WHERE child_paper_id = ? AND parent_paper_id = ?
        """,
        (child_paper_id, parent_paper_id),
    ).fetchone()
    if existing:
        return False
    try:
        conn.execute(
            """
            INSERT INTO snowball_edges (child_paper_id, parent_paper_id, depth)
            VALUES (?, ?, ?)
            """,
            (child_paper_id, parent_paper_id, depth),
        )
    except sqlite3.IntegrityError:
        return False
    return True


# ---------------------------------------------------------------------------
# Snowball search logic
# ---------------------------------------------------------------------------

def _get_seed_papers(conn: sqlite3.Connection, query_ids: list[int]) -> list[int]:
    """Return the ids of all papers attached to *query_ids*.

    Provenance lives in the ``paper_queries`` junction in the v2 schema, so
    the query joins through it instead of reading a (non-existent)
    ``papers.query_id`` column.
    """
    if not query_ids:
        return []
    placeholders = ",".join("?" * len(query_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT p.id
        FROM papers p
        JOIN paper_queries pq ON pq.paper_id = p.id
        WHERE pq.query_id IN ({placeholders})
        ORDER BY p.id
        """,
        list(query_ids),
    ).fetchall()
    return [row[0] for row in rows]


