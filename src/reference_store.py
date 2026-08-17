"""Two-phase, local-first snowball reference inventory + resolution.

This module implements the research-backed snowball redesign:

* Phase 1 (harvest) - :func:`harvest_references` checks OUR OWN database before
  any external API, stores the COMPLETE reference list of every seed paper in
  ``reference_lists`` (even references not yet in our DB), then resolves the
  reachable ones in batches via Crossref / OpenAlex. Backward and forward
  directions are supported.
* Phase 2 (resolve) - :func:`resolve_reference_lists` takes every still-unresolved
  ``reference_lists`` row that carries a DOI and bulk-resolves it, inserting
  papers, linking ``snowball_edges`` and recording Open-Access PDF links.

All external HTTP goes through :func:`src.snowball._get_json` / the shared
:class:`src.rate_limiter.RateLimiter`, so pacing, ``Retry-After`` and adaptive
backoff are honoured and a run stops gracefully when the budget is spent or the
API is unavailable. Every run is logged to ``snowball_runs`` (TARCiS-style).
"""

from __future__ import annotations

import csv
from typing import Any, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from src.rate_limiter import RateLimiter, RateLimitError
from src.snowball import (
    CROSSREF_BASE,
    _create_snowball_edge,
    _ensure_source_snowball,
    _find_or_create_paper,
    _get_json,
    find_existing_paper_id,
    normalise_doi,
    normalise_title,
    s2_get_references,
)

#: OpenAlex base URL
OPENALEX_BASE = "https://api.openalex.org/works"
#: Default pacing interval (seconds) when no limiter is supplied
DEFAULT_MIN_INTERVAL = 1.0

#: Map a source to its alternate for cross-source retry (Crossref <-> OpenAlex)
_ALTERNATE_SOURCE = {"crossref": "openalex", "openalex": "crossref"}

#: Run statistics: integer counters plus the nested ``status_counts`` mapping
#: (``dict[str, int]``) produced by :func:`_status_counts`.
StatsDict = dict[str, Any]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _chunk(seq: Iterable[Any], n: int) -> Iterable[list[Any]]:
    items = list(seq)
    for i in range(0, len(items), n):
        yield items[i : i + n]


def local_find_paper(
    conn: Any, doi: Optional[str], norm_title: Optional[str]
) -> Optional[int]:
    """Return the id of a paper matching *doi* / *norm_title* in OUR database.

    Mirrors :func:`src.snowball.find_existing_paper_id` precedence (DOI first,
    then title; two papers with different non-null DOIs are never merged).
    """
    return find_existing_paper_id(conn, doi, norm_title)


# ---------------------------------------------------------------------------
# external clients (stdlib only)
# ---------------------------------------------------------------------------

