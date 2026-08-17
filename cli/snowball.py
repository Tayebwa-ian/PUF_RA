"""CLI: run snowball / backward search."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.db import get_connection
from src.snowball import run_snowball

_COMMANDS = ("run", "stats")


def _parse_id_list(raw: Optional[str]) -> Optional[list[int]]:
    """Parse a comma-separated id list into ints (None when empty)."""
    if not raw:
        return None
    ids = [int(part) for part in raw.replace(",", " ").split() if part.strip()]
    return ids or None


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="results.db", help="SQLite database path")
    parser.add_argument(
        "--query-ids", type=int, nargs="+", default=None,
        help="Seed query IDs (legacy form of --seed-query-ids)",
    )
    parser.add_argument(
        "--seed-query-ids", default=None,
        help="Comma-separated query IDs; seeds come from paper_queries",
    )
    parser.add_argument(
        "--seed-paper-ids", default=None,
        help="Comma-separated paper IDs to use as seeds (highest precedence)",
    )
    parser.add_argument("--depth", type=int, default=1, help="Max snowball depth")
    parser.add_argument("--max-refs", type=int, default=15, help="Max refs per paper")
    parser.add_argument(
        "--source", choices=["semantic_scholar", "crossref"], default="semantic_scholar"
    )
    parser.add_argument(
        "--max-api-calls", type=int, default=40,
        help="Stop after this many API requests (0 or negative = unlimited)",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Minimum seconds between API calls (rate limiter pacing)",
    )
    parser.add_argument(
        "--auto-relevance", action="store_true", default=True,
        help="Run relevance evaluation when new papers were inserted",
    )
    parser.add_argument("--no-auto-relevance", action="store_false", dest="auto_relevance")
    parser.add_argument(
        "--reexpand", action="store_true",
        help="Also re-expand seeds that already have snowball edges",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf snowball",
        description="Run backward snowball search over the corpus.",
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_run_arguments(subparsers.add_parser("run", help="Run snowball search"))

    stats_parser = subparsers.add_parser("stats", help="Show snowball statistics")
    stats_parser.add_argument("--db", default="results.db", help="SQLite database path")

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    if not argv or argv[0] not in _COMMANDS:
        argv = ["run"] + argv

    args = parser.parse_args(argv)

    if args.command == "stats":
        with get_connection(args.db) as conn:
            total_edges = conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0]
            max_depth = conn.execute(
                "SELECT COALESCE(MAX(depth), 0) FROM snowball_edges"
            ).fetchone()[0]
            child_count = conn.execute(
                "SELECT COUNT(DISTINCT child_paper_id) FROM snowball_edges"
            ).fetchone()[0]
        print(f"  Total snowball edges: {total_edges}")
        print(f"  Max depth: {max_depth}")
        print(f"  Papers discovered: {child_count}")
        return 0

    seed_query_ids = _parse_id_list(args.seed_query_ids) or args.query_ids
    seed_paper_ids = _parse_id_list(args.seed_paper_ids)
    max_api_calls = args.max_api_calls if args.max_api_calls and args.max_api_calls > 0 else None

    with get_connection(args.db) as conn:
        stats = run_snowball(
            conn,
            seed_query_ids=seed_query_ids,
            seed_paper_ids=seed_paper_ids,
            depth=args.depth,
            max_refs_per_paper=args.max_refs,
            source=args.source,
            delay=args.delay,
            max_api_calls=max_api_calls,
            auto_relevance=args.auto_relevance,
            skip_expanded=not args.reexpand,
        )
    print(f"Snowball stats: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
