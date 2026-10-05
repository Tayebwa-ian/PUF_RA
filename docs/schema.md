# Schema Documentation — PUF Research Pipeline

> **Safe querying.** The structured way to read this database (without handing
> out raw SQL) is the read-only MCP server in `src/mcp_server.py`. It exposes
> `execute_select`, `get_paper`, `get_paper_provenance`, `insert_paper`,
> `list_papers`, `search_papers`, `get_paper_by_doi`, plus `store_analysis` /
> `list_analysis`. `execute_select` rejects anything but a read-only `SELECT` and
> opens a read-only connection; it also permits a leading `WITH` (read-only
> common-table expression) in addition to a bare `SELECT`. See
> [`docs/analysis.md`](analysis.md) for how the analyst agent uses it.

## Entity-Relationship Diagram

```
┌────────────┐       ┌──────────────────┐       ┌─────────────┐
│  sources   │1────N│   paper_sources  │N────1│   papers    │
│            │       │                  │       │             │
│ id PK      │       │ paper_id  PK,FK  │       │ id PK       │
│ name UNIQUE│       │ source_id PK,FK  │       │ doi UNIQUE  │
│ description│       └──────────────────┘       │ title       │
└────────────┘                                   │ authors     │
       ▲                                         │ year        │
       │                                         │ abstract    │
┌────────────┐       ┌──────────────────┐        │ is_relevant │
│  queries   │1────N│   paper_queries  │N───────│ relevance_  │
│            │       │                  │       │ score       │
│ id PK      │       │ paper_id  PK,FK  │       └──────┬──────┘
│ platform   │       │ query_id  PK,FK  │              │
│ query_text │       └──────────────────┘              │1
└────────────┘                                         │ N
                                                       ▼
                                             ┌──────────────────┐
                                             │  relevance_evals │
                                             │                  │
                                             │ id PK            │
                                             │ paper_id FK      │
                                             │ method           │
                                             │ score            │
                                             │ is_relevant      │
                                             │ threshold        │
                                             │ details (JSON)   │
                                             └──────────────────┘

┌────────────┐       ┌─────────────┐
│   runs     │1────N│  decisions  │
│            │       │             │
│ id PK      │       │ id PK       │
│ model      │       │ run_id FK   │
│ prompt_text│       │ paper_id FK │
│ misc       │       │ decision    │
└────────────┘       │ justification│
                     │ excerpt     │
                     │ ...         │
                     └─────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    snowball_edges                            │
│                                                              │
│  child_paper_id PK,FK ────┐                                  │
│  parent_paper_id PK,FK ───┼──▶ papers (both sides)           │
│  depth                     │                                  │
└─────────────────────────────────────────────────────────────┘
```

## Table Reference

### `sources`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| name | TEXT | NOT NULL, UNIQUE | Source name (e.g. 'ACM') |
| description | TEXT | NULLABLE | Optional description |

### `queries`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| run_at | TEXT | NOT NULL, DEFAULT current_timestamp | When query was run |
| platform | TEXT | NOT NULL | Platform name (e.g. 'IEEE Xplore') |
| query_text | TEXT | NOT NULL | The search query string |

### `papers`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| title | TEXT | NOT NULL | Paper title |
| authors | TEXT | NOT NULL | Author names (normalised) |
| year | INTEGER | NOT NULL | Publication year |
| abstract | TEXT | NOT NULL | Paper abstract |
| publication_title | TEXT | NOT NULL | Journal/conference name |
| doi | TEXT | UNIQUE, NULLABLE | Digital Object Identifier |
| keywords | TEXT | NULLABLE | Author keywords |
| is_relevant | BOOLEAN | DEFAULT NULL | Legacy binary flag: NULL=unevaluated, TRUE=`score >= threshold` |
| relevance_score | REAL | NULLABLE | Continuous score from the relevance engine |
| relevance_class | TEXT | NULLABLE | **Three-class decision** (`in-scope` / `out-of-scope` / `hybrid`); NULL = unevaluated. Added by migration **v4**; written by `src/relevance.py` (`evaluate_corpus(store=True)`). |
| pdf_url | TEXT | NULLABLE | PDF location (added by migration v2) |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Insertion timestamp |
| updated_at | TEXT | NOT NULL, DEFAULT current_timestamp | Last update timestamp |

### `paper_queries` (junction)
| Column | Type | Constraints | Description |
|---|---|---|---|
| paper_id | INTEGER | PK, FK → papers(id) ON DELETE CASCADE | Paper reference |
| query_id | INTEGER | PK, FK → queries(id) ON DELETE CASCADE | Query reference |

### `paper_sources` (junction)
| Column | Type | Constraints | Description |
|---|---|---|---|
| paper_id | INTEGER | PK, FK → papers(id) ON DELETE CASCADE | Paper reference |
| source_id | INTEGER | PK, FK → sources(id) ON DELETE CASCADE | Source reference |

