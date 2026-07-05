from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fluxion.channels.control_formatter import render_control_response
from fluxion.core.engine import GatewayCore
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage
from fluxion.mcp_server import model_catalog


class _Executor:
    def supports(self, task):
        return True


class _ChannelAdapter:
    def send_status(self, *args, **kwargs):
        pass


def _gateway(*, settings=None) -> GatewayCore:
    tmp_dir = tempfile.TemporaryDirectory()
    storage = JsonlStorage(Path(tmp_dir.name))
    gateway = GatewayCore(
        router=TaskRouter(
            executors={"codex": _Executor(), "claude": _Executor()},
            default_executor="codex",
        ),
        storage=storage,
        sessions=SessionManager(storage=storage),
        artifact_max_files=10,
        worker_count=1,
        max_pending_per_user=5,
        max_retries=0,
        retry_backoff_sec=1,
        settings=settings,
    )
    gateway._test_tmp_dir = tmp_dir  # keep tmpdir alive for the test lifetime
    return gateway


def _cli(response):
    assert response is not None
    return render_control_response(response, channel="cli")


def test_usage_command_formats_weekly_reset_as_days():
    gateway = _gateway(settings=SimpleNamespace())
    resets_at = (datetime.now(UTC) + timedelta(days=5, hours=12, minutes=30)).isoformat()
    payload = {
        "enabled": True,
        "locale": "en",
        "generated_at": datetime.now(UTC).isoformat(),
        "providers": [
            {
                "provider": "codex",
                "status": "ok",
                "account_label": "plus",
                "windows": [
                    {
                        "key": "7d",
                        "label": "Weekly",
                        "used_percent": 5.0,
                        "resets_at": resets_at,
                    }
                ],
                "fetched_at": datetime.now(UTC).isoformat(),
                "detail": "",
            }
        ],
    }
    (gateway._storage.data_dir / "usage_cache.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    response = gateway.handle_control_command(
        text="usage",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert response is not None
    assert response.kind == "usage"
    assert response.data == payload
    text = _cli(response)
    assert "[Fluxion] Current Subscription Usage / Quota" in text
    assert "Codex · plus · OK" in text
    assert "• Weekly: 5.0% · resets in 5d 12h" in text
    assert "132h" not in text


def test_executors_command_lists_supported_executors():
    gateway = _gateway(settings=SimpleNamespace())

    response = gateway.handle_control_command(
        text="executors",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    text = _cli(response)
    assert "[Fluxion] Supported Executors:" in text
    assert "- codex" in text
    assert "Selectable models" not in text


def test_models_bare_command_lists_default_executor_models(monkeypatch):
    calls = []

    def fake_view(*, agent, project, settings):
        calls.append((agent, project, settings))
        return {
            "found": True,
            "agent": agent,
            "source": "live_catalog+local_prices",
            "default_model": "codex-cli-default",
            "models": [{"id": "gpt-5.4-mini"}],
            "warnings": [],
        }

    monkeypatch.setattr(model_catalog, "list_agent_models_view", fake_view)
    settings = SimpleNamespace()
    gateway = _gateway(settings=settings)

    response = gateway.handle_control_command(
        text="models",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert calls == [("codex", "", settings)]
    text = _cli(response)
    assert "[Fluxion] Models for codex:" in text
    assert "- active: executor default (codex-cli-default)" in text
    assert "- gpt-5.4-mini" in text


def test_models_bare_command_uses_active_executor_override(monkeypatch):
    calls = []

    def fake_view(*, agent, project, settings):
        calls.append((agent, project, settings))
        return {
            "found": True,
            "agent": agent,
            "source": "executor_aliases+local_prices",
            "default_model": "claude-cli-default",
            "models": [{"id": "haiku"}],
            "warnings": [],
        }

    monkeypatch.setattr(model_catalog, "list_agent_models_view", fake_view)
    settings = SimpleNamespace()
    gateway = _gateway(settings=settings)
    gateway._sessions.set_executor_override(
        conversation_key="slack:C123:C123",
        channel="slack",
        user_id="U123",
        executor_name="claude",
    )

    response = gateway.handle_control_command(
        text="models",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert calls == [("claude", "", settings)]
    text = _cli(response)
    assert "[Fluxion] Models for claude:" in text
    assert "- haiku" in text
    assert "Claude models are CLI aliases" in text


def test_models_bare_command_marks_active_model_override(monkeypatch):
    def fake_view(*, agent, project, settings):
        return {
            "found": True,
            "agent": agent,
            "source": "live_catalog",
            "default_model": "codex-cli-default",
            "models": [{"id": "gpt-5.4-mini"}],
            "warnings": [],
        }

    monkeypatch.setattr(model_catalog, "list_agent_models_view", fake_view)
    gateway = _gateway(settings=SimpleNamespace())
    gateway._sessions.set_model_override(
        conversation_key="slack:C123:C123",
        channel="slack",
        user_id="U123",
        executor_name="codex",
        model="gpt-5.4-mini",
    )

    response = gateway.handle_control_command(
        text="models",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    text = _cli(response)
    assert "- active: gpt-5.4-mini" in text
    assert "- gpt-5.4-mini ← active" in text


def test_models_agent_command_lists_selectable_models(monkeypatch):
    calls = []

    def fake_view(*, agent, project, settings):
        calls.append((agent, project, settings))
        return {
            "found": True,
            "agent": "codex",
            "source": "live_catalog+local_prices",
            "default_model": "codex-cli-default",
            "models": [
                {
                    "id": "gpt-5.4-mini",
                    "input_per_1m": 0.75,
                    "output_per_1m": 4.5,
                    "supported_reasoning_efforts": ["low", "medium"],
                }
            ],
            "price_references": [{"id": "gpt-5.4"}],
            "warnings": [],
        }

    monkeypatch.setattr(model_catalog, "list_agent_models_view", fake_view)
    settings = SimpleNamespace()
    gateway = _gateway(settings=settings)

    response = gateway.handle_control_command(
        text="models codex",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert calls == [("codex", "", settings)]
    text = _cli(response)
    assert "[Fluxion] Models for codex:" in text
    assert "- active: executor default (codex-cli-default)" in text
    assert "- gpt-5.4-mini (in=$0.75/1M, out=$4.5/1M); efforts=low,medium" in text
    assert "price_references[]" not in text
    assert "pricing references are omitted" in text


def test_models_agent_command_reports_missing_settings():
    gateway = _gateway(settings=None)

    response = gateway.handle_control_command(
        text="models codex",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert (
        _cli(response)
        == "[Fluxion] Model listing is unavailable: gateway settings are not attached."
    )


def test_legacy_model_list_alias_is_not_handled():
    gateway = _gateway(settings=SimpleNamespace())

    response = gateway.handle_control_command(
        text="model-list codex",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert response is None


def test_use_executor_requires_explicit_scope():
    gateway = _gateway(settings=SimpleNamespace())

    response = gateway.handle_control_command(
        text="use claude",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )

    assert _cli(response) == "[Fluxion] Usage: use executor <executor> | use model <model-id>"
    assert (
        gateway._sessions.get_executor_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
        )
        is None
    )


def test_use_executor_switches_and_clears_override():
    gateway = _gateway(settings=SimpleNamespace())

    switched = gateway.handle_control_command(
        text="use executor claude",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )
    assert _cli(switched) == "[Fluxion] Executor switched to claude."
    assert (
        gateway._sessions.get_executor_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
        )
        == "claude"
    )

    cleared = gateway.handle_control_command(
        text="use executor default",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )
    assert _cli(cleared) == "[Fluxion] Cleared executor override. Reverted to default (codex)."
    assert (
        gateway._sessions.get_executor_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
        )
        is None
    )


def test_use_model_validates_active_executor_models(monkeypatch):
    calls = []

    def fake_view(*, agent, project, settings):
        calls.append(agent)
        return {
            "found": True,
            "agent": agent,
            "source": "executor_aliases",
            "default_model": "",
            "models": [{"id": "haiku"}],
            "price_references": [{"id": "claude-sonnet-4.6"}],
            "warnings": [],
        }

    monkeypatch.setattr(model_catalog, "list_agent_models_view", fake_view)
    gateway = _gateway(settings=SimpleNamespace())
    gateway._sessions.set_executor_override(
        conversation_key="slack:C123:C123",
        channel="slack",
        user_id="U123",
        executor_name="claude",
    )

    rejected = gateway.handle_control_command(
        text="use model claude-sonnet-4.6",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )
    assert _cli(rejected) == (
        "[Fluxion] Unknown model for claude: claude-sonnet-4.6. "
        "Use `models` to list selectable model IDs."
    )
    assert (
        gateway._sessions.get_model_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
        )
        is None
    )

    switched = gateway.handle_control_command(
        text="use model haiku",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )
    assert _cli(switched) == "[Fluxion] Model for claude switched to haiku."
    assert calls == ["claude", "claude"]
    assert (
        gateway._sessions.get_model_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
        )
        == "haiku"
    )

    cleared = gateway.handle_control_command(
        text="use model default",
        user_id="U123",
        convo_key="slack:C123:C123",
        channel="slack",
    )
    assert _cli(cleared) == "[Fluxion] Cleared model override for claude."
    assert (
        gateway._sessions.get_model_override(
            conversation_key="slack:C123:C123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
        )
        is None
    )


def test_submit_task_applies_active_model_override():
    gateway = _gateway(settings=SimpleNamespace())
    gateway._sessions.set_model_override(
        conversation_key="slack:C123:C123",
        channel="slack",
        user_id="U123",
        executor_name="codex",
        model="gpt-5.4-mini",
    )
    task = Task.create(
        channel="slack",
        user_id="U123",
        text="hello",
        workspace=Path("/tmp"),
        metadata={"conversation_key": "slack:C123:C123"},
    )

    ok, info = gateway.submit_task(
        task=task,
        channel_adapter=_ChannelAdapter(),
        channel_context={},
    )

    assert ok
    assert info == task.id
    assert task.metadata["executor"] == "codex"
    assert task.metadata["model"] == "gpt-5.4-mini"
