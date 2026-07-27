"""Empirical relevance engine for PUF attack papers.

Scores papers against the topic:
    "Physical attacks on Physical Unclonable Functions (PUFs)"

Uses a hybrid approach:
  - Keyword matching with category-based weights
  - BM25 scoring over the corpus of abstracts

Both methods are deterministic and reproducible.

Usage:
    from src.relevance import evaluate_corpus, TOPIC_KEYWORDS

    results = evaluate_corpus(conn, threshold=0.15)
    # results: list of (paper_id, score, is_relevant, details)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from typing import Any

from src.db import get_connection


# ---------------------------------------------------------------------------
# Topic keyword configuration
# ---------------------------------------------------------------------------

#: Topic keyword set organised by category with weights.
#: Weights reflect domain expertise:
#:   - Core terms (puf, physical unclonable function): high weight
#:   - Attack terms (power analysis, side-channel, etc.): highest weight
#:   - PUF type terms: moderate weight
TOPIC_KEYWORDS: dict[str, dict[str, float]] = {
    "core": {
        "puf": 2.0,
        "physical unclonable function": 2.0,
    },
    "attacks": {
        "power analysis": 3.0,
        "side-channel": 3.0,
        "side channel": 3.0,
        "electromagnetic": 3.0,
        "em analysis": 3.0,
        "probing": 3.0,
        "invasive attack": 3.0,
        "semi-invasive": 3.0,
        "semi invasive": 3.0,
        "delayering": 3.0,
        "cloning": 3.0,
        "modeling attack": 3.0,
        "machine learning attack": 3.0,
        "fault injection": 3.0,
        "laser": 3.0,
        "focused ion beam": 3.0,
        "fib": 3.0,
        "physical attack": 2.5,
    },
    "puf_types": {
        "arbiter puf": 1.5,
        "ring oscillator": 1.5,
        "ro puf": 1.5,
        "sram puf": 1.5,
        "bistable ring": 1.5,
        "puf-based": 1.5,
        "puf based": 1.5,
        "memory puf": 1.5,
        "butterfly puf": 1.5,
    },
}

# Flatten for lookup: keyword -> (category, weight)
_KEYWORD_LOOKUP: dict[str, tuple[str, float]] = {}
for category, keywords in TOPIC_KEYWORDS.items():
    for kw, weight in keywords.items():
        _KEYWORD_LOOKUP[kw] = (category, weight)

# Sort keywords by length descending for greedy matching (longest first)
_SORTED_KEYWORDS = sorted(_KEYWORD_LOOKUP.keys(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

def _preprocess(text: str) -> str:
    """Lowercase, remove punctuation, normalise whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def keyword_score(abstract: str) -> tuple[float, dict[str, Any]]:
    """Compute keyword-based relevance score for an abstract.

    Args:
        abstract: The paper abstract text.

    Returns:
        Tuple of (normalised_score, details_dict).
        Score is in [0, 1].
    """
    if not abstract or len(abstract.strip()) < 20:
        return 0.0, {"matched": {}, "raw_score": 0, "max_possible": 0}

    text = _preprocess(abstract)
    max_possible = sum(w for _, w in _KEYWORD_LOOKUP.values())
    matched: dict[str, float] = {}
    raw_score = 0.0

    # Use a set to track matched positions to avoid double-counting overlapping keywords
    matched_positions: set[int] = set()

    for kw in _SORTED_KEYWORDS:
        category, weight = _KEYWORD_LOOKUP[kw]
        kw_norm = _preprocess(kw)
        start = 0
        while True:
            idx = text.find(kw_norm, start)
            if idx == -1:
                break
            # Check if this position overlaps with already-matched text
            overlap = any(idx <= pos < idx + len(kw_norm) for pos in matched_positions)
            if not overlap:
                matched[kw] = matched.get(kw, 0.0) + weight
                raw_score += weight
                matched_positions.update(range(idx, idx + len(kw_norm)))
            start = idx + 1

    normalised = min(raw_score / max_possible, 1.0) if max_possible > 0 else 0.0
    details = {
        "matched": matched,
        "raw_score": raw_score,
        "max_possible": max_possible,
    }
    return normalised, details


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------

