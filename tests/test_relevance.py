"""Tests for the relevance engine."""

import json
import sqlite3

from pathlib import Path
from unittest import mock

import pytest

from src.db_schema import create_schema
from src.relevance import (
    BM25,
    _preprocess,
    _classify,
    _scores_for_corpus,
    derive_threshold,
    evaluate_corpus,
    evaluate_paper,
    keyword_score,
    TOPIC_KEYWORDS,
)
from src import baselines
from src import eval_store


def _make_db_with_papers(papers: list[dict]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with papers table populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    for p in papers:
        conn.execute(
            """
            INSERT INTO papers (title, authors, year, abstract, publication_title, doi, keywords)
            VALUES (:title, :authors, :year, :abstract, :publication_title, :doi, :keywords)
            """,
            p,
        )
    conn.commit()
    return conn


SAMPLE_PAPERS = [
    {
        "title": "Power Analysis of SRAM PUFs",
        "authors": "Smith, John",
        "year": 2023,
        "abstract": (
            "This paper presents a comprehensive power analysis attack on SRAM-based "
            "physical unclonable functions. We demonstrate that side-channel leakage "
            "can be exploited to clone PUF responses."
        ),
        "publication_title": "IEEE TIFS",
        "doi": "10.1/p1",
        "keywords": "PUF, power analysis",
    },
    {
        "title": "Machine Learning for IoT",
        "authors": "Doe, Jane",
        "year": 2024,
        "abstract": (
            "We propose a machine learning attack to model PUF "
            "challenge-response behavior for authentication; no hardware "
            "access is required."
        ),
        "publication_title": "ACM IoT",
        "doi": "10.1/p2",
        "keywords": "IoT, ML",
    },
    {
        "title": "Delayering Attacks on Ring Oscillator PUFs",
        "authors": "Lee, Kevin",
        "year": 2022,
        "abstract": (
            "An invasive delayering attack on ring oscillator PUFs is presented. "
            "We use focused ion beam (FIB) techniques to extract secret keys."
        ),
        "publication_title": "CHES 2022",
        "doi": "10.1/p3",
        "keywords": "RO PUF, invasive attack",
    },
]


def test_keyword_score_relevant_paper():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p1",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert score > 0
    assert is_rel is True
    assert rel_class == "in-scope"


def test_keyword_score_irrelevant_paper():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p2",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    # ML/modeling paper should have low relevance
    assert score < 0.5
    assert rel_class == "out-of-scope"


def test_keyword_score_empty_abstract():
    conn = _make_db_with_papers([
        {
            "title": "Empty",
            "authors": "X",
            "year": 2024,
            "abstract": "",
            "publication_title": "X",
            "doi": "10.1/empty",
            "keywords": None,
        }
    ])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/empty",)).fetchone()
    score, _, _, _ = evaluate_paper(conn, row["id"], threshold=0.0)
    assert score == 0.0


def test_classify_three_classes():
    # both physical + ML signals -> hybrid
    assert _classify(
        "A power analysis side-channel attack combined with a neural network "
        "modeling attack to clone the PUF.",
        0.0, 0.5,
    ) == "hybrid"
    # physical only -> in-scope
    assert _classify(
        "A side-channel power analysis attack on SRAM PUFs.",
        0.9, 0.5,
    ) == "in-scope"
    # ML only (no physical) -> out-of-scope
    assert _classify(
        "We propose a machine learning modeling attack to predict PUF responses; "
        "no physical access required.",
        0.9, 0.5,
    ) == "out-of-scope"
    # neither, below threshold -> out-of-scope
    assert _classify("A paper about cooking recipes.", 0.1, 0.5) == "out-of-scope"
    # neither, above threshold -> in-scope
    assert _classify("A paper about cooking recipes.", 0.9, 0.5) == "in-scope"


def test_bm25_scoring():
    corpus = [
        ["puf", "power", "analysis", "attack"],
        ["machine", "learning", "iot", "authentication"],
        ["delayering", "invasive", "attack", "ro", "puf"],
    ]
    bm25 = BM25(corpus)
    query = ["puf", "attack"]
    scores = bm25.score_all(query)
    assert len(scores) == 3
    # First doc has both terms
    assert scores[0] > 0
    # Second doc has neither
    assert scores[1] == 0.0


def test_bm25_empty_corpus():
    bm25 = BM25([])
    assert bm25.score_all(["test"]) == []


def test_evaluate_corpus_stores_results_and_evals():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    results = evaluate_corpus(conn, threshold=0.1, store=True, run=1)
    assert len(results) == 3
    # 5-tuple now carries the 3-class relevance_class
    for r in results:
        assert len(r) == 5
        paper_id, score, is_rel, rel_class, details = r
        assert rel_class in ("in-scope", "out-of-scope", "hybrid")
    # backwards-compat raw-score table still written
    stored = conn.execute("SELECT COUNT(*) FROM relevance_evals").fetchone()[0]
    assert stored == 3
    # papers table updated incl. relevance_class
    evaluated = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_relevant IS NOT NULL"
    ).fetchone()[0]
    assert evaluated == 3
    classed = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE relevance_class IS NOT NULL"
    ).fetchone()[0]
    assert classed == 3
    # canonical ingestion into eval_runs/evals (single source of truth)
    runs = eval_store.list_runs(conn)
    assert len(runs) == 1
    run = runs[0]
    assert run["method"] == "baseline_hybrid"
    assert run["model"] == "deterministic"
    assert run["temperature"] == 0.0
    evals = conn.execute(
        "SELECT decision FROM evals WHERE run_id=?", (run["id"],)
    ).fetchall()
    assert len(evals) == 3
    assert {e["decision"] for e in evals} <= {"in-scope", "out-of-scope", "hybrid"}


