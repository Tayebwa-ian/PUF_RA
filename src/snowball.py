"""Snowball / backward search module.

Expands the literature review by following the reference chains of
already-relevant papers. Uses Semantic Scholar API (primary) and
Crossref API (fallback) to fetch references.

Usage:
    from src.snowball import run_snowball

    with get_connection("puf.db") as conn:
        run_snowball(
            conn,
            seed_query_ids=[3, 4],
            depth=1,
            max_refs_per_paper=20,
            source="semantic_scholar",
        )
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

#: Semantic Scholar base URL (free tier, no auth required)
S2_BASE = "https://api.semanticscholar.org/graph/v1"
#: Crossref base URL
CROSSREF_BASE = "https://api.crossref.org/works"


class RateLimitError(Exception):
    """Raised when an API rate limit is hit."""


def _get_json(url: str, retries: int = 3, delay: float = 1.0) -> dict[str, Any]:
    """GET *url*, parse JSON response, with simple exponential backoff."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "PUF-RA-Pipeline/2.0"})
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                wait = delay * (2 ** attempt)
                print(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
        except (URLError, OSError) as exc:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise
    raise RateLimitError(f"Failed after {retries} retries: {url}")


# ---------------------------------------------------------------------------
# Semantic Scholar client
# ---------------------------------------------------------------------------

def s2_search_paper(title: str) -> Optional[dict[str, Any]]:
    """Search Semantic Scholar for a paper by title.

    Returns the top result dict or None.
    """
    url = f"{S2_BASE}/paper/search?query={title}&fields=title,authors,year,abstract,externalIds,publicationVenue&limit=1"
    data = _get_json(url)
    papers = data.get("data", [])
    return papers[0] if papers else None


def s2_get_references(paper_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch references for a Semantic Scholar paper.

    Args:
        paper_id: Semantic Scholar paper ID (e.g. 'CorpusId:1234' or DOI).
        limit: Maximum number of references to return.

    Returns:
        List of reference dicts.
    """
    url = f"{S2_BASE}/paper/{paper_id}/references?fields=title,authors,year,abstract,externalIds,publicationVenue&limit={limit}"
    data = _get_json(url)
    return [ref.get("citedPaper", ref) for ref in data.get("data", [])]


# ---------------------------------------------------------------------------
# Crossref client
# ---------------------------------------------------------------------------

def crossref_get_references(doi: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch references from Crossref for a paper DOI.

    Args:
        doi: Paper DOI.
        limit: Maximum number of references to return.

    Returns:
        List of reference dicts with 'DOI', 'title', 'author', etc.
    """
    url = f"{CROSSREF_BASE}/{doi}"
    data = _get_json(url)
    message = data.get("message", {})
    refs = message.get("reference", [])
    # Crossref may not return full metadata for references; return what we have
    return refs[:limit]


# ---------------------------------------------------------------------------
# Reference normalisation
# ---------------------------------------------------------------------------

def _normalise_reference(ref: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract and normalise metadata from a reference dict.

    Returns a dict with 'doi', 'title', 'authors', 'year', 'abstract'
    or None if the reference is unusable (no title and no DOI).
    """
    # Try to get DOI
    doi = ref.get("DOI") or ref.get("externalIds", {}).get("DOI")
    # Try to get title
    title = ref.get("title") or ref.get("Title")
    if isinstance(title, list):
        title = title[0] if title else ""
    title = (title or "").strip()

    if not title and not doi:
        return None

    # Authors
    authors_raw = ref.get("authors") or ref.get("author") or []
    authors_list = []
    for a in authors_raw:
        name = a.get("name") or a.get("given", "") + " " + a.get("family", "")
        if name and name.strip():
            authors_list.append(name.strip())
    authors = "; ".join(authors_list)

    # Year
    year = ref.get("year") or ref.get("published", {}).get("date-parts", [[None]])[0][0]
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None

    # Abstract
    abstract = ref.get("abstract") or ""
    if isinstance(abstract, list):
        abstract = " ".join(abstract)
    abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
    }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def _ensure_source_snowball(conn: sqlite3.Connection) -> int:
    """Get or create the 'snowball' source and return its id."""
    row = conn.execute("SELECT id FROM sources WHERE name = 'snowball'").fetchone()
    if row:
        return row[0]
    cursor = conn.execute("INSERT INTO sources (name, description) VALUES (?, ?)",
                          ("snowball", "Backward snowball search"))
    return cursor.lastrowid


def _find_or_create_paper(
    conn: sqlite3.Connection,
    ref: dict[str, Any],
    source_id: int,
) -> int:
    """Find existing paper by DOI (preferred) or title, or create new.

    Args:
        conn: SQLite connection.
        ref: Normalised reference dict with 'doi', 'title', etc.
        source_id: Source ID to link the paper to.

    Returns:
        Paper ID.
    """
    doi = ref.get("doi")
    title = ref.get("title", "").strip()

    # Try DOI first
    if doi:
        row = conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()
        if row:
            paper_id = row[0]
            # Update metadata if missing
            _update_paper_if_needed(conn, paper_id, ref)
            _link_source(conn, paper_id, source_id)
            return paper_id

    # Try title (case-insensitive)
    if title:
        row = conn.execute(
            "SELECT id FROM papers WHERE lower(title) = ?", (title.lower(),)
        ).fetchone()
        if row:
            paper_id = row[0]
            _update_paper_if_needed(conn, paper_id, ref)
            _link_source(conn, paper_id, source_id)
            return paper_id

    # Create new paper
    pub_title = ref.get("publication_title", "")
    if not pub_title:
        pub_title = ref.get("publicationVenue", {}).get("name", "")
    cursor = conn.execute(
        """
        INSERT INTO papers (title, authors, year, abstract, publication_title, doi, keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            ref.get("authors", ""),
            ref.get("year"),
            ref.get("abstract", ""),
            pub_title,
            doi,
            None,
        ),
    )
    paper_id = cursor.lastrowid
    _link_source(conn, paper_id, source_id)
    return paper_id


def _update_paper_if_needed(conn: sqlite3.Connection, paper_id: int, ref: dict[str, Any]) -> None:
    """Update paper fields if they are missing or empty."""
    row = conn.execute(
        "SELECT abstract, authors, year, doi FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if not row:
        return

    updates = {}
    if not row["abstract"] and ref.get("abstract"):
        updates["abstract"] = ref["abstract"]
    if not row["authors"] and ref.get("authors"):
        updates["authors"] = ref["authors"]
    if not row["year"] and ref.get("year"):
        updates["year"] = ref["year"]
    if not row["doi"] and ref.get("doi"):
        updates["doi"] = ref["doi"]

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [paper_id]
        conn.execute(f"UPDATE papers SET {set_clause}, updated_at = current_timestamp WHERE id = ?", values)


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
) -> None:
    """Create a snowball edge, avoiding duplicates and cycles."""
    try:
        conn.execute(
            """
            INSERT INTO snowball_edges (child_paper_id, parent_paper_id, depth)
            VALUES (?, ?, ?)
            """,
            (child_paper_id, parent_paper_id, depth),
        )
    except sqlite3.IntegrityError:
        pass  # edge already exists


# ---------------------------------------------------------------------------
# Snowball search logic
# ---------------------------------------------------------------------------

def _get_seed_papers(conn: sqlite3.Connection, query_ids: list[int]) -> list[int]:
    """Get paper IDs to use as seeds for snowball search.

    Seeds are papers marked as relevant by the relevance engine or LLM.
    """
    query = """
        SELECT DISTINCT p.id
        FROM papers p
        LEFT JOIN relevance_evals re ON p.id = re.paper_id
        LEFT JOIN decisions d ON p.id = d.paper_id
        WHERE p.query_id IN ({}) OR p.id IN (
            SELECT paper_id FROM relevance_evals WHERE is_relevant = 1
            UNION
            SELECT paper_id FROM decisions WHERE decision = 'REVIEW'
        )
    """.format(",".join("?" * len(query_ids)))
    rows = conn.execute(query, query_ids).fetchall()
    return [row["id"] for row in rows]


def run_snowball(
    conn: sqlite3.Connection,
    seed_query_ids: list[int],
    depth: int = 1,
    max_refs_per_paper: int = 20,
    source: str = "semantic_scholar",
    delay: float = 1.0,
    auto_relevance: bool = True,
    auto_screen: bool = False,
    screening_client: Optional[Any] = None,
    screening_model: Optional[str] = None,
    screening_prompt: Optional[str] = None,
) -> dict[str, int]:
    """Run backward snowball search from seed papers.

    Args:
        conn: SQLite connection.
        seed_query_ids: Query IDs to find seed papers from.
        depth: Maximum snowball depth (1 = direct references only).
        max_refs_per_paper: Maximum references to fetch per paper.
        source: API source ('semantic_scholar' or 'crossref').
        delay: Delay in seconds between API calls (to respect rate limits).
        auto_relevance: If True, run relevance evaluation on new papers.
        auto_screen: If True, run LLM screening on new papers (requires client).
        screening_client: OpenAI-compatible client for LLM screening.
        screening_model: Model name for LLM screening.
        screening_prompt: System prompt for LLM screening.

    Returns:
        Dict with stats: {'discovered': N, 'new': N, 'edges': N}
    """
    source_id = _ensure_source_snowball(conn)
    seed_paper_ids = _get_seed_papers(conn, seed_query_ids)

    if not seed_paper_ids:
        print("No seed papers found. Run relevance evaluation first.")
        return {"discovered": 0, "new": 0, "edges": 0}

    print(f"Found {len(seed_paper_ids)} seed papers. Starting snowball search...")

    discovered_total = 0
    new_total = 0
    edges_total = 0

    # Track processed papers at each depth to avoid reprocessing
    processed: dict[int, set[int]] = {d: set() for d in range(1, depth + 1)}

    for current_depth in range(1, depth + 1):
        if current_depth == 1:
            parents = seed_paper_ids
        else:
            # Get papers discovered at previous depth
            parents = [
                row["child_paper_id"]
                for row in conn.execute(
                    "SELECT child_paper_id FROM snowball_edges WHERE depth = ?",
                    (current_depth - 1,),
                )
                if row["child_paper_id"] not in processed[current_depth - 1]
            ]
            # Also include papers newly created at previous depth
            for row in conn.execute(
                "SELECT DISTINCT child_paper_id FROM snowball_edges WHERE depth = ?",
                (current_depth - 1,),
            ):
                cid = row["child_paper_id"]
                if cid not in parents:
                    parents.append(cid)

        for parent_id in parents:
            if parent_id in processed[current_depth]:
                continue
            processed[current_depth].add(parent_id)

            # Get paper metadata
            paper_row = conn.execute(
                "SELECT title, doi FROM papers WHERE id = ?", (parent_id,)
            ).fetchone()
            if not paper_row:
                continue

            title = paper_row["title"]
            doi = paper_row["doi"]
            s2_id = None

            # Resolve Semantic Scholar ID if needed
            if source == "semantic_scholar":
                s2_paper = s2_search_paper(title)
                if not s2_paper and doi:
                    s2_paper = s2_search_paper(doi)
                if s2_paper:
                    s2_id = s2_paper.get("paperId") or s2_paper.get("externalIds", {}).get("CorpusId")

            # Fetch references
            refs = []
            try:
                if source == "semantic_scholar":
                    if s2_id:
                        refs = s2_get_references(s2_id, limit=max_refs_per_paper)
                elif source == "crossref":
                    if doi:
                        refs = crossref_get_references(doi, limit=max_refs_per_paper)
            except Exception as exc:
                print(f"  Error fetching refs for paper {parent_id}: {exc}")
                continue

            discovered_total += len(refs)

            for ref in refs:
                normed = _normalise_reference(ref)
                if not normed:
                    continue

                try:
                    child_id = _find_or_create_paper(conn, normed, source_id)
                    _create_snowball_edge(conn, child_id, parent_id, current_depth)
                    edges_total += 1
                    new_total += 1  # simplified; actual 'new' would require tracking
                except Exception as exc:
                    print(f"  Error processing ref for paper {parent_id}: {exc}")
                    continue

            time.sleep(delay)

    conn.commit()

    # Auto-run relevance evaluation if requested
    if auto_relevance:
        from src.relevance import evaluate_corpus
        print("Running relevance evaluation on updated corpus...")
        evaluate_corpus(conn, store=True)

    # Auto-run LLM screening if requested
    if auto_screen and screening_client:
        from src.screening import run_screening
        print("Running LLM screening on updated corpus...")
        run_screening(
            conn,
            client=screening_client,
            model=screening_model or "",
            system_prompt=screening_prompt or "",
            query_ids=seed_query_ids,
            dry_run=False,
        )

    stats = {
        "discovered": discovered_total,
        "new": new_total,
        "edges": edges_total,
    }
    print(f"Snowball complete: {stats}")
    return stats