class BM25:
    """Simple BM25 implementation over a corpus of documents."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_count = len(corpus)
        self.avgdl = (
            sum(len(doc) for doc in corpus) / self.doc_count
            if self.doc_count > 0
            else 0.0
        )
        self.doc_freqs: Counter = Counter()
        self.idf: dict[str, float] = {}
        self._compute_idf()

    def _compute_idf(self) -> None:
        """Compute inverse document frequency for each term."""
        for doc in self.corpus:
            seen = set(doc)
            for term in seen:
                self.doc_freqs[term] += 1

        for term, freq in self.doc_freqs.items():
            self.idf[term] = math.log((self.doc_count - freq + 0.5) / (freq + 0.5) + 1)

    def score(self, query: list[str], doc: list[str]) -> float:
        """Compute BM25 score of query against a single document."""
        if not doc or not query:
            return 0.0
        score = 0.0
        doc_len = len(doc)
        for term in query:
            if term not in self.idf:
                continue
            tf = doc.count(term)
            numerator = self.idf[term] * tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            score += numerator / denominator if denominator != 0 else 0.0
        return score

    def score_all(self, query: list[str]) -> list[float]:
        """Return BM25 scores for all documents in the corpus."""
        return [self.score(query, doc) for doc in self.corpus]


def _build_bm25_corpus(conn: sqlite3.Connection) -> list[list[str]]:
    """Build tokenised corpus from all paper abstracts in the database."""
    corpus = []
    for row in conn.execute("SELECT abstract FROM papers"):
        abstract = row[0] or ""
        tokens = _preprocess(abstract).split()
        corpus.append(tokens)
    return corpus


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def evaluate_corpus(
    conn: sqlite3.Connection,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
    threshold: float = 0.15,
    store: bool = True,
) -> list[tuple[int, float, bool, dict[str, Any]]]:
    """Evaluate relevance for all papers in the database.

    Args:
        conn: SQLite connection.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        keyword_weight: Weight for keyword score in hybrid mode.
        bm25_weight: Weight for BM25 score in hybrid mode.
        threshold: Score threshold above which a paper is marked relevant.
        store: If True, persist results in relevance_evals table.

    Returns:
        List of (paper_id, score, is_relevant, details) tuples.
    """
    # Fetch all papers
    papers = conn.execute(
        "SELECT id, abstract FROM papers ORDER BY id"
    ).fetchall()

    if not papers:
        return []

    abstracts = [row["abstract"] or "" for row in papers]

    # Compute keyword scores
    kw_scores: list[float] = []
    kw_details_list: list[dict[str, Any]] = []
    for abstract in abstracts:
        score, details = keyword_score(abstract)
        kw_scores.append(score)
        kw_details_list.append(details)

    # Compute BM25 scores
    bm25_scores: list[float] = [0.0] * len(papers)
    bm25_corpus = [_preprocess(a).split() for a in abstracts]

    if method in ("bm25", "hybrid"):
        if bm25_corpus:
            bm25 = BM25(bm25_corpus)
            query_terms = list(_KEYWORD_LOOKUP.keys())
            query_tokens = _preprocess(" ".join(query_terms)).split()
            raw_bm25 = bm25.score_all(query_tokens)
            # Normalise BM25 scores to [0, 1]
            max_bm25 = max(raw_bm25) if raw_bm25 else 1.0
            if max_bm25 > 0:
                bm25_scores = [s / max_bm25 for s in raw_bm25]

    # Compute composite scores
    results: list[tuple[int, float, bool, dict[str, Any]]] = []
    for idx, row in enumerate(papers):
        paper_id = row["id"]
        abstract = abstracts[idx]

        if method == "keyword":
            score = kw_scores[idx]
        elif method == "bm25":
            score = bm25_scores[idx]
        else:  # hybrid
            score = keyword_weight * kw_scores[idx] + bm25_weight * bm25_scores[idx]

        is_relevant = score >= threshold
        details = {
            "keyword_score": kw_scores[idx],
            "bm25_score": bm25_scores[idx],
            "keyword_details": kw_details_list[idx],
            "method": method,
            "threshold": threshold,
        }

        results.append((paper_id, score, is_relevant, details))

        if store:
            conn.execute(
                """
                INSERT INTO relevance_evals (paper_id, method, score, is_relevant, threshold, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (paper_id, method, score, is_relevant, threshold, json.dumps(details)),
            )

    if store:
        conn.commit()

    # Update papers table with latest relevance info
    for paper_id, score, is_relevant, _ in results:
        conn.execute(
            "UPDATE papers SET is_relevant = ?, relevance_score = ?, updated_at = current_timestamp WHERE id = ?",
            (is_relevant, score, paper_id),
        )
    if store:
        conn.commit()

    return results


