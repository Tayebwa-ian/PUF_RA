# Migration Guide

## Migrating from v1 to v2 Schema

The v1 schema had 4 tables: `queries`, `papers` (with `query_id`), `runs`, `decisions`.
The v2 schema normalizes relationships using junction tables and adds new tables.

### Automated Migration

Run the migration script:

```python
from src.db_schema import migrate_from_v1
from src.db import get_connection

with get_connection("old_results.db") as conn:
    migrate_from_v1(conn, dry_run=False)
```

### What the Migration Does

1. Creates all v2 tables (`sources`, `paper_sources`, `snowball_edges`, `relevance_evals`, etc.).
2. Copies data from v1 tables into v2 tables.
3. Populates `paper_queries` from the legacy `papers.query_id` column.
4. Infers sources from query platform names (e.g., 'IEEE Xplore' → 'IEEE').
5. Leaves old `query_id` column on `papers` for reference (not dropped).

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

### Backup

Always backup your database before migration:

```bash
cp results.db results.db.backup
```

### Dry Run

Test the migration without modifying the database:

```python
with get_connection("old_results.db") as conn:
    migrate_from_v1(conn, dry_run=True)
```
