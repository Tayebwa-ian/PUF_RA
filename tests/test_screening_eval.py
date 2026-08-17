"""Round-trip test: LLM screening -> eval JSONL -> DB ingest.

Skipped automatically when `openai` (and thus `src.screening`) is unavailable.
"""

from pathlib import Path

import json
import pytest

screening = pytest.importorskip("src.screening")
from src.db import get_connection
from src.db_schema import create_schema
from src import eval_store


def _seed(conn, n=3):
    create_schema(conn)
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO papers (id,title,authors,year,abstract,publication_title,doi) "
            "VALUES (?,?,?,?,?,?,?)",
            (i, f"P{i}", "A", 2020, f"abstract {i}", "V", f"10.0/{i}"),
        )
    conn.commit()


def test_screen_to_jsonl_then_ingest(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    with get_connection(db) as conn:
        _seed(conn, 3)

    def fake_query_llm(client, model, system_prompt, title, abstract, timeout=30.0):
        return {
            "decision": "in-scope",
            "relevance_score": 0.9,
            "confidence": 0.9,
            "techniques": ["power analysis"],
            "rationale": "side-channel attack",
        }, 10

    monkeypatch.setattr(screening, "query_llm", fake_query_llm)
    out = tmp_path / "evals.jsonl"
    with get_connection(db) as conn:
        n = screening.screen_to_jsonl(
            conn, None, "model-x", "SYS", out, [1, 2, 3],
            prompt_id="P2", model_version="v1", temperature=0.0, run=1,
        )
        assert n == 3
        res = eval_store.ingest_eval_file(conn, out)
        assert res["evals"] == 3
        runs = eval_store.list_runs(conn)
        assert runs[0]["model"] == "model-x"
        assert runs[0]["prompt_id"] == "P2"

    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert lines[0]["decision"] == "in-scope"
    assert lines[0]["method"] == "llm"
    assert lines[0]["matched_keywords"] == ["power analysis"]
