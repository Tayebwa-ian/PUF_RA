# LLM-as-Judge Approach

## Overview

The LLM-as-judge approach is an auxiliary quality control mechanism used to audit the quality of the rationales and decisions produced by the screening methods in the PUF_RA literature-review pipeline. The screening methods under evaluation include:

- **Deterministic baselines**: Keyword and BM25-based relevance screening
- **Embedding baseline**: Cosine similarity using the Uni Passau `octen-embedding-8b` model
- **LLM prompts**: Configurable LLM screening approaches (3 prompts × 3 LLMs)

The LLM-as-judge is **not** used as a primary classifier for paper relevance. Instead, it serves as an independent auditing mechanism to evaluate whether the rationales and decisions produced by the screening methods are coherent, relevant to the paper's title and abstract, and consistent with the assigned decision.

## Bias Mitigation Mechanisms

To ensure the LLM-as-judge provides unbiased and reliable quality assessments, the following bias control mechanisms are implemented:

### Out-of-Family Judge

The judge model is a different LLM than those being evaluated. This prevents self-preference bias, where an LLM might favor its own outputs or the outputs of models from the same family or architecture. By using an out-of-family judge, the evaluation remains objective and independent of the models under study.

### Order Swap

To detect and mitigate position bias, the evaluation targets are presented to the judge in random order. Position bias occurs when an LLM's judgment is influenced by the position of an item in a list (e.g., favoring the first or last item presented). By randomizing the order of evaluation targets, the judge's assessments are decoupled from positional artifacts.

### Length Control

To ensure the judge's evaluation is not influenced by the length of the rationale or decision, length control mechanisms are applied:

1. **Normalization/Truncation**: Rationales are normalized or truncated to a standard length before being passed to the judge, ensuring that longer rationales do not artificially inflate quality scores.
2. **Explicit Instructions**: The judge is explicitly instructed to ignore length and focus solely on the coherence, relevance, and consistency of the rationale and decision.

These mechanisms ensure that the judge's score reflects the actual quality of the rationale and decision, not superficial characteristics like length or position.

## Interpreting Judge Results

The LLM-as-judge produces the following outputs for each evaluated screening method decision:

### Score

A continuous `score` ranging from `0.0` to `1.0` that represents the quality and coherence of the rationale and the consistency of the decision with the paper's content (title and abstract).

- **High scores (e.g., >0.8)**: Indicate high-quality rationales that are coherent, relevant to the abstract/title, and consistent with the assigned decision. These decisions are considered reliable.
- **Medium scores (e.g., 0.5–0.8)**: Indicate rationales that may have minor issues or lack sufficient detail. These may require closer inspection.
- **Low scores (e.g., <0.5)**: Indicate potential issues such as incoherent rationales, rationales that are not relevant to the paper's content, or decisions that do not align with the rationale. These require manual review.

### Verdict

A categorical `verdict` that summarizes the judge's assessment:

- **`pass`**: The rationale and decision meet the quality standards. No further review is needed.
- **`needs_review`**: The rationale or decision has potential issues that require manual inspection.
- **`fail`**: The rationale is incoherent, irrelevant, or the decision is inconsistent with the content. This indicates a significant quality issue.

### Rationale

A `rationale` field containing the judge's explanation for its judgment. This provides transparency into why a particular score or verdict was assigned, enabling human reviewers to understand the judge's reasoning and verify its assessment.

## Implementation Details

### Database Storage

Judge evaluations are stored in the `llm_judge` database table with the following schema:

| Column | Type | Description |
|--------|------|-------------|
| `eval_id` | TEXT | The identifier of the original evaluation record being judged |
| `judge_model` | TEXT | The model name used as the judge |
| `judge_prompt_id` | TEXT | The prompt template ID used for the judge evaluation |
| `score` | REAL | The continuous quality score (0.0 to 1.0) |
| `verdict` | TEXT | The categorical verdict: `pass`, `needs_review`, or `fail` |
| `rationale` | TEXT | The judge's explanation for its judgment |
| `created_at` | TEXT | Timestamp of when the judge evaluation was performed (ISO-8601 format) |

### Integration with Evaluation Pipeline

The LLM-as-judge evaluations are integrated into the evaluation pipeline as a post-processing step. After the screening methods produce their decisions and rationales, the judge model evaluates each output and stores the results in the `llm_judge` table. This allows for:

1. **Quality auditing**: Identifying which screening method outputs require manual review.
2. **Method comparison**: Comparing the quality of rationales and decisions across different screening methods.
3. **Bias detection**: Using the judge's assessments to identify systematic issues in specific screening methods or prompt configurations.

### Evaluation Metrics

The judge's `score` and `verdict` outputs are used to compute additional evaluation metrics:

- **Pass rate**: The percentage of evaluations that receive a `pass` verdict.
- **Average score**: The mean `score` across all evaluations for a given screening method.
- **Review rate**: The percentage of evaluations that require manual review (`needs_review` or `fail` verdicts).

These metrics provide a quantitative measure of the quality and reliability of each screening method's outputs, complementing the standard Precision/Recall/F1 and ROC-AUC/PR-AUC metrics used for relevance classification.
