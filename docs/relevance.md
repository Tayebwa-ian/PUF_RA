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
   band **and** the observed score range (SBERT cosine scores may be negative,
   so a negative cut-off is reachable) and maximises F1 (or sensitivity+specificity−1) against `ground_truth_consensus`,
   with `in-scope`/`hybrid` as positives, `out-of-scope` as negatives and `disagree`
   skipped. It returns `None` (→ keep the default `0.15` for keyword/BM25/hybrid,
   `0.3` for SBERT) when there is no usable ground truth, and it never writes to
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
