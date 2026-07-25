"""SSE codec and the Responses event vocabulary.

Event names mirror `codex-rs/codex-api/src/sse/responses.rs` at `c8957bbf0f`.
"""

from __future__ import annotations

import json

from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    EV_CREATED,
    EV_FUNCTION_CALL_ARGUMENTS_DELTA,
    EV_OUTPUT_TEXT_DELTA,
    RECOGNIZED_EVENTS,
    SSEDecoder,
    encode_sse,
    is_terminal_event,
)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def test_decodes_a_single_event():
    events = SSEDecoder().feed(sse({"type": EV_CREATED}))
    assert [e.type for e in events] == [EV_CREATED]


def test_event_split_across_chunks_is_reassembled():
    """A single event routinely arrives across two TCP reads."""
    decoder = SSEDecoder()
    text = sse({"type": EV_OUTPUT_TEXT_DELTA, "delta": "hello"})
    midpoint = len(text) // 2
    assert decoder.feed(text[:midpoint]) == []
    events = decoder.feed(text[midpoint:])
    assert [e.data["delta"] for e in events] == ["hello"]


def test_multiple_events_in_one_chunk():
    decoder = SSEDecoder()
    events = decoder.feed(sse({"type": EV_CREATED}) + sse({"type": EV_COMPLETED}))
    assert [e.type for e in events] == [EV_CREATED, EV_COMPLETED]


def test_crlf_line_endings_are_handled():
    decoder = SSEDecoder()
    raw = f"data: {json.dumps({'type': EV_CREATED})}\r\n\r\n"
    assert [e.type for e in decoder.feed(raw)] == [EV_CREATED]


def test_comment_and_keepalive_lines_are_skipped():
    decoder = SSEDecoder()
    assert decoder.feed(": keepalive\n\n") == []
    assert [e.type for e in decoder.feed(sse({"type": EV_CREATED}))] == [EV_CREATED]


def test_done_sentinel_is_not_an_event():
    assert SSEDecoder().feed("data: [DONE]\n\n") == []


def test_multiline_data_is_joined_with_newlines():
    """Per the SSE spec, consecutive `data:` lines join with "\\n"."""
    decoder = SSEDecoder()
    body = json.dumps({"type": EV_OUTPUT_TEXT_DELTA, "delta": "x"}, indent=2)
    framed = "".join(f"data: {line}\n" for line in body.split("\n")) + "\n"
    events = decoder.feed(framed)
    assert [e.type for e in events] == [EV_OUTPUT_TEXT_DELTA]
    assert events[0].data["delta"] == "x"


def test_malformed_json_is_dropped_not_fatal():
    """One bad frame must not kill an in-flight stream."""
    decoder = SSEDecoder()
    assert decoder.feed("data: {not json\n\n") == []
    assert [e.type for e in decoder.feed(sse({"type": EV_CREATED}))] == [EV_CREATED]


def test_invalid_utf8_does_not_raise():
    decoder = SSEDecoder()
    payload = json.dumps({"type": EV_OUTPUT_TEXT_DELTA, "delta": "x"}).encode()
    assert decoder.feed(b"data: " + b"\xff\xfe" + b"\n\n") == []
    assert len(decoder.feed(b"data: " + payload + b"\n\n")) == 1


def test_flush_recovers_a_trailing_event_without_terminator():
    """Some upstreams close right after the final event, without the blank line."""
    decoder = SSEDecoder()
    assert decoder.feed(f"data: {json.dumps({'type': EV_COMPLETED})}\n") == []
    assert [e.type for e in decoder.flush()] == [EV_COMPLETED]


def test_flush_on_empty_buffer_yields_nothing():
    assert SSEDecoder().flush() == []


def test_event_without_a_type_field_decodes_with_empty_type():
    events = SSEDecoder().feed(sse({"no_type": 1}))
    assert events[0].type == ""


def test_encode_round_trips_through_the_decoder():
    payload = {"type": EV_OUTPUT_TEXT_DELTA, "delta": "hi"}
    events = SSEDecoder().feed(encode_sse(payload))
    assert events[0].data == payload


def test_encode_escapes_non_ascii_like_codex_does():
    encoded = encode_sse({"type": EV_OUTPUT_TEXT_DELTA, "delta": "中文"})
    assert b"\\u4e2d" in encoded
    assert SSEDecoder().feed(encoded)[0].data["delta"] == "中文"


def test_recognized_events_match_the_codex_parser():
    """Emitting an unknown event is safe; omitting a required one is not."""
    assert EV_CREATED in RECOGNIZED_EVENTS
    assert EV_FUNCTION_CALL_ARGUMENTS_DELTA in RECOGNIZED_EVENTS
    assert "response.made_up" not in RECOGNIZED_EVENTS
    assert len(RECOGNIZED_EVENTS) == 16


def test_terminal_events_end_the_stream():
    assert is_terminal_event(EV_COMPLETED)
    assert is_terminal_event("response.failed")
    assert is_terminal_event("error")
    assert not is_terminal_event(EV_OUTPUT_TEXT_DELTA)
