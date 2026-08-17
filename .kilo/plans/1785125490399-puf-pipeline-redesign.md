# Plan: PUF Research Pipeline — Database Redesign, Relevance Engine, Snowball Search & TUI

> **Status:** Implementation-ready. Approved: junction tables, docs folder, snowball search.

---

## 1. Current State Analysis

### Existing files
| File | Role | Notes |
|---|---|---|
| `database_struct.sql` | SQLite schema | 4 tables: `queries`, `papers`, `runs`, `decisions` |
| `run_bibtex_to_csv.py` | BibTeX → CSV | Custom parser, argparse CLI |
| `run_csv_to_db.py` | CSV → SQLite | Supports ACM/IEEE CSV formats |
| `run_llm_screening.py` | LLM screening | Hardcoded paths, no CLI, OpenAI-compatible API |
| `cititations_data/query{1,2}/` | .bib files | 4 large BibTeX files from ACM + IEEE |
| `how_to_get_csvs.md` | Documentation | How to download CSVs from IEEE/ACM |

### Problems with current design
1. **`papers.query_id` is a single FK** — a paper found in both query1 and query2 is stored as two rows (no deduplication).
2. **No source tracking** — `papers` has no column indicating which database(s) a paper came from.
3. **No DOI uniqueness constraint** — the same paper can be inserted multiple times from different queries/sources.
4. **No relevance mechanism** — all papers are treated equally; no empirical way to score topic relevance.
5. **Pipeline is multi-step (BibTeX→CSV→DB)** — unnecessary intermediate format; error-prone and hard to extend.
6. **No UI** — all interaction is through ad-hoc scripts; no way to browse, filter, or inspect the database.
7. **Hardcoded configuration** in `run_llm_screening.py` (paths, model, query IDs).
8. **No snowball/backward search** — can't expand relevant papers by following their reference chains.

---

## 2. Proposed Database Schema

### Core design principle
**Normalize source/query relationships using junction tables, not boolean columns.**

#### Why not boolean columns?
- Boolean columns (`from_ACM`, `query1`, `query2`) don't scale: adding query3 or source "Springer" requires schema migrations.
- A many-to-many relationship (papers ↔ queries, papers ↔ sources) is the canonical relational model.
- Junction tables support efficient queries like "all papers from ACM that also appeared in query1 and query2".

#### New schema

```sql
-- =====================================================================
-- SOURCES: the databases/platforms papers are retrieved from
-- =====================================================================
CREATE TABLE sources (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- 'ACM', 'IEEE', 'Springer', 'Semantic Scholar', 'snowball'
    description TEXT
);

-- =====================================================================
-- QUERIES: search queries run on specific platforms
-- =====================================================================
CREATE TABLE queries (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    platform TEXT NOT NULL,
    query_text TEXT NOT NULL
);

-- =====================================================================
-- PAPERS: central paper registry (DOI is unique to avoid duplicates)
-- =====================================================================
CREATE TABLE papers (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER NOT NULL,
    abstract TEXT NOT NULL,
    publication_title TEXT NOT NULL,
    doi TEXT UNIQUE,                    -- NULL allowed; UNIQUE where not NULL
    keywords TEXT,
    is_relevant BOOLEAN DEFAULT NULL,   -- NULL = not yet evaluated
    relevance_score REAL,               -- empirical score from relevance engine
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    updated_at TEXT NOT NULL DEFAULT current_timestamp
);

-- =====================================================================
-- JUNCTION: which queries returned which papers
-- =====================================================================
CREATE TABLE paper_queries (
    paper_id INTEGER NOT NULL,
    query_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, query_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
);

-- =====================================================================
-- JUNCTION: which sources provided which papers
-- =====================================================================
CREATE TABLE paper_sources (
    paper_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, source_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

-- =====================================================================
-- SNOWBALL_EDGES: tracks parent->child relationships in backward search
-- =====================================================================
CREATE TABLE snowball_edges (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    child_paper_id INTEGER NOT NULL,         -- the paper that was found
    parent_paper_id INTEGER NOT NULL,        -- the paper whose references led to it
    depth INTEGER NOT NULL DEFAULT 1,        -- snowball depth (1 = direct reference)
    discovered_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (child_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

-- =====================================================================
-- RELEVANCE_EVALS: empirical relevance evaluations per paper
-- =====================================================================
CREATE TABLE relevance_evals (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    method TEXT NOT NULL,               -- 'keyword', 'bm25', 'llm'
    score REAL NOT NULL,
    is_relevant BOOLEAN NOT NULL,
    threshold REAL NOT NULL,
    details TEXT,                       -- JSON: matched terms, BM25 scores, etc.
    evaluated_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

-- =====================================================================
-- RUNS: LLM screening runs
-- =====================================================================
CREATE TABLE runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    model TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    misc TEXT NOT NULL
);

-- =====================================================================
-- DECISIONS: per-paper LLM decisions
-- =====================================================================
CREATE TABLE decisions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    criterion TEXT NOT NULL,
    justification TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    excerpt_verified BOOLEAN NOT NULL,
    tokens_used INT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);
```

