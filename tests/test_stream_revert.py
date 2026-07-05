from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.claude.events import parse_claude_stream_events
from fluxion.executors.codex.events import parse_codex_json_events
from fluxion.workspace.change_set import (
    build_stream_change_set,
    load_change_set,
    revert_change_set,
    save_change_set,
)


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


# --- executor stream -> operations -----------------------------------------


def _assistant_tool(name: str, tool_input: dict, tool_id: str) -> dict:
    return {
        "type": "assistant",
        "session_id": "s1",
        "message": {
            "type": "message",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def _tool_result(tool_id: str, text: str) -> dict:
    return {
        "type": "user",
        "message": {
            "type": "message",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
        },
    }


def test_claude_write_create_vs_overwrite(tmp_path: Path) -> None:
    stdout = _jsonl(
        _assistant_tool("Write", {"file_path": str(tmp_path / "new.txt"), "content": "hi"}, "t1"),
        _tool_result("t1", "File created successfully at: " + str(tmp_path / "new.txt")),
        _assistant_tool("Write", {"file_path": str(tmp_path / "old.txt"), "content": "x"}, "t2"),
        _tool_result("t2", "The file " + str(tmp_path / "old.txt") + " has been updated."),
    )

    ops = parse_claude_stream_events(stdout, workspace=tmp_path).operations

    assert {"op": "create", "path": "new.txt", "content": "hi"} in ops
    assert {"op": "overwrite", "path": "old.txt", "content": "x"} in ops


def test_claude_edit_and_multiedit_operations(tmp_path: Path) -> None:
    stdout = _jsonl(
        _assistant_tool(
            "Edit",
            {"file_path": str(tmp_path / "a.txt"), "old_string": "foo", "new_string": "bar"},
            "t1",
        ),
        _assistant_tool(
            "MultiEdit",
            {
                "file_path": str(tmp_path / "b.txt"),
                "edits": [
                    {"old_string": "1", "new_string": "2"},
                    {"old_string": "3", "new_string": "4", "replace_all": True},
                ],
            },
            "t2",
        ),
    )

    ops = parse_claude_stream_events(stdout, workspace=tmp_path).operations

    assert {
        "op": "edit",
        "path": "a.txt",
        "edits": [{"old": "foo", "new": "bar", "replace_all": False}],
    } in ops
    assert {
        "op": "edit",
        "path": "b.txt",
        "edits": [
            {"old": "1", "new": "2", "replace_all": False},
            {"old": "3", "new": "4", "replace_all": True},
        ],
    } in ops


def test_codex_file_change_operations(tmp_path: Path) -> None:
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "th1"},
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "status": "completed",
                "changes": [
                    {"path": str(tmp_path / "added.txt"), "kind": "add"},
                    {"path": str(tmp_path / "changed.txt"), "kind": "update"},
                    {"path": str(tmp_path / "gone.txt"), "kind": "delete"},
                ],
            },
        },
    )

    ops = parse_codex_json_events(stdout, workspace=tmp_path).operations

    assert ops == [
        {"op": "add", "path": "added.txt"},
        {"op": "update", "path": "changed.txt"},
        {"op": "delete", "path": "gone.txt"},
    ]


# --- build_stream_change_set + revert --------------------------------------


def _roundtrip_revert(tmp_path: Path, operations: list[dict]):
    cs = build_stream_change_set(
        run_id="r1", workspace=tmp_path, status="ok", operations=operations
    )
    save_change_set(tmp_path / "data", cs)
    assert load_change_set(tmp_path / "data", "r1") is not None
    return cs, revert_change_set(tmp_path / "data", "r1")


def test_revert_created_file_deletes_it(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    target.write_text("created body", encoding="utf-8")

    cs, result = _roundtrip_revert(
        tmp_path, [{"op": "create", "path": "new.txt", "content": "created body"}]
    )

    assert cs.recoverable_files == ["new.txt"]
    assert result.success is True
    assert not target.exists()


def test_revert_edit_reverse_applies_fragments(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    # Post-run on-disk state already has the edits applied.
    target.write_text("hello bar world bar", encoding="utf-8")

    cs, result = _roundtrip_revert(
        tmp_path,
        [
            {
                "op": "edit",
                "path": "a.txt",
                "edits": [{"old": "foo", "new": "bar", "replace_all": True}],
            }
        ],
    )

    assert cs.recoverable_files == ["a.txt"]
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "hello foo world foo"


def test_revert_single_edit_only_first_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("bar and bar", encoding="utf-8")

    _cs, result = _roundtrip_revert(
        tmp_path,
        [{"op": "edit", "path": "a.txt", "edits": [{"old": "foo", "new": "bar"}]}],
    )

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "foo and bar"


def test_overwrite_is_unrecoverable(tmp_path: Path) -> None:
    target = tmp_path / "old.txt"
    target.write_text("new content", encoding="utf-8")

    cs, result = _roundtrip_revert(
        tmp_path, [{"op": "overwrite", "path": "old.txt", "content": "new content"}]
    )

    assert cs.unrecoverable_files == ["old.txt"]
    assert result.success is False
    assert result.conflicts and "set FLUXION_REVERT_CAPTURE=full" in result.conflicts[0].reason
    assert target.read_text(encoding="utf-8") == "new content"


def test_codex_delete_is_unrecoverable(tmp_path: Path) -> None:
    cs = build_stream_change_set(
        run_id="r1",
        workspace=tmp_path,
        status="ok",
        operations=[{"op": "delete", "path": "gone.txt"}],
    )
    assert cs.unrecoverable_files == ["gone.txt"]
    assert cs.files[0].change_type == "deleted"


def test_create_then_delete_is_noop(tmp_path: Path) -> None:
    cs = build_stream_change_set(
        run_id="r1",
        workspace=tmp_path,
        status="ok",
        operations=[
            {"op": "create", "path": "tmp.txt", "content": "x"},
            {"op": "delete", "path": "tmp.txt"},
        ],
    )
    assert cs.has_changes is False


def test_edit_conflict_when_file_changed_after_run(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("bar", encoding="utf-8")
    cs = build_stream_change_set(
        run_id="r1",
        workspace=tmp_path,
        status="ok",
        operations=[{"op": "edit", "path": "a.txt", "edits": [{"old": "foo", "new": "bar"}]}],
    )
    save_change_set(tmp_path / "data", cs)
    # User edits the file after the run -> sha no longer matches the recorded state.
    target.write_text("something else entirely", encoding="utf-8")

    result = revert_change_set(tmp_path / "data", "r1")

    assert result.success is False
    assert result.conflicts[0].reason == "file changed after the run"
    assert target.read_text(encoding="utf-8") == "something else entirely"