def _openalex_filter(
    filter_value: str, mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """GET OpenAlex ``/works?filter=...`` and return (results, api_calls)."""
    url = f"{OPENALEX_BASE}?filter={quote(filter_value)}&per-page=200"
    if mailto:
        url += f"&mailto={quote(mailto)}"
    data = _get_json(url, rate_limiter=limiter)
    return data.get("results") or [], 1


def _openalex_resolve_ids(
    ids: list[str], mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """Batch-resolve a list of OpenAlex ids into normalised reference dicts.

    A failed chunk (HTTP/transport/rate-limit) is skipped so a single bad
    identifier never aborts the whole harvest.
    """
    out: list[dict[str, Any]] = []
    used = 0
    for chunk in _chunk(ids, 50):
        try:
            works, calls = _openalex_filter(
                "ids.openalex:" + "|".join(chunk), mailto, limiter
            )
        except (HTTPError, URLError, OSError, ValueError, RateLimitError):
            continue
        used += calls
        for work in works:
            out.append(_normalise_openalex_work(work))
    return out, used


def _openalex_resolve_dois(
    dois: list[str], mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """Resolve a list of DOIs via OpenAlex ``filter=doi:`` (the alternate path).

    Per-DOI HTTP errors (e.g. a DOI the API rejects) are skipped so a single bad
    identifier never aborts the whole batch resolution.
    """
    out: list[dict[str, Any]] = []
    used = 0
    for doi in dois:
        nd = normalise_doi(doi)
        if not nd:
            continue
        try:
            works, calls = _openalex_filter(f"doi:{quote(nd)}", mailto, limiter)
        except HTTPError:
            continue
        used += calls
        for work in works:
            out.append(_normalise_openalex_work(work))
    return out, used


def _crossref_resolve_dois(
    dois: list[str], mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """Resolve a list of DOIs via Crossref.

    Crossref's ``filter=doi:`` endpoint does not honour a ``|``-joined OR list
    (it returns no items), so each DOI is resolved with its own polite request.
    API calls are counted per DOI; 404s for unknown DOIs are skipped.
    """
    out: list[dict[str, Any]] = []
    used = 0
    for doi in dois:
        if not doi:
            continue
        url = f"{CROSSREF_BASE}/{quote(doi, safe='/')}"
        if mailto:
            url += f"?mailto={quote(mailto)}"
        try:
            data = _get_json(url, rate_limiter=limiter)
        except HTTPError:
            continue
        used += 1
        item = data.get("message") or {}
        if item.get("DOI") or item.get("title"):
            out.append(_normalise_crossref_item(item))
    return out, used


# ---------------------------------------------------------------------------
# reference normalisation
# ---------------------------------------------------------------------------

def _join_authors(authors_raw: Any) -> str:
    names: list[str] = []
    if authors_raw is None:
        return ""
    if isinstance(authors_raw, str):
        return authors_raw
    for author in authors_raw:
        if not isinstance(author, dict):
            continue
        name = author.get("name") or (
            f"{author.get('given', '')} {author.get('family', '')}".strip()
        )
        if name:
            names.append(name.strip())
    return "; ".join(names)


def _first(value: Any) -> str:
    if isinstance(value, list):
        return (value[0] if value else "") or ""
    return value or ""


def _normalise_crossref_reference(ref: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Crossref ``message.reference`` entry (citied-by list)."""
    title = _first(ref.get("article-title") or ref.get("title"))
    year = ref.get("year")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    return {
        "doi": ref.get("DOI"),
        "title": title,
        "authors": _join_authors(ref.get("author")),
        "year": year,
        "unstructured": (ref.get("unstructured") or "").strip(),
        "publication_title": "",
        "pdf_url": None,
    }


def _normalise_crossref_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise a full Crossref work (from a ``filter=doi`` lookup)."""
    title = _first(item.get("title"))
    authors = _join_authors(item.get("author"))
    year = None
    published = item.get("published") or {}
    date_parts = published.get("date-parts") or [[]]
    if date_parts and date_parts[0]:
        try:
            year = int(date_parts[0][0])
        except (TypeError, ValueError):
            year = None
    pdf_url = None
    for link in item.get("link") or []:
        if isinstance(link, dict) and link.get("content-type") == "application/pdf":
            pdf_url = link.get("URL")
            break
    if not pdf_url:
        pdf_url = item.get("URL")
    return {
        "doi": item.get("DOI"),
        "title": title,
        "authors": authors,
        "year": year,
        "unstructured": "",
        "publication_title": _first(item.get("container-title")),
        "pdf_url": pdf_url,
    }


def _normalise_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    """Normalise an OpenAlex work into a reference dict."""
    authorships = work.get("authorships") or []
    authors = "; ".join(
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if a.get("author", {}).get("display_name")
    )
    source = (work.get("primary_location") or {}).get("source") or {}
    publication_title = source.get("display_name") or ""
    best = work.get("best_oa_location") or {}
    pdf_url = best.get("pdf_url")
    return {
        "doi": work.get("doi"),
        "title": work.get("title") or "",
        "authors": authors,
        "year": work.get("publication_year"),
        "unstructured": "",
        "publication_title": publication_title,
        "pdf_url": pdf_url,
    }


def _normalise_s2_reference(ref: dict[str, Any]) -> dict[str, Any]:
    venue = ref.get("publicationVenue")
    if isinstance(venue, dict):
        venue_name = venue.get("name", "")
    else:
        venue_name = ""
    return {
        "doi": (ref.get("externalIds") or {}).get("DOI"),
        "title": ref.get("title") or "",
        "authors": _join_authors(ref.get("authors")),
        "year": ref.get("year"),
        "unstructured": "",
        "publication_title": venue_name,
        "pdf_url": None,
    }


# ---------------------------------------------------------------------------
# seed reference-list fetching
# ---------------------------------------------------------------------------

def _fetch_seed_reference_list(
    conn: Any,
    seed_id: int,
    seed_doi: Optional[str],
    seed_title: Optional[str],
    direction: str,
    source: str,
    limiter: RateLimiter,
    mailto: Optional[str],
    budget: Optional[int],
) -> tuple[list[dict[str, Any]], int]:
    """Fetch the reference list of one seed paper.

    Returns ``(references, api_calls_used)``. ``references`` are normalised
    dicts; ``api_calls_used`` counts only real outbound requests.
    """
    if budget is not None and budget <= 0:
        return [], 0

    if source == "crossref":
        if direction != "backward":
            print("  Crossref only supports backward references; skipping.")
            return [], 0
        if not seed_doi:
            return [], 0
        url = f"{CROSSREF_BASE}/{quote(seed_doi, safe='/')}"
        if mailto:
            url += f"?mailto={quote(mailto)}"
        data = _get_json(url, rate_limiter=limiter)
        refs = data.get("message", {}).get("reference") or []
        return [_normalise_crossref_reference(r) for r in refs if isinstance(r, dict)], 1

    if source == "openalex":
        if not seed_doi:
            return [], 0
        works, used = _openalex_filter(f"doi:{quote(seed_doi)}", mailto, limiter)
        if not works:
            return [], used
        if direction == "backward":
            referenced = works[0].get("referenced_works") or []
            ids = [w.rsplit("/", 1)[-1] for w in referenced if isinstance(w, str)]
            refs, used2 = _openalex_resolve_ids(ids, mailto, limiter)
            return refs, used + used2
        oa_id = works[0].get("id")
        oid = oa_id.rsplit("/", 1)[-1] if isinstance(oa_id, str) else oa_id
        citing, used2 = _openalex_filter(f"cites:{oid}", mailto, limiter)
        return [_normalise_openalex_work(w) for w in citing], used + used2

    if source == "s2":
        if direction != "backward":
            print("  Semantic Scholar forward search is not supported; skipping.")
            return [], 0
        if not seed_doi:
            return [], 0
        srefs = s2_get_references(f"DOI:{seed_doi}", limit=200, rate_limiter=limiter)
        return [_normalise_s2_reference(r) for r in srefs], 1

    print(f"  Unknown source '{source}'; skipping seed {seed_id}.")
    return [], 0


# ---------------------------------------------------------------------------
# persistence helpers
# ---------------------------------------------------------------------------

def _upsert_reference_list(
    conn: Any,
    parent_id: int,
    direction: str,
    ref: dict[str, Any],
    resolved_pid: Optional[int],
    source: str,
    ref_index: Optional[int],
) -> None:
    ref_doi = normalise_doi(ref.get("doi"))
    unstructured = (ref.get("unstructured") or "").strip()
    title_full = (ref.get("title") or "").strip()
    if not ref_doi and not unstructured and title_full:
        unstructured = title_full
    status = "resolved" if resolved_pid is not None else "pending"
    conn.execute(
        """
        INSERT OR IGNORE INTO reference_lists
            (parent_paper_id, direction, ref_index, ref_doi, ref_title,
             ref_year, ref_authors, ref_unstructured, resolved_paper_id, source,
             status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parent_id,
            direction,
            ref_index,
            ref_doi,
            title_full,
            ref.get("year"),
            ref.get("authors") or "",
            unstructured,
            resolved_pid,
            source,
            status,
        ),
    )
    if resolved_pid is not None:
        # The row may already exist (INSERT OR IGNORE hit the unique index): link
        # it and mark it resolved in the SAME write, so a locally-resolved
        # reference never stays 'pending' (not even with resolve=False).
        conn.execute(
            """
            UPDATE reference_lists
            SET resolved_paper_id = COALESCE(resolved_paper_id, ?),
                status = 'resolved'
            WHERE parent_paper_id = ? AND direction = ?
              AND COALESCE(ref_doi, '') = ? AND COALESCE(ref_unstructured, '') = ?
            """,
            (resolved_pid, parent_id, direction, ref_doi or "", unstructured),
        )


def _find_or_create_ref_paper(
    conn: Any, ref: dict[str, Any], source_id: int, pdf_url: Optional[str]
) -> tuple[int, bool]:
    existing = local_find_paper(conn, ref.get("doi"), ref.get("title"))
    if existing is not None:
        if pdf_url:
            conn.execute(
                "UPDATE papers SET pdf_url = ? WHERE id = ? AND pdf_url IS NULL",
                (pdf_url, existing),
            )
        return existing, False
    normed = {
        "doi": ref.get("doi"),
        "title": (ref.get("title") or "").strip(),
        "authors": ref.get("authors") or "",
        "year": ref.get("year") or 0,
        "abstract": "",
        "publication_title": ref.get("publication_title") or "",
    }
    paper_id = _find_or_create_paper(conn, normed, source_id)
    if pdf_url:
        conn.execute(
            "UPDATE papers SET pdf_url = ? WHERE id = ? AND pdf_url IS NULL",
            (pdf_url, paper_id),
        )
    return paper_id, True


def _fetch_unresolved_with_doi(
    conn: Any, direction: Optional[str]
) -> list[dict[str, Any]]:
    if direction is None:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year,
                   ref_authors, ref_unstructured
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND ref_doi IS NOT NULL
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year,
                   ref_authors, ref_unstructured
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND ref_doi IS NOT NULL
              AND direction = ?
            """,
            (direction,),
        ).fetchall()
    return [dict(r) for r in rows]


def _resolve_dois_via_source(
    dois: list[str], source: str, mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """Resolve a list of DOIs through *source* (Crossref or OpenAlex).

    OpenAlex is queried by ``filter=doi:`` (the correct OpenAlex filter for a
    DOI); ``ids.openalex:`` is only used by the harvest path that already has
    OpenAlex ids from ``referenced_works``.
    """
    if source == "openalex":
        return _openalex_resolve_dois(list(dois), mailto, limiter)
    return _crossref_resolve_dois(list(dois), mailto, limiter)


def _batch_resolve_references(
    conn: Any,
    rows: list[dict[str, Any]],
    source: str,
    mailto: Optional[str],
    limiter: RateLimiter,
    max_api_calls: Optional[int],
    api_calls: int,
    stats: StatsDict,
) -> int:
    """Resolve a batch of unresolved references that carry a DOI.

    Tries the chosen *source* first; any DOI not returned by it is retried
    against the ALTERNATE source (Crossref <-> OpenAlex). A DOI resolved by
    either source is inserted/linked with ``status='resolved'``; a DOI that
    fails on BOTH sources is marked ``status='fetch_error'`` -- never silently
    dropped.
    """
    if not rows:
        return api_calls
    source_id = _ensure_source_snowball(conn)
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        nd = normalise_doi(row["ref_doi"])
        if nd is None:
            # A ref_doi we cannot normalise is unusable for lookup: record an
            # explicit outcome instead of leaving the row silently 'pending'.
            _set_ref_status(conn, row["id"], "fetch_error")
            continue
        groups.setdefault(nd, []).append(row)
        if nd not in order:
            order.append(nd)

    alternate = _ALTERNATE_SOURCE[source]

    for chunk in _chunk(order, 50):
        if max_api_calls is not None and api_calls >= max_api_calls:
            print(f"  API budget reached ({max_api_calls}); stopping resolution.")
            break
        remaining = None if max_api_calls is None else max_api_calls - api_calls
        if remaining is not None and remaining <= 0:
            break
        primary_chunk = list(chunk)[:remaining] if remaining is not None else list(chunk)

        resolved, used = _resolve_dois_via_source(primary_chunk, source, mailto, limiter)
        api_calls += used
        stats["api_calls"] = api_calls

        by_doi = {normalise_doi(r.get("doi")): r for r in resolved}
        missing = [d for d in primary_chunk if d not in by_doi]
        if missing and remaining is not None:
            missing = missing[:remaining]
        if missing:
            alt_resolved, used2 = _resolve_dois_via_source(missing, alternate, mailto, limiter)
            api_calls += used2
            stats["api_calls"] = api_calls
            for r in alt_resolved:
                by_doi.setdefault(normalise_doi(r.get("doi")), r)

        for doi in chunk:
            meta = by_doi.get(doi)
            if meta is None:
                for row in groups[doi]:
                    _set_ref_status(conn, row["id"], "fetch_error")
                continue
            paper_id, is_new = _find_or_create_ref_paper(
                conn, meta, source_id, meta.get("pdf_url")
            )
            if is_new:
                stats["new_papers"] += 1
            for row in groups[doi]:
                conn.execute(
                    "UPDATE reference_lists SET resolved_paper_id = ?, status = 'resolved' WHERE id = ?",
                    (paper_id, row["id"]),
                )
                if _create_snowball_edge(conn, paper_id, row["parent_paper_id"], 1):
                    stats["edges"] += 1
        conn.commit()
    return api_calls


def _fetch_unresolved_doi_less(
    conn: Any, direction: Optional[str]
) -> list[dict[str, Any]]:
    if direction is None:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year, ref_authors
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND ref_doi IS NULL
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year, ref_authors
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND ref_doi IS NULL AND direction = ?
            """,
            (direction,),
        ).fetchall()
    return [dict(r) for r in rows]


def _openalex_title_search(
    norm_title: str, mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """GET OpenAlex ``/works?filter=title.search:<title>`` and normalise.

    HTTP/transport errors are swallowed (returning no candidates) so a single
    unparseable or rejected title query can never abort the whole assured run.
    """
    url = f"{OPENALEX_BASE}?filter=title.search:{quote(norm_title)}&per-page=50"
    if mailto:
        url += f"&mailto={quote(mailto)}"
    try:
        data = _get_json(url, rate_limiter=limiter)
    except (HTTPError, URLError, OSError, ValueError):
        return [], 1
    return [_normalise_openalex_work(w) for w in (data.get("results") or [])], 1


def _crossref_title_search(
    title: str, mailto: Optional[str], limiter: RateLimiter
) -> tuple[list[dict[str, Any]], int]:
    """GET Crossref ``?query.bibliographic=<title>`` and normalise items.

    HTTP/transport errors are swallowed (returning no candidates) so a single
    unparseable or rejected title query can never abort the whole assured run.
    """
    url = f"{CROSSREF_BASE}?query.bibliographic={quote(title)}&rows=20"
    if mailto:
        url += f"&mailto={quote(mailto)}"
    try:
        data = _get_json(url, rate_limiter=limiter)
    except (HTTPError, URLError, OSError, ValueError):
        return [], 1
    items = data.get("message", {}).get("items") or []
    return [_normalise_crossref_item(it) for it in items if isinstance(it, dict)], 1


def _best_title_match(
    works: list[dict[str, Any]], norm_title: str, year: Optional[int]
) -> Optional[dict[str, Any]]:
    """Return the first work whose normalised title equals *norm_title* and whose
    year is within +/-1 of *year* (conservative, avoids false merges)."""
    for work in works:
        wt = normalise_title(work.get("title") or "")
        if wt != norm_title:
            continue
        wy = work.get("year")
        if year is not None and wy is not None:
            try:
                if abs(int(year) - int(wy)) > 1:
                    continue
            except (TypeError, ValueError):
                pass
        return work
    return None


def _resolve_by_title(
    ref: dict[str, Any], mailto: Optional[str], limiter: RateLimiter
) -> tuple[Optional[dict[str, Any]], int]:
    """Resolve a DOI-less reference by title + year via OpenAlex then Crossref.

    Conservative: only accept a candidate whose normalised title is EQUAL and
    whose publication year is within +/-1 of the reference year. Returns
    ``(normalised reference dict or None, api_calls_used)``.
    """
    title = (ref.get("ref_title") or ref.get("title") or "").strip()
    if not title:
        return None, 0
    year = ref.get("ref_year")
    norm_title = normalise_title(title)
    works, used = _openalex_title_search(norm_title, mailto, limiter)
    match = _best_title_match(works, norm_title, year)
    if match:
        return match, used
    works2, used2 = _crossref_title_search(title, mailto, limiter)
    return _best_title_match(works2, norm_title, year), used + used2


def _resolve_doi_less_references(
    conn: Any,
    rows: list[dict[str, Any]],
    mailto: Optional[str],
    limiter: RateLimiter,
    max_api_calls: Optional[int],
    api_calls: int,
    stats: StatsDict,
) -> int:
    """Resolve DOI-less references by title search (conservative).

    A DOI-less reference WITH a title is searched via :func:`_resolve_by_title`;
    on a confident match it is inserted/linked and set ``status='resolved'``. A
    DOI-less reference WITHOUT a title has nothing to recover and is set
    ``status='unresolved_no_doi'``. A title search that finds no confident
    match is set ``status='unresolved_title_failed'``. Nothing is silently
    dropped.
    """
    if not rows:
        return api_calls
    source_id = _ensure_source_snowball(conn)
    for row in rows:
        if max_api_calls is not None and api_calls >= max_api_calls:
            print(f"  API budget reached ({max_api_calls}); stopping title resolution.")
            break
        title = (row["ref_title"] or "").strip()
        if not title:
            _set_ref_status(conn, row["id"], "unresolved_no_doi")
            continue
        meta, used = _resolve_by_title(row, mailto, limiter)
        api_calls += used
        stats["api_calls"] = api_calls
        if meta is None:
            _set_ref_status(conn, row["id"], "unresolved_title_failed")
            continue
        paper_id, is_new = _find_or_create_ref_paper(conn, meta, source_id, meta.get("pdf_url"))
        if is_new:
            stats["new_papers"] += 1
        conn.execute(
            "UPDATE reference_lists SET resolved_paper_id = ?, status = 'resolved' WHERE id = ?",
            (paper_id, row["id"]),
        )
        if _create_snowball_edge(conn, paper_id, row["parent_paper_id"], 1):
            stats["edges"] += 1
    conn.commit()
    return api_calls


def _set_ref_status(conn: Any, ref_id: int, status: str) -> None:
    conn.execute("UPDATE reference_lists SET status = ? WHERE id = ?", (status, ref_id))


def _sync_resolved_status(conn: Any) -> None:
    """Promote any row with a linked paper to status='resolved'."""
    conn.execute(
        "UPDATE reference_lists SET status = 'resolved' "
        "WHERE resolved_paper_id IS NOT NULL AND status != 'resolved'"
    )
    conn.commit()


def _status_counts(conn: Any) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM reference_lists GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["c"]) for r in rows}


_UNRESOLVED_REASONS = {
    "pending": "not yet processed",
    "fetch_error": "both sources failed (404/error on Crossref and OpenAlex)",
    "unresolved_no_doi": "no DOI and no title metadata to recover",
    "unresolved_title_failed": "title search found no confident match",
}


def _reason_for(status: str) -> str:
    return _UNRESOLVED_REASONS.get(status, "")


def _print_status_summary(counts: dict[str, int]) -> None:
    total = sum(counts.values())
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"  reference_lists status summary: {parts} (total={total})")


def export_unresolved(conn: Any, path: str) -> int:
    """Write every NON-resolved reference_lists row to *path* as CSV.

    Columns: ref_doi, ref_title, ref_year, source, status, reason. Returns the
    number of rows written (so callers can report "N unresolved -> file").
    """
    rows = conn.execute(
        "SELECT ref_doi, ref_title, ref_year, source, status "
        "FROM reference_lists WHERE status != 'resolved' ORDER BY status, id"
    ).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ref_doi", "ref_title", "ref_year", "source", "status", "reason"])
        for r in rows:
            writer.writerow(
                [r["ref_doi"], r["ref_title"], r["ref_year"], r["source"], r["status"],
                 _reason_for(r["status"])]
            )
    return len(rows)


def verify_retrieval(conn: Any) -> dict[str, Any]:
    """Backfill any reference that now has a matching papers row by DOI.

    For every ``reference_lists`` row that carries a DOI but is not yet
    ``status='resolved'``, check whether a ``papers`` row with that DOI now
    exists (e.g. inserted by a later harvest). If so, link it, create the
    snowball edge and set ``status='resolved'``. Returns per-status counts and
    the list of still-missing DOI'd references. This is the ASSURED backstop
    that guarantees no resolvable reference is silently left pending.
    """
    rows = conn.execute(
        """
        SELECT id, parent_paper_id, ref_doi, ref_title, ref_year
        FROM reference_lists
        WHERE ref_doi IS NOT NULL AND status != 'resolved'
        """
    ).fetchall()
    still_missing: list[dict[str, Any]] = []
    backfilled = 0
    for row in rows:
        nd = normalise_doi(row["ref_doi"])
        if nd is None:
            # Unusable DOI: report it and give it an explicit status.
            still_missing.append(
                {
                    "ref_doi": row["ref_doi"],
                    "ref_title": row["ref_title"],
                    "parent_paper_id": row["parent_paper_id"],
                }
            )
            _set_ref_status(conn, row["id"], "fetch_error")
            continue
        paper = conn.execute(
            "SELECT id FROM papers WHERE lower(doi) = ?", (nd,)
        ).fetchone()
        if paper is None:
            still_missing.append(
                {
                    "ref_doi": row["ref_doi"],
                    "ref_title": row["ref_title"],
                    "parent_paper_id": row["parent_paper_id"],
                }
            )
            continue
        pid = int(paper[0])
        conn.execute(
            "UPDATE reference_lists SET resolved_paper_id = ?, status = 'resolved' WHERE id = ?",
            (pid, row["id"]),
        )
        _create_snowball_edge(conn, pid, row["parent_paper_id"], 1)
        backfilled += 1
    conn.commit()
    return {
        "backfilled": backfilled,
        "still_missing": still_missing,
        "status_counts": _status_counts(conn),
    }


def _retry_assured(
    conn: Any,
    source: str,
    mailto: Optional[str],
    limiter: RateLimiter,
    max_api_calls: Optional[int],
    api_calls: int,
    stats: StatsDict,
) -> int:
    """Re-attempt the rows that still failed, across both sources / via title.

    Re-runs DOI resolution for ``fetch_error`` rows (both sources) and title
    resolution for ``unresolved_title_failed`` rows. After this, any reference
    that remains unresolved is genuinely unobtainable and is reported (never
    silently dropped).
    """
    fetch_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year, ref_authors
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND status = 'fetch_error'
            """
        ).fetchall()
    ]
    api_calls = _batch_resolve_references(
        conn, fetch_rows, source, mailto, limiter, max_api_calls, api_calls, stats
    )

    title_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, parent_paper_id, ref_doi, ref_title, ref_year, ref_authors
            FROM reference_lists
            WHERE resolved_paper_id IS NULL AND status = 'unresolved_title_failed'
            """
        ).fetchall()
    ]
    api_calls = _resolve_doi_less_references(
        conn, title_rows, mailto, limiter, max_api_calls, api_calls, stats
    )
    return api_calls


def _resolve_phase(
    conn: Any,
    direction: Optional[str],
    source: str,
    mailto: Optional[str],
    limiter: RateLimiter,
    max_api_calls: Optional[int],
    api_calls: int,
    stats: StatsDict,
    assured: bool,
    export_path: Optional[str],
) -> int:
    """Run the full resolution pass + (optional) assured backstop and reporting."""
    _sync_resolved_status(conn)

    api_calls = _batch_resolve_references(
        conn, _fetch_unresolved_with_doi(conn, direction), source, mailto,
        limiter, max_api_calls, api_calls, stats,
    )
    api_calls = _resolve_doi_less_references(
        conn, _fetch_unresolved_doi_less(conn, direction), mailto, limiter,
        max_api_calls, api_calls, stats,
    )

    counts = _status_counts(conn)
    stats["status_counts"] = counts

    if assured:
        vr = verify_retrieval(conn)
        print(f"  verify_retrieval backfilled {vr['backfilled']} reference(s) by DOI.")
        api_calls = _retry_assured(
            conn, source, mailto, limiter, max_api_calls, api_calls, stats
        )
        counts = _status_counts(conn)
        stats["status_counts"] = counts

    if export_path:
        n = export_unresolved(conn, export_path)
        stats["exported_unresolved"] = n
        if n:
            print(f"  Exported {n} unresolved reference(s) to {export_path}")

    _print_status_summary(counts)
    return api_calls


# ---------------------------------------------------------------------------
# run logging
# ---------------------------------------------------------------------------

def _insert_run(conn: Any, direction: str, source: str, seed_count: int) -> int:
    cur = conn.execute(
        "INSERT INTO snowball_runs (direction, source, seed_count) VALUES (?, ?, ?)",
        (direction, source, seed_count),
    )
    return cur.lastrowid


def _finish_run(conn: Any, run_id: int, stats: StatsDict) -> None:
    conn.execute(
        """
        UPDATE snowball_runs
        SET references_harvested = ?, new_papers = ?, edges = ?, api_calls = ?,
            finished_at = current_timestamp
        WHERE id = ?
        """,
        (
            stats["references_harvested"],
            stats["new_papers"],
            stats["edges"],
            stats["api_calls"],
            run_id,
        ),
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def harvest_references(
    conn: Any,
    seed_paper_ids: list[int],
    direction: str = "backward",
    source: str = "crossref",
    mailto: Optional[str] = None,
    rate_limiter: Optional[RateLimiter] = None,
    max_api_calls: Optional[int] = None,
    run_row_id: Optional[int] = None,
    resolve: bool = True,
    assured: bool = True,
    export_path: Optional[str] = None,
) -> StatsDict:
    """Phase 1: harvest + store a seed paper's full reference list, then resolve.

    Checks OUR OWN database first (local-first). Stores every reference in
    ``reference_lists`` (inventory completeness), resolves already-known
    references locally, and batch-resolves the remaining DOI-bearing references
    via the chosen external source. Logs the run to ``snowball_runs``.
    """
    limiter = rate_limiter or RateLimiter(min_interval=DEFAULT_MIN_INTERVAL)
    seed_paper_ids = (
        list(dict.fromkeys(int(pid) for pid in seed_paper_ids))
        if seed_paper_ids
        else []
    )
    run_id = (
        run_row_id
        if run_row_id is not None
        else _insert_run(conn, direction, source, len(seed_paper_ids))
    )
    stats: StatsDict = {
        "seeds": len(seed_paper_ids),
        "references_harvested": 0,
        "new_papers": 0,
        "edges": 0,
        "api_calls": 0,
        "resolved_local": 0,
        "aborted": 0,
    }
    api_calls = 0
    stop = False

    for seed_id in seed_paper_ids:
        if stop:
            break
        if max_api_calls is not None and api_calls >= max_api_calls:
            print(f"  API budget reached ({max_api_calls}); stopping harvest.")
            stop = True
            break
        row = conn.execute(
            "SELECT doi, title FROM papers WHERE id = ?", (seed_id,)
        ).fetchone()
        if not row:
            continue
        seed_doi, seed_title = row[0], row[1]
        budget = None if max_api_calls is None else max_api_calls - api_calls
        try:
            refs, used = _fetch_seed_reference_list(
                conn, seed_id, seed_doi, seed_title, direction, source,
                limiter, mailto, budget,
            )
        except RateLimitError as exc:
            stats["aborted"] = 1
            print(f"  Stopping harvest (rate limit): {exc}")
            stop = True
            break
        except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError) as exc:
            print(f"  Error fetching refs for seed {seed_id}: {exc!r}")
            continue

        api_calls += used
        stats["api_calls"] = api_calls

        seen: set[tuple[str, str]] = set()
        for idx, ref in enumerate(refs):
            ref_doi = normalise_doi(ref.get("doi"))
            unstructured = (ref.get("unstructured") or "").strip()
            key = (ref_doi or "", unstructured)
            if key in seen:
                continue
            seen.add(key)
            local_id = local_find_paper(conn, ref.get("doi"), ref.get("title"))
            _upsert_reference_list(
                conn, seed_id, direction, ref, local_id, source, idx
            )
            stats["references_harvested"] += 1
            if local_id is not None:
                if _create_snowball_edge(conn, local_id, seed_id, 1):
                    stats["edges"] += 1
                stats["resolved_local"] += 1
        conn.commit()

    if resolve:
        try:
            api_calls = _resolve_phase(
                conn,
                direction,
                source,
                mailto,
                limiter,
                max_api_calls,
                api_calls,
                stats,
                assured,
                export_path,
            )
        except RateLimitError:
            stats["aborted"] = 1
            print("  API unavailable (rate limit); stopping resolution gracefully.")
            if export_path:
                n = export_unresolved(conn, export_path)
                stats["exported_unresolved"] = n
                if n:
                    print(f"  Exported {n} unresolved reference(s) to {export_path}")
            _print_status_summary(_status_counts(conn))
        stats["api_calls"] = api_calls

    _finish_run(conn, run_id, stats)
    conn.commit()
    return stats


def resolve_reference_lists(
    conn: Any,
    source: str = "crossref",
    mailto: Optional[str] = None,
    rate_limiter: Optional[RateLimiter] = None,
    max_api_calls: Optional[int] = None,
    assured: bool = True,
    export_path: Optional[str] = None,
) -> StatsDict:
    """Phase 2: resolve every still-unresolved reference.

    Resolves DOI-bearing references (with a Crossref<->OpenAlex retry) and
    DOI-less references (via conservative title search), then -- when
    *assured* is True -- runs :func:`verify_retrieval` and re-attempts the
    still-failing rows. Every outcome is recorded in ``reference_lists.status``
    and the non-resolved rows are exported to *export_path* (CLI:
    ``--export-unresolved``).
    """
    limiter = rate_limiter or RateLimiter(min_interval=DEFAULT_MIN_INTERVAL)
    run_id = _insert_run(conn, "resolve", source, 0)
    stats: StatsDict = {
        "seeds": 0,
        "references_harvested": 0,
        "new_papers": 0,
        "edges": 0,
        "api_calls": 0,
        "resolved_local": 0,
        "aborted": 0,
    }
    api_calls = 0
    try:
        api_calls = _resolve_phase(
            conn, None, source, mailto, limiter, max_api_calls, 0, stats,
            assured, export_path,
        )
    except RateLimitError:
        stats["aborted"] = 1
        print("  API unavailable (rate limit); stopping resolution gracefully.")
        if export_path:
            n = export_unresolved(conn, export_path)
            stats["exported_unresolved"] = n
            if n:
                print(f"  Exported {n} unresolved reference(s) to {export_path}")
        _print_status_summary(_status_counts(conn))
    stats["api_calls"] = api_calls
    _finish_run(conn, run_id, stats)
    conn.commit()
    return stats


def get_reference_inventory(
    conn: Any, parent_paper_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """Return reference_lists rows (optionally for one parent paper)."""
    if parent_paper_id is None:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, direction, ref_index, ref_doi, ref_title,
                   ref_year, ref_authors, ref_unstructured, resolved_paper_id,
                   source, discovered_at
            FROM reference_lists
            ORDER BY parent_paper_id, direction, ref_index
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, parent_paper_id, direction, ref_index, ref_doi, ref_title,
                   ref_year, ref_authors, ref_unstructured, resolved_paper_id,
                   source, discovered_at
            FROM reference_lists
            WHERE parent_paper_id = ?
            ORDER BY direction, ref_index
            """,
            (parent_paper_id,),
        ).fetchall()
    return [dict(r) for r in rows]