### `snowball_edges`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| child_paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Discovered paper |
| parent_paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Source paper |
| depth | INTEGER | NOT NULL, DEFAULT 1 | Snowball depth |
| discovered_at | TEXT | NOT NULL, DEFAULT current_timestamp | When discovered |

**Composite UNIQUE constraint:** `(child_paper_id, parent_paper_id)` prevents duplicate edges.

### `relevance_evals`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Paper reference |
| method | TEXT | NOT NULL | 'keyword', 'bm25', 'hybrid', or 'llm' |
| score | REAL | NOT NULL | Relevance score |
| is_relevant | BOOLEAN | NOT NULL | Derived from threshold (legacy binary) |
| threshold | REAL | NOT NULL | Threshold used |
| decision | TEXT | NULLABLE | Three-class decision for this raw score (`in-scope` / `out-of-scope` / `hybrid`). Added by migration **v4**. Note: the *authoritative* per-run decisions live in `evals`; `relevance_evals` is raw-score detail only (see `docs/evaluation.md` §5). |
| details | TEXT | NULLABLE | JSON with matched terms, etc. |
| evaluated_at | TEXT | NOT NULL, DEFAULT current_timestamp | Evaluation timestamp |

### `runs`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| run_at | TEXT | NOT NULL, DEFAULT current_timestamp | Run timestamp |
| model | TEXT | NOT NULL | Model identifier |
| prompt_text | TEXT | NOT NULL | System prompt used |
| misc | TEXT | NOT NULL | Free-form metadata (JSON recommended) |

### `decisions`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| run_id | INTEGER | NOT NULL, FK → runs(id) | Run reference |
| paper_id | INTEGER | NOT NULL, FK → papers(id) | Paper reference |
| decision | TEXT | NOT NULL | 'REVIEW' or 'EXCLUDE' |
| criterion | TEXT | NOT NULL | Criterion applied |
| justification | TEXT | NOT NULL | Model reasoning |
| excerpt | TEXT | NOT NULL | Text excerpt from abstract |
| excerpt_verified | BOOLEAN | NOT NULL | Whether excerpt found in abstract |
| tokens_used | INT | NOT NULL | Token count for this paper |

### `ground_truth`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Paper reference |
| annotator_id | TEXT | NOT NULL | Stable id per human annotator (e.g. 'A') |
| label | TEXT | NOT NULL, CHECK in ('in-scope','out-of-scope','hybrid') | Gold label |
| confidence | REAL | NULLABLE | Annotator confidence 0–1 |
| rationale | TEXT | NULLABLE | One-line justification |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

**Composite UNIQUE constraint:** `(paper_id, annotator_id)` — re-ingest updates in place.

### `ground_truth_consensus`
| Column | Type | Constraints | Description |
|---|---|---|---|
| paper_id | INTEGER | PK, FK → papers(id) ON DELETE CASCADE | Paper reference |
| consensus_label | TEXT | NOT NULL, CHECK in (... ,'disagree') | Agreed label, or 'disagree' if annotators clash |
| n_annotators | INTEGER | NOT NULL | Number of annotators for this paper |
| n_agree | INTEGER | NOT NULL | Size of the majority |
| method | TEXT | NULLABLE | 'unanimous' or 'majority' |
| notes | TEXT | NULLABLE | Free-form notes |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

Recomputed on every ground-truth ingest (see `src/eval_store.ingest_ground_truth`).

### `eval_runs`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| method | TEXT | NOT NULL | 'baseline_keyword' \| 'baseline_bm25' \| 'baseline_hybrid' \| 'embedding' \| 'llm' |
| model | TEXT | NOT NULL | Model id, or 'deterministic' |
| model_version | TEXT | NOT NULL DEFAULT '' | Model version string |
| prompt_id | TEXT | NOT NULL DEFAULT '' | 'P1' \| 'P2' \| 'P3' \| 'n/a' |
| temperature | REAL | NOT NULL DEFAULT 0.0 | Sampling temperature |
| run_index | INTEGER | NOT NULL DEFAULT 1 | Repetition index |
| config_hash | TEXT | NULLABLE | Hash of prompt + params (reproducibility) |
| notes | TEXT | NULLABLE | Free-form notes |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

**Composite UNIQUE constraint:** `(method, model, model_version, prompt_id, temperature, run_index)` — makes re-ingest idempotent.

