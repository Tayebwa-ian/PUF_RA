---
description: Commit reviewed-and-green changes in the PUF_RA repo via the git-manager subagent. Pass files to stage (e.g. /commit src/db.py tests/test_import.py), or omit to stage the working-tree diff. Never pushes unless you also say "and push".
agent: orchestrator
---
Commit the changes described by: $ARGUMENTS

Preconditions (enforce before committing): tests GREEN (verify with the
**tester** if unsure) and code-review APPROVED (open a `REVIEW` first if not
done). Use the `git-workflow` skill. Delegate to the **git-manager** subagent
via the Task tool. Scope:
- If $ARGUMENTS lists files, stage only those.
- If empty, stage the working-tree diff (never `git add -A` blindly).

Decide push from $ARGUMENTS: only push if the user explicitly said "and push"
or a branch/PR was requested. Have the git-manager post a `GIT` entry with the
commit hash (and PR URL if any). Report the commit hash concisely, referencing
the board entry.
