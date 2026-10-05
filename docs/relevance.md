# Relevance Engine Documentation

## Overview

The relevance engine scores papers against the topic:

> *"Physical attacks on Physical Unclonable Functions (PUFs)"*

It is the **deterministic baseline** of the evaluation study (see
[`docs/evaluation.md`](evaluation.md)). Like every method in the study, it
assigns each paper one of the three classes `in-scope` / `out-of-scope` /
`hybrid` (the **hybrid rule**: a physical side-channel attack *combined with* ML
modeling → `hybrid`; pure ML/modeling → `out-of-scope`). The decision threshold
can be **derived from ground truth** with `src.relevance.derive_threshold`
(max-F1 or Youden's J vs `ground_truth_consensus`, exposed as
`puf relevance baseline --derive-threshold`); when no ground truth is available
the configurable default `0.15` applies. The resulting 3-class decisions are
ingested into `eval_runs` / `evals` so they share one `eval_run_id` namespace
with the embedding baseline and the LLM prompts.

## Methodology

## Baseline Methods Overview

The relevance engine supports the following categories of screening methods, all of which assign each paper one of the three classes `in-scope` / `out-of-scope` / `hybrid`:

1. **Deterministic baselines**: `keyword`, `bm25`, and `hybrid` (a weighted combination of keyword + BM25 scoring) implemented in `src/relevance.py`.
2. **Embedding baseline**: Cosine similarity using the Uni Passau `octen-embedding-8b` API via `src.embeddings.get_or_compute_embedding`.

### Uni Passau octen-embedding-8b Embedding Approach

The primary method for storing paper embeddings in the `paper_embeddings` table is the Uni Passau-hosted `octen-embedding-8b` model via an OpenAI-compatible API. This approach:

- **Avoids local dependencies**: Uses a hosted API from Uni Passau to avoid local `sentence-transformers`, `torch`, or `loky` multiprocessing issues in Python 3.14.
- **Endpoint**: `https://llms.innkube.fim.uni-passau.de/v1/embeddings`
- **Model**: `octen-embedding-8b`
- **Document encoding**: Text is prefixed with `- ` for document encoding as per the model's known issue.
- **Storage**: Embeddings are stored systematically in the `paper_embeddings` database table with columns `paper_id`, `model_name`, `embedding_vector` (JSON array of floats), and `computed_at` to avoid recomputation during threshold setting and classification.
- **API key requirement**: The Uni Passau API requires an access key, provided via the `api_key` parameter or the `UNIPASSAU_EMBEDDING_API_KEY` environment variable.

See `src/embeddings.py` for the implementation of `get_or_compute_embedding(conn, paper_id, model_name="octen-embedding-8b", api_key=None)`.

### Embedding Baseline Details

**How it works:**
1. It defines a `TOPIC_SENTENCE`: 
   ```
   physical attack on physically unclonable function side-channel analysis fault injection invasive semi-invasive probing
   ```
2. For each paper in the corpus, it encodes the title + abstract and the `TOPIC_SENTENCE` using the Uni Passau octen-embedding-8b API.
3. It computes the **cosine similarity** between the paper embedding and the topic sentence embedding.
4. The resulting score is a floating-point value (typically in the range `[-1.0, 1.0]`, though cosine scores may be negative or outside the `[0, 1]` band).
5. The `_classify` function maps the score and threshold to one of the 3 classes: `in-scope`, `out-of-scope`, or `hybrid`.

**First-class status:**
The embedding baseline is a **first-class method** of the study. `puf eval baseline --method embedding` runs it (model `octen-embedding-8b`) and emits 3-class decisions into the same `eval_runs` / `evals` tables as the deterministic baselines and LLM configurations.

### Batched Embedding Computation and Caching

To avoid API rate limits and timeout issues when computing embeddings for all 4992 papers, the pipeline uses **batched API calls** and **database caching**:

- **Database caching**: Before computing any embedding via the Uni Passau API, the pipeline checks the `paper_embeddings` table using `get_embedding_from_db(conn, paper_id, model_name)`. If an embedding exists, it is retrieved from the database and **not recomputed**. This ensures that already-embedded papers are never repeated.
- **Batched API calls**: For papers that do not have embeddings in the database, the pipeline computes embeddings in **batches of 64** using the `compute_embeddings_batch(texts, model, api_key)` function in `src/embeddings.py`. This reduces the number of API requests from ~4576 individual calls to ~72 batched calls, significantly reducing API timeout issues and rate limit exposure.
- **Storage**: Each computed embedding is stored in the `paper_embeddings` table immediately after computation via `store_embedding(conn, paper_id, model_name, embedding)`, ensuring the cache is updated for future runs.

