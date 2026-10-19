import sqlite3
import json
from datetime import datetime

DB_PATH = "results.db"

THRESHOLDS = {
    "keyword": 0.22,
    "bm25": 0.52,
    "hybrid": 0.55,
    "embedding": 0.62
}

METHOD_MAPPING = {
    "baseline_keyword": "keyword",
    "baseline_bm25": "bm25",
    "baseline_hybrid": "hybrid",
    "embedding-octen-8b": "embedding"
}

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id, method, model, prompt_id, temperature, run_index FROM eval_runs")
    runs = cursor.fetchall()

    run_method_map = {}
    for run_id, method, model, prompt_id, temperature, run_index in runs:
        method_key = METHOD_MAPPING.get(method, method)
        if method_key in THRESHOLDS:
            run_method_map[run_id] = {
                "method": method_key,
                "threshold": THRESHOLDS[method_key]
            }

    cursor.execute("SELECT id, run_id, paper_id, decision, score FROM evals")
    evals = cursor.fetchall()

    cursor.execute("DELETE FROM relevance_evals")

    insert_count = 0
    for eval_id, run_id, paper_id, decision, score in evals:
        if run_id not in run_method_map:
            continue

        method_info = run_method_map[run_id]
        method = method_info["method"]
        threshold = method_info["threshold"]

        is_relevant = 1 if decision in ("in-scope", "hybrid") else 0

        details = json.dumps({
            "decision": decision,
            "score": score,
            "run_id": run_id
        })

        evaluated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO relevance_evals (paper_id, method, score, is_relevant, threshold, details, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (paper_id, method, score, is_relevant, threshold, details, evaluated_at))
        insert_count += 1

    conn.commit()
    conn.close()

    print(f"Populated {insert_count} records into relevance_evals table.")

if __name__ == "__main__":
    main()
