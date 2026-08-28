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

**The original flow is preserved.** The initial design — *collect a full list of references → validate them → extract titles and abstracts* — is implemented unchanged, just made modular and resumable as the **two-phase pipeline** in `src/reference_store.py`: **harvest** (`harvest_references`, collect and store the complete reference list per seed, direction-tagged, nothing dropped) → **resolve/validate** (`resolve_reference_lists`, validate each stored reference and extract DOI / title / authors / year) → **backfill** (`backfill_abstracts`, extract the abstracts that the resolve metadata lacked). Each stage is separately invocable (`--harvest-only`, `--resolve-only`, `puf snowball backfill-abstracts`) and idempotent.

**Both directions are implemented, with different source support.** Backward search runs on Crossref (`message.reference`), OpenAlex (`referenced_works`) **and** Semantic Scholar (`s2_get_references`); forward search is implemented via OpenAlex `filter=cites:{openalex_id}` **only** — Crossref and Semantic Scholar forward search are explicitly unsupported and skipped — so `--direction forward` / `--direction both` must be combined with `--source openalex`.

**Options Considered:**
- **A. Semantic Scholar API (recommended, standalone):** Free tier, structured reference metadata, DOIs available, no API key. It is a **first-class, standalone** resolve source (alias `s2`) — it resolves references on its own, with **no mandatory Crossref/OpenAlex *not-found* fallback** (by default it still falls back to OpenAlex/Crossref on a *rate-limit*).
- **B. Crossref API:** Reliable DOI lookup, polite pool, but limited reference metadata; the natural *alternate* of OpenAlex.
- **C. OpenAlex API:** Rich metadata + OA-PDF links, but enforces a daily polite-pool request budget that can be exhausted; the natural *alternate* of Crossref.
- **D. Manual PDF parsing:** Too fragile, no scale.
- **E. arXiv API:** Limited to preprints, not suitable for hardware security.

**Decision:** Semantic Scholar is a **standalone first-class** resolve source for *not-found* DOIs (no mandatory Crossref/OpenAlex not-found fallback) but, like Crossref/OpenAlex, **falls back to the next platform when rate-limited** (HTTP 429) by default. Crossref and OpenAlex are *alternates of each other* (a Crossref miss retries OpenAlex and vice-versa). All API traffic is paced by a shared `RateLimiter` (`--delay`, default `1.0s`), and `backfill_abstracts` is resilient (commit-per-paper, guards `RateLimitError`). Single-source resolution is available via `--no-alternate` (see Decisions 8 and 9).

**Rationale:**
- S2 returns structured reference data with DOIs, authors, abstracts, and requires no API key.
- Standing S2 up as a standalone source for *not-found* DOIs removes a hard dependency on any single not-found fallback and lets a run complete even when a single source is missing a record; a rate-limited source simply spills onto the next platform (Decision 9).
- Crossref <-> OpenAlex remain a peer alternate pair so DOI-based resolution still has redundancy when desired.

**Consequences:**
- Rate limits (S2 ~100 req/5min) require explicit pacing via `--delay` (default `1.0s`); pacing now applies to **all** API calls through the shared `RateLimiter`, with adaptive backoff on failure and `Retry-After` honouring.
- Many references may lack DOIs; title-based dedup is best-effort.
- Resolution is idempotent and resumable; `backfill_abstracts` commits after each paper and never aborts the whole batch on a throttle.
- The stages are decoupled, so a rate-limited or budget-bounded run only loses progress on the *current* stage: the harvested inventory stays in `reference_lists` and the next `--resolve-only` / `backfill-abstracts` run picks up exactly where the previous one stopped.
- A forward pass depends on OpenAlex; when OpenAlex is throttled or its daily budget is exhausted, forward *harvesting* has no substitute source (backward harvesting and the whole resolve stage do).

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

