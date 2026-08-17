"""Baseline scoring exporters for the PUF screening study.

Produces eval JSONL files from non-LLM baselines so they can be ingested into
the database alongside the LLM-prompt evaluations.

- Deterministic baselines: keyword / BM25 / hybrid from ``src.relevance``.
- SBERT baseline: embedding similarity with ``sentence-transformers`` — a
  **first-class, non-optional** method of the study (its dependencies are
  installed; see ``docs/evaluation.md``). Default model: ``all-MiniLM-L6-v2``.

All exporters write the same JSONL schema consumed by
``src.eval_store.ingest_eval_file``.

Usage:
    from src.baselines import export_baseline_jsonl, export_sbert_jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.relevance import evaluate_corpus, _baseline_eval_records, _classify

TOPIC_SENTENCE = (
    "physical attack on physically unclonable function side-channel analysis "
    "fault injection invasive semi-invasive probing"
)


def _write_records(out_path: Path, records: list[dict[str, Any]]) -> int:
    out_path = Path(out_path)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def export_baseline_jsonl(
    conn: Any,
    out_path: str | Path,
    method: str = "hybrid",
    keyword_weight: float = 0.4,
    bm25_weight: float = 0.6,
    threshold: float = 0.15,
    run: int = 1,
) -> int:
    """Run the deterministic relevance engine and write eval JSONL.

    The 3-class decision (in-scope / out-of-scope / hybrid) is produced by the
    shared ``_classify`` rule, so it aligns exactly with ``evaluate_corpus`` and
    the SBERT baseline. The record dicts come from the *same* helper
    (``relevance._baseline_eval_records``) that ``evaluate_corpus`` ingests with,
    so the JSONL and the in-memory ingestion can never drift. Nothing is
    persisted here (``store=False``); ingest the file with
    ``eval_store.ingest_eval_file``.
    """
    results = evaluate_corpus(
        conn,
        method=method,
        keyword_weight=keyword_weight,
        bm25_weight=bm25_weight,
        threshold=threshold,
        store=False,
    )
    records = _baseline_eval_records(results, method, threshold, run)
    return _write_records(out_path, records)


def export_sbert_jsonl(
    conn: Any,
    out_path: str | Path,
    model_name: str = "all-MiniLM-L6-v2",
    threshold: float = 0.3,
    run: int = 1,
) -> int:
    """Embed abstracts and a topic sentence; write similarity-based eval JSONL.

    ``sentence-transformers`` is a first-class dependency (installed in the
    project venv), so this no longer raises when the import is missing. The
    default model is ``all-MiniLM-L6-v2`` (the one documented in
    ``docs/relevance.md`` / ``docs/evaluation.md``); ``eval_runs.model`` records
    the model name that was actually used.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore
    import torch  # type: ignore

    rows = conn.execute(
        "SELECT id, abstract FROM papers ORDER BY id"
    ).fetchall()
    if not rows:
        return 0

    abstracts = [r["abstract"] or "" for r in rows]
    model = SentenceTransformer(model_name)
    topic_emb = model.encode([TOPIC_SENTENCE], convert_to_tensor=True)
    doc_embs = model.encode(abstracts, convert_to_tensor=True)
    cos = torch.nn.functional.cosine_similarity(doc_embs, topic_emb, dim=1)

    records = []
    for row, sim in zip(rows, cos.tolist()):
        sim_f = float(sim)
        decision = _classify(row["abstract"] or "", sim_f, threshold)
        records.append({
            "eval_id": f"eval-{run}-{row['id']}",
            "paper_id": row["id"],
            "title": "",
            "method": "sbert",
            "model": model_name,
            "model_version": "",
            "prompt_id": "n/a",
            "decision": decision,
            "score": sim_f,
            "confidence": None,
            "matched_keywords": [],
            "rationale": f"SBERT cosine similarity {sim_f:.4f} vs {threshold}",
            "temperature": 0.0,
            "run": run,
            "timestamp": "",
        })
    return _write_records(out_path, records)