This approach ensures that:
1. Already-embedded papers are never recomputed or repeated.
2. API budget and rate limits are respected by minimizing the number of API requests.
3. Embeddings of different papers are separable (verified by cosine similarity checks between different paper embeddings, which show distinct 4096-dimensional vectors with low cosine similarity values between different papers).

### Threshold Derivation for All Baselines

Just like the deterministic baselines, the embedding baseline's threshold is **derived from the ground truth** using the same optimization criteria:
- `derive_threshold(conn, method="hybrid", criterion="f1")` finds the threshold that maximizes the F1 score.
- `derive_threshold(conn, method="hybrid", criterion="youden")` finds the threshold that maximizes Youden's index.

The `derive_threshold` function:
1. Calls `_scores_for_corpus(conn, method)` to get the scores for every paper.
2. Builds a candidate threshold grid that covers the **OBSERVED score range** (since cosine scores may be negative or outside the `[0, 1]` band, the grid is dynamically generated based on the min/max observed scores).
3. Evaluates each candidate threshold against the `ground_truth_consensus` labels to find the optimal one.

**Threshold derivation details for deterministic baselines (keyword/bm25/hybrid):**
- The threshold is derived from `ground_truth_consensus` using max-F1 or Youden's J optimization.
- **Positive class**: `in-scope` or `hybrid` (papers with physical attacks that must be kept).
- **Negative class**: `out-of-scope` (ML/modeling attacks).
- **Disagree rows**: `disagree` rows are skipped (not yet adjudicated).
- **Candidate threshold grid**: Covers both the conventional `[0, 1]` band and the observed score range.
- **Fallback threshold**: `0.15` when no usable ground truth exists.

### Derived Thresholds for Hybrid Method (keyword+BM25)

Based on 50 ground truth consensus rows:

- **max-F1 threshold**: `0.0`
- **Youden's J threshold**: `0.55`

These thresholds are derived from `ground_truth_consensus` using max-F1 or Youden's J optimization. The positive class is `in-scope` or `hybrid` (papers with physical attacks that must be kept), and the negative class is `out-of-scope` (ML/modeling attacks). `disagree` rows are skipped.

### Derived Thresholds for Embedding Baseline (octen-embedding-8b)

Based on 50 ground truth consensus rows (in-scope: 16, out-of-scope: 17, hybrid: 17), with the mapping where positive = `in-scope` and negative = `out-of-scope` ∪ `hybrid`:

- **max-F1 threshold**: `0.62`
- **Youden's J threshold**: `0.62`

These thresholds are derived from `ground_truth_consensus` using max-F1 or Youden's J optimization on the cosine similarity scores computed between the paper's (title + abstract) embedding and the topic sentence embedding using the `octen-embedding-8b` model. The positive class is `in-scope` (pure physical attack, i.e., the papers the screen must keep), and the negative class is `out-of-scope` or `hybrid` (ML/modeling or side-channel + ML, i.e., to be discarded). `disagree` rows are skipped.

**Recommendation:** Use the threshold of `0.62` for the embedding baseline, as both max-F1 and Youden's J optimization yield the same value, indicating it optimally balances precision/recall and sensitivity/specificity for classifying papers as `in-scope` versus `out-of-scope`/`hybrid`.

**Fallback threshold:**
If no usable ground truth exists (i.e., `ground_truth_consensus` is empty or unusable), the system falls back to a configurable default threshold:
- `0.15` for keyword/BM25/hybrid methods.
- **`0.3` for the embedding baseline** using octen-embedding-8b.

This fallback is explicitly documented: an explicit `--threshold` argument always overrides the auto-derived threshold, but when `--threshold` is not provided and no consensus labels exist, `0.3` is used for the embedding baseline.

### Topic Keyword Set

Curated from domain expertise, organised by category:

| Category | Keywords | Weight |
|---|---|---|
| Core | `puf`, `physical unclonable function` | 2.0 |
| Attacks | `power analysis`, `side-channel`, `electromagnetic`, `em analysis`, `probing`, `invasive attack`, `semi-invasive`, `delayering`, `cloning`, `modeling attack`, `machine learning attack`, `fault injection`, `laser`, `focused ion beam`, `fib`, `physical attack` | 3.0 |
| PUF types | `arbiter puf`, `ring oscillator`, `ro puf`, `sram puf`, `bistable ring`, `puf-based`, `memory puf`, `butterfly puf` | 1.5 |

### Scoring Algorithm

