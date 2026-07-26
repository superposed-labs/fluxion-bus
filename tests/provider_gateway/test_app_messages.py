"""End-to-end: an Anthropic Messages turn served by a local agent CLI."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fluxion.core.models.result import ExecutionResult
from fluxion.provider_gateway.app import GatewayContext, create_app
from fluxion.provider_gateway.attribution import AttributionStore
from fluxion.provider_gateway.auth import TokenAuthenticator
from fluxion.provider_gateway.capabilities import ModelCapabilities
from fluxion.provider_gateway.messages_stream import FLUXION_RESULT
from fluxion.provider_gateway.routing import PolicySpec, Router
from fluxion.provider_gateway.sticky import StickyStore
from fluxion.provider_gateway.upstream.local_agent import LocalAgentUpstream

TOKEN = "gateway-token"
CANDIDATE = "local_claude:haiku"
SESSION = "9653cd7b-b208-4c59-b96f-e58bf8ed39f1"


class RecordingExecutor:
    def __init__(self, chunks=("looking… ",), summary="the answer"):
        self.tasks = []
        self.chunks = chunks
        self.summary = summary

    def name(self):
        return "claude"

    def supports(self, task):
        return True

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.tasks.append(task)
        for chunk in self.chunks:
            if stream_output:
                stream_output(chunk)
        return ExecutionResult(
            success=True,
            summary=self.summary,
            stdout="",
            stderr="",
            exit_code=0,
            executor_session_id="claude-sess-1",
        )


def build_ctx(tmp_path, executor=None):
    executor = executor or RecordingExecutor()
    caps = ModelCapabilities(frozenset({"streaming", "tool_calling"}), max_context_tokens=200_000)
    return GatewayContext(
        router=Router(
            policies={"p": PolicySpec("p", candidates=(CANDIDATE,))},
            routes={},
            capabilities={CANDIDATE: caps},
            default_policy_id="p",
        ),
        sticky=StickyStore(tmp_path / "sticky.db"),
        authenticator=TokenAuthenticator(TOKEN),
        local_agents={
            "local_claude": LocalAgentUpstream(
                provider_id="local_claude", executor=executor, models={"haiku": caps}
            )
        },
        workspaces={"local_claude": tmp_path},
        attribution=AttributionStore(tmp_path / "attribution.db"),
    )


def post(client, body=None, session=SESSION, stream=True):
    """Send a turn. Streams by default, as Claude Code does on every request."""
    body = body or {"model": "haiku", "messages": [{"role": "user", "content": "hello"}]}
    if stream is not None:
        body = {**body, "stream": stream}
    return client.post(
        "/v1/messages",
        json=body,
        headers={"authorization": f"Bearer {TOKEN}", "X-Claude-Code-Session-Id": session},
    )


def events_of(response):
    """Parse the `event:`/`data:` pairs an Anthropic client would read."""
    parsed = []
    for block in response.text.split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) == 2 and lines[0].startswith("event: "):
            parsed.append((lines[0][7:], json.loads(lines[1][6:])))
    return parsed


def test_a_turn_streams_a_well_formed_message(tmp_path):
    response = post(TestClient(create_app(build_ctx(tmp_path))))

    assert response.status_code == 200
    names = [name for name, _ in events_of(response)]
    assert names[0] == "message_start"
    assert names[-1] == "message_stop"
    assert names.count("content_block_start") == names.count("content_block_stop") == 1
    assert "content_block_delta" in names


def test_the_agents_answer_reaches_the_client(tmp_path):
    events = events_of(post(TestClient(create_app(build_ctx(tmp_path)))))
    text = "".join(
        payload["delta"]["text"] for name, payload in events if name == "content_block_delta"
    )
    assert "the answer" in text


def test_an_answer_that_was_never_streamed_is_still_sent(tmp_path):
    """agy prints its answer in one burst after the stream, so a block closed on
    the streamed chunks alone would hold only the working notes."""
    executor = RecordingExecutor(chunks=("thinking…",), summary="42")
    events = events_of(post(TestClient(create_app(build_ctx(tmp_path, executor)))))
    text = "".join(
        payload["delta"]["text"] for name, payload in events if name == "content_block_delta"
    )
    assert text == "thinking…42"


def test_the_internal_result_event_never_reaches_the_client(tmp_path):
    """It carries the agent session id, which is gateway bookkeeping."""
    body = post(TestClient(create_app(build_ctx(tmp_path)))).text
    assert FLUXION_RESULT not in body
    assert "claude-sess-1" not in body


def test_a_follow_up_turn_resumes_the_agent_session(tmp_path):
    """Without this the conversation silently restarts on every turn."""
    executor = RecordingExecutor()
    client = TestClient(create_app(build_ctx(tmp_path, executor)))

    post(client)
    post(client)

    assert executor.tasks[0].metadata["executor_session_id"] == ""
    assert executor.tasks[1].metadata["executor_session_id"] == "claude-sess-1"


def test_a_resumed_turn_stops_replaying_the_history(tmp_path):
    """Turn one sends the transcript; turn two must not send it again."""
    executor = RecordingExecutor()
    client = TestClient(create_app(build_ctx(tmp_path, executor)))
    body = {
        "model": "haiku",
        "messages": [
            {"role": "user", "content": "my project is called Fluxion"},
            {"role": "assistant", "content": "Got it."},
            {"role": "user", "content": "what is it called?"},
        ],
    }

    post(client, body)
    post(client, body)

    assert "my project is called Fluxion" in executor.tasks[0].text
    assert "my project is called Fluxion" not in executor.tasks[1].text
    assert "what is it called?" in executor.tasks[1].text


def test_a_different_conversation_starts_cold(tmp_path):
    executor = RecordingExecutor()
    client = TestClient(create_app(build_ctx(tmp_path, executor)))

    post(client, session=SESSION)
    post(client, session="11111111-2222-3333-4444-555555555555")

    assert executor.tasks[1].metadata["executor_session_id"] == ""


# ── non-streaming ────────────────────────────────────────────────────
def test_a_request_without_stream_gets_one_object(tmp_path):
    """`stream` defaults to false in the Messages API, and a caller that wants
    one answer — a script, a CI step, another agent's delegate — is exactly who
    this ingress serves. SSE would hand its SDK a body it cannot parse."""
    response = post(TestClient(create_app(build_ctx(tmp_path))), stream=None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["type"] == "message"
    assert payload["role"] == "assistant"
    assert payload["stop_reason"] == "end_turn"
    assert payload["content"] == [{"type": "text", "text": "looking… the answer"}]


def test_stream_false_is_honoured_as_well_as_absent(tmp_path):
    response = post(TestClient(create_app(build_ctx(tmp_path))), stream=False)
    assert response.json()["content"][0]["text"] == "looking… the answer"


def test_the_two_shapes_agree_about_the_same_turn(tmp_path):
    """Token counts and the message id are read back out of the events rather
    than recomputed, so the object cannot disagree with the stream."""
    streamed = events_of(post(TestClient(create_app(build_ctx(tmp_path)))))
    collected = post(TestClient(create_app(build_ctx(tmp_path))), stream=None).json()

    start = next(payload for name, payload in streamed if name == "message_start")
    delta = next(payload for name, payload in streamed if name == "message_delta")
    assert collected["usage"]["input_tokens"] == start["message"]["usage"]["input_tokens"]
    assert collected["usage"]["output_tokens"] == delta["usage"]["output_tokens"]
    assert collected["model"] == start["message"]["model"]


def test_a_non_streaming_failure_becomes_a_status_code(tmp_path):
    """A caller waiting for one object has nowhere to read an error event."""

    class FailingExecutor(RecordingExecutor):
        def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
            self.tasks.append(task)
            return ExecutionResult(
                success=False, summary="the agent gave up", stdout="", stderr="", exit_code=1
            )

    response = post(
        TestClient(create_app(build_ctx(tmp_path, FailingExecutor()))),
        stream=None,
    )

    assert response.status_code == 500
    assert "the agent gave up" in response.json()["error"]["message"]


def test_a_non_streaming_turn_is_still_remembered(tmp_path):
    """Bookkeeping is not a property of the wire shape: without this the next
    turn restarts the agent session."""
    executor = RecordingExecutor()
    client = TestClient(create_app(build_ctx(tmp_path, executor)))

    post(client, stream=None)
    post(client, stream=None)

    assert executor.tasks[1].metadata["executor_session_id"] == "claude-sess-1"


def test_an_unauthenticated_request_is_refused(tmp_path):
    client = TestClient(create_app(build_ctx(tmp_path)))
    assert client.post("/v1/messages", json={"messages": []}).status_code == 401


def test_the_api_key_header_authenticates_too(tmp_path):
    """Anthropic clients send `x-api-key`; Claude Code sends `authorization`."""
    client = TestClient(create_app(build_ctx(tmp_path)))
    response = client.post(
        "/v1/messages",
        json={"model": "haiku", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": TOKEN},
    )
    assert response.status_code == 200
