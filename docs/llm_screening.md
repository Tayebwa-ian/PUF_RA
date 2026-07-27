# LLM Screening Documentation

## Overview

The LLM screening module automates the REVIEW/EXCLUDE decision process for systematic literature reviews. It uses an OpenAI-compatible API to evaluate each paper's relevance based on its title and abstract.

## Setup

1. **API endpoint:** Configure via `--base-url` (e.g., `https://llms.innkube.fim.uni-passau.de`).
2. **API key:** Pass via `--api-key` (not stored in repo).
3. **Model:** Select via `--model` (e.g., `qwen3-next-80b-a3b-instruct`).
4. **System prompt:** Provide via `--system-prompt` file path.

## System Prompt Guidelines

The system prompt should instruct the model to:
- Return JSON with keys: `decision`, `criterion`, `justification`, `excerpt`.
- Use `decision` = "REVIEW" or "EXCLUDE".
- Base decision on the abstract only.
- Provide a short excerpt from the abstract supporting the decision.

## Usage

```bash
puf screen \
  --model qwen3-next-80b-a3b-instruct \
  --api-key "$API_KEY" \
  --base-url "https://llms.innkube.fim.uni-passau.de" \
  --query-ids 3 4 \
  --system-prompt system_prompt.txt \
  --max-retries 3
```

## Verification

The `verify_excerpt` function checks that the LLM's claimed excerpt actually appears in the abstract. This guards against hallucinated citations.

## Storage

Decisions are stored in the `decisions` table with:
- `run_id` — links to the screening run
- `paper_id` — links to the paper
- `decision`, `criterion`, `justification`, `excerpt`
- `excerpt_verified` — boolean
- `tokens_used` — for cost tracking

## Dry Run

Use `--dry-run` to print actions without calling the API (useful for testing).
