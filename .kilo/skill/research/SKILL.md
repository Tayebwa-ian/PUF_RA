---
name: research
description: How the researcher subagent surveys SOTA sources and posts comparable, actionable RESEARCH findings to the message board for the architect and orchestrator.
---

# Research (Researcher) — SOTA Findings

The `researcher` subagent surveys top-notch sources and produces findings that
improve the PUF_RA pipeline. It is read-only and feeds the `architect`. Use this
skill when the orchestrator opens a `RESEARCH` entry.

## Topics that matter here

- Systematic literature review (SLR) pipelines & PRISMA-style screening.
- Relevance ranking: keyword, BM25, embeddings, hybrid/learned weighting.
- LLM-based inclusion/exclusion screening (prompts, calibration, abstention).
- Deduplication: DOI/metadata normalization, fuzzy matching.
- Snowballing: backward/forward, citation-graph traversal, API limits.
- Agentic software workflows (orchestrator/subagent patterns, message boards).

## How to search well

- Prefer peer-reviewed surveys and official framework docs. Use `websearch`
  with the current year for recency, and `webfetch` to read the actual source.
- Capture the **claim**, the **source URL**, and the **date/venue**.
- Triangulate: one source is a hint, two agreeing sources is a finding.

## How to post

Post `RESEARCH` entries to `.kilo/board/BOARD.md` (see `message-board` skill):

```markdown
## [MSG-NNN] research: hybrid relevance weighting
- Type: RESEARCH
- From: researcher
- To: architect
- Status: DONE
- Priority: normal
- Created: YYYY-MM-DD
- Updated: YYYY-MM-DD
- Body: |
  claim: learned per-topic weights beat fixed weights on SLR relevance.
  source: https://... (2024 survey)
  gap-vs-current: src/relevance.py uses fixed category weights.
  suggestion: add lightweight per-query weight tuning; architect to design.
- Result: 1 finding; feeds ARCH.
```

## Rules

- Read-only: never edit code. Inform; the architect/coder act.
- Be comparative and specific; one finding per entry.
- If the topic is too broad, post a scoping `INFO` note instead of a shallow
  dump. Cite URLs.
