# Snowballing — Methodology & Results

Goal: expand the physical-attack PUF corpus via the citation graph in both
directions, deduplicating every discovered paper against the existing corpus,
recording full provenance, and giving every harvested reference an explicit,
auditable outcome (resolved / unrecoverable / pending) so the literature screen
is reproducible and **nothing is lost silently**.

This page explains **how** the snowballing was performed for the PUF
physical-attacks SoK study and reports the **actual results**, including the
assured-retrieval accounting that guarantees no reference is dropped silently.
See [`docs/database_migration.md`](database_migration.md) for the schema
evolution that underpins it.

## Two-phase design (harvest inventory → resolve)

Implemented in `src/reference_store.py`. The redesign deliberately separates
*inventory* from *resolution*:

1. **Phase 1 — harvest (inventory).** `harvest_references(conn, seed_paper_ids,
   direction, source, ...)` fetches, for each seed, the **complete** reference
   list from the chosen source and stores **every** reference — even ones with no
   DOI, only an unstructured string, or not yet in our DB — in `reference_lists`.
   This makes the inventory complete and lets a budget-limited run stop at any
   point and resume later. The exact source calls are:
   - backward + crossref: `GET /works/{DOI}` → `message.reference[]`
   - backward + openalex: `GET /works?filter=doi:{DOI}` → `referenced_works` IDs, batch-resolved
   - forward + openalex: `GET /works?filter=cites:{openalex_id}` (citing works)
   - backward + s2: `s2_get_references`
   A per-parent expression-unique index prevents duplicate inventory rows.
   Already-known references are resolved locally (creating `snowball_edges`), and
   the remaining DOI-bearing references are batch-resolved (Crossref per-DOI
   polite lookups, or OpenAlex `filter=doi:...|...`), inserting `papers`, linking
   `snowball_edges`, and capturing Open-Access PDF links into `papers.pdf_url`.
2. **Phase 2 — resolve (bulk).** `resolve_reference_lists(conn, source, ...)`
   (or the `resolve=True` tail of `harvest_references`) walks **every** unresolved
   `reference_lists` row that carries a DOI and bulk-resolves it. This implements
   "store the list, then resolve later / bulk-download". The chosen *source* may
   be `crossref`, `openalex`, or `semantic_scholar` — a real, standalone resolve
   source (alias `s2`) that resolves via Semantic Scholar with **no** OpenAlex
   fallback.

Because resolution is idempotent (guarded by `resolved_paper_id IS NULL`), a
`--resolve-only` run can be repeated indefinitely; each pass picks up where the
last stopped.

## Local-first resolution

Before any external API is contacted, every seed and every reference is checked
against **OUR OWN database** via `local_find_paper` (to `find_existing_paper_id`):
DOI first, then normalised title. A reference already present is linked
immediately — zero network calls — and only genuinely-unknown references are
resolved externally. Local-first also prevents re-inserting a paper that was
found by `query1`, `query2` **and** snowballing: such a paper stays a single row
and merely accumulates `paper_sources` links.

## Abstract capture for resolved papers

Snowball-resolved references now store the paper **abstract** (not just DOI /
title / authors), pulled from the source work metadata during resolution:

* **Crossref** — `message.abstract` is JATS XML; tags are stripped to plain text
  (``_strip_jats``).
* **OpenAlex** — `abstract_inverted_index` is reconstructed into plain text
  (``_openalex_inverted_index_to_text``).
* **Semantic Scholar** — `abstract` is returned as plain text.

The abstract is written by `_find_or_create_ref_paper`: for a NEW paper via
`_find_or_create_paper`, and for an already-known paper (local-found shortcut) by
`_update_paper_if_needed` (`src/snowball.py`), which fills only the fields the
existing row is missing and never overwrites a stored abstract. Every
harvest/resolve call therefore leaves the corpus with the abstract needed for
relevance screening whenever the source metadata carried one; a resolved paper
whose metadata has no abstract at all is left to `puf snowball
backfill-abstracts` (below).

Papers harvested *before* this change (or any paper whose `abstract` is empty but
carries a DOI) can be filled in afterwards without re-running the whole snowball:

```bash
puf snowball backfill-abstracts --db results.db --source crossref --delay 1.0
puf snowball backfill-abstracts --db results.db --source semantic_scholar --delay 1.0
puf snowball backfill-abstracts --db results.db --source crossref --no-alternate --delay 1.0
```

