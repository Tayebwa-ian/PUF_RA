# AGENTS.md — PUF_RA Agentic Coding Team

This repo is set up for **agentic coding** with a small team of Kilo agents
coordinated by a single **orchestrator** and a shared **message board**.

## Orchestrator operating mode

- Plan-then-approve: present an execution plan and get user sign-off before any implementation.
- Direct Q&A: answer project/architecture questions without spawning subagents (see `.kilo/skill/orchestrator-policy`).


## Research aim & locked design decisions

This repo is the engine for a **Master's-thesis study** about **evaluating the
tools** (notably LLM prompts / relevance-screening methods) **used in the
process of writing a Systematication-of-Knowledge (SoK) paper** on **physical
attacks on Physical Unclonable Functions (PUFs)**.

**Critical framing:** the thesis *evaluates the tooling* (deterministic baseline
+ LLM-based screening) that supports literature screening for an SoK paper — it
does **NOT** use LLMs to *write* the paper itself. The LLMs under study are
screening aids under evaluation, not co-authors. The pipeline must therefore
follow empirical, provable, reproducible scientific method, and every tool it
evaluates is itself the object of study.

**Locked decisions (do not contradict without user sign-off):**

- **Scope — physical attacks on PUFs.** Physical attacks = side-channel
  (power/EM/timing analysis), fault injection (laser/voltage/clock glitching),
  and invasive/semi-invasive (probing, FIB, depackaging, delayering).
  **ML / modeling attacks are explicitly OUT OF SCOPE** — they are logical,
  non-invasive CRP-based attacks, *not* physical. Hybrid attacks (side-channel
  **+** ML) are a distinct borderline class.
- **Ground-truth labels:** exactly three classes — `in-scope` (physical
  attack), `out-of-scope` (e.g. ML/modeling), `hybrid` (side-channel + ML).
  Stratify ~30–50 papers across these; a second annotator labels too, and we
  report inter-rater agreement (Cohen's / Fleiss' κ).
- **Baseline:** deterministic `src/relevance.py` (keyword + BM25); its decision
  threshold is derived from ground truth (max-F1 / Youden), not a fixed `0.15`.
- **Methods compared:** baseline + an **SBERT embedding baseline** + **9 LLM
  configs (3 prompts × 3 LLMs)**. LLMs are **configurable** via
  `config/eval_models.json` (open-source likely, others not ruled out); never
  hard-code a model.
- **Outputs:** structured **JSONL** (`data/evals/*.jsonl`) with a fixed schema
  (eval_id, paper_id, method, model, prompt_id, decision, score, confidence,
  rationale, temperature, run, timestamp).
- **Evaluation:** per-method Precision/Recall/F1, ROC-AUC/PR-AUC, κ vs ground
  truth; McNemar + bootstrap CIs + Holm–Bonferroni across methods; LLM-as-judge
  with bias controls (out-of-family judge, order swap, length control).
- **Reproducibility:** `temperature=0`, `run ≥ 3`, protocol documented in
  `docs/` (PRISMA-style) up front — no post-hoc tuning.

## Team

| Agent | Mode | Role | Invoked by |
|---|---|---|---|
| `orchestrator` | primary | Decomposes tasks, routes work, owns the board | user (`/orchestrate`, `/test`, …) |
| `coder` | subagent | Principal engineer; implements features + tests (best practices) | orchestrator (Task) |
| `tester` | subagent | Runs pytest, reports pass/fail, minimal snippets | orchestrator (Task) |
| `debugger` | subagent | Reproduces failures and fixes code | orchestrator (Task) |
| `code-review` | subagent | Reviews changed files, posts findings (read-only) | orchestrator (Task) |
| `git-manager` | subagent | Stages/commits, branches/PRs (read-only on code) | orchestrator (Task) |
| `researcher` | subagent | Surveys SOTA sources, posts `RESEARCH` findings (read-only) | orchestrator (Task) |
| `architect` | subagent | Evaluates design vs standards/research, posts `ARCH` recs (read-only) | orchestrator (Task) |
| `analyst` | subagent (realised via a `coder`/`general` subagent following `.kilo/agent/analyst.md`) | Computes corpus/relevance/snowball/evaluation statistics; queries DB via MCP, plots, persists via `store_analysis` | orchestrator (Task) |

## Message board (shared state)

All coordination lives in `.kilo/board/BOARD.md`. Agents post `TASK-`/`MSG-`
entries; the orchestrator is the single router. Protocol: see the
`message-board` skill. Rules:

- Append entries at the bottom; never delete; update `Status` in place.
- Minimal, repo-relative references (`src/db.py:120`). No full logs.
- Errors flow automatically: **TEST → BUG (debugger) → TEST (verify) → REVIEW
  → GIT**. The orchestrator forwards each handoff.
- Design flows as: **RESEARCH (researcher) → ARCH (architect) → TASK
  (coder/debugger)**. Findings become delegatable recommendations.

