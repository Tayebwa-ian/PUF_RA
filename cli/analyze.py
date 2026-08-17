"""CLI: puf analyze — corpus / relevance / snowball / evaluation statistics.

Mirrors the other subcommands (``cli/snowball.py`` style). Builds an
:class:`src.analysis.AnalysisClient` (preferring the MCP server, falling back to
a direct SQLite connection) and runs the requested analyses, writing JSON +
PNG outputs to ``data/analysis`` and persisting the result via the MCP
``store_analysis`` tool.

Usage:
    puf analyze all
    puf analyze corpus --name my_corpus --out-dir data/analysis
    puf analyze relevance --mode direct
    puf analyze snowball --db results.db
"""

from __future__ import annotations

import argparse
import json
import sys

from src.analysis import AnalysisClient, run_analysis


_KIND_BY_ACTION = {
    "corpus": ("corpus",),
    "relevance": ("relevance",),
    "snowball": ("snowball",),
    "evaluation": ("evaluation",),
    "all": ("corpus", "relevance", "snowball", "evaluation"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf analyze",
        description="Compute corpus / relevance / snowball / evaluation statistics.",
    )
    parser.add_argument(
        "action",
        choices=["corpus", "relevance", "snowball", "evaluation", "all"],
        help="Which analysis(es) to run.",
    )
    parser.add_argument("--name", default=None, help="Analysis name (default: analysis_<action>)")
    parser.add_argument("--db", default="results.db", help="SQLite database path")
    parser.add_argument(
        "--out-dir", default="data/analysis", help="Output directory for JSON + PNG files"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "mcp", "direct"],
        default="auto",
        help="Transport: auto (MCP, fallback direct), mcp (require MCP), or direct.",
    )
    args = parser.parse_args(argv)

    client = AnalysisClient(db_path=args.db, mcp_mode=args.mode)
    name = args.name or f"analysis_{args.action}"
    kinds = _KIND_BY_ACTION[args.action]

    summary = run_analysis(name, client, kinds=kinds, out_dir=args.out_dir)

    print(json.dumps(
        {
            "name": summary["name"],
            "json": summary["json"],
            "plots": summary["plots"],
            "transport": "mcp" if client.uses_mcp else "direct",
        },
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
