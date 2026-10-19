"""Bootstrapping logic for threshold stability analysis.

This module provides functions to assess threshold stability for max-F1 and
Youden's J thresholds using bootstrapping with resampling of the
ground_truth_consensus table.
"""

from __future__ import annotations

import math
import random
import sqlite3
from typing import Any, Optional

from src.relevance import _scores_for_corpus


def bootstrap_threshold_stability(
    conn: sqlite3.Connection,
    method: str,
    criterion: str,
    n_bootstraps: int = 1000,
    sample_size: Optional[int] = None,
    step: float = 0.01,
) -> dict[str, Any]:
    """Resample ground_truth_consensus papers with replacement and derive thresholds.

    Args:
        conn: SQLite connection.
        method: Scoring method ('keyword', 'bm25', 'hybrid', 'embedding').
        criterion: 'f1' (max-F1) or 'youden' (max sensitivity + specificity - 1).
        n_bootstraps: Number of bootstrap iterations.
        sample_size: Number of papers to sample in each bootstrap. If None,
            uses the total number of labelled papers.
        step: Sweep granularity in score units for derive_threshold.

    Returns:
        A dictionary with:
        - threshold_f1_distribution: list of F1-derived thresholds
        - threshold_youden_distribution: list of Youden-derived thresholds
        - f1_threshold_ci_95: 95% CI for F1 threshold
        - youden_threshold_ci_95: 95% CI for Youden threshold
        - per_class_metrics_bootstrap: dict with per-class metric statistics
    """
    # Get all labelled papers from ground_truth_consensus
    labelled_papers = conn.execute(
        "SELECT paper_id, consensus_label FROM ground_truth_consensus"
    ).fetchall()

    # Filter to only in-scope, out-of-scope, and hybrid
    labelled_data = []
    for row in labelled_papers:
        label = row["consensus_label"]
        if label == "in-scope":
            labelled_data.append((row["paper_id"], 1))
        elif label in ("out-of-scope", "hybrid"):
            labelled_data.append((row["paper_id"], 0))
        # 'disagree' is skipped

    if not labelled_data:
        raise ValueError("No labelled papers found in ground_truth_consensus")

    if sample_size is None:
        sample_size = len(labelled_data)

    # Compute scores once using the original connection
    if method == "embedding":
        import os
        api_key = os.environ.get("UNIPASSAU_EMBEDDING_API_KEY")
        from src.baselines import embedding_scores
        scored = embedding_scores(conn, model_name="octen-embedding-8b", api_key=api_key)
        scores_dict = dict(scored)
    else:
        scores_dict = dict(_scores_for_corpus(conn, method))

    # Resample with replacement
    random.seed(42)  # For reproducibility

    threshold_f1_distribution: list[float] = []
    threshold_youden_distribution: list[float] = []

    for _ in range(n_bootstraps):
        # Sample with replacement - this gives us a list of (paper_id, label) tuples
        # with possible duplicates
        bootstrap_sample = random.choices(labelled_data, k=sample_size)

        # Get unique paper_ids and their labels from the bootstrap sample
        # If a paper_id appears multiple times, we only include it once
        unique_bootstrap_data = {}
        for paper_id, label in bootstrap_sample:
            if paper_id not in unique_bootstrap_data:
                unique_bootstrap_data[paper_id] = label

        # Convert to list of (paper_id, label)
        unique_bootstrap_list = [(pid, label) for pid, label in unique_bootstrap_data.items()]

        # Create labelled dict for threshold derivation
        labelled: dict[int, int] = {}
        for paper_id, label in unique_bootstrap_list:
            if label == 1:
                labelled[paper_id] = 1
            else:
                labelled[paper_id] = 0

        # Compute pairs of (score, y) for the bootstrap sample
        pairs = [(scores_dict.get(pid, 0.0), y) for pid, y in labelled.items() if pid in scores_dict]
        n_pos = sum(y for _, y in pairs)
        n_neg = len(pairs) - n_pos

        if n_pos == 0 or n_neg == 0:
            continue

        # Derive thresholds by sweeping candidate thresholds
        # The candidate grid must cover the OBSERVED score range
        observed = [s for s, _ in pairs]
        start = min(0.0, min(observed) - 1e-3)
        end = max(1.0, max(observed) + 1e-3)
        n_steps = max(1, int(math.ceil((end - start) / step)))

        f1_threshold = None
        best_f1_value = -1.0
        for i in range(n_steps + 1):
            t = round(start + i * step, 10)
            tp = sum(1 for s, y in pairs if s >= t and y == 1)
            fp = sum(1 for s, y in pairs if s >= t and y == 0)
            fn = n_pos - tp
            tn = n_neg - fp
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            value = (
                2 * precision * recall / (precision + recall)
                if (precision + recall)
                else 0.0
            )
            if value > best_f1_value:
                best_f1_value = value
                f1_threshold = t

        if f1_threshold is not None:
            threshold_f1_distribution.append(f1_threshold)

        youden_threshold = None
        best_youden_value = -1.0
        for i in range(n_steps + 1):
            t = round(start + i * step, 10)
            tp = sum(1 for s, y in pairs if s >= t and y == 1)
            fp = sum(1 for s, y in pairs if s >= t and y == 0)
            fn = n_pos - tp
            tn = n_neg - fp
            value = tp / n_pos + tn / n_neg - 1.0
            if value > best_youden_value:
                best_youden_value = value
                youden_threshold = t

        if youden_threshold is not None:
            threshold_youden_distribution.append(youden_threshold)

    # Compute 95% CIs
    f1_threshold_ci_95: Optional[tuple[float, float]] = None
    if threshold_f1_distribution:
        sorted_f1 = sorted(threshold_f1_distribution)
        lower_idx = int(0.025 * len(sorted_f1))
        upper_idx = int(0.975 * len(sorted_f1))
        f1_threshold_ci_95 = (sorted_f1[lower_idx], sorted_f1[upper_idx])

    youden_threshold_ci_95: Optional[tuple[float, float]] = None
    if threshold_youden_distribution:
        sorted_youden = sorted(threshold_youden_distribution)
        lower_idx = int(0.025 * len(sorted_youden))
        upper_idx = int(0.975 * len(sorted_youden))
        youden_threshold_ci_95 = (sorted_youden[lower_idx], sorted_youden[upper_idx])

    # Compute per-class metrics on the original 50 papers using the derived thresholds
    per_class_metrics_bootstrap = _compute_per_class_metrics_bootstrap(
        conn, method, threshold_f1_distribution, threshold_youden_distribution, step
    )

    return {
        "threshold_f1_distribution": threshold_f1_distribution,
        "threshold_youden_distribution": threshold_youden_distribution,
        "f1_threshold_ci_95": f1_threshold_ci_95,
        "youden_threshold_ci_95": youden_threshold_ci_95,
        "per_class_metrics_bootstrap": per_class_metrics_bootstrap,
    }


