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


## [TASK-002] Empower agents + SQL MCP + DB build, ingest, snowball
- Type: COORD
- From: orchestrator
- To: orchestrator
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  User request (5 parts):
  1. Empower coder + git-manager agents: shell access as current user, NEVER sudo.
  2. Build an MCP server to interact with the SQLite DB.
  3. Create the real DB and ingest cititations_data/query1 + query2; dedup by DOI AND title; record provenance (query1/query2 + platform).
  4. Plan for DB migration (design will evolve; never lose data) — forward, idempotent, data-preserving migrations.
  5. Implement snowballing well with smart rate limiting; run it on existing DB papers; insert discovered papers deduped by DOI; record snowball provenance like query1/query2; a paper found by query1 AND query2 AND snowball is ONE row with provenance links to all three (no reinsert).
  Subtasks: MSG-003 (agent config), MSG-004 (migration framework), MSG-005 (MCP server), MSG-006 (ingest + real DB), MSG-007 (snowball + run).
- Result: Implemented & committed: agent no-sudo shell, MCP server, results.db built+ingested (2198 distinct, 582 deduped), migration framework, snowball run (2389 papers, 199 edges, multi-method provenance). 91 passed.

## [MSG-003] agent config: shell as current user, never sudo
- Type: COORD
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: Create repo kilo.json (deny sudo, allow shell) and .kilo/agent/{coder,git-manager}.md with the no-sudo policy.
- Result: kilo.json + agent md files created; sudo blocked for repo agents.

## [MSG-004] migration framework (forward, data-preserving)
- Type: ARCH
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: Enhance src/db_schema.py with a versioned, idempotent, additive migration framework (schema_migrations table + apply_migrations) that never drops user data; add one example forward migration; docs/database_migration.md; tests.
- Result: Added schema_migrations framework + example migration (v1 add_papers_notes_column); ensure_schema/apply_migrations/get_schema_version idempotent; docs + tests; all tests pass.

## [MSG-005] MCP server for SQLite DB
- Type: CODER
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: Create an MCP server (stdio) exposing tools to read/query the SQLite DB (list/get papers, get by DOI, search, provenance, guarded SELECT, optional insert). Add `mcp` dep; stdlib fallback if SDK unavailable. Tests.
- Result: Created src/mcp_server.py (MCP stdio server, 7 tools, read-only SELECT guard + mode=ro conn); official mcp SDK (MCPServer, mcp 2.0.0) with stdlib JSON-RPC fallback; mcp added to requirements.txt; tests/test_mcp_server.py (25 tests); 62 passed, 1 skipped.

## [MSG-006] ingest query1/query2 into real DB + provenance
- Type: CODER
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: Create ingestion (scripts/ingest_citations.py) over cititations_data/query1 + query2; dedup by DOI AND normalized title; record provenance via paper_queries + paper_sources; idempotent; populate the real results.db. Tests incl. cross-query DOI dedup.
- Result: Populated results.db: 2198 distinct papers from 2780 entries (582 deduped across query1/query2); method-source provenance query1:ACM=1000, query1:IEEE=417, query2:ACM=608, query2:IEEE=754; 570 papers carry multi-method links (one with all 4). Also fixed parse hang on brace-unbalanced titles in src/bibtex_parser.py. All tests pass (68 passed, 1 skipped).

## [MSG-007] snowballing + smart rate limit + run
- Type: CODER
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: Fix snowball seed bug (v2 has no papers.query_id; use paper_queries); add smart RateLimiter (Retry-After, jitter, adaptive backoff, per-host); dedup by DOI+title; record snowball provenance via paper_sources('snowball') + snowball_edges; multi-membership (query1+query2+snowball = one row, all links); CLI `puf snowball`; run on existing DB, insert deduped. Tests.
- Result: Snowball fixed (v2 seed via paper_queries join + seed_paper_ids/all-papers fallback), new src/rate_limiter.py RateLimiter (pacing, Retry-After seconds/HTTP-date, jitter, adaptive backoff, RateLimitError stop) + max_api_calls budget and resume via skip_expanded, dedup by DOI(case-insensitive)+normalized title with paper_sources accumulation (no re-insert) and snowball_edges; CLI `puf snowball` (run default) + scripts/run_snowball.py; real run on results.db: semantic_scholar hit persistent HTTP 429 and stopped gracefully (30 refs, 30 linked, aborted=1), crossref runs inserted 191 new papers (2198 -> 2389, 199 edges, 198 snowball links, 7 papers now multi-method incl. paper 368 = query1:ACM+query2:ACM+snowball); tests pass (88 passed, 1 skipped).
## [TEST-001] Baseline: TASK-002 implementation
- Type: TEST
- From: tester
- To: orchestrator
- Status: PASS
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Ran `python -m pytest -q`. Result: 88 passed, 1 skipped. No failures or errors.
  Real DB sanity: papers=2389, snowball source links=198, snowball_edges=199.
