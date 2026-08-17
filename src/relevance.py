"""Empirical relevance engine for PUF attack papers.

Scores papers against the topic:
    "Physical attacks on Physical Unclonable Functions (PUFs)"

Uses a hybrid approach:
  - Keyword matching with category-based weights (word-boundary matching, so a
    short signal like ``fib`` never matches "fiber" / "fibrosis" / "Fibonacci")
  - BM25 scoring over the corpus of abstracts

Every method emits the study's three classes (``in-scope`` / ``out-of-scope`` /
``hybrid``) via the shared :func:`_classify` rule. Both scorers are
deterministic and reproducible.

The decision threshold can be derived from the human ground truth with
:func:`derive_threshold` (max-F1 or Youden's J against
``ground_truth_consensus``); otherwise the configurable default (``0.15``)
applies.

Usage:
    from src.relevance import evaluate_corpus, derive_threshold, TOPIC_KEYWORDS

    threshold = derive_threshold(conn, method="hybrid") or 0.15
    results = evaluate_corpus(conn, threshold=threshold)
    # results: list of (paper_id, score, is_relevant, relevance_class, details)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from typing import Any, Optional

from src.db import get_connection
from src import eval_store


# ---------------------------------------------------------------------------
# Text preprocessing / matching primitives
# ---------------------------------------------------------------------------

def _preprocess(text: str) -> str:
    """Lowercase, remove punctuation, normalise whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _word_pattern(term: str) -> re.Pattern[str]:
    """Compile a **word-boundary** matcher for an already-preprocessed ``term``.

    Word boundaries are essential: plain substring containment made the
    three-letter signal ``fib`` match "fiber", "fibrosis" and "Fibonacci", which
    mislabelled ~12 corpus papers as physical attacks. An optional plural suffix
    is allowed so that the common inflections still match ("physical attacks",
    "PUFs", "side-channels") without matching longer, unrelated words.
    """
    return re.compile(r"\b" + re.escape(term) + r"(?:s|es)?\b", re.IGNORECASE)


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

#: ``(keyword, compiled word-boundary matcher)`` in longest-first order, so a
#: long keyword claims its span before a shorter one nested inside it.
_KEYWORD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (kw, _word_pattern(_preprocess(kw))) for kw in _SORTED_KEYWORDS
]

# ML / modelling attack signals (sub-category of out-of-scope). Kept separate
# from the physical-attack signals so a paper that mentions both is "hybrid".
_ML_MODELING_KEYWORDS = {"modeling attack", "machine learning attack"}

#: Physical-attack signals derived from the ``attacks`` keyword category,
#: excluding the ML/modelling keywords above. A match implies a physical attack
#: (side-channel, fault injection, invasive/semi-invasive, probing, EM, laser,
#: FIB/depackaging, delayering, depackaging, physical attack, cloning).
_PHYSICAL_ATTACK_SIGNALS: list[str] = [
    kw for kw in TOPIC_KEYWORDS["attacks"] if kw not in _ML_MODELING_KEYWORDS
]

#: ML / modelling attack signals (logical, non-invasive, CRP-based attacks).
_ML_SIGNALS: list[str] = [
    "machine learning attack",
    "modeling attack",
    "dnn",
    "neural network",
    "deep learning",
    "ml attack",
]

#: Word-boundary matchers for the two signal families used by :func:`_classify`.
_PHYSICAL_ATTACK_PATTERNS: list[re.Pattern[str]] = [
    _word_pattern(_preprocess(sig)) for sig in _PHYSICAL_ATTACK_SIGNALS
]
_ML_PATTERNS: list[re.Pattern[str]] = [
    _word_pattern(_preprocess(sig)) for sig in _ML_SIGNALS
]


