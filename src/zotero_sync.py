"""Optional, graceful Zotero bulk-download bridge for snowball results.

This module is intentionally dependency-light: ``pyzotero`` is imported lazily
inside the functions, so the rest of the pipeline never requires it. If Zotero
is not installed or not configured, the functions print a clear message and
return 0 instead of raising.

Usage:
    from src.zotero_sync import collect_dois_for_zotero, push_dois_to_zotero

    with get_connection("results.db") as conn:
        push_dois_to_zotero(conn, paper_ids=[12, 44, 91])
"""

from __future__ import annotations

import os
import re
import difflib

from typing import Any, Optional

try:  # pragma: no cover - optional dependency
    from pyzotero import Zotero
except Exception:  # noqa: BLE001 - ImportError or any init failure
    Zotero = None  # type: ignore[assignment]


def collect_dois_for_zotero(
    conn: Any, paper_ids: Optional[list[int]] = None
) -> list[str]:
    """Return the DOIs of papers (optionally restricted to *paper_ids*)."""
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            f"SELECT doi FROM papers WHERE doi IS NOT NULL AND id IN ({placeholders})",
            list(paper_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doi FROM papers WHERE doi IS NOT NULL"
        ).fetchall()
    return [row[0] for row in rows if row[0]]


def _build_template(conn: Any, doi: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT title, authors, year, publication_title, doi FROM papers WHERE doi = ?",
        (doi,),
    ).fetchone()
    if not row:
        return None
    title, authors, year, publication_title, _ = row
    creators = []
    if authors:
        for name in str(authors).split(";"):
            name = name.strip()
            if name:
                creators.append({"creatorType": "author", "name": name})
    template: dict[str, Any] = {
        "itemType": "journalArticle",
        "title": title or "",
        "creators": creators,
        "date": str(year) if year else "",
        "publicationTitle": publication_title or "",
        "DOI": doi,
    }
    return template


def _ensure_collection(zotero: Any, collection_name: str) -> Optional[str]:
    try:
        names = zotero.collection_names()
        if collection_name in names:
            return names[collection_name]
        created = zotero.create_collections(
            [{"name": collection_name}]
        )
        if created:
            return created[0].get("key")
    except Exception as exc:  # noqa: BLE001 - Zotero API can raise anything
        print(f"  Zotero collection lookup failed ({exc}); importing without collection.")
    return None


def push_dois_to_zotero(
    conn: Any,
    paper_ids: Optional[list[int]] = None,
    collection_name: str = "PUF_RA_snowball",
    zotero: Any = None,
    batch_size: int = 50,
) -> int:
    """Push papers (by DOI) to Zotero for bulk PDF download.

    Builds a ``journalArticle`` template per paper and batch-creates them via
    ``pyzotero.Zotero.create_items`` in chunks of *batch_size*. Returns the
    number of items pushed. If ``pyzotero`` is unavailable or no Zotero client
    is configured, prints a clear message and returns 0 (never crashes).
    """
    if zotero is None and Zotero is None:
        print(
            "pyzotero is not installed; skipping Zotero sync "
            "(install with `pip install pyzotero` to enable)."
        )
        return 0

    if zotero is None:
        library_id = os.environ.get("ZOTERO_LIBRARY_ID")
        library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")
        api_key = os.environ.get("ZOTERO_API_KEY")
        if not (library_id and api_key):
            print(
                "Zotero is not configured (set ZOTERO_LIBRARY_ID, "
                "ZOTERO_LIBRARY_TYPE, ZOTERO_API_KEY); skipping sync."
            )
            return 0
        zotero = Zotero(library_id, library_type, api_key)

    collection_key = _ensure_collection(zotero, collection_name)

    dois = collect_dois_for_zotero(conn, paper_ids)
    if not dois:
        print("No DOIs to push to Zotero.")
        return 0

    pushed = 0
    for start in range(0, len(dois), batch_size):
        batch = dois[start : start + batch_size]
        templates = []
        for doi in batch:
            template = _build_template(conn, doi)
            if template:
                templates.append(template)
        if not templates:
            continue
        try:
            if collection_key:
                zotero.create_items(templates, collection_key)
            else:
                zotero.create_items(templates)
            pushed += len(templates)
        except Exception as exc:  # noqa: BLE001 - network / API errors
            print(f"  Zotero push failed for a batch ({exc}); stopping.")
            break

    print(f"Pushed {pushed} DOIs to Zotero collection '{collection_name}'.")
    return pushed


