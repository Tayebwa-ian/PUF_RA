<!-- New entries go above this line. -->






## [MSG-000] Board initialized
- Type: INFO
- From: orchestrator
- To: ALL
- Status: DONE
- Priority: normal
- Created: 2026-08-16T09:00:00+02:00
- Updated: 2026-08-16T09:00:00+02:00
- Body: Message board created. Agents post here; orchestrator routes.
- Result: Board ready.

## [TEST-014] Baseline — tests/test_reference_store.py (TASK-014)
- Type: TEST
- From: tester
- To: orchestrator
- Status: DONE
- Created: 2026-08-27T16:53:20+02:00
- Updated: 2026-08-27T16:53:20+02:00
- Parent: TASK-014
- Body: "Targeted baseline for TASK-014 (file under change). Ran `python -m pytest tests/test_reference_store.py -q` (full suite avoided — test_analysis.py spawns a blocking mcp_server subprocess) plus an import smoke test."
- Result: "49 passed in 9.89s. No failures, no errors. Import smoke: `imports-ok` (src.reference_store, src.zotero_sync, src.rate_limiter, cli.snowball). GREEN."