**Context:** Crossref and OpenAlex are implemented as *alternates* of each other — a Crossref miss retries OpenAlex (and vice-versa). Semantic Scholar is standalone for *not-found* DOIs but gains a rate-limit fallback (Decision 9). We needed a way to force resolution on a **single** source with no cross-source retry at all (including on rate-limit).

**Options Considered:**
- **A. Always retry the alternate (`--no-alternate` absent):** Maximises recovery; uses OpenAlex as the Crossref fallback and vice-versa.
- **B. Single-source mode (`--no-alternate`, recommended for constrained runs):** Never fall back to the alternate; an unresolvable DOI is marked `fetch_error` on the chosen source alone.

**Decision:** Support both. The default keeps the Crossref<->OpenAlex alternate retry; `--no-alternate` disables all cross-source retries for any `--source`.

**Rationale / Why it exists:**
- **OpenAlex budget exhaustion.** OpenAlex's polite pool enforces a daily request budget. Once exhausted, its fallback becomes unavailable and a Crossref-led run can stall. `--no-alternate --source semantic_scholar` (or `--source crossref --no-alternate`) keeps resolution moving on a single source without depending on OpenAlex.
- **Semantic Scholar preference.** When S2 is the preferred/primary source there is no OpenAlex *not-found* fallback by design (a rate-limited S2 still falls back to OpenAlex/Crossref by default); `--no-alternate` makes the single-source contract explicit and also disables the rate-limit fallback, letting Crossref/OpenAlex be used in isolation when only that source is trustworthy or allowed.

**Consequences:**
- With `--no-alternate`, only the chosen source is attempted; a failed DOI is recorded as `fetch_error` rather than silently retried cross-source.
- The run still stops gracefully and stays resumable (idempotent, `verify_retrieval` backstop); the next run can drop `--no-alternate` to fill remaining gaps via the alternate.


---

## Decision 9: Cross-source Fallback on Rate-Limit (HTTP 429)

**Context:** A resolve or abstract-backfill run used to *abort gracefully* the moment any
source raised `RateLimitError` (HTTP 429) — i.e. one throttled platform stopped the whole
batch, wasting the remaining API budget on the other platforms. We wanted to maximise API
utilisation: when one platform is rate-limited, try another and keep going.

**Options Considered:**
- **A. Abort the run on the first 429 (legacy):** Simple, but leaves other healthy platforms unused and forces a manual re-run.
- **B. Cross-source fallback on 429 (recommended):** Maintain a per-source fallback chain and skip any throttled platform, continuing the batch until every candidate is throttled or the budget is exhausted.
- **C. Retry the same platform forever:** Contradicts the `RateLimiter` give-up contract and risks bans.

**Decision:** By default, resolution and abstract backfill switch to the next platform in
`_SOURCE_RATELIMIT_CHAIN` whenever the current one returns HTTP 429, and the batch
**continues** (no graceful abort):

- `semantic_scholar` -> `openalex` -> `crossref` -> `zotero`
- `openalex` -> `crossref` -> `zotero` -> `semantic_scholar`
- `crossref` -> `openalex` -> `zotero` -> `semantic_scholar`

Source-aware pacing applies per source (`DEFAULT_SOURCE_INTERVALS`): Crossref / OpenAlex
polite pools run at ~0.05 s, Semantic Scholar at ~0.6 s (~100 req / 5 min), and Zotero is
**instant** (a local read, not a rate-limited API). The **OpenAlex batched multi-DOI
pre-pass** (`_openalex_batch_by_dois`) is active for the **OpenAlex resolve** source and for
**Crossref/OpenAlex backfill** (Crossref *resolve* stays per-DOI); a **batched Zotero
pre-pass** resolves DOIs from the local library before the slow S2 endpoint. Both pre-passes
are additive and skipped under `--no-batch`, which opts out when OpenAlex itself is
unavailable / budget-blocked.

