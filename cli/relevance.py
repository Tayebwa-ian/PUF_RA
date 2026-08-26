"""CLI: evaluate paper relevance."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.db import get_connection
from src.relevance import (
    derive_threshold,
    evaluate_corpus,
    evaluate_paper,
    get_relevance_stats,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf relevance",
        description="Evaluate paper relevance against PUF attack topic.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Evaluate all papers
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate all papers")
    eval_parser.add_argument("--db", default="results.db", help="SQLite database path")
    eval_parser.add_argument("--method", choices=["keyword", "bm25", "hybrid"], default="hybrid")
    eval_parser.add_argument("--threshold", type=float, default=None, help="Relevance threshold (None=auto-derive from ground truth)")
    eval_parser.add_argument("--keyword-weight", type=float, default=0.4)
    eval_parser.add_argument("--bm25-weight", type=float, default=0.6)
    eval_parser.add_argument("--no-store", action="store_true", help="Do not persist results")

    # Evaluate single paper
    paper_parser = subparsers.add_parser("paper", help="Evaluate a single paper")
    paper_parser.add_argument("paper_id", type=int, help="Paper ID")
    paper_parser.add_argument("--db", default="results.db", help="SQLite database path")
    paper_parser.add_argument("--method", choices=["keyword", "bm25", "hybrid"], default="hybrid")
    paper_parser.add_argument("--threshold", type=float, default=0.15)

    # Statistics
    stats_parser = subparsers.add_parser("stats", help="Show relevance statistics")
    stats_parser.add_argument("--db", default="results.db", help="SQLite database path")

    # Export a deterministic baseline and ingest it as canonical evals
    baseline_parser = subparsers.add_parser(
        "baseline", help="Export deterministic baseline -> eval JSONL -> evals"
    )
    baseline_parser.add_argument("--method", choices=["keyword", "bm25", "hybrid"], default="hybrid")
    baseline_parser.add_argument("--threshold", type=float, default=None, help="Relevance threshold (None=auto-derive)")
    baseline_parser.add_argument("--out", type=Path, default=None, help="Output JSONL (default data/evals/baseline_<method>_<run>.jsonl)")
    baseline_parser.add_argument("--run", type=int, default=1)
    baseline_parser.add_argument("--db", default="results.db", help="SQLite database path")
    baseline_parser.add_argument(
        "--derive-threshold",
        action="store_true",
        help=(
            "Derive the decision threshold from ground_truth_consensus instead of "
            "using --threshold (falls back to --threshold when there is no usable "
            "ground truth)"
        ),
    )
    baseline_parser.add_argument(
        "--criterion",
        choices=["f1", "youden"],
        default="f1",
        help="Criterion optimised by --derive-threshold (default: max-F1)",
    )

    # Export the SBERT baseline and ingest it as canonical evals
    sbert_parser = subparsers.add_parser(
        "sbert", help="Export SBERT baseline -> eval JSONL -> evals"
    )
    sbert_parser.add_argument("--model", default="all-MiniLM-L6-v2", help="SBERT model name")
    sbert_parser.add_argument("--threshold", type=float, default=None, help="Relevance threshold (None=auto-derive)")
    sbert_parser.add_argument("--out", type=Path, default=None, help="Output JSONL (default data/evals/sbert_<model>_<run>.jsonl)")
    sbert_parser.add_argument("--run", type=int, default=1)
    sbert_parser.add_argument(
        "--criterion", choices=["f1", "youden"], default="f1",
        help="Criterion optimised when auto-deriving (default: max-F1)",
    )
    sbert_parser.add_argument("--db", default="results.db", help="SQLite database path")

    derive_parser = subparsers.add_parser(
        "derive-threshold", help="Print the ground-truth-derived threshold per method",
    )
    derive_parser.add_argument(
        "--method", choices=["keyword", "bm25", "hybrid", "sbert", "all"], default="all",
        help="Which deterministic baseline method(s) to derive for",
    )
    derive_parser.add_argument(
        "--criterion", choices=["f1", "youden"], default="f1",
        help="Criterion optimised by the sweep (default: max-F1)",
    )
    derive_parser.add_argument(
        "--apply", action="store_true",
        help="No-op: thresholds are auto-derived from ground_truth_consensus at "
             "evaluation time; documents that behaviour (nothing to persist).",
    )
    derive_parser.add_argument("--db", default="results.db", help="SQLite database path")

    args = parser.parse_args(argv)

    if args.command == "evaluate":
        with get_connection(args.db) as conn:
            results = evaluate_corpus(
                conn,
                method=args.method,
                keyword_weight=args.keyword_weight,
                bm25_weight=args.bm25_weight,
                threshold=args.threshold,
                store=not args.no_store,
            )
        relevant = sum(1 for _, _, is_rel, _rel_class, _ in results if is_rel)
        print(f"Evaluated {len(results)} papers. Relevant: {relevant}, Irrelevant: {len(results) - relevant}")
        if results:
            print(f"  effective threshold: {results[0][4]["threshold"]:.4f}")

    elif args.command == "paper":
        with get_connection(args.db) as conn:
            score, is_relevant, relevance_class, details = evaluate_paper(
                conn, args.paper_id, method=args.method, threshold=args.threshold
            )
        print(f"Paper {args.paper_id}: score={score:.4f}, relevant={is_relevant}, class={relevance_class}")
        print(f"  Details: {details}")

    elif args.command == "stats":
        with get_connection(args.db) as conn:
            stats = get_relevance_stats(conn)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif args.command == "baseline":
        from src import baselines
        from src import eval_store

        out = args.out or (Path("data/evals") / f"baseline_{args.method}_{args.run}.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        threshold = args.threshold
        with get_connection(args.db) as conn:
            if args.derive_threshold or threshold is None:
                derived = derive_threshold(
                    conn, method=args.method, criterion=args.criterion
                )
                if derived is not None:
                    threshold = derived
                    print(
                        f"Derived threshold ({args.criterion}) from ground truth: "
                        f"{threshold:.4f}"
                    )
                else:
                    threshold = threshold if threshold is not None else 0.15
                    print(
                        "No usable ground truth (ground_truth_consensus): using "
                        f"fallback threshold {threshold}."
                    )
            n = baselines.export_baseline_jsonl(
                conn, out, method=args.method, threshold=threshold, run=args.run
            )
            res = eval_store.ingest_eval_file(conn, out)
        print(f"Baseline {args.method}: wrote {n} record(s) to {out}; ingested {res['evals']} eval(s) into run(s)={res['runs']} (threshold={threshold}).")

    elif args.command == "sbert":
        from src import baselines
        from src import eval_store

        out = args.out or (Path("data/evals") / f"sbert_{args.model}_{args.run}.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        threshold = args.threshold
        with get_connection(args.db) as conn:
            if threshold is None:
                derived = derive_threshold(
                    conn, method="sbert", sbert_model=args.model, criterion=args.criterion
                )
                if derived is not None:
                    threshold = derived
                    print(
                        f"Derived SBERT threshold ({args.criterion}) from ground truth: "
                        f"{threshold:.4f}"
                    )
                else:
                    threshold = 0.3
                    print(
                        "No usable ground truth for SBERT (ground_truth_consensus): "
                        "using default threshold 0.3."
                    )
            n = baselines.export_sbert_jsonl(
                conn, out, model_name=args.model, threshold=threshold, run=args.run
            )
            res = eval_store.ingest_eval_file(conn, out)
        print(f"SBERT {args.model}: wrote {n} record(s) to {out}; ingested {res['evals']} eval(s) into run(s)={res['runs']} (threshold={threshold}).")

    elif args.command == "derive-threshold":
        methods = (
            ["keyword", "bm25", "hybrid", "sbert"]
            if args.method == "all" else [args.method]
        )
        with get_connection(args.db) as conn:
            for method in methods:
                try:
                    thr = derive_threshold(
                        conn, method=method, criterion=args.criterion
                    )
                except Exception as exc:  # e.g. SBERT model unavailable
                    print(f"  {method}: unavailable ({exc})")
                    continue
                if thr is None:
                    print(
                        f"  {method}: no usable ground truth "
                        "(fallback default 0.15 applies)"
                    )
                else:
                    print(f"  {method}: {thr:.4f}")
            if args.apply:
                print(
                    "  --apply: thresholds are auto-derived from "
                    "ground_truth_consensus at evaluation time; evaluate_corpus / "
                    "baseline / sbert read them directly, so there is nothing to "
                    "persist."
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
