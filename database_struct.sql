# Table that contains a single query (Boolean search string) run on a paper database (IEEExplore, ACM).
# Used to associate found papers with a given query.
CREATE TABLE queries (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    platform TEXT NOT NULL,
    query_text TEXT NOT NULL
)

# Table that contains information on a paper in the paper pool.
# The "human_decision" is used for manual "REVIEW" or "EXCLUDE" assignment (for evaluation purposes).
CREATE TABLE papers (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER NOT NULL,
    abstract TEXT NOT NULL,
    publication_title TEXT NOT NULL,
    doi TEXT,
    keywords TEXT,
    human_decision TEXT,
    FOREIGN KEY (query_id) REFERENCES queries (id)
);

# Table that describes a run of LLM screening with the utilized model identifier and system prompt.
CREATE TABLE runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT current_timestamp,
    model TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    misc TEXT NOT NULL
)

# Table that describes one "REVIEW"/"EXCLUDE" decision by a LLM for one paper. It further includes
# the reasoning provided by the model and the amount of tokens used.
CREATE TABLE decisions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    criterion TEXT NOT NULL,
    justification TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    excerpt_verified BOOLEAN NOT NULL,
    tokens_used INT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id),
    FOREIGN KEY (paper_id) REFERENCES papers (id)
)