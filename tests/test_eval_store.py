"""Tests for the evaluation store, ground-truth agreement, and metrics."""

from __future__ import annotations

import json
from pathlib import Path

from src.db import get_connection
from src.db_schema import create_schema, table_exists
from src import eval_store


def _seed(conn, n_papers: int = 4):
    create_schema(conn)
    for i in range(1, n_papers + 1):
        conn.execute(
            """INSERT INTO papers
               (id, title, authors, year, abstract, publication_title, doi)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (i, f"Paper {i}", "A", 2020, f"abstract {i}", "Venue", f"10.0/{i}"),
        )
    conn.commit()


def _write_jsonl(path: Path, records: list[dict]):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_create_schema_includes_eval_tables(tmp_path):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        create_schema(conn)
        for t in ("ground_truth", "ground_truth_consensus", "eval_runs", "evals", "llm_judge"):
            assert table_exists(conn, t), t


def test_ingest_eval_file_and_idempotent(tmp_path):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        _seed(conn)
    jsonl = tmp_path / "evals.jsonl"
    _write_jsonl(jsonl, [
        {"eval_id": "e1", "paper_id": 1, "method": "baseline_hybrid",
         "model": "deterministic", "prompt_id": "n/a", "decision": "in-scope",
         "score": 0.5, "matched_keywords": ["power analysis"], "temperature": 0.0, "run": 1},
        {"eval_id": "e2", "paper_id": 2, "method": "baseline_hybrid",
         "model": "deterministic", "prompt_id": "n/a", "decision": "out-of-scope",
         "score": 0.1, "matched_keywords": [], "temperature": 0.0, "run": 1},
    ])
    with get_connection(db) as conn:
        res = eval_store.ingest_eval_file(conn, jsonl)
        assert res == {"files": 1, "runs": 1, "evals": 2}
        res2 = eval_store.ingest_eval_file(conn, jsonl)  # idempotent upsert
        assert res2["evals"] == 2  # not 4
        runs = eval_store.list_runs(conn)
        assert len(runs) == 1
        assert runs[0]["n_evals"] == 2


def test_ground_truth_agreement_and_consensus(tmp_path):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        _seed(conn, 4)
    csv = tmp_path / "gt.csv"
    csv.write_text(
        "paper_id,annotator_id,label,confidence,rationale\n"
        "1,A,in-scope,0.9,side-channel\n"
        "2,A,out-of-scope,0.9,modeling\n"
        "3,A,hybrid,0.8,side+ml\n"
        "4,A,in-scope,0.9,side-channel\n"
        "1,B,in-scope,0.9,side-channel\n"
        "2,B,out-of-scope,0.9,modeling\n"
        "3,B,hybrid,0.8,side+ml\n"
        "4,B,out-of-scope,0.9,modeling\n",
        encoding="utf-8",
    )
    with get_connection(db) as conn:
        res = eval_store.ingest_ground_truth(conn, csv)
        # po=0.75, pe=0.3125 -> kappa=0.63636...
        assert res["kappa"] is not None
        assert abs(res["kappa"] - 0.6363636) < 1e-3
        consensus = {c["paper_id"]: c["consensus_label"] for c in res["consensus"]}
        assert consensus[4] == "disagree"   # annotators disagreed
        assert consensus[1] == "in-scope"   # unanimous


def test_compute_metrics_perfect_on_consensus(tmp_path):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        _seed(conn, 4)
    # ground truth: p1 in-scope, p2 out-of-scope, p3 hybrid, p4 disagree
    csv = tmp_path / "gt.csv"
    csv.write_text(
        "paper_id,annotator_id,label,confidence,rationale\n"
        "1,A,in-scope,0.9,x\n2,A,out-of-scope,0.9,x\n3,A,hybrid,0.8,x\n"
        "1,B,in-scope,0.9,x\n2,B,out-of-scope,0.9,x\n3,B,hybrid,0.8,x\n",
        encoding="utf-8",
    )
    jsonl = tmp_path / "evals.jsonl"
    _write_jsonl(jsonl, [
        {"eval_id": "e1", "paper_id": 1, "method": "llm", "model": "m",
         "prompt_id": "P2", "decision": "in-scope", "score": 0.9, "temperature": 0.0, "run": 1},
        {"eval_id": "e2", "paper_id": 2, "method": "llm", "model": "m",
         "prompt_id": "P2", "decision": "out-of-scope", "score": 0.1, "temperature": 0.0, "run": 1},
        {"eval_id": "e3", "paper_id": 3, "method": "llm", "model": "m",
         "prompt_id": "P2", "decision": "hybrid", "score": 0.7, "temperature": 0.0, "run": 1},
        {"eval_id": "e4", "paper_id": 4, "method": "llm", "model": "m",
         "prompt_id": "P2", "decision": "out-of-scope", "score": 0.2, "temperature": 0.0, "run": 1},
    ])
    with get_connection(db) as conn:
        eval_store.ingest_ground_truth(conn, csv)
        eval_store.ingest_eval_file(conn, jsonl)
        run_id = eval_store.list_runs(conn)[0]["id"]
        m = eval_store.compute_metrics(conn, run_id)
        assert m["n"] == 3  # p4 excluded (disagree)
        assert m["accuracy"] == 1.0
        assert m["kappa"] == 1.0
        assert m["per_class"]["hybrid"]["f1"] == 1.0


def test_roc_auc_separable():
    assert eval_store._roc_auc([1, 0, 1, 0], [0.9, 0.2, 0.8, 0.1]) == 1.0
    assert eval_store._roc_auc([1, 0, 1, 0], [0.1, 0.9, 0.2, 0.8]) == 0.0
    assert eval_store._roc_auc([1, 0], [0.5, 0.5]) == 0.5  # degenerate tie -> undefined
    assert eval_store._roc_auc([1, 1], [0.9, 0.8]) is None  # no negatives
    assert eval_store._roc_auc([0, 0], [0.9, 0.8]) is None  # no positives


def test_export_paper_sheet(tmp_path):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        _seed(conn, 3)
        out = tmp_path / "sheet.csv"
        n = eval_store.export_paper_sheet(conn, out)
    assert n == 3
    text = out.read_text(encoding="utf-8")
    assert "paper_id,doi,title" in text
    assert "1,10.0/1," in text
