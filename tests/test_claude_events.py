from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.claude.events import (
    extract_claude_stream_message,
    parse_claude_stream_events,
)


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _assistant_tool(session_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "type": "message",
            "content": [{"type": "tool_use", "name": name, "input": tool_input}],
        },
    }


def test_parse_claude_stream_tool_changes(tmp_path: Path) -> None:
    stdout = _jsonl(
        {"type": "system", "subtype": "init", "session_id": "session-1"},
        _assistant_tool(
            "session-1",
            "Write",
            {"file_path": str(tmp_path / "new.json"), "content": "{}"},
        ),
        _assistant_tool(
            "session-1",
            "Edit",
            {"file_path": str(tmp_path / "existing.txt"), "old_string": "a", "new_string": "b"},
        ),
        _assistant_tool("session-1", "Bash", {"command": f"rm {tmp_path / 'delete_me.txt'}"}),
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-1",
            "result": "FINAL_ANSWER\nDone\nACTIONS_JSON\n{}",
        },
    )

    capture = parse_claude_stream_events(stdout, workspace=tmp_path)

    assert capture.is_stream_json is True
    assert capture.session_id == "session-1"
    assert capture.final_message == "FINAL_ANSWER\nDone\nACTIONS_JSON\n{}"
    assert capture.changed_files == ["new.json", "existing.txt", "delete_me.txt"]
    assert capture.risk_flags == []


def test_parse_claude_stream_unknown_shell_mutation_is_incomplete(tmp_path: Path) -> None:
    stdout = _jsonl(
        _assistant_tool(
            "session-1", "Bash", {"command": 'python -c \'open("x", "w").write("y")\''}
        ),
    )

    capture = parse_claude_stream_events(stdout, workspace=tmp_path)

    assert capture.changed_files == []
    assert capture.risk_flags == [
        "shell_side_effects_detected",
        "changed_files_may_be_incomplete",
    ]


def test_parse_claude_stream_plain_json_is_not_stream(tmp_path: Path) -> None:
    capture = parse_claude_stream_events(
        json.dumps({"result": "FINAL_ANSWER\nDone\nACTIONS_JSON\n{}"}),
        workspace=tmp_path,
    )

    assert capture.is_stream_json is True
    assert capture.final_message == "FINAL_ANSWER\nDone\nACTIONS_JSON\n{}"


def test_extract_claude_stream_message_ignores_interim_text() -> None:
    stdout = _jsonl(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "I will inspect first."}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "FINAL_ANSWER\nfinished\nACTIONS_JSON\n{}"}]
            },
        },
    )

    assert extract_claude_stream_message(stdout) == "FINAL_ANSWER\nfinished\nACTIONS_JSON\n{}"


def test_extract_claude_stream_message_tolerates_partial_line() -> None:
    stdout = (
        _jsonl(
            {
                "type": "result",
                "session_id": "session-1",
                "result": "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}",
            }
        )
        + '\n{"type": "assistant",'
    )

    assert extract_claude_stream_message(stdout) == "FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{}"
