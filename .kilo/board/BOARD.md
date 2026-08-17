# Agent Message Board

Shared coordination surface for the PUF_RA agentic coding team. The
**orchestrator** is the single router: it reads OPEN/IN_PROGRESS items and
dispatches them to the right specialist subagent via the Task tool. Subagents
post their results here so that *every* agent (and the user) has a single
source of truth for what is happening, what broke, and who owns it.

## Conventions

- Append new entries at the **bottom** of the file (chronological log).
- Every entry is a `## [ID] TITLE` block. IDs are zero-padded running numbers
  (MSG-001, MSG-002, ...). Use a `TASK-` prefix for top-level user requests
  and `MSG-` for sub-items.
- Required fields per entry: `Type`, `From`, `To`, `Status`, `Priority`,
  `Created`, `Updated`, `Body`.
- Statuses: `OPEN`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `WONT_FIX`.
- Types: `COORD` (orchestration), `TEST`, `BUG`, `REVIEW`, `GIT`, `CODER`
  (implementation), `RESEARCH` (SOTA findings), `ARCH` (design
  recommendation), `INFO`.
- Keep entries short. Link files with repo-relative paths. Paste only the
  relevant error snippet, never whole logs.
- When a subagent finishes, it sets `Status: DONE` and writes a one-line
  `Result:`. The orchestrator closes the parent `TASK-` when all children are
  `DONE` or `WONT_FIX`.

## Routing rules

| Type     | Owner subagent | Triggered by                                                  |
|----------|----------------|---------------------------------------------------------------|
| TEST     | tester         | baseline checks, verify a fix, regression after edit         |
| BUG      | debugger       | any failing test, exception, or lint error                    |
| REVIEW   | code-review    | changed files ready for review                                |
| GIT      | git-manager    | reviewed & green changes ready to commit                      |
| CODER    | coder          | feature implementation assigned by the orchestrator          |
| RESEARCH | researcher     | SOTA survey requested by the orchestrator                     |
| ARCH     | architect      | design evaluation; consumes RESEARCH, yields delegatable recs |

The orchestrator forwards automatically:
- `TEST` fails -> `BUG` (debugger); `BUG` fix re-verified by `tester`;
  green `TEST` -> `REVIEW` (code-review); approved `REVIEW` -> `GIT`
  (git-manager).
- `RESEARCH` findings -> `ARCH` (architect consumes them).
- `ARCH` recommendation (`delegatable: yes`) -> new `TASK-` for `coder`
  (new code) or `debugger` (fix).

---

## [TASK-001] Physical-attack PUF relevance: rigorous evaluation study
- Type: COORD
- From: orchestrator
- To: orchestrator
- Status: DONE
- Priority: high
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: |
  Implement the IEEE-grade evaluation study (R1-R7). LOCKED DECISIONS:
  - Scope: physical attacks on PUFs (side-channel, fault injection,
    invasive/semi-invasive). ML/modeling attacks OUT of scope.
  - Ground-truth labels: in-scope / out-of-scope / hybrid (NOT
    relevant/irrelevant/borderline).
  - 30-50 stratified papers + 2nd annotator (report Cohen's/Fleiss' kappa).
  - Baseline: src/relevance.py (threshold derived from ground truth).
  - Add SBERT baseline; 9 LLM configs (3 prompts x 3 LLMs), LLMs
    configurable via config/eval_models.json.
  - Outputs: data/evals/*.jsonl; metrics F1/kappa/McNemar/bootstrap;
    LLM-as-judge with bias controls.
  - Reproducibility: temperature=0, >=3 runs, protocol in docs/.
  - Thesis focus: we EVALUATE the tools (LLM prompts / screening methods) used in
    the SoK-paper workflow; we do NOT use LLMs to write the paper itself.
  Blocked on user approval to edit source (per "review before changes").
- Result: implemented — DB schema extended (ground_truth, ground_truth_consensus,
  eval_runs, evals, llm_judge); eval_store ingest + κ + metrics; 3 research-backed
  prompts (config/prompts) + eval_models.json; screening->JSONL; baselines (SBERT
  optional); cli/eval + dispatcher; ground-truth guideline + template; docs
  (evaluation, prompts, schema, pipeline, llm_screening) + README updated;
  31 tests pass (1 skipped: needs openai). Awaiting human ground-truth fill.

## [MSG-001] research: PUF attack taxonomy + LLM-judge biases
- Type: RESEARCH
- From: researcher
- To: architect
- Status: DONE
- Priority: high
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: |
  Finding 1 (taxonomy): PUF attacks split into modeling/ML (non-invasive,
  logical, CRP-based) vs physical (side-channel power/EM/timing, fault
  injection, invasive/semi-invasive probing/FIB/depackaging/delayering);
  hybrid = side-channel + ML. Sources: Soton invasive/semi/non-invasive survey;
  DATE'14 hybrid; 2026 SLR "Modeling of PUF..."; Chowdhury'20.
  Gap: src/relevance.py scores "machine learning attack"/"modeling attack"
  as physical (weight 3.0) -> CONSTRUCT-VALIDITY THREAT.
  Finding 2 (LLM judge): position/verbosity/self-preference/family/authority
  biases documented (Zheng'24; CALM'24; "Judging the Judges"'26). Mitigations:
  out-of-family judge, order swap, length control.
  Suggestion: fix physical-attack scope (R1); add bias controls (R6).
- Result: 2 findings; feeds ARCH MSG-002.

## [MSG-002] arch: redesign relevance evaluation for scientific rigor
- Type: ARCH
- From: architect
- To: orchestrator
- Status: DONE
- Priority: high
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: |
  severity: high
  proposal: implement R1-R7 - redefine physical-attack scope (ML out of
  scope), build 30-50 stratified ground truth with in-scope/out-of-scope/
  hybrid labels + 2nd annotator, make relevance.py threshold-from-ground-
  truth + add SBERT baseline, add JSONL evaluation harness with F1/kappa/
  McNemar/bootstrap, configurable LLM registry, LLM-as-judge bias controls,
  document protocol + threats.
  affected: src/relevance.py, src/screening.py, new src/evaluation.py,
  src/baselines.py (SBERT), config/eval_models.json, config/prompts/*,
  data/ground_truth/*, data/evals/*.jsonl, docs/study_protocol.md,
  docs/threats_to_validity.md
  benefit: valid, reproducible, publishable comparison (deterministic vs LLM).
  tradeoff: more work; sentence-transformers dep; API keys for LLMs.
  delegatable: yes
  draft-task: "Implement R1-R7 with locked decisions; ground-truth labels
  in-scope/out-of-scope/hybrid; ML/modeling out of scope; LLMs configurable."
- Result: 1 recommendation; schedule on approval.

<!-- New entries go above this line. -->

## [MSG-000] Board initialized
- Type: INFO
- From: orchestrator
- To: ALL
- Status: DONE
- Priority: normal
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: Message board created. Agents post here; orchestrator routes.
- Result: Board ready.