`--no-alternate` disables **all** cross-source fallback (strict single-source): a rate-limited
source leaves its DOIs as `fetch_error` and the batch still continues (it never raises/aborts
the whole run). A 404 / not-found does **NOT** trigger a cross-source fallback, except the
existing Crossref<->OpenAlex 404-alternate (`_ALTERNATE_SOURCE`); Semantic Scholar stays
standalone for not-found DOIs (a miss there returns `None` without contacting other platforms).
If every candidate in the chain is throttled, the helper returns an empty result and the
affected DOIs are recorded as `fetch_error` — the run finishes normally.

**Rationale / Why it exists:**
- **Maximise API utilisation.** A 429 on one platform should not block work the other platforms can still do within budget.
- **Resilience.** A single throttled source degrades gracefully to a smaller result set instead of aborting the run.
- **Strict mode preserved.** `--no-alternate` keeps a fully deterministic, single-source contract for constrained/reproducible runs (Decision 8).

**Consequences:**
- Title-search resolution (`_resolve_by_title`) is likewise rate-limit resilient: a 429 on OpenAlex retries Crossref and vice-versa; a single `primary` source yields no match rather than aborting.
- `fetch_error` now also means "all attempted sources were rate-limited", not only "not found on the chosen source".
- The legacy `except RateLimitError` abort remains as a defensive guard but is no longer reached on the DOI-resolution / backfill paths (it still triggers if an un-guarded code path raises).
- **Where `aborted: 1` is still correct.** The seed reference-list fetch in `harvest_references` (stage 1 of the two-phase path) keeps the pre-TASK-010 graceful stop, because it queries a *single* source with no cross-source substitute: it stops harvesting, commits the inventory gathered so far and stays resumable. The **resolve** and **backfill** stages do **not** set `aborted` on a 429; they switch platform and continue (the resolve/backfill chains now end in a Zotero local-library lookup, Decision 10). Documentation must scope any "stops gracefully / `aborted: 1`" statement to the harvest seed-fetch case.


## Decision 10 — Single snowball implementation + Zotero rate-limit fallback

**Decision:** Remove the legacy `run_snowball` path and the standalone
`scripts/run_snowball.py` runner (TASK-011). `src/snowball.py` becomes a
shared-helper module only; `src/reference_store.py` is the single implementation
of the snowball pipeline (`harvest_references`, `resolve_reference_lists`,
`backfill_abstracts`). Zotero is added as a **last-resort** resolve/backfill
source: `_SOURCE_RATELIMIT_CHAIN` places `"zotero"` immediately BEFORE
  `"semantic_scholar"` (Zotero is the fast local pre-pass; S2 is the slow last resort), and
`src.zotero_sync.lookup_doi_in_zotero` / `fetch_abstract_via_zotero` read the
user's *local* Zotero library (`pyzotero`). Because that read is local it is
**immune to external API rate limits** for papers already cached in Zotero, and
it **never raises** — an unconfigured Zotero simply yields no match and the chain
continues.

**Rationale / Why it exists:**
- **Dead-code removal.** Two snowball implementations (`run_snowball` + the
  reference-store pipeline) duplicated logic and confused which path ran.
  Consolidating on `reference_store` removes the split.
- **Rate-limit resilience, continued.** When Crossref, OpenAlex *and* Semantic
  Scholar are all throttled, a paper already saved in the operator's Zotero
  library can still be resolved/abstracted instead of becoming `fetch_error`.
- **Safety.** The Zotero branch cannot escape a `RateLimitError`; it degrades to
  "no match" exactly like an empty external result.

**Consequences:**
- `--no-alternate` disables *all* cross-source fallback, including the Zotero
  last resort, restoring a strictly single-source contract.
- `puf snowball` is the only supported entry point; `scripts.run_snowball` no
  longer exists. `auto_relevance` (re-running relevance evaluation when new
  papers are inserted) is ported into the new path; LLM `auto_screen` remains out
  of scope.
