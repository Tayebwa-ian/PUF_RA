"""Baseline scoring exporters for the PUF screening study.

Produces eval JSONL files from non-LLM baselines so they can be ingested into
the database alongside the LLM-prompt evaluations.

- Deterministic baselines: keyword / BM25 / hybrid from ``src.relevance``.
- SBERT baseline: embedding similarity (optional; requires ``sentence-transformers``).

All exporters write the same JSONL schema consumed by
``src.eval_store.ingest_eval_file``.

Usage:
    from src.baselines import export_baseline_jsonl, export_sbert_jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.relevance import evaluate_corpus

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

    The baseline yields a continuous score, so the 3-class decision is mapped
    from the threshold (in-scope if relevant, else out-of-scope). ``hybrid`` is
    not distinguished by this baseline — a known limitation documented in the
    evaluation report.
    """
    results = evaluate_corpus(
        conn,
        method=method,
        keyword_weight=keyword_weight,
        bm25_weight=bm25_weight,
        threshold=threshold,
        store=False,
    )
    records = []
    for paper_id, score, is_relevant, details in results:
        matched = list((details.get("keyword_details") or {}).get("matched", {}).keys())
        records.append({
            "eval_id": f"eval-{run}-{paper_id}",
            "paper_id": paper_id,
            "title": "",
            "method": f"baseline_{method}",
            "model": "deterministic",
            "model_version": "",
            "prompt_id": "n/a",
            "decision": "in-scope" if is_relevant else "out-of-scope",
            "score": score,
            "confidence": None,
            "matched_keywords": matched,
            "rationale": f"hybrid score {score:.4f} vs threshold {threshold}",
            "temperature": 0.0,
            "run": run,
            "timestamp": "",
        })
    return _write_records(out_path, records)


def export_sbert_jsonl(
    conn: Any,
    out_path: str | Path,
    model_name: str = "all-MiniLM-L6-v2",
    threshold: float = 0.3,
    run: int = 1,
) -> int:
    """Embed abstracts and a topic sentence; write similarity-based eval JSONL.

    Requires ``sentence-transformers`` (optional dependency). Raises a clear
    error if it is not installed.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import torch  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "SBERT baseline requires 'sentence-transformers'. "
            "Install it (or skip this baseline) to use export_sbert_jsonl."
        ) from exc

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
        records.append({
            "eval_id": f"eval-{run}-{row['id']}",
            "paper_id": row["id"],
            "title": "",
            "method": "sbert",
            "model": model_name,
            "model_version": "",
            "prompt_id": "n/a",
            "decision": "in-scope" if sim_f >= threshold else "out-of-scope",
            "score": sim_f,
            "confidence": None,
            "matched_keywords": [],
            "rationale": f"SBERT cosine similarity {sim_f:.4f} vs {threshold}",
            "temperature": 0.0,
            "run": run,
            "timestamp": "",
        })
    return _write_records(out_path, records)
