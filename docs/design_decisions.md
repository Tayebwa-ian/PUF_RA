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
- The decision threshold is **auto-derived from ground truth** (`derive_threshold`, max-F1 / Youden vs `ground_truth_consensus`); the configurable `0.15` is only the **fallback** used when no consensus labels exist. An explicit `--threshold` always overrides the derived value.
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
- Old scripts (`run_bibtex_to_csv.py`, `run_csv_to_db.py`) were **deleted in TASK-004**; import now flows through `cli/import` (→ `src/bibtex_importer.py` / `src/csv_importer.py`) and citation ingestion through `scripts/ingest_citations.py`.
- New codebase is simpler and easier to maintain.

---

## Decision 5: Snowball via Semantic Scholar as a Standalone, First-Class Resolve Source

**Context:** Need to expand the literature review by following reference chains (backward + forward) and resolving each discovered reference to a `papers` row with full provenance.

**Options Considered:**
- **A. Semantic Scholar API (recommended, standalone):** Free tier, structured reference metadata, DOIs available, no API key. It is a **first-class, standalone** resolve source (alias `s2`) — it resolves references on its own, with **no mandatory Crossref/OpenAlex fallback**.
- **B. Crossref API:** Reliable DOI lookup, polite pool, but limited reference metadata; the natural *alternate* of OpenAlex.
- **C. OpenAlex API:** Rich metadata + OA-PDF links, but enforces a daily polite-pool request budget that can be exhausted; the natural *alternate* of Crossref.
- **D. Manual PDF parsing:** Too fragile, no scale.
- **E. arXiv API:** Limited to preprints, not suitable for hardware security.

**Decision:** Semantic Scholar is a **standalone first-class** resolve source (no mandatory Crossref/OpenAlex fallback). Crossref and OpenAlex are *alternates of each other* (a Crossref miss retries OpenAlex and vice-versa). All API traffic is paced by a shared `RateLimiter` (`--delay`, default `1.0s`), and `backfill_abstracts` is resilient (commit-per-paper, guards `RateLimitError`). Single-source resolution is available via `--no-alternate` (see Decision 8).

**Rationale:**
- S2 returns structured reference data with DOIs, authors, abstracts, and requires no API key.
- Standing S2 up as a standalone source removes a hard dependency on any single fallback and lets a run complete even when the alternate source is unavailable.
- Crossref <-> OpenAlex remain a peer alternate pair so DOI-based resolution still has redundancy when desired.

**Consequences:**
- Rate limits (S2 ~100 req/5min) require explicit pacing via `--delay` (default `1.0s`); pacing now applies to **all** API calls through the shared `RateLimiter`, with adaptive backoff on failure and `Retry-After` honouring.
- Many references may lack DOIs; title-based dedup is best-effort.
- Resolution is idempotent and resumable; `backfill_abstracts` commits after each paper and never aborts the whole batch on a throttle.

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

## Decision 8: Single-Source Resolution (`--no-alternate`)

**Context:** Crossref and OpenAlex are implemented as *alternates* of each other — a Crossref miss retries OpenAlex (and vice-versa). Semantic Scholar is already standalone. We needed a way to force resolution on a **single** source with no cross-source retry.

**Options Considered:**
- **A. Always retry the alternate (`--no-alternate` absent):** Maximises recovery; uses OpenAlex as the Crossref fallback and vice-versa.
- **B. Single-source mode (`--no-alternate`, recommended for constrained runs):** Never fall back to the alternate; an unresolvable DOI is marked `fetch_error` on the chosen source alone.

**Decision:** Support both. The default keeps the Crossref<->OpenAlex alternate retry; `--no-alternate` disables all cross-source retries for any `--source`.

**Rationale / Why it exists:**
- **OpenAlex budget exhaustion.** OpenAlex's polite pool enforces a daily request budget. Once exhausted, its fallback becomes unavailable and a Crossref-led run can stall. `--no-alternate --source semantic_scholar` (or `--source crossref --no-alternate`) keeps resolution moving on a single source without depending on OpenAlex.
- **Semantic Scholar preference.** When S2 is the preferred/primary source there is no OpenAlex fallback by design; `--no-alternate` makes the single-source contract explicit and also lets Crossref/OpenAlex be used in isolation when only that source is trustworthy or allowed.

**Consequences:**
- With `--no-alternate`, only the chosen source is attempted; a failed DOI is recorded as `fetch_error` rather than silently retried cross-source.
- The run still stops gracefully and stays resumable (idempotent, `verify_retrieval` backstop); the next run can drop `--no-alternate` to fill remaining gaps via the alternate.
