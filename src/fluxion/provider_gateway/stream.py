"""Stream commit state: the gate that makes failover safe.

Once we have emitted a semantically meaningful event downstream, the agent
runtime has already acted on it. Silently retrying the turn on another provider
from that point would replay tool calls — the same shell command or file write
running twice. So failover is allowed in exactly one state, and the check is a
type, not a convention.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ── Responses event vocabulary ───────────────────────────────────────
# The exact set Codex's SSE parser recognizes, read from
# `codex-rs/codex-api/src/sse/responses.rs` at `c8957bbf0f`. Anything outside
# this set hits the parser's `_ => trace!("unhandled responses event")` arm, so
# emitting an unknown event is safe; *omitting* a required one is not.

EV_CREATED = "response.created"
EV_METADATA = "response.metadata"
EV_OUTPUT_ITEM_ADDED = "response.output_item.added"
EV_OUTPUT_ITEM_DONE = "response.output_item.done"
EV_OUTPUT_TEXT_DELTA = "response.output_text.delta"
EV_FUNCTION_CALL_ARGUMENTS_DELTA = "response.function_call_arguments.delta"
EV_CUSTOM_TOOL_CALL_INPUT_DELTA = "response.custom_tool_call_input.delta"
EV_REASONING_SUMMARY_PART_ADDED = "response.reasoning_summary_part.added"
EV_REASONING_SUMMARY_TEXT_DELTA = "response.reasoning_summary_text.delta"
EV_REASONING_SUMMARY_TEXT_DONE = "response.reasoning_summary_text.done"
EV_REASONING_TEXT_DELTA = "response.reasoning_text.delta"
EV_NEW_TOOL_EVENT = "response.new_tool_event"
EV_COMPLETED = "response.completed"
EV_FAILED = "response.failed"
EV_INCOMPLETE = "response.incomplete"
EV_ERROR = "error"

# Keepalive. Not part of the Responses protocol — deliberately a name no client
# implements, so it can only ever be ignored.
#
# It has to be a real event with a non-empty `data:` payload rather than an SSE
# comment (`: ping`). Codex times the stream with
# `timeout(idle_timeout, stream.next())` over an `.eventsource()`-parsed stream
# (`codex-client/src/sse.rs`, `codex-api/src/sse/responses.rs`), and that parser
# drops comment lines and refuses to dispatch an event whose data buffer is
# empty. A comment therefore produces no item, does not reset the timer, and
# would make this whole mechanism a no-op with no symptom — the turn would still
# be killed at the timeout while the logs showed heartbeats going out.
#
# An unknown type is safe on the other side: Codex's event match ends in
# `_ => trace!("unhandled responses event")`, and even a payload it cannot parse
# at all is a `debug!` and a `continue`.
EV_HEARTBEAT = "fluxion.heartbeat"

RECOGNIZED_EVENTS = frozenset(
    {
        EV_CREATED,
        EV_METADATA,
        EV_OUTPUT_ITEM_ADDED,
        EV_OUTPUT_ITEM_DONE,
        EV_OUTPUT_TEXT_DELTA,
        EV_FUNCTION_CALL_ARGUMENTS_DELTA,
        EV_CUSTOM_TOOL_CALL_INPUT_DELTA,
        EV_REASONING_SUMMARY_PART_ADDED,
        EV_REASONING_SUMMARY_TEXT_DELTA,
        EV_REASONING_SUMMARY_TEXT_DONE,
        EV_REASONING_TEXT_DELTA,
        EV_NEW_TOOL_EVENT,
        EV_COMPLETED,
        EV_FAILED,
        EV_INCOMPLETE,
        EV_ERROR,
    }
)

# Events that end the stream. After one of these, nothing more may be written.
TERMINAL_EVENTS = frozenset({EV_COMPLETED, EV_FAILED, EV_INCOMPLETE, EV_ERROR})


def is_terminal_event(event_type: str) -> bool:
    return event_type in TERMINAL_EVENTS


def heartbeat_event(response_id: str) -> dict[str, Any]:
    """A keepalive frame carrying no content.

    Kept out of `RECOGNIZED_EVENTS`: this is Fluxion's own framing, not part of
    the protocol being spoken, and nothing downstream should start treating it
    as one.
    """
    return {"type": EV_HEARTBEAT, "response_id": response_id}


# ── SSE codec ────────────────────────────────────────────────────────

_DONE_SENTINEL = "[DONE]"


@dataclass(frozen=True)
class SSEEvent:
    """One decoded server-sent event."""

    data: Mapping[str, Any]
    raw: str

    @property
    def type(self) -> str:
        value = self.data.get("type")
        return value if isinstance(value, str) else ""


class SSEDecoder:
    """Incremental SSE decoder.

    Feed-based rather than line-based because upstream chunks split wherever TCP
    decides to: a single event routinely arrives across two reads, and an event
    boundary rarely aligns with a chunk boundary.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str | bytes) -> list[SSEEvent]:
        """Decode whatever complete events `chunk` completes."""
        if isinstance(chunk, bytes):
            # Malformed UTF-8 must not kill an in-flight stream; a mangled
            # character costs one garbled token, a raised exception costs the
            # whole turn.
            chunk = chunk.decode("utf-8", errors="replace")
        self._buffer += chunk

        events: list[SSEEvent] = []
        # Normalize CRLF so the blank-line split works regardless of the peer's
        # line endings.
        self._buffer = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in self._buffer:
            block, _, self._buffer = self._buffer.partition("\n\n")
            event = _decode_block(block)
            if event is not None:
                events.append(event)
        return events

    def flush(self) -> list[SSEEvent]:
        """Decode a trailing event that arrived without its blank-line terminator.

        Some upstreams close the connection right after the final event without
        the terminating newline. Dropping it would silently lose the response's
        completion marker.
        """
        remaining, self._buffer = self._buffer.strip(), ""
        if not remaining:
            return []
        event = _decode_block(remaining)
        return [event] if event is not None else []


def _decode_block(block: str) -> SSEEvent | None:
    data_lines: list[str] = []
    for line in block.split("\n"):
        # A leading colon marks a comment/keepalive; there is no payload to read.
        if not line or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field != "data":
            # `event:` and `id:` carry no payload for the Responses protocol,
            # which puts the discriminator in the JSON body instead.
            continue
        data_lines.append(value[1:] if value.startswith(" ") else value)

    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    if raw.strip() == _DONE_SENTINEL:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return SSEEvent(data=decoded, raw=raw) if isinstance(decoded, Mapping) else None


def encode_sse(payload: Mapping[str, Any]) -> bytes:
    """Serialize one event in the framing Codex expects.

    `ensure_ascii` mirrors Codex's own `to_ascii_json_string`, keeping payloads
    byte-identical to what it produces and sidestepping any intermediary that
    mishandles raw UTF-8 in an SSE body.
    """
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"data: {body}\n\n".encode("ascii")
