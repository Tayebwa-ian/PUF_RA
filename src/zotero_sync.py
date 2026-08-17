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
        library_id = __import__("os").environ.get("ZOTERO_LIBRARY_ID")
        library_type = __import__("os").environ.get("ZOTERO_LIBRARY_TYPE", "user")
        api_key = __import__("os").environ.get("ZOTERO_API_KEY")
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
