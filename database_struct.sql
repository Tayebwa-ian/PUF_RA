-- =====================================================================
-- PUF Research Pipeline — SQLite Schema
-- =====================================================================
-- Version: 2.0
-- Date: 2026-07-27
-- Description: Normalized schema for systematic literature review of
--              physical attacks on PUFs. Supports multi-source imports,
--              DOI deduplication, empirical relevance scoring, LLM screening,
--              and snowball/backward search provenance tracking.
--
-- Table of contents:
--   1. sources        — database platforms (ACM, IEEE, Springer, etc.)
--   2. queries        — search queries run on platforms
--   3. papers         — central paper registry (DOI unique)
--   4. paper_queries  — junction: which queries returned which papers
--   5. paper_sources  — junction: which sources provided which papers
--   6. snowball_edges — provenance: parent paper -> child paper references
--   7. relevance_evals— empirical relevance evaluations per paper
--   8. runs           — LLM screening runs
--   9. decisions      — per-paper LLM decisions
-- =====================================================================


-- =====================================================================
-- 1. SOURCES
-- =====================================================================
-- Represents a data source / platform from which papers are retrieved.
-- Examples: 'ACM', 'IEEE', 'Springer', 'Semantic Scholar', 'snowball'.
--
-- Relationship: 1:N with paper_sources
-- =====================================================================
CREATE TABLE sources (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- 'ACM', 'IEEE', 'Springer', 'Semantic Scholar', 'snowball'
    description TEXT
);


-- =====================================================================
-- 2. QUERIES
-- =====================================================================
-- Represents a single search query run on a specific platform.
-- Each row captures the query string and when it was executed.
--
-- Relationship: 1:N with paper_queries
-- =====================================================================
CREATE TABLE queries (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    platform TEXT NOT NULL,             -- e.g. 'ACM Digital Library', 'IEEE Xplore'
    query_text TEXT NOT NULL            -- the Boolean / natural-language query string
);


-- =====================================================================
-- 3. PAPERS
-- =====================================================================
-- Central paper registry. DOI is UNIQUE to prevent duplicates across
-- queries and sources. Papers without a DOI are allowed but may
-- duplicate on re-import (title-based dedup is best-effort).
--
-- Relevance columns:
--   is_relevant     — NULL = not yet evaluated, TRUE = relevant, FALSE = irrelevant
--   relevance_score — continuous score from the relevance engine (higher = more relevant)
--
-- Relationship: 1:N with paper_queries, paper_sources, snowball_edges,
--               relevance_evals, decisions
-- =====================================================================
CREATE TABLE papers (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER NOT NULL,
    abstract TEXT NOT NULL,
    publication_title TEXT NOT NULL,
    doi TEXT UNIQUE,                    -- NULL allowed; UNIQUE constraint where not NULL
    keywords TEXT,
    is_relevant BOOLEAN DEFAULT NULL,   -- NULL = not yet evaluated
    relevance_score REAL,               -- empirical score from relevance engine
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    updated_at TEXT NOT NULL DEFAULT current_timestamp
);


-- =====================================================================
-- 4. PAPER_QUERIES (junction)
-- =====================================================================
-- Links papers to the queries that returned them.
-- A paper may appear in multiple query results; this table captures that.
--
-- Composite PK ensures a paper is only linked once to a given query.
-- Relationship: N:1 with papers, N:1 with queries
-- =====================================================================
CREATE TABLE paper_queries (
    paper_id INTEGER NOT NULL,
    query_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, query_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
);


-- =====================================================================
-- 5. PAPER_SOURCES (junction)
-- =====================================================================
-- Links papers to the sources that provided them.
-- A paper may be imported from multiple sources (e.g. both ACM and IEEE).
--
-- Relationship: N:1 with papers, N:1 with sources
-- =====================================================================
CREATE TABLE paper_sources (
    paper_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, source_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);


-- =====================================================================
-- 6. SNOWBALL_EDGES
-- =====================================================================
-- Tracks the provenance of papers discovered via backward snowball search.
-- Each row represents "paper B was discovered because paper A cited it".
--
-- Columns:
--   child_paper_id  — the paper that was discovered (the reference)
--   parent_paper_id — the paper whose reference list led to the child
--   depth           — snowball depth (1 = direct reference from seed paper)
--
-- Composite PK (child, parent) prevents duplicate edges and cycles.
-- Relationship: N:1 with papers (as child), N:1 with papers (as parent)
-- =====================================================================
CREATE TABLE snowball_edges (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    child_paper_id INTEGER NOT NULL,
    parent_paper_id INTEGER NOT NULL,
    depth INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (child_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_paper_id) REFERENCES papers(id) ON DELETE CASCADE
);


-- =====================================================================
-- 7. RELEVANCE_EVALS
-- =====================================================================
-- Stores each empirical relevance evaluation run for a paper.
-- Multiple evaluations may exist for the same paper (different methods,
-- thresholds, or re-evaluations).
--
-- Columns:
--   method      — 'keyword', 'bm25', 'llm'
--   score       — continuous relevance score
--   is_relevant — boolean decision based on threshold
--   threshold   — the threshold used for this evaluation
--   details     — JSON blob with matched terms, per-keyword contributions, etc.
--
-- Relationship: N:1 with papers
-- =====================================================================
CREATE TABLE relevance_evals (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    method TEXT NOT NULL,               -- 'keyword', 'bm25', 'llm'
    score REAL NOT NULL,
    is_relevant BOOLEAN NOT NULL,
    threshold REAL NOT NULL,
    details TEXT,                       -- JSON: matched terms, BM25 scores, etc.
    evaluated_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);