def test_export_baseline_jsonl_three_class(tmp_path):
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    out = tmp_path / "baseline.jsonl"
    n = baselines.export_baseline_jsonl(conn, out, method="hybrid", threshold=0.1, run=1)
    assert n == 3
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    for rec in lines:
        assert rec["decision"] in ("in-scope", "out-of-scope", "hybrid")
        assert rec["model"] == "deterministic"
        assert rec["temperature"] == 0.0
        assert rec["method"] == "baseline_hybrid"
    # ML-only paper must be out-of-scope (3-class rule, not raw score)
    ml = next(r for r in lines if r["paper_id"] == 2)
    assert ml["decision"] == "out-of-scope"


def test_preprocess():
    assert _preprocess("Hello World!") == "hello world"
    assert _preprocess("POWER ANALYSIS") == "power analysis"
    assert _preprocess("  multiple   spaces  ") == "multiple spaces"


# ---------------------------------------------------------------------------
# Word-boundary keyword matching (regression: `fib` vs fiber/fibrosis/Fibonacci)
# ---------------------------------------------------------------------------

FIBER_PAPER = {
    "title": "Fiber-level Woven Fabric Capture",
    "authors": "Nguyen, T.",
    "year": 2021,
    "abstract": (
        "We present a fiber-level woven fabric capture pipeline for textile "
        "rendering. A companion clinical cohort with cystic fibrosis is "
        "discussed, and Fibonacci lattices provide the sampling grid."
    ),
    "publication_title": "SIGGRAPH",
    "doi": "10.1/fiber",
    "keywords": "fabric, fiber",
}


def test_fib_signal_requires_word_boundary():
    """`fib` must not match fiber / fibrosis / Fibonacci (12 corpus papers)."""
    score, details = keyword_score(FIBER_PAPER["abstract"])
    assert "fib" not in details["matched"]
    assert details["matched"] == {}
    assert score == 0.0
    # ...and therefore no physical-attack signal is inferred
    assert _classify(FIBER_PAPER["abstract"], 0.0, 0.15) == "out-of-scope"


def test_real_focused_ion_beam_paper_still_matches():
    abstract = (
        "A focused ion beam (FIB) circuit edit is combined with delayering to "
        "probe an SRAM PUF and extract the secret key."
    )
    _score, details = keyword_score(abstract)
    assert "focused ion beam" in details["matched"]
    assert "fib" in details["matched"]  # the parenthesised acronym
    assert "delayering" in details["matched"]
    assert _classify(abstract, 0.0, 0.15) == "in-scope"


