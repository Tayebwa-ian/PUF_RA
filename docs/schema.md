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