`backfill_abstracts(conn, source="crossref", mailto=None, max_api_calls=None,
limiter=None, no_alternate=False)` SELECTs every `papers` row with a NULL/empty
`abstract` and a non-null `DOI`, fetches the abstract by DOI and UPDATEs it. The
source may be `crossref` (retries OpenAlex on a miss unless `--no-alternate`),
`openalex`, or `semantic_scholar`/`s2` (standalone — no OpenAlex fallback, even
without `--no-alternate`). It is idempotent and resumable (only empty abstracts
are touched), honours `--max-api-calls` / `--delay`, and is resilient: each paper
is committed individually and a throttled fetch does not abort the remaining
batch (the run stops gracefully and returns what was done).

## Backward + Forward (TARCiS / PRISMA-S)

* **Backward** (who a seed *cites*): Crossref `GET /works/{DOI} -> message.reference[]`,
  or OpenAlex `filter=doi:{DOI} -> referenced_works`.
* **Forward** (who *cited* a seed): OpenAlex `filter=cites:{openalex_id}`.
* `--direction {backward,forward,both}` runs one or both; `--both` does backward
  then forward. Result rows are tagged `direction` in `reference_lists`.

Seeding precedence for a run is `seed_paper_ids` (explicit ids) >
`seed_query_ids` (resolved through the `paper_queries` junction, the v2
provenance table) > the whole corpus (`SELECT id FROM papers`). Seeds that
already have outgoing `snowball_edges` are skipped (`skip_expanded=True`) so a
budget-limited run resumes where the previous one stopped; use `--reexpand` to
force re-expansion.

The run is logged to `snowball_runs` in a TARCiS-style row
`(direction, source, seed_count, references_harvested, new_papers, edges,
api_calls, started_at, finished_at, note)`, and the screening follows the
PRISMA-S (systematic snowballing) spirit: transparent, auditable, resumable.

## Batch + smart rate limiting

All external HTTP goes through the shared `src.rate_limiter.RateLimiter` (used
by `src.snowball._get_json` on the legacy path and by the reference-store
resolver):

| Mechanism | Behaviour |
|-----------|-----------|
| Pacing | `wait_before_call()` keeps successive requests at least `min_interval` seconds apart (`--delay`). |
| Adaptive pacing | While consecutive failures accumulate, the interval widens by `backoff_base ** failures` (capped at `max_wait`). |
| Server-directed backoff | On `429`/`5xx`, `Retry-After` is honoured — integer seconds or HTTP date (`email.utils.parsedate_to_datetime`), capped at `max_wait`. |
| Exponential backoff + jitter | Without a header: `min(max_wait, backoff_base ** attempt) + uniform(0, jitter)`. |
| Give up politely | After `max_retries` consecutive failures a `RateLimitError` is raised; the resolver logs it, commits what was found and stops instead of hammering the API. |

Non-retryable HTTP errors (e.g. `404`) are re-raised; the per-paper loop logs
them and continues with the next seed.

* **Budget** — `--max-api-calls` caps the number of HTTP requests; the run stops
  cleanly after committing once the budget is spent. Unprocessed references
  remain `pending` and are picked up by the next run.
* **Graceful stop** — on `RateLimitError` the run stops gracefully (commits what
  was found, keeps the rest resumable) rather than crashing; large-scale
  snowballing is achieved by combining `--delay` with repeated bounded runs,
  which resume automatically.

**OpenAlex budget exhaustion.** OpenAlex's polite pool enforces a daily request
budget that can be exhausted mid-run, after which its fallback becomes
unavailable and resolution can stall. When this happens, pass
`--no-alternate --source semantic_scholar` (or `--source crossref --no-alternate`)
to continue resolving on a single source without depending on OpenAlex; the run
still stops gracefully and stays resumable.

## API usage

### Semantic Scholar (standalone resolve source)

- **Base URL:** `https://api.semanticscholar.org/graph/v1`
- **Search paper:** `GET /paper/search?query={title}&fields=title,authors,year,abstract,externalIds,publicationVenue&limit=1`
- **Get references:** `GET /paper/{paper_id}/references?fields=...&limit={n}` where `paper_id` may be `DOI:10.x/y`
- **Rate limit:** ~100 requests per 5 minutes (free tier); the unauthenticated pool frequently answers `429`
- **Auth:** None required

