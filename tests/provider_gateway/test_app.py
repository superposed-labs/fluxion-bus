"""Gateway-level behaviour of the ASGI app, independent of what serves a turn.

Auth, body validation, routing by role header, sticky bookkeeping, and the
endpoint surface. `test_app_local_agent.py` covers what a local agent run
produces; this file covers everything wrapped around it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fluxion.core.models.result import ExecutionResult
from fluxion.provider_gateway.app import GatewayContext, create_app
from fluxion.provider_gateway.auth import TokenAuthenticator
from fluxion.provider_gateway.capabilities import (
    COMPACT,
    STREAMING,
    TOOL_CALLING,
    ModelCapabilities,
)
from fluxion.provider_gateway.ingress.responses import CodexResponsesIngress
from fluxion.provider_gateway.routing import PolicySpec, Router
from fluxion.provider_gateway.sticky import StickyStore
from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    EV_CREATED,
    EV_OUTPUT_TEXT_DELTA,
    SSEDecoder,
)
from fluxion.provider_gateway.upstream.local_agent import LocalAgentUpstream

TOKEN = "gateway-token"
FAST = "local_fast:fast"
BACKUP = "local_backup:backup"

CAPABLE = ModelCapabilities(
    frozenset({STREAMING, TOOL_CALLING, COMPACT}), max_context_tokens=400_000
)


class ScriptedExecutor:
    """A local agent whose outcome each test decides."""

    def __init__(self, *, success=True, summary="ok", session_id="sess-1"):
        self.success = success
        self.summary = summary
        self.session_id = session_id
        self.tasks = []

    def name(self):
        return "scripted"

    def supports(self, task):
        return True

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.tasks.append(task)
        if stream_output:
            stream_output("hi")
        return ExecutionResult(
            success=self.success,
            summary=self.summary,
            stdout="",
            stderr="",
            exit_code=0 if self.success else 1,
            executor_session_id=self.session_id,
        )


def turn_metadata(**fields) -> str:
    return json.dumps({"request_kind": "turn", **fields})


def build_context(tmp_path, executor=None, *, stats=None):
    executor = executor or ScriptedExecutor()
    router = Router(
        policies={"balanced": PolicySpec("balanced", candidates=(FAST,), fallback=(BACKUP,))},
        routes={"auto": "balanced", "reviewer": "balanced"},
        capabilities={FAST: CAPABLE, BACKUP: CAPABLE},
        stats=stats or {},
    )
    return GatewayContext(
        router=router,
        sticky=StickyStore(tmp_path / "sticky.db"),
        authenticator=TokenAuthenticator(TOKEN),
        local_agents={
            "local_fast": LocalAgentUpstream(
                provider_id="local_fast", executor=executor, models={"fast": CAPABLE}
            ),
            "local_backup": LocalAgentUpstream(
                provider_id="local_backup", executor=executor, models={"backup": CAPABLE}
            ),
        },
        workspaces={"local_fast": tmp_path, "local_backup": tmp_path},
        ingress=CodexResponsesIngress(),
    )


def client_for(context) -> TestClient:
    return TestClient(create_app(context))


def post(client, body=None, *, token=TOKEN, headers=None):
    all_headers = {"authorization": f"Bearer {token}"} if token else {}
    all_headers.update(headers or {})
    body = body or {"model": "fast", "input": [{"role": "user", "content": "go"}]}
    return client.post("/v1/responses", json=body, headers=all_headers)


def events_of(response) -> list[str]:
    decoder = SSEDecoder()
    events = decoder.feed(response.content) + decoder.flush()
    return [event.type for event in events]


@pytest.fixture
def client(tmp_path):
    return client_for(build_context(tmp_path))


# ── auth ─────────────────────────────────────────────────────────────
def test_request_without_a_token_is_rejected(client):
    assert post(client, token=None).status_code == 401


def test_request_with_a_wrong_token_is_rejected(client):
    assert post(client, token="not-it").status_code == 401


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").status_code == 200


# ── streaming ────────────────────────────────────────────────────────
def test_events_are_streamed_back_to_the_caller(client):
    response = post(client)
    assert response.status_code == 200
    types = events_of(response)
    assert types[0] == EV_CREATED
    assert EV_OUTPUT_TEXT_DELTA in types
    assert types[-1] == EV_COMPLETED


def test_buffering_is_disabled_for_the_stream(client):
    """Without this, a proxy can hold the whole turn back and the window sits empty."""
    response = post(client)
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store"


# ── sticky bookkeeping ───────────────────────────────────────────────
def test_successful_turn_is_remembered(tmp_path):
    context = build_context(tmp_path)
    client = client_for(context)
    post(client, headers={"x-codex-turn-metadata": turn_metadata(thread_id="thread-1")})

    assert context.sticky.list_routes(), "an explicit identity should persist its route"


def test_failed_turn_is_not_remembered(tmp_path):
    """A route that just failed is not evidence it works."""
    context = build_context(tmp_path, ScriptedExecutor(success=False, summary="boom"))
    client = client_for(context)
    post(client, headers={"x-codex-turn-metadata": turn_metadata(thread_id="thread-1")})

    assert context.sticky.list_routes() == []


def test_ephemeral_identity_is_not_remembered(tmp_path):
    """Pinning a guessed identity leaks one conversation's choice into another."""
    context = build_context(tmp_path)
    post(client_for(context))  # no thread metadata at all

    assert context.sticky.list_routes() == []


