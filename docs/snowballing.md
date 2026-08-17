# Snowballing — Methodology & Results

> Dedicated companion to [`docs/snowball.md`](snowball.md) (command reference) and
> [`docs/database_migration.md`](database_migration.md) (schema evolution).
> This page explains **how** the snowballing was performed for the PUF
> physical-attacks SoK study and reports the **actual results**, including the
> assured-retrieval accounting that guarantees no reference is dropped silently.

## Goal

Expand the seed corpus of physical-attack PUF papers by following the citation
graph in both directions, deduplicating every discovered paper against the
existing corpus, recording full provenance, and — critically — giving **every**
harvested reference an explicit, auditable outcome (resolved / unrecoverable /
pending) so the literature screen is reproducible and nothing is lost silently.

## Two-phase design (harvest inventory → resolve)

Implemented in `src/reference_store.py`. The redesign deliberately separates
*inventory* from *resolution*:

1. **Phase 1 — harvest (inventory).** For each seed, the **complete** reference
   list is fetched from the chosen source and **every** reference — even ones
   with no DOI, only an unstructured string, or not yet in our DB — is stored in
   `reference_lists`. This makes the inventory complete and lets a budget-limited
   run stop at any point and resume later.
2. **Phase 2 — resolve (bulk).** `resolve_reference_lists(conn, ...)` (or the
   `resolve=True` tail of `harvest_references`) walks the still-unresolved
   `reference_lists` rows and resolves them, inserting `papers`, linking
   `snowball_edges`, and capturing Open-Access PDF links.

Because resolution is idempotent (guarded by `resolved_paper_id IS NULL`), a
`--resolve-only` run can be repeated indefinitely; each pass picks up where the
last stopped.

## Local-first resolution

Before any external API is contacted, every seed and every reference is checked
against **OUR OWN database** via `local_find_paper` (to `find_existing_paper_id`):
DOI first, then normalised title. A reference already present is linked
immediately — zero network calls — and only genuinely-unknown references are
resolved externally. Local-first also prevents re-inserting a paper that was
found by `query1`, `query2` **and** snowballing: such a paper stays a single
row and merely accumulates `paper_sources` links.

## Backward + Forward (TARCiS / PRISMA-S)

* **Backward** (who a seed *cites*): Crossref `GET /works/{DOI} -> message.reference[]`,
  or OpenAlex `filter=doi:{DOI} -> referenced_works`.
* **Forward** (who *cited* a seed): OpenAlex `filter=cites:{openalex_id}`.
* `--direction {backward,forward,both}` runs one or both; `--both` does backward
  then forward. Result rows are tagged `direction` in `reference_lists`.

The run is logged to `snowball_runs` in a TARCiS-style row
`(direction, source, seed_count, references_harvested, new_papers, edges,
api_calls, started_at, finished_at, note)`, and the screening follows the
PRISMA-S (systematic snowballing) spirit: transparent, auditable, resumable.

## Batch + smart rate limiting

All external HTTP goes through `src.snowball._get_json` and the shared
`src.rate_limiter.RateLimiter`:

* **Pacing** — `wait_before_call()` keeps successive requests >= `min_interval`
  apart (`--delay`).
* **Server-directed backoff** — on `429`/`5xx`, `Retry-After` (seconds or HTTP
  date) is honoured, capped at `max_wait`.
* **Adaptive + jitter** — consecutive failures widen the interval by
  `backoff_base ** attempt + uniform(0, jitter)`; after `max_retries` it raises
  `RateLimitError`, which the resolver catches to **stop gracefully** (commit
  what was found, export the rest) instead of hammering the API.
* **Budget** — `--max-api-calls` caps requests; the run stops cleanly after
  committing once the budget is spent. Unprocessed references remain `pending`
  and are picked up by the next run.

## Zotero + OA-PDF hooks

* `src.zotero_sync.push_dois_to_zotero(...)` pushes discovered DOIs to a Zotero
  collection for bulk PDF download. `pyzotero` is optional — when unconfigured
  it prints a clear message and returns 0 (never crashes).
* `--with-pdf` reports the count of papers with a captured `pdf_url`. OA PDF
  links are taken from OpenAlex `best_oa_location.pdf_url` and Crossref
  `link[].URL` with `application/pdf`.

## Provenance & dedup (multi-method, one row)

* **Dedup** is by DOI (case-insensitive, resolver prefixes stripped), then by
  normalised title (whitespace-collapsed, lowercased); two papers with
  *different* non-null DOIs are never merged on title alone.
* A paper found by `query1:ACM`, `query2:IEEE` **and** snowballing is **one
  row** with three `paper_sources` links. Missing metadata (abstract, authors,
  year, DOI) is backfilled on the existing row.
* `snowball_edges` records each parent->child traversal at `depth`, skipping
  self- and duplicate edges.

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
| `fetch_error` | had a DOI but failed on **both** Crossref and OpenAlex |

Resolution rules applied in **both** `harvest_references` and
`resolve_reference_lists`:

1. **Local-first** — if `local_find_paper` finds it, link + `status='resolved'`.
2. **DOI'd, not local** — try the chosen source; on 404/error retry the
   **alternate** source (Crossref <-> OpenAlex, OpenAlex via `filter=doi:`). If
   either returns metadata -> insert + link + `resolved`. If **both** fail ->
   `fetch_error`.
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

* [`docs/snowball.md`](snowball.md) — full command/CLI reference and rate-limiting detail.
* [`docs/database_migration.md`](database_migration.md) — additive, idempotent
  migration framework (v1–v3).
* `src/reference_store.py` — harvest / resolve / `verify_retrieval` implementation.
* `tests/test_reference_store.py` — hermetic tests for local-first, multi-source
  retry, title fallback, status accounting, `verify_retrieval` backfill, and
  unresolved CSV export.
