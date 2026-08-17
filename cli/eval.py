"""CLI: evaluation harness — ingest evals, ground truth, metrics, baselines.

Subcommands:
  ingest        Ingest eval JSONL files into the database.
  groundtruth   Ingest the human ground-truth CSV and compute agreement (κ).
  metrics       Compute per-method metrics vs the consensus gold standard.
  runs          List eval runs.
  export-papers Write a fill-in sheet (paper_id, doi, title, ...) for annotators.
  baseline      Export a deterministic / SBERT baseline as eval JSONL.
  screen        Run LLM screening and write eval JSONL (requires API access).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.db import get_connection
from src import eval_store
from src import baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf eval",
        description="Evaluation harness for the PUF screening study.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_p = sub.add_parser("ingest", help="Ingest eval JSONL files")
    ingest_p.add_argument("files", nargs="+", type=Path, help="Eval JSONL files")
    ingest_p.add_argument("--db", default="results.db", help="SQLite database path")

    gt_p = sub.add_parser("groundtruth", help="Ingest ground-truth CSV")
    gt_p.add_argument("csv", type=Path, help="Ground-truth CSV")
    gt_p.add_argument("--db", default="results.db", help="SQLite database path")

    met_p = sub.add_parser("metrics", help="Metrics for a run vs consensus")
    met_p.add_argument("run_id", type=int, help="Eval run id")
    met_p.add_argument("--db", default="results.db", help="SQLite database path")

    runs_p = sub.add_parser("runs", help="List eval runs")
    runs_p.add_argument("--db", default="results.db", help="SQLite database path")

    exp_p = sub.add_parser("export-papers", help="Write annotator fill-in sheet")
    exp_p.add_argument("out", type=Path, help="Output CSV path")
    exp_p.add_argument("--db", default="results.db", help="SQLite database path")

    base_p = sub.add_parser("baseline", help="Export a baseline as eval JSONL")
    base_p.add_argument("--method", choices=["keyword", "bm25", "hybrid", "sbert"], required=True)
    base_p.add_argument("--out", type=Path, required=True, help="Output JSONL path")
    base_p.add_argument("--threshold", type=float, default=0.15)
    base_p.add_argument("--model", default="all-MiniLM-L6-v2", help="SBERT model name")
    base_p.add_argument("--db", default="results.db", help="SQLite database path")

    screen_p = sub.add_parser("screen", help="Run LLM screening -> eval JSONL")
    screen_p.add_argument("--prompt", type=Path, required=True, help="System prompt file")
    screen_p.add_argument("--model", required=True, help="Model id")
    screen_p.add_argument("--api-key", required=True, help="API key")
    screen_p.add_argument("--base-url", required=True, help="OpenAI-compatible base URL")
    screen_p.add_argument("--query-ids", type=int, nargs="+", required=True)
    screen_p.add_argument("--out", type=Path, required=True, help="Output JSONL path")
    screen_p.add_argument("--prompt-id", default="P1")
    screen_p.add_argument("--model-version", default="")
    screen_p.add_argument("--temperature", type=float, default=0.0)
    screen_p.add_argument("--run", type=int, default=1)
    screen_p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "ingest":
        with get_connection(args.db) as conn:
            res = eval_store.ingest_eval_files(conn, args.files)
        print(f"Ingested {res['files']} file(s): {res['runs']} run(s), {res['evals']} eval(s).")

    elif args.command == "groundtruth":
        with get_connection(args.db) as conn:
            res = eval_store.ingest_ground_truth(conn, args.csv)
        print(f"Ground truth: {res['rows']} row(s), {res['papers']} paper(s).")
        kappa = res["kappa"]
        print(f"Inter-rater agreement (κ): {kappa if kappa is None else round(kappa, 4)}")

    elif args.command == "metrics":
        with get_connection(args.db) as conn:
            m = eval_store.compute_metrics(conn, args.run_id)
        print(json.dumps(m, indent=2, default=str))

    elif args.command == "runs":
        with get_connection(args.db) as conn:
            rows = eval_store.list_runs(conn)
        for r in rows:
            print(f"run {r['id']}: {r['method']}/{r['model']} "
                  f"prompt={r['prompt_id']} temp={r['temperature']} n={r['n_evals']}")

    elif args.command == "export-papers":
        with get_connection(args.db) as conn:
            n = eval_store.export_paper_sheet(conn, args.out)
        print(f"Wrote {n} paper row(s) to {args.out}")

    elif args.command == "baseline":
        with get_connection(args.db) as conn:
            if args.method == "sbert":
                n = baselines.export_sbert_jsonl(conn, args.out, model_name=args.model, threshold=args.threshold)
            else:
                n = baselines.export_baseline_jsonl(
                    conn, args.out, method=args.method, threshold=args.threshold
                )
        print(f"Wrote {n} baseline eval record(s) to {args.out}")

    elif args.command == "screen":
        import openai
        from src import screening

        client = openai.OpenAI(api_key=args.api_key, base_url=args.base_url)
        system_prompt = screening.load_prompt(args.prompt)
        with get_connection(args.db) as conn:
            n = screening.screen_to_jsonl(
                conn, client, args.model, system_prompt, args.out,
                query_ids=args.query_ids, prompt_id=args.prompt_id,
                model_version=args.model_version, temperature=args.temperature,
                run=args.run, dry_run=args.dry_run,
            )
        print(f"Wrote {n} eval record(s) to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
