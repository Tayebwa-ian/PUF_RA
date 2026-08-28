"""CLI: run snowball / backward search (two-phase, local-first)."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.db import get_connection
from src.rate_limiter import RateLimiter, DEFAULT_SOURCE_INTERVALS

_COMMANDS = ("run", "stats", "backfill-abstracts")


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
        "--source", choices=["semantic_scholar", "crossref", "openalex", "s2", "zotero"],
        default="semantic_scholar",
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
    # --- new two-phase, local-first flags ---
    parser.add_argument(
        "--direction", choices=["backward", "forward", "both"], default="backward",
        help="Snowball direction(s) for the new local-first path",
    )
    parser.add_argument(
        "--harvest-only", action="store_true",
        help="Only harvest the reference inventory (no second-phase resolve)",
    )
    parser.add_argument(
        "--resolve-only", action="store_true",
        help="Only run the second-phase resolver over existing reference_lists",
    )
    parser.add_argument(
        "--zotero-sync", action="store_true",
        help="Push discovered DOIs to Zotero for bulk PDF download",
    )
    parser.add_argument(
        "--with-pdf", action="store_true",
        help="Capture / report Open-Access PDF links during resolution",
    )
    parser.add_argument(
        "--mailto", default=None,
        help="Contact email for the polite Crossref / OpenAlex pool",
    )
    parser.add_argument(
        "--assured", dest="assured", action="store_true", default=True,
        help="Run the assured backstop (verify_retrieval + retry) after resolving",
    )
    parser.add_argument(
        "--no-assured", dest="assured", action="store_false",
        help="Disable the assured backstop (first-pass resolution only)",
    )
    parser.add_argument(
        "--export-unresolved", default="snowball_unresolved.csv",
        help="CSV path for non-resolved references ('' disables the export)",
    )
    parser.add_argument(
        "--no-alternate", action="store_true",
        help="Single-source resolution: do not fall back to the alternate "
             "source (resolve on Crossref or Semantic Scholar alone).",
    )
    parser.add_argument(
        "--no-batch", action="store_true",
        help="Skip the OpenAlex batched multi-DOI pre-pass (per-DOI chain only).",
    )


def _cli_seeds(conn, seed_paper_ids, seed_query_ids):
    from src.snowball import _get_seed_papers

    if seed_paper_ids:
        return list(dict.fromkeys(int(pid) for pid in seed_paper_ids))
    if seed_query_ids:
        return _get_seed_papers(conn, list(seed_query_ids))
    return [row[0] for row in conn.execute("SELECT id FROM papers ORDER BY id")]


def _run_new_path(conn, args) -> int:
    from src.db_schema import ensure_schema
    from src.reference_store import harvest_references, resolve_reference_lists
    from src.zotero_sync import push_dois_to_zotero

    ensure_schema(conn)
    seed_query_ids = _parse_id_list(args.seed_query_ids) or args.query_ids
    seed_paper_ids = _parse_id_list(args.seed_paper_ids)
    max_api_calls = (
        args.max_api_calls if args.max_api_calls and args.max_api_calls > 0 else None
    )

    limiter = RateLimiter(
        min_interval=args.delay,
        per_source_intervals=DEFAULT_SOURCE_INTERVALS,
    )
    if args.resolve_only:
        stats = resolve_reference_lists(
            conn, source=_legacy_to_new_source(args.source),
            mailto=args.mailto, max_api_calls=max_api_calls,
            assured=args.assured,
            export_path=args.export_unresolved or None,
            rate_limiter=limiter, no_alternate=args.no_alternate,
            use_batch=not args.no_batch,
        )
        print(f"Resolve stats: {stats}")
        return 0

    if args.zotero_sync:
        n = push_dois_to_zotero(conn, paper_ids=seed_paper_ids, batch_size=50)
        print(f"Zotero pushed: {n}")
        return 0

    directions = (
        ["forward", "backward"] if args.direction == "both" else [args.direction]
    )
    total_new = 0
    for direction in directions:
        seeds = _cli_seeds(conn, seed_paper_ids, seed_query_ids)
        stats = harvest_references(
            conn,
            seeds,
            direction=direction,
            source=_legacy_to_new_source(args.source),
            mailto=args.mailto,
            max_api_calls=max_api_calls,
            resolve=not args.harvest_only,
            assured=args.assured,
            export_path=args.export_unresolved or None,
            rate_limiter=limiter, no_alternate=args.no_alternate,
            use_batch=not args.no_batch,
        )
        print(f"Harvest ({direction}) stats: {stats}")
        if args.with_pdf:
            count = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE pdf_url IS NOT NULL"
            ).fetchone()[0]
            print(f"Papers with a captured PDF link: {count}")
        total_new += int((stats or {}).get("new_papers", 0))

    if args.auto_relevance and total_new > 0:
        from src.relevance import evaluate_corpus

        print("Running relevance evaluation on updated corpus...")
        evaluate_corpus(conn, store=True)
        conn.commit()
    elif args.auto_relevance:
        print("No new papers; skipping relevance evaluation.")

    return 0


def _legacy_to_new_source(source: str) -> str:
    # s2 / semantic_scholar are both the real Semantic Scholar source; never
    # remap to crossref (the new resolver handles S2 directly). zotero is a
    # local-library source and is passed through unchanged.
    if source == "s2":
        return "semantic_scholar"
    if source == "zotero":
        return "zotero"
    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf snowball",
        description="Run backward/forward snowball search over the corpus.",
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_run_arguments(subparsers.add_parser("run", help="Run snowball search"))

    stats_parser = subparsers.add_parser("stats", help="Show snowball statistics")
    stats_parser.add_argument("--db", default="results.db", help="SQLite database path")

    backfill_parser = subparsers.add_parser(
        "backfill-abstracts",
        help="Backfill missing abstracts for harvested papers (Crossref/OpenAlex/Semantic Scholar)",
    )
    backfill_parser.add_argument("--db", default="results.db", help="SQLite database path")
    backfill_parser.add_argument(
        "--source", choices=["crossref", "openalex", "semantic_scholar", "s2", "zotero"],
        default="crossref",
        help="Primary source (Crossref/OpenAlex/Semantic Scholar). With default "
             "behaviour Crossref/OpenAlex retry each other on a miss, but "
             "Semantic Scholar resolves standalone (no OpenAlex fallback); "
             "--no-alternate disables all retries so the chosen source is used "
             "alone",
    )
    backfill_parser.add_argument(
        "--max-api-calls", type=int, default=None,
        help="Stop after this many API requests (None = unlimited)",
    )
    backfill_parser.add_argument("--mailto", default=None, help="Polite-pool contact email")
    backfill_parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Minimum seconds between API calls (rate limiter pacing)",
    )
    backfill_parser.add_argument(
        "--no-alternate", action="store_true",
        help="Single-source backfill: do not fall back to OpenAlex "
             "(Semantic Scholar is standalone; Crossref alone when set).",
    )
    backfill_parser.add_argument(
        "--no-batch", action="store_true",
        help="Skip the OpenAlex batched multi-DOI pre-pass (per-DOI chain only).",
    )

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    if not argv or argv[0] not in _COMMANDS:
        argv = ["run"] + argv

    args = parser.parse_args(argv)

    if args.command == "stats":
        from src.reference_store import snowball_coverage
        with get_connection(args.db) as conn:
            total_edges = conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0]
            max_depth = conn.execute(
                "SELECT COALESCE(MAX(depth), 0) FROM snowball_edges"
            ).fetchone()[0]
            child_count = conn.execute(
                "SELECT COUNT(DISTINCT child_paper_id) FROM snowball_edges"
            ).fetchone()[0]
            cov = snowball_coverage(conn)
        print(f"  Total snowball edges: {total_edges}")
        print(f"  Max depth: {max_depth}")
        print(f"  Papers discovered: {child_count}")
        print(
            f"  Coverage: papers={cov['papers_total']} "
            f"missing_abstract={cov['papers_missing_abstract']} "
            f"missing_title={cov['papers_missing_title']} "
            f"resolved_missing_abstract={cov['resolved_missing_abstract']} "
            f"resolved_missing_title={cov['resolved_missing_title']}"
        )
        return 0

    if args.command == "backfill-abstracts":
        from src.db_schema import ensure_schema
        from src.rate_limiter import RateLimiter
        from src.reference_store import backfill_abstracts

        with get_connection(args.db) as conn:
            ensure_schema(conn)
            limiter = RateLimiter(
                min_interval=args.delay,
                per_source_intervals=DEFAULT_SOURCE_INTERVALS,
            )
            n = backfill_abstracts(
                conn,
                source=args.source,
                mailto=args.mailto,
                max_api_calls=args.max_api_calls,
                limiter=limiter,
                no_alternate=args.no_alternate,
                use_batch=not args.no_batch,
            )
        print(f"Backfilled abstracts for {n} paper(s).")
        return 0

    with get_connection(args.db) as conn:
        return _run_new_path(conn, args)


if __name__ == "__main__":
    sys.exit(main())