def test_fiber_paper_not_in_scope_via_fib():
    """End-to-end: the fiber/fibrosis paper is never in-scope because of `fib`."""
    conn = _make_db_with_papers(SAMPLE_PAPERS + [FIBER_PAPER])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/fiber",)).fetchone()
    score, is_rel, rel_class, details = evaluate_paper(conn, row["id"], threshold=0.15)
    assert "fib" not in details["keyword_details"]["matched"]
    assert rel_class == "out-of-scope"
    assert is_rel is False
    assert score < 0.15
    # the genuine FIB/delayering paper stays in-scope
    p3 = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p3",)).fetchone()
    _s, _r, p3_class, p3_details = evaluate_paper(conn, p3["id"], threshold=0.15)
    assert "fib" in p3_details["keyword_details"]["matched"]
    assert p3_class == "in-scope"


def test_word_boundary_keeps_plural_inflections():
    """Word boundaries must not lose the common plurals (`physical attacks`)."""
    _score, details = keyword_score(
        "A survey of physical attacks and side-channels on SRAM PUFs is given "
        "for hardware security engineers."
    )
    assert "physical attack" in details["matched"]
    assert "side-channel" in details["matched"]
    # "SRAM PUFs" is claimed by the longer keyword (longest-first, no overlap)
    assert "sram puf" in details["matched"]
    assert "puf" not in details["matched"]
    # the bare plural still matches the core keyword on its own
    _score2, details2 = keyword_score(
        "PUFs are lightweight hardware primitives evaluated in this survey paper."
    )
    assert "puf" in details2["matched"]


# ---------------------------------------------------------------------------
# Ground-truth-derived threshold
# ---------------------------------------------------------------------------


def _seed_consensus(conn, labels: dict[str, str]) -> None:
    """Insert ground_truth_consensus rows keyed by DOI -> consensus label."""
    for doi, label in labels.items():
        pid = conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO ground_truth_consensus "
            "(paper_id, consensus_label, n_annotators, n_agree, method) "
            "VALUES (?, ?, 2, 2, 'unanimous')",
            (pid, label),
        )
    conn.commit()


def test_derive_threshold_max_f1_separates_classes():
    conn = _make_db_with_papers(SAMPLE_PAPERS + [FIBER_PAPER])
    _seed_consensus(
        conn,
        {
            "10.1/p1": "in-scope",       # power analysis / side-channel
            "10.1/p3": "in-scope",       # delayering / FIB
            "10.1/p2": "out-of-scope",   # pure ML modeling attack
            "10.1/fiber": "out-of-scope",
        },
    )
    threshold = derive_threshold(conn, method="keyword", criterion="f1")
    assert threshold is not None
    assert 0.0 <= threshold <= 1.0

    scores = dict(_scores_for_corpus(conn, method="keyword"))
    ids = {
        doi: conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()["id"]
        for doi in ("10.1/p1", "10.1/p2", "10.1/p3", "10.1/fiber")
    }
    # the derived cut-off keeps both positives and drops the fiber negative
    assert scores[ids["10.1/p1"]] >= threshold
    assert scores[ids["10.1/p3"]] >= threshold
    assert scores[ids["10.1/fiber"]] < threshold
    # derivation must not write anything
    assert conn.execute(
        "SELECT COUNT(*) FROM papers WHERE relevance_class IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relevance_evals").fetchone()[0] == 0


def test_derive_threshold_youden_and_missing_ground_truth():
    conn = _make_db_with_papers(SAMPLE_PAPERS + [FIBER_PAPER])
    # no consensus rows at all -> None (caller falls back to its default)
    assert derive_threshold(conn) is None
    # only one class -> still None (a sweep needs positives and negatives)
    _seed_consensus(conn, {"10.1/p1": "in-scope", "10.1/p3": "in-scope"})
    assert derive_threshold(conn, method="keyword") is None
    # 'disagree' rows are skipped, hybrid counts as positive
    _seed_consensus(conn, {"10.1/p2": "disagree", "10.1/fiber": "out-of-scope"})
    youden = derive_threshold(conn, method="keyword", criterion="youden")
    assert youden is not None and 0.0 <= youden <= 1.0
    with pytest.raises(ValueError):
        derive_threshold(conn, criterion="nonsense")


