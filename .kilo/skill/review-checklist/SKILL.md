---
name: review-checklist
description: Structured code-review checklist for the PUF_RA repo, including what to check, how to rate severity, and how to post findings to the shared message board. Use when reviewing changed files after tests go green.
---

# Review Checklist (PUF_RA)

Use when the orchestrator opens a `REVIEW` entry. You are read-only: post
findings, do not edit. (See the `code-review` subagent prompt and `message-board`
skill.)

## How to find the changes

```bash
git status --short
git diff main --stat
git diff --staged --stat
```

Review the files listed in the `REVIEW` entry (repo-relative).

## Checklist

**Correctness**
- [ ] Null/None handling; no unguarded `.get()` chains that can mask bugs
- [ ] SQL: correct joins, parameter binding, FK integrity, dedup (DOI unique)
- [ ] Pandas/IO: encoding, missing columns, empty inputs handled
- [ ] Exceptions: caught where they should be; no bare `except:`
- [ ] Off-by-one, loop boundaries, regex correctness

**Conventions (match `src/` style)**
- [ ] Typed signatures; descriptive names
- [ ] No unnecessary comments (repo avoids comments unless asked)
- [ ] Module layout consistent with siblings
- [ ] CLI entry points follow `cli/*.py` patterns

**Security**
- [ ] No secrets or API keys logged or committed
- [ ] No `eval`/`exec` on untrusted input; no shell injection in `subprocess`
- [ ] Safe DB access (parameterized queries)

**Tests**
- [ ] Changed behavior is covered; if a gap is obvious, suggest a test (do not
      write it yourself)

## Rating severity

- `blocking` — must be fixed before commit (correctness bug, security, broken
  behavior).
- `non-blocking` — nice-to-have (style, minor robustness).

## Reporting

Post a `REVIEW` entry: `Status: DONE`, verdict (`APPROVED` or
`CHANGES_REQUESTED`), and one bullet per issue:

```markdown
## [MSG-NNN] review: db dedup fix
- Type: REVIEW
- From: code-review
- To: orchestrator
- Status: DONE
- Priority: high
- Created: YYYY-MM-DD
- Updated: YYYY-MM-DD
- Body: |
  CHANGES_REQUESTED
  - blocking: src/db.py:88 — INSERT uses literal instead of parameter; SQL injection risk
  - non-blocking: src/bibtex_importer.py:140 — consider early return for empty list
- Result: 1 blocking issue; route to debugger.
```

If clean: `Result: APPROVED — ready for GIT.`
