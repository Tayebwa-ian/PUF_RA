# Relevance Engine Documentation

## Overview

The relevance engine scores papers against the topic:

> *"Physical attacks on Physical Unclonable Functions (PUFs)"*

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
2. **Keyword matching:** Count exact matches of topic keywords in the abstract. Weight by category. Avoid double-counting overlapping matches.
3. **BM25 scoring:** Build a corpus from all abstracts. Compute BM25 score of each abstract against the concatenated topic keyword set.
4. **Composite score:** `score = 0.4 * normalized_keyword_score + 0.6 * BM25_score`
5. **Threshold:** `is_relevant = score >= 0.15` (configurable).

### BM25 Parameters

- `k1 = 1.5` (term frequency saturation)
- `b = 0.75` (length normalisation)

## Usage

```bash
# Evaluate all papers
puf relevance evaluate --threshold 0.15

# Evaluate with BM25 only
puf relevance evaluate --method bm25 --threshold 0.15

# Evaluate single paper
puf relevance paper 42 --method hybrid
```

## Interpreting Scores

| Score range | Interpretation |
|---|---|
| 0.00 – 0.05 | Irrelevant (no topic terms found) |
| 0.05 – 0.15 | Borderline (some terms, weak match) |
| 0.15 – 0.30 | Relevant (clear topic match) |
| 0.30+ | Highly relevant (strong topic focus) |

## Tuning

To adjust relevance sensitivity:
- Increase `--threshold` for higher precision (fewer papers marked relevant).
- Decrease `--threshold` for higher recall (more papers marked relevant).
- Adjust `--keyword-weight` and `--bm25-weight` to change method preference.

## Extending Keywords

Edit `src/relevance.py` and modify `TOPIC_KEYWORDS`. Add new categories or keywords as needed for your specific research question.
