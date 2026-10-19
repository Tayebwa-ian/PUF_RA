# Prewritten SQL Statements for Analyst Agent

This file contains prewritten, tested SQL statements that support the analyst agent in performing comprehensive analysis of the PUF_RA database. Each statement is carefully crafted to be read-only and efficient.

## 1. Corpus by Source

**Purpose**: Count distinct papers per source (e.g., snowball, query1:ACM, query2:IEEE).

```sql
SELECT s.name AS source, COUNT(DISTINCT ps.paper_id) AS n_papers 
FROM sources s 
JOIN paper_sources ps ON ps.source_id = s.id 
GROUP BY s.name 
ORDER BY n_papers DESC;
```

**Expected Output**: List of sources with paper counts, ordered descending.

## 2. Corpus by Year

**Purpose**: Count papers per publication year (excluding papers with year=0/unknown).

```sql
SELECT year, COUNT(*) AS n_papers 
FROM papers 
WHERE year > 0 
GROUP BY year 
ORDER BY year;
```

**Expected Output**: List of years with paper counts, ordered by year.

## 3. Relevance Distribution

**Purpose**: Count papers per relevance class (in-scope, out-of-scope, hybrid, unlabeled).

```sql
SELECT COALESCE(relevance_class, 'unlabeled') AS relevance_class, 
       COUNT(*) AS n_papers 
FROM papers 
GROUP BY relevance_class 
ORDER BY n_papers DESC;
```

**Expected Output**: List of relevance classes with paper counts, ordered descending.

## 4. Snowball Status Counts

**Purpose**: Count reference-lists by status (resolved, unresolved_no_doi, pending, unresolved_title_failed, fetch_error).

```sql
SELECT status, COUNT(*) AS n 
FROM reference_lists 
GROUP BY status 
ORDER BY n DESC;
```

**Expected Output**: List of statuses with count, ordered descending.

## 5. Snowball Edges Count

**Purpose**: Count total snowball edges (parent-child relationships).

```sql
SELECT COUNT(*) AS n FROM snowball_edges;
```

**Expected Output**: Single integer representing total edges.

## 6. Snowball Discovered Count

**Purpose**: Count distinct papers discovered via snowballing.

```sql
SELECT COUNT(DISTINCT child_paper_id) AS n FROM snowball_edges;
```

**Expected Output**: Single integer representing distinct discovered papers.

## 7. Eval Runs List

**Purpose**: List all evaluation runs with their metadata.

```sql
SELECT id, method, model, prompt_id, temperature, run_index 
FROM eval_runs 
ORDER BY id;
```

**Expected Output**: List of eval runs with method, model, prompt_id, temperature, run_index.

## 8. Ground Truth Consensus Count

**Purpose**: Count papers with ground truth consensus labels.

```sql
SELECT COUNT(*) AS n FROM ground_truth_consensus;
```

**Expected Output**: Single integer representing consensus paper count.

## 9. Evals Count

**Purpose**: Count total evaluation records.

```sql
SELECT COUNT(*) AS n FROM evals;
```

**Expected Output**: Single integer representing total eval records.

## 10. Corpus by Method Distinct Papers

**Purpose**: Count distinct papers per discovery method category (snowball, database_query, other).

```sql
SELECT 
    CASE 
        WHEN lower(s.name) LIKE '%snowball%' THEN 'snowball' 
        WHEN lower(s.name) LIKE 'query%' 
          OR lower(s.name) IN ('acm', 'ieee', 'springer', 'semantic scholar') 
        THEN 'database_query' 
        ELSE 'other' 
    END AS category, 
    COUNT(DISTINCT ps.paper_id) AS n_papers 
FROM sources s 
JOIN paper_sources ps ON ps.source_id = s.id 
GROUP BY category 
ORDER BY n_papers DESC;
```

**Expected Output**: List of categories (snowball, database_query) with distinct paper counts.

## 11. Corpus Multi-Method Papers

**Purpose**: Count papers found by multiple methods (overlap between sources).

```sql
SELECT COUNT(*) AS n 
FROM (
    SELECT paper_id 
    FROM paper_sources 
    GROUP BY paper_id 
    HAVING COUNT(DISTINCT source_id) > 1
);
```

**Expected Output**: Single integer representing multi-method papers count.

## 12. Corpus Distinct Papers Total

**Purpose**: Count total distinct papers with at least one source.

```sql
SELECT COUNT(DISTINCT paper_id) AS n FROM paper_sources;
```

**Expected Output**: Single integer representing total distinct papers.

## 13. Binary Classification Distribution (Include vs Exclude)

**Purpose**: Count papers by binary classification (include = in-scope, exclude = out-of-scope + hybrid).

```sql
SELECT 
    CASE 
        WHEN relevance_class = 'in-scope' THEN 'include'
        ELSE 'exclude'
    END AS binary_class,
    COUNT(*) AS n_papers
FROM papers
GROUP BY binary_class;
```

**Expected Output**: Two rows: include (in-scope count) and exclude (out-of-scope + hybrid count).

## 14. Per-Method Classification Distribution

**Purpose**: Count decisions per method from relevance_evals table.

```sql
SELECT method, decision, COUNT(*) AS n_papers
FROM relevance_evals
GROUP BY method, decision
ORDER BY method, decision;
```

**Expected Output**: List of methods (keyword, bm25, hybrid, embedding) with decisions (in-scope, out-of-scope, hybrid) and counts.

## 15. Ground Truth Consensus Labels Distribution

**Purpose**: Count papers per consensus label (in-scope, out-of-scope, hybrid, disagree).

```sql
SELECT consensus_label, COUNT(*) AS n_papers
FROM ground_truth_consensus
GROUP BY consensus_label;
```

**Expected Output**: List of consensus labels with paper counts.