### ER diagram (textual)
```
sources (1) ──── (N) paper_sources (N) ──── (1) papers (1) ──── (N) paper_queries (N) ──── (1) queries
                                                                         │
                                                                         ▼
                                                                   relevance_evals
                                                                         │
papers (1) ──── (N) decisions (N) ──── (1) runs
   │
   └─── (N) snowball_edges ──── (N) papers
```

### Migration path
- Existing `results.db` can be migrated via a one-time script that creates the new tables, copies data, and populates junction tables.
- The old `papers.query_id` FK is deprecated; the migration script will populate `paper_queries` from it and then drop the column (or leave it for backward compatibility).

---

## 3. Relevance Mechanism (Empirical)

### Design: Hybrid Keyword + BM25 Scoring

**Goal:** Provide an empirical, reproducible relevance score for each paper against the topic:
> *"Physical attacks on Physical Unclonable Functions (PUFs)"*

**Topic keyword set (curated from domain expertise):**

| Category | Keywords |
|---|---|
| Core | `puf`, `physical unclonable function` |
| Attack types | `power analysis`, `side-channel`, `electromagnetic`, `em analysis`, `probing`, `invasive attack`, `semi-invasive`, `delayering`, `cloning`, `modeling attack`, `machine learning attack`, `fault injection` |
| PUF types | `arbiter puf`, `ring oscillator`, `ro puf`, `sram puf`, `bistable ring`, `puf-based` |

**Algorithm:**
1. **Preprocessing:** Lowercase, remove punctuation, tokenize abstract.
2. **Keyword matching:** Count exact/partial matches of topic keywords in the abstract. Weight by keyword category (core terms ×2, attack terms ×3, PUF types ×1.5).
3. **BM25 scoring:** Build a corpus from all abstracts. Compute BM25 score of each abstract against the concatenated topic keyword set. This captures term frequency and document frequency.
4. **Composite score:** `score = 0.4 * normalized_keyword_score + 0.6 * BM25_score`
5. **Threshold:** `is_relevant = score >= 0.15` (configurable). Papers with NULL abstract or very short abstracts get score 0.

**Storage:** Each evaluation run stores a row in `relevance_evals` with method, score, threshold, and JSON details (matched terms, per-keyword contributions).

**Extensibility:** The `method` column supports future LLM-based relevance scoring (reusing the existing `run_llm_screening.py` infrastructure).

---

## 4. Snowball / Backward Search

### What is it?
Snowball (backward) search expands the literature review by following the reference chains of already-relevant papers. If paper A cites paper B, and paper B is about PUF attacks, then paper B should be included in our corpus.

### Design: Semantic Scholar API + Crossref Fallback

