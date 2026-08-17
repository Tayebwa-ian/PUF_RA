# Schema Documentation — PUF Research Pipeline

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
| is_relevant | BOOLEAN | DEFAULT NULL | NULL=unevaluated, TRUE=relevant, FALSE=irrelevant |
| relevance_score | REAL | NULLABLE | Score from relevance engine |
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
| method | TEXT | NOT NULL | 'keyword', 'bm25', or 'llm' |
| score | REAL | NOT NULL | Relevance score |
| is_relevant | BOOLEAN | NOT NULL | Derived from threshold |
| threshold | REAL | NOT NULL | Threshold used |
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
| method | TEXT | NOT NULL | 'baseline_keyword' \| 'baseline_bm25' \| 'baseline_hybrid' \| 'sbert' \| 'llm' |
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
```
