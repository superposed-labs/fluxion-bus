"""Reading agy's `--output-format stream-json` output.

Every event below is shaped after a live `agy 1.1.14` run; the answer fixture is
a trimmed capture of one, including the four-chunk `text_delta` split that the
answer reassembly depends on.
"""

from __future__ import annotations

import json

from fluxion.executors.antigravity.events import (
    agy_answer_text,
    agy_conversation_id,
    agy_reasoning_text,
    agy_result_error,
    describe_tool_step,
    iter_agy_events,
)


def _line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _init(conversation_id: str = "conv-1") -> str:
    return _line(
        {
            "event": "init",
            "conversation_id": conversation_id,
            "init": {"cwd": "/repo", "permission_mode": "always-proceed"},
        }
    )


def _tool(index: int, state: str, name: str, parameters: dict) -> str:
    return _line(
        {
            "event": "step_update",
            "step_update": {
                "step_index": index,
                "state": state,
                "step_type": "tool",
                "tool_name": name,
                "tool_info": {"name": name, "parameters": parameters},
            },
        }
    )


def _answer(index: int, state: str, delta: str) -> str:
    return _line(
        {
            "event": "step_update",
            "step_update": {
                "step_index": index,
                "state": state,
                "step_type": "agent_response",
                "text_delta": delta,
            },
        }
    )


def _result(status: str, response: str, error: str = "") -> str:
    result = {
        "conversation_id": "conv-1",
        "status": status,
        "response": response,
        "duration_seconds": 12.3,
        "usage": {"input_tokens": 1, "output_tokens": 2, "thinking_tokens": 3},
    }
    if error:
        result["error"] = error
    return _line({"event": "result", "result": result})


# ── the answer ───────────────────────────────────────────────────────
def test_text_deltas_reassemble_into_the_answer():
    """Chunks are incremental — the live capture's concatenation is the response."""
    stdout = (
        _init()
        + _answer(4, "ACTIVE", "FINAL_ANSWER:\nThe file ")
        + _answer(4, "ACTIVE", "a.txt holds one ")
        + _answer(4, "DONE", 'word.\nACTIONS_JSON:\n{"upload_files": []}\n')
        + _result(
            "SUCCESS",
            'FINAL_ANSWER:\nThe file a.txt holds one word.\nACTIONS_JSON:\n{"upload_files": []}\n',
        )
    )
    assert agy_answer_text(stdout) == (
        'FINAL_ANSWER:\nThe file a.txt holds one word.\nACTIONS_JSON:\n{"upload_files": []}\n'
    )


def test_the_answer_only_grows():
    """The executor streams this as a prefix delta; a rewrite would garble it."""
    stdout = _init()
    seen = ""
    for chunk in (
        _answer(2, "ACTIVE", "one "),
        _answer(2, "ACTIVE", "two "),
        _answer(2, "DONE", "three"),
        _result("SUCCESS", "one two three"),
    ):
        stdout += chunk
        current = agy_answer_text(stdout)
        assert current.startswith(seen)
        seen = current
    assert seen == "one two three"


def test_a_response_without_deltas_still_lands():
    stdout = _init() + _result("SUCCESS", "the answer")
    assert agy_answer_text(stdout) == "the answer"


def test_output_that_is_not_the_event_stream_passes_through():
    """A crash notice printed before the stream opens is all we have."""
    assert agy_answer_text("agy: language server failed\n") == "agy: language server failed\n"
    assert agy_answer_text("") == ""


def test_noise_after_the_stream_opens_is_not_part_of_the_answer():
    """Once events are flowing, only events count — nothing else reaches a channel."""
    stdout = _init() + "stray line agy wrote to stdout\n" + _answer(2, "DONE", "real answer")
    assert agy_answer_text(stdout) == "real answer"


def test_a_half_written_line_is_not_an_event_yet():
    stdout = _init() + _answer(2, "DONE", "done") + '{"event":"result","resu'
    assert agy_answer_text(stdout) == "done"


# ── working notes ────────────────────────────────────────────────────
def test_each_tool_is_reported_once():
    """Tools surface twice — ACTIVE, then DONE — with identical parameters."""
    stdout = (
        _init()
        + _tool(3, "ACTIVE", "view_file", {"AbsolutePath": "/repo/a.txt"})
        + _tool(3, "DONE", "view_file", {"AbsolutePath": "/repo/a.txt"})
    )
    assert agy_reasoning_text(stdout) == "view_file(repo/a.txt)"