**Primary source:** [Semantic Scholar API](https://api.semanticscholar.org/) (free, no auth required for basic tier)
- Endpoint: `GET /paper/{paper_id}/references?fields=title,authors,year,abstract,externalIds,publicationVenue`
- Rate limit: ~100 requests per 5 minutes
- Returns structured reference data with DOIs when available

**Fallback:** [Crossref API](https://api.crossref.org/works/{DOI})
- Endpoint: `GET /works/{DOI}`
- Returns `reference` array with DOIs, titles, authors
- Rate limit: ~50 requests per second (polite pool)

**Algorithm:**
1. **Fetch references** for each seed paper (papers where `is_relevant = True` or `LLM decision = REVIEW`) using Semantic Scholar.
2. **Normalize references:** Extract DOI, title, authors, year from each reference entry.
3. **Deduplicate:** Check if reference already exists in DB by DOI (preferred) or normalized title (fuzzy match if DOI missing).
4. **Insert new papers:** Create `papers` row with source = "snowball". Link via `paper_sources` and `snowball_edges` (parent → child).
5. **Track depth:** Depth 1 = direct reference from seed paper. Depth 2 = reference of a reference (optional recursive expansion).
6. **Re-run pipeline:** After import, run relevance evaluation and optionally LLM screening on new papers.
7. **Avoid cycles:** Use `snowball_edges` primary key (child, parent) to prevent re-processing the same edge.

**CLI commands:**
```
puf snowball run [--depth 1] [--max-refs-per-paper 20] [--source semantic|crossref]
puf snowball stats                     # show snowball expansion stats
puf snowball graph [--format dot]      # export snowball graph for visualization
```

**TUI integration:**
- Paper detail view shows "References" section with count of snowball children.
- Button "Fetch references" triggers snowball expansion for a single paper.
- Visual graph of snowball expansion (using Textual's tree widget or export to Graphviz).

**Storage in DB:**
- `paper_sources` gets a new source `"snowball"` (or `"Semantic Scholar"` for API-sourced papers).
- `snowball_edges` table tracks parent→child relationships with depth.
- `relevance_evals` can track whether a paper was discovered via snowball and its relevance score.

**Configuration:**
```yaml
# config/snowball.yaml
max_depth: 2
max_refs_per_paper: 20
api: semantic_scholar
rate_limit_delay: 1.0  # seconds between API calls
auto_screen: true       # run LLM screening on new papers
auto_relevance: true    # run relevance eval on new papers
```

---

## 5. Pipeline Redesign

### New flow: BibTeX → SQLite (direct)

**Old flow:** `*.bib` → `run_bibtex_to_csv.py` → `*.csv` → `run_csv_to_db.py` → SQLite
**New flow:** `*.bib` → `src/bibtex_importer.py` → SQLite (direct)

**Benefits:**
- Eliminates intermediate CSV files.
- Captures source and query metadata directly during import.
- Handles DOI-based deduplication atomically.
- Single responsibility per module.

### Modules

| Module | Responsibility |
|---|---|
| `src/bibtex_parser.py` | Parse BibTeX (extracted from `run_bibtex_to_csv.py`, enhanced with source tracking) |
| `src/db_schema.py` | Schema definition + migration utilities |
| `src/bibtex_importer.py` | Import parsed BibTeX into SQLite (dedup, junction tables) |
| `src/csv_importer.py` | Import CSV into SQLite (refactored from `run_csv_to_db.py`) |
| `src/relevance.py` | Relevance engine (keyword + BM25) |
| `src/screening.py` | LLM screening (refactored from `run_llm_screening.py`) |
| `src/snowball.py` | Snowball/backward search via Semantic Scholar API |
| `src/db.py` | Database connection + common query helpers |
| `cli/import.py` | CLI entry point for importing BibTeX/CSV |
| `cli/relevance.py` | CLI entry point for running relevance evaluation |
| `cli/screen.py` | CLI entry point for LLM screening |
| `cli/snowball.py` | CLI entry point for snowball search |
| `cli/tui.py` | Textual-based TUI for database interaction |

### Deduplication logic in `bibtex_importer.py`
```python
def upsert_paper(conn, paper_dict, query_ids, source_ids):
    """Insert or update a paper, linking it to queries and sources.
    
    DOI is the unique identifier. If a paper with the same DOI exists,
    update its fields and add new query/source links.
    """
```

### Snowball deduplication logic in `snowball.py`
```python
def find_or_create_paper(conn, ref_dict, parent_paper_id, depth):
    """Find existing paper by DOI or title, or create new one.
    
    Links paper to source 'snowball' and creates snowball_edge.
    Returns paper_id.
    """
```

---

## 6. Documentation Structure (`docs/`)

### Philosophy
Documentation is a first-class artifact. Every design decision, architectural tradeoff, and operational procedure is documented with rationale. The `docs/` folder contains system-level documentation; `README.md` is the entry point.

### File inventory

| File | Purpose | Audience |
|---|---|---|
| `docs/design_decisions.md` | Full design rationale, tradeoffs, alternatives considered | Developers, researchers |
| `docs/schema.md` | ER diagram, table-by-table column docs, constraints | Developers |
| `docs/snowballing.md` | Snowball search methodology, algorithm, API usage, rate limits | Developers |
| `docs/relevance.md` | Relevance engine methodology, keyword sets, tuning | Researchers |
| `docs/llm_screening.md` | LLM screening setup, prompts, evaluation | Researchers |
| `docs/pipeline.md` | End-to-end data flow, module responsibilities | Developers |
| `docs/migration.md` | How to migrate from old `results.db` to new schema | Operators |
| `docs/api_keys.md` | Where to put API keys, environment variables | Operators |
| `README.md` | Project overview, quickstart, CLI reference | Everyone |

### Design decisions doc structure (`docs/design_decisions.md`)
For each decision, document:
- **Context:** What problem are we solving?
- **Options:** Alternatives considered (with pros/cons)
- **Decision:** What we chose and why
- **Consequences:** Tradeoffs, future implications

### Inline documentation standards
- All modules have module-level docstrings explaining purpose and public API.
- All public functions have Google-style docstrings (`Args`, `Returns`, `Raises`).
- Complex logic has inline comments explaining *why*, not *what*.
- Database schema SQL files have table-level and column-level comments.

---

## 7. Terminal UI (TUI) — `cli/tui.py`

**Framework:** [Textual](https://textual.textualize.io/) (modern, async, rich widgets)

### Screens / Views
| Screen | Purpose |
|---|---|
| **Dashboard** | Summary stats: total papers, relevant count, by source, by query, snowball stats, top keywords |
| **Paper List** | Browse papers with filters (relevance, source, query, year range, snowball depth). Sortable columns. |
| **Paper Detail** | Full paper view: title, authors, abstract, DOI, keywords, relevance score, linked queries/sources, snowball ancestors/descendants |
| **Relevance Review** | Manually review and override `is_relevant` for papers (human decision) |
| **LLM Screening** | Trigger LLM screening runs, view past runs and decisions |
| **Snowball** | Run snowball search, view expansion graph, browse discovered papers |
| **Import** | Import BibTeX/CSV with progress bars |

### Key features
- **Search:** Full-text search across titles and abstracts (FTS5 or simple LIKE).
- **Export:** Export filtered results to CSV/BibTeX.
- **Snowball visualization:** Tree view showing seed papers → discovered papers.
- **Keyboard-driven:** Vim-style navigation, shortcuts for common actions.

---

## 8. File Structure

```
PUF_RA/
├── cititations_data/                          # existing citation data (keep)
│   ├── query1/
│   │   ├── acm_citation1.bib
│   │   └── ieee_citation1.bib
│   └── query2/
│       ├── acm_citation.bib
│       └── ieee_citation.bib
├── database_struct.sql                        # new schema (replaces old)
├── how_to_get_csvs.md                         # existing docs (keep)
├── run_llm_screening.py                       # → deprecated, replaced by cli/screen.py
├── run_csv_to_db.py                           # → deprecated, replaced by cli/import.py
├── run_bibtex_to_csv.py                       # → deprecated, replaced by cli/import.py
├── src/
│   ├── __init__.py
│   ├── db.py                                  # DB connection, helpers
│   ├── db_schema.py                           # CREATE TABLE statements, migrations
│   ├── bibtex_parser.py                       # BibTeX parser
│   ├── bibtex_importer.py                     # BibTeX → SQLite (direct)
│   ├── csv_importer.py                        # CSV → SQLite
│   ├── relevance.py                           # Keyword + BM25 relevance engine
│   ├── screening.py                           # LLM screening logic
│   └── snowball.py                            # Snowball/backward search via APIs
├── cli/
│   ├── __init__.py
│   ├── import.py                              # `puf import bibtex ...` / `puf import csv ...`
│   ├── relevance.py                           # `puf relevance evaluate [--threshold X]`
│   ├── screen.py                              # `puf screen [--model X]`
│   ├── snowball.py                            # `puf snowball run [--depth N]`
│   └── tui.py                                 # `puf tui` — Textual TUI
├── tests/
│   ├── __init__.py
│   ├── test_bibtex_parser.py
│   ├── test_relevance.py
│   ├── test_import.py
│   ├── test_snowball.py
│   └── test_schema.py
├── docs/
│   ├── design_decisions.md                    # full design rationale with alternatives
│   ├── schema.md                              # ER diagram, table-by-table docs
│   ├── snowballing.md                         # snowball search methodology, algorithm & API docs
│   ├── relevance.md                           # relevance engine methodology
│   ├── llm_screening.md                       # LLM screening setup & evaluation
│   ├── pipeline.md                            # end-to-end data flow
│   └── migration.md                           # migration guide from old schema
├── requirements.txt                           # dependencies
├── pyproject.toml                             # package config, entry points
└── README.md                                  # usage guide
```

### Entry points (via `pyproject.toml` console_scripts)
```
puf import bibtex <files...> --query <id> --source <name>
puf import csv <file> --format <acm|ieee> --query <id> --source <name>
puf relevance evaluate [--threshold 0.15]
puf relevance stats
puf screen [--model <name>] [--query-ids <ids>]
puf snowball run [--depth 1] [--max-refs 20]
puf snowball stats
puf snowball graph
puf tui
```

---

## 9. Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| **Junction tables over boolean columns** | Normalized, scalable, supports arbitrary number of queries/sources without schema changes. Boolean columns require migrations for every new source/query. |
| **DOI as UNIQUE constraint on `papers`** | DOI is the canonical persistent identifier for scholarly papers. Enables dedup across queries and sources. |
| **Relevance via keyword + BM25 hybrid** | Keyword matching is precise for domain-specific terms; BM25 captures semantic similarity across the corpus. Both are deterministic and reproducible (unlike LLM scoring). |
| **Direct BibTeX → DB (no CSV intermediate)** | Eliminates a fragile step, reduces disk I/O, and allows capturing source/query metadata atomically. |
| **Textual TUI** | Modern Python TUI framework with async support, rich widgets (tables, trees, forms). Better UX than argparse-only for browsing. |
| **Modular `src/` package** | Single responsibility per module. Testable in isolation. Importable from notebooks/scripts. |
| **Backward compatibility via wrappers** | Old scripts (`run_csv_to_db.py`, etc.) can be kept as thin wrappers calling the new modules, so existing workflows aren't broken during transition. |
| **Snowball via Semantic Scholar API** | Free tier sufficient for research scale. Returns structured reference metadata with DOIs. No auth required. Fallback to Crossref for DOI-only lookups. |
| **`snowball_edges` table** | Tracks discovery provenance. Enables "show me all papers discovered from this seed paper" queries. Prevents cycles via (child, parent) PK. |
| **Comprehensive `docs/` folder** | Design decisions, schema, and operational docs are first-class. Enables onboarding, reproducibility, and auditability for a scientific paper pipeline. |

---

## 10. Implementation Order

1. **Schema + migration** — Write `database_struct.sql`, `src/db_schema.py`, migration script for existing DBs.
2. **Refactor parser** — Extract `src/bibtex_parser.py` from existing code.
3. **DB layer** — `src/db.py` (connection helpers, upsert logic).
4. **Importers** — `src/bibtex_importer.py`, `src/csv_importer.py`.
5. **Relevance engine** — `src/relevance.py` + topic keyword configuration.
6. **Snowball module** — `src/snowball.py` (Semantic Scholar client, dedup logic, edge tracking).
7. **Screening module** — `src/screening.py` (refactored from `run_llm_screening.py`).
8. **CLI entry points** — `cli/import.py`, `cli/relevance.py`, `cli/screen.py`, `cli/snowball.py`.
9. **TUI** — `cli/tui.py` (dashboard, paper list, detail view, snowball view).
10. **Tests** — Unit tests for each module.
11. **Documentation** — `docs/*.md`, `README.md`.
12. **Deprecate old scripts** — Add deprecation warnings, update references.

---

## 11. Validation Plan

- [ ] `pytest tests/` — all unit tests pass.
- [ ] Import all 4 .bib files into a fresh DB → verify paper count, dedup by DOI, junction table populated.
- [ ] Run relevance evaluation → verify scores are non-NULL for papers with abstracts, NULL for empty abstracts.
- [ ] Run snowball search on relevant papers → verify new papers are discovered, deduped, linked via `snowball_edges`.
- [ ] TUI launches and can display paper list, filter by relevance, show snowball graph.
- [ ] LLM screening still works with the new schema (refactored `screening.py`).
- [ ] Migration script tested on a copy of an existing `results.db`.
- [ ] Documentation build check: all `docs/*.md` files render correctly.

---

## 12. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Semantic Scholar API rate limits during snowball | Medium | Medium | Implement exponential backoff, cache results, allow manual pacing |
| Rate limits prevent full snowball expansion | Medium | Low | Process papers in batches, allow `--delay` CLI flag, support Crossref fallback |
| Many references lack DOIs → dedup by title | High | Medium | Implement fuzzy title matching (Levenshtein or TF-IDF similarity) |
| Existing `results.db` migration loses data | Low | High | Write migration script with dry-run mode, backup requirement, verbose logging |
| BM25 relevance is too strict/lenient for PUF domain | Medium | Medium | Make threshold configurable, allow manual override in TUI, store multiple eval runs |
| TUI becomes complex and buggy | Medium | Medium | Build incrementally (dashboard first, then paper list, then snowball view) |

---

## 13. Key Design Decisions (Resolved)

| Decision | Resolution |
|---|---|
| **Database schema approach** | **Approach A: Junction tables** — `paper_queries`, `paper_sources`, `snowball_edges`. Normalized, scalable, no schema changes needed for new queries/sources. |
| **Docs folder** | **Full `docs/` folder** with system docs, design decisions, schema, migration, and operational guides. |
| **Snowball search** | **Semantic Scholar API primary, Crossref fallback**. Track via `snowball_edges` table. Configurable depth and batch size. |