def _compute_per_class_metrics_bootstrap(
    conn: sqlite3.Connection,
    method: str,
    threshold_f1_distribution: list[float],
    threshold_youden_distribution: list[float],
    step: float,
) -> dict[str, Any]:
    """Compute per-class metric statistics using bootstrap-derived thresholds."""
    # Get all labelled papers from ground_truth_consensus
    labelled_papers = conn.execute(
        "SELECT paper_id, consensus_label FROM ground_truth_consensus"
    ).fetchall()

    # Map to (paper_id, true_label) where true_label is 1 for in-scope, 0 otherwise
    labelled_data = []
    for row in labelled_papers:
        label = row["consensus_label"]
        if label == "in-scope":
            labelled_data.append((row["paper_id"], 1))
        elif label in ("out-of-scope", "hybrid"):
            labelled_data.append((row["paper_id"], 0))

    if not labelled_data:
        return {}

    # Get scores for all papers
    if method == "embedding":
        import os
        api_key = os.environ.get("UNIPASSAU_EMBEDDING_API_KEY")
        from src.baselines import embedding_scores
        scored = embedding_scores(conn, model_name="octen-embedding-8b", api_key=api_key)
        scores_dict = dict(scored)
    else:
        scores_dict = dict(_scores_for_corpus(conn, method))

    # Compute metrics for F1 threshold
    f1_metrics = {"precision": [], "recall": [], "f1": []}
    if threshold_f1_distribution:
        # Use the median F1 threshold
        sorted_f1 = sorted(threshold_f1_distribution)
        median_f1_threshold = sorted_f1[len(sorted_f1) // 2]

        # Compute metrics
        tp = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) >= median_f1_threshold and y == 1)
        fp = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) >= median_f1_threshold and y == 0)
        fn = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) < median_f1_threshold and y == 1)
        tn = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) < median_f1_threshold and y == 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        f1_metrics["precision"].append(precision)
        f1_metrics["recall"].append(recall)
        f1_metrics["f1"].append(f1)

    # Compute metrics for Youden threshold
    youden_metrics = {"precision": [], "recall": [], "f1": []}
    if threshold_youden_distribution:
        # Use the median Youden threshold
        sorted_youden = sorted(threshold_youden_distribution)
        median_youden_threshold = sorted_youden[len(sorted_youden) // 2]

        # Compute metrics
        tp = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) >= median_youden_threshold and y == 1)
        fp = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) >= median_youden_threshold and y == 0)
        fn = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) < median_youden_threshold and y == 1)
        tn = sum(1 for pid, y in labelled_data if scores_dict.get(pid, 0) < median_youden_threshold and y == 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        youden_metrics["precision"].append(precision)
        youden_metrics["recall"].append(recall)
        youden_metrics["f1"].append(f1)

    return {
        "f1_threshold_metrics": f1_metrics if f1_metrics["precision"] else None,
        "youden_threshold_metrics": youden_metrics if youden_metrics["precision"] else None,
    }
