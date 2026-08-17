# Prompt Design — Rationale & Best Practices

The three screening prompts (`config/prompts/p1_zero_shot.txt`, `p2_rubric.txt`,
`p3_fewshot.txt`) were designed from current prompt-engineering best practices.
This note records *why* each choice was made so the study is defensible to an IEEE
reviewer.

## Principles applied (source: llmbestpractices.com, Anthropic/Claude & OpenAI
guidance, prompt-eval literature)

1. **System/user split.** Durable rules (role, label definitions, output schema,
   non-goals) live in the *system* prompt; the variable paper content is the
   *user* message. This avoids instruction drift across repeated calls.
2. **Explicit role framing.** Each prompt opens with a domain-expert role ("senior
   security reviewer …") — vague roles produce vague output.
3. **Structured JSON output + schema.** The model returns a fixed schema
   (`decision`, `relevance_score`, `confidence`, `techniques`, `rationale`). We use
   the provider's JSON mode and validate the result (invalid `decision` → retry).
4. **State non-goals explicitly.** "ML/modeling attacks are OUT OF SCOPE" is
   stated as a positive rule, not just a negation.
5. **Recency-bias restatement.** The critical "ML/modeling is out of scope" rule is
   repeated at the *end* of P2/P3, because models attend most to the last
   instructions.
6. **Few-shot only where the boundary is subtle.** The in-scope vs hybrid vs
   out-of-scope boundary is genuinely ambiguous, so P3 uses 3 diverse worked
   examples (clear in-scope, clear ML-only, borderline hybrid) rather than abstract
   rules alone.
7. **Escape hatch / confidence.** Every prompt returns a `confidence` so weak
   cases are visible rather than forced.
8. **No co-authoring.** Prompts never ask the model to *write* the paper; they
   only classify. This honours the thesis framing.

## Per-prompt design

| Prompt | Intended role in the study | Key design choice |
|---|---|---|
| **P1 — zero-shot** | Lower bound / simplest; tests whether the task is statable in words alone. | Minimal instructions + JSON schema; no examples. |
| **P2 — rubric** | The "rigorous" baseline prompt. | Precise definition of physical attacks, explicit ML-exclusion rule, decision rules, schema, critical rule restated at end. |
| **P3 — few-shot + CoT** | Tests whether examples + reasoning help on borderline (hybrid) cases. | 3 diverse examples inside `<example>` tags + `<thinking>` reasoning step before the JSON. |

## LLM-as-judge bias controls

When using an LLM to grade the *quality* of a decision/rationale (optional
`llm_judge` table), the judge must be:
- an **out-of-family** model relative to the evaluated models (avoid self/family
  preference bias),
- run with **candidate order swapped** (position bias),
- given **length-controlled** inputs (verbosity bias),
- **pinned** (model + prompt version logged) so scores are reproducible.

## Iteration discipline

Treat each prompt as production code: keep the eval set (ground truth) fixed,
change one prompt at a time, and compare on κ / F1 / AUC before promoting a new
prompt. Never tune against the gold set; only audit with it.
