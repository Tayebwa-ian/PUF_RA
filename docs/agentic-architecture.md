# PUF_RA — Agentic Coding System Architecture

This document describes the **agentic coding system** layered on top of the
PUF_RA literature-review pipeline. It is the human-readable, high-level view of
the agent topology, the shared message board, and how work flows between agents.
Machine-readable details live in `.kilo/` (agents, commands, skills) and the
coordination log in `.kilo/board/BOARD.md`.

---

## 1. Top-level architecture

The system is a **single-router, multi-worker** topology. One `orchestrator`
owns all coordination and routing; the rest are `subagent`s invoked only via
the Task tool. Agents share state through a file-based **message board**, never
by peer-to-peer calls. Two layers sit under the orchestrator:

- **Execution layer** — turns intent into working, tested, committed code.
- **Design / research layer** — read-only advisors that raise the *quality* of
  what the execution layer builds.

```
                         ┌──────────────────────────────┐
                         │          USER / KILO          │
                         └───────────────┬────────────────┘
                                         │ /orchestrate, /code, /test …
                                         ▼
                         ┌──────────────────────────────┐
                         │       orchestrator (primary)  │  ◀── single router
                         │  owns BOARD.md, decomposes,    │
                         │  routes, never edits/commits  │
                         └───┬───────────────┬──────────┬┘
              Task tool      │               │          │
            ┌────────────────┼───────────────┴──────────┼───────────────┐
            ▼                ▼                          ▼              ▼
     ┌────────────┐   ┌────────────┐            ┌─────────────┐  ┌────────────┐
     │  EXECUTION  │   │  EXECUTION  │            │ DESIGN/RES.  │  │ DESIGN/RES.│
     │   LAYER     │   │   LAYER     │            │   LAYER     │  │   LAYER    │
     ├────────────┤   ├────────────┤            ├─────────────┤  ├────────────┤
     │ coder      │   │ tester     │            │ researcher  │  │ architect  │
     │ debugger    │   │ code-review│            │ (SOTA)      │  │ (design)   │
     │ git-manager │   │            │            │            │  │            │
     └─────┬──────┘   └─────┬──────┘            └──────┬──────┘  └─────┬──────┘
           │  edits code    │ read-only                │ RESEARCH      │ ARCH
           └────────────────┴──────────────────────────┴───────────────┘
                                         │
                                         ▼
                         ┌──────────────────────────────┐
                         │   .kilo/board/BOARD.md        │  ◀── shared state
                         │   TASK-/MSG-/ARCH/RESEARCH…   │
                         └──────────────────────────────┘
```

---

## 2. Agents and responsibilities

| Agent | Mode | Edits code? | Responsibility | Triggered by |
|---|---|---|---|---|
| `orchestrator` | primary | no | Decompose, route, own the board, close tasks | user (commands) |
| `coder` | subagent | **yes** (scoped) | Implement features + tests to senior-engineer standards | orchestrator |
| `tester` | subagent | no | Run `pytest -q`, report pass/fail + minimal snippets | orchestrator |
| `debugger` | subagent | **yes** (scoped) | Reproduce failures, fix code | orchestrator (`BUG`) |
| `code-review` | subagent | no | Review diff for correctness/quality/conventions | orchestrator (`REVIEW`) |
| `git-manager` | subagent | no (code) | Stage/commit, branch/PR reviewed-green changes | orchestrator (`GIT`) |
| `researcher` | subagent | no | Survey SOTA, post `RESEARCH` findings | orchestrator (`RESEARCH`) |
| `architect` | subagent | no | Evaluate design vs standards/research, post `ARCH` recs | orchestrator (`ARCH`) |

The `coder` is the **10-year-expert implementation engine**: it builds new
functionality and ships tests, holding to typing, DRY/SOLID-where-it-pays, clear
interfaces, and the repo's no-unnecessary-comments convention. The `debugger`
is the **reactive fixer** for already-red tests. They share edit rights but are
never pointed at the same file concurrently (the orchestrator enforces this).

---

## 3. The message board (shared state)

All coordination lives in `.kilo/board/BOARD.md` — an append-only Markdown log.
Every agent posts `## [ID] TITLE` blocks with a fixed schema:

```
Type, From, To, Status, Priority, Created, Updated, Body, Result
```