A DOI is used directly as the Semantic Scholar identifier (`DOI:10.x/y`), which
saves one request; Semantic Scholar resolves DOI-bearing references standalone.
Papers without a DOI have no title search in S2 — they are skipped gracefully
during S2 resolution and remain `pending`/`unresolved_title_failed`. Crossref
uses `GET /works/{DOI}`.

### Crossref (alternate of OpenAlex)

- **Base URL:** `https://api.crossref.org/works`
- **Get work:** `GET /works/{DOI}`
- **References:** `message.reference` array in the response
- **Rate limit:** ~50 requests per second (polite pool)
- **Auth:** None required

Semantic Scholar may not have all papers, especially older or less-cited ones;
in that case the run stops gracefully with `aborted: 1`. Reference metadata
quality varies — Crossref references frequently carry only an unstructured
string (skipped) and few abstracts.

## CLI commands

```bash
# Snowball the whole corpus, bounded to 40 API requests
puf snowball --db results.db --depth 1 --max-refs 15 --source semantic_scholar --max-api-calls 40

# Seed from the papers of queries 3 and 4 (paper_queries junction)
puf snowball run --seed-query-ids 3,4 --depth 1 --max-refs 20

# Seed from explicit papers, no relevance re-run
puf snowball run --seed-paper-ids 12,44,91 --no-auto-relevance

# Show snowball stats
puf snowball stats

# Backfill abstracts for harvested papers missing them (Crossref, OpenAlex retry)
puf snowball backfill-abstracts --db results.db --source crossref --delay 1.0
puf snowball backfill-abstracts --db results.db --source semantic_scholar --delay 1.0
puf snowball backfill-abstracts --db results.db --source crossref --no-alternate --delay 1.0

# Standalone runner with the same options
python -m scripts.run_snowball --db results.db --depth 1 --max-refs 15 --max-api-calls 40
```

`run` is the default subcommand, so the first two forms are equivalent.
`--query-ids 3 4` is still accepted for backwards compatibility. References are
fetched, normalised (DOI, title, authors, year, abstract, venue), deduplicated,
and provenance is accumulated; the relevance pipeline re-runs afterwards only
when new papers were inserted (disable with `--no-auto-relevance`).

Two-phase flags:

* `--harvest-only` stores the inventory without the Phase-2 resolve.
* `--resolve-only` runs only Phase 2 (idempotent, resumable).
* `--direction {backward,forward,both}` selects the citation direction.
* `--source {crossref,openalex,semantic_scholar,s2}` selects the resolution
  source. `crossref` and `openalex` are batch-friendly and retry each other;
  `semantic_scholar` (alias `s2`) resolves via Semantic Scholar **alone** (no
  OpenAlex fallback). Combine with `--no-alternate` (below) to force
  single-source resolution on any source.
* `--with-pdf` reports the count of papers with a captured `pdf_url`.
* `--assured` (default `True`) runs the `verify_retrieval` backstop and a
  final retry of failed rows before export.
* `--export-unresolved <path>` writes the unresolved-reference CSV (default
  `snowball_unresolved.csv`).
* `--no-alternate` — single-source resolution: never fall back to the alternate
  source. Ordinarily a Crossref miss retries OpenAlex (and vice-versa); with this
  flag the primary source alone is used and an unresolvable DOI is marked
  `fetch_error` instead of being retried cross-source. Essential when OpenAlex is
  rate-limited or its daily budget is exhausted (see Rate limits below): combine
  with `--source semantic_scholar` (or `crossref`) to keep resolving without
  OpenAlex.
* `--delay <seconds>` sets the rate-limiter pacing interval; it is honoured on
  **both** the harvest and the resolve paths (the CLI builds a single
  `RateLimiter(min_interval=delay)` and passes it to `harvest_references` and
  `resolve_reference_lists`).

## Statistics

`run_snowball` returns
`{'seeds', 'skipped', 'processed', 'discovered', 'new', 'linked', 'edges',
'api_calls', 'aborted'}`:
`new` counts inserted papers, `linked` counts already-known papers that gained
the `snowball` provenance link, and `aborted` is `1` when the API became
unavailable (graceful stop).