def test_evaluate_corpus_no_store_does_not_write_papers():
    """store=False must leave papers/relevance_evals untouched."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    results = evaluate_corpus(conn, threshold=0.1, store=False)
    assert len(results) == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM papers WHERE relevance_class IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_relevant IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relevance_evals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evals").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Phase B: auto-derived, method-specific relevance threshold
# ---------------------------------------------------------------------------

def test_derive_threshold_returns_sensible_cutoff():
    """A synthetic ground_truth_consensus yields a sane cutoff in [0, 1]."""
    conn = _make_db_with_papers(SAMPLE_PAPERS + [FIBER_PAPER])
    _seed_consensus(
        conn,
        {
            "10.1/p1": "in-scope",
            "10.1/p3": "in-scope",
            "10.1/p2": "out-of-scope",
            "10.1/fiber": "out-of-scope",
        },
    )
    threshold = derive_threshold(conn, method="hybrid", criterion="f1")
    assert threshold is not None
    assert 0.0 <= threshold <= 1.0


def test_evaluate_corpus_uses_derived_threshold():
    """With ground truth present, evaluate_corpus applies the derived cutoff
    (not the 0.15 default) for the keyword baseline method."""
    pos1 = {
        "title": "Power Analysis and Side-Channel Probing of PUFs",
        "authors": "A. Author",
        "year": 2023,
        "abstract": (
            "We present a power analysis and side-channel probing attack on SRAM "
            "physical unclonable function. Fault injection and laser techniques are "
            "used; electromagnetic analysis confirms the leakage."
        ),
        "publication_title": "IEEE",
        "doi": "10.1/pos1",
        "keywords": "puf, side-channel",
    }
    pos2 = {
        "title": "Invasive Delayering Attack on Ring Oscillator PUFs",
        "authors": "B. Author",
        "year": 2022,
        "abstract": (
            "An invasive delayering attack on ring oscillator physical unclonable "
            "function is described. Focused ion beam (FIB) is used to extract keys; "
            "probing completes the attack. We further demonstrate power analysis, "
            "side-channel fault injection, laser and electromagnetic analysis on the "
            "same PUF hardware."
        ),
        "publication_title": "ACM",
        "doi": "10.1/pos2",
        "keywords": "puf, fib",
    }
    neg1 = {
        "title": "A Survey of PUF Types",
        "authors": "C. Author",
        "year": 2021,
        "abstract": (
            "physical unclonable function arbiter puf ring oscillator ro puf sram "
            "puf bistable ring puf based memory puf butterfly puf and puf"
        ),
        "publication_title": "MDPI",
        "doi": "10.1/neg1",
        "keywords": "puf",
    }
    neg2 = {
        "title": "Another PUF Taxonomy",
        "authors": "D. Author",
        "year": 2020,
        "abstract": (
            "physical unclonable function arbiter puf ring oscillator ro puf sram "
            "puf bistable ring puf based memory puf butterfly puf and puf"
        ),
        "publication_title": "MDPI",
        "doi": "10.1/neg2",
        "keywords": "puf",
    }
    conn = _make_db_with_papers([pos1, pos2, neg1, neg2])
    _seed_consensus(
        conn,
        {
            "10.1/pos1": "in-scope",
            "10.1/pos2": "in-scope",
            "10.1/neg1": "out-of-scope",
            "10.1/neg2": "out-of-scope",
        },
    )

    derived = derive_threshold(conn, method="keyword")
    assert derived is not None
    assert derived != 0.15  # ground truth must move the cutoff off the default

    results = evaluate_corpus(conn, method="keyword", threshold=None, store=False)
    abstracts = {
        r["id"]: r["abstract"]
        for r in conn.execute("SELECT id, abstract FROM papers")
    }
    flipped = False
    for paper_id, score, _is_rel, rel_class, details in results:
        assert details["threshold"] == derived
        expected = _classify(abstracts[paper_id], score, derived)
        assert rel_class == expected
        if _classify(abstracts[paper_id], score, 0.15) != expected:
            flipped = True
    # at least one paper's decision differs from what the 0.15 default would give
    assert flipped


def test_evaluate_corpus_falls_back_to_default_without_ground_truth():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    # no consensus rows -> effective threshold is the 0.15 fallback
    results = evaluate_corpus(conn, method="keyword", threshold=None, store=False)
    assert all(details["threshold"] == 0.15 for _, _, _, _, details in results)


def test_evaluate_corpus_explicit_threshold_overrides_derivation():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    _seed_consensus(
        conn,
        {
            "10.1/p1": "in-scope",
            "10.1/p3": "in-scope",
            "10.1/p2": "out-of-scope",
        },
    )
    # explicit threshold must win over the derived cutoff
    results = evaluate_corpus(conn, method="keyword", threshold=0.05, store=False)
    assert all(details["threshold"] == 0.05 for _, _, _, _, details in results)
