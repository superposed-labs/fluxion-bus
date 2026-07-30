"""End-to-end: a Codex sub-agent request served by a local agent CLI.

This is the plan-A path — the sub-agent thread is backed by the user's
subscription-based agent rather than a model API.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), color=(255, 128, 0)).save(output, format="PNG")
    return output.getvalue()


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


class FailingExecutor(RecordingExecutor):
    """A turn that never reaches a completed event — an interrupt, or a crash."""

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.tasks.append(task)
        return ExecutionResult(
            success=False, summary="interrupted", stdout="", stderr="", exit_code=1
        )


class NativeImageExecutor(RecordingExecutor):
    def supports_native_images(self):
        return True


def build_ctx(tmp_path, executor=None, workspace=None):
    executor = executor or RecordingExecutor()
    caps = ModelCapabilities(
        frozenset({"streaming", "tool_calling", "reasoning", "image_input"}),
        max_context_tokens=200_000,
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


def test_responses_image_is_materialized_for_the_agent(tmp_path):
    executor = RecordingExecutor()
    png = png_bytes()
    body = {
        "model": "opus",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "review this screenshot"},
                    {
                        "type": "input_image",
                        "image_url": ("data:image/png;base64," + base64.b64encode(png).decode()),
                    },
                ],
            }
        ],
    }

    response = post(TestClient(create_app(build_ctx(tmp_path, executor))), body)

    assert response.status_code == 200
    task = executor.tasks[0]
    assert "review this screenshot" in task.text
    assert len(task.image_attachments) == 1
    assert task.image_attachments[0].path.read_bytes() == png
    assert str(task.image_attachments[0].path.relative_to(tmp_path)) in task.text


def test_native_image_executor_receives_attachment_without_a_path_manifest(tmp_path):
    executor = NativeImageExecutor()
    png = png_bytes()
    body = {
        "model": "opus",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect natively"},
                    {
                        "type": "input_image",
                        "image_url": ("data:image/png;base64," + base64.b64encode(png).decode()),
                    },
                ],
            }
        ],
    }

    response = post(TestClient(create_app(build_ctx(tmp_path, executor))), body)

    assert response.status_code == 200
    task = executor.tasks[0]
    assert task.text == "inspect natively"
    assert len(task.image_attachments) == 1
    assert task.image_attachments[0].path.read_bytes() == png


def test_codex_app_source_path_is_rewritten_before_file_bridge_execution(tmp_path):
    executor = RecordingExecutor()
    png = png_bytes()
    original = "/Users/user/Downloads/ChatGPT Image 2026.png"
    body = {
        "model": "opus",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"请查看图片。图片路径：{original}\n"
                            f'<image name=[Image #1] path="{original}">\n</image>'
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": ("data:image/png;base64," + base64.b64encode(png).decode()),
                    },
                ],
            }
        ],
    }

    response = post(TestClient(create_app(build_ctx(tmp_path, executor))), body)

    assert response.status_code == 200
    task = executor.tasks[0]
    relative = str(task.image_attachments[0].path.relative_to(tmp_path))
    assert original not in task.text
    assert "<image" not in task.text
    assert "图片路径：[Attached image 1]" in task.text
    assert "[Attached image 1]" in task.text
    assert task.text.count(relative) == 1
    assert "Internal attachment file(s)" in task.text


def test_remote_responses_image_url_is_passed_without_gateway_download(tmp_path):
    executor = RecordingExecutor()
    body = {
        "model": "opus",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "review this screenshot"},
                    {
                        "type": "input_image",
                        "image_url": "https://example.com/screenshot.png",
                    },
                ],
            }
        ],
    }

    response = post(TestClient(create_app(build_ctx(tmp_path, executor))), body)

    assert response.status_code == 200
    assert len(executor.tasks) == 1
    assert "https://example.com/screenshot.png" in executor.tasks[0].text
    assert "gateway did not download" in executor.tasks[0].text.lower()
    assert executor.tasks[0].attachments == ()
    assert executor.tasks[0].image_attachments == ()


def test_unknown_image_format_reaches_the_agent_as_a_file_attachment(tmp_path):
    executor = NativeImageExecutor()
    payload = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00example"
    body = {
        "model": "opus",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect this HEIC"},
                    {
                        "type": "input_image",
                        "image_url": (
                            "data:image/heic;base64," + base64.b64encode(payload).decode()
                        ),
                    },
                ],
            }
        ],
    }

    response = post(TestClient(create_app(build_ctx(tmp_path, executor))), body)

    assert response.status_code == 200
    task = executor.tasks[0]
    assert task.image_attachments == ()
    assert len(task.attachments) == 1
    assert task.attachments[0].path.read_bytes() == payload
    assert task.attachments[0].path.suffix == ".heic"
    assert str(task.attachments[0].path.relative_to(tmp_path)) in task.text


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


def test_a_follow_up_turn_reuses_the_spawn_turn_workspace(tmp_path):
    """Codex reports `workspaces` when it spawns a sub-agent and omits it on
    every later message to that sub-agent — measured against codex-cli 0.145.0.
    Without the remembered workspace the second turn has nowhere to run, and the
    whole conversation dies at turn two with a 503.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    executor = RecordingExecutor()
    context = build_ctx(tmp_path, executor)
    context.workspaces = {}
    client = TestClient(create_app(context))
    spawn = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {
            "x-codex-turn-metadata": turn_metadata(
                thread_id="t1", workspaces={str(repo): {"has_changes": False}}
            )
        },
    }
    follow_up = {
        "model": "opus",
        "input": [{"role": "user", "content": "and now this"}],
        "client_metadata": {"x-codex-turn-metadata": turn_metadata(thread_id="t1")},
    }

    assert post(client, spawn).status_code == 200
    assert post(client, follow_up).status_code == 200
    assert executor.tasks[1].workspace == repo


