"""CLI: run snowball / backward search."""

from __future__ import annotations

import argparse
import sys

from src.db import get_connection
from src.snowball import run_snowball


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf snowball",
        description="Run backward snowball search from relevant papers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run snowball search")
    run_parser.add_argument("--db", default="results.db", help="SQLite database path")
    run_parser.add_argument("--query-ids", type=int, nargs="+", required=True, help="Seed query IDs")
    run_parser.add_argument("--depth", type=int, default=1, help="Max snowball depth")
    run_parser.add_argument("--max-refs", type=int, default=20, help="Max refs per paper")
    run_parser.add_argument("--source", choices=["semantic_scholar", "crossref"], default="semantic_scholar")
    run_parser.add_argument("--delay", type=float, default=1.0, help="Delay between API calls (seconds)")
    run_parser.add_argument("--auto-relevance", action="store_true", default=True, help="Run relevance eval after")
    run_parser.add_argument("--no-auto-relevance", action="store_false", dest="auto_relevance")

    stats_parser = subparsers.add_parser("stats", help="Show snowball statistics")
    stats_parser.add_argument("--db", default="results.db", help="SQLite database path")

    args = parser.parse_args(argv)

    if args.command == "run":
        with get_connection(args.db) as conn:
            stats = run_snowball(
                conn,
                seed_query_ids=args.query_ids,
                depth=args.depth,
                max_refs_per_paper=args.max_refs,
                source=args.source,
                delay=args.delay,
                auto_relevance=args.auto_relevance,
            )
        print(f"Snowball stats: {stats}")

    elif args.command == "stats":
        with get_connection(args.db) as conn:
            total_edges = conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0]
            max_depth = conn.execute("SELECT COALESCE(MAX(depth), 0) FROM snowball_edges").fetchone()[0]
            child_count = conn.execute("SELECT COUNT(DISTINCT child_paper_id) FROM snowball_edges").fetchone()[0]
        print(f"  Total snowball edges: {total_edges}")
        print(f"  Max depth: {max_depth}")
        print(f"  Papers discovered: {child_count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
