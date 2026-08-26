# System Documentation — PUF Research Pipeline

This document provides an overview of the PUF Research Pipeline system architecture, module responsibilities, and data flow.

## System Overview

The PUF Research Pipeline is a systematic literature review (SLR) tool for collecting, deduplicating, evaluating, and screening academic papers on **physical attacks on Physical Unclonable Functions (PUFs)**.

The pipeline supports:
1. **Multi-source import** — BibTeX/CSV from ACM, IEEE, Springer, Semantic Scholar
2. **Deduplication** — DOI-based unique constraint prevents duplicates
3. **Empirical relevance scoring** — Hybrid keyword + BM25 scoring against a curated topic keyword set
4. **LLM screening & evaluation harness** — 3-class (in-scope/out-of-scope/hybrid) screening with OpenAI-compatible APIs, plus a comparison harness that scores deterministic, embedding (SBERT), and LLM-prompt methods against a human ground truth.
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
Custom BibTeX parser. Supports `{value}`, `"value"`, and raw delimiters with brace-depth tracking. (The legacy root script `run_bibtex_to_csv.py` that wrapped an earlier copy of this parser was **deleted in TASK-004**; BibTeX import now flows through `cli/import` → `src/bibtex_importer.py`.)

### `src/bibtex_importer.py`
Direct BibTeX → SQLite importer. Handles upsert by DOI, junction table population, and source/query registration.

### `src/csv_importer.py`
CSV → SQLite importer. Supports ACM and IEEE CSV formats. (The legacy root script `run_csv_to_db.py` that this module was factored from was **deleted in TASK-004**; CSV import now flows through `cli/import` → `src/csv_importer.py`, and citation ingestion through `scripts/ingest_citations.py`.)

### `src/mcp_server.py`
Read-only MCP (stdio) server that is the **safe, supported way to query and analyse the database** without handing out raw SQL. It exposes read tools `execute_select`, `get_paper_provenance`, `list_papers`, `search_papers`, `get_paper_by_doi` (plus `store_analysis` / `list_analysis` for persisting analysis results). `execute_select` rejects anything but SELECT statements and opens a read-only connection.

### `src/analysis.py`
Statistics over the corpus, 3-class relevance distribution, snowball status, and per-run evaluation metrics (vs `ground_truth_consensus`). Queries the DB through the MCP server with a direct-SQLite fallback and persists results via `store_analysis`. Exposed as `cli/analyze.py` (`puf analyze`).

### `src/rate_limiter.py`
Smart client-side rate limiting: minimum call spacing, adaptive widening while failures persist, `Retry-After` parsing (seconds or HTTP date), exponential backoff with jitter, and `RateLimitError` once the retry budget is spent.

### `src/relevance.py`
Empirical relevance engine. Hybrid keyword + BM25 scoring with configurable weights and threshold. Stores evaluations in `relevance_evals`.

### `src/screening.py`
LLM screening module. Queries OpenAI-compatible API, verifies excerpts, stores decisions in `decisions` table.

### `src/snowball.py`
Backward + forward snowball expansion. Seeds from explicit paper ids, from `paper_queries` (query ids) or from the whole corpus. References are resolved by a chosen standalone source — `semantic_scholar` (alias `s2`) is a first-class resolve source with **no** OpenAlex fallback, while `crossref` and `openalex` are alternates of each other (a miss on one retries the other; `--no-alternate` disables that retry). All API traffic is paced by a shared `RateLimiter` (`--delay`, default `1.0s`); references are normalised, deduplicated by DOI + normalised title, accumulated into `paper_sources` provenance (`snowball` added to existing query links, never re-inserting a paper), and linked via `snowball_edges`.

### `src/eval_store.py`
Evaluation storage & analysis. Ingests eval JSONL and ground-truth CSV; computes inter-rater agreement (Cohen's / Fleiss' κ) and consensus; computes per-method metrics (precision/recall/F1, κ, ROC-AUC) vs the gold standard.

### `src/baselines.py`
Deterministic (keyword / BM25 / hybrid) and optional SBERT baselines; export their scores as eval JSONL consumable by `eval_store`.

### `cli/eval.py`
Evaluation CLI: `ingest`, `groundtruth`, `metrics`, `runs`, `export-papers`, `baseline`, `screen`.

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
[src/relevance.py] → evaluate corpus → 3-class relevance_class + eval_runs/evals
    │
    ▼
[src/screening.py] → LLM 3-class decisions → eval JSONL
    │
    ▼
[src/baselines.py] → deterministic + SBERT 3-class scores → eval JSONL
    │
    ▼
[src/eval_store.py] → ingest eval JSONL + ground truth → metrics vs consensus
    │
    ▼
[src/analysis.py] → corpus/relevance/snowball/evaluation stats → JSON+PNG (data/analysis) + store_analysis
    │
    ▼
[cli/tui.py] / MCP server (src/mcp_server.py) → interactive browsing, safe read-only DB query
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
| `ground_truth` | Human gold labels (per annotator) |
| `ground_truth_consensus` | Agreed gold labels + agreement |
| `eval_runs` | One evaluation configuration (method/model/prompt) |
| `evals` | Per-paper evaluations (3-class decisions + scores) |
| `llm_judge` | Optional LLM-as-judge quality scores |

## Configuration

- `config/snowball.yaml` — Snowball search parameters (depth, max refs, API choice)
- `config/prompts/*.txt` — the three screening prompts (P1 zero-shot, P2 rubric, P3 few-shot)
- `config/eval_models.json` — registry of the 3 LLMs used for the 3×3 evaluation grid (configurable, no hard-coded ids)
- API keys are passed via CLI arguments (not stored in repo)

## Error Handling

- Importers use try/except per-row; bad rows are skipped with warnings.
- Snowball API calls go through `src/rate_limiter.py`: pacing, `Retry-After`, exponential backoff with jitter, and a clean stop (`aborted: 1`) after `max_retries` consecutive failures.
- Database operations use context managers with rollback on failure.
- LLM screening retries per paper up to `max_retries` times.

## Performance Considerations

- SQLite WAL mode for better concurrent reads.
- Junction tables use composite primary keys for fast lookups.
- BM25 corpus is built in-memory; for >100K papers, consider incremental BM25 or external search engine.
- Snowball API rate limits handled by `RateLimiter` (`--delay` pacing) plus an `--max-api-calls` budget; bounded runs resume by skipping already-expanded seeds.

## Security Considerations

- No secrets stored in repo; API keys passed via CLI args.
- HTML entity unescaping in abstract text to prevent injection.
- Foreign key constraints enforced via `PRAGMA foreign_keys=ON`.
