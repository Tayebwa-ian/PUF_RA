"""CLI: import papers from BibTeX or CSV into the database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.bibtex_parser import parse_bibtex
from src.bibtex_importer import import_bibtex
from src.csv_importer import import_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="puf import",
        description="Import papers from BibTeX or CSV into the database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # BibTeX subcommand
    bib_parser = subparsers.add_parser("bibtex", help="Import BibTeX files")
    bib_parser.add_argument("files", nargs="+", type=Path, help="BibTeX files to import")
    bib_parser.add_argument("--db", type=Path, default=Path("results.db"), help="SQLite database path")
    bib_parser.add_argument("--query-ids", type=int, nargs="+", required=True, help="Query IDs to associate")
    bib_parser.add_argument("--source", dest="sources", action="append", default=[], help="Source name (repeatable)")
    bib_parser.add_argument("--platform", default="unknown", help="Platform name for query registration")
    bib_parser.add_argument("--query-text", default="", help="Query text for registration")

    # CSV subcommand
    csv_parser = subparsers.add_parser("csv", help="Import a CSV file")
    csv_parser.add_argument("csv", type=Path, help="CSV file to import")
    csv_parser.add_argument("--format", choices=["acm", "ieee"], required=True, help="CSV format")
    csv_parser.add_argument("--db", type=Path, default=Path("results.db"), help="SQLite database path")
    csv_parser.add_argument("--query-ids", type=int, nargs="+", required=True, help="Query IDs to associate")
    csv_parser.add_argument("--source", dest="sources", action="append", default=[], help="Source name (repeatable)")

    args = parser.parse_args(argv)

    if args.command == "bibtex":
        if not args.sources:
            args.sources = ["unknown"]
        all_entries = []
        for bib_path in args.files:
            if not bib_path.exists():
                print(f"Warning: {bib_path} not found — skipping.", file=sys.stderr)
                continue
            text = bib_path.read_text(encoding="utf-8", errors="replace")
            entries = parse_bibtex(text)
            print(f"  {bib_path}: {len(entries)} entries parsed")
            all_entries.extend(entries)

        if not all_entries:
            print("No entries to import.", file=sys.stderr)
            return 1

        imported, skipped = import_bibtex(
            entries=all_entries,
            query_ids=args.query_ids,
            source_names=args.sources,
            db_path=str(args.db),
            platform=args.platform,
            query_text=args.query_text,
        )
        print(f"Imported {imported} paper(s), skipped {skipped}.")

    elif args.command == "csv":
        if not args.sources:
            args.sources = ["unknown"]
        imported, skipped = import_csv(
            csv_path=args.csv,
            db_path=args.db,
            fmt=args.format,
            query_ids=args.query_ids,
            source_names=args.sources,
        )
        print(f"Imported {imported} paper(s), skipped {skipped}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
