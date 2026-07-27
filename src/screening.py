"""LLM screening module.

Refactored from the original run_llm_screening.py.
Screens papers using an OpenAI-compatible LLM API and stores decisions
in the database.

Usage:
    from src.screening import run_screening, query_llm

    with get_connection("puf.db") as conn:
        run_screening(
            conn,
            model="qwen3-next-80b-a3b-instruct",
            api_key="...",
            base_url="https://...",
            query_ids=[3, 4],
            max_retries=3,
        )
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import textwrap
from typing import Any, Optional

import openai

from src.db import get_connection

# ---------------------------------------------------------------------------
# HTML / quote normalisation (moved from original run_llm_screening.py)
# ---------------------------------------------------------------------------

ELISION = re.compile(r"\s*(?:\[\.\.\.\]|\.{3,}|…)\s*")
TRAILING_ELLIPSIS = re.compile(r"[\s.…]+$")
QUOTE_MAP = str.maketrans({
    "\u201c": '"', "\u201d": '"',
    "\u2018": "'", "\u2019": "'",
    "\u201e": '"', "\u201a": "'",
    "\u00ab": '"', "\u00bb": '"',
})


def _normalize(s: str) -> str:
    s = s.replace("\u00a0", " ").translate(QUOTE_MAP)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_excerpt(excerpt: str, abstract: str) -> bool:
    """Verify that the LLM's excerpt is actually present in the abstract.

    Handles elision (e.g. '[...]') and quote normalisation.

    Args:
        excerpt: Text excerpt claimed to be from the abstract.
        abstract: The full abstract text.

    Returns:
        True if all non-elided fragments are found in order.
    """
    excerpt = TRAILING_ELLIPSIS.sub("", excerpt)
    norm_abstract = _normalize(abstract).lower()
    pos = 0
    for frag in ELISION.split(excerpt):
        frag = _normalize(frag).lower()
        if not frag:
            continue
        idx = norm_abstract.find(frag, pos)
        if idx == -1:
            return False
        pos = idx + len(frag)
    return True


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

def query_llm(
    client: openai.OpenAI,
    model_name: str,
    system_prompt: str,
    title: str,
    abstract: str,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], int]:
    """Query the LLM for a screening decision on a paper.

    Args:
        client: OpenAI-compatible client.
        model_name: Model identifier.
        system_prompt: System prompt for the screening task.
        title: Paper title.
        abstract: Paper abstract.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (result_dict, tokens_used).
        result_dict must contain 'decision', 'criterion', 'justification', 'excerpt'.

    Raises:
        RuntimeError: If the response cannot be parsed.
    """
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Title: '{title}'\nAbstract: '{abstract}'"},
        ],
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    result = json.loads(response.choices[0].message.content)
    token_usage = response.usage.total_tokens
    return result, token_usage


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_papers(
    conn: sqlite3.Connection,
    query_ids: list[int],
) -> list[tuple[int, str, str]]:
    """Yield (paper_id, title, abstract) for papers matching query IDs.

    Unescapes HTML entities in title and abstract.
    """
    placeholders = ",".join("?" for _ in query_ids)
    query = textwrap.dedent(f"""
        SELECT id, title, abstract
        FROM papers
        WHERE query_id IN ({placeholders})
        ORDER BY id
    """)
    papers = []
    for paper_id, title, abstract in conn.execute(query, query_ids):
        papers.append((paper_id, html.unescape(title), html.unescape(abstract)))
    return papers


def insert_run(
    conn: sqlite3.Connection,
    model: str,
    prompt_text: str,
    misc: str = "",
) -> int:
    """Insert a new screening run and return its ID."""
    query = textwrap.dedent("""
        INSERT INTO runs (model, prompt_text, misc)
        VALUES (?, ?, ?)
        RETURNING id
    """)
    (run_id,) = conn.execute(query, (model, prompt_text, misc)).fetchone()
    return run_id


def insert_decision(
    conn: sqlite3.Connection,
    run_id: int,
    paper_id: int,
    decision: str,
    criterion: str,
    justification: str,
    excerpt: str,
    excerpt_verified: bool,
    tokens_used: int,
) -> int:
    """Insert a screening decision for a paper."""
    query = textwrap.dedent("""
        INSERT INTO decisions
            (run_id, paper_id, decision, criterion, justification, excerpt, excerpt_verified, tokens_used)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """)
    (decision_id,) = conn.execute(
        query,
        (run_id, paper_id, decision, criterion, justification, excerpt, excerpt_verified, tokens_used),
    ).fetchone()
    return decision_id


# ---------------------------------------------------------------------------
# Main screening logic
# ---------------------------------------------------------------------------

def run_screening(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    system_prompt: str,
    query_ids: list[int],
    max_retries: int = 3,
    dry_run: bool = False,
) -> int:
    """Run LLM screening on papers matching query_ids.

    Args:
        conn: SQLite connection.
        client: OpenAI-compatible client.
        model: Model identifier.
        system_prompt: System prompt for screening.
        query_ids: List of query IDs to screen papers from.
        max_retries: Maximum retry attempts per paper.
        dry_run: If True, print actions without calling the API.

    Returns:
        The run_id.
    """
    run_id = insert_run(conn, model, system_prompt, "")
    print(f"Started screening run {run_id}.")

    papers = get_papers(conn, query_ids)
    print(f"Screening {len(papers)} papers...")

    for paper_id, title, abstract in papers:
        if dry_run:
            print(f"[DRY RUN] Would screen paper {paper_id}: {title}")
            continue

        for attempt in range(max_retries):
            try:
                res, tokens_used = query_llm(
                    client, model, system_prompt, title, abstract
                )
                excerpt = res.get("excerpt", "")
                excerpt_verified = verify_excerpt(excerpt, abstract)
                decision = res.get("decision", "EXCLUDE")
                criterion = res.get("criterion", "")
                justification = res.get("justification", "")

                insert_decision(
                    conn, run_id, paper_id,
                    decision, criterion, justification,
                    excerpt, excerpt_verified, tokens_used,
                )
                print(
                    f"  Paper {paper_id}: {decision} "
                    f"(verified={excerpt_verified}, tokens={tokens_used})"
                )
                break
            except Exception as exc:
                print(f"  attempt {attempt + 1}/{max_retries} failed: {exc}")
        else:
            raise RuntimeError(
                f"Paper {paper_id} failed after {max_retries} attempts"
            )

        conn.commit()

    print(f"Screening run {run_id} complete.")
    return run_id
