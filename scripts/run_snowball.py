"""Run the backward snowball search against an existing study database.

Convenience runner around :func:`src.snowball.run_snowball` with the same
options as ``puf snowball``:

    python -m scripts.run_snowball --db results.db --depth 1 --max-refs 15 \
        --source semantic_scholar --max-api-calls 40

Without ``--seed-query-ids``/``--seed-paper-ids`` every paper in the corpus is
used as a seed; the API-call budget keeps the run polite. Network problems are
reported and the run stops after committing whatever was already discovered.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.db import get_connection
from src.snowball import run_snowball


def _parse_id_list(raw: Optional[str]) -> Optional[list[int]]:
    """Parse a comma-separated id list into ints (None when empty)."""
    if not raw:
        return None
    ids = [int(part) for part in raw.replace(",", " ").split() if part.strip()]
    return ids or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_snowball",
        description="Backward snowball search with smart rate limiting.",
    )
    parser.add_argument("--db", default="results.db", help="SQLite database path")
    parser.add_argument("--depth", type=int, default=1, help="Max snowball depth")
    parser.add_argument("--max-refs", type=int, default=15, help="Max refs per paper")
    parser.add_argument(
        "--source", choices=["semantic_scholar", "crossref"], default="semantic_scholar"
    )
    parser.add_argument(
        "--seed-query-ids", default=None, help="Comma-separated query IDs to seed from"
    )
    parser.add_argument(
        "--seed-paper-ids", default=None, help="Comma-separated paper IDs to seed from"
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    max_api_calls = args.max_api_calls if args.max_api_calls and args.max_api_calls > 0 else None

    with get_connection(args.db) as conn:
        stats = run_snowball(
            conn,
            seed_query_ids=_parse_id_list(args.seed_query_ids),
            seed_paper_ids=_parse_id_list(args.seed_paper_ids),
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
