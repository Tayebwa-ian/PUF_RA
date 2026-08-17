# Evaluation Study — Protocol & Methodology

This document specifies how the pipeline **evaluates the tools** (LLM prompts and
baselines) used to build a Systematication-of-Knowledge (SoK) paper on physical
attacks on PUFs. It is the study protocol; follow it *before* tuning anything.

> **Thesis framing (locked):** we evaluate screening *tools*, not the paper
> itself. The LLMs under study are screening aids under evaluation, never
> co-authors. ML/modeling attacks are explicitly **out of scope** as physical
> attacks.

---

## 1. Research questions

- **RQ1** — Do LLM-based screening prompts agree with the expert ground truth
  more than the deterministic baseline?
- **RQ2** — Which prompt design (P1 zero-shot / P2 rubric / P3 few-shot) yields
  the highest agreement?
- **RQ3** — Which LLM (3 models, configurable) is most reliable?
- **RQ4** — Does an embedding (SBERT) baseline help where keyword/BM25 fails
  (e.g. paraphrase, hybrid cases)?
- **RQ5** — What are the cost / reproducibility trade-offs, and do LLM judges
  exhibit bias on this task?

## 2. Design

**Methods compared (factorial):**
- Deterministic baseline: `keyword`, `bm25`, `hybrid` (`src/relevance.py`).
- SBERT embedding baseline (`src/baselines.export_sbert_jsonl`).
- **9 LLM configurations = 3 prompts (P1/P2/P3) × 3 LLMs**, configured in
  `config/eval_models.json` (no hard-coded model ids in source).

**Ground truth (human task — you + one co-annotator):**
- 30–50 papers, stratified across `in-scope` / `out-of-scope` / `hybrid`.
- Two independent annotators; inter-rater agreement reported (Cohen's κ for 2,
  Fleiss' κ for >2). Disagreements flagged `disagree` for adjudication.
- Exact format & labels: `docs/ground_truth_guidelines.md`.

## 3. Prompts (best-practice design)

Designed per current prompt-engineering guidance (system/user split, explicit role,
structured JSON output, few-shot only where the boundary is subtle, recency-bias
restatement of the critical rule). See `docs/prompts.md` for the rationale and
`config/prompts/p{1,2,3}*.txt` for the files.

- **P1 — zero-shot:** minimal, just the label definitions + JSON schema.
- **P2 — rubric:** precise definition of physical attacks, explicit *ML out of
  scope* rule, decision rules, JSON schema; the critical rule repeated at the end.
- **P3 — few-shot + CoT:** 3 worked examples (in/out/hybrid) + reasoning,
  targets the hard borderline cases.

## 4. Output contract

Each evaluation is one line of JSONL (`data/evals/*.jsonl`) with the schema
consumed by `src/eval_store.ingest_eval_file`:

```json
{"eval_id":"eval-1-42","paper_id":42,"title":"...","method":"llm","model":"gpt-4o",
 "model_version":"2024-08","prompt_id":"P2","decision":"in-scope","score":0.9,
 "confidence":0.9,"matched_keywords":["power analysis"],"rationale":"...",
 "temperature":0.0,"run":1,"timestamp":"ISO8601"}
```

`decision` is always one of the three labels (matching ground truth), so metrics
align directly.

## 5. Database

New tables (see `database_struct.sql` / `src/db_schema.py`):
- `ground_truth` (per annotator), `ground_truth_consensus` (agreed labels + κ).
- `eval_runs` (method/model/prompt/temp/repetition — idempotent key),
  `evals` (per-paper 3-class decision + score).
- `llm_judge` (optional LLM-as-judge quality scores).

## 6. Metrics & statistics

For each `eval_runs` row vs the consensus gold standard (`src/eval_store.compute_metrics`):
- Per-class **Precision / Recall / F1** (macro), overall **accuracy**.
- **Cohen's κ** between method decisions and consensus.
- **ROC-AUC** (binary, `in-scope` positive) where a continuous `score` exists.
- Paired comparisons: **McNemar's test**, **bootstrap CIs**, **Holm–Bonferroni**
  across the 10+ methods.
- **LLM-as-judge** (optional): pin an *out-of-family* judge model, swap candidate
  order, length-control, and report bias diagnostics (position / verbosity /
  self-preference). See `docs/prompts.md`.

## 7. Reproducibility

- `temperature = 0` (greedy); each configuration run **≥ 3 times**, report mean ±
  variance.
- Models and prompts are **config-driven** (`config/eval_models.json`,
  `config/prompts/`) — never hard-coded.
- The protocol above is fixed up front (pre-registration-style) to avoid post-hoc
  tuning. Log `config_hash` per run.

## 8. Threats to validity

- **Construct** — the baseline's keyword set conflates ML/modeling with physical
  attacks; this is corrected by the scope lock and the 3-class scheme.
- **Internal** — prompt wording / order; mitigated by P2's recency restatement
  and the bias-controlled judge.
- **External** — corpus size / representativeness; mitigated by stratification
  and ≥30 papers.
- **Conclusion** — multiple comparisons; mitigated by Holm–Bonferroni and CIs.

## 9. End-to-end commands

```bash
# 1. (You) import papers, then export a fill-in sheet
puf eval export-papers data/ground_truth/ground_truth_template.csv
# 2. (You + co-annotator) fill data/ground_truth/ground_truth.csv
puf eval groundtruth data/ground_truth/ground_truth.csv   # reports κ

# 3. Baselines -> JSONL -> DB
puf eval baseline --method hybrid --out data/evals/baseline_hybrid.jsonl
puf eval baseline --method sbert  --out data/evals/sbert.jsonl
puf eval ingest data/evals/*.jsonl

# 4. LLM prompts (needs API) -> JSONL -> DB
puf eval screen --prompt config/prompts/p2_rubric.txt --model "$M" \
  --api-key "$KEY" --base-url "$URL" --query-ids 3 4 \
  --out data/evals/llm_p2_${M}.jsonl --prompt-id P2
puf eval ingest data/evals/*.jsonl

# 5. Metrics
puf eval runs
puf eval metrics <run_id>
```
