"""End-to-end: a Codex sub-agent request served by a local agent CLI.

This is the plan-A path — the sub-agent thread is backed by the user's
subscription-based agent rather than a model API.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fluxion.core.models.result import ExecutionResult
from fluxion.provider_gateway.app import GatewayContext, build_context, create_app
from fluxion.provider_gateway.attribution import BILLING_SUBSCRIPTION, AttributionStore
from fluxion.provider_gateway.auth import TokenAuthenticator
from fluxion.provider_gateway.capabilities import ModelCapabilities
from fluxion.provider_gateway.config import GatewaySettings, RoutingConfig
from fluxion.provider_gateway.routing import PolicySpec, Router
from fluxion.provider_gateway.sticky import StickyStore
from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    EV_CREATED,
    EV_OUTPUT_ITEM_ADDED,
    EV_OUTPUT_TEXT_DELTA,
    SSEDecoder,
)
from fluxion.provider_gateway.upstream.local_agent import LocalAgentUpstream

TOKEN = "gateway-token"
CANDIDATE = "local_claude:opus"


class RecordingExecutor:
    def __init__(self):
        self.tasks = []

    def name(self):
        return "claude"

    def supports(self, task):
        return True

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.tasks.append(task)
        if stream_output:
            stream_output("reading files…")
            stream_output(" done")
        return ExecutionResult(
            success=True,
            summary="ok",
            stdout="",
            stderr="",
            exit_code=0,
            changed_files=["src/a.py"],
            executor_session_id="claude-sess-1",
            token_usage={"input_tokens": 90_000, "output_tokens": 4_000},
        )


def build_ctx(tmp_path, executor=None, workspace=None):
    executor = executor or RecordingExecutor()
    caps = ModelCapabilities(
        frozenset({"streaming", "tool_calling", "reasoning"}), max_context_tokens=200_000
    )
    return GatewayContext(
        router=Router(
            policies={"balanced": PolicySpec("balanced", candidates=(CANDIDATE,))},
            routes={"auto": "balanced"},
            capabilities={CANDIDATE: caps},
        ),
        sticky=StickyStore(tmp_path / "sticky.db"),
        authenticator=TokenAuthenticator(TOKEN),
        local_agents={
            "local_claude": LocalAgentUpstream(
                provider_id="local_claude", executor=executor, models={"opus": caps}
            )
        },
        workspaces={"local_claude": workspace or tmp_path},
        attribution=AttributionStore(tmp_path / "attribution.db"),
    )


def post(client, body=None):
    return client.post(
        "/v1/responses",
        json=body or {"model": "opus", "input": [{"role": "user", "content": "review auth.py"}]},
        headers={"authorization": f"Bearer {TOKEN}"},
    )


def events_of(response):
    decoder = SSEDecoder()
    return decoder.feed(response.content) + decoder.flush()


def turn_metadata(**fields):
    return json.dumps({"request_kind": "turn", **fields})


def test_local_agent_serves_the_turn(tmp_path):
    executor = RecordingExecutor()
    response = post(TestClient(create_app(build_ctx(tmp_path, executor))))

    assert response.status_code == 200
    types = [e.type for e in events_of(response)]
    assert types[0] == EV_CREATED
    assert EV_OUTPUT_TEXT_DELTA in types
    assert types[-1] == EV_COMPLETED
    assert executor.tasks[0].text == "review auth.py"


def test_agent_output_streams_into_the_window(tmp_path):
    events = events_of(post(TestClient(create_app(build_ctx(tmp_path)))))
    narration_id = next(e for e in events if e.type == EV_OUTPUT_ITEM_ADDED).data["item"]["id"]
    deltas = [
        e.data["delta"]
        for e in events
        if e.type == EV_OUTPUT_TEXT_DELTA and e.data["item_id"] == narration_id
    ]
    assert deltas == ["reading files…", " done"]


def test_workspace_comes_from_codex_turn_metadata(tmp_path):
    """The sub-agent's work belongs in the repo the parent is operating on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    executor = RecordingExecutor()
    body = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {
            "x-codex-turn-metadata": turn_metadata(
                thread_id="t1", workspaces={str(repo): {"has_changes": True}}
            )
        },
    }
    post(TestClient(create_app(build_ctx(tmp_path, executor))), body)
    assert executor.tasks[0].workspace == repo


