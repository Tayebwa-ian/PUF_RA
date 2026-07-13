#!/usr/bin/env python3
"""Import papers from a CSV file into the `papers` SQLite table.

The CSV layout is selected with `--format`. Currently supported:
  * `acm`  — columns include `entry_type`, `cite_key`, `author`, `title`,
    `year`, `journal`, `booktitle`, `abstract`, `doi`, `keywords`, ...
  * `ieee` — columns include `Document Title`, `Authors`,
    `Publication Title`, `Publication Year`, `Abstract`, `DOI`,
    `Author Keywords`, ...

To add a new format, write a `map_<name>_row(row, row_num)` function and
register it in the `PARSERS` dict below.

Any empty mapped field aborts the whole import (nothing is committed) so the
source data can be inspected and fixed before re-running.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def require(value, field_name, row_num, optional=False):
    """Return a stripped non-empty string, or raise ValueError.

    If `optional` is True, an empty/missing value returns None (SQL NULL)
    instead of raising.
    """
    if value is None or str(value).strip() == "":
        if optional:
            return None
        raise ValueError(
            f"Empty value for field '{field_name}' in row {row_num}."
        )
    return str(value).strip()


def to_year(value, field_name, row_num):
    s = require(value, field_name, row_num)
    try:
        return int(s)
    except ValueError as exc:
        raise ValueError(
            f"Field '{field_name}' in row {row_num} is not an integer: {s!r}"
        ) from exc


# Canonical separator for multi-value text fields (authors, keywords).
NORMALIZED_SEP = "; "


def normalize_multi(value, field_name, row_num, sep,
                    item_transform=None, optional=False):
    """Split `value` on `sep`, strip each part, drop empties, optionally
    transform each part, and rejoin with `NORMALIZED_SEP`.

    If `optional` is True, an empty/missing field returns None (stored as
    SQL NULL) instead of raising. Useful for fields like keywords that may
    legitimately be blank.
    """
    if optional and (value is None or str(value).strip() == ""):
        return None
    raw = require(value, field_name, row_num)
    parts = [p.strip() for p in raw.split(sep) if p.strip()]
    if not parts:
        if optional:
            return None
        raise ValueError(
            f"Field '{field_name}' in row {row_num} has no values after splitting."
        )
    if item_transform is not None:
        parts = [item_transform(p) for p in parts]
    return NORMALIZED_SEP.join(parts)


def flip_bibtex_name(name):
    """Convert a BibTeX-style 'Last, First' name to 'First Last'.

    Splits on the first comma only, so suffixes/middle parts like
    'Smith, John A.' stay intact ('John A. Smith'). Names with no comma
    are returned unchanged.
    """
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name


def map_acm_row(row, row_num):
    entry_type = require(row.get("entry_type"), "entry_type", row_num).lower()

    if entry_type == "article":
        publication_title = require(row.get("journal"), "journal", row_num)
    elif entry_type == "inproceedings":
        publication_title = require(row.get("booktitle"), "booktitle", row_num)
    else:
        raise ValueError(
            f"Unsupported entry_type {entry_type!r} in row {row_num}. "
            f"Expected 'article' or 'inproceedings'."
        )

    return {
        "title":             require(row.get("title"),    "title",    row_num),
        "authors":           normalize_multi(row.get("author"), "author",
                                             row_num, " and ", flip_bibtex_name),
        "year":              to_year(row.get("year"),     "year",     row_num),
        "abstract":          require(row.get("abstract"), "abstract", row_num),
        "doi":               require(row.get("doi"),      "doi",      row_num,
                                     optional=True),
        "publication_title": publication_title,
        "keywords":          normalize_multi(row.get("keywords"), "keywords",
                                             row_num, ",", optional=True),
    }


def map_ieee_row(row, row_num):
    return {
        "title":             require(row.get("Document Title"),
                                     "Document Title", row_num),
        "authors":           normalize_multi(row.get("Authors"),
                                             "Authors", row_num, ";"),
        "year":              to_year(row.get("Publication Year"),
                                     "Publication Year", row_num),
        "abstract":          require(row.get("Abstract"),
                                     "Abstract", row_num),
        "doi":               require(row.get("DOI"),
                                     "DOI", row_num, optional=True),
        "publication_title": require(row.get("Publication Title"),
                                     "Publication Title", row_num),
        "keywords":          normalize_multi(row.get("Author Keywords"),
                                             "Author Keywords", row_num, ";",
                                             optional=True),
    }


INSERT_SQL = """
INSERT INTO papers (title, authors, year, abstract, doi, publication_title, keywords, query_id)
VALUES (:title, :authors, :year, :abstract, :doi, :publication_title, :keywords, :query_id)
"""


# Registry of supported CSV layouts. To add a new format, write a
# `map_<name>_row(row, row_num)` function above and add it here.
PARSERS = {
    "acm":  map_acm_row,
    "ieee": map_ieee_row,
}


def import_csv(csv_path: Path, db_path: Path, fmt: str,
               query_id: int) -> tuple[int, int]:
    mapper = PARSERS[fmt]

    papers = []
    skipped = 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for n, row in enumerate(reader, start=2):
            try:
                papers.append(mapper(row, n))
            except ValueError as exc:
                print(f"Skipping row {n}: {exc}", file=sys.stderr)
                skipped += 1

    # query_id is the same for every row in a given CSV; attach it here
    # rather than threading it through every format-specific mapper.
    for paper in papers:
        paper["query_id"] = query_id

    if not papers:
        return 0, skipped

    with sqlite3.connect(db_path) as conn:
        conn.executemany(INSERT_SQL, papers)
        conn.commit()

    return len(papers), skipped


def main():
    parser = argparse.ArgumentParser(
        description="Import a CSV of papers into the `papers` SQLite table."
    )
    parser.add_argument("csv", type=Path, help="Path to the CSV file.")
    parser.add_argument(
        "format",
        choices=sorted(PARSERS),
        help="CSV layout to parse.",
    )
    parser.add_argument("db", type=Path, help="Path to the SQLite database.")
    parser.add_argument(
        "query_id",
        type=int,
        help="Value to store in the `query_id` column for every imported row.",
    )
    args = parser.parse_args()

    try:
        imported, skipped = import_csv(args.csv, args.db,
                                       args.format, args.query_id)
    except sqlite3.Error as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        sys.exit(1)

    summary = f"Imported {imported} paper(s) from {args.csv}"
    if skipped:
        summary += f" ({skipped} row(s) skipped)"
    print(summary + ".", file=sys.stderr)


if __name__ == "__main__":
    main()