def test_a_spawn_that_fails_still_leaves_the_sub_agent_reachable(tmp_path):
    """The spawn turn is the only one that reports a workspace.

    Codex sends `workspaces` when it spawns a sub-agent and never again. When
    that first turn was interrupted, the workspace used to go unrecorded — every
    later message to that sub-agent 503'd with nowhere to run, so an interrupted
    spawn bricked the sub-thread rather than merely failing it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    executor = FailingExecutor()
    context = build_ctx(tmp_path, executor)
    client = TestClient(create_app(context))
    spawn = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {
            "x-codex-turn-metadata": turn_metadata(
                thread_id="t1", workspaces={str(repo): {"has_changes": False}}
            )
        },
    }

    post(client, spawn)
    follow_up = post(
        client,
        {
            "model": "opus",
            "input": [{"role": "user", "content": "and now this"}],
            "client_metadata": {"x-codex-turn-metadata": turn_metadata(thread_id="t1")},
        },
    )

    assert follow_up.status_code == 200
    # The default workspace configured for this provider is tmp_path, so landing
    # on `repo` can only have come from what the spawn turn reported.
    assert executor.tasks[1].workspace == repo


def test_a_remembered_workspace_that_is_gone_does_not_win(tmp_path):
    """A repo can be moved or deleted between turns; the configured default is
    still a real directory and the remembered path no longer is."""
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    repo = tmp_path / "transient"
    repo.mkdir()
    executor = RecordingExecutor()
    context = build_ctx(tmp_path, executor, workspace=fallback)
    client = TestClient(create_app(context))
    spawn = {
        "model": "opus",
        "input": [{"role": "user", "content": "go"}],
        "client_metadata": {
            "x-codex-turn-metadata": turn_metadata(
                thread_id="t1", workspaces={str(repo): {"has_changes": False}}
            )
        },
    }

    post(client, spawn)
    repo.rmdir()
    post(
        client,
        {**spawn, "client_metadata": {"x-codex-turn-metadata": turn_metadata(thread_id="t1")}},
    )

    assert executor.tasks[1].workspace == fallback


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


def test_build_context_gives_the_router_a_live_health_source(tmp_path):
    """Without this wiring the whole runtime check is dead code: `is_healthy` was a no-op."""
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    context = build_context(
        settings, RoutingConfig.parse(_routing()), executors={"claude": RecordingExecutor()}
    )
    try:
        assert context.model_health is not None
        assert context.router.health_check == context.model_health.health_check
    finally:
        context.sticky.close()


def test_model_health_can_be_switched_off(tmp_path):
    """The escape hatch: route exactly as configured and let a dead id fail at the CLI."""
    settings = GatewaySettings.load(
        env={
            "FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t"),
            "FLUXION_PROVIDER_MODEL_HEALTH_REFRESH_SEC": "0",
        }
    )
    context = build_context(
        settings, RoutingConfig.parse(_routing()), executors={"claude": RecordingExecutor()}
    )
    try:
        assert context.model_health is None
        assert context.router.health_check("local_claude:opus") == ""
    finally:
        context.sticky.close()


def test_ejecting_a_model_drains_its_sticky_routes_from_the_real_store(tmp_path):
    """Rows outliving their model are re-read and re-rejected on every later turn."""
    from fluxion.provider_gateway.app import _build_model_health
    from fluxion.provider_gateway.model_catalog import ExecutorCatalog

    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    store = StickyStore(tmp_path / "sticky.db")
    # Claude has no catalog command of its own, so stub a readable one: what is
    # under test is the drain, not catalog reading.
    health = _build_model_health(
        settings,
        RoutingConfig.parse(_routing()),
        store,
        load=lambda executor: ExecutorCatalog(
            executor=executor, model_ids=frozenset({"some-other-model"})
        ),
    )

    store.remember(_identity(), "local_claude", "opus", "balanced")
    health.refresh()

    assert store.lookup("rk-1") is None
    store.close()


def _identity():
    from fluxion.provider_gateway.identity import IdentityConfidence, RequestIdentity

    return RequestIdentity(
        ingress="codex", route_key="rk-1", confidence=IdentityConfidence.EXPLICIT
    )


def test_unregistered_executor_is_skipped_loudly(tmp_path):
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    with pytest.raises(Exception, match="would serve nothing"):
        build_context(settings, RoutingConfig.parse(_routing()), executors={})


def _raw(body):
    from fluxion.provider_gateway.request import RawRequest

    return RawRequest.create(body)
