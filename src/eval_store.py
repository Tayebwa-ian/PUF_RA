"""Evaluation storage and analysis for the PUF screening study.

This module persists and analyses the study's evaluation data:

- Eval JSONL files (baseline / SBERT / LLM prompts) -> `eval_runs` + `evals`.
- Human ground-truth CSV -> `ground_truth` + `ground_truth_consensus`, with
  inter-rater agreement (Cohen's / Fleiss' kappa).
- Per-method metrics vs the consensus gold standard.

All labels use the locked three-class scheme:
`in-scope`, `out-of-scope`, `hybrid`.

Usage:
    from src.eval_store import ingest_eval_file, ingest_ground_truth, compute_metrics

    with get_connection("results.db") as conn:
        ingest_eval_file(conn, Path("data/evals/llm_p2_gpt4o.jsonl"))
        ingest_ground_truth(conn, Path("data/ground_truth/ground_truth.csv"))
        print(compute_metrics(conn, run_id=1))
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

LABELS = ("in-scope", "out-of-scope", "hybrid")


# ---------------------------------------------------------------------------
# Eval ingestion (JSONL -> eval_runs + evals)
# ---------------------------------------------------------------------------

def _run_key(
    method: str,
    model: str,
    model_version: str,
    prompt_id: str,
    temperature: float,
    run_index: int,
) -> tuple:
    return (
        method,
        model,
        model_version or "",
        prompt_id or "",
        float(temperature if temperature is not None else 0.0),
        int(run_index if run_index is not None else 1),
    )


def _get_or_create_run(conn: sqlite3.Connection, key: tuple) -> int:
    method, model, model_version, prompt_id, temperature, run_index = key
    row = conn.execute(
        """
        SELECT id FROM eval_runs
        WHERE method=? AND model=? AND model_version=? AND prompt_id=?
          AND temperature=? AND run_index=?
        """,
        (method, model, model_version, prompt_id, temperature, run_index),
    ).fetchone()
    if row is not None:
        return row[0]
    (run_id,) = conn.execute(
        """
        INSERT INTO eval_runs
            (method, model, model_version, prompt_id, temperature, run_index)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (method, model, model_version, prompt_id, temperature, run_index),
    ).fetchone()
    return run_id


def _upsert_eval(conn: sqlite3.Connection, run_id: int, record: dict) -> None:
    decision = record.get("decision")
    if decision not in LABELS:
        raise ValueError(f"Invalid decision label: {decision!r}")
    matched = record.get("matched_keywords")
    if isinstance(matched, list):
        matched = json.dumps(matched)
    conn.execute(
        """
        INSERT INTO evals
            (run_id, paper_id, decision, score, confidence, rationale,
             matched_keywords, latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, paper_id) DO UPDATE SET
            decision=excluded.decision,
            score=excluded.score,
            confidence=excluded.confidence,
            rationale=excluded.rationale,
            matched_keywords=excluded.matched_keywords,
            latency_ms=excluded.latency_ms
        """,
        (
            run_id,
            int(record["paper_id"]),
            decision,
            record.get("score"),
            record.get("confidence"),
            record.get("rationale"),
            matched,
            record.get("latency_ms"),
        ),
    )


