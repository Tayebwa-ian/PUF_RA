"""CLI: run LLM screening."""

from __future__ import annotations

import argparse
import sys

import openai

from src.db import get_connection
from src.screening import run_screening


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf screen",
        description="Run LLM screening on papers.",
    )
    parser.add_argument("--db", default="results.db", help="SQLite database path")
    parser.add_argument("--model", required=True, help="Model identifier")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible API base URL")
    parser.add_argument("--query-ids", type=int, nargs="+", required=True, help="Query IDs to screen")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per paper")
    parser.add_argument("--system-prompt", default="", help="Path to system prompt file")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without calling API")

    args = parser.parse_args(argv)

    system_prompt = ""
    if args.system_prompt:
        system_prompt = Path(args.system_prompt).read_text(encoding="utf-8").strip()

    client = openai.OpenAI(api_key=args.api_key, base_url=args.base_url)

    with get_connection(args.db) as conn:
        run_screening(
            conn,
            client=client,
            model=args.model,
            system_prompt=system_prompt,
            query_ids=args.query_ids,
            max_retries=args.max_retries,
            dry_run=args.dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