- Result: PASS — all green, ready for review

## [REVIEW-001] Review of TASK-002 changes
- Type: REVIEW
- From: code-review
- To: orchestrator
- Status: APPROVED
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  All five requirements are substantially met and the full suite is green
  (88 passed, 1 skipped). Safety posture is good: MCP `execute_select`
  rejects non-SELECT/chained statements and opens a read-only connection;
  `insert_paper` validates types; `kilo.json` sets `"sudo *": "deny"`; and
  `src/snowball._get_seed_papers` correctly joins through `paper_queries`
  (no reference to the removed `papers.query_id`). Two correctness issues
  were found, one of which is a genuine gap against requirement 3.
  Issues (severity: blocker/major/minor):
  - scripts/ingest_citations.py:74-91 — `find_existing_paper` returns None as
    soon as the incoming entry has a DOI that is not found, and never falls
    back to a title match. A paper that appears once with a DOI and once
    without one is therefore inserted twice, breaking requirement 3
    ("dedup by DOI AND title"). Fix: mirror `src/snowball.find_existing_paper_id`
    — after the DOI miss, fall back to title matching but skip any existing
    row that carries a *different* non-null DOI; add a test asserting a
    DOI-bearing entry dedups against an existing title-only row. (major)
  - src/db_schema.py:351-361 — legacy `migrate_from_v1` inserts the column
    `human_decision` into the v2 `papers` table, which has no such column
    (v2 uses `is_relevant`/`relevance_score`), so a v1->v2 migrate raises
    OperationalError. It is outside the new forward framework (req 4) but a
    real crash. Fix: drop `human_decision` from the selected/inserted column
    list. (major, legacy/pre-existing — non-blocking for the 5 requirements)
  - src/mcp_server.py:90-146 — `assert_select_only` rejects any statement that
    merely contains a forbidden word inside a string literal (e.g.
    `WHERE title='update'`). Conservative false positive; acceptable for a
    read-only guard. Non-blocking. (minor)
  - src/snowball.py:719-721 — `stats["api_calls"]` is derived from
    `limiter.total_calls`, which also increments on every `wait_before_call`
    (pacing), so the reported/budgeted API-call count can exceed real
    requests and the budget stop can fire a request early. Non-blocking;
    consider counting only real requests via `_fetch_references`' `used`. (minor)
- Result: APPROVED after BUG-001 fixes (ingest title-fallback dedup, migrate_from_v1 human_decision removed, MCP guard string-literal handling, snowball api_calls count); 88+ pass.

## [BUG-001] ingest title-fallback dedup + minor fixes
- Type: BUG
- From: orchestrator
- To: debugger
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Reviewer (REVIEW-001) flagged: (1) ingest find_existing_paper missing title fallback when DOI absent -> double-insert risk; (2) migrate_from_v1 references non-existent human_decision column; (3) MCP execute_select false-positive on keywords inside string literals; (4) snowball api_calls over-count.
- Result: all four fixed; dedup + migrate + MCP guard + count corrected; tests pass.

## [GIT-001] Commit TASK-002 (reviewed + green)
- Type: GIT
- From: git-manager
- To: orchestrator
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Committed reviewed-green TASK-002 changes (agent config, MCP server, migration framework, query1/query2 ingestion into results.db, snowballing + run). 91 passed, 1 skipped.
- Result: committed 6a34a87 (local only, not pushed).

## [TASK-003] Orchestrator SOP + compact command + snowballing redesign
- Type: COORD
- From: orchestrator
- To: orchestrator
- Status: IN_PROGRESS
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Three-part initiative to codify and improve orchestration + snowballing:
  1. Orchestrator SOP: encode plan-then-approve and direct Q&A as a skill
     (.kilo/skill/orchestrator-policy) + AGENTS.md note (MSG-A).
  2. Compact command: add a concise orchestrator command/shorthand for the
     standard pipeline (MSG-B).
  3. Snowballing redesign: redesign the snowballing subsystem for scientific
     rigor and rate-limit resilience (MSG-C).
- Result: in progress

## [MSG-A] Orchestrator SOP (skill + AGENTS note)
- Type: COORD
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Create .kilo/skill/orchestrator-policy/SKILL.md (plan-then-approve +
  direct Q&A) and add an 'Orchestrator operating mode' note to AGENTS.md.
- Result: Created orchestrator-policy skill + AGENTS.md note; plan-then-approve + direct Q&A encoded.

