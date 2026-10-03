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

## Pipeline stages (collect → validate → extract)

Snowballing runs as **three separable stages** that map 1:1 onto the original
design — *collect a full list of references → validate them → extract titles and
abstracts*. That design is unchanged; it is now **modular** (each stage is its own
resumable CLI step) and **rate-limit resilient** (TASK-010 / Decision 9).

| # | Stage | Original step | Function (`src/reference_store.py`) | CLI |
|---|---|---|---|---|
| 1 | **Harvest** | collect the full reference list | `harvest_references()` | `puf snowball run --harvest-only` |
| 2 | **Resolve / validate** | validate each reference, extract DOI / title / authors / year | `resolve_reference_lists()` | `puf snowball run --resolve-only` |
| 3 | **Backfill** | extract the abstracts | `backfill_abstracts()` | `puf snowball backfill-abstracts` |

Stage 1 stores **every** reference it sees in `reference_lists` — direction-tagged,
including DOI-less and unstructured ones — so nothing is lost. Stage 2 validates
each stored reference against Crossref / OpenAlex / Semantic Scholar, links it to a
`papers` row and records an explicit `status`. Stage 3 fills the abstracts that the
resolve metadata did not carry. `puf snowball run` without a stage flag executes
stage 1 **and** stage 2 in one go (stage 2 then scoped to the direction just
harvested), so the default behaviour is the full collect→validate flow.

