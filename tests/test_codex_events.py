from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.codex.events import (
    extract_codex_json_stream_message,
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
