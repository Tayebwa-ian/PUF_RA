# Snowball / Backward Search Documentation

## Overview

Snowball (backward) search expands the literature review by following the reference chains of already-relevant papers. If paper A cites paper B, and paper B is on topic, then paper B should be included in our corpus.

## Algorithm

1. **Identify seed papers:** Papers marked as relevant by the relevance engine (`is_relevant = True`) or LLM (`decision = 'REVIEW'`).
2. **Fetch references:** For each seed paper, fetch its reference list via Semantic Scholar API (or Crossref fallback).
3. **Normalise references:** Extract DOI, title, authors, year, abstract from each reference entry.
4. **Deduplicate:** Check if reference already exists in DB by DOI (preferred) or normalized title.
5. **Insert new papers:** Create `papers` row with source = "snowball". Link via `paper_sources` and `snowball_edges`.
6. **Track depth:** Depth 1 = direct reference from seed paper. Depth 2 = reference of a reference (recursive expansion).
7. **Re-run pipeline:** After import, run relevance evaluation and optionally LLM screening on new papers.
8. **Avoid cycles:** Use `snowball_edges` composite PK `(child, parent)` to prevent re-processing the same edge.

## API Usage

### Semantic Scholar (Primary)

- **Base URL:** `https://api.semanticscholar.org/graph/v1`
- **Search paper:** `GET /paper/search?query={title}&fields=title,authors,year,abstract,externalIds,publicationVenue&limit=1`
- **Get references:** `GET /paper/{paper_id}/references?fields=...&limit={n}`
- **Rate limit:** ~100 requests per 5 minutes (free tier)
- **Auth:** None required

### Crossref (Fallback)

- **Base URL:** `https://api.crossref.org/works`
- **Get work:** `GET /works/{DOI}`
- **References:** `message.reference` array in response
- **Rate limit:** ~50 requests per second (polite pool)
- **Auth:** None required

## Configuration

```yaml
# config/snowball.yaml (defaults shown)
max_depth: 2
max_refs_per_paper: 20
api: semantic_scholar
rate_limit_delay: 1.0  # seconds between API calls
auto_screen: false
auto_relevance: true
```

## CLI Commands

```bash
# Run snowball on relevant papers from queries 3 and 4
puf snowball run --query-ids 3 4 --depth 1 --max-refs 20 --source semantic_scholar

# Run snowball with auto-screening
puf snowball run --query-ids 3 4 --depth 1 --auto-relevance --auto-screen --model gpt-4o

# Show snowball stats
puf snowball stats
```

## Deduplication

References are deduplicated by:
1. **DOI** (preferred): Exact match on `papers.doi`.
2. **Title** (fallback): Case-insensitive exact match on `papers.title`.
3. If neither DOI nor title matches, a new paper row is created.

## Limitations

- Semantic Scholar may not have all papers, especially older or less-cited ones.
- Reference metadata quality varies; DOIs may be missing.
- Rate limits may slow large-scale snowballing; use `--delay` to adjust.
