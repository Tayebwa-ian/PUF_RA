# Database Migration Strategy

The PUF_RA schema evolves over the course of the study. This document describes the forward, idempotent, **data-preserving** migration framework implemented in `src/db_schema.py`, so that the database can never lose user data when the design changes.

## Guiding principles

1. **Forward-only / additive migrations.** Every migration advances the schema to a higher version. Migrations only *add* structure: `ALTER TABLE ... ADD COLUMN` or `CREATE TABLE`. They never drop columns, drop tables, or delete rows. Because nothing is removed, previously stored data is always retained.
2. **Idempotency.** Each migration's `up` function checks whether its change is already present (e.g. via `PRAGMA table_info`) before issuing DDL, and the applier skips any version already recorded in `schema_migrations`. The whole framework can be run any number of times without error or duplicated work.
3. **Explicit version tracking.** A `schema_migrations` table records every applied `(version, name, applied_at)`. `get_schema_version(conn)` returns the highest applied version (0 if none).

## The `schema_migrations` version table

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT current_timestamp
);
```

This table is part of `SCHEMA_STATEMENTS`, so a fresh database created with `create_schema` already contains it. For existing databases, `apply_migrations` creates it on demand before doing anything else.

## How to add a new migration

1. Write an **additive** `up` function that is safe to re-run. Guard each DDL statement so it only executes if the target does not already exist:

    ```python
    @register_migration(2, "add_evals_latency_bucket")
    def up(conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(evals)")}
        if "latency_bucket" not in cols:
            conn.execute("ALTER TABLE evals ADD COLUMN latency_bucket TEXT;")
    ```

2. Use the **next** integer version (current max + 1). Registrations are sorted and applied in ascending order, so versions must be unique and monotonic.
3. That is it. The migration is auto-discovered from the `MIGRATIONS` registry and applied automatically by `apply_migrations` / `ensure_schema`.

## Applying migrations

- `apply_migrations(conn, up_to=None)` -- ensures `schema_migrations` exists, then applies every pending migration (skipping already-applied versions) in ascending order, recording each, and commits. Pass `up_to` to cap the version (inclusive). Returns the list of versions actually applied.
- `ensure_schema(conn)` -- calls `create_schema(conn)` (base v2 schema) and then `apply_migrations(conn)`. Use this instead of `create_schema` when you want a fully-up-to-date schema.
- `get_applied_versions(conn)` / `get_schema_version(conn)` -- introspection.

## Example migration

Version `1`, `add_papers_notes_column`, adds an optional `notes TEXT` column to `papers`. It is guarded by a `PRAGMA table_info(papers)` check, so re-running is safe and no existing paper rows are touched.

```python
@register_migration(1, "add_papers_notes_column")
def _migration_1_add_papers_notes_column(conn: sqlite3.Connection) -> None:
    """Example forward migration: add an optional notes column to papers."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "notes" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN notes TEXT;")
```

## Migration history (v1-v6)

| Version | Name | Adds |
|---|---|---|
| 1 | `add_papers_notes_column` | `papers.notes` |
| 2 | `add_reference_lists_runs_pdf` | `reference_lists`, `snowball_runs`, `papers.pdf_url` |
| 3 | `add_reference_lists_status` | `reference_lists.status` |
| 4 | `add_relevance_class` | `papers.relevance_class`, `relevance_evals.decision` |
| 5 | `add_analysis_runs` | `analysis_runs` |
| 6 | `add_paper_embeddings` | `paper_embeddings` table |

## Migrating from v1 to v2 Schema (Legacy Migration)

The v1 schema had 4 tables: `queries`, `papers` (with `query_id`), `runs`, `decisions`. The v2 schema normalizes relationships using junction tables and adds new tables.

The earlier one-off `migrate_from_v1(conn, dry_run=False)` remains available for converting legacy v1 databases (the pre-junction-table schema) into the current v2 schema. It is a separate, one-time transform and is not part of the version-tracked forward framework described above.

### What the migration does

1. Creates all v2 tables (`sources`, `paper_sources`, `snowball_edges`, `relevance_evals`, etc.).
2. Copies data from v1 tables into v2 tables.
3. Populates `paper_queries` from the legacy `papers.query_id` column.
4. Infers sources from query platform names (e.g., 'IEEE Xplore' → 'IEEE').
5. Leaves old `query_id` column on `papers` for reference (not dropped).

### Automated Migration steps

Run the migration script:

```python
from src.db_schema import migrate_from_v1
from src.db import get_connection

with get_connection("old_results.db") as conn:
    migrate_from_v1(conn, dry_run=False)
```

### Manual Steps After Migration

1. Verify data integrity:

    ```sql
    SELECT COUNT(*) FROM papers;           -- should match v1 count
    SELECT COUNT(*) FROM paper_queries;    -- should equal papers count (if each paper had one query)
    SELECT COUNT(*) FROM sources;          -- should have entries for each platform
    ```

2. Run relevance evaluation:

    ```bash
    puf relevance evaluate --threshold 0.15
    ```

3. Update existing scripts to use new import paths if needed.

### Backup instructions

Always backup your database before migration:

```bash
cp results.db results.db.backup
```

### Dry Run instructions

Test the migration without modifying the database:

```python
with get_connection("old_results.db") as conn:
    migrate_from_v1(conn, dry_run=True)
```