## [MSG-B] Compact command for standard pipeline
- Type: COORD
- From: orchestrator
- To: coder
- Status: DONE
- Priority: normal
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Add a concise orchestrator command/shorthand that runs the standard
  plan -> research -> arch -> implement -> test -> review -> git pipeline.
- Result: Added src/compact.py + scripts/compact.py + `puf compact` + skill + tests; archives resolved entries, writes STATE.md, idempotent.

## [MSG-C] Snowballing redesign
- Type: COORD
- From: orchestrator
- To: coder
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Redesign the snowballing subsystem for scientific rigor and rate-limit
  resilience (provenance, dedup, adaptive pacing, graceful abort).
- Result: Two-phase local-first snowball implemented: reference_lists inventory
  + backward/forward + batch Crossref/OpenAlex + Zotero/PDF hooks + snowball_runs
  logging (src/reference_store.py, src/zotero_sync.py, new CLI flags). Real run on
  results.db: papers 2389->2479 (+90), reference_lists inventory=4382
  (backward 4378 + forward 4), snowball_edges=377, resolved=379, papers with
  pdf_url=90; budget stops graceful; Zotero no-ops when unconfigured. Existing
  snowball tests still green; added tests/test_reference_store.py (7 tests); full
  suite 102 passed, 1 skipped.

## [TEST-002] Baseline: TASK-003
- Type: TEST
- From: tester
- To: orchestrator
- Status: PASS
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Ran `python -m pytest -q`. Result: 102 passed, 1 skipped in 58.53s. No failures or errors.
  Real DB: papers=2479, reference_lists=4382, snowball_edges=377, snowball_runs=5, pdf_urls=90.
- Result: PASS — all green, ready for review

## [REVIEW-002] Review of TASK-003
- Type: REVIEW
- From: code-review
- To: orchestrator
- Status: APPROVED
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Verdict: all three requirements are substantially met, but R-B has one
  blocking data-loss bug. R-A (orchestrator SOP) is cleanly encoded in
  .kilo/skill/orchestrator-policy/SKILL.md + AGENTS.md "Orchestrator operating
  mode". R-C (snowball redesign) is correct: local-first resolution via
  local_find_paper before any per-paper network call, full reference_lists
  inventory (incl. DOI-less/unstructured refs), backward+forward (OpenAlex
  cites:), batch resolution (OpenAlex `ids.openalex:` `|` OR; Crossref per-DOI
  with documented no-OR limitation), lazy/optional pyzotero (batches <=50,
  graceful no-op), pdf_url captured from OpenAlex best_oa_location/Crossref,
  snowball_runs TARCiS-style logging, DOI+title dedup precedence preserved,
  expression-unique reference_lists index, and migration v2 is additive +
  idempotent (guarded ALTER for pdf_url; CREATE IF NOT EXISTS for new tables)
  so the 2389-paper corpus is preserved. Legacy run_snowball path is unchanged
  and the suite is green (102 passed, 1 skipped; 35 in the 3 reviewed files).
  Issues (severity: blocker/major/minor):
  - scripts/compact.py:64 — `--apply` does `Path(args.archive).write_text(...)`
    on `.kilo/board/BOARD.archive.md`, and `compact_board`/`_build_archive`
    never read the pre-existing archive, so a SECOND `--apply` overwrites the
    file with only the current run's entries and DISCARDS all previously
    archived summaries. This directly violates R-B ("no data loss", "resolved
    entries ... never deleted") and the skill's recommended "periodically ...
    housekeeping" use. Fix: open the existing archive in append mode / read it
    first and concatenate the new `archived` lines, instead of overwriting.
    (blocking)
  - docs/snowball.md:156 — lists `snowball_runs` column `note`, but
    `_insert_run`/`_finish_run` (src/reference_store.py:475-498) never populate
    it. Harmless; either drop `note` from the doc tuple or set it. (minor, non-blocking)
  - src/reference_store.py:577-588 — the in-memory `seen` dedup key is
    `(ref_doi or "", unstructured)` while the DB unique index uses
    COALESCE(ref_doi,'')/COALESCE(ref_unstructured,''), and `_upsert_reference_list`
    reassigns `unstructured=title_full` for title-only refs. Mismatch can let an
    INSERT OR IGNORE silently drop a row while `references_harvested` is still
    incremented. Edge case, low impact. (minor, non-blocking)
- Result: APPROVED after BUG-002 fix (compact archive now merges/dedupes across runs, no data loss); R-A and R-C already approved.

## [BUG-002] compact archive must merge, not overwrite
- Type: BUG
- From: orchestrator
- To: debugger
- Status: DONE
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  REVIEW-002 flagged R-B blocking: scripts/compact.py overwrote BOARD.archive.md each --apply, losing prior archives. Fixed to merge/dedupe; added regression test.
- Result: archive now appends/merges across runs; no data loss; tests pass.

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
