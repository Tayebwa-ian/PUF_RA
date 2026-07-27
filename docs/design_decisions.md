# Design Decisions — PUF Research Pipeline

This document records key architectural and design decisions with rationale, alternatives considered, and consequences.

## Decision 1: Junction Tables Over Boolean Columns

**Context:** The original schema used a single `query_id` foreign key on `papers`. The user requested adding boolean columns (`from_ACM`, `from_ieee`, `query1`, `query2`) to track sources and queries.

**Options Considered:**
- **A. Junction tables (recommended):** `paper_queries` and `paper_sources` as many-to-many tables.
- **B. Boolean columns:** Add `from_ACM`, `from_ieee`, `query1`, `query2` to `papers`.

**Decision:** Junction tables.

**Rationale:**
- Normalized: no redundant data.
- Scalable: adding query3 or source "Scopus" requires no schema changes.
- Queryable: easy to find "papers from ACM AND query1".
- Boolean columns require ALTER TABLE for every new source/query.

**Consequences:**
- Queries require JOINs (negligible for <10K papers).
- More tables to manage, but cleaner long-term.

---

## Decision 2: DOI as UNIQUE Constraint

**Context:** Same paper may be imported from multiple queries or sources.

**Options Considered:**
- **A. DOI UNIQUE constraint:** `doi TEXT UNIQUE` on `papers`.
- **B. Composite unique on (title, year):** Catch duplicates without DOI.
- **C. No dedup:** Allow duplicates.

**Decision:** DOI UNIQUE constraint, with best-effort title dedup as fallback.

**Rationale:**
- DOI is the canonical persistent identifier.
- Simple and efficient.
- Title-only dedup is fragile (typos, abbreviations).

**Consequences:**
- Papers without DOI may duplicate on re-import.
- Import logic must handle IntegrityError gracefully.

---

## Decision 3: Hybrid Keyword + BM25 Relevance Scoring

**Context:** Need an empirical, reproducible relevance score for the topic "Physical attacks on PUFs".

**Options Considered:**
- **A. Keyword matching only:** Simple, precise, but misses synonyms.
- **B. BM25 only:** Captures term frequency, but may miss domain-specific terms.
- **C. Hybrid keyword + BM25 (recommended):** Combine precision of keywords with BM25's corpus-aware scoring.
- **D. LLM-based scoring:** Flexible but non-deterministic, expensive, requires API.

**Decision:** Hybrid keyword + BM25.

**Rationale:**
- Deterministic and reproducible (critical for scientific SLR).
- Keyword weights encode domain expertise.
- BM25 normalises across corpus size.
- LLM scoring can be added later as a third method.

**Consequences:**
- Threshold tuning required (default 0.15).
- Topic keyword set must be curated and maintained.

---

## Decision 4: Direct BibTeX → DB (No CSV Intermediate)

**Context:** Original pipeline used BibTeX → CSV → SQLite.

**Options Considered:**
- **A. Direct BibTeX → DB:** Eliminates CSV step.
- **B. Keep CSV intermediate:** More tooling, more error-prone.
- **C. Both:** Support both paths.

**Decision:** Direct BibTeX → DB as primary; keep CSV importer for existing workflows.

**Rationale:**
- Eliminates a fragile step (CSV encoding, delimiter issues).
- Allows atomic capture of source/query metadata.
- CSV importer retained for backward compatibility.

**Consequences:**
- Old scripts (`run_bibtex_to_csv.py`, `run_csv_to_db.py`) deprecated.
- New codebase is simpler and easier to maintain.

---

## Decision 5: Snowball via Semantic Scholar API

**Context:** Need to expand literature review by following reference chains.

**Options Considered:**
- **A. Semantic Scholar API (recommended):** Free tier, structured reference metadata, DOIs available.
- **B. Crossref API:** Reliable DOI lookup, but limited reference metadata.
- **C. Manual PDF parsing:** Too fragile, no scale.
- **D. arXiv API:** Limited to preprints, not suitable for hardware security.

**Decision:** Semantic Scholar API primary, Crossref fallback.

**Rationale:**
- Returns structured reference data with DOIs, authors, abstracts.
- Free tier sufficient for research scale.
- No API key required.

**Consequences:**
- Rate limits (~100 req/5min) require pacing.
- Many references may lack DOIs; title-based dedup is best-effort.

---

## Decision 6: Textual TUI

**Context:** Need a UI to interact with the database.

**Options Considered:**
- **A. Textual (recommended):** Modern Python TUI, async, rich widgets.
- **B. Rich + questionary:** Simpler but less structured.
- **C. Web UI (Flask/FastAPI):** Overkill for local research tool.
- **D. Jupyter notebook:** Good for analysis, poor for browsing.

**Decision:** Textual.

**Rationale:**
- Rich widgets (tables, trees, forms) ideal for paper browsing.
- Keyboard-driven, terminal-native.
- Async support for API calls.

**Consequences:**
- Adds dependency on Textual.
- TUI is the most complex module; requires incremental testing.

---

## Decision 7: Comprehensive `docs/` Folder

**Context:** User requested dedicated documentation folder.

**Decision:** Full `docs/` folder with system docs, design decisions, schema, migration, and operational guides.

**Rationale:**
- Scientific pipelines require reproducibility and auditability.
- Design decisions documented with rationale enable future maintainers.
- Onboarding new researchers is faster with structured docs.

**Consequences:**
- Documentation is a first-class artifact, not an afterthought.
- Requires maintenance as code evolves.
