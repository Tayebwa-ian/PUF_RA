# Database Migration Strategy

The PUF_RA schema will evolve over the course of the study. This document
describes the forward, idempotent, **data-preserving** migration framework
implemented in `src/db_schema.py`, so that the database can never lose user
data when the design changes.

## Guiding principles

1. **Forward-only / additive migrations.** Every migration advances the schema
   to a higher version. Migrations only *add* structure: `ALTER TABLE ... ADD
   COLUMN` or `CREATE TABLE`. They never drop columns, drop tables, or delete
   rows. Because nothing is removed, previously stored data is always retained.
2. **Idempotency.** Each migration's `up` function checks whether its change is
   already present (e.g. via `PRAGMA table_info`) before issuing DDL, and the
   applier skips any version already recorded in `schema_migrations`. The whole
   framework can be run any number of times without error or duplicated work.
3. **Explicit version tracking.** A `schema_migrations` table records every
   applied `(version, name, applied_at)`. `get_schema_version(conn)` returns the
   highest applied version (0 if none).

## The `schema_migrations` version table

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT current_timestamp
);
```

This table is part of `SCHEMA_STATEMENTS`, so a fresh database created with
`create_schema` already contains it. For existing databases, `apply_migrations`
creates it on demand before doing anything else.

## How to add a new migration

1. Write an **additive** `up` function that is safe to re-run. Guard each DDL
   statement so it only executes if the target does not already exist:

   ```python
   @register_migration(2, "add_evals_latency_bucket")
   def up(conn):
       cols = {r[1] for r in conn.execute("PRAGMA table_info(evals)")}
       if "latency_bucket" not in cols:
           conn.execute("ALTER TABLE evals ADD COLUMN latency_bucket TEXT;")
   ```

2. Use the **next** integer version (current max + 1). Registrations are sorted
   and applied in ascending order, so versions must be unique and monotonic.
3. That is it. The migration is auto-discovered from the `MIGRATIONS` registry
   and applied automatically by `apply_migrations` / `ensure_schema`.

## Applying migrations

- `apply_migrations(conn, up_to=None)` -- ensures `schema_migrations` exists,
  then applies every pending migration (skipping already-applied versions) in
  ascending order, recording each, and commits. Pass `up_to` to cap the version
  (inclusive). Returns the list of versions actually applied.
- `ensure_schema(conn)` -- calls `create_schema(conn)` (base v2 schema) and then
  `apply_migrations(conn)`. Use this instead of `create_schema` when you want a
  fully-up-to-date schema.
- `get_applied_versions(conn)` / `get_schema_version(conn)` -- introspection.

## Example migration

Version `1`, `add_papers_notes_column`, adds an optional `notes TEXT` column to
`papers`. It is guarded by a `PRAGMA table_info(papers)` check, so re-running
is safe and no existing paper rows are touched.

## Legacy v1 databases

The earlier one-off `migrate_from_v1(conn, dry_run=False)` remains available for
converting legacy v1 databases (the pre-junction-table schema) into the current
v2 schema. It is a separate, one-time transform and is not part of the
version-tracked forward framework described above.
