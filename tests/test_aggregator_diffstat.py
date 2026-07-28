"""What `diff_summary` reports for a run, and why the line counts are absent.

This file used to test `_parse_diffstat_totals`, a helper that parsed insertion
and deletion totals out of `git diff --stat`. It was written and tested but
never called: those totals cover the whole working tree, so attributing them to
one run would count unrelated uncommitted edits and every earlier run. The
helper is gone; these tests pin the contract that replaced it.
"""

from __future__ import annotations

from fluxion.web.services.aggregator import _apply_event, _initial_task


def _task_with_result(result: dict) -> dict:
    event = {"task_id": "t1", "status": "RETURNED", "task": {}, "result": result}
    task = _initial_task("t1", event)
    _apply_event(task, event)
    return task


def test_files_is_run_scoped_and_line_counts_are_declared_unmeasured() -> None:
    task = _task_with_result(
        {
            "success": True,
            "changed_files": ["a.swift", "b.swift"],
            # Whole-tree text from get_git_diff_summary(), counts and all.
            "diff_summary": (
                "Diff stat:\n a.swift | 286 ++++\n b.swift | 51 +\n"
                " 2 files changed, 338 insertions(+), 1 deletion(-)"
            ),
        }
    )

    summary = task["diff_summary"]
    assert summary["files"] == 2
    # The 338/1 above belong to the working tree, not to this run, so they are
    # deliberately not surfaced — and the reader is told they weren't measured
    # rather than left to read 0 as "nothing changed".
    assert summary["additions"] == 0
    assert summary["deletions"] == 0
    assert summary["lines_counted"] is False


def test_structured_per_run_diff_summary_is_passed_through() -> None:
    # An executor that reports real run-scoped numbers keeps them.
    task = _task_with_result(
        {
            "success": True,
            "changed_files": ["a.swift"],
            "diff_summary": {"files": 1, "additions": 12, "deletions": 3},
        }
    )
    assert task["diff_summary"] == {"files": 1, "additions": 12, "deletions": 3}


def test_a_run_touching_nothing_reports_no_files() -> None:
    task = _task_with_result({"success": True, "changed_files": [], "diff_summary": "No git diff."})
    assert task["diff_summary"]["files"] == 0
    assert task["diff_summary"]["lines_counted"] is False
