"""Render a local agent run as an Anthropic Messages event stream.

The Responses renderer in `upstream/local_agent.py` and this one consume the
same protocol-agnostic `(channel, chunk, run)` sequence from the executor
bridge; only the wire shape differs.

Event names and framing verified against `claude-cli/2.1.219`. Two differences
from the Responses side are worth knowing before changing anything here:

- Framing carries a named event: `event: <type>\\ndata: <json>`. A bare `data:`
  line is what the Responses API uses and is silently ignored by an Anthropic
  client, which then waits out its read timeout.
- Content arrives as indexed blocks that must be opened and closed in order.
  Every `content_block_start` needs its `content_block_stop`, or the client
  keeps the message open after `message_stop`.

Reasoning is deliberately *not* emitted as a `thinking` block. Real thinking
blocks carry a `signature` the client may verify, and an unsigned one risks
failing the whole turn; the agent's working notes are folded into the text
instead, which is what the answer channel already does on this path.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

# Anthropic stop reasons. A local agent always runs to its own completion, so
# the turn is only ever a natural end or an error.
STOP_END_TURN = "end_turn"

# Internal, never written to the wire. The Anthropic protocol has nowhere to
# carry what the gateway records after a turn — the agent's session id above
# all, without which the next turn cannot resume and the conversation silently
# restarts. The Responses side gets this from its own `fluxion` block.
FLUXION_RESULT = "fluxion.result"

# Anthropic's own keepalive. Unlike the Responses side — where the heartbeat has
# to be an invented type because the protocol has none — `ping` is part of this
# protocol and every Messages client already knows to ignore it, so there is no
# reason to invent anything here.
EV_PING = "ping"


def ping_event() -> dict[str, Any]:
    """A keepalive carrying no content."""
    return {"type": EV_PING}


def encode_messages_sse(payload: Mapping[str, Any]) -> bytes:
    """Frame one event the way an Anthropic client parses it."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {payload['type']}\ndata: {body}\n\n".encode()


def fresh_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def message_start(message_id: str, model: str, input_tokens: int) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    }


def content_block_start(index: int = 0) -> dict[str, Any]:
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    }


def content_block_delta(text: str, index: int = 0) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }


def content_block_stop(index: int = 0) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def message_delta(output_tokens: int, stop_reason: str = STOP_END_TURN) -> dict[str, Any]:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }


def message_stop() -> dict[str, Any]:
    return {"type": "message_stop"}


def error_event(message: str, kind: str = "api_error") -> dict[str, Any]:
    """An in-stream failure.

    Sent as an event rather than an HTTP status because by this point the 200
    and its headers are already on the wire.
    """
    return {"type": "error", "error": {"type": kind, "message": message}}


def non_streaming_message(
    message_id: str, model: str, text: str, input_tokens: int, output_tokens: int
) -> dict[str, Any]:
    """The whole response as one object, for a request without `stream`."""
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": STOP_END_TURN,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def fluxion_result(run: Any) -> dict[str, Any]:
    """Carry the run's bookkeeping to the gateway, not to the client."""
    return {
        "type": FLUXION_RESULT,
        "success": bool(getattr(run, "success", False)),
        "executor_session_id": str(getattr(run, "session_id", "") or ""),
        "changed_files": list(getattr(run, "changed_files", ()) or ()),
        "agent_token_usage": dict(getattr(run, "token_usage", {}) or {}),
    }