- **Types**: `COORD`, `TEST`, `BUG`, `REVIEW`, `GIT`, `CODER`, `RESEARCH`,
  `ARCH`, `INFO`.
- **Statuses**: `OPEN`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `WONT_FIX`.
- **IDs**: zero-padded running numbers (`MSG-001`, `TASK-002`, …). Never delete;
  update `Status` in place.

The board is the single source of truth: an error posted by the tester is read
by the debugger directly, so the orchestrator forwards *references*, not
re-explanations. Protocol detail: `message-board` skill.

---

## 4. Execution pipeline (the "fix & ship" loop)

```
user ─▶ orchestrator: open TASK-NNN
        │
        ├─(opt) researcher ──▶ RESEARCH ──▶ architect ──▶ ARCH  (design feed)
        │
        ├─ coder (or debugger if already red) ──▶ CODER
        ▼
   tester ── pytest -q ──▶ TEST
        │ RED
        ▼
   debugger (reads TEST snippet) ──▶ BUG
        │
        ▼ (loop until GREEN)
   code-review ──▶ REVIEW (APPROVED / CHANGES_REQUESTED)
        │ blocking → back to debugger
        ▼
   git-manager ──▶ GIT (commit hash)
        │
        ▼
   orchestrator: close TASK-NNN = DONE
```

Automatic forwarding rules (enforced by the orchestrator):
`TEST→BUG`, `BUG→TEST(verify)`, `TEST(green)→REVIEW`, `REVIEW→GIT`,
`RESEARCH→ARCH`, `ARCH(delegatable)→TASK(coder|debugger)`.

---

## 5. Design / research loop (the "improve" loop)

This is the new, quality-raising half of the system:

```
researcher ──websearch/webfetch──▶ RESEARCH (claim + URL + gap + suggestion)
                                        │  To: architect
                                        ▼
architect ──evaluates design vs findings/standards──▶ ARCH
            (proposal, affected, benefit, tradeoff, delegatable: yes/no)
                                        │  To: orchestrator
                                        ▼
orchestrator triages:
   delegatable: yes  ──▶ TASK-NNN ──▶ coder (new code) | debugger (fix)
   needs sign-off    ──▶ ask user
```

The architect also runs **continuously/asynchronously**: it may re-read the
board at any time and post new `ARCH` entries (e.g. after fresh `RESEARCH`
lands). The orchestrator periodically triages the board for `ARCH` items, so
improvements are proposed and pulled into the execution pipeline without anyone
manually requesting them each time.

---

## 6. Skills & commands (reusable glue)

**Skills** encode *how* to do a repeatable task: `message-board`, `run-tests`,
`debug-workflow`, `review-checklist`, `git-workflow`, `implement`, `research`,
`architecture-review`.

**Commands** are thin user entry points, all routed to the orchestrator:

| Command | Purpose |
|---|---|
| `/orchestrate <task>` | Full pipeline for a task |
| `/code <desc>` | Implement a feature with tests (→ coder) |
| `/test [path]` | Run suite / one test (→ tester) |
| `/debug [test\|error\|MSG-id]` | Fix a failure (→ debugger) |
| `/review [files]` | Review changes (→ code-review) |
| `/research <topic>` | SOTA research pass (→ researcher) |
| `/architect [area]` | Design evaluation + recs (→ architect) |
| `/commit [files]` | Commit reviewed-green changes (→ git-manager) |

---

## 7. Planning subagent — assessment

**Not added as a separate persistent agent.** The orchestrator already performs
decomposition, ordering, and handoffs — that *is* planning. A standalone `planner`
would duplicate the orchestrator's role and create a competing router. Forward-
looking planning is instead covered by `researcher` (what to improve) + `architect`
(how to improve, as delegatable `ARCH` recs). If a single initiative grows too
large to plan inline, the orchestrator spawns a one-off planning pass (e.g.
delegate "draft a phased plan" to the `architect`) rather than keeping a permanent
planner. Topology stays at **one router + seven workers**.

---

## 8. Guardrails (enforced by config + prompt)

- `tester`, `code-review`, `architect`, `researcher` are read-only (no code edits).
- `git-manager` never edits code and never pushes unless asked.
- Only `coder` and `debugger` edit source, within assigned scope; never the same
  file concurrently.
- The orchestrator never commits/pushes except via `git-manager`.
- Board entries stay minimal and repo-relative (`src/db.py:120`); no full logs.
```