# ---------------------------------------------------------------------------
# Single-paper evaluation
# ---------------------------------------------------------------------------

def evaluate_paper(
    conn: sqlite3.Connection,
    paper_id: int,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
    threshold: float = 0.15,
) -> tuple[float, bool, dict[str, Any]]:
    """Evaluate relevance for a single paper.

    Args:
        conn: SQLite connection.
        paper_id: ID of the paper to evaluate.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        keyword_weight: Weight for keyword score in hybrid mode.
        bm25_weight: Weight for BM25 score in hybrid mode.
        threshold: Score threshold for relevance.

    Returns:
        Tuple of (score, is_relevant, details).
    """
    row = conn.execute(
        "SELECT abstract FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Paper {paper_id} not found")

    abstract = row["abstract"] or ""
    kw_score, kw_details = keyword_score(abstract)

    # BM25 over full corpus
    bm25_score = 0.0
    if method in ("bm25", "hybrid"):
        corpus = []
        for r in conn.execute("SELECT abstract FROM papers"):
            corpus.append(_preprocess(r["abstract"] or "").split())
        if corpus:
            bm25 = BM25(corpus)
            query_terms = list(_KEYWORD_LOOKUP.keys())
            query_tokens = _preprocess(" ".join(query_terms)).split()
            raw_bm25 = bm25.score_all(query_tokens)
            doc_idx = conn.execute(
                "SELECT id FROM papers WHERE id = ?", (paper_id,)
            ).fetchall()
            # Find index of this paper
            idx = None
            for i, r in enumerate(conn.execute("SELECT id FROM papers ORDER BY id")):
                if r["id"] == paper_id:
                    idx = i
                    break
            if idx is not None and raw_bm25:
                max_bm25 = max(raw_bm25)
                bm25_score = raw_bm25[idx] / max_bm25 if max_bm25 > 0 else 0.0

    if method == "keyword":
        score = kw_score
    elif method == "bm25":
        score = bm25_score
    else:
        score = keyword_weight * kw_score + bm25_weight * bm25_score

    is_relevant = score >= threshold
    details = {
        "keyword_score": kw_score,
        "bm25_score": bm25_score,
        "keyword_details": kw_details,
        "method": method,
        "threshold": threshold,
    }
    return score, is_relevant, details


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_relevance_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return summary statistics about relevance evaluations."""
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    evaluated = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_relevant IS NOT NULL"
    ).fetchone()[0]
    relevant = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_relevant = 1"
    ).fetchone()[0]
    irrelevant = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_relevant = 0"
    ).fetchone()[0]

    latest_eval = conn.execute(
        "SELECT evaluated_at FROM relevance_evals ORDER BY evaluated_at DESC LIMIT 1"
    ).fetchone()
    last_evaluated = latest_eval["evaluated_at"] if latest_eval else None

    return {
        "total_papers": total,
        "evaluated": evaluated,
        "relevant": relevant,
        "irrelevant": irrelevant,
        "unevaluated": total - evaluated,
        "last_evaluated": last_evaluated,
    }
