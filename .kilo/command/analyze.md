---
description: Compute corpus / relevance / snowball / evaluation statistics for the PUF_RA study — query the DB via the MCP server, write JSON + PNG plots to data/analysis, and persist via store_analysis. Backed by scripts/analyze.py / puf analyze.
agent: orchestrator
---
Run the analysis pass for: $ARGUMENTS

Use the `analysis` capability (see `docs/analysis.md`). This is the user-facing
way to produce the study's statistics, backed by `scripts/analyze.py` /
`puf analyze` and `src/analysis.py`.

Run:

    /analyze [corpus|relevance|snowball|evaluation|all] [--name X] [--db results.db] [--out-dir data/analysis] [--mode auto|mcp|direct]

Behavior:
- **`corpus`** — papers per source / discovery method / year (bar + year plots).
- **`relevance`** — 3-class `papers.relevance_class` distribution.
- **`snowball`** — `reference_lists` status breakdown + edge / discovered counts.
- **`evaluation`** — per `eval_run_id` metrics (P/R/F1, κ, ROC/PR-AUC, confusion)
  via `eval_store.compute_metrics` vs `ground_truth_consensus`.
- **`all`** — runs every analysis above (default).

Options:
- `--name X` — analysis name (also the JSON/plot file stem and persisted key;
  default `analysis_<action>`).
- `--db results.db` — SQLite database path.
- `--out-dir data/analysis` — directory for the JSON + PNG outputs.
- `--mode auto|mcp|direct` — transport: `auto` (MCP, fallback to direct SQLite),
  `mcp` (require the MCP server), or `direct` (local connection only).

What it does (no data loss):
1. Builds an `AnalysisClient` that queries the DB **through the MCP server**
   (`execute_select`, `get_paper_provenance`) with a direct-SQLite fallback.
2. Computes the requested statistics and writes `data/analysis/<name>.json`
   plus PNG plots.
3. Persists the full result via `store_analysis` into `analysis_runs`, so the
   run is queryable later through the MCP server (`list_analysis`).

Note: the analyst role is documented in `.kilo/agent/analyst.md`; it is realised
by dispatching a `coder` / `general` subagent with that persona.
