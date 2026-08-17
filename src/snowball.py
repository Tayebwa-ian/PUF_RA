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

Usage:
    from src.snowball import run_snowball

    with get_connection("results.db") as conn:
        run_snowball(
            conn,
            seed_query_ids=[3, 4],
            depth=1,
            max_refs_per_paper=15,
            max_api_calls=40,
        )
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen

from src.rate_limiter import RateLimiter, RateLimitError

__all__ = [
    "RateLimiter",
    "RateLimitError",
    "crossref_get_references",
    "find_existing_paper_id",
    "run_snowball",
    "s2_get_references",
    "s2_search_paper",
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
        limiter.wait_before_call()
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


# ---------------------------------------------------------------------------
# Semantic Scholar client
# ---------------------------------------------------------------------------

def s2_search_paper(
    title: str, rate_limiter: Optional[RateLimiter] = None
) -> Optional[dict[str, Any]]:
    """Search Semantic Scholar for a paper by title.

    Returns the top result dict or None.
    """
    url = (
        f"{S2_BASE}/paper/search?query={quote_plus(title)}"
        f"&fields={S2_FIELDS}&limit=1"
    )
    data = _get_json(url, rate_limiter=rate_limiter)
    papers = data.get("data") or []
    top = papers[0] if papers else None
    return top if isinstance(top, dict) else None


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
    data = _get_json(url, rate_limiter=rate_limiter)
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

def crossref_get_references(
    doi: str, limit: int = 20, rate_limiter: Optional[RateLimiter] = None
) -> list[dict[str, Any]]:
    """Fetch references from Crossref for a paper DOI.

    Args:
        doi: Paper DOI.
        limit: Maximum number of references to return.
        rate_limiter: Shared limiter for pacing/backoff.

    Returns:
        List of reference dicts with 'DOI', 'title', 'author', etc.
    """
    url = f"{CROSSREF_BASE}/{quote(doi, safe='/')}"
    data = _get_json(url, rate_limiter=rate_limiter)
    message = data.get("message") or {}
    refs = [entry for entry in (message.get("reference") or []) if isinstance(entry, dict)]
    return refs[:limit]


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


def _all_paper_ids(conn: sqlite3.Connection) -> list[int]:
    """Return every paper id in the corpus, ordered by id."""
    return [row[0] for row in conn.execute("SELECT id FROM papers ORDER BY id")]


def _resolve_seeds(
    conn: sqlite3.Connection,
    seed_paper_ids: Optional[list[int]],
    seed_query_ids: Optional[list[int]],
) -> list[int]:
    """Pick the seed set: explicit paper ids > query ids > whole corpus."""
    if seed_paper_ids:
        return list(dict.fromkeys(int(pid) for pid in seed_paper_ids))
    if seed_query_ids:
        return _get_seed_papers(conn, list(seed_query_ids))
    return _all_paper_ids(conn)


def _fetch_references(
    title: str,
    doi: Optional[str],
    source: str,
    max_refs: int,
    limiter: RateLimiter,
    budget: Optional[int],
) -> tuple[list[dict[str, Any]], int]:
    """Fetch the reference list of one paper.

    Returns ``(references, api_calls_used)``. A DOI is used directly as the
    Semantic Scholar identifier so the extra title search is only needed for
    papers without a usable DOI.
    """
    used = 0

    def _budget_left() -> bool:
        return budget is None or (budget - used) > 0

    if source == "crossref":
        if not doi or not _budget_left():
            return [], used
        used += 1
        return crossref_get_references(doi, limit=max_refs, rate_limiter=limiter), used

    if doi:
        if not _budget_left():
            return [], used
        used += 1
        try:
            return (
                s2_get_references(f"DOI:{doi}", limit=max_refs, rate_limiter=limiter),
                used,
            )
        except HTTPError as exc:
            if exc.code != 404:
                raise

    if not title or not _budget_left():
        return [], used
    used += 1
    match = s2_search_paper(title, rate_limiter=limiter)
    s2_id = None
    if match:
        s2_id = match.get("paperId") or (match.get("externalIds") or {}).get("CorpusId")
    if not s2_id or not _budget_left():
        return [], used
    used += 1
    return s2_get_references(str(s2_id), limit=max_refs, rate_limiter=limiter), used


def run_snowball(
    conn: sqlite3.Connection,
    seed_query_ids: Optional[list[int]] = None,
    depth: int = 1,
    max_refs_per_paper: int = 20,
    source: str = "semantic_scholar",
    delay: float = 1.0,
    auto_relevance: bool = True,
    auto_screen: bool = False,
    screening_client: Optional[Any] = None,
    screening_model: Optional[str] = None,
    screening_prompt: Optional[str] = None,
    seed_paper_ids: Optional[list[int]] = None,
    max_api_calls: Optional[int] = None,
    rate_limiter: Optional[RateLimiter] = None,
    skip_expanded: bool = True,
) -> dict[str, int]:
    """Run a backward snowball search and persist the discoveries.

    Args:
        conn: SQLite connection.
        seed_query_ids: Seed from every paper linked to these queries.
        depth: Maximum snowball depth (1 = direct references only).
        max_refs_per_paper: Maximum references to fetch per paper.
        source: API source ('semantic_scholar' or 'crossref').
        delay: Minimum spacing between API calls, in seconds.
        auto_relevance: Run relevance evaluation when new papers were added.
        auto_screen: Run LLM screening on the updated corpus.
        screening_client: OpenAI-compatible client for LLM screening.
        screening_model: Model name for LLM screening.
        screening_prompt: System prompt for LLM screening.
        seed_paper_ids: Explicit seed paper ids (highest precedence).
        max_api_calls: Stop after this many API requests (politeness budget).
        rate_limiter: Pre-configured limiter; one is built from *delay* if None.
        skip_expanded: Skip seeds that already have snowball edges, so a
            budget-limited run resumes where the previous one stopped.

    Returns:
        Stats dict with 'seeds', 'skipped', 'processed', 'discovered', 'new',
        'linked', 'edges', 'api_calls' and 'aborted'.
    """
    source_id = _ensure_source_snowball(conn)
    limiter = rate_limiter if rate_limiter is not None else RateLimiter(min_interval=delay)
    seeds = _resolve_seeds(conn, seed_paper_ids, seed_query_ids)

    if skip_expanded:
        expanded = {
            row[0]
            for row in conn.execute("SELECT DISTINCT parent_paper_id FROM snowball_edges")
        }
        pending = [paper_id for paper_id in seeds if paper_id not in expanded]
    else:
        expanded = set()
        pending = list(seeds)

    stats = {
        "seeds": len(seeds),
        "skipped": len(seeds) - len(pending),
        "processed": 0,
        "discovered": 0,
        "new": 0,
        "linked": 0,
        "edges": 0,
        "api_calls": 0,
        "aborted": 0,
    }

    if not pending:
        print("No seed papers to expand; nothing to snowball.")
        return stats

    print(
        f"Found {len(seeds)} seed papers "
        f"({stats['skipped']} already expanded). Starting snowball search..."
    )

    processed: set[int] = set()
    frontier = pending
    stop = False

    for current_depth in range(1, depth + 1):
        if stop or not frontier:
            break
        next_frontier: list[int] = []

        for parent_id in frontier:
            if parent_id in processed:
                continue
            if max_api_calls is not None and stats["api_calls"] >= max_api_calls:
                print(f"API call budget reached ({max_api_calls}); stopping.")
                stop = True
                break
            processed.add(parent_id)

            paper_row = conn.execute(
                "SELECT title, doi FROM papers WHERE id = ?", (parent_id,)
            ).fetchone()
            if not paper_row:
                continue
            title, doi = paper_row[0], paper_row[1]

            budget = None if max_api_calls is None else max_api_calls - stats["api_calls"]
            used = 0
            try:
                refs, used = _fetch_references(
                    title, doi, source, max_refs_per_paper, limiter, budget
                )
            except RateLimitError as exc:
                stats["api_calls"] += used
                print(f"Stopping: API unavailable for paper {parent_id}: {exc}")
                stats["aborted"] = 1
                stop = True
                break
            except HTTPError as exc:
                stats["api_calls"] += used
                print(f"  HTTP error for paper {parent_id}: {exc}")
                continue
            except (URLError, OSError, ValueError, TypeError, KeyError) as exc:
                stats["api_calls"] += used
                print(f"  Error fetching refs for paper {parent_id}: {exc!r}")
                continue
            except Exception as exc:
                stats["api_calls"] += used
                print(f"Stopping: unexpected error for paper {parent_id}: {exc!r}")
                stats["aborted"] = 1
                stop = True
                break

            stats["api_calls"] += used
            stats["processed"] += 1
            stats["discovered"] += len(refs)

            for ref in refs:
                normed = _normalise_reference(ref)
                if not normed:
                    continue
                try:
                    known_id = find_existing_paper_id(
                        conn, normed.get("doi"), normed.get("title")
                    )
                    child_id = _find_or_create_paper(conn, normed, source_id)
                except sqlite3.Error as exc:
                    print(f"  Error processing ref for paper {parent_id}: {exc}")
                    continue

                if known_id is None:
                    stats["new"] += 1
                else:
                    stats["linked"] += 1
                if _create_snowball_edge(conn, child_id, parent_id, current_depth):
                    stats["edges"] += 1
                if child_id not in processed:
                    next_frontier.append(child_id)

            conn.commit()

        frontier = next_frontier

    conn.commit()

    if auto_relevance and stats["new"] > 0:
        from src.relevance import evaluate_corpus

        print("Running relevance evaluation on updated corpus...")
        evaluate_corpus(conn, store=True)
        conn.commit()
    elif auto_relevance:
        print("No new papers; skipping relevance evaluation.")

    if auto_screen and screening_client and stats["new"] > 0:
        from src.screening import run_screening

        print("Running LLM screening on updated corpus...")
        run_screening(
            conn,
            client=screening_client,
            model=screening_model or "",
            system_prompt=screening_prompt or "",
            query_ids=seed_query_ids or [],
            dry_run=False,
        )
        conn.commit()

    print(f"Snowball complete: {stats}")
    return stats
