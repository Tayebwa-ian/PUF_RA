"""Tests for the bootstrap_thresholds module."""

import sqlite3

from src.db_schema import create_schema
from src.bootstrap_thresholds import bootstrap_threshold_stability, _compute_per_class_metrics_bootstrap


def _make_db_with_consensus() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with papers and ground_truth_consensus tables populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    
    # Insert sample papers
    papers = [
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
        {
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
        },
    ]
    
    for p in papers:
        conn.execute(
            """
            INSERT INTO papers (title, authors, year, abstract, publication_title, doi, keywords)
            VALUES (:title, :authors, :year, :abstract, :publication_title, :doi, :keywords)
            """,
            p,
        )
    
    # Insert ground_truth_consensus
    consensus_data = [
        (1, "in-scope", 2, 2, "unanimous"),
        (2, "out-of-scope", 2, 2, "unanimous"),
        (3, "in-scope", 2, 2, "unanimous"),
        (4, "out-of-scope", 2, 2, "unanimous"),
    ]
    
    for paper_id, consensus_label, n_annotators, n_agree, method in consensus_data:
        conn.execute(
            "INSERT INTO ground_truth_consensus (paper_id, consensus_label, n_annotators, n_agree, method) VALUES (?, ?, ?, ?, ?)",
            (paper_id, consensus_label, n_annotators, n_agree, method),
        )
    
    conn.commit()
    return conn


def test_bootstrap_threshold_stability_basic():
    """Test basic functionality of bootstrap_threshold_stability."""
    conn = _make_db_with_consensus()
    
    result = bootstrap_threshold_stability(
        conn, method="keyword", criterion="f1", n_bootstraps=10, step=0.01
    )
    
    # Check that the result has the expected structure
    assert "threshold_f1_distribution" in result
    assert "threshold_youden_distribution" in result
    assert "f1_threshold_ci_95" in result
    assert "youden_threshold_ci_95" in result
    assert "per_class_metrics_bootstrap" in result
    
    # Check that distributions are lists
    assert isinstance(result["threshold_f1_distribution"], list)
    assert isinstance(result["threshold_youden_distribution"], list)
    
    # Check that CIs are tuples or None
    assert result["f1_threshold_ci_95"] is None or isinstance(result["f1_threshold_ci_95"], tuple)
    assert result["youden_threshold_ci_95"] is None or isinstance(result["youden_threshold_ci_95"], tuple)


def test_bootstrap_threshold_stability_with_youden():
    """Test bootstrap_threshold_stability with Youden criterion."""
    conn = _make_db_with_consensus()
    
    result = bootstrap_threshold_stability(
        conn, method="keyword", criterion="youden", n_bootstraps=10, step=0.01
    )
    
    # Check that the result has the expected structure
    assert "threshold_f1_distribution" in result
    assert "threshold_youden_distribution" in result
    assert "f1_threshold_ci_95" in result
    assert "youden_threshold_ci_95" in result
    assert "per_class_metrics_bootstrap" in result


def test_bootstrap_threshold_stability_no_labelled_papers():
    """Test bootstrap_threshold_stability with no labelled papers."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    
    # Don't insert any consensus data
    
    try:
        bootstrap_threshold_stability(conn, method="keyword", criterion="f1", n_bootstraps=10)
        assert False, "Expected ValueError for no labelled papers"
    except ValueError as e:
        assert "No labelled papers found in ground_truth_consensus" in str(e)


def test_compute_per_class_metrics_bootstrap():
    """Test _compute_per_class_metrics_bootstrap function."""
    conn = _make_db_with_consensus()
    
    # Create dummy distributions
    f1_distribution = [0.1, 0.15, 0.2]
    youden_distribution = [0.12, 0.16, 0.22]
    
    result = _compute_per_class_metrics_bootstrap(
        conn, method="keyword", 
        threshold_f1_distribution=f1_distribution, 
        threshold_youden_distribution=youden_distribution, 
        step=0.01
    )
    
    # Check that the result has the expected structure
    assert "f1_threshold_metrics" in result
    assert "youden_threshold_metrics" in result
    
    # Check that metrics are dicts or None
    assert result["f1_threshold_metrics"] is None or isinstance(result["f1_threshold_metrics"], dict)
    assert result["youden_threshold_metrics"] is None or isinstance(result["youden_threshold_metrics"], dict)


def test_compute_per_class_metrics_bootstrap_empty_distributions():
    """Test _compute_per_class_metrics_bootstrap with empty distributions."""
    conn = _make_db_with_consensus()
    
    result = _compute_per_class_metrics_bootstrap(
        conn, method="keyword", 
        threshold_f1_distribution=[], 
        threshold_youden_distribution=[], 
        step=0.01
    )
    
    # Check that the result has the expected structure
    assert "f1_threshold_metrics" in result
    assert "youden_threshold_metrics" in result
    
    # Check that metrics are None when distributions are empty
    assert result["f1_threshold_metrics"] is None
    assert result["youden_threshold_metrics"] is None
