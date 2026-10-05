# Analysis — Corpus / Relevance / Snowball / Evaluation Statistics

This document explains the analysis capability of the PUF_RA study end-to-end:
the **analyst agent role**, the **`/analyze` command**, how it queries the
database, what statistics it produces, and how the results are persisted.

## 1. The analyst role

The `analyst` is a dedicated analysis agent (see `.kilo/agent/analyst.md`). It
is *realised by dispatching a `coder` / `general` subagent* with that persona —
the harness `Task` tool uses fixed subagent types, so there is no standalone
`analyst` worker; the orchestrator configures a subagent to follow the analyst
conventions. Its responsibilities:

- Design efficient SQL / queries for the statistic being computed.
- Query the database **through the MCP server** (or a direct-SQLite fallback).
- Compute insightful statistics (corpus composition, 3-class relevance
  distribution, snowball edges, per-run evaluation metrics).
- Generate graphs/plots (matplotlib) and write them to `data/analysis/`.
- Persist results (`data/analysis/<name>.json` + PNG **and** `store_analysis`)
  so they are queryable later.

## 2. The `/analyze` command

Backed by `scripts/analyze.py` / `cli/analyze.py` (`puf analyze`) and
`src/analysis.py`. It queries the DB through the MCP server and writes stats +
plots to `data/analysis/`, persisting via `store_analysis`.

```bash
/analyze [corpus|relevance|snowball|evaluation|all] [--name X] [--db results.db] [--out-dir data/analysis] [--mode auto|mcp|direct]
```

| Argument | Meaning |
|---|---|
| `corpus` | Papers per source / discovery method / year (bar + year plots). |
| `relevance` | 3-class `papers.relevance_class` distribution. |
| `snowball` | `reference_lists` status breakdown + edge / discovered counts. |
| `evaluation` | Per `eval_run_id` metrics vs `ground_truth_consensus`. |
| `all` | Runs every analysis above (default). |
| `--name X` | Analysis name (JSON/plot stem + persisted key; default `analysis_<action>`). |
| `--db results.db` | SQLite database path. |
| `--out-dir data/analysis` | Output directory for JSON + PNG. |
| `--mode auto\|mcp\|direct` | Transport: `auto` (MCP, fallback direct), `mcp` (require MCP), `direct` (local connection only). |

## 3. Querying the DB through the MCP server

The analyst never reaches for raw SQL against the live data. It builds an
`AnalysisClient` (`src/analysis.py`) which:

1. **Prefers the MCP server** (`src/mcp_server.py`) — the safe, read-only way to
   query. **One** stdio session (one `python -m src.mcp_server` subprocess) is
   opened per client and reused for every call; `client.close()` (or using the
   client as a context manager) shuts it down. Tool results are decoded from the
   SDK's snake_case `CallToolResult` fields (`is_error`, `content`,
   `structured_content`), collecting **all** content blocks — a list-returning
   tool emits one text block per row — and a guard rejection is raised as a
   `ValueError`. Read tools used:
   - `execute_select(sql)` — runs a guarded `SELECT` (rejects anything but
     SELECT, opens a read-only connection).
   - `get_paper_provenance(paper_id)` — returns sources / queries / snowball
     links for a paper.
   - `list_papers`, `search_papers`, `get_paper_by_doi` — discovery helpers.
2. **Falls back to a direct SQLite connection** when MCP is unavailable
   (`--mode direct`, or `auto` with no MCP server). The direct path mirrors the
   same read-only semantics and only the `store_analysis` write is permitted.

Per-run evaluation metrics are computed with `eval_store.compute_metrics(conn,
eval_run_id)` against a local connection, because they aggregate the consensus
gold standard rather than being an ad-hoc query; everything else flows through
`execute_select` / `get_paper_provenance`.

## 4. Statistics produced

| Statistic | Source | Notes |
|---|---|---|
| Corpus by source | `sources` ⋈ `paper_sources` | Distinct papers **per source** (`COUNT(DISTINCT paper_id)`). |
| Corpus by method | `sources` ⋈ `paper_sources` | Collapsed into `database_query` / `snowball` / `other`. **Counted per paper–source link:** a paper found by several methods is counted **in each** method, so the categories can sum to more than the corpus size (e.g. 2783 + 584 = 3367 links for 2775 distinct papers). The distinct-paper view is reported alongside: `by_category_distinct_papers`, `n_papers_distinct`, `n_multi_method_papers`, plus a `counting` note carried into the JSON. |
| Provenance mix | `get_paper_provenance` | For the most multiply-sourced papers: their sources, query count and snowball parents/children (makes the multi-method overlap inspectable). |
| Corpus by year | `papers` (group by `year`) | Publication-year histogram. |
| Relevance distribution | `papers.relevance_class` | Counts per 3-class label (`in-scope` / `out-of-scope` / `hybrid` / `unlabeled`). |
| Snowball status | `reference_lists.status` + `snowball_edges` | Status breakdown, edge count, distinct discovered papers. |
| Evaluation summary | `eval_runs` + `evals` vs `ground_truth_consensus` | Per `eval_run_id`: P/R/F1 (macro), accuracy, Cohen's κ, the scalar ROC-AUC `auc_in_scope` (where a continuous `score` exists), and a 3×3 confusion matrix. Emitted only when both `evals` and `ground_truth_consensus` are non-empty. |

All methods share one `eval_run_id` namespace (`evals.run_id = eval_runs.id`), so
the deterministic baseline, embedding baseline, and the 9 LLM configs are directly comparable
(see `docs/evaluation.md`).

## 5. Persisting results

For each run named `<name>`:

- `data/analysis/<name>.json` — the full statistics dict (corpus / relevance /
  snowball / evaluation).
- `data/analysis/<name>_*.png` — plots (corpus bars, years, snowball status,
  eval metrics, and `<name>_confusion.png` — the per-run 3×3 confusion-matrix
  heatmaps from `plot_confusion`; there is no ROC curve because only the scalar
  `auc_in_scope` is available) generated with matplotlib.
- The `analysis_runs` table — written via the MCP `store_analysis` tool (or the
  direct equivalent), recording `(name, result_json)`. Retrieve later with
  `list_analysis` through the MCP server.

This dual persistence (files for humans, `analysis_runs` for machines) means any
analysis is reproducible and re-queryable without re-running the pipeline.

## 6. Example

```bash
puf analyze all --db results.db --name run1
```

Computes every statistic against `results.db`, writes `data/analysis/run1.json`
plus the PNG plots, and persists the run as `run1` in `analysis_runs`. Inspect
later with the MCP `list_analysis` tool, or re-run a single slice:

```bash
puf analyze evaluation --db results.db --name run1_eval --mode mcp
```