1. **Preprocessing:** Lowercase, remove punctuation, normalise whitespace.
2. **Keyword matching:** Count **word-boundary** matches of topic keywords in the
   abstract (`\bkeyword(s|es)?\b`, longest keyword first, overlapping spans
   counted once). Word boundaries matter: bare substring containment made the
   3-letter signal `fib` match "fiber" / "fibrosis" / "Fibonacci"; the optional
   plural keeps "physical attacks" / "PUFs" / "side-channels" matching. Weight
   by category.
3. **BM25 scoring:** Build a corpus from all abstracts. Compute BM25 score of each abstract against the concatenated topic keyword set.
4. **Composite score:** `score = 0.4 * normalized_keyword_score + 0.6 * BM25_score`
5. **Threshold (auto-derived per method from ground truth):** whenever
   `ground_truth_consensus` has rows, the cut-off used by the fall-back branch of
   the 3-class rule is **derived automatically** by
   `derive_threshold(conn, method, criterion="f1"|"youden", step=0.01)` — this is
   done internally by `evaluate_corpus`, `puf relevance baseline` and
   `puf eval baseline --method embedding`, so the same threshold that was optimised against the
   curated labels is the one applied to the corpus. The sweep covers the `[0, 1]`
   band **and** the observed score range (cosine similarities may be negative or
   values outside the `[0, 1]` band) and maximises F1 (or
   sensitivity+specificity−1) against `ground_truth_consensus`, with `in-scope`/`hybrid`
   as positives, `out-of-scope` as negatives and `disagree`
   skipped. It returns
   `None` (→ keep the default `0.15` for keyword/BM25/hybrid methods, `0.3` for embedding)
   when there is no usable ground truth, and it never writes to
   the database (scores come from `_scores_for_corpus`). An explicit `--threshold` always
   overrides the derived value. When storing, the engine writes the 3-class
   `relevance_class` onto `papers` and ingests decisions into `eval_runs` / `evals`;
   with `store=False` nothing is persisted.

### BM25 Parameters

- `k1 = 1.5` (term frequency saturation)
- `b = 0.75` (length normalisation)

## Usage

```bash
# Evaluate the whole corpus with the hybrid baseline. The threshold is
# AUTO-derived from ground truth when curated labels exist; 0.15 is only the
# fallback when there is no usable ground truth.
puf relevance evaluate --method hybrid

# Same, without persisting anything
puf relevance evaluate --method hybrid --no-store

# Evaluate a single paper
puf relevance paper 42 --method hybrid

# Deterministic baseline: threshold is AUTO-derived from ground truth when it
# exists; --derive-threshold forces re-derivation, --threshold overrides it.
puf relevance baseline --method hybrid --derive-threshold [--criterion youden]
puf relevance baseline --method hybrid --threshold 0.2   # explicit override

# Print the ground-truth-derived threshold for every baseline method
puf relevance derive-threshold --method all [--criterion youden]

# Embedding baseline (octen-embedding-8b) -> 3-class decisions
# (threshold also auto-derived from ground truth when present)
puf eval baseline --method embedding --out data/evals/embedding.jsonl
```

The deterministic baseline and embedding baseline both land in `eval_runs` / `evals`; analyse
them together via [`docs/analysis.md`](analysis.md) (e.g. `puf analyze all`).

## Interpreting Scores

The composite score is continuous, but the decision is the 3-class label
(`papers.relevance_class`, also in `evals.decision`):

| Label | Meaning |
|---|---|
| `in-scope` | Physical attack on a PUF (side-channel / fault injection / invasive-semi) |
| `out-of-scope` | Not a physical attack (e.g. pure ML/modeling CRP attack) |
| `hybrid` | Physical side-channel **+** ML/modeling combined |
| (unlabeled) | Not yet evaluated |

The continuous score still orders borderline papers; use it for ranking, not as
a binary gate.

## Tuning

To adjust relevance sensitivity:
- Prefer deriving the threshold from ground truth (`derive_threshold(...)`, run via
  `puf relevance derive-threshold`) over hand-tuning `--threshold`; the `0.15`
  default (or `0.3` for the embedding baseline) is only the fallback for corpora without consensus
  labels, and `evaluate`/`baseline`/`embedding` already auto-derive it. An explicit
  `--threshold` still overrides the derived value.
- The 9 LLM methods (`config/eval_models.json`) are **threshold-free**: they return
  `in-scope`/`out-of-scope`/`hybrid` directly from the prompt, so no cut-off applies.
- Adjust `--keyword-weight` and `--bm25-weight` to change method preference.

## Extending Keywords

Edit `src/relevance.py` and modify `TOPIC_KEYWORDS`. Add new categories or keywords as needed for your specific research question.
