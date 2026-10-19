# LLM Screening Documentation

## Overview

The LLM screening module classifies each paper into the **three-class scheme** used by
the study — `in-scope` (physical attack on a PUF), `out-of-scope` (e.g. ML/modeling
attack), or `hybrid` (side-channel + ML) — using an OpenAI-compatible API. It emits
**structured JSONL** that feeds the evaluation harness (`src/eval_store`).

> This study *evaluates the tools* (LLM prompts) used to build the SoK paper. The
> LLM is a screening aid under evaluation, not a co-author.

These three classes are **shared by every method in the study** — the
deterministic keyword/BM25/hybrid baseline, the embedding baseline (octen-embedding-8b), and the
9 LLM configurations all emit them, so their decisions are comparable under one
`eval_run_id` namespace (`evals.run_id = eval_runs.id`; see
[`docs/evaluation.md`](evaluation.md) and [`docs/analysis.md`](analysis.md)). The
LLM prompts are one of several screening aids under evaluation, not the only
path.

## Prompt Management

All LLM prompts are stored in `config/prompts.py` as string constants with XML tags for sections. The three prompt templates are:

- **`P1_ZERO_SHOT_PROMPT`**: Minimal zero-shot prompt with label definitions and JSON schema.
- **`P2_RUBRIC_PROMPT`**: Rubric-based prompt with precise definitions, explicit "ML out of scope" rule, decision rules, and the critical rule repeated at the end.
- **`P3_FEWSHOT_PROMPT`**: Few-shot prompt with CoT reasoning and 3 worked examples (in/out/hybrid) targeting hard borderline cases.

Prompts are accessed via the `get_prompt(prompt_id)` function in `config/prompts.py`, which returns the appropriate template string based on the prompt ID (`p1`, `p2`, or `p3`).

## Dynamic Ingestion of Title and Abstract

The LLM screening module dynamically ingests the paper's title and abstract using XML tags. The title and abstract are formatted and passed to the LLM as:

```xml
<paper>
<title>{title}</title>
<abstract>{abstract}</abstract>
</paper>
```

This XML structure ensures consistent formatting and clear section demarcation for the model to process.

## System Prompt Design

The three prompts are designed per current best practices (see `docs/prompts.md` and
`docs/evaluation.md`): durable rules in the system prompt, explicit role framing,
structured JSON output with a schema, few-shot examples only where the boundary is
subtle (P3), and the critical "ML/modeling is OUT OF SCOPE" rule repeated at the end
(recency bias). The model must return:

```json
{
  "decision": "in-scope" | "out-of-scope" | "hybrid",
  "relevance_score": 0.0,
  "confidence": 0.0,
  "techniques": ["power analysis"],
  "rationale": "one sentence"
}
```

## Usage (emit eval JSONL)

```bash
puf eval screen \
  --prompt-id P2 \
  --model "$MODEL" \
  --api-key "$API_KEY" \
  --base-url "$BASE_URL" \
  --query-ids 3 4 \n  --out data/evals/llm_p2_${MODEL}.jsonl \
  --prompt-id P2
```

This writes one JSONL line per paper. Ingest it into the database with:

```bash
puf eval ingest data/evals/*.jsonl
```

## Verification

`verify_excerpt` (legacy 2-class path) still checks excerpt presence. For the eval
path, `screen_to_jsonl` validates that `decision` is one of the three classes and
writes the fixed schema; invalid responses raise so they can be retried.

## Dry Run

`puf eval screen --dry-run` writes placeholder JSONL without calling the API.

## Legacy 2-class storage

The older `puf screen` (REVIEW/EXCLUDE) path stores decisions in the `decisions`
table (`run_id`, `paper_id`, `decision`, `criterion`, `justification`, `excerpt`,
`excerpt_verified`, `tokens_used`). The evaluation study uses the newer 3-class
`evals` / `eval_runs` tables instead.

All evaluation results (baseline, embedding, LLM) are analysed together via the
analyst agent / `/analyze` command — see [`docs/analysis.md`](analysis.md).
