"""Tests for src.condense.condense_board (pure + idempotent board compaction)."""

from __future__ import annotations

from src.condense import condense_board

SAMPLE = """# Agent Message Board

Shared coordination surface. Conventions below.

## Conventions

- Append new entries at the bottom.

## [TASK-001] Open orchestration task
- Type: COORD
- From: orchestrator
- To: orchestrator
- Status: OPEN
- Priority: high
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: |
  First body line for the open task.
  Second body line that is fairly long and should be kept or trimmed.
  Third body line also retained under the limit.
  Fourth body line that exceeds the trim window and must be dropped.
- Result: in progress

## [MSG-001] Resolved research finding
- Type: RESEARCH
- From: researcher
- To: architect
- Status: DONE
- Priority: high
- Created: 2026-08-16
- Updated: 2026-08-16
- Body: |
  This is the first sentence of the resolved entry body.
  More detail that should not appear in the archive summary.

## [MSG-002] Open implementation subtask
- Type: CODER
- From: orchestrator
- To: coder
- Status: IN_PROGRESS
- Priority: high
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Implement the feature and ship tests.

## [MSG-003] Wont-fix item
- Type: INFO
- From: orchestrator
- To: ALL
- Status: WONT_FIX
- Priority: low
- Created: 2026-08-17
- Updated: 2026-08-17
- Body: |
  Out of scope; will not be addressed this cycle.

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
"""


def _open_ids(text: str) -> set[str]:
    ids = set()
    for line in text.splitlines():
        if line.startswith("## ["):
            import re

            m = re.match(r"##\s+\[([A-Za-z0-9][A-Za-z0-9_-]*)\]", line)
            if m:
                ids.add(m.group(1))
    return ids


def test_condense_basic_split():
    new_board, archive_text, state_text = condense_board(SAMPLE)

    # Resolved entries removed from the new board.
    assert "## [MSG-001]" not in new_board
    assert "## [MSG-003]" not in new_board
    # The after-marker DONE entry is preserved verbatim (live tail rule).
    assert "## [MSG-000]" in new_board

    # Open entries retained with their status.
    assert "## [TASK-001]" in new_board
    assert "## [MSG-002]" in new_board
    assert "Status: OPEN" in new_board
    assert "Status: IN_PROGRESS" in new_board

    # Resolved entries present in archive text with the required format.
    assert "- [MSG-001]" in archive_text
    assert "- [MSG-003]" in archive_text
    assert "DONE" in archive_text
    assert "WONT_FIX" in archive_text
    assert "first sentence of the resolved entry body" in archive_text

    # State text has counts and open IDs.
    assert "Total entries:" in state_text
    assert "Open:" in state_text
    assert "Resolved:" in state_text
    assert "TASK-001" in state_text
    assert "MSG-002" in state_text


def test_open_body_trimmed():
    new_board, _, _ = condense_board(SAMPLE)
    # The long open body should be trimmed, dropping the 4th line.
    assert "Fourth body line that exceeds" not in new_board
    # A trim marker is emitted when body was shortened.
    assert "(trimmed" in new_board


def test_idempotent():
    new_board_1, archive_1, _ = condense_board(SAMPLE)
    new_board_2, archive_2, _ = condense_board(new_board_1, archive_path="ARCH.md")
    # The open set is stable across runs.
    assert _open_ids(new_board_1) == _open_ids(new_board_2)
    # No resolved entries re-introduced.
    assert "## [MSG-001]" not in new_board_2
    assert "## [MSG-003]" not in new_board_2
    # Board compaction is idempotent: a 3rd pass matches the 2nd.
    new_board_3, _, _ = condense_board(new_board_2, archive_path="ARCH.md")
    assert _open_ids(new_board_2) == _open_ids(new_board_3)


def test_missing_marker_safe():
    text = "# Board\n\n## [TASK-9] thing\n- Status: DONE\n- Body: |\n  done body.\n"
    new_board, archive_text, state_text = condense_board(text)
    assert "## [TASK-9]" not in new_board
    assert "- [TASK-9]" in archive_text
    assert "Total entries: 1" in state_text


def _board_with(entry_id: str, body: str) -> str:
    return (
        "# Board\n\n"
        "## [%s] Resolved thing\n"
        "- Type: INFO\n"
        "- Status: DONE\n"
        "- Body: |\n"
        "  %s\n\n"
        "<!-- New entries go above this line. -->\n"
        % (entry_id, body)
    )