- **Timestamps & status lifecycle:**
  - **Timestamps (mandatory):** every entry's `Created` and `Updated` MUST be an
    exact ISO-8601 datetime with timezone offset (e.g. `2026-08-17T13:39:05+02:00`),
    never a date-only value. This lets us trace posting order and archive the board in order.
  - **Status lifecycle:** an agent sets its entry to `DONE` as soon as its assigned
    work is finished — the tester stamps `DONE` (not just `PASS`) once the suite is
    green; the code-reviewer stamps `DONE` (not just `APPROVED`) once approved;
    `BUG`/`CODER`/`RESEARCH`/`ARCH` likewise `DONE` on completion. `PASS` /
    `APPROVED` / `CHANGES_REQUESTED` are TRANSIENT states.
  - **Orchestrator reconciliation:** when a parent `TASK-` is complete, the
    orchestrator stamps ALL its child `PASS` / `APPROVED` / `CHANGES_REQUESTED`
    entries to `DONE`. (`WONT_FIX` is also terminal.) This keeps the board
    archivable, since `condense` archives only `DONE`/`WONT_FIX`.

## Standard pipeline (orchestrator)

1. Open `TASK-NNN` (COORD).
2. **(Optional) Research** — `researcher` posts `RESEARCH` findings.
3. **(Optional) Architect** — `architect` turns findings into `ARCH` recs;
   triage the `delegatable: yes` ones into sub-`TASK-`s.
4. **Implement** — `coder` builds the feature + tests (or go to 5 if already
   red).
5. **tester** → baseline `pytest -q`.
6. If RED → `BUG` for **debugger** (paste snippet from the `TEST` entry).
7. **tester** re-verifies → loop to 6 until GREEN.
8. **code-review** → `REVIEW` (APPROVED / CHANGES_REQUESTED).
9. Blocking issues → back to **debugger**, then re-verify.
10. **git-manager** → `GIT` commit (push only if asked).
11. Close `TASK-NNN` DONE.

The architect may also run **continuously/asynchronously**, posting new `ARCH`
entries whenever it re-reads the board or new `RESEARCH` lands; the
orchestrator periodically triages them.

## Convenience commands

| Command | Routes to | Purpose |
|---|---|---|
| `/orchestrate <task>` | orchestrator | Full pipeline for a task |
| `/code <desc>` | orchestrator → coder | Implement a feature with tests |
| `/test [path]` | orchestrator → tester | Run suite / one test |
| `/debug [test\|error\|MSG-id]` | orchestrator → debugger | Fix a failure |
| `/review [files]` | orchestrator → code-review | Review changes |
| `/research <topic>` | orchestrator → researcher | SOTA research pass |
| `/architect [area]` | orchestrator → architect | Design evaluation + recs |
| `/commit [files]` | orchestrator → git-manager | Commit reviewed-green changes |
| `/condense [--apply]` | orchestrator → condense | Compact the agent message board (archive resolved, condense open, write STATE.md). |
| `/analyze [corpus|relevance|snowball|evaluation|all] [--name X] [--db results.db] [--out-dir data/analysis] [--mode auto|mcp|direct]` | orchestrator → analyst | Compute study statistics via MCP; write JSON + PNG to data/analysis and persist via store_analysis. |

## Skills (repeatable tasks)

- `message-board` — board protocol & routing.
- `run-tests` — how to run pytest and report.
- `debug-workflow` — reproduce → fix → verify → report.
- `review-checklist` — what to check, severity, reporting.
- `git-workflow` — stage intended files, commit style, hooks, PRs.
- `implement` — coder standards + repo conventions.
- `research` — how the researcher surveys SOTA and posts findings.
- `architecture-review` — how the architect evaluates design and posts recs.
- `condense` — board condensation protocol (`/condense`, `puf condense`).
- `analysis` — the analyst agent role (`.kilo/agent/analyst.md`) + the `/analyze`
  command (`cli/analyze.py`) for corpus/relevance/snowball/evaluation statistics
  (docs/analysis.md). No `.kilo/skill/analysis/` directory: the persona file and
  the command are the reference.

## Project quick facts

- Python 3.10+, deps in `requirements.txt`, `.venv` present.
- Tests: `python -m pytest -q` (`testpaths = ["tests"]`).
- CLI entry: `puf` (see `pyproject.toml`); docs in `docs/`.
- SQLite schema: `database_struct.sql` + `src/db_schema.py`.
- Style: typed, descriptive names, no unnecessary comments.

## Guardrails

- The **tester**, **code-review**, **architect**, and **researcher** never edit
  code (read-only / advisory).
- The **git-manager** never edits code and never pushes unless asked.
- Only the **coder** and **debugger** edit source; the coder builds features,
  the debugger fixes failures — within the assigned scope.
- The orchestrator never commits/pushes except via the `git-manager` in step 10.

## Planning subagent — assessment

**Decision: not needed as a separate persistent agent.**

The orchestrator already owns decomposition, ordering, and handoffs — that *is*
the planning function. Adding a standalone `planner` would duplicate the
orchestrator's core responsibility and risk a second router competing for the
board. Instead:

- **Research-driven planning** is covered by `researcher` (what to improve) +
  `architect` (how to improve, as delegatable `ARCH` recs). Those two together
  provide forward-looking planning without a dedicated agent.
- **Execution planning** stays with the orchestrator's pipeline (steps 1–11).
- If a single initiative ever grows too large to plan inline, the orchestrator
  can spawn a one-off planning pass (e.g. delegate "draft a phased plan" to the
  `architect` as a `COORD`/`ARCH` artifact) rather than maintaining a permanent
  planner agent.

This keeps the topology at one router + seven workers and avoids role overlap.
