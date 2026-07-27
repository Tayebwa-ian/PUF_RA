# System Documentation — PUF Research Pipeline

This document provides an overview of the PUF Research Pipeline system architecture, module responsibilities, and data flow.

## System Overview

The PUF Research Pipeline is a systematic literature review (SLR) tool for collecting, deduplicating, evaluating, and screening academic papers on **physical attacks on Physical Unclonable Functions (PUFs)**.

The pipeline supports:
1. **Multi-source import** — BibTeX/CSV from ACM, IEEE, Springer, Semantic Scholar
2. **Deduplication** — DOI-based unique constraint prevents duplicates
3. **Empirical relevance scoring** — Hybrid keyword + BM25 scoring against a curated topic keyword set
4. **LLM screening** — Automated REVIEW/EXCLUDE decisions using OpenAI-compatible APIs
5. **Snowball/backward search** — Recursive expansion via reference chain following (Semantic Scholar / Crossref)
6. **TUI** — Interactive terminal UI for browsing, filtering, and managing the corpus

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌────────────────────┐
│  Data Sources   │────▶│   Import Layer  │────▶│   SQLite Database  │
│  (ACM, IEEE,    │     │  (bibtex, csv)  │     │   (v2 schema)      │
│   Springer,     │     │                 │     │                    │
│   Semantic)     │     │                 │     │                    │
└─────────────────┘     └─────────────────┘     └────────────────────┘
                                                        │
                          ┌─────────────────────────────┼─────────────────────────────┐
                          │                             │                             │
                          ▼                             ▼                             ▼
                  ┌─────────────────┐          ┌─────────────────┐        ┌─────────────────┐
                  │  Relevance Engine│          │  LLM Screening  │        │  Snowball Search│
                  │  (keyword+BM25)  │          │  (OpenAI API)   │        │  (S2/Crossref)  │
                  └─────────────────┘          └─────────────────┘        └─────────────────┘
                          │                             │                             │
                          └─────────────────────────────┼─────────────────────────────┘
                                                        │
                                                        ▼
                                              ┌────────────────────┐
                                              │   TUI / CLI        │
                                              │   (Textual UI)     │
                                              └────────────────────┘
```

## Module Responsibilities

### `src/db.py`
Database connection management with context manager, common query helpers.

### `src/db_schema.py`
Schema definition and migration utilities. Holds all CREATE TABLE statements and a migration path from v1 to v2.

### `src/bibtex_parser.py`
Custom BibTeX parser. Extracted from the original `run_bibtex_to_csv.py`. Supports `{value}`, `"value"`, and raw delimiters with brace-depth tracking.

### `src/bibtex_importer.py`
Direct BibTeX → SQLite importer. Handles upsert by DOI, junction table population, and source/query registration.

### `src/csv_importer.py`
CSV → SQLite importer. Supports ACM and IEEE CSV formats. Refactored from `run_csv_to_db.py`.

### `src/relevance.py`
Empirical relevance engine. Hybrid keyword + BM25 scoring with configurable weights and threshold. Stores evaluations in `relevance_evals`.

### `src/screening.py`
LLM screening module. Queries OpenAI-compatible API, verifies excerpts, stores decisions in `decisions` table.

### `src/snowball.py`
Backward snowball search. Fetches references via Semantic Scholar API (primary) and Crossref (fallback). Normalises references, deduplicates by DOI/title, creates `snowball_edges`.

### `cli/*.py`
CLI entry points using argparse. Each subcommand maps to a single module responsibility.

### `cli/tui.py`
Textual-based TUI with Dashboard, Paper List, Paper Detail, and Snowball screens.

## Data Flow

```
BibTeX files
    │
    ▼
[src/bibtex_parser.py] → parsed entries
    │
    ▼
[src/bibtex_importer.py] → upsert to papers, link to queries/sources
    │
    ▼
[src/relevance.py] → evaluate corpus → populate relevance_evals
    │
    ▼
[src/screening.py] → LLM decisions → populate decisions
    │
    ▼
[src/snowball.py] → fetch references → new papers + edges
    │
    ▼
[cli/tui.py] → interactive browsing, filtering, export
```

## Database Schema (v2)

See `database_struct.sql` for the canonical schema. Key tables:

| Table | Purpose |
|---|---|
| `sources` | Data platforms (ACM, IEEE, etc.) |
| `queries` | Search queries run on platforms |
| `papers` | Central paper registry (DOI unique) |
| `paper_queries` | Junction: queries ↔ papers |
| `paper_sources` | Junction: sources ↔ papers |
| `snowball_edges` | Backward search provenance |
| `relevance_evals` | Empirical relevance evaluations |
| `runs` | LLM screening runs |
| `decisions` | Per-paper LLM decisions |

## Configuration

- `config/snowball.yaml` — Snowball search parameters (depth, max refs, API choice)
- API keys are passed via CLI arguments (not stored in repo)
- System prompts for LLM screening are passed via file path

## Error Handling

- Importers use try/except per-row; bad rows are skipped with warnings.
- Snowball API calls use exponential backoff for rate limits.
- Database operations use context managers with rollback on failure.
- LLM screening retries per paper up to `max_retries` times.

## Performance Considerations

- SQLite WAL mode for better concurrent reads.
- Junction tables use composite primary keys for fast lookups.
- BM25 corpus is built in-memory; for >100K papers, consider incremental BM25 or external search engine.
- Snowball API rate limits handled with configurable delay.

## Security Considerations

- No secrets stored in repo; API keys passed via CLI args.
- HTML entity unescaping in abstract text to prevent injection.
- Foreign key constraints enforced via `PRAGMA foreign_keys=ON`.
