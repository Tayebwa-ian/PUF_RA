"""Baseline scoring exporters for the PUF screening study.

Produces eval JSONL files from non-LLM baselines so they can be ingested into
the database alongside the LLM-prompt evaluations.

- Deterministic baselines: keyword / BM25 / hybrid from ``src.relevance``.
- Embedding baseline: cosine similarity with the Uni Passau octen-embedding-8b
  API via ``src.embeddings.get_or_compute_embedding``.

All exporters write the same JSONL schema consumed by
``src.eval_store.ingest_eval_file``.

Usage:
    from src.baselines import export_baseline_jsonl, export_embedding_jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from src.relevance import evaluate_corpus, _baseline_eval_records, _classify
from src.embeddings import get_or_compute_embedding

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
    the embedding baseline. The record dicts come from the *same* helper
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


def _compute_topic_embedding(model_name: str = "octen-embedding-8b", api_key: str | None = None) -> list[float]:
    """Compute the topic embedding using the Uni Passau octen-embedding-8b API."""
    # Compute embedding for the topic sentence
    from src.embeddings import compute_embedding
    encoded_topic = f"- {TOPIC_SENTENCE}"
    return compute_embedding(encoded_topic, model=model_name, api_key=api_key)


def _compute_paper_embeddings_batched(conn: Any, model_name: str = "octen-embedding-8b", api_key: str | None = None) -> dict[int, list[float]]:
    """Compute embeddings for all papers in the database using batched API calls."""
    rows = conn.execute("SELECT id, title, abstract FROM papers ORDER BY id").fetchall()
    
    # First, get all existing embeddings from DB
    paper_embeddings_map: dict[int, list[float]] = {}
    papers_to_embed: list[tuple[int, str]] = []  # (paper_id, text)

    for row in rows:
        paper_id = row["id"]
        title = row["title"] or ""
        abstract = row["abstract"] or ""
        text = f"{title}\n{abstract}" if abstract else title

        # Try to get from database first
        from src.embeddings import get_embedding_from_db
        emb = get_embedding_from_db(conn, paper_id, model_name)
        if emb is not None:
            paper_embeddings_map[paper_id] = emb
        else:
            papers_to_embed.append((paper_id, text))

    # Batch embed papers that are not in DB
    if papers_to_embed:
        # Process in batches of 64
        batch_size = 64
        from src.embeddings import compute_embeddings_batch, store_embedding
        
        for i in range(0, len(papers_to_embed), batch_size):
            batch = papers_to_embed[i:i+batch_size]
            batch_texts = [text for _, text in batch]
            batch_embeddings = compute_embeddings_batch(batch_texts, model=model_name, api_key=api_key)
            
            for (paper_id, _), emb in zip(batch, batch_embeddings):
                paper_embeddings_map[paper_id] = emb
                # Store in database
                store_embedding(conn, paper_id, model_name, emb)

    return paper_embeddings_map


def _compute_cosine_similarity(embedding1: list[float], embedding2: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    # Convert to torch tensors
    t1 = torch.tensor(embedding1, dtype=torch.float32)
    t2 = torch.tensor(embedding2, dtype=torch.float32)
    
    # Compute cosine similarity
    sim = F.cosine_similarity(t1.unsqueeze(0), t2.unsqueeze(0), dim=1).item()
    return sim


def embedding_scores(conn: Any, model_name: str = "octen-embedding-8b", api_key: str | None = None) -> list[tuple[int, float]]:
    """Return ``(paper_id, cosine_similarity)`` for every paper vs the topic.

    Embeds each paper (title + abstract) and the study topic sentence with the
    Uni Passau octen-embedding-8b API and returns the cosine similarity to the topic.
    Shared by :func:`export_embedding_jsonl` and the embedding baseline of
    :func:`src.relevance.derive_threshold`, so threshold derivation can never
    drift from the baseline export.
    """
    # Compute topic embedding
    topic_emb = _compute_topic_embedding(model_name=model_name, api_key=api_key)
    
    # Compute paper embeddings using batched approach
    paper_embeddings = _compute_paper_embeddings_batched(conn, model_name=model_name, api_key=api_key)
    
    # Compute cosine similarities
    results = []
    for row in conn.execute("SELECT id FROM papers ORDER BY id").fetchall():
        paper_id = row["id"]
        if paper_id in paper_embeddings:
            sim = _compute_cosine_similarity(paper_embeddings[paper_id], topic_emb)
            results.append((paper_id, float(sim)))
        else:
            results.append((paper_id, 0.0))
            
    return results


def export_embedding_jsonl(
    conn: Any,
    out_path: str | Path,
    model_name: str = "octen-embedding-8b",
    threshold: float = 0.3,
    run: int = 1,
    api_key: str | None = None,
) -> int:
    """Compute embeddings and similarity-based eval JSONL using octen-embedding-8b API.

    Uses the Uni Passau-hosted octen-embedding-8b model via the OpenAI-compatible
    API to compute embeddings for each paper (title + abstract) and the topic
    sentence, then writes similarity-based eval JSONL with method="embedding".
    """
    rows = conn.execute(
        "SELECT id FROM papers ORDER BY id"
    ).fetchall()
    if not rows:
        return 0

    scores_dict = dict(embedding_scores(conn, model_name=model_name, api_key=api_key))
    records = []
    for row in rows:
        paper_id = row["id"]
        sim_f = scores_dict.get(paper_id, 0.0)
        # For embedding baseline, we classify based on score and threshold
        # The _classify function is designed for keyword/BM25 scores, so for embeddings
        # we use a simpler classification: score >= threshold -> in-scope, else out-of-scope
        # But we should still respect the 3-class rule based on keywords
        abstract_row = conn.execute("SELECT abstract FROM papers WHERE id = ?", (paper_id,)).fetchone()
        abstract = abstract_row["abstract"] or ""
        decision = _classify(abstract, sim_f, threshold)
        records.append({
            "eval_id": f"eval-{run}-{paper_id}",
            "paper_id": paper_id,
            "title": "",
            "method": "embedding",
            "model": model_name,
            "model_version": "",
            "prompt_id": "n/a",
            "decision": decision,
            "score": sim_f,
            "confidence": None,
            "matched_keywords": [],
            "rationale": f"octen-embedding-8b cosine similarity {sim_f:.4f} vs {threshold}",
            "temperature": 0.0,
            "run": run,
            "timestamp": "",
        })
    return _write_records(out_path, records)
