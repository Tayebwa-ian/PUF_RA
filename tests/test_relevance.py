"""Tests for the relevance engine."""

import math
import sqlite3
import tempfile

import pytest

from src.db_schema import create_schema
from src.relevance import (
    BM25,
    _preprocess,
    evaluate_corpus,
    evaluate_paper,
    keyword_score,
    TOPIC_KEYWORDS,
)


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
            "We propose a lightweight machine learning model for IoT device "
            "authentication. No physical attacks are considered."
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
    score, is_rel, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert score > 0
    assert is_rel is True


def test_keyword_score_irrelevant_paper():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p2",)).fetchone()
    score, is_rel, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    # Machine learning paper should have low relevance
    assert score < 0.5


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
    score, _, _ = evaluate_paper(conn, row["id"], threshold=0.0)
    assert score == 0.0


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


def test_evaluate_corpus_stores_results():
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    results = evaluate_corpus(conn, threshold=0.1, store=True)
    assert len(results) == 3
    stored = conn.execute("SELECT COUNT(*) FROM relevance_evals").fetchone()[0]
    assert stored == 3
    # Check papers table updated
    evaluated = conn.execute("SELECT COUNT(*) FROM papers WHERE is_relevant IS NOT NULL").fetchone()[0]
    assert evaluated == 3


def test_preprocess():
    assert _preprocess("Hello World!") == "hello world"
    assert _preprocess("POWER ANALYSIS") == "power analysis"
    assert _preprocess("  multiple   spaces  ") == "multiple spaces"
