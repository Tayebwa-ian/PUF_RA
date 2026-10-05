"""Tests for the embeddings module."""

import json
import sqlite3
from pathlib import Path
from unittest import mock

import httpx
import pytest

from src.db_schema import create_schema
from src.embeddings import (
    _get_api_key,
    _encode_text_for_document,
    compute_embedding,
    store_embedding,
    get_embedding_from_db,
    get_or_compute_embedding,
    DEFAULT_MODEL,
    API_ENDPOINT,
)
from src import baselines


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
]


# ---------------------------------------------------------------------------
# API key functions
# ---------------------------------------------------------------------------

def test_get_api_key_with_parameter():
    """Test getting API key from parameter."""
    key = _get_api_key("test_api_key")
    assert key == "test_api_key"


def test_get_api_key_with_environment_variable(monkeypatch):
    """Test getting API key from environment variable."""
    monkeypatch.setenv("UNIPASSAU_EMBEDDING_API_KEY", "env_api_key")
    key = _get_api_key(None)
    assert key == "env_api_key"


def test_get_api_key_raises_without_key():
    """Test that _get_api_key raises when no key is provided."""
    with pytest.raises(ValueError, match="Uni Passau embedding API key is required"):
        _get_api_key(None)


# ---------------------------------------------------------------------------
# Text encoding functions
# ---------------------------------------------------------------------------

def test_encode_text_for_document():
    """Test that text is encoded with '- ' prefix for document encoding."""
    text = "This is a test abstract."
    encoded = _encode_text_for_document(text)
    assert encoded == "- This is a test abstract."


# ---------------------------------------------------------------------------
# Embedding storage functions
# ---------------------------------------------------------------------------

def test_store_embedding_and_retrieve():
    """Test storing and retrieving an embedding from the database."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    paper_id = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p1",)).fetchone()["id"]
    
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    
    # Store embedding
    store_embedding(conn, paper_id, DEFAULT_MODEL, embedding)
    
    # Retrieve embedding
    retrieved = get_embedding_from_db(conn, paper_id, DEFAULT_MODEL)
    assert retrieved == embedding
    
    # Test retrieval with different model returns None
    retrieved_other = get_embedding_from_db(conn, paper_id, "other-model")
    assert retrieved_other is None


def test_paper_embeddings_table_schema():
    """Test that the paper_embeddings table has the correct schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    
    # Check table exists
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t["name"] for t in tables]
    assert "paper_embeddings" in table_names
    
    # Check columns
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_embeddings)").fetchall()}
    assert "paper_id" in columns
    assert "model_name" in columns
    assert "embedding_vector" in columns
    assert "computed_at" in columns


# ---------------------------------------------------------------------------
# API client functions (mocked)
# ---------------------------------------------------------------------------

def test_compute_embedding_success(monkeypatch):
    """Test successful embedding computation via the API."""
    # Mock the httpx response
    mock_response = mock.Mock()
    mock_response.json.return_value = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3, 0.4, 0.5]}
        ]
    }
    mock_response.raise_for_status = mock.Mock()
    
    # Create a proper context manager mock for httpx.Client
    mock_client = mock.MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_response
    
    # Patch httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)
    
    embedding = compute_embedding("test text", model=DEFAULT_MODEL, api_key="test_key")
    assert embedding == [0.1, 0.2, 0.3, 0.4, 0.5]
    
    # Verify the request was made correctly
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == API_ENDPOINT
    payload = call_args[1]["json"]
    assert payload["model"] == DEFAULT_MODEL
    assert payload["input"] == ["- test text"]
    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer test_key"


def test_compute_embedding_raises_on_error(monkeypatch):
    """Test that compute_embedding raises on API error."""
    mock_response = mock.Mock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized", request=mock.Mock(), response=mock.Mock()
    )
    
    # Create a proper context manager mock for httpx.Client
    mock_client = mock.MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_response
    
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)
    
    with pytest.raises(httpx.HTTPStatusError):
        compute_embedding("test text", model=DEFAULT_MODEL, api_key="test_key")


# ---------------------------------------------------------------------------
# get_or_compute_embedding function
# ---------------------------------------------------------------------------

def test_get_or_compute_embedding_from_db():
    """Test that get_or_compute_embedding retrieves from the database when available."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    paper_id = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p1",)).fetchone()["id"]
    
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    store_embedding(conn, paper_id, DEFAULT_MODEL, embedding)
    
    # Should retrieve from DB, not compute
    retrieved = get_or_compute_embedding(conn, paper_id, model_name=DEFAULT_MODEL, api_key="test_key")
    assert retrieved == embedding


def test_get_or_compute_embedding_computes_and_stores(monkeypatch):
    """Test that get_or_compute_embedding computes and stores when not in DB."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    paper_id = conn.execute("SELECT id FROM papers WHERE doi = ?", ("10.1/p1",)).fetchone()["id"]
    
    # Mock the httpx response for compute_embedding
    mock_response = mock.Mock()
    mock_response.json.return_value = {
        "data": [
            {"embedding": [0.6, 0.7, 0.8, 0.9, 1.0]}
        ]
    }
    mock_response.raise_for_status = mock.Mock()
    
    # Create a proper context manager mock for httpx.Client
    mock_client = mock.MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_response
    
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)
    
    # Should compute and store
    embedding = get_or_compute_embedding(conn, paper_id, model_name=DEFAULT_MODEL, api_key="test_key")
    assert embedding == [0.6, 0.7, 0.8, 0.9, 1.0]
    
    # Verify it was stored in the database
    retrieved = get_embedding_from_db(conn, paper_id, DEFAULT_MODEL)
    assert retrieved == [0.6, 0.7, 0.8, 0.9, 1.0]


def test_get_or_compute_embedding_paper_not_found():
    """Test that get_or_compute_embedding raises when paper is not found."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    
    with pytest.raises(ValueError, match="Paper 999 not found"):
        get_or_compute_embedding(conn, paper_id=999, model_name=DEFAULT_MODEL, api_key="test_key")


# ---------------------------------------------------------------------------
# Embedding baseline exporter tests
# ---------------------------------------------------------------------------

def test_export_embedding_jsonl_three_class(tmp_path):
    """Test that export_embedding_jsonl produces correct 3-class decisions."""
    conn = _make_db_with_papers(SAMPLE_PAPERS)
    out = tmp_path / "embedding.jsonl"
    
    # Mock the embedding functions
    mock_topic_emb = [0.5, 0.5, 0.5, 0.5, 0.5]
    mock_paper_emb = [0.6, 0.6, 0.6, 0.6, 0.6]
    
    with mock.patch.object(baselines, '_compute_topic_embedding', return_value=mock_topic_emb):
        with mock.patch.object(baselines, '_compute_paper_embeddings', return_value={1: mock_paper_emb}):
            n = baselines.export_embedding_jsonl(
                conn, out, model_name="octen-embedding-8b", threshold=0.3, run=1, api_key="test_key"
            )
    
    assert n == 1
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["method"] == "embedding"
    assert rec["model"] == "octen-embedding-8b"
    assert rec["decision"] in ("in-scope", "out-of-scope", "hybrid")
    assert rec["temperature"] == 0.0
    assert "octen-embedding-8b cosine similarity" in rec["rationale"]