-- =====================================================================
-- 8. RUNS
-- =====================================================================
-- Describes a single LLM screening run with its model identifier,
-- system prompt, and miscellaneous metadata.
--
-- Relationship: 1:N with decisions
-- =====================================================================
CREATE TABLE runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    model TEXT NOT NULL,                -- e.g. 'qwen35-397b', 'gpt-4o'
    prompt_text TEXT NOT NULL,          -- the system prompt used
    misc TEXT NOT NULL                  -- free-form metadata (JSON recommended)
);


-- =====================================================================
-- 9. DECISIONS
-- =====================================================================
-- Stores the LLM's REVIEW / EXCLUDE decision for a single paper
-- within a given run. Includes the model's justification, the excerpt
-- it cited from the abstract, and token usage.
--
-- Relationship: N:1 with runs, N:1 with papers
-- =====================================================================
CREATE TABLE decisions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    decision TEXT NOT NULL,             -- 'REVIEW' or 'EXCLUDE'
    criterion TEXT NOT NULL,            -- the criterion applied
    justification TEXT NOT NULL,        -- model's reasoning
    excerpt TEXT NOT NULL,              -- text excerpt from abstract supporting decision
    excerpt_verified BOOLEAN NOT NULL,  -- whether excerpt was found in abstract
    tokens_used INT NOT NULL,           -- total tokens used for this paper
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);


-- =====================================================================
-- 10. GROUND_TRUTH
-- =====================================================================
-- Human-curated gold-standard labels. One row per (paper, annotator).
-- Labels are exactly: 'in-scope' (physical attack on a PUF),
-- 'out-of-scope' (e.g. ML/modeling attack), 'hybrid' (side-channel + ML).
--
-- Relationship: N:1 with papers
-- =====================================================================
CREATE TABLE ground_truth (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    annotator_id TEXT NOT NULL,
    label TEXT NOT NULL
        CHECK (label IN ('in-scope', 'out-of-scope', 'hybrid')),
    confidence REAL,
    rationale TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (paper_id, annotator_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);


-- =====================================================================
-- 11. GROUND_TRUTH_CONSENSUS
-- =====================================================================
-- Aggregated gold standard: one row per paper after combining annotators.
-- `consensus_label` is 'in-scope' / 'out-of-scope' / 'hybrid', or 'disagree'
-- when annotators do not agree (flagged for adjudication).
--
-- Relationship: 1:1 with papers
-- =====================================================================
CREATE TABLE ground_truth_consensus (
    paper_id INTEGER NOT NULL PRIMARY KEY,
    consensus_label TEXT NOT NULL
        CHECK (consensus_label IN ('in-scope', 'out-of-scope', 'hybrid', 'disagree')),
    n_annotators INTEGER NOT NULL,
    n_agree INTEGER NOT NULL,
    method TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);


-- =====================================================================
-- 12. EVAL_RUNS
-- =====================================================================
-- Groups a set of evaluations produced by one method / model / prompt /
-- temperature / repetition. A deterministic run key is derived from these
-- fields so re-ingesting the same JSONL is idempotent.
--
-- Relationship: 1:N with evals
-- =====================================================================
CREATE TABLE eval_runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL,                -- 'baseline_keyword'|'baseline_bm25'|'baseline_hybrid'|'sbert'|'llm'
    model TEXT NOT NULL,                 -- model id, or 'deterministic'
    model_version TEXT NOT NULL DEFAULT '',
    prompt_id TEXT NOT NULL DEFAULT '', -- 'P1'|'P2'|'P3'|'n/a'
    temperature REAL NOT NULL DEFAULT 0.0,
    run_index INTEGER NOT NULL DEFAULT 1,
    config_hash TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (method, model, model_version, prompt_id, temperature, run_index)
);


-- =====================================================================
-- 13. EVALS
-- =====================================================================
-- One row per paper evaluation produced by a method/run. The `decision`
-- uses the SAME three-class label scheme as ground truth so metrics align.
--
-- Relationship: N:1 with eval_runs, N:1 with papers
-- =====================================================================
CREATE TABLE evals (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('in-scope', 'out-of-scope', 'hybrid')),
    score REAL,
    confidence REAL,
    rationale TEXT,
    matched_keywords TEXT,               -- JSON list (baselines)
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (run_id, paper_id),
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);


-- =====================================================================
-- 14. LLM_JUDGE
-- =====================================================================
-- Optional LLM-as-judge scores on individual evaluations. Used to audit
-- the *quality* of rationales/decisions (with bias controls in code).
--
-- Relationship: N:1 with evals
-- =====================================================================
CREATE TABLE llm_judge (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    eval_id INTEGER NOT NULL,
    judge_model TEXT NOT NULL,
    judge_prompt_id TEXT NOT NULL DEFAULT '',
    score REAL,
    verdict TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL DEFAULT current_timestamp,
    UNIQUE (eval_id, judge_model, judge_prompt_id),
    FOREIGN KEY (eval_id) REFERENCES evals(id) ON DELETE CASCADE
);


-- =====================================================================
-- 15. PAPER_EMBEDDINGS
-- =====================================================================
-- Stores dense vector embeddings for papers to avoid recomputation during
-- threshold setting and classification. Embeddings are stored per paper and
-- model, with the embedding_vector stored as a JSON array of floats.
--
-- Relationship: N:1 with papers
-- =====================================================================
CREATE TABLE paper_embeddings (
    paper_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    embedding_vector TEXT NOT NULL,    -- JSON array of floats
    computed_at TEXT NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (paper_id, model_name),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
