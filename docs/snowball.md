# Snowball / Backward Search Documentation

## Overview

Snowball (backward) search expands the literature review by following the reference chains of papers already in the corpus. If paper A cites paper B, and paper B is on topic, then paper B should be included in our corpus.

## Algorithm

1. **Identify seed papers:** Precedence is `seed_paper_ids` (explicit ids) > `seed_query_ids` (resolved through the `paper_queries` junction, the v2 provenance table) > the whole corpus (`SELECT id FROM papers`).
2. **Skip expanded seeds:** Seeds that already have outgoing `snowball_edges` are skipped (`skip_expanded=True`), so a budget-limited run resumes where the previous one stopped. Use `--reexpand` to force re-expansion.
3. **Fetch references:** A DOI is used directly as the Semantic Scholar identifier (`DOI:10.x/y`), which saves one request; papers without a DOI fall back to a title search. Crossref uses `GET /works/{DOI}`.
4. **Normalise references:** Extract DOI, title, authors, year, abstract, venue from each reference entry.
5. **Deduplicate:** Match on DOI (case-insensitive, resolver prefixes stripped), then on the normalised title (whitespace-collapsed, lowercased).
6. **Accumulate provenance:** A known paper is *never* re-inserted; it only gains the `snowball` link in `paper_sources`, so a paper found by `query1:ACM`, `query2:ACM` *and* snowballing is one row with three source links. Missing metadata (abstract, authors, year, DOI) is backfilled.
7. **Track depth:** Depth 1 = direct reference of a seed. Depth 2 = reference of a reference; the frontier of each depth is the set of children discovered at the previous depth.
8. **Avoid cycles/duplicates:** Self-edges are skipped and `(child, parent)` edges are inserted only once.
9. **Bound the run:** `max_api_calls` caps the number of HTTP requests; the run stops cleanly (after committing) once the budget is spent.
10. **Re-run pipeline:** Relevance evaluation runs afterwards only when new papers were inserted.

## Smart rate limiting

`src/rate_limiter.py` provides `RateLimiter`, shared by every request of a run:

| Mechanism | Behaviour |
|-----------|-----------|
| Pacing | `wait_before_call()` keeps successive requests at least `min_interval` seconds apart (`--delay`). |
| Adaptive pacing | While consecutive failures accumulate, the interval widens by `backoff_base ** failures` (capped at `max_wait`). |
| Server-directed backoff | On `429`/`5xx`, `Retry-After` is honoured — integer seconds or HTTP date (`email.utils.parsedate_to_datetime`), capped at `max_wait`. |
| Exponential backoff + jitter | Without a header: `min(max_wait, backoff_base ** attempt) + uniform(0, jitter)`. |
| Give up politely | After `max_retries` consecutive failures a `RateLimitError` is raised; `run_snowball` logs it, sets `aborted: 1`, commits and stops instead of hammering the API. |

Non-retryable HTTP errors (e.g. `404`) are re-raised; the per-paper loop logs them and continues with the next seed.

## API Usage

### Semantic Scholar (Primary)

- **Base URL:** `https://api.semanticscholar.org/graph/v1`
- **Search paper:** `GET /paper/search?query={title}&fields=title,authors,year,abstract,externalIds,publicationVenue&limit=1`
- **Get references:** `GET /paper/{paper_id}/references?fields=...&limit={n}` where `paper_id` may be `DOI:10.x/y`
- **Rate limit:** ~100 requests per 5 minutes (free tier); the unauthenticated pool frequently answers `429`
- **Auth:** None required

### Crossref (Fallback)

- **Base URL:** `https://api.crossref.org/works`
- **Get work:** `GET /works/{DOI}`
- **References:** `message.reference` array in response
- **Rate limit:** ~50 requests per second (polite pool)
- **Auth:** None required

## CLI Commands

```bash
# Snowball the whole corpus, bounded to 40 API requests
puf snowball --db results.db --depth 1 --max-refs 15 --source semantic_scholar --max-api-calls 40

# Seed from the papers of queries 3 and 4 (paper_queries junction)
puf snowball run --seed-query-ids 3,4 --depth 1 --max-refs 20

# Seed from explicit papers, no relevance re-run
puf snowball run --seed-paper-ids 12,44,91 --no-auto-relevance

# Show snowball stats
puf snowball stats

# Standalone runner with the same options
python -m scripts.run_snowball --db results.db --depth 1 --max-refs 15 --max-api-calls 40
```