# ── routing ──────────────────────────────────────────────────────────
def test_route_header_selects_the_policy(tmp_path):
    context = build_context(tmp_path)
    response = post(client_for(context), headers={"x-fluxion-route": "reviewer"})

    assert response.status_code == 200


def test_the_agent_gets_the_task_not_the_transport(tmp_path):
    """Routing headers are Fluxion's own vocabulary and mean nothing to an agent."""
    executor = ScriptedExecutor()
    post(
        client_for(build_context(tmp_path, executor)),
        body={"model": "fast", "input": [{"role": "user", "content": "summarize README"}]},
        headers={"x-fluxion-route": "auto"},
    )

    assert executor.tasks[0].text == "summarize README"


def test_read_only_role_never_starts_an_executor_that_cannot_enforce_it(tmp_path):
    """`reviewer` declares a read-only sandbox; ScriptedExecutor cannot honor it.

    The refusal has to happen here, before the agent runs, or the role file's
    promise is broken silently in the user's real workspace.
    """
    executor = ScriptedExecutor()
    response = post(
        client_for(build_context(tmp_path, executor)), headers={"x-fluxion-route": "reviewer"}
    )

    assert executor.tasks == []
    assert events_of(response) == ["response.failed"]


# ── request validation ───────────────────────────────────────────────
def test_malformed_json_is_a_400(client):
    response = client.post(
        "/v1/responses",
        content=b"{not json",
        headers={"authorization": f"Bearer {TOKEN}", "content-type": "application/json"},
    )
    assert response.status_code == 400


def test_non_object_body_is_a_400(client):
    response = client.post(
        "/v1/responses", json=[1, 2, 3], headers={"authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 400


def test_request_with_no_usable_input_is_reported_not_streamed(client):
    """The failure has to arrive as a status, before headers commit the stream."""
    response = post(client, body={"model": "fast", "input": []})
    assert response.status_code == 200
    assert events_of(response) == ["response.failed"]


# ── endpoint surface ─────────────────────────────────────────────────
def test_readyz_lists_configured_providers(client):
    payload = client.get("/readyz").json()
    assert payload["status"] == "ready"
    assert payload["providers"] == ["local_backup", "local_fast"]


def test_readyz_is_unready_without_providers(tmp_path):
    context = build_context(tmp_path)
    context.local_agents = {}
    assert client_for(context).get("/readyz").status_code == 503


def test_healthz_does_not_depend_on_providers(tmp_path):
    """A wedged agent must not make a supervisor restart a healthy process."""
    context = build_context(tmp_path)
    context.local_agents = {}
    assert client_for(context).get("/healthz").status_code == 200


def test_models_endpoint_lists_logical_models(client):
    ids = {entry["id"] for entry in client.get("/v1/models").json()["data"]}
    assert ids == {"fast", "backup"}


def test_legacy_compact_endpoint_exists(client):
    """Only reached when a user has disabled remote_compaction_v2."""
    response = client.post(
        "/v1/responses/compact",
        json={"model": "fast", "input": [{"role": "user", "content": "compact this"}]},
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
