# System Documentation — PUF Research Pipeline

This document provides an overview of the PUF Research Pipeline system architecture, module responsibilities, and data flow.

## System Overview

The PUF Research Pipeline is a systematic literature review (SLR) tool for collecting, deduplicating, evaluating, and screening academic papers on **physical attacks on Physical Unclonable Functions (PUFs)**.

The pipeline supports:
1. **Multi-source import** — BibTeX/CSV from ACM, IEEE, Springer, Semantic Scholar
2. **Deduplication** — DOI-based unique constraint prevents duplicates
3. **Empirical relevance scoring** — Hybrid keyword + BM25 scoring against a curated topic keyword set
4. **LLM screening & evaluation harness** — 3-class (in-scope/out-of-scope/hybrid) screening with OpenAI-compatible APIs, plus a comparison harness that scores deterministic, embedding, and LLM-prompt methods against a human ground truth.
5. **Snowball search (backward *and* forward)** — The original collect → validate → extract flow, implemented as three separable stages: **harvest** (collect each seed's full reference list) → **resolve** (validate every stored reference, extract DOI/title/authors/year) → **backfill** (extract the abstracts). Backward works on Crossref / OpenAlex / Semantic Scholar; forward uses OpenAlex `filter=cites:` and therefore requires `--source openalex`.
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
                  │  (keyword+BM25)  │          │  (OpenAI API)   │        │ (S2/CR/OpenAlex)│
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
**Shared-helper module** for the snowball feature (`_get_json`, reference
normalisation, `s2_get_references`, seed selection, deduplication and
paper/source/edge persistence helpers). The single snowball implementation lives
in `src/reference_store.py` (`harvest_references`, `resolve_reference_lists`,
`backfill_abstracts`); `src/snowball.py` is only the low-level helper layer it
depends on. Zotero is wired as a rate-limit-immune last-resort source in the
resolve + backfill chains (Decision 10).

### `src/reference_store.py`
**Primary two-phase, local-first snowball path — backward *and* forward.** It realises the original *collect → validate → extract* design as three separable, resumable stages:

1. `harvest_references(...)` — **collect**: fetch a seed's complete reference list and store **every** entry (direction-tagged) in `reference_lists`. Backward: Crossref `message.reference`, OpenAlex `referenced_works`, Semantic Scholar `s2_get_references`. Forward: OpenAlex `filter=cites:{openalex_id}` **only** — Crossref and S2 forward search are explicitly skipped, so forward needs `--source openalex` (`--direction forward` / `--direction both`). CLI: `--harvest-only`.
2. `resolve_reference_lists(...)` — **validate**: resolve every unresolved `reference_lists` row (**both** directions) by DOI, with a conservative title fallback, inserting/linking `papers` + `snowball_edges` and recording an explicit `status` per row. On a **rate-limit (HTTP 429)** the next platform in `_SOURCE_RATELIMIT_CHAIN` is tried and the batch **continues** — no abort (TASK-010 / Decision 9). CLI: `--resolve-only`.
3. `backfill_abstracts(...)` — **extract abstracts**: fill empty `papers.abstract` values (by DOI for DOI-bearing papers, and by **title search across Crossref/OpenAlex/S2/Zotero** for DOI-less, title-bearing papers — filling the abstract and, if missing, the DOI and canonical title), committing per paper. It switches sources automatically on **ANY** network error — not just HTTP 429 but also `URLError`/`OSError`/`TimeoutError`/5xx — recording each dead source in a run-wide **`throttled` set** so it is skipped for the remainder of the run (no repeated backoff, no long delays). It runs **batched pre-passes in order Zotero (local, free) -> OpenAlex -> Crossref -> S2**, then the per-DOI fallback (`_fetch_abstract_for_backfill` / `_backfill_title_search`). CLI: `puf snowball backfill-abstracts`.

All external HTTP is paced by a **source-aware** `RateLimiter` (Crossref / OpenAlex
~0.05 s, Semantic Scholar ~0.6 s, Zotero instant). Resolution also runs a **batched
OpenAlex multi-DOI pre-pass** (`_openalex_batch_by_dois`) — active for the **OpenAlex
resolve** source and **Crossref/OpenAlex backfill** (Crossref *resolve* stays per-DOI
polite) — plus a **batched Zotero pre-pass** that resolves DOIs from the local library
before the slow Semantic Scholar endpoint; both are **additive** and skipped under
`--no-batch`, which leaves the per-DOI cross-source chain as the sole path (useful when
OpenAlex is unavailable / budget-blocked). For `backfill_abstracts` specifically, the batch pre-passes run in order **Zotero (local, free) -> OpenAlex -> Crossref -> S2**; each is guarded by the run-wide `throttled` set and **skipped once its source errors** (a transient failure records the source as dead so later papers skip it). The OpenAlex pre-pass is active only when the chosen `source` is `openalex`; Crossref/S2 pre-passes run for every source. Zotero (local) is rate-limit immune and resolves cached DOIs instantly.

`semantic_scholar` (alias `s2`) is a first-class resolve source that is standalone for *not-found* DOIs but falls back to OpenAlex/Crossref on a rate-limit by default; `crossref` and `openalex` are alternates of each other (a miss on one retries the other). `--no-alternate` disables all cross-source fallback, incl. on rate-limit (the affected DOIs become `fetch_error` and the batch still continues). All API traffic is paced by a shared `RateLimiter` (`--delay`, default `1.0s`). Full methodology: [`docs/snowballing.md`](snowballing.md).

### `src/eval_store.py`
Evaluation storage & analysis. Ingests eval JSONL and ground-truth CSV; computes inter-rater agreement (Cohen's / Fleiss' κ) and consensus; computes per-method metrics (precision/recall/F1, κ, ROC-AUC) vs the gold standard.

### `src/baselines.py`
Deterministic (keyword / BM25 / hybrid) and embedding baselines; export their scores as eval JSONL consumable by `eval_store`.

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
[src/baselines.py] → deterministic + embedding 3-class scores → eval JSONL
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
| `snowball_edges` | Snowball provenance: parent → child edges (backward *and* forward) |
| `reference_lists` | Harvested reference inventory (direction-tagged) + assured-retrieval `status` |
| `snowball_runs` | One row per harvest / resolve run (TARCiS-style accounting) |
| `relevance_evals` | Empirical relevance evaluations |
| `runs` | LLM screening runs |
| `decisions` | Per-paper LLM decisions |
| `ground_truth` | Human gold labels (per annotator) |
| `ground_truth_consensus` | Agreed gold labels + agreement |
| `eval_runs` | One evaluation configuration (method/model/prompt) |
| `evals` | Per-paper evaluations (3-class decisions + scores) |
| `llm_judge` | Optional LLM-as-judge quality scores |

## Configuration

- Snowball parameters are **CLI flags**, not a config file (`--depth`, `--max-refs`, `--source`, `--direction`, `--delay`, `--max-api-calls`, `--harvest-only` / `--resolve-only`, `--no-alternate`); there is no `config/snowball.yaml`
- `config/prompts/*.txt` — the three screening prompts (P1 zero-shot, P2 rubric, P3 few-shot)
- `config/eval_models.json` — registry of the 3 LLMs used for the 3×3 evaluation grid (configurable, no hard-coded ids)
- API keys are passed via CLI arguments (not stored in repo)

## Error Handling

- Importers use try/except per-row; bad rows are skipped with warnings.
- Snowball API calls go through `src/rate_limiter.py`: pacing, `Retry-After`, exponential backoff with jitter. On the **resolve / backfill** stages of the two-phase path, a `RateLimitError` after `max_retries` consecutive failures is handled by falling back to the next source in the chain and continuing the batch (it never raises/aborts those stages; only a fully throttled chain records `fetch_error`). `--no-alternate` makes even a rate-limit strict-single-source (the affected DOIs become `fetch_error` and the batch still continues).
- The **harvest** stage's seed reference-list fetch keeps a **graceful stop with
  `aborted: 1`**, because it has a single source and no cross-source fallback (the
  inventory gathered so far is committed and the next run resumes).
- Database operations use context managers with rollback on failure.
- LLM screening retries per paper up to `max_retries` times.

## Performance Considerations

- SQLite WAL mode for better concurrent reads.
- Junction tables use composite primary keys for fast lookups.
- BM25 corpus is built in-memory; for >100K papers, consider incremental BM25 or external search engine.
- Snowball API rate limits handled by `RateLimiter` (`--delay` pacing) plus an `--max-api-calls` budget; bounded runs resume by skipping already-expanded seeds, and `--resolve-only` resumes the resolve stage over rows still `pending` (idempotent, guarded by `resolved_paper_id IS NULL`).

## Security Considerations

- No secrets stored in repo; API keys passed via CLI args.
- HTML entity unescaping in abstract text to prevent injection.
- Foreign key constraints enforced via `PRAGMA foreign_keys=ON`.