def _normalise_zotero_item(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build a ``{doi,title,authors,year,abstract}`` dict from a Zotero item.

    ``creators`` are rendered as "Given Family" and joined with "; "; the year
    is parsed from the ``date`` field; ``abstractNote`` becomes ``abstract``.
    Returns ``None`` when *data* is unusable.
    """
    if not isinstance(data, dict):
        return None
    title = data.get("title") or ""
    creators = data.get("creators") or []
    authors = "; ".join(
        f"{c.get('given', '').strip()} {c.get('family', '').strip()}".strip()
        for c in creators
        if isinstance(c, dict)
    )
    year = None
    date = data.get("date") or ""
    match = re.search(r"\d{4}", str(date))
    if match:
        try:
            year = int(match.group(0))
        except ValueError:
            year = None
    abstract = data.get("abstractNote") or ""
    return {
        "doi": data.get("DOI"),
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "publication_title": data.get("publicationTitle") or "",
    }


def _build_zotero_client() -> Any:
    """Construct a Zotero client from the environment, or return None.

    Never raises: if ``pyzotero`` is missing or the ``ZOTERO_*`` env vars are
    unset, ``None`` is returned so the caller can treat Zotero as unavailable.
    """
    if Zotero is None:
        return None
    library_id = os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")
    api_key = os.environ.get("ZOTERO_API_KEY")
    if not (library_id and api_key):
        return None
    try:
        return Zotero(library_id, library_type, api_key)
    except Exception:  # noqa: BLE001 - Zotero init can raise anything
        return None


def lookup_doi_in_zotero(
    doi: str, zotero: Any = None, qmode: str = "everything"
) -> Optional[dict[str, Any]]:
    """Look up a single DOI in the user's local Zotero library.

    Queries the user's Zotero library via ``zotero.items(query=doi)`` with
    ``qmode="everything"`` and returns a normalised dict
    ``{doi, title, authors, year, abstract}`` for the item whose DOI matches
    *doi*. Returns ``None`` when Zotero is not configured, ``pyzotero`` is
    missing, no item matches, or the lookup fails for any reason. Never raises.
    """
    if zotero is None:
        zotero = _build_zotero_client()
    if zotero is None:
        return None

    from src.snowball import normalise_doi

    try:
        items = zotero.items(query=doi, qmode=qmode)
    except Exception:  # noqa: BLE001 - the Zotero API can raise anything
        return None

    norm_doi = normalise_doi(doi)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        item_doi = normalise_doi(data.get("DOI"))
        if norm_doi and item_doi and item_doi != norm_doi:
            continue
        return _normalise_zotero_item(data)
    return None


def build_library_doi_index(zotero: Any = None) -> dict[str, dict[str, Any]]:
    """Fetch the user's entire Zotero library ONCE and build a DOI->metadata index.

    Returns ``{normalised_doi: {title, authors, year, abstract, publication_title}}``.
    Returns ``{}`` when ``pyzotero`` is unavailable or ``ZOTERO_LIBRARY_ID`` /
    ``ZOTERO_API_KEY`` are unset. Never raises: a network/API failure yields an empty
    index so the caller falls back to the per-DOI chain. The index is built from a
    single local library read, so it is instant and rate-limit immune.
    """
    if zotero is None:
        zotero = _build_zotero_client()
    if zotero is None:
        return {}
    try:
        from src.snowball import normalise_doi
        items = zotero.everything(zotero.items())
    except Exception:  # noqa: BLE001 - the Zotero API can raise anything
        return {}
    index: dict[str, dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        doi = normalise_doi(data.get("DOI"))
        if not doi:
            continue
        meta = _normalise_zotero_item(data)
        if meta is None:
            continue
        index[doi] = {
            "title": meta.get("title", ""),
            "authors": meta.get("authors", ""),
            "year": meta.get("year"),
            "abstract": meta.get("abstract", ""),
            "publication_title": meta.get("publication_title", ""),
        }
    return index


def lookup_doi_in_zotero_batch(
    dois: list[str], zotero: Any = None, qmode: str = "everything"
) -> list[dict[str, Any]]:
    """Resolve many DOIs against the local Zotero library in a single library fetch.

    Builds the in-memory DOI index via :func:`build_library_doi_index` (one local
    read of the whole library) and returns a list of normalised work dicts (same
    shape as :func:`lookup_doi_in_zotero`: doi/title/authors/year/abstract/
    publication_title/unstructured/pdf_url) for every supplied DOI present in the
    library. If the index cannot be built, falls back to calling
    :func:`lookup_doi_in_zotero` per DOI (still a local, instant read). Never raises.
    """
    from src.snowball import normalise_doi

    index = build_library_doi_index(zotero=zotero)
    if not index:
        # Fall back to per-DOI lookups (local, instant) when the bulk fetch fails.
        out: list[dict[str, Any]] = []
        for doi in dois:
            meta = lookup_doi_in_zotero(doi, zotero=zotero, qmode=qmode)
            if meta:
                out.append(meta)
        return out
    out: list[dict[str, Any]] = []
    for doi in dois:
        nd = normalise_doi(doi)
        if not nd:
            continue
        meta = index.get(nd)
        if not meta:
            continue
        out.append(
            {
                "doi": nd,
                "title": meta.get("title", ""),
                "authors": meta.get("authors", ""),
                "year": meta.get("year"),
                "abstract": meta.get("abstract", ""),
                "publication_title": meta.get("publication_title", ""),
                "unstructured": "",
                "pdf_url": None,
            }
        )
    return out


def fetch_abstract_via_zotero(
    doi: str, mailto: Optional[str] = None, limiter: Any = None, zotero: Any = None
) -> Optional[str]:
    """Return the abstract (``abstractNote``) for *doi* from Zotero, or None.

    *mailto* and *limiter* are accepted for call-compatibility with the other
    abstract fetchers but are unused (Zotero is a local read, not a rate-limited
    API). Returns ``None`` when Zotero is unavailable or the DOI is not found.
    Never raises.
    """
    item = lookup_doi_in_zotero(doi, zotero=zotero)
    if not item:
        return None
    abstract = item.get("abstract")
    return abstract if abstract else None

def _title_match_ratio(a: str, b: str) -> float:
    """Tolerant title similarity in [0, 1] (NFKC + punctuation/space collapse)."""
    return difflib.SequenceMatcher(None, normalise_title(a), normalise_title(b)).ratio()


def search_title_in_zotero(title: str) -> Optional[dict[str, Any]]:
    """Find a Zotero library item by tolerant title match (rate-limit immune).

    Builds the local library index once via ``zotero.everything(zotero.items())``
    (the same client pattern as :func:`build_library_doi_index`) and returns a
    normalised ``{doi, title, authors, year, abstract, publication_title}`` dict for
    the item whose normalised title best matches *title*. Returns ``None`` when
    pyzotero is missing, ``ZOTERO_*`` is unset, or no item matches. Never raises:
    a network/API failure yields ``None`` so the caller falls back to other sources.
    """
    if not title:
        return None
    try:
        from src.snowball import normalise_title
    except Exception:  # noqa: BLE001
        return None
    try:
        zotero = _build_zotero_client()
        if zotero is None:
            return None
        items = zotero.everything(zotero.items())
    except Exception:  # noqa: BLE001 - the Zotero API can raise anything
        return None
    best: Optional[dict[str, Any]] = None
    best_ratio = 0.0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        meta = _normalise_zotero_item(data)
        if meta is None:
            continue
        t = meta.get("title") or ""
        if not t:
            continue
        ratio = _title_match_ratio(t, title)
        if ratio >= 0.85 and ratio > best_ratio:
            best = meta
            best_ratio = ratio
    if best is None:
        return None
    return {
        "doi": best.get("doi"),
        "title": best.get("title", ""),
        "authors": best.get("authors", ""),
        "year": best.get("year"),
        "abstract": best.get("abstract", ""),
        "publication_title": best.get("publication_title", ""),
    }
