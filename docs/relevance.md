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
with SBERT and the LLM prompts. The **SBERT embedding baseline is first-class**
— `puf relevance sbert` runs it (default model `all-MiniLM-L6-v2`) and emits
3-class decisions into the same tables.

## Methodology

## Baseline Methods Overview

The relevance engine supports three categories of screening methods, all of which assign each paper one of the three classes `in-scope` / `out-of-scope` / `hybrid`:

1. **Deterministic baselines**: `keyword`, `bm25`, and `hybrid` (a weighted combination of keyword + BM25 scoring) implemented in `src/relevance.py`.
2. **SBERT embedding baseline**: Implemented in `src/baselines.py`, using `sentence-transformers` to compute cosine similarity between abstract embeddings and a topic sentence.
3. **LLM-based screening**: 9 configurations (3 prompts × 3 LLMs) configured via `config/eval_models.json`.

### Why SBERT Was Added

The deterministic baselines (keyword/BM25/hybrid) rely on **lexical matching**: they look for exact words, phrases, or term frequency patterns in the text. While effective for papers that use the curated topic keywords, they may fail to identify papers that discuss physical attacks on PUFs using synonyms, related concepts, or different phrasing (e.g., a paper that says "hardware-based security primitives" instead of "physical unclonable functions").

The **SBERT (Sentence-BERT) embedding baseline** was added to address these limitations by providing **semantic understanding**:

- **Semantic embeddings**: SBERT generates dense vector embeddings that capture the semantic meaning of the text, not just lexical overlap.
- **Cosine similarity**: It computes the cosine similarity between the abstract embedding and a topic sentence embedding, identifying papers that are semantically related to the topic, even if they don't contain the exact keywords.
- **Distinguishing the 3 classes**: This is particularly important for the 3-class labeling task:
  - `in-scope`: physical attacks (side-channel, fault injection, invasive/semi-invasive)
  - `out-of-scope`: ML/modeling attacks (logical, non-invasive CRP-based attacks)
  - `hybrid`: side-channel + ML

SBERT helps distinguish between these classes by understanding the semantic context of the abstract, whereas keyword/BM25 might misclassify a paper if it uses different terminology. This addresses **Research Question 4 (RQ4)**: *"Does an embedding (SBERT) baseline help where keyword/BM25 fails (e.g. paraphrase, hybrid cases)?"*

### SBERT Embedding Baseline Details

**How it works:**
1. It defines a `TOPIC_SENTENCE`: 
   ```
   physical attack on physically unclonable function side-channel analysis fault injection invasive semi-invasive probing
   ```
2. For each paper in the corpus, it encodes the abstract (or an empty string if no abstract exists) and the `TOPIC_SENTENCE` using the SBERT model (`sentence-transformers` library).
3. It computes the **cosine similarity** between the abstract embedding and the topic sentence embedding using `torch.nn.functional.cosine_similarity`.
4. The resulting score is a floating-point value (typically in the range `[-1.0, 1.0]`, though SBERT cosine scores may be negative or outside the `[0, 1]` band).
5. The `_classify` function maps the score and threshold to one of the 3 classes: `in-scope`, `out-of-scope`, or `hybrid`.

**First-class status:**
The SBERT baseline is a **first-class, non-optional method** of the study. Its dependencies (`sentence-transformers`, `torch`) are installed, and `puf relevance sbert` runs it (default model `all-MiniLM-L6-v2`) and emits 3-class decisions into the same `eval_runs` / `evals` tables as the deterministic baselines and LLM configurations.

### Threshold Derivation for All Baselines

Just like the deterministic baselines, the SBERT baseline's threshold is **derived from the ground truth** using the same optimization criteria:
- `derive_threshold(conn, method="sbert", criterion="f1")` finds the threshold that maximizes the F1 score.
- `derive_threshold(conn, method="sbert", criterion="youden")` finds the threshold that maximizes Youden's index.

The `derive_threshold` function:
1. Calls `sbert_scores(conn, model_name)` to get the list of `(paper_id, cosine_similarity)` for every paper.
2. Builds a candidate threshold grid that covers the **OBSERVED score range** (since SBERT cosine scores may be negative or outside the `[0, 1]` band, the grid is dynamically generated based on the min/max observed scores).
3. Evaluates each candidate threshold against the `ground_truth_consensus` labels to find the optimal one.

**Fallback threshold:**
If no usable ground truth exists (i.e., `ground_truth_consensus` is empty or unusable), the system falls back to a configurable default threshold:
- `0.15` for keyword/BM25/hybrid methods.
- **`0.3` for SBERT** (as documented in `docs/relevance.md` and `docs/evaluation.md`).

This fallback is explicitly documented: an explicit `--threshold` argument always overrides the auto-derived threshold, but when `--threshold` is not provided and no consensus labels exist, `0.3` is used for SBERT.

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
   `puf relevance sbert`, so the same threshold that was optimised against the
   curated labels is the one applied to the corpus. The sweep covers the `[0, 1]`
   band **and** the observed score range (SBERT's observed score range may include
   negative values or values outside the `[0, 1]` band) and maximises F1 (or
   sensitivity+specificity−1) against `ground_truth_consensus`, with `in-scope`/`hybrid`
   as positives, `out-of-scope` as negatives and `disagree`
   skipped. It returns
   `None` (→ keep the default `0.15` for keyword/BM25/hybrid methods, `0.3` for SBERT)
   when there is no usable ground truth, and it never writes to
   the database (scores come from `_scores_for_corpus`, or from
   `sentence_transformers` for the SBERT branch). An explicit `--threshold` always
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

# SBERT baseline (first-class, all-MiniLM-L6-v2) -> 3-class decisions
# (threshold also auto-derived from ground truth when present)
puf relevance sbert
```

The deterministic baseline and SBERT both land in `eval_runs` / `evals`; analyse
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
  default (or `0.3` for SBERT) is only the fallback for corpora without consensus
  labels, and `evaluate`/`baseline`/`sbert` already auto-derive it. An explicit
  `--threshold` still overrides the derived value.
- The 9 LLM methods (`config/eval_models.json`) are **threshold-free**: they return
  `in-scope`/`out-of-scope`/`hybrid` directly from the prompt, so no cut-off applies.
- Adjust `--keyword-weight` and `--bm25-weight` to change method preference.

## Extending Keywords

Edit `src/relevance.py` and modify `TOPIC_KEYWORDS`. Add new categories or keywords as needed for your specific research question.