def ingest_eval_file(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    """Read one eval JSONL file and insert its records.

    Returns a summary dict with counts of runs and evals inserted.
    """
    path = Path(path)
    n_runs = 0
    n_evals = 0
    seen_runs: set[tuple] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            key = _run_key(
                record.get("method", "unknown"),
                record.get("model", "unknown"),
                record.get("model_version", ""),
                record.get("prompt_id", "n/a"),
                record.get("temperature", 0.0),
                record.get("run", 1),
            )
            run_id = _get_or_create_run(conn, key)
            if key not in seen_runs:
                seen_runs.add(key)
                n_runs += 1
            _upsert_eval(conn, run_id, record)
            n_evals += 1
    conn.commit()
    return {"files": 1, "runs": n_runs, "evals": n_evals}


def ingest_eval_files(conn: sqlite3.Connection, paths: list[Path]) -> dict[str, int]:
    """Ingest multiple eval JSONL files."""
    total: dict[str, int] = {"files": 0, "runs": 0, "evals": 0}
    for p in paths:
        res = ingest_eval_file(conn, p)
        total["files"] += res["files"]
        total["runs"] += res["runs"]
        total["evals"] += res["evals"]
    return total


# ---------------------------------------------------------------------------
# Ground-truth ingestion + inter-rater agreement
# ---------------------------------------------------------------------------

def ingest_ground_truth(conn: sqlite3.Connection, csv_path: Path) -> dict[str, Any]:
    """Ingest a ground-truth CSV and recompute consensus + agreement.

    The CSV must have the header:
        paper_id,annotator_id,label,confidence,rationale
    Returns a summary including the overall kappa.
    """
    csv_path = Path(csv_path)
    rows: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            label = (r.get("label") or "").strip()
            if label not in LABELS:
                raise ValueError(f"Invalid ground-truth label: {label!r}")
            paper_id = int(r["paper_id"])
            conn.execute(
                """
                INSERT INTO ground_truth
                    (paper_id, annotator_id, label, confidence, rationale)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, annotator_id) DO UPDATE SET
                    label=excluded.label,
                    confidence=excluded.confidence,
                    rationale=excluded.rationale
                """,
                (
                    paper_id,
                    (r.get("annotator_id") or "").strip(),
                    label,
                    _to_float(r.get("confidence")),
                    (r.get("rationale") or "").strip(),
                ),
            )
            rows.append({"paper_id": paper_id, "annotator_id": r.get("annotator_id"), "label": label})

    consensus = _compute_consensus(conn)
    kappa = _overall_kappa(conn)
    conn.commit()
    return {
        "rows": len(rows),
        "papers": len(consensus),
        "kappa": kappa,
        "consensus": consensus,
    }


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _compute_consensus(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Recompute and store consensus labels for every labeled paper."""
    conn.execute("DELETE FROM ground_truth_consensus")
    by_paper: dict[int, list[str]] = {}
    for row in conn.execute(
        "SELECT paper_id, label FROM ground_truth ORDER BY paper_id"
    ):
        by_paper.setdefault(row["paper_id"], []).append(row["label"])

    results = []
    for paper_id, labels in by_paper.items():
        n = len(labels)
        majority = max(set(labels), key=labels.count)
        n_agree = labels.count(majority)
        if n_agree == n:
            consensus_label = majority
            method = "unanimous"
        else:
            consensus_label = "disagree"
            method = "majority"
        conn.execute(
            """
            INSERT INTO ground_truth_consensus
                (paper_id, consensus_label, n_annotators, n_agree, method)
            VALUES (?, ?, ?, ?, ?)
            """,
            (paper_id, consensus_label, n, n_agree, method),
        )
        results.append({
            "paper_id": paper_id,
            "consensus_label": consensus_label,
            "n_annotators": n,
            "n_agree": n_agree,
        })
    return results


def _overall_kappa(conn: sqlite3.Connection) -> Optional[float]:
    """Inter-rater agreement across all labeled papers.

    Cohen's kappa for exactly 2 annotators; Fleiss' kappa for >2.
    Returns None when fewer than 2 annotators labeled anything.
    """
    rows = conn.execute(
        "SELECT paper_id, annotator_id, label FROM ground_truth"
    ).fetchall()
    if not rows:
        return None

    annotators = sorted({r["annotator_id"] for r in rows})
    n_raters = len(annotators)
    if n_raters < 2:
        return None

    by_paper: dict[int, dict[str, str]] = {}
    for r in rows:
        by_paper.setdefault(r["paper_id"], {})[r["annotator_id"]] = r["label"]

    if n_raters == 2:
        a, b = annotators
        po = 0
        n = 0
        p_a: dict[str, float] = {l: 0.0 for l in LABELS}
        p_b: dict[str, float] = {l: 0.0 for l in LABELS}
        for paper_id, labs in by_paper.items():
            if a in labs and b in labs:
                n += 1
                if labs[a] == labs[b]:
                    po += 1
                p_a[labs[a]] += 1
                p_b[labs[b]] += 1
        if n == 0:
            return None
        po /= n
        pe = sum((p_a[l] / n) * (p_b[l] / n) for l in LABELS)
        return _safe_kappa(po, pe)

    # Fleiss' kappa for >2 raters
    n = len(by_paper)
    if n == 0:
        return None
    p_c: dict[str, float] = {l: 0.0 for l in LABELS}
    p_bar = 0.0
    for paper_id, labs in by_paper.items():
        counts = {l: 0 for l in LABELS}
        for ann in annotators:
            if ann in labs:
                counts[labs[ann]] += 1
        for l in LABELS:
            p_c[l] += counts[l]
            p_bar += (counts[l] ** 2 - n_raters) / (n_raters * (n_raters - 1))
        # accumulate per-category proportions
    for l in LABELS:
        p_c[l] /= (n * n_raters)
    p_bar /= n
    pe = sum(v * v for v in p_c.values())
    return _safe_kappa(p_bar, pe)


def _safe_kappa(observed: float, expected: float) -> Optional[float]:
    if expected >= 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


# ---------------------------------------------------------------------------
# Metrics: method decisions vs consensus gold standard
# ---------------------------------------------------------------------------

def compute_metrics(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Compute per-method metrics against the consensus gold standard.

    Returns accuracy, macro P/R/F1, Cohen's kappa vs consensus, a 3x3 confusion
    matrix, and a binary ROC-AUC treating `in-scope` as positive.
    """
    consensus = {
        r["paper_id"]: r["consensus_label"]
        for r in conn.execute(
            "SELECT paper_id, consensus_label FROM ground_truth_consensus"
        )
    }
    evals = conn.execute(
        "SELECT paper_id, decision, score FROM evals WHERE run_id=?", (run_id,)
    ).fetchall()

    pairs = [
        (e["paper_id"], e["decision"], e["score"])
        for e in evals
        if e["paper_id"] in consensus and consensus[e["paper_id"]] != "disagree"
    ]

    confusion: dict[str, dict[str, int]] = {g: {p: 0 for p in LABELS} for g in LABELS}
    y_true_bin, y_score_bin = [], []
    for paper_id, decision, score in pairs:
        gold = consensus[paper_id]
        confusion[gold][decision] += 1
        y_true_bin.append(1 if gold == "in-scope" else 0)
        y_score_bin.append(score if score is not None else (1.0 if decision == "in-scope" else 0.0))

    total = len(pairs)
    correct = sum(1 for p, d, _ in pairs if consensus[p] == d)
    accuracy = correct / total if total else 0.0

    per_class = {}
    for cls in LABELS:
        tp = confusion[cls][cls]
        fp = sum(confusion[g][cls] for g in LABELS if g != cls)
        fn = sum(confusion[cls][p] for p in LABELS if p != cls)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) else 0.0
        )
        per_class[cls] = {"precision": precision, "recall": recall, "f1": f1}

    macro_p = sum(per_class[c]["precision"] for c in LABELS) / 3
    macro_r = sum(per_class[c]["recall"] for c in LABELS) / 3
    macro_f1 = sum(per_class[c]["f1"] for c in LABELS) / 3

    kappa = _cohen_kappa_pairs([(consensus[p], d) for p, d, _ in pairs])
    auc = _roc_auc(y_true_bin, y_score_bin)

    return {
        "run_id": run_id,
        "n": total,
        "accuracy": accuracy,
        "macro_precision": macro_p,
        "macro_recall": macro_r,
        "macro_f1": macro_f1,
        "per_class": per_class,
        "kappa": kappa,
        "auc_in_scope": auc,
        "confusion": confusion,
    }


