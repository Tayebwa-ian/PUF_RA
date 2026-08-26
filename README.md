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
- **Evaluation harness** — compares deterministic, SBERT, and LLM-prompt screening against a human ground truth (Cohen's/Fleiss' κ, precision/recall/F1, ROC-AUC).
- **Snowball/backward search** — Recursive expansion via reference chain following
- **Interactive TUI** — Textual-based terminal UI for browsing and filtering

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
puf screen --model qwen3-next-80b-a3b-instruct \
  --api-key "$API_KEY" \
  --base-url "https://llms.innkube.fim.uni-passau.de" \
  --query-ids 3 4 \
  --system-prompt system_prompt.txt

# ---- Evaluation study (see docs/evaluation.md) ----
puf eval export-papers data/ground_truth/ground_truth_template.csv
puf eval groundtruth data/ground_truth/ground_truth.csv
puf eval baseline --method hybrid --out data/evals/baseline_hybrid.jsonl
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
puf snowball stats
puf tui
puf eval ingest <files...>
puf eval groundtruth <csv>
puf eval metrics <run_id>
puf eval runs
puf eval export-papers <out.csv>
puf eval baseline --method <keyword|bm25|hybrid|sbert> --out <jsonl>
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
│   ├── relevance.py
│   ├── screening.py
│   ├── snowball.py
│   ├── eval_store.py        # Eval/ground-truth ingest, κ, metrics
│   └── baselines.py         # Deterministic + SBERT baseline JSONL exporters
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
- `docs/snowballing.md` — Snowball search methodology, algorithm, API docs
- `docs/relevance.md` — Relevance engine methodology
- `docs/llm_screening.md` — LLM screening setup, 3-class prompts, eval JSONL
- `docs/evaluation.md` — Evaluation study protocol, metrics, reproducibility
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