def test_reading_the_same_file_later_is_real_work():
    stdout = (
        _init()
        + _tool(3, "DONE", "view_file", {"AbsolutePath": "/repo/a.txt"})
        + _tool(9, "DONE", "view_file", {"AbsolutePath": "/repo/a.txt"})
    )
    assert agy_reasoning_text(stdout).count("view_file(repo/a.txt)") == 2


def test_a_failed_tool_says_so():
    """Nothing else in the run reports this: agy exits zero over failed steps."""
    stdout = (
        _init()
        + _tool(7, "ACTIVE", "run_command", {"CommandLine": "pytest -q"})
        + _tool(7, "ERROR", "run_command", {"CommandLine": "pytest -q"})
    )
    assert agy_reasoning_text(stdout) == "$ pytest -q\n\n$ pytest -q failed"


def test_working_notes_only_grow():
    stdout = _init()
    seen = ""
    for chunk in (
        _tool(3, "ACTIVE", "view_file", {"AbsolutePath": "/repo/a.txt"}),
        _tool(3, "DONE", "view_file", {"AbsolutePath": "/repo/a.txt"}),
        _tool(5, "ACTIVE", "run_command", {"CommandLine": "ls"}),
    ):
        stdout += chunk
        current = agy_reasoning_text(stdout)
        assert current.startswith(seen)
        seen = current


def test_answer_text_is_not_working_notes():
    """They go to different places; mixing them dumps scratch work into the reply."""
    stdout = _init() + _answer(4, "DONE", "the answer")
    assert agy_reasoning_text(stdout) == ""


# ── rendering ────────────────────────────────────────────────────────
def test_a_command_is_rendered_as_what_ran():
    step = {"tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "wc -l *.txt"}}}
    assert describe_tool_step(step) == "$ wc -l *.txt"


def test_absolute_paths_are_shortened_to_the_useful_end():
    step = {
        "tool_name": "view_file",
        "tool_info": {"parameters": {"AbsolutePath": "/a/very/long/prefix/pkg/mod.py"}},
    }
    assert describe_tool_step(step) == "view_file(pkg/mod.py)"


def test_a_search_is_named_by_its_query():
    step = {
        "tool_name": "grep_search",
        "tool_info": {"parameters": {"Query": "def execute", "SearchPath": "/repo"}},
    }
    assert describe_tool_step(step) == "grep_search(def execute)"


def test_a_tool_without_a_recognised_subject_is_still_named():
    step = {"tool_name": "manage_task", "tool_info": {"parameters": {"Action": "status"}}}
    assert describe_tool_step(step) == "manage_task"


def test_long_subjects_are_clipped():
    step = {"tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "x" * 200}}}
    rendered = describe_tool_step(step)
    assert len(rendered) < 100
    assert rendered.endswith("...")


def test_multiline_commands_stay_on_one_line():
    step = {"tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "a\nb"}}}
    assert describe_tool_step(step) == "$ a b"


# ── identity and terminal status ─────────────────────────────────────
def test_the_conversation_id_comes_off_the_first_line():
    assert agy_conversation_id(_init("conv-42") + _answer(1, "DONE", "hi")) == "conv-42"
    assert agy_conversation_id("no events here") == ""


def test_a_run_that_stopped_without_answering_is_a_failure():
    """Otherwise this reads as success and the user is told 'Task completed.'"""
    stdout = _init() + _result("ERROR", "", error="context canceled")
    assert agy_result_error(stdout) == (
        "AntiGravity ended with status ERROR before it answered: context canceled"
    )


def test_a_canceled_run_names_its_status():
    assert "CANCELED" in agy_result_error(_init() + _result("CANCELED", ""))


def test_an_answered_run_outranks_its_status():
    """agy reports ERROR/context canceled for a turn that killed its own
    background task — measured, with the answer already delivered."""
    stdout = (
        _init() + _answer(4, "DONE", "hello") + _result("ERROR", "hello", error="context canceled")
    )
    assert agy_result_error(stdout) == ""


def test_a_run_still_in_flight_reports_nothing():
    assert (
        agy_result_error(_init() + _tool(3, "ACTIVE", "list_dir", {"DirectoryPath": "/repo"})) == ""
    )
    assert agy_result_error(_init() + _result("SUCCESS", "done")) == ""


def test_events_are_parsed_in_order():
    events = iter_agy_events(_init() + _answer(1, "DONE", "x") + _result("SUCCESS", "x"))
    assert [event["event"] for event in events] == ["init", "step_update", "result"]
