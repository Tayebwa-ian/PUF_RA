"""Embedding API client and storage for paper embeddings.

This module provides an OpenAI-compatible embedding API client for the
Uni Passau hosted octen-embedding-8b model, and functions to store and
retrieve embeddings from the paper_embeddings table to avoid recomputation.

Batched embedding computation is supported via `compute_embeddings_batch`.

Usage:
    from src.embeddings import get_or_compute_embedding, compute_embeddings_batch

    with get_connection("results.db") as conn:
        embedding = get_or_compute_embedding(conn, paper_id=42)
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

import httpx


API_ENDPOINT = "https://llms.innkube.fim.uni-passau.de/v1/embeddings"
DEFAULT_MODEL = "octen-embedding-8b"


def _get_api_key(api_key: Optional[str] = None) -> str:
    """Get the API key from the provided parameter or environment variable.

    The API key is never logged or exposed in error messages for security.
    """
    if api_key is not None:
        return api_key
    env_key = os.environ.get("UNIPASSAU_EMBEDDING_API_KEY")
    if env_key is not None:
        return env_key
    env_key_fallback = os.environ.get("UNI_PASSAU_API_KEY")
    if env_key_fallback is not None:
        return env_key_fallback
    raise ValueError(
        "Uni Passau embedding API key is required. "
        "Provide it via the api_key parameter or the UNIPASSAU_EMBEDDING_API_KEY or UNI_PASSAU_API_KEY environment variable."
    )


def _encode_text_for_document(text: str) -> str:
    """Encode text for document embedding as per model's known issue.

    The octen-embedding-8b model requires a "- " prefix for document encoding.
    """
    return f"- {text}"


def compute_embedding(text: str, model: str = DEFAULT_MODEL, api_key: Optional[str] = None) -> list[float]:
    """Compute embedding for a text using the Uni Passau octen-embedding-8b API.

    Args:
        text: The text to embed (title + abstract with "- " prefix).
        model: The model name to use (default: octen-embedding-8b).
        api_key: The API key for the Uni Passau embedding API.

    Returns:
        A list of floats representing the embedding vector.
    """
    key = _get_api_key(api_key)
    encoded_text = _encode_text_for_document(text)

    payload = {
        "model": model,
        "input": [encoded_text],
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    with httpx.Client() as client:
        response = client.post(API_ENDPOINT, json=payload, headers=headers, timeout=30.0)
        response.raise_for_status()

    data = response.json()
    embedding = data["data"][0]["embedding"]
    return embedding


def compute_embeddings_batch(texts: list[str], model: str = DEFAULT_MODEL, api_key: Optional[str] = None) -> list[list[float]]:
    """Compute embeddings for a list of texts using the Uni Passau octen-embedding-8b API.

    Args:
        texts: A list of texts to embed (each text will be pre-encoded with "- " prefix internally).
        model: The model name to use (default: octen-embedding-8b).
        api_key: The API key for the Uni Passau embedding API.

    Returns:
        A list of lists of floats representing the embedding vectors, in the same order as the input texts.
    """
    key = _get_api_key(api_key)
    encoded_texts = [_encode_text_for_document(text) for text in texts]

    payload = {
        "model": model,
        "input": encoded_texts,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    with httpx.Client() as client:
        response = client.post(API_ENDPOINT, json=payload, headers=headers, timeout=60.0)
        response.raise_for_status()

    data = response.json()
    # data["data"] is a list of objects with "embedding" and "index"
    # The OpenAI-compatible API returns them in the same order as the input
    embeddings = [item["embedding"] for item in data["data"]]
    return embeddings


def store_embedding(conn: sqlite3.Connection, paper_id: int, model_name: str, embedding: list[float]) -> None:
    """Store an embedding in the paper_embeddings table.

    Args:
        conn: SQLite connection.
        paper_id: The paper ID.
        model_name: The model name used to compute the embedding.
        embedding: The embedding vector as a list of floats.
    """
    embedding_vector = json.dumps(embedding)
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_embeddings
            (paper_id, model_name, embedding_vector, computed_at)
        VALUES (?, ?, ?, current_timestamp)
        """,
        (paper_id, model_name, embedding_vector),
    )
    conn.commit()


def get_embedding_from_db(conn: sqlite3.Connection, paper_id: int, model_name: str = DEFAULT_MODEL) -> Optional[list[float]]:
    """Retrieve an embedding from the paper_embeddings table.

    Args:
        conn: SQLite connection.
        paper_id: The paper ID.
        model_name: The model name to retrieve.

    Returns:
        The embedding vector as a list of floats, or None if not found.
    """
    row = conn.execute(
        """
        SELECT embedding_vector FROM paper_embeddings
        WHERE paper_id = ? AND model_name = ?
        """,
        (paper_id, model_name),
    ).fetchone()

    if row is None:
        return None

    return json.loads(row["embedding_vector"])


def get_or_compute_embedding(
    conn: sqlite3.Connection,
    paper_id: int,
    model_name: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
) -> list[float]:
    """Retrieve an embedding from the database or compute it via the API.

    First attempts to retrieve the embedding from the paper_embeddings table.
    If not found, computes it via the Uni Passau octen-embedding-8b API and
    stores it in the database.

    Args:
        conn: SQLite connection.
        paper_id: The paper ID.
        model_name: The model name to use (default: octen-embedding-8b).
        api_key: The API key for the Uni Passau embedding API.

    Returns:
        The embedding vector as a list of floats.
    """
    # Try to get from database
    embedding = get_embedding_from_db(conn, paper_id, model_name)
    if embedding is not None:
        return embedding

    # Compute via API
    # Fetch paper title and abstract
    row = conn.execute(
        "SELECT title, abstract FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()

    if not row:
        raise ValueError(f"Paper {paper_id} not found")

    title = row["title"] or ""
    abstract = row["abstract"] or ""

    # Combine title and abstract for embedding
    text = f"{title}\n{abstract}" if abstract else title

    # Compute embedding
    embedding = compute_embedding(text, model=model_name, api_key=api_key)

    # Store in database
    store_embedding(conn, paper_id, model_name, embedding)

    return embedding