`run` is the default subcommand, so the first two forms are equivalent. `--query-ids 3 4` is still accepted for backwards compatibility.

## Statistics

`run_snowball` returns `{'seeds', 'skipped', 'processed', 'discovered', 'new', 'linked', 'edges', 'api_calls', 'aborted'}`:
`new` counts inserted papers, `linked` counts already-known papers that gained the `snowball` provenance link, and `aborted` is `1` when the API became unavailable.

## Deduplication

References are deduplicated by:
1. **DOI** (preferred): case-insensitive match after stripping `doi:` / `https://doi.org/` prefixes.
2. **Title** (fallback): whitespace-collapsed, lowercased match; two papers with *different* non-null DOIs are never merged on title alone.
3. If neither matches, a new paper row is created and linked to the `snowball` source.

## Limitations

- Semantic Scholar may not have all papers, especially older or less-cited ones; the unauthenticated pool is often rate limited, in which case the run stops gracefully with `aborted: 1`.
- Reference metadata quality varies; Crossref references frequently carry only an unstructured string (skipped) and few abstracts.
- Rate limits slow large-scale snowballing; combine `--delay` with repeated bounded runs (`--max-api-calls`), which resume automatically.

## Two-phase, local-first redesign (research-backed)

The snowball subsystem was redesigned for scientific rigor and rate-limit
resilience. It is implemented in `src/reference_store.py` and `src/zotero_sync.py`
alongside the legacy `src.snowball.run_snowball` (still available via the classic
CLI flags).

### Principle: local-first

Every seed paper is checked against **OUR OWN database** before any external API
is contacted. A reference that already exists in `papers` is linked immediately
(no network call), and only the genuinely-unknown references are resolved
externally.

### Phase 1 — harvest (inventory)

`harvest_references(conn, seed_paper_ids, direction, source, ...)`:

1. For each seed, fetch its **complete** reference list from the source:
   - backward + crossref: `GET /works/{DOI}` → `message.reference[]`
   - backward + openalex: `GET /works?filter=doi:{DOI}` → `referenced_works` IDs,
     batch-resolved
   - forward + openalex: `GET /works?filter=cites:{openalex_id}` (citing works)
   - backward + s2: `s2_get_references`
2. **Store every reference** in the `reference_lists` table (inventory
   completeness) — including references not yet in our DB — even those with no
   DOI (unstructured only). A per-parent expression-unique index prevents
   duplicate inventory rows.
3. Resolve already-known references locally (creates `snowball_edges`).
4. Batch-resolve the remaining DOI-bearing references (Crossref per-DOI polite
   lookups, or OpenAlex `filter=doi:...|...`), inserting `papers`, linking
   `snowball_edges`, and capturing Open-Access PDF links into `papers.pdf_url`.

### Phase 2 — resolve (later / bulk)

`resolve_reference_lists(conn, source, ...)` second phase takes **every**
unresolved `reference_lists` row that carries a DOI and bulk-resolves it. This
implements "store the list, then resolve later / bulk-download": a budget-limited
harvest can stop at any point and a later `--resolve-only` run continues,
idempotently, picking up where it left off.

### Directions, sources, batching

- `--direction {backward,forward,both}` — forward snowball (who cited our papers)
  is supported via OpenAlex `cites:`. `--both` runs backward then forward.
- `--source {crossref,openalex,s2}` — Crossref (backward) and OpenAlex
  (backward + forward) are batch-friendly; S2 is backward-only.
- `--harvest-only` stores the inventory without the Phase-2 resolve;
  `--resolve-only` runs only Phase 2.
- External calls go through `src.rate_limiter.RateLimiter` (pacing, `Retry-After`,
  adaptive backoff). `--max-api-calls` bounds the run; it stops gracefully after
  committing what was discovered (TARCiS-style).

### Zotero / PDF hooks

- `src/zotero_sync.push_dois_to_zotero(conn, paper_ids, ...)` pushes discovered
  DOIs to a Zotero collection for bulk PDF download. `pyzotero` is optional — if
  it is not installed or not configured (`ZOTERO_LIBRARY_ID` / `ZOTERO_API_KEY`),
  the function prints a clear message and returns 0 (never crashes).
- `--with-pdf` reports the count of papers with a captured `pdf_url`.

### `snowball_runs` logging (TARCiS-style)

Every harvest/resolve run writes a row to `snowball_runs`
`(direction, source, seed_count, references_harvested, new_papers, edges,
api_calls, started_at, finished_at, note)`, so the provenance and cost of each
snowball expansion is auditable.