def _classify(abstract: str, score: float, threshold: float) -> str:
    """Map an abstract + continuous score to one of the three study classes.

    Signals are matched on **word boundaries** (see :func:`_word_pattern`), so
    ``fib`` matches "FIB milling" but not "fiber" / "fibrosis" / "Fibonacci".

    Physical-attack signals imply a real physical attack; ML/modelling signals
    imply a logical, non-invasive modelling attack. Rule:
      - both present            -> ``hybrid``
      - physical only           -> ``in-scope``
      - ML only (no physical)   -> ``out-of-scope``
      - neither                 -> ``in-scope`` iff score >= threshold else
                                   ``out-of-scope``
    """
    text = _preprocess(abstract or "")
    has_physical = any(p.search(text) for p in _PHYSICAL_ATTACK_PATTERNS)
    has_ml = any(p.search(text) for p in _ML_PATTERNS)
    if has_physical and has_ml:
        return "hybrid"
    if has_physical:
        return "in-scope"
    if has_ml:
        return "out-of-scope"
    return "in-scope" if score >= threshold else "out-of-scope"


def _baseline_eval_records(
    results: list[tuple],
    method: str,
    threshold: float,
    run: int,
) -> list[dict[str, Any]]:
    """Build canonical eval JSONL records from deterministic-baseline results."""
    records = []
    for paper_id, score, is_relevant, relevance_class, details in results:
        matched = list((details.get("keyword_details") or {}).get("matched", {}).keys())
        records.append({
            "eval_id": f"eval-{run}-{paper_id}",
            "paper_id": paper_id,
            "title": "",
            "method": f"baseline_{method}",
            "model": "deterministic",
            "model_version": "",
            "prompt_id": "n/a",
            "decision": relevance_class,
            "score": score,
            "confidence": None,
            "matched_keywords": matched,
            "rationale": f"{method} score {score:.4f} vs threshold {threshold}",
            "temperature": 0.0,
            "run": run,
            "timestamp": "",
        })
    return records


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def keyword_score(abstract: str) -> tuple[float, dict[str, Any]]:
    """Compute keyword-based relevance score for an abstract.

    Keywords are matched with **word-boundary** regexes (longest keyword first),
    so overlapping matches are counted once and short signals cannot match inside
    unrelated words.

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

    # Track matched character positions (match spans) so overlapping keywords are
    # not double-counted; the longest-first order lets the longer keyword win.
    matched_positions: set[int] = set()

    for kw, pattern in _KEYWORD_PATTERNS:
        _category, weight = _KEYWORD_LOOKUP[kw]
        for match in pattern.finditer(text):
            span = range(match.start(), match.end())
            if any(pos in matched_positions for pos in span):
                continue  # already claimed by a longer keyword
            matched[kw] = matched.get(kw, 0.0) + weight
            raw_score += weight
            matched_positions.update(span)

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

def _score_components(
    conn: sqlite3.Connection,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
) -> list[dict[str, Any]]:
    """Score the whole corpus **without writing anything**.

    Shared by :func:`evaluate_corpus` (which then classifies + persists) and by
    :func:`derive_threshold` (which only needs the scores), so threshold
    derivation can never mutate the database.

    Args:
        conn: SQLite connection.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        keyword_weight: Weight for the keyword score in hybrid mode.
        bm25_weight: Weight for the BM25 score in hybrid mode.

    Returns:
        One dict per paper (ordered by ``papers.id``) with ``paper_id``,
        ``abstract``, ``keyword_score``, ``bm25_score``, ``keyword_details`` and
        the composite ``score``.
    """
    papers = conn.execute("SELECT id, abstract FROM papers ORDER BY id").fetchall()
    if not papers:
        return []

    abstracts = [row["abstract"] or "" for row in papers]

    kw_scores: list[float] = []
    kw_details_list: list[dict[str, Any]] = []
    for abstract in abstracts:
        score, details = keyword_score(abstract)
        kw_scores.append(score)
        kw_details_list.append(details)

    bm25_scores: list[float] = [0.0] * len(papers)
    if method in ("bm25", "hybrid"):
        bm25_corpus = [_preprocess(a).split() for a in abstracts]
        if bm25_corpus:
            bm25 = BM25(bm25_corpus)
            query_terms = list(_KEYWORD_LOOKUP.keys())
            query_tokens = _preprocess(" ".join(query_terms)).split()
            raw_bm25 = bm25.score_all(query_tokens)
            # Normalise BM25 scores to [0, 1]
            max_bm25 = max(raw_bm25) if raw_bm25 else 1.0
            if max_bm25 > 0:
                bm25_scores = [s / max_bm25 for s in raw_bm25]

    components: list[dict[str, Any]] = []
    for idx, row in enumerate(papers):
        if method == "keyword":
            score = kw_scores[idx]
        elif method == "bm25":
            score = bm25_scores[idx]
        else:  # hybrid
            score = keyword_weight * kw_scores[idx] + bm25_weight * bm25_scores[idx]
        components.append(
            {
                "paper_id": row["id"],
                "abstract": abstracts[idx],
                "keyword_score": kw_scores[idx],
                "bm25_score": bm25_scores[idx],
                "keyword_details": kw_details_list[idx],
                "score": score,
            }
        )
    return components


def _scores_for_corpus(
    conn: sqlite3.Connection,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
) -> list[tuple[int, float]]:
    """``(paper_id, composite score)`` for every paper, computed without storing."""
    return [
        (c["paper_id"], c["score"])
        for c in _score_components(conn, method, keyword_weight, bm25_weight)
    ]


def derive_threshold(
    conn: sqlite3.Connection,
    method: str = "hybrid",
    criterion: str = "f1",
    step: float = 0.01,
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
) -> Optional[float]:
    """Derive the decision threshold from the human ground truth.

    Sweeps candidate thresholds over ``[0, 1]`` in ``step`` increments and keeps
    the one that maximises ``criterion`` against ``ground_truth_consensus``:

    * **positive** — consensus label ``in-scope`` or ``hybrid`` (both involve a
      physical attack, i.e. the papers the screen must keep),
    * **negative** — consensus label ``out-of-scope``,
    * ``disagree`` rows are skipped (not yet adjudicated).

    Scores come from :func:`_scores_for_corpus`, so nothing is written to the
    database.

    Args:
        conn: SQLite connection.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        criterion: ``"f1"`` (max-F1, default) or ``"youden"``
            (max sensitivity + specificity - 1).
        step: Sweep granularity in score units (default ``0.01``).
        keyword_weight: Weight for the keyword score in hybrid mode.
        bm25_weight: Weight for the BM25 score in hybrid mode.

    Returns:
        The best threshold, or ``None`` when there is no usable ground truth (no
        consensus rows, only ``disagree`` rows, or a single class present). The
        caller then falls back to its configured default (``0.15``).

    Raises:
        ValueError: For an unknown ``criterion`` or a ``step`` outside ``(0, 1]``.
    """
    if criterion not in ("f1", "youden"):
        raise ValueError(f"Unknown criterion: {criterion!r} (use 'f1' or 'youden')")
    if not 0 < step <= 1:
        raise ValueError("step must be in (0, 1]")

    try:
        consensus = conn.execute(
            "SELECT paper_id, consensus_label FROM ground_truth_consensus"
        ).fetchall()
    except sqlite3.Error:  # table absent (pre-migration database)
        return None

    labelled: dict[int, int] = {}
    for row in consensus:
        label = row["consensus_label"]
        if label in ("in-scope", "hybrid"):
            labelled[int(row["paper_id"])] = 1
        elif label == "out-of-scope":
            labelled[int(row["paper_id"])] = 0
        # 'disagree' (and anything unexpected) is skipped
    if not labelled:
        return None

    scores = dict(_scores_for_corpus(conn, method, keyword_weight, bm25_weight))
    pairs = [(scores[pid], y) for pid, y in labelled.items() if pid in scores]
    n_pos = sum(y for _, y in pairs)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None  # a sweep needs both classes

    best_threshold: Optional[float] = None
    best_value = -1.0
    n_steps = int(round(1.0 / step))
    for i in range(n_steps + 1):
        t = round(i * step, 10)
        tp = sum(1 for s, y in pairs if s >= t and y == 1)
        fp = sum(1 for s, y in pairs if s >= t and y == 0)
        fn = n_pos - tp
        tn = n_neg - fp
        if criterion == "f1":
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            value = (
                2 * precision * recall / (precision + recall)
                if (precision + recall)
                else 0.0
            )
        else:  # Youden's J = sensitivity + specificity - 1
            value = tp / n_pos + tn / n_neg - 1.0
        if value > best_value:
            best_value = value
            best_threshold = t
    return best_threshold


def evaluate_corpus(
    conn: sqlite3.Connection,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
    threshold: float = 0.15,
    store: bool = True,
    run: int = 1,
) -> list[tuple[int, float, bool, str, dict[str, Any]]]:
    """Evaluate relevance for all papers in the database (3-class decisions).

    Args:
        conn: SQLite connection.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        keyword_weight: Weight for keyword score in hybrid mode.
        bm25_weight: Weight for BM25 score in hybrid mode.
        threshold: Score threshold used by the fall-back branch of the 3-class
            rule (see :func:`_classify`); derive it with
            :func:`derive_threshold` when ground truth exists.
        store: If True, persist per-paper rows in ``relevance_evals``, update
            ``papers`` (``is_relevant`` / ``relevance_score`` /
            ``relevance_class``) and ingest the canonical 3-class decisions into
            ``eval_runs`` / ``evals``. If False, nothing is written.
        run: Repetition index recorded as ``eval_runs.run_index``.

    Returns:
        List of 5-tuples ``(paper_id, score, is_relevant, relevance_class,
        details)`` where ``relevance_class`` is one of ``in-scope`` /
        ``out-of-scope`` / ``hybrid`` (``is_relevant`` is the legacy binary
        ``score >= threshold`` flag, kept for ``relevance_evals``).
    """
    components = _score_components(conn, method, keyword_weight, bm25_weight)
    if not components:
        return []

    results: list[tuple[int, float, bool, str, dict[str, Any]]] = []
    for comp in components:
        paper_id = comp["paper_id"]
        abstract = comp["abstract"]
        score = comp["score"]

        is_relevant = score >= threshold
        relevance_class = _classify(abstract, score, threshold)
        details = {
            "keyword_score": comp["keyword_score"],
            "bm25_score": comp["bm25_score"],
            "keyword_details": comp["keyword_details"],
            "method": method,
            "threshold": threshold,
        }

        results.append((paper_id, score, is_relevant, relevance_class, details))

        if store:
            conn.execute(
                """
                INSERT INTO relevance_evals
                    (paper_id, method, score, is_relevant, threshold, decision, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (paper_id, method, score, is_relevant, threshold, relevance_class, json.dumps(details)),
            )

    # Update papers table with latest relevance info — only when storing, so
    # `--no-store` / export_baseline_jsonl(store=False) stay read-only (note
    # src.db.get_connection commits on context exit).
    if store:
        for paper_id, score, is_relevant, relevance_class, _ in results:
            conn.execute(
                "UPDATE papers SET is_relevant = ?, relevance_score = ?, relevance_class = ?, updated_at = current_timestamp WHERE id = ?",
                (is_relevant, score, relevance_class, paper_id),
            )
        conn.commit()

    # Canonical ingestion into eval_runs/evals: the deterministic baseline's
    # authoritative 3-class decision lives in evals keyed by eval_runs.id
    # (the eval_run_id), exactly like SBERT and (later) LLM prompts.
    if store:
        records = _baseline_eval_records(results, method, threshold, run)
        eval_store.ingest_eval_records(conn, records)

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
) -> tuple[float, bool, str, dict[str, Any]]:
    """Evaluate relevance for a single paper (3-class decision, no writes).

    Args:
        conn: SQLite connection.
        paper_id: ID of the paper to evaluate.
        method: Scoring method ('keyword', 'bm25', 'hybrid').
        keyword_weight: Weight for keyword score in hybrid mode.
        bm25_weight: Weight for BM25 score in hybrid mode.
        threshold: Score threshold used by the fall-back branch of the 3-class
            rule (see :func:`_classify`); :func:`derive_threshold` can supply a
            ground-truth-derived value.

    Returns:
        4-tuple ``(score, is_relevant, relevance_class, details)`` where
        ``relevance_class`` is ``in-scope`` / ``out-of-scope`` / ``hybrid`` and
        ``is_relevant`` is the legacy binary ``score >= threshold`` flag.
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
    relevance_class = _classify(abstract, score, threshold)
    details = {
        "keyword_score": kw_score,
        "bm25_score": bm25_score,
        "keyword_details": kw_details,
        "method": method,
        "threshold": threshold,
    }
    return score, is_relevant, relevance_class, details


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
