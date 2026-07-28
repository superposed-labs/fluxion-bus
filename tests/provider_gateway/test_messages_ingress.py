"""The Anthropic Messages ingress: identity, prompt assembly, and wire shape.

Request shapes are copied from a real `claude-cli/2.1.219` request captured by
pointing `ANTHROPIC_BASE_URL` at a logging server.
"""

from __future__ import annotations

import json

from fluxion.provider_gateway.identity import IdentityConfidence
from fluxion.provider_gateway.ingress.messages import (
    AnthropicMessagesIngress,
    extract_messages_prompt,
)
from fluxion.provider_gateway.messages_stream import (
    encode_messages_sse,
    message_start,
    message_stop,
)
from fluxion.provider_gateway.request import RawRequest

SESSION = "9653cd7b-b208-4c59-b96f-e58bf8ed39f1"
ACCOUNT = "4a30fd4e-f558-48ce-b087-f428c1c1d00c"

# `metadata.user_id` arrives as a JSON *string*, not an object.
USER_ID_BLOB = json.dumps({"device_id": "a04022d5", "account_uuid": ACCOUNT, "session_id": SESSION})


def identify(body=None, headers=None):
    ingress = AnthropicMessagesIngress()
    raw = RawRequest.create(body or {}, headers or {})
    return ingress.extract_identity(ingress.normalize(raw))


# ── identity ─────────────────────────────────────────────────────────
def test_the_session_header_keys_the_conversation():
    identity = identify(headers={"X-Claude-Code-Session-Id": SESSION})
    assert identity.confidence is IdentityConfidence.EXPLICIT
    assert identity.session_id == SESSION


def test_the_session_is_also_read_from_the_metadata_blob():
    """A client may send only the body field; the id is the same either way."""
    identity = identify(body={"metadata": {"user_id": USER_ID_BLOB}})
    assert identity.confidence is IdentityConfidence.EXPLICIT
    assert identity.session_id == SESSION


def test_the_same_conversation_keeps_one_route_key():
    """Turn two must resume turn one's agent session, not start a new one."""
    first = identify(headers={"X-Claude-Code-Session-Id": SESSION})
    second = identify(headers={"X-Claude-Code-Session-Id": SESSION})
    assert first.route_key == second.route_key


def test_different_conversations_do_not_share_a_route_key():
    other = identify(headers={"X-Claude-Code-Session-Id": "11111111-2222-3333-4444-555555555555"})
    assert other.route_key != identify(headers={"X-Claude-Code-Session-Id": SESSION}).route_key


def test_a_client_identifying_nothing_is_ephemeral():
    """Sharing one key across unrelated turns would resume a stranger's session."""
    identity = identify(body={"messages": []})
    assert identity.confidence is IdentityConfidence.EPHEMERAL


def test_a_malformed_metadata_blob_is_not_fatal():
    assert identify(body={"metadata": {"user_id": "{not json"}}).session_id is None


# ── prompt assembly ──────────────────────────────────────────────────
CONVERSATION = {
    "system": [{"type": "text", "text": "You are a helpful assistant."}],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "my project is called Fluxion"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Got it."}]},
        {"role": "user", "content": [{"type": "text", "text": "what is it called?"}]},
    ],
}


def test_a_resumed_turn_sends_only_the_newest_message():
    """The agent already remembers the rest; replaying it would bill it twice."""
    prompt = extract_messages_prompt(CONVERSATION, resuming=True)
    assert "what is it called?" in prompt
    assert "my project is called Fluxion" not in prompt
    assert "You are a helpful assistant." in prompt


def test_a_cold_start_keeps_the_history():
    """The failure this prevents is an agent that forgets mid-conversation.

    The Messages API is stateless and the client resends everything, so on a
    cold start — expired sticky row, first turn, a different workspace — the
    context is right there in the request. Sending only the last message would
    ask "what is it called?" of an agent that never heard the first turn, and
    nothing would report an error.
    """
    prompt = extract_messages_prompt(CONVERSATION, resuming=False)
    assert "my project is called Fluxion" in prompt
    assert "what is it called?" in prompt


def test_the_replayed_history_says_who_spoke():
    """Without labels the transcript is one block and the agent cannot tell
    which half it supposedly said."""
    prompt = extract_messages_prompt(CONVERSATION, resuming=False)
    assert "User: my project is called Fluxion" in prompt
    assert "Assistant: Got it." in prompt


def test_a_plain_string_system_prompt_works():
    prompt = extract_messages_prompt(
        {"system": "be terse", "messages": [{"role": "user", "content": "hi"}]}, resuming=True
    )
    assert prompt.startswith("be terse")
    assert "hi" in prompt


def test_non_text_blocks_are_dropped():
    """Tool results belong to the client's tool loop, which never runs here."""
    prompt = extract_messages_prompt(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "x", "content": "42"},
                        {"type": "text", "text": "and the question?"},
                    ],
                }
            ]
        },
        resuming=True,
    )
    assert prompt == "and the question?"


def test_a_request_without_messages_yields_no_prompt():
    assert extract_messages_prompt({}, resuming=False) == ""


# ── wire shape ───────────────────────────────────────────────────────
def test_events_carry_a_named_event_line():
    """A bare `data:` line is the Responses framing; an Anthropic client ignores
    it and then waits out its read timeout."""
    encoded = encode_messages_sse(message_stop()).decode()
    assert encoded.startswith("event: message_stop\ndata: {")
    assert encoded.endswith("\n\n")


def test_message_start_declares_the_model_and_input_size():
    payload = message_start("msg_1", "haiku", 120)["message"]
    assert payload["role"] == "assistant"
    assert payload["model"] == "haiku"
    assert payload["usage"]["input_tokens"] == 120
    assert payload["stop_reason"] is None


def test_unicode_survives_the_wire():
    """`ensure_ascii` is right for Codex and wrong here — Anthropic clients read
    UTF-8, and escaping would show up as literal backslashes in the answer."""
    encoded = encode_messages_sse({"type": "x", "text": "科技新闻"}).decode()
    assert "科技新闻" in encoded