def _cohen_kappa_pairs(pairs: list[tuple[str, str]]) -> Optional[float]:
    if not pairs:
        return None
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    n = len(a)
    po = sum(1 for i in range(n) if a[i] == b[i]) / n
    p_a = {l: a.count(l) / n for l in LABELS}
    p_b = {l: b.count(l) / n for l in LABELS}
    pe = sum(p_a[l] * p_b[l] for l in LABELS)
    return _safe_kappa(po, pe)


def _roc_auc(y_true: list[int], y_score: list[float]) -> Optional[float]:
    """Binary ROC-AUC via the Mann-Whitney rank statistic (pure Python)."""
    pos = [s for t, s in zip(y_true, y_score) if t == 1]
    neg = [s for t, s in zip(y_true, y_score) if t == 0]
    if not pos or not neg:
        return None
    combined = sorted(y_score)
    rank: dict[float, float] = {}
    for i, v in enumerate(combined):
        if v not in rank:
            ties = [j for j, x in enumerate(combined) if x == v]
            rank[v] = (ties[0] + ties[-1]) / 2.0 + 1.0  # average rank (1-based)
    rank_sum = sum(rank[s] for s in pos)
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all eval runs with their eval counts."""
    return conn.execute(
        """
        SELECT r.id, r.method, r.model, r.model_version, r.prompt_id,
               r.temperature, r.run_index, COUNT(e.id) AS n_evals
        FROM eval_runs r
        LEFT JOIN evals e ON e.run_id = r.id
        GROUP BY r.id
        ORDER BY r.id
        """
    ).fetchall()


def export_paper_sheet(conn: sqlite3.Connection, out_path: Path) -> int:
    """Write a fill-in CSV skeleton (paper_id, doi, title, ...) for annotators."""
    out_path = Path(out_path)
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["paper_id", "doi", "title", "annotator_id", "label", "confidence", "rationale"])
        for row in conn.execute(
            "SELECT id, doi, title FROM papers ORDER BY id"
        ):
            writer.writerow([row["id"], row["doi"] or "", row["title"], "", "", "", ""])
            n += 1
    return n


def ingest_eval_records(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, int]:
    """Insert already-parsed eval records into eval_runs + evals.

    Records follow the same schema as JSONL files consumed by
    :func:`ingest_eval_file`. This lets callers (e.g. the deterministic baseline
    in ``src.relevance``) ingest in-memory results without round-tripping through
    a JSONL file. Returns a summary dict with counts of runs and evals inserted.
    """
    n_runs = 0
    n_evals = 0
    seen_runs: set[tuple] = set()
    for record in records:
        key = _run_key(
            record.get("method", "unknown"),
            record.get("model", "unknown"),
            record.get("model_version", ""),
            record.get("prompt_id", "n/a"),
            record.get("temperature", 0.0),
            record.get("run", 1),
        )
        run_id = _get_or_create_run(conn, key)
        if key not in seen_runs:
            seen_runs.add(key)
            n_runs += 1
        _upsert_eval(conn, run_id, record)
        n_evals += 1
    conn.commit()
    return {"runs": n_runs, "evals": n_evals}
