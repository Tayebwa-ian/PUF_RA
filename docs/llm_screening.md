# LLM Screening Documentation

## Overview

The LLM screening module classifies each paper into the **three-class scheme** used by
the study — `in-scope` (physical attack on a PUF), `out-of-scope` (e.g. ML/modeling
attack), or `hybrid` (side-channel + ML) — using an OpenAI-compatible API. It emits
**structured JSONL** that feeds the evaluation harness (`src/eval_store`).

> This study *evaluates the tools* (LLM prompts) used to build the SoK paper. The
> LLM is a screening aid under evaluation, not a co-author.

## Setup

1. **API endpoint:** Configure via `--base-url`.
2. **API key:** Pass via `--api-key` (not stored in repo; read from `config/eval_models.json`).
3. **Model:** Select via `--model` from `config/eval_models.json` (configurable; do not hard-code).
4. **System prompt:** Use one of `config/prompts/p1_zero_shot.txt`, `p2_rubric.txt`, `p3_fewshot.txt`.

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
  --prompt config/prompts/p2_rubric.txt \
  --model "$MODEL" \
  --api-key "$API_KEY" \
  --base-url "$BASE_URL" \
  --query-ids 3 4 \
  --out data/evals/llm_p2_${MODEL}.jsonl \
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

