# PUF Research Pipeline

A toolchain for a Master's-thesis study that **evaluates the tools** (LLM prompts
and baselines) used to build a Systematication-of-Knowledge (SoK) paper on
**physical attacks on Physical Unclonable Functions (PUFs)**. It collects,
deduplicates, scores, and screens papers, then compares screening methods against a
human ground truth. (The LLMs are screening aids under evaluation — not
co-authors.)

## Features

- **Multi-source import** — BibTeX/CSV from ACM, IEEE, Springer, Semantic Scholar
- **Deduplication** — DOI-based unique constraint prevents duplicates
- **Empirical relevance scoring** — Hybrid keyword + BM25 scoring against a curated topic keyword set
- **LLM screening** — 3-class (in-scope / out-of-scope / hybrid) screening using OpenAI-compatible APIs; emits structured JSONL.
- **Evaluation harness** — compares deterministic, embedding, and LLM-prompt screening against a human ground truth (Cohen's/Fleiss' κ, precision/recall/F1, ROC-AUC).
- **Snowball search (backward + forward)** — Citation-graph expansion in three resumable stages: **harvest** (collect each seed's full reference list) → **resolve** (validate them, extract title/authors/year) → **backfill** (extract abstracts via batched Crossref + Semantic Scholar pre-passes, with OpenAlex as a rate-limited extra). Backward via Crossref / OpenAlex / Semantic Scholar, forward via OpenAlex `cites:` (needs `--source openalex`); rate-limit resilient — the resolve stage switches source on HTTP 429 and keeps going
- **Interactive TUI** — Textual-based terminal UI for browsing and filtering
- **Embedding storage** — Dense vector embeddings stored systematically in the `paper_embeddings` table (columns: `paper_id`, `model_name`, `embedding_vector` [JSON array of floats], `computed_at`) to avoid recomputation during threshold setting and classification. Primary method: Uni Passau-hosted `octen-embedding-8b` via OpenAI-compatible API.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Import BibTeX files
puf import bibtex cititations_data/query1/*.bib --query-ids 1 --source ACM
puf import bibtex cititations_data/query2/*.bib --query-ids 2 --source IEEE

# Evaluate relevance (threshold auto-derived from ground truth; 0.15 only the fallback when none)
puf relevance evaluate

# Run LLM screening (requires API key)
puf eval screen --prompt config/prompts/p2_rubric.txt --model "$MODEL" \
  --api-key "$API_KEY" --base-url "$BASE_URL" --query-ids 3 4 \
  --out data/evals/llm_p2_${MODEL}.jsonl --prompt-id P2

# ---- Evaluation study (see docs/evaluation.md) ----
puf eval export-papers data/ground_truth/ground_truth_template.csv
puf eval groundtruth data/ground_truth/ground_truth.csv
puf eval baseline --method hybrid --out data/evals/baseline_hybrid.jsonl
puf eval baseline --method embedding --out data/evals/embedding.jsonl
puf eval ingest data/evals/*.jsonl
puf eval screen --prompt config/prompts/p2_rubric.txt --model "$MODEL" \
  --api-key "$API_KEY" --base-url "$BASE_URL" --query-ids 3 4 \
  --out data/evals/llm_p2_${MODEL}.jsonl --prompt-id P2
puf eval metrics <run_id>

# Launch TUI
puf tui
```

## CLI Reference

```
puf import bibtex <files...> --query-ids <ids> --source <name>
puf import csv <file> --format <acm|ieee> --query-ids <ids> --source <name>
puf relevance evaluate [--method <keyword|bm25|hybrid>] [--derive-threshold] [--threshold 0.15]
puf relevance paper <id>
puf relevance stats
puf screen --model <name> --api-key <key> --base-url <url> --query-ids <ids>
puf snowball run --query-ids <ids> [--depth 1] [--max-refs 20]
                 [--source <semantic_scholar|crossref|openalex|s2|zotero>]
                 [--direction <backward|forward|both>]   # forward/both require --source openalex
                 [--harvest-only | --resolve-only] [--delay 1.0] [--max-api-calls 40] [--no-alternate] [--no-batch]
puf snowball backfill-abstracts [--source <crossref|openalex|semantic_scholar|s2|zotero>] [--delay 1.0] [--no-batch]
puf snowball stats
puf tui
puf eval ingest <files...>
puf eval groundtruth <csv>
puf eval metrics <run_id>
puf eval runs
puf eval export-papers <out.csv>
puf eval baseline --method <keyword|bm25|hybrid|embedding> --out <jsonl>
puf eval screen --prompt <file> --model <name> --api-key <key> --base-url <url> --query-ids <ids> --out <jsonl>
```

## Project Structure

```
PUF_RA/
├── cititations_data/        # BibTeX exports from ACM/IEEE
├── src/                     # Core library modules
│   ├── bibtex_parser.py
│   ├── bibtex_importer.py
│   ├── csv_importer.py
│   ├── db.py
│   ├── db_schema.py
│   ├── embeddings.py        # Uni Passau octen-embedding-8b API client and paper_embeddings storage
│   ├── relevance.py
│   ├── screening.py
│   ├── snowball.py           # Shared-helper module (low-level snowball helpers used by reference_store)
│   ├── reference_store.py    # Two-phase harvest → resolve → backfill (backward + forward)
│   ├── eval_store.py        # Eval/ground-truth ingest, κ, metrics
│   └── baselines.py         # Deterministic + embedding baseline JSONL exporters
├── cli/                     # CLI entry points
│   ├── main.py              # Dispatcher: puf <cmd>
│   ├── import.py
│   ├── relevance.py
│   ├── screen.py
│   ├── snowball.py
│   ├── tui.py
│   └── eval.py              # puf eval <subcommand>
├── config/
│   ├── prompts/             # p1_zero_shot.txt, p2_rubric.txt, p3_fewshot.txt
│   └── eval_models.json     # Configurable LLM registry (3x3 grid)
├── data/
│   ├── ground_truth/        # Human gold labels (you + co-annotator)
│   └── evals/              # Eval JSONL (baselines + LLM prompts)
├── tests/                   # Unit tests
├── docs/                    # System documentation
├── database_struct.sql      # Canonical SQLite schema (v2)
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Documentation

- `docs/design_decisions.md` — Architectural rationale and tradeoffs
- `docs/schema.md` — ER diagram and table reference
- `docs/snowballing.md` — Snowball methodology: harvest → resolve → backfill stages, backward + forward directions, rate-limit resilience, assured retrieval, results
- `docs/relevance.md` — Relevance engine methodology, threshold derivation, embedding approach
- `docs/llm_screening.md` — LLM screening setup, 3-class prompts, eval JSONL
- `docs/evaluation.md` — Evaluation study protocol, metrics, reproducibility, embedding storage architecture
- `docs/ground_truth_guidelines.md` — Human gold-label contract & format
- `docs/prompts.md` — Prompt-design rationale & best practices
- `docs/pipeline.md` — End-to-end data flow
- `docs/migration.md` — Migrating from v1 schema

## Migration from v1

If you have an existing `results.db` from the old pipeline:

```python
from src.db_schema import migrate_from_v1
from src.db import get_connection

with get_connection("results.db") as conn:
    migrate_from_v1(conn, dry_run=False)
```

## Requirements

- Python 3.10+
- SQLite 3
- Dependencies: see `requirements.txt`

## License

MIT