## Zotero + OA-PDF hooks

* `src.zotero_sync.push_dois_to_zotero(conn, paper_ids, ...)` pushes discovered
  DOIs to a Zotero collection for bulk PDF download. `pyzotero` is optional — if
  it is not installed or not configured (`ZOTERO_LIBRARY_ID` / `ZOTERO_API_KEY`),
  the function prints a clear message and returns 0 (never crashes).
* `--with-pdf` reports the count of papers with a captured `pdf_url`. OA PDF
  links are taken from OpenAlex `best_oa_location.pdf_url` and Crossref
  `link[].URL` with `application/pdf`.

## Provenance & dedup (multi-method, one row)

* **Dedup** is by DOI (case-insensitive, resolver prefixes stripped), then by
  normalised title (whitespace-collapsed, lowercased); two papers with
  *different* non-null DOIs are never merged on title alone. If neither matches,
  a new paper row is created and linked to the `snowball` source.
* A paper found by `query1:ACM`, `query2:IEEE` **and** snowballing is **one
  row** with three `paper_sources` links. Missing metadata (abstract, authors,
  year, DOI) is backfilled on the existing row.
* `snowball_edges` records each parent->child traversal at `depth`, skipping
  self- and duplicate `(child, parent)` edges. Depth 1 = direct reference of a
  seed; depth 2 = reference of a reference, where the frontier of each depth is
  the set of children discovered at the previous depth.

## Migration safety

`reference_lists` (and `snowball_runs`, `papers.pdf_url`) were added via the
forward, **additive, idempotent** migration framework in `src/db_schema.py`
(see [`docs/database_migration.md`](database_migration.md)). The assured-retrieval
`status` column is **migration v3** (`add_reference_lists_status`):

```python
@register_migration(3, "add_reference_lists_status")
def _migration_3_reference_lists_status(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reference_lists)")}
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE reference_lists ADD COLUMN status "
            "TEXT NOT NULL DEFAULT 'pending'"
        )
```

The `ALTER` is guarded by `PRAGMA table_info`, so re-applying migrations over an
already-migrated database is a no-op and **no existing reference row is ever
touched**. `ensure_schema()` creates the base schema and applies any pending
migrations automatically.

Note on the CHECK constraint: SQLite cannot add a constraint to an existing
column, so on a **freshly created** database the `status` column carries the
`CHECK (status IN (...))` constraint from `CREATE_REFERENCE_LISTS`, while on a
**migrated (pre-existing)** database the column is added by a plain `ALTER TABLE
... ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'` **without** the CHECK.
The allowed values are therefore identical everywhere, but on migrated databases
they are enforced by the resolution code (`src/reference_store.py`) rather than
by the schema. No table rebuild is performed, so no existing row is touched.

## Assured retrieval (no silent failures)

This is the core guarantee added for the physical-attacks study: **every
reference gets an explicit outcome**, and unrecoverable references are reported,
never ignored.

