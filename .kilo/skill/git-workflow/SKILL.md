---
name: git-workflow
description: Repeatable git workflow for the PUF_RA repo — staging only intended files, writing a concise commit message in repo style, handling hook failures, and opening PRs. Use when the orchestrator assigns a GIT entry after review approval.
---

# Git Workflow (PUF_RA)

Use when the orchestrator opens a `GIT` entry for reviewed-and-green changes.
You do NOT edit source — only version control. (See the `git-manager` subagent
prompt and `message-board` skill.)

## Steps

1. **Read the board.** Open `.kilo/board/BOARD.md`, find the `GIT` entry, and
   note the exact files to commit and whether a branch/PR is requested.
2. **Inspect state** before touching anything:
   ```bash
   git status --short
   git diff --stat
   git log --oneline -5
   ```
3. **Stage only intended files** — never `git add -A` blindly:
   ```bash
   git add src/db.py cli/import.py tests/test_import.py
   ```
4. **Commit** with a concise, repo-style message:
   - Imperative subject line, < 70 chars, no trailing period.
   - Body only if needed: what and why, referencing the board `TASK-`/`GIT` id.
   - No Co-Authored-By / assistant signatures unless the repo requires them.
   ```bash
   git commit -m "Fix DOI dedup on BibTeX import

   Parameterize INSERT to preserve FK integrity. See BOARD GIT-012."
   ```
5. **Handle hook failures** — if a hook (lint/format) rejects the commit, do
   not bypass it. Report the failure on the board as a `BLOCKED`/new `BUG` so
   the debugger can fix it.
6. **Branch / PR only if requested** in the `GIT` entry:
   ```bash
   git checkout -b fix/doi-dedup
   git push -u origin fix/doi-dedup
   gh pr create --fill   # if gh is authed
   ```
7. **Report.** Post `GIT` entry: `Status: DONE`, `Result:` with the commit
   hash (and PR URL if any). Link the parent `TASK-`.

## Guardrails

- Never push to a remote unless the `GIT` entry explicitly asks.
- Never force-push, amend shared history, or commit `.env`/secrets.
- Only commit files listed in the `GIT` entry. If `git status` shows
  unexpected changes, STOP and set the entry to `BLOCKED` with the details.
- Keep commits focused; split large scopes into multiple commits.