**Backward is the primary direction; forward is available but optional.**
Backward (what a seed cites) works on Crossref, OpenAlex **and** Semantic
Scholar and is what built the reference inventory. Forward (what cites a seed)
is implemented via OpenAlex `filter=cites:` and is therefore **opt-in** — it
requires `--source openalex` (`--direction forward` / `--direction both`) and
is **NOT** required to obtain titles + abstracts + provenance for the
reference list. See [Backward + Forward](#backward--forward-tarcis--prisma-s).

### One implementation (shared helpers + single path)

Snowballing has a **single** implementation in `src/reference_store.py`
(`harvest_references`, `resolve_reference_lists`, `backfill_abstracts`). The old
legacy `run_snowball` path and the standalone `scripts/run_snowball.py` runner
were **removed in TASK-011**; `src/snowball.py` is now a **shared-helper module**
(`_get_json`, reference normalisation, `s2_get_references`, seed selection,
dedup and paper/source/edge persistence helpers) used by the reference store.

| Path | When it runs | Rate-limit behaviour |
|---|---|---|
| **Two-phase, local-first path** (`src/reference_store.py`) | the only path — `puf snowball run` (default `--source semantic_scholar`), `--source openalex|crossref|s2|zotero`, any `--direction`, `--resolve-only`, `--harvest-only`, `--with-pdf`, `--mailto`, `--zotero-sync` | Stages 2 and 3 **never abort the batch**: on HTTP 429 the resolver switches to the next platform in `_SOURCE_RATELIMIT_CHAIN` and continues (Decision 9), so their stats keep `aborted: 0`. Only stage 1 — the seed reference-list fetch, which has a single source and no cross-source fallback — stops harvesting gracefully with `aborted: 1`, committing everything already harvested for the next run. |

So `aborted: 1` describes the **harvest** stage's seed reference-list fetch and
the defensive guard around the resolve phase — it is **not** the general behaviour
of the resolve/backfill path on a rate limit.

#### Zotero as a rate-limit-immune fallback

Zotero is wired as a **fast local** source in both the resolve and backfill
fallback chains: `_SOURCE_RATELIMIT_CHAIN` places `"zotero"` **immediately BEFORE**
`"semantic_scholar"` (the slow, rate-limited S2 endpoint is the true last resort).
A batched helper (`src.zotero_sync.build_library_doi_index` /
`lookup_doi_in_zotero_batch`) reads the **entire local Zotero library ONCE** and
builds an in-memory DOI index, so many DOIs are resolved in a single instant,
rate-limit-immune read instead of waiting on S2; the per-DOI `lookup_doi_in_zotero` /
`fetch_abstract_via_zotero` remain as the fallback when the bulk index cannot be
built. Zotero is **immune to external API rate limits** for papers already cached,
and it **never raises** — when `pyzotero` is missing or the `ZOTERO_*` env vars are
unset it simply yields no match and the chain continues. `--no-alternate` disables
*all* cross-source fallback including Zotero; `--no-batch` skips the batched
OpenAlex/Zotero pre-passes and falls back to the per-DOI chain.

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
   *not-found* fallback. Standalone `resolve_reference_lists` (`--resolve-only`)
   is **direction-agnostic**: it walks the unresolved rows of **both** directions,
   whereas the `resolve=True` tail inside `harvest_references` is scoped to the
   direction just harvested. DOI-less rows that carry a title go through the
   conservative title search in the same pass. On HTTP **429** the resolver
   switches to the next platform in the fallback chain and the batch **continues**
   (TASK-010 / Decision 9) — it does not abort.

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
limiter=None, no_alternate=False, use_batch=True)` fills empty `papers.abstract`
values for every paper that has an empty abstract **and** either a `DOI` **or** a
non-null `title` (DOI-less, title-bearing papers are handled via title search,
below). It runs, in order, a set of **batched** pre-passes (all skipped under
`--no-batch` / `--no-alternate`) and then a per-DOI fallback:

* **Zotero local pre-pass** (`_zotero_batch_by_dois`) -- resolves any cached DOIs
  from the local library **first**, instantly and rate-limit immune (the free, fast
  path that avoids every external API).
* **OpenAlex batch pre-pass** (`_openalex_batch_by_dois`) -- runs only when the chosen
  `source` is `openalex`; one `filter=doi:` GET per chunk of ~50 DOIs.
* **Crossref batch pre-pass** (`_crossref_batch_by_dois`) -- one
  `GET /works?filter=doi:a,b,c,...` per chunk of ~50 DOIs fills many abstracts in a
  single request (~50 DOIs per GET).
* **Semantic Scholar batch pre-pass** (`_s2_batch_by_dois`) -- one
  `POST /paper/batch` per chunk of up to 100 DOIs (capped at <=500 ids per POST)
  resolves many DOIs in a single round-trip.

Each pre-pass is **strictly additive** and **adaptive**: on a transient error (HTTP
429 via `RateLimitError`, a network `URLError`/`OSError`/`TimeoutError`, or a 5xx) the
source is added to the run-wide **`throttled` set** and skipped for the *remainder of
the run* -- so a dead source is tried exactly once, never retried per paper (no repeated
backoff, no long delays). The per-DOI fallback loop (`_fetch_abstract_for_backfill`)
then mops up the remainder, switching to the next platform in `_SOURCE_RATELIMIT_CHAIN`
on *any* transient error (not just 429); a throttled fetch does not abort the batch,
each paper commits individually, and the run returns what was done. The source may be
`crossref` (retries OpenAlex on a miss unless `--no-alternate`), `openalex`, or
`semantic_scholar`/`s2` (standalone for not-found DOIs; on a rate-limit it falls back to
OpenAlex/Crossref unless `--no-alternate`). `--no-alternate` keeps the lookup strictly
single-source, but the dead source is still recorded in `throttled` so later papers skip it.

**DOI-less (title-only) papers.** After the DOI pre-passes, each title-bearing paper
with no DOI is resolved by a tolerant title search across **all four** sources in order
**Crossref -> OpenAlex -> Semantic Scholar -> Zotero** (`_backfill_title_search`), skipping
any source already in the run-wide `throttled` set. A candidate is accepted only by
`_best_title_match` (tolerant title similarity >= 0.85 via `difflib` on NFKC-normalised,
accent/punctuation-stripped keys, with a +/-1-year guard); the Zotero match is a tolerant
best-title match over the operator's local library. On a match the paper's `abstract`
**and**, if it was missing, its `DOI` **and** canonical `title` are filled.

It is idempotent and resumable (only empty abstracts are touched) and honours
`--max-api-calls` / `--delay`.

## Backward + Forward (TARCiS / PRISMA-S)

Backward is the primary direction; forward is available but optional. They
differ in **which sources can serve them**.

* **Backward** (who a seed *cites*) — supported on **all three** sources: Crossref
  `GET /works/{DOI} -> message.reference[]`, OpenAlex
  `filter=doi:{DOI} -> referenced_works`, or Semantic Scholar
  (`s2_get_references`, `DOI:10.x/y`).
* **Forward** (who *cited* a seed) — **OpenAlex only**, via
  `filter=cites:{openalex_id}`. Crossref forward and Semantic Scholar forward are
  explicitly **not supported** (each is skipped with a message), so a forward pass
  **requires `--source openalex`**:

  ```bash
  puf snowball run --source openalex --direction forward   # forward only
  puf snowball run --source openalex --direction both      # forward + backward
  ```

  `--source semantic_scholar --direction forward` (likewise `--source crossref
  --direction forward`) harvests **0** forward references by design — use
  `--source openalex` for the forward pass.
* `--direction {backward,forward,both}` selects the direction(s); `both` issues one
  `harvest_references` call per direction (forward first, then backward). Result
  rows are tagged `direction` (`backward` / `forward`) in `reference_lists`.
* `--resolve-only` is direction-agnostic — it resolves every unresolved
  `reference_lists` row whatever direction it was harvested in.

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
by `src.snowball._get_json` (shared by the reference-store resolver):

| Mechanism | Behaviour |
|-----------|-----------|
| Pacing | `wait_before_call()` keeps successive requests at least `min_interval` seconds apart (`--delay`). |
| Adaptive pacing | While consecutive failures accumulate, the interval widens by `backoff_base ** failures` (capped at `max_wait`). |
| Server-directed backoff | On `429`/`5xx`, `Retry-After` is honoured — integer seconds or HTTP date (`email.utils.parsedate_to_datetime`), capped at `max_wait`. |
| Exponential backoff + jitter | Without a header: `min(max_wait, backoff_base ** attempt) + uniform(0, jitter)`. |
| Give up politely | After `max_retries` consecutive failures a `RateLimitError` is raised; the resolver falls back to the next source in the chain (and continues the batch) instead of hammering the API, recording `fetch_error` only once every candidate is throttled. |

Non-retryable HTTP errors (e.g. `404`) are re-raised; the per-paper loop logs
them and continues with the next seed.

### Source-aware pacing

The limiter is **source-aware**: it keeps a separate pacing interval per source
(`DEFAULT_SOURCE_INTERVALS`) and the CLI builds it with
`RateLimiter(min_interval=delay, per_source_intervals=...)` so high-throughput
pools stay fast while slow ones stay polite:

| Source | Pacing interval |
|--------|-----------------|
| `crossref` | `0.05s` (polite pool; `--mailto`) |
| `openalex` | `0.05s` (polite pool; `--mailto`) |
| `semantic_scholar` | `0.6s` (~100 req / 5 min) |
| `zotero` | `0.0s` (local read, rate-limit immune) |

`--delay` remains the fallback / `min_interval` for any source not in the map, so
a healthy fast source (Crossref / OpenAlex) is paced at `0.05s` regardless of
`--delay`, S2 at `0.6s`, and Zotero is effectively instant. Adaptive widening on
consecutive failures still applies on top of the per-source interval.

### OpenAlex batched DOI lookup (fast primary path)

Titles, abstracts and provenance for the reference list are obtained primarily
via an **OpenAlex batched multi-DOI lookup** (`_openalex_batch_by_dois`): one
`GET /works?filter=doi:d1|d2|...` call per chunk of `50` DOIs, instead of one
request per DOI. This batch pre-pass runs **before** the existing per-DOI
fallback chain in `backfill_abstracts` (fills most abstracts in ~1 call per
50 DOIs) and in the **OpenAlex** branch of `_batch_resolve_references` (links
papers + `snowball_edges` + `status='resolved'`), and is **strictly additive** —
on any error it defers to the unchanged per-DOI Crossref → OpenAlex → Semantic
Scholar → Zotero chain. It is used whenever OpenAlex is an allowed source — i.e.
for **OpenAlex resolve** and for **Crossref/OpenAlex backfill**. **Crossref
*resolution* remains per-DOI** (paced at `0.05s` via the Polite Pool) and does
**not** use the batch pre-pass. The pre-pass is **never** used for
`semantic_scholar`, which is standalone for not-found DOIs, and **never** under
`--no-alternate`). S2 therefore stays a true last-resort for not-found DOIs and
forward snowball stays opt-in.

  A **batched Zotero pre-pass** (`_zotero_batch_by_dois`, backed by
  `zotero_sync.lookup_doi_in_zotero_batch` / `build_library_doi_index`) runs
  alongside the OpenAlex one: it reads the local Zotero library once and resolves
  any cached DOIs instantly (before S2), so papers already in the operator's
  library never wait on the rate-limited S2 endpoint. Both pre-passes are skipped
  under `--no-batch`.

**Backfill batch pre-passes.** `backfill_abstracts` additionally runs **Crossref**
(`_crossref_batch_by_dois`, ~50 DOIs per `GET /works?filter=doi:a,b,c`) and
**Semantic Scholar** (`_s2_batch_by_dois`, `POST /paper/batch`, up to 100 DOIs per
POST / capped <=500) batch pre-passes before the per-DOI chain, in addition to the
OpenAlex and Zotero ones above -- all strictly additive. OpenAlex is the most
aggressively rate-limited of the three (HTTP 429 once its daily polite-pool budget,
which resets ~midnight UTC, is spent), so in practice **Crossref + S2 are the
reliable everyday backfill path**; a throttled OpenAlex batch is simply skipped and
the run continues to the next pre-pass / the per-DOI fallback.

* **Budget** — `--max-api-calls` caps the number of HTTP requests; the run stops
  cleanly after committing once the budget is spent. Unprocessed references
  remain `pending` and are picked up by the next run.
* **Continue across sources (stage 2 / stage 3)** — on `RateLimitError` the current
  source is skipped and the next platform in the fallback chain is tried; the batch
  keeps going (commits what was found, keeps the rest resumable) rather than
  crashing or aborting. Only if *every* candidate source is throttled does the
  affected DOI become `fetch_error`, and the batch still continues. Large-scale
  snowballing is achieved by combining `--delay` with repeated bounded runs, which
  resume automatically. `--no-alternate` makes rate-limit strict-single-source (no
  fallback) but still never aborts the whole run.
* **Graceful stop (stage 1)** — harvesting a seed's reference list has
  a single source and therefore no cross-source fallback: if it is throttled,
  `harvest_references` stops harvesting, commits the inventory collected so far and
  reports `aborted: 1`; the next run resumes with the remaining seeds.

**OpenAlex budget exhaustion.** OpenAlex's polite pool enforces a daily request
budget that can be exhausted mid-run. By default a throttled OpenAlex is skipped and
resolution spills onto Crossref/Semantic Scholar (the run continues, no stall). For a
fully deterministic single-source run, pass `--no-alternate --source semantic_scholar`
(or `--source crossref --no-alternate`) to continue resolving on a single source
without depending on OpenAlex; the run still continues and stays resumable.

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

Semantic Scholar may not have all papers, especially older or less-cited ones. A
*not-found* on S2 does **not** abort anything: S2 is standalone for not-found DOIs,
so the affected reference is recorded as `fetch_error` and the batch continues. A
*rate-limited* S2 spills onto OpenAlex/Crossref and the batch also continues
(Decision 9). Reference metadata quality varies — Crossref
references frequently carry only an unstructured string (skipped) and few
abstracts.

## CLI commands

```bash
# Snowball the whole corpus, bounded to 40 API requests
puf snowball --db results.db --depth 1 --max-refs 15 --source semantic_scholar --max-api-calls 40