The `reference_lists.status` values are (schema-enforced on new databases,
code-enforced on migrated ones — see [Migration safety](#migration-safety)):

| status | meaning |
|---|---|
| `pending` | harvested but not yet processed (e.g. budget ran out; resumable) |
| `resolved` | linked to a `papers` row (local, by DOI, by title, or by `verify_retrieval`) |
| `unresolved_no_doi` | no DOI **and** no usable title to recover from |
| `unresolved_title_failed` | had a title, but no confident title match within +/-1 year |
| `fetch_error` | had a DOI but failed on the chosen source (Crossref, OpenAlex, or Semantic Scholar); with `--no-alternate` only the single chosen source is attempted |

Resolution rules applied in **both** `harvest_references` and
`resolve_reference_lists`:

1. **Local-first** — if `local_find_paper` finds it, link + `status='resolved'`.
2. **DOI'd, not local** — try the chosen source. For Crossref or OpenAlex, on a
   404/error retry the **alternate** source (Crossref <-> OpenAlex, OpenAlex via
   `filter=doi:`); Semantic Scholar resolves standalone (no OpenAlex fallback).
   If the attempted source(s) return metadata -> insert + link + `resolved`. If
   all attempted sources fail -> `fetch_error`. With `--no-alternate` only the
   chosen source is attempted (no retry).
3. **DOI-less with a title** — `_resolve_by_title` queries OpenAlex
   `filter=title.search:` then Crossref `query.bibliographic=`, accepting a
   candidate only when the **normalised title is exactly equal AND the year is
   within +/-1** (conservative, avoids false merges). Match -> `resolved`;
   otherwise `unresolved_title_failed`.
4. **DOI-less, no title** -> `unresolved_no_doi`.

### `verify_retrieval` — the backstop

```python
verify_retrieval(conn) -> dict
```

For **every** `reference_lists` row with a `ref_doi` and `status != 'resolved'`,
it checks whether a `papers` row with that DOI now exists (e.g. inserted by a
later harvest). If so it backfills `resolved_paper_id` + `snowball_edges` +
`status='resolved'`. It returns per-status counts **and** the list of still-missing
DOI'd references, so the run can prove completeness.

### `--assured` (default True) and unresolved export

After the normal pass, `assured` mode runs `verify_retrieval` and then
re-attempts the `fetch_error` (both sources) and `unresolved_title_failed`
(title search) rows once more. Finally it writes a CSV of **all non-resolved**
references — `ref_doi, ref_title, ref_year, source, status, reason` — to
`--export-unresolved` (default `snowball_unresolved.csv`). The run prints a
clear `reference_lists status summary` line. **Nothing is silently dropped:**
pending/failed references are enumerated in the CSV and the summary.

## Results (TARCiS-style)

* **Seed set:** the full `results.db` corpus (all `papers` rows), used as
  snowball seeds.
* **Directions used:** backward (Crossref + OpenAlex) and a forward OpenAlex
  pass (rate-limited; backward assured retrieval is the fully-completed path).
* **Sources:** Crossref (`--source crossref`) for backward; OpenAlex
  (`filter=doi:` / `cites:`) for forward and as the alternate retry source.
* **Dedup method:** DOI (case-insensitive) then normalised title; multi-method
  provenance -> one row per paper via `paper_sources` + `snowball_edges`.
* **Dates:** runs executed 2026-08-17; schema at migration v3.
* **Reproduce:**
  ```bash
  python -m scripts.run_snowball --db results.db --direction both \
      --source crossref --resolve-only --assured \
      --export-unresolved snowball_unresolved.csv --max-api-calls 150
  ```

### Counts (results.db, after assured runs)

| Metric | Value |
|---|---|
| Papers before snowballing | 2479 |
| Papers after (incl. resolved references) | **2775** (+296) |
| `reference_lists` rows harvested | **4382** (backward 4378, forward 4) |
| `reference_lists.status = resolved` | **687** |
| `reference_lists.status = pending` (budget-resumable) | 3618 |
| `reference_lists.status = fetch_error` (both sources failed) | 2 |
| `reference_lists.status = unresolved_no_doi` | 75 |
| `reference_lists.status = unresolved_title_failed` | 0 |
| `snowball_edges` (parent->child links) | **685** |
| Papers with an Open-Access `pdf_url` | **384** |
| References exported to `snowball_unresolved.csv` | 3695 |

**Interpretation.** Of 4382 harvested references, 687 are resolved and linked.
The large `pending` bucket is **not** silent loss: it is the budget-exhausted
residue of a bounded (`--max-api-calls 150`) run and is fully enumerated in
`snowball_unresolved.csv`; each subsequent `--resolve-only` run resumes it. Only
**2** references are `fetch_error` (genuinely absent from both Crossref and
OpenAlex) and **75** are `unresolved_no_doi` (no DOI and no recoverable title) —
both classes are explicitly reported. DOI-less references *with* a title are
recovered via the conservative Crossref/OpenAlex title fallback
(`_resolve_by_title`), which on this corpus resolved real papers (e.g.
*"How Unique is Whose Web Browser?"*, *"APDU Transport over SPI/I2C"*) that
carry no DOI in the seed's reference metadata.

## See also

* [`docs/database_migration.md`](database_migration.md) — additive, idempotent
  migration framework (v1–v5).
* `src/reference_store.py` — harvest / resolve / `verify_retrieval` implementation.
* `tests/test_reference_store.py` — hermetic tests for local-first, multi-source
  retry, title fallback, status accounting, `verify_retrieval` backfill, and
  unresolved CSV export.