def test_configured_default_is_used_when_metadata_has_none(tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    executor = RecordingExecutor()
    post(TestClient(create_app(build_ctx(tmp_path, executor, workspace=fallback))))
    assert executor.tasks[0].workspace == fallback


def test_request_is_refused_when_no_workspace_can_be_determined(tmp_path):
    """Guessing would point an agent at the wrong repository."""
    context = build_ctx(tmp_path)
    context.workspaces = {}
    assert post(TestClient(create_app(context))).status_code == 503


def test_session_is_remembered_and_resumed_on_the_next_turn(tmp_path):
    """A follow-up turn continues the agent session instead of starting cold."""
    executor = RecordingExecutor()
    context = build_ctx(tmp_path, executor)
    client = TestClient(create_app(context))
    body = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {"x-codex-turn-metadata": turn_metadata(thread_id="t1")},
    }

    post(client, body)
    identity = context.ingress.extract_identity(context.ingress.normalize(_raw(body)))
    stored = context.sticky.lookup(identity.route_key)
    assert stored is not None and stored.executor_session_id == "claude-sess-1"

    post(client, body)
    assert executor.tasks[1].metadata["executor_session_id"] == "claude-sess-1"


def test_reported_usage_is_the_subthread_not_the_agent_burn(tmp_path):
    """90k agent tokens must not become the sub-thread's reported context."""
    completed = events_of(post(TestClient(create_app(build_ctx(tmp_path)))))[-1]
    usage = completed.data["response"]["usage"]
    assert usage["input_tokens"] < 1000
    assert completed.data["response"]["fluxion"]["agent_token_usage"] == {
        "input_tokens": 90_000,
        "output_tokens": 4_000,
    }


def test_turn_is_attributed_to_the_agent_session(tmp_path):
    """Cost per sub-agent is a join against the usage layer, not a second ledger."""
    context = build_ctx(tmp_path)
    body = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {
            "x-codex-turn-metadata": turn_metadata(thread_id="t1", parent_thread_id="p1")
        },
    }
    post(TestClient(create_app(context)), body)

    recorded = context.attribution.list_recent()
    assert len(recorded) == 1
    assert recorded[0].executor_session_id == "claude-sess-1"
    assert recorded[0].parent_thread_id == "p1"
    assert recorded[0].billing_source == BILLING_SUBSCRIPTION
    assert context.attribution.sessions_for_parent("p1") == ["claude-sess-1"]


def test_readyz_counts_local_agent_providers(tmp_path):
    body = TestClient(create_app(build_ctx(tmp_path))).get("/readyz").json()
    assert body["providers"] == ["local_claude"]


def test_models_endpoint_lists_local_agent_models(tmp_path):
    data = TestClient(create_app(build_ctx(tmp_path))).get("/v1/models").json()
    assert {entry["id"] for entry in data["data"]} == {"opus"}


# ── configuration ────────────────────────────────────────────────────
def _routing(**overrides):
    base = {
        "version": 1,
        "providers": [
            {
                "id": "local_claude",
                "protocol": "local_agent",
                "executor": "claude",
                "models": [{"id": "opus", "capabilities": {"max_context_tokens": 200000}}],
            }
        ],
        "policies": {"balanced": {"candidates": ["local_claude:opus"]}},
        "routes": {"auto": "balanced"},
    }
    base.update(overrides)
    return base


def test_local_agent_capabilities_are_granted_implicitly():
    """Codex always sends tools; without this every request would be filtered out."""
    caps = RoutingConfig.parse(_routing()).capability_index()["local_claude:opus"]
    assert caps.supports("tool_calling")
    assert caps.supports("reasoning")
    assert caps.max_context_tokens == 200_000


def test_explicit_capability_config_still_wins():
    routing = _routing()
    routing["providers"][0]["models"][0]["capabilities"] = {"image_input": False}
    caps = RoutingConfig.parse(routing).capability_index()["local_claude:opus"]
    assert not caps.supports("image_input")
    assert caps.supports("tool_calling")


def test_local_agent_provider_must_name_an_executor():
    routing = _routing()
    del routing["providers"][0]["executor"]
    with pytest.raises(Exception, match="must\n?\\s*name an 'executor'|name an 'executor'"):
        RoutingConfig.parse(routing)


def test_unknown_protocol_is_refused():
    routing = _routing()
    routing["providers"][0]["protocol"] = "telepathy"
    with pytest.raises(Exception, match="unknown protocol"):
        RoutingConfig.parse(routing)


def test_build_context_wires_a_registered_executor(tmp_path):
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    context = build_context(
        settings, RoutingConfig.parse(_routing()), executors={"claude": RecordingExecutor()}
    )
    assert set(context.local_agents) == {"local_claude"}
    context.sticky.close()


def test_unregistered_executor_is_skipped_loudly(tmp_path):
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    with pytest.raises(Exception, match="would serve nothing"):
        build_context(settings, RoutingConfig.parse(_routing()), executors={})


def _raw(body):
    from fluxion.provider_gateway.request import RawRequest

    return RawRequest.create(body)
