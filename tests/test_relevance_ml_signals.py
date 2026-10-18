"""Tests for the expanded _ML_SIGNALS in relevance.py."""

import sqlite3

from src.db_schema import create_schema
from src.relevance import _classify, _ML_SIGNALS, keyword_score, evaluate_paper


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


# Test papers with standalone ML terms
ML_SIGNAL_PAPERS = [
    {
        "title": "Neural Networks for PUF Modeling",
        "authors": "Author, A.",
        "year": 2023,
        "abstract": (
            "We propose a neural network approach to model PUF challenge-response "
            "behavior for authentication; no hardware access is required."
        ),
        "publication_title": "IEEE TIFS",
        "doi": "10.1/ml1",
        "keywords": "neural network, PUF",
    },
    {
        "title": "Deep Learning Attacks on PUFs",
        "authors": "Author, B.",
        "year": 2022,
        "abstract": (
            "This paper demonstrates deep learning techniques to predict PUF "
            "challenge-response behavior without physical access."
        ),
        "publication_title": "ACM IoT",
        "doi": "10.1/ml2",
        "keywords": "deep learning, PUF",
    },
    {
        "title": "Machine Learning for PUF Security",
        "authors": "Author, C.",
        "year": 2021,
        "abstract": (
            "A comprehensive study of machine learning approaches for PUF security "
            "analysis and modeling."
        ),
        "publication_title": "CHES",
        "doi": "10.1/ml3",
        "keywords": "machine learning, PUF",
    },
    {
        "title": "Artificial Intelligence in PUF Modeling",
        "authors": "Author, D.",
        "year": 2020,
        "abstract": (
            "We explore artificial intelligence methods for PUF challenge-response "
            "prediction."
        ),
        "publication_title": "IEEE",
        "doi": "10.1/ml4",
        "keywords": "artificial intelligence, PUF",
    },
    {
        "title": "AI Based PUF Attacks",
        "authors": "Author, E.",
        "year": 2019,
        "abstract": (
            "This work presents ai based techniques for PUF modeling and prediction."
        ),
        "publication_title": "ACM",
        "doi": "10.1/ml5",
        "keywords": "ai, PUF",
    },
    {
        "title": "Neural Networks and PUFs",
        "authors": "Author, F.",
        "year": 2018,
        "abstract": (
            "A study on neural networks applied to physical unclonable functions."
        ),
        "publication_title": "IEEE",
        "doi": "10.1/ml6",
        "keywords": "neural networks, PUF",
    },
]


def test_ml_signals_includes_standalone_terms():
    """Verify that _ML_SIGNALS includes the expanded standalone ML terms."""
    expected_signals = [
        "machine learning attack",
        "modeling attack",
        "dnn",
        "neural network",
        "neural networks",
        "deep learning",
        "ml attack",
        "machine learning",
        "artificial intelligence",
        "ai",
    ]
    for signal in expected_signals:
        assert signal in _ML_SIGNALS, f"Missing signal: {signal}"


def test_neural_network_paper_classified_as_out_of_scope():
    """Verify that papers with 'neural network' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[:1])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml1",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_deep_learning_paper_classified_as_out_of_scope():
    """Verify that papers with 'deep learning' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[1:2])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml2",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_machine_learning_paper_classified_as_out_of_scope():
    """Verify that papers with 'machine learning' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[2:3])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml3",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_artificial_intelligence_paper_classified_as_out_of_scope():
    """Verify that papers with 'artificial intelligence' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[3:4])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml4",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_ai_paper_classified_as_out_of_scope():
    """Verify that papers with 'ai' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[4:5])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml5",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_neural_networks_paper_classified_as_out_of_scope():
    """Verify that papers with 'neural networks' are classified as out-of-scope."""
    conn = _make_db_with_papers(ML_SIGNAL_PAPERS[5:6])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/ml6",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "out-of-scope"


def test_hybrid_paper_with_physical_and_ml_signals_classified_as_hybrid():
    """Verify that papers with both physical and ML signals are classified as hybrid."""
    hybrid_paper = {
        "title": "Side-Channel and Neural Network Attack on PUFs",
        "authors": "Author, G.",
        "year": 2023,
        "abstract": (
            "We present a side-channel power analysis attack combined with a neural "
            "network modeling approach to clone PUF responses."
        ),
        "publication_title": "IEEE TIFS",
        "doi": "10.1/hybrid1",
        "keywords": "side-channel, neural network",
    }
    conn = _make_db_with_papers([hybrid_paper])
    row = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/hybrid1",)).fetchone()
    score, is_rel, rel_class, _ = evaluate_paper(conn, row["id"], threshold=0.1)
    assert rel_class == "hybrid"


def test_classify_detects_ml_signals():
    """Verify that _classify detects ML signals correctly using _ML_PATTERNS."""
    # Test neural network
    nn_abstract = "We propose a neural network approach to model PUF."
    assert _classify(nn_abstract, 0.0, 0.5) == "out-of-scope"
    
    # Test deep learning
    dl_abstract = "Deep learning techniques to predict PUF challenge-response behavior."
    assert _classify(dl_abstract, 0.0, 0.5) == "out-of-scope"
    
    # Test machine learning
    ml_abstract = "A comprehensive study of machine learning approaches for PUF security."
    assert _classify(ml_abstract, 0.0, 0.5) == "out-of-scope"
    
    # Test artificial intelligence
    ai_abstract = "We explore artificial intelligence methods for PUF challenge-response prediction."
    assert _classify(ai_abstract, 0.0, 0.5) == "out-of-scope"
    
    # Test ai
    ai_short_abstract = "This work presents ai based techniques for PUF modeling."
    assert _classify(ai_short_abstract, 0.0, 0.5) == "out-of-scope"
    
    # Test neural networks (plural)
    nn_plurals_abstract = "A study on neural networks applied to physical unclonable functions."
    assert _classify(nn_plurals_abstract, 0.0, 0.5) == "out-of-scope"
