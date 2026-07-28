"""The Anthropic Messages side of the gateway.

Anything that speaks Anthropic's `/v1/messages` can reach a local agent here —
Claude Code is the client this was built against, but nothing below is specific
to it beyond where the session id is read from.

Shapes verified by pointing `claude-cli/2.1.219` at a logging server with
`ANTHROPIC_BASE_URL`, not from documentation: `POST /v1/messages?beta=true`,
`anthropic-version: 2023-06-01`, streaming on, the conversation resent in full
every turn, and a stable `X-Claude-Code-Session-Id`.

Two consequences of that shape drive this module:

- The API is stateless, so continuity has to be reconstructed here. See
  `extract_messages_prompt` for what "reconstructed" means and when.
- The client declares its own tools (40 of them for Claude Code) and expects the
  model to drive them. A local agent runs its own tools inside its own loop, so
  those never fire. This ingress therefore serves callers that want an *answer*,
  not callers that want a model to drive their loop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fluxion.provider_gateway.identity import (
    IdentityConfidence,
    RequestIdentity,
    derive_route_key,
)
from fluxion.provider_gateway.request import NormalizedRequest, RawRequest

INGRESS_NAME = "anthropic"

# Claude Code sends the same session id twice: as a header, and inside the
# `metadata.user_id` JSON blob. The header is read first because it needs no
# parsing, but both are checked — a different client may send only one.
SESSION_HEADER = "x-claude-code-session-id"

# `metadata.user_id` is a JSON *string*, not an object, holding device_id,
# account_uuid, and session_id.
_METADATA_SESSION_KEYS = ("session_id", "account_uuid", "device_id")


@dataclass(frozen=True)
class MessagesIdentity:
    session_id: str | None = None
    account_id: str | None = None


class MessagesIdentityExtractor:
    """Derives a stable conversation key from a Messages request."""

    def __call__(self, request: NormalizedRequest) -> RequestIdentity:
        parsed = self.parse(request)
        if parsed.session_id:
            return RequestIdentity(
                ingress=INGRESS_NAME,
                route_key=derive_route_key(INGRESS_NAME, [parsed.account_id, parsed.session_id]),
                confidence=IdentityConfidence.EXPLICIT,
                session_id=parsed.session_id,
            )
        # No session id: every turn is its own conversation. That is correct
        # rather than merely safe — sharing a key across unrelated turns would
        # resume some other conversation's agent session into this one.
        return RequestIdentity(
            ingress=INGRESS_NAME,
            route_key=derive_route_key(INGRESS_NAME, [_fingerprint(request)]),
            confidence=IdentityConfidence.EPHEMERAL,
        )

    def parse(self, request: NormalizedRequest) -> MessagesIdentity:
        header = request.raw.headers.get(SESSION_HEADER, "").strip()
        blob = _metadata_blob(request.metadata)
        return MessagesIdentity(
            session_id=header or str(blob.get("session_id") or "").strip() or None,
            account_id=str(blob.get("account_uuid") or "").strip() or None,
        )


def _metadata_blob(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap `metadata.user_id`, which is a JSON string rather than an object."""
    user_id = metadata.get("user_id")
    if isinstance(user_id, Mapping):
        return user_id
    if not isinstance(user_id, str):
        return {}
    try:
        parsed = json.loads(user_id)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _fingerprint(request: NormalizedRequest) -> str:
    """A per-request key for a client that identifies nothing."""
    return f"anon:{id(request.raw.body):x}"


class AnthropicMessagesIngress:
    """The Messages-facing side of the gateway."""

    name = INGRESS_NAME

    def __init__(self) -> None:
        self.identity = MessagesIdentityExtractor()

    def normalize(self, raw: RawRequest) -> NormalizedRequest:
        return NormalizedRequest.from_raw(raw)

    def extract_identity(self, request: NormalizedRequest) -> RequestIdentity:
        return self.identity(request)


def extract_messages_prompt(body: Mapping[str, Any], *, resuming: bool) -> str:
    """Build the local agent's prompt from a Messages request.

    `resuming` decides how much of the conversation to send, and getting it
    wrong is silent both ways.

    The Messages API is stateless: the client resends the whole conversation
    every turn. A local agent is the opposite — it keeps its own session and is
    resumed by id. When the two are paired and a session *is* being resumed,
    the agent already remembers everything, so only the newest message belongs
    in the prompt; replaying the transcript would duplicate its own memory back
    at it and pay for the whole history again on every turn.

    When there is no session to resume — first turn, expired sticky entry,
    evicted row, a different workspace — the agent starts blank. Sending only
    the last message then produces an agent that has plainly forgotten the
    conversation, mid-conversation, with no error anywhere. The history the
    client just sent is exactly what is needed to rebuild that context, so on a
    cold start it is included.

    The system prompt is always included: it is the caller's framing, and it is
    not part of what the agent's own session remembers.
    """
    system = _system_text(body.get("system"))
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return system.strip()

    turns = [turn for turn in messages if isinstance(turn, Mapping)]
    if resuming:
        selected = _last_user_turn(turns)
    else:
        selected = [_render_turn(turn) for turn in turns]

    body_text = "\n\n".join(part for part in selected if part)
    return "\n\n".join(part for part in (system, body_text) if part).strip()


def _last_user_turn(turns: Sequence[Mapping[str, Any]]) -> list[str]:
    for turn in reversed(turns):
        if turn.get("role") == "user":
            text = _content_text(turn.get("content"))
            return [text] if text else []
    return []


def _render_turn(turn: Mapping[str, Any]) -> str:
    """One transcript line, labelled by speaker.

    Labels matter on the cold-start path: without them a replayed conversation
    reads as one undifferentiated block, and the agent cannot tell which parts
    it supposedly said.
    """
    text = _content_text(turn.get("content"))
    if not text:
        return ""
    role = str(turn.get("role") or "user").strip().lower()
    label = "Assistant" if role == "assistant" else "User"
    return f"{label}: {text}"


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system.strip()
    return _content_text(system)


def _content_text(content: Any) -> str:
    """Flatten a content value, keeping only what a text prompt can carry.

    Tool results and images are dropped rather than rendered. They belong to the
    client's own tool loop, which does not run on this path at all, so quoting
    them would describe work the agent never did.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, Sequence):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part).strip()
