"""CLI: evaluate paper relevance."""

from __future__ import annotations

import argparse
import sys

from src.db import get_connection
from src.relevance import evaluate_corpus, evaluate_paper, get_relevance_stats


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
    eval_parser.add_argument("--threshold", type=float, default=0.15, help="Relevance threshold")
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
        relevant = sum(1 for _, _, is_rel, _ in results if is_rel)
        print(f"Evaluated {len(results)} papers. Relevant: {relevant}, Irrelevant: {len(results) - relevant}")

    elif args.command == "paper":
        with get_connection(args.db) as conn:
            score, is_relevant, details = evaluate_paper(
                conn, args.paper_id, method=args.method, threshold=args.threshold
            )
        print(f"Paper {args.paper_id}: score={score:.4f}, relevant={is_relevant}")
        print(f"  Details: {details}")

    elif args.command == "stats":
        with get_connection(args.db) as conn:
            stats = get_relevance_stats(conn)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
