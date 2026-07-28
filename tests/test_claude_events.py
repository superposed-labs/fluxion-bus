from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.claude.events import (
    describe_tool_use,
    extract_claude_stream_message,
    extract_claude_stream_reasoning,
    extract_claude_stream_text,
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


def _assistant_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_extract_claude_stream_text_returns_narration_without_a_marker() -> None:
    # The marker-gated reader yields "" for a whole run when the prompt never
    # asked for FINAL_ANSWER, which reads to a live consumer as a hung stream.
    stdout = _jsonl(_assistant_text("Reading parser.py."), _assistant_text("Patched line 40."))

    assert extract_claude_stream_message(stdout) == ""
    assert extract_claude_stream_text(stdout) == "Reading parser.py.\n\nPatched line 40."


def test_extract_claude_stream_text_grows_monotonically() -> None:
    # The streaming caller sends `current[sent_len:]` as a delta, so a shrinking
    # result would silently swallow output instead of emitting it.
    events = [_assistant_text("one"), _assistant_text("two"), _assistant_text("three")]
    lengths = [len(extract_claude_stream_text(_jsonl(*events[:n]))) for n in range(1, 4)]

    assert lengths == sorted(lengths)
    assert len(set(lengths)) == 3


def _partial(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
    }


def test_extract_claude_stream_text_reads_partial_message_chunks() -> None:
    # Without these the caller sees nothing until a whole message is finished,
    # which for a long turn means one silent minute then the entire answer.
    stdout = _jsonl(_partial("Hel"), _partial("lo"))
    assert extract_claude_stream_text(stdout) == "Hello"


def test_partial_chunks_are_superseded_not_added_to() -> None:
    """The finished message repeats what the partials already carried."""
    stdout = _jsonl(_partial("Hel"), _partial("lo"), _assistant_text("Hello"))
    assert extract_claude_stream_text(stdout) == "Hello"


def test_partial_and_finished_messages_interleave_monotonically() -> None:
    events = [
        _partial("first"),
        _assistant_text("first"),
        _partial("sec"),
        _partial("ond"),
        _assistant_text("second"),
    ]
    lengths = [
        len(extract_claude_stream_text(_jsonl(*events[:n]))) for n in range(1, len(events) + 1)
    ]
    assert lengths == sorted(lengths), lengths
    assert extract_claude_stream_text(_jsonl(*events)) == "first\n\nsecond"


def test_thinking_and_tool_argument_deltas_are_not_user_text() -> None:
    stdout = _jsonl(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"a":'},
            },
        },
        _partial("visible"),
    )
    assert extract_claude_stream_text(stdout) == "visible"


def _thinking_delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": text},
        },
    }


def _assistant_tool_use(name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]},
    }


def test_reasoning_collects_thinking_and_tool_activity() -> None:
    stdout = _jsonl(
        _thinking_delta("Let me look at "),
        _thinking_delta("the README."),
        _assistant_tool_use("Read", {"file_path": "README.md"}),
        _assistant_text("The README covers routing."),
    )
    reasoning = extract_claude_stream_reasoning(stdout)

    assert "Let me look at the README." in reasoning
    assert "Read(README.md)" in reasoning
    # The answer belongs to the other channel.
    assert "The README covers routing." not in reasoning


def test_reasoning_and_answer_do_not_overlap() -> None:
    stdout = _jsonl(_thinking_delta("thinking hard"), _assistant_text("the answer"))
    assert extract_claude_stream_reasoning(stdout) == "thinking hard"
    assert extract_claude_stream_text(stdout) == "the answer"


def test_reasoning_grows_monotonically() -> None:
    """The caller sends `current[sent_len:]`; a shrink would swallow output."""
    events = [
        _thinking_delta("abc"),
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "abc"}]}},
        _assistant_tool_use("Grep", {"pattern": "route"}),
    ]
    lengths = [len(extract_claude_stream_reasoning(_jsonl(*events[:n]))) for n in range(1, 4)]
    assert lengths == sorted(lengths), lengths


def test_tool_descriptions_stay_short_and_named() -> None:
    assert describe_tool_use({"name": "Read", "input": {"file_path": "a.py"}}) == "Read(a.py)"
    assert describe_tool_use({"name": "Bash", "input": {"command": "x" * 200}}).endswith("...)")
    # An unknown tool still says something rather than vanishing.
    assert describe_tool_use({"name": "MysteryTool", "input": {"q": 1}}) == "MysteryTool"


def test_tool_paths_are_shortened_for_a_collapsed_view() -> None:
    """A full workspace path pushes the filename past the truncation limit."""
    described = describe_tool_use(
        {"name": "Read", "input": {"file_path": "/very/long/ws/src/app.py"}}
    )
    assert described == "Read(src/app.py)"
