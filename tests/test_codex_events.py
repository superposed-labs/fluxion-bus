from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.codex.events import (
    extract_codex_json_stream_message,
    extract_codex_stream_reasoning,
    parse_codex_json_events,
)


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


def test_parse_codex_error_message_unwraps_nested_json(tmp_path: Path) -> None:
    # Mirrors a real bad-model run: the actionable message is buried in a
    # JSON-string error payload, not the generic "Codex execution failed".
    nested = json.dumps(
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "message": "The 'bogus-model' model is not supported with a ChatGPT account.",
            },
        }
    )
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "th1"},
        {"type": "turn.started"},
        {"type": "error", "message": nested},
        {"type": "turn.failed", "error": {"message": nested}},
    )

    capture = parse_codex_json_events(stdout, workspace=tmp_path)

    assert capture.error_message == (
        "The 'bogus-model' model is not supported with a ChatGPT account."
    )


def test_parse_codex_file_change_events(tmp_path: Path) -> None:
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"},
        {
            "type": "item.started",
            "item": {
                "type": "file_change",
                "status": "in_progress",
                "changes": [{"path": str(tmp_path / "ignored.txt"), "kind": "add"}],
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "status": "completed",
                "changes": [
                    {"path": str(tmp_path / "gone.txt"), "kind": "delete"},
                    {"path": str(tmp_path / "existing.txt"), "kind": "update"},
                    {"path": str(tmp_path / "new.json"), "kind": "add"},
                    {"path": "/outside.txt", "kind": "add"},
                ],
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}"},
        },
    )

    capture = parse_codex_json_events(stdout, workspace=tmp_path)

    assert capture.is_jsonl is True
    assert capture.session_id == "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"
    assert capture.final_message == "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}"
    assert capture.changed_files == ["gone.txt", "existing.txt", "new.json"]
    assert capture.risk_flags == []


def test_parse_codex_shell_mutation_without_file_change_is_incomplete(tmp_path: Path) -> None:
    stdout = _jsonl(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "status": "completed",
                "command": '/bin/zsh -lc "printf hi > new.txt && rm -f old.txt"',
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
    )

    capture = parse_codex_json_events(stdout, workspace=tmp_path)

    assert capture.changed_files == []
    assert capture.risk_flags == [
        "shell_side_effects_detected",
        "changed_files_may_be_incomplete",
    ]


def test_parse_codex_plain_text_is_not_jsonl(tmp_path: Path) -> None:
    capture = parse_codex_json_events(
        'FINAL_ANSWER:\n{"looks": "json"}\nACTIONS_JSON:\n{}',
        workspace=tmp_path,
    )

    assert capture.is_jsonl is False
    assert capture.changed_files == []


def test_extract_codex_json_stream_message_ignores_interim_messages() -> None:
    stdout = _jsonl(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I will inspect."}},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "FINAL_ANSWER\nfinished\nACTIONS_JSON\n{}"},
        },
    )

    assert extract_codex_json_stream_message(stdout) == "FINAL_ANSWER\nfinished\nACTIONS_JSON\n{}"


def test_extract_codex_json_stream_message_tolerates_partial_line() -> None:
    stdout = (
        _jsonl(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}"},
            }
        )
        + '\n{"type": "item.completed",'
    )

    assert extract_codex_json_stream_message(stdout) == "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}"


def _item_event(kind: str, item_id: str, **fields) -> dict:
    return {"type": kind, "item": {"id": item_id, **fields}}


def test_codex_reasoning_collects_working_not_the_answer() -> None:
    stdout = "\n".join(
        json.dumps(e)
        for e in [
            _item_event("item.completed", "i1", type="reasoning", text="Checking the routes."),
            _item_event("item.completed", "i2", type="command_execution", command="ls -la"),
            _item_event("item.completed", "i3", type="agent_message", text="Four routes exist."),
        ]
    )
    reasoning = extract_codex_stream_reasoning(stdout)

    assert "Checking the routes." in reasoning
    assert "$ ls -la" in reasoning
    # The answer belongs to the other channel.
    assert "Four routes exist." not in reasoning


def test_codex_items_are_deduped_by_id() -> None:
    """started / updated / completed all carry the same item; appending each
    would repeat every command two or three times."""
    stdout = "\n".join(
        json.dumps(e)
        for e in [
            _item_event("item.started", "i1", type="command_execution", command="pytest"),
            _item_event("item.updated", "i1", type="command_execution", command="pytest"),
            _item_event("item.completed", "i1", type="command_execution", command="pytest"),
        ]
    )
    assert extract_codex_stream_reasoning(stdout) == "$ pytest"


def test_codex_reasoning_grows_monotonically() -> None:
    events = [
        _item_event("item.completed", "i1", type="reasoning", text="first"),
        _item_event("item.completed", "i2", type="command_execution", command="ls"),
        _item_event("item.completed", "i3", type="reasoning", text="second"),
    ]
    lengths = [
        len(extract_codex_stream_reasoning("\n".join(json.dumps(e) for e in events[:n])))
        for n in range(1, 4)
    ]
    assert lengths == sorted(lengths), lengths


def test_codex_file_changes_are_named() -> None:
    stdout = json.dumps(
        _item_event(
            "item.completed",
            "i1",
            type="file_change",
            changes=[{"path": "/ws/src/app.py", "kind": "update"}],
        )
    )
    assert extract_codex_stream_reasoning(stdout) == "Edit src/app.py"