def test_archive_merges_across_runs_no_data_loss(tmp_path):
    # Hermetic: temp board + temp archive, two consecutive --apply runs.
    import sys
    from pathlib import Path

    if str(Path(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.condense import condense_command

    board = tmp_path / "BOARD.md"
    archive = tmp_path / "ARCH.md"
    state = tmp_path / "STATE.md"  # never touch the tracked repo-root STATE.md

    # First run archives X.
    board.write_text(_board_with("X", "first body"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0
    archived_after_run1 = archive.read_text(encoding="utf-8")
    assert "- [X]" in archived_after_run1
    assert "- [Y]" not in archived_after_run1

    # Second run archives a DIFFERENT entry (Y) using the SAME archive file.
    board.write_text(_board_with("Y", "second body"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0
    archived_after_run2 = archive.read_text(encoding="utf-8")

    # No data loss: the entry archived in run 1 is still present.
    assert "- [X]" in archived_after_run2
    assert "- [Y]" in archived_after_run2
    # STATE was written to the temp path only.
    assert state.exists()


def test_archive_dedupes_repeated_ids(tmp_path):
    import sys
    from pathlib import Path

    if str(Path(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.condense import condense_command

    board = tmp_path / "BOARD.md"
    archive = tmp_path / "ARCH.md"
    state = tmp_path / "STATE.md"  # never touch the tracked repo-root STATE.md

    board.write_text(_board_with("X", "first body"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0
    # Re-run with the same resolved entry; it must not be duplicated.
    board.write_text(_board_with("X", "first body"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0
    assert archive.read_text(encoding="utf-8").count("- [X]") == 1


def test_merge_archive_keeps_colliding_ids():
    # Regression: an incoming [ID] that already exists in the archive must NOT
    # be silently dropped. When the body differs, both records survive with
    # distinct keys (the incoming one disambiguated via a stable `#N` suffix).
    from scripts.condense import merge_archive

    existing = (
        "# Board Archive\n\n"
        "Resolved entries archived from the agent message board.\n\n"
        "- [MSG-001] research taxonomy finding — DONE — old body text\n"
    )
    new = (
        "# Board Archive\n\n"
        "Resolved entries archived from the agent message board.\n\n"
        "- [MSG-001] live-run finding — DONE — different body text\n"
    )
    merged = merge_archive(existing, new)

    # The previously archived entry is left untouched and retained.
    assert "- [MSG-001]" in merged
    assert "old body text" in merged
    # The colliding incoming entry is preserved (disambiguated), not dropped.
    assert "- [MSG-001#2]" in merged
    assert "different body text" in merged
    assert merged.count("old body text") == 1
    assert merged.count("different body text") == 1


def test_merge_archive_dedupes_identical_rearchived_lines():
    # A genuinely identical re-archive (same text) is still deduped, not counted
    # twice, and not given a suffix.
    from scripts.condense import merge_archive

    existing = (
        "# Board Archive\n\n"
        "- [X] same body — DONE — summary\n"
    )
    new = (
        "# Board Archive\n\n"
        "- [X] same body — DONE — summary\n"
    )
    merged = merge_archive(existing, new)
    assert merged.count("- [X]") == 1
    assert "- [X#2]" not in merged


def test_condense_archive_no_silent_drop_on_id_collision(tmp_path):
    # End-to-end regression mirroring the reported incident: two separate runs
    # each archive a distinct [MSG-001] (different bodies). Both must survive.
    import sys
    from pathlib import Path as _P

    if str(_P(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from scripts.condense import condense_command

    board = tmp_path / "BOARD.md"
    archive = tmp_path / "ARCH.md"
    state = tmp_path / "STATE.md"

    def _board_with_body(entry_id: str, body: str) -> str:
        return (
            "# Board\n\n"
            "## [%s] Resolved thing\n"
            "- Type: INFO\n"
            "- Status: DONE\n"
            "- Created: 2026-08-26\n"
            "- Body: |\n"
            "  %s\n\n"
            "<!-- New entries go above this line. -->\n" % (entry_id, body)
        )

    board.write_text(_board_with_body("MSG-001", "taxonomy finding from research"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0

    board.write_text(_board_with_body("MSG-001", "live-run finding from orchestrator"), encoding="utf-8")
    assert condense_command(
        ["--board", str(board), "--archive", str(archive),
         "--state", str(state), "--apply"]
    ) == 0

    archived = archive.read_text(encoding="utf-8")
    assert "taxonomy finding from research" in archived
    assert "live-run finding from orchestrator" in archived
    assert "- [MSG-001]" in archived
    assert "- [MSG-001#2]" in archived