### `evals`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| run_id | INTEGER | NOT NULL, FK → eval_runs(id) ON DELETE CASCADE | Run reference |
| paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Paper reference |
| decision | TEXT | NOT NULL, CHECK in ('in-scope','out-of-scope','hybrid') | Three-class label |
| score | REAL | NULLABLE | Continuous score (e.g. relevance) |
| confidence | REAL | NULLABLE | Model confidence 0–1 |
| rationale | TEXT | NULLABLE | Justification |
| matched_keywords | TEXT | NULLABLE | JSON list (baselines) |
| latency_ms | INTEGER | NULLABLE | Latency |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

**Composite UNIQUE constraint:** `(run_id, paper_id)` — upsert on re-ingest.

### `llm_judge`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| eval_id | INTEGER | NOT NULL, FK → evals(id) ON DELETE CASCADE | Eval being judged |
| judge_model | TEXT | NOT NULL | Judge model id |
| judge_prompt_id | TEXT | NOT NULL DEFAULT '' | Judge prompt id |
| score | REAL | NULLABLE | Quality score |
| verdict | TEXT | NULLABLE | e.g. 'consistent' / 'inconsistent' |
| rationale | TEXT | NULLABLE | Judge reasoning |
| created_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

### `analysis_runs`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| name | TEXT | NOT NULL, UNIQUE | Analysis name (upsert key, e.g. `analysis_all`) |
| generated_at | TEXT | NOT NULL, DEFAULT current_timestamp | When the analysis ran |
| result_json | TEXT | NOT NULL | The full statistics payload as JSON |

Written by the MCP `store_analysis` tool (or `AnalysisClient.store`) and read via
`list_analysis`; added by migration **v5**. See [`docs/analysis.md`](analysis.md).

### `reference_lists`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| parent_paper_id | INTEGER | NOT NULL, FK → papers(id) ON DELETE CASCADE | Paper whose bibliography this row came from |
| direction | TEXT | NOT NULL DEFAULT 'backward', CHECK in ('backward','forward') | Snowball direction |
| ref_index | INTEGER | NULLABLE | Position in the bibliography |
| ref_doi / ref_title / ref_year / ref_authors / ref_unstructured | TEXT/INTEGER | NULLABLE | Parsed reference fields |
| resolved_paper_id | INTEGER | NULLABLE, FK → papers(id) ON DELETE SET NULL | Paper this reference resolved to |
| source | TEXT | NULLABLE | Where the reference metadata came from (e.g. Crossref) |
| status | TEXT | NOT NULL DEFAULT 'pending', CHECK in ('pending','resolved','unresolved_no_doi','unresolved_title_failed','fetch_error') | Assured-retrieval accounting; added by migration **v3**, plotted by `puf analyze snowball` |
| discovered_at | TEXT | NOT NULL, DEFAULT current_timestamp | Timestamp |

**Composite UNIQUE index** `uq_reference_lists`: `(parent_paper_id, direction, COALESCE(ref_doi,''), COALESCE(ref_unstructured,''))`.

### `snowball_runs`
| Column | Type | Constraints | Description |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | Primary key |
| direction | TEXT | NULLABLE | `backward` / `forward` for a harvest run; `resolve` for a `--resolve-only` (Phase-2) run |
| source | TEXT | NULLABLE | API used (e.g. Crossref / Semantic Scholar) |
| seed_count | INTEGER | NULLABLE | Seed papers processed |
| references_harvested | INTEGER | NOT NULL DEFAULT 0 | References written to `reference_lists` |
| new_papers | INTEGER | NOT NULL DEFAULT 0 | Papers newly inserted |
| edges | INTEGER | NOT NULL DEFAULT 0 | `snowball_edges` rows created |
| api_calls | INTEGER | NOT NULL DEFAULT 0 | API calls issued |
| started_at / finished_at | TEXT | NOT NULL DEFAULT current_timestamp / NULLABLE | Run window |
| note | TEXT | NULLABLE | Free-form note |

Added (with `reference_lists`) by migration **v2**.

### Migration history (`schema_migrations`)
| Version | Name | Adds |
|---|---|---|
| 1 | `add_papers_notes_column` | `papers.notes` |
| 2 | `add_reference_lists_runs_pdf` | `reference_lists`, `snowball_runs`, `papers.pdf_url` |
| 3 | `add_reference_lists_status` | `reference_lists.status` |
| 4 | `add_relevance_class` | `papers.relevance_class`, `relevance_evals.decision` |
| 5 | `add_analysis_runs` | `analysis_runs` |

## Index Recommendations

For large corpora (>10K papers), consider adding indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_relevance ON papers(is_relevant, relevance_score);
CREATE INDEX IF NOT EXISTS idx_snowball_child ON snowball_edges(child_paper_id);
CREATE INDEX IF NOT EXISTS idx_snowball_parent ON snowball_edges(parent_paper_id);
CREATE INDEX IF NOT EXISTS idx_relevance_paper ON relevance_evals(paper_id);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_papers_relevance_class ON papers(relevance_class);
```
