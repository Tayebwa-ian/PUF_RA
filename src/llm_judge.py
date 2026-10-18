"""LLM-as-judge evaluation for screening decisions and rationales.

This module provides functionality to audit the quality of decisions and
rationales produced by screening methods using an out-of-family judge model.

Usage:
    from src.llm_judge import judge_rationale_and_decision, ingest_judge_results, judge_run_evals

    with get_connection("results.db") as conn:
        # Judge a specific eval record
        judge_result = judge_rationale_and_decision(
            conn, eval_id=123,
            judge_model="gpt-4o",
            judge_prompt_id="judge_p1",
            api_key="...",
            base_url="https://..."
        )
        
        # Ingest judge results
        ingest_judge_results(conn, [judge_result])
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from typing import Any, Optional

import openai

from src.eval_store import LABELS


def judge_rationale_and_decision(
    conn: sqlite3.Connection,
    eval_id: int,
    judge_model: str,
    judge_prompt_id: str,
    api_key: str,
    base_url: str,
) -> Optional[dict[str, Any]]:
    """Fetch the evaluation record and send it to the judge model's API.

    Args:
        conn: SQLite connection.
        eval_id: The evals.id record to judge.
        judge_model: The judge model identifier.
        judge_prompt_id: The prompt template ID used for the judge evaluation.
        api_key: API key for the judge model.
        base_url: Base URL for the judge model's API.

    Returns:
        A dict with keys: eval_id, judge_model, judge_prompt_id, score, verdict, rationale.
        Returns None if the eval_id is not found or if the judge call fails.
    """
    # Fetch the evaluation record
    eval_row = conn.execute(
        """
        SELECT e.id, e.paper_id, e.decision, e.score, e.rationale,
               p.title, p.abstract
        FROM evals e
        JOIN papers p ON e.paper_id = p.id
        WHERE e.id = ?
        """,
        (eval_id,),
    ).fetchone()
    
    if not eval_row:
        return None
    
    decision = eval_row["decision"]
    score = eval_row["score"]
    rationale = eval_row["rationale"] or ""
    title = eval_row["title"] or ""
    abstract = eval_row["abstract"] or ""
    
    # Construct the judge prompt
    judge_system_prompt = textwrap.dedent("""
        You are an independent LLM-as-judge auditing the quality of screening decisions and rationales.
        
        Your task is to evaluate the quality and coherence of a rationale and the consistency 
        of a decision with the paper's content (title and abstract).
        
        You must output a JSON object with the following fields:
        - score: A continuous score from 0.0 to 1.0 representing the quality and coherence.
        - verdict: One of "pass", "needs_review", or "fail".
        - rationale: Your explanation for the judgment.
        
        Scoring guidelines:
        - High scores (>0.8): High-quality rationales that are coherent, relevant to the abstract/title, 
          and consistent with the assigned decision.
        - Medium scores (0.5–0.8): Rationales that may have minor issues or lack sufficient detail.
        - Low scores (<0.5): Incoherent rationales, rationales not relevant to the paper's content, 
          or decisions that do not align with the rationale.
        
        Verdict guidelines:
        - "pass": The rationale and decision meet the quality standards. No further review is needed.
        - "needs_review": The rationale or decision has potential issues that require manual inspection.
        - "fail": The rationale is incoherent, irrelevant, or the decision is inconsistent with the content.
        
        Instructions:
        - Ignore the length of the rationale and focus solely on coherence, relevance, and consistency.
        - Do not evaluate the correctness of the decision itself, only the quality of the rationale 
          and its consistency with the decision and paper content.
    """).strip()
    
    judge_user_prompt = textwrap.dedent(f"""
        Paper Title: {title}
        
        Paper Abstract: {abstract}
        
        Assigned Decision: {decision}
        
        Assigned Score: {score if score is not None else 'N/A'}
        
        Rationale Provided: {rationale if rationale else 'None provided'}
        
        Please evaluate the quality of this rationale and the consistency of the decision.
    """).strip()
    
    # Initialize the judge client
    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    
    try:
        response = client.chat.completions.create(
            model=judge_model,
            messages=[
                {"role": "system", "content": judge_system_prompt},
                {"role": "user", "content": judge_user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Extract and validate the judge's response
        score_val = float(result.get("score", 0.0))
        score_val = max(0.0, min(1.0, score_val))  # Clamp to [0.0, 1.0]
        
        verdict_val = (result.get("verdict") or "").strip().lower()
        if verdict_val not in ("pass", "needs_review", "fail"):
            verdict_val = "needs_review"  # Fallback
        
        rationale_val = (result.get("rationale") or "").strip()
        
        judge_record = {
            "eval_id": eval_id,
            "judge_model": judge_model,
            "judge_prompt_id": judge_prompt_id,
            "score": score_val,
            "verdict": verdict_val,
            "rationale": rationale_val,
        }
        
        return judge_record
        
    except Exception as exc:
        print(f"Judge call failed for eval_id {eval_id}: {exc}")
        return None


def ingest_judge_results(
    conn: sqlite3.Connection,
    judge_records: list[dict[str, Any]],
) -> int:
    """Store the judge's evaluations in the `llm_judge` table.

    Args:
        conn: SQLite connection.
        judge_records: List of judge result dicts with keys:
            eval_id, judge_model, judge_prompt_id, score, verdict, rationale.

    Returns:
        Number of records inserted.
    """
    inserted_count = 0
    for rec in judge_records:
        eval_id = int(rec["eval_id"])
        judge_model = str(rec["judge_model"])
        judge_prompt_id = str(rec.get("judge_prompt_id", ""))
        score = float(rec["score"]) if rec.get("score") is not None else None
        verdict = str(rec["verdict"]) if rec.get("verdict") else None
        rationale = str(rec["rationale"]) if rec.get("rationale") else None
        
        try:
            conn.execute(
                """
                INSERT INTO llm_judge
                    (eval_id, judge_model, judge_prompt_id, score, verdict, rationale)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(eval_id, judge_model, judge_prompt_id) DO UPDATE SET
                    score=excluded.score,
                    verdict=excluded.verdict,
                    rationale=excluded.rationale
                """,
                (eval_id, judge_model, judge_prompt_id, score, verdict, rationale),
            )
            inserted_count += 1
        except Exception as exc:
            print(f"Failed to insert judge record for eval_id {eval_id}: {exc}")
            continue
            
    conn.commit()
    return inserted_count


def judge_run_evals(
    conn: sqlite3.Connection,
    run_id: int,
    judge_model: str,
    judge_prompt_id: str,
    api_key: str,
    base_url: str,
) -> int:
    """Judge all eval records for a specific run.

    Args:
        conn: SQLite connection.
        run_id: The eval_runs.id to judge.
        judge_model: The judge model identifier.
        judge_prompt_id: The prompt template ID used for the judge evaluation.
        api_key: API key for the judge model.
        base_url: Base URL for the judge model's API.

    Returns:
        Number of judge records successfully created.
    """
    # Fetch all evals for this run
    eval_rows = conn.execute(
        """
        SELECT id FROM evals WHERE run_id = ?
        """,
        (run_id,),
    ).fetchall()
    
    judge_records = []
    for eval_row in eval_rows:
        eval_id = eval_row["id"]
        judge_record = judge_rationale_and_decision(
            conn, eval_id, judge_model, judge_prompt_id, api_key, base_url
        )
        if judge_record:
            judge_records.append(judge_record)
    
    # Ingest the judge results
    if judge_records:
        inserted_count = ingest_judge_results(conn, judge_records)
        return inserted_count
    
    return 0