# Seed from the papers of queries 3 and 4 (paper_queries junction)
puf snowball run --seed-query-ids 3,4 --depth 1 --max-refs 20

# Seed from explicit papers, no relevance re-run
puf snowball run --seed-paper-ids 12,44,91 --no-auto-relevance

# Stage-by-stage (collect -> validate -> extract abstracts)
puf snowball run --db results.db --source openalex --direction both --harvest-only
puf snowball run --db results.db --source semantic_scholar --resolve-only
puf snowball backfill-abstracts --db results.db --source crossref --delay 1.0

# Show snowball stats
puf snowball stats

# Backfill abstracts for harvested papers missing them (Crossref, OpenAlex retry)
puf snowball backfill-abstracts --db results.db --source crossref --delay 1.0
puf snowball backfill-abstracts --db results.db --source semantic_scholar --delay 1.0
puf snowball backfill-abstracts --db results.db --source crossref --no-alternate --delay 1.0

# (canonical entry point is `puf snowball`; the standalone scripts.run_snowball
#  runner was removed in TASK-011)
puf snowball run --db results.db --depth 1 --max-refs 15 --source semantic_scholar --max-api-calls 40
```

`run` is the default subcommand, so the first two forms are equivalent.
`--query-ids 3 4` is still accepted for backwards compatibility. References are
fetched, normalised (DOI, title, authors, year, abstract, venue), deduplicated,
and provenance is accumulated; the relevance pipeline re-runs afterwards only
when new papers were inserted (disable with `--no-auto-relevance`).

Two-phase flags:

* `--harvest-only` runs **stage 1** only: store the reference inventory without the
  Phase-2 resolve.
* `--resolve-only` runs only **stage 2** / Phase 2 (idempotent, resumable, and
  direction-agnostic — it picks up backward *and* forward rows). **Stage 3** is the
  separate `puf snowball backfill-abstracts` command.
* `--direction {backward,forward,both}` selects the citation direction. Forward
  harvesting is **OpenAlex-only**, so `forward` / `both` must be combined with
  `--source openalex`; with any other source the forward pass is skipped and
  harvests 0 references.
* `--source {crossref,openalex,semantic_scholar,s2,zotero}` selects the harvest/resolve
  source (CLI default: `semantic_scholar`). `crossref` and `openalex` are
  batch-friendly and retry each other; `semantic_scholar` (alias `s2`) resolves via
  Semantic Scholar **alone** for *not-found* DOIs (no OpenAlex not-found fallback),
  while a *rate-limited* source of any kind spills onto the next platform in the
  chain (Decision 9). Combine with `--no-alternate` (below) to force strict
  single-source resolution, incl. on rate-limit. Forward harvesting needs
  `--source openalex`. `--source zotero` uses the local Zotero library as the
  resolve/backfill source — a rate-limit-immune last resort (see Zotero fallback
  above); it is consulted after Crossref/OpenAlex/Semantic Scholar.
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
* `--no-batch` — skip the batched OpenAlex multi-DOI pre-pass (and the batched
  Zotero pre-pass), forcing the per-DOI cross-source chain only. Useful when
  OpenAlex is unavailable or its daily budget is exhausted and you do not want a
  large batched read to fail before falling back. Crossref/OpenAlex *resolve*
  stays per-DOI either way; the pre-pass only affects OpenAlex resolve and
  Crossref/OpenAlex backfill.
* `--delay <seconds>` sets the rate-limiter pacing interval; it is honoured on
  **both** the harvest and the resolve paths (the CLI builds a single
  `RateLimiter(min_interval=delay)` and passes it to `harvest_references` and
  `resolve_reference_lists`).

## Statistics

**Two-phase path.** `harvest_references` and `resolve_reference_lists`
(`src/reference_store.py`) return `{'seeds', 'references_harvested',
'new_papers', 'edges', 'api_calls', 'resolved_local', 'aborted'}` (plus
`exported_unresolved` when the unresolved CSV was written). Here `aborted` stays
**0** when a source is throttled during resolution — the resolver switches
platform and continues (Decision 9). It becomes `1` only when the *harvest*
stage's single-source reference-list fetch is throttled (harvesting stops
gracefully; everything already harvested is committed and resumable) or when an
un-guarded `RateLimitError` reaches the defensive handler wrapped around the
resolve phase.

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
| `fetch_error` | had a DOI but failed on the chosen source (Crossref, OpenAlex, or Semantic Scholar) — including when *all* attempted sources were rate-limited; with `--no-alternate` only the single chosen source is attempted |

Resolution rules applied in **both** `harvest_references` and
`resolve_reference_lists`:

1. **Local-first** — if `local_find_paper` finds it, link + `status='resolved'`.
2. **DOI'd, not local** — try the chosen source. On a **RateLimitError (HTTP 429)**
   the next platform in the fallback chain is tried (`semantic_scholar` -> `openalex`
   -> `crossref`, `openalex` -> `crossref` -> `semantic_scholar`, `crossref` ->
   `openalex` -> `semantic_scholar`) and the batch **continues**; this maximises API
   utilisation. A 404/error (not-found) does **NOT** trigger a cross-source fallback,
   except the existing Crossref <-> OpenAlex 404-alternate (`_ALTERNATE_SOURCE`);
   Semantic Scholar resolves standalone for not-found DOIs (no OpenAlex fallback). If
   the attempted source(s) return metadata -> insert + link + `resolved`. If all
   attempted sources fail (or are all rate-limited) -> `fetch_error`. With `--no-alternate`
   only the chosen source is attempted (no cross-source fallback at all, incl. on
   rate-limit; the affected DOIs become `fetch_error` and the batch still continues).
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
* **Dates:** initial harvest 2026-08-17; assured `--resolve-only` resolution run 2026-08-27 (numbers below); schema at migration v3.
* **Reproduce:**
  ```bash
  puf snowball run --db results.db --direction both \
      --source crossref --resolve-only --assured \
      --export-unresolved snowball_unresolved.csv --max-api-calls 150
  ```
  With `--resolve-only` the `--direction` flag is inert (the resolver walks every
  unresolved row of both directions); the forward rows counted below were
  harvested earlier with `--source openalex --direction forward`, the only
  source that supports forward search.

### Counts (results.db — 2026-08-27)

> **Date: 2026-08-27.** The table below records the current state of `results.db`.
> Resolution: `--resolve-only --source crossref` (Crossref-only, OpenAlex was
> budget-blocked). Abstract backfill: attempted via Crossref; Crossref Polite Pool
> does not return abstract text for most (conference) DOIs, so abstracts remain
> unfilled — see [Abstract backfill status](#abstract-backfill-status-2026-08-27).
> The accounting scheme does not change between runs; regenerate the current
> figures with `puf analyze snowball --db results.db` or `puf snowball stats`.

| Metric | Value |
|---|---|
| Papers in corpus (total) | **4936** |
| `reference_lists` rows (TOTAL) | **4382** |
| `reference_lists.status = resolved` | **2895** |
| `reference_lists.status = pending` (budget-resumable) | 0 |
| `reference_lists.status = fetch_error` (DOI present, Crossref unresolvable) | 24 |
| `reference_lists.status = unresolved_no_doi` (terminal, expected) | 1405 |
| `reference_lists.status = unresolved_title_failed` | 58 |
| `snowball_edges` (parent->child links) | **2890** |
| Max snowball depth | 1 |
| Distinct papers discovered via snowball (`child_paper_id`) | **2809** |
| Corpus papers missing abstract | 1811 (of 4936) |
| Resolved-target papers missing abstract | 1645 |
| Resolved-target papers missing title | 174 |

**Interpretation.** Of 4382 harvested references, 2895 are resolved and linked
via `snowball_edges`, giving a corpus of 4936 papers total with 2809 distinct
papers discovered through snowballing at a maximum depth of 1. The `pending`
bucket is now empty (0): the budget-exhausted residue of the earlier bounded run
(2026-08-17) has been fully consumed by subsequent `--resolve-only` runs, and
nothing is silently dropped — every non-resolved reference is enumerated in
`snowball_unresolved.csv`. Only **24** references are `fetch_error` (a DOI is
present but Crossref could not resolve it; recoverable via OpenAlex / Semantic
Scholar / Zotero) and **1405** are `unresolved_no_doi` (no DOI and no recoverable
title — terminal and expected), with a further **58** `unresolved_title_failed`
(had a title but no confident +/-1-year match). All three non-resolved classes
are explicitly reported. DOI-less references *with* a title are recovered via the
conservative Crossref/OpenAlex title fallback (`_resolve_by_title`), which on
this corpus resolved real papers (e.g. *"How Unique is Whose Web Browser?"*,
*"APDU Transport over SPI/I2C"*) that carry no DOI in the seed's reference
metadata.

### Abstract backfill status (2026-08-27)

Of the 4936 corpus papers, **1810 still have no abstract** — and 1644 of those are
snowball-resolved targets. This is a **source-availability** limitation, not a
code defect: Crossref's Polite Pool does not return abstract text for most
(conference) DOIs, so the Crossref backfill could not fill them. The missing
abstracts live in **OpenAlex** (`abstract_inverted_index`) and **Semantic
Scholar**, which do carry abstract text.

**Attempted fill (2026-08-27).** A backfill was actually attempted on 2026-08-27:

- A **Zotero** backfill was attempted but `ZOTERO_LIBRARY_ID` / `ZOTERO_API_KEY`
  are unset, so it filled 0 (instant no-op). Once the operator configures Zotero,
  `puf snowball backfill-abstracts --source zotero` fills library papers instantly
  via the batched local lookup.
- A **Semantic Scholar** backfill (`--source semantic_scholar --no-alternate`) was
  attempted but S2 returned HTTP 504 gateway errors and filled only 1 abstract.
- **Crossref's** Polite Pool does not return abstract text, so it contributes 0 (as
  already noted).

Therefore the remaining gap (1810 corpus papers missing an abstract; 1644 of them
snowball-resolved) is **SOLELY an external-API-availability gap, not a code
defect**: OpenAlex is hard budget-blocked (Retry-After ~15.7 h, resets ~midnight
UTC) and S2 was degraded (504s) at run time. Once OpenAlex's budget resets,
`puf snowball backfill-abstracts --source openalex` (OpenAlex batched multi-DOI
lookup, fast) closes the gap; `--no-batch` skips the OpenAlex pre-pass when it is
unavailable.

**Current coverage (2026-08-27):** corpus 4936 papers; 1810 missing abstract;
resolved-target papers missing abstract 1644; resolved-target missing title 174.

**Completion path.** Once OpenAlex's prepaid budget resets (≈ midnight UTC; on
2026-08-27 OpenAlex was hard-429'd with a ~15.7 h `Retry-After`), re-run:

```bash
puf snowball backfill-abstracts --source openalex
```

This uses the new **OpenAlex batched multi-DOI lookup** (one call per 50 DOIs —
fast), described in [OpenAlex batched DOI lookup](#openalex-batched-doi-lookup-fast-primary-path).
A Crossref / Semantic Scholar / Zotero fallback then covers the remainder. When
OpenAlex is unavailable, `--no-batch` skips the OpenAlex pre-pass and falls back
to the per-DOI chain.

**Status of the completeness guarantee.** The study goal that *every resolved
paper has a title + abstract* is therefore **NOT yet satisfied**: it is blocked
on **OpenAlex availability**, not on code. Resolution (titles + links) is
complete; abstract backfill is the outstanding, source-gated step.

## See also

* [`docs/database_migration.md`](database_migration.md) — additive, idempotent
  migration framework (v1–v5).
* `src/reference_store.py` — harvest / resolve / `verify_retrieval` implementation.
* `tests/test_reference_store.py` — hermetic tests for local-first, multi-source
  retry, title fallback, status accounting, `verify_retrieval` backfill, and
  unresolved CSV export.
