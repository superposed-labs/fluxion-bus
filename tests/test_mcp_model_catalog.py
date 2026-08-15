from __future__ import annotations

import json
import types

from fluxion.executors.antigravity import models as antigravity_models
from fluxion.mcp_server import model_catalog


class _Settings:
    default_executor = "codex"
    claude_model = "sonnet"
    claude_provider = "official"
    antigravity_command = ""

    def resolve_project(self, project):
        if project:
            return types.SimpleNamespace(key=project, default_executor="claude")
        return None


def _prices():
    return {
        "updated_at": "2026-06-13",
        "models": {
            "gpt-5.5": {
                "provider": "codex",
                "rates": [{"effective_date": "2026-01-01", "in": 5, "out": 30, "cw": 5, "cr": 1}],
            },
            "gpt-5.4-mini": {
                "provider": "codex",
                "rates": [
                    {"effective_date": "2026-01-01", "in": 0.25, "out": 2, "cw": 1, "cr": 0.1}
                ],
            },
            "claude-opus-4-1": {
                "provider": "claude",
                "rates": [{"effective_date": "2026-01-01", "in": 15, "out": 75, "cw": 20, "cr": 2}],
            },
            "claude-fable-5": {
                "provider": "claude",
                "rates": [{"effective_date": "2026-01-01", "in": 10, "out": 50, "cw": 12, "cr": 1}],
            },
            "gemini-3-flash": {
                "provider": "antigravity",
                "rates": [{"effective_date": "2026-01-01", "in": 2, "out": 12, "cw": 3, "cr": 1}],
            },
        },
        "families": {
            "haiku": [{"effective_date": "2026-01-01", "in": 0.8, "out": 4, "cw": 1, "cr": 0.1}],
            "sonnet": [{"effective_date": "2026-01-01", "in": 3, "out": 15, "cw": 4, "cr": 0.3}],
            "fable": [{"effective_date": "2026-01-01", "in": 10, "out": 50, "cw": 12, "cr": 1}],
        },
        "providers": {
            "codex": [{"effective_date": "2026-01-01", "in": 1, "out": 10, "cw": 1, "cr": 0.1}],
            "claude": [{"effective_date": "2026-01-01", "in": 1, "out": 10, "cw": 1, "cr": 0.1}],
        },
    }


def test_codex_models_use_live_catalog_enriched_with_prices(monkeypatch):
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: _prices())
    monkeypatch.setattr(model_catalog, "resolve_codex_command", lambda: "/bin/codex")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        return types.SimpleNamespace(
            stdout=json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.4-mini",
                            "supported_reasoning_levels": [{"effort": "low"}],
                        },
                        {
                            "slug": "gpt-5.5",
                            "supported_reasoning_levels": [{"effort": "high"}],
                        },
                    ]
                }
            )
        )

    monkeypatch.setattr(model_catalog.subprocess, "run", fake_run)

    view = model_catalog.list_agent_models_view(agent="codex", project="", settings=_Settings())

    assert view["found"] is True
    assert view["source"] == "live_catalog+local_prices"
    assert [item["id"] for item in view["models"]] == ["gpt-5.5", "gpt-5.4-mini"]
    assert view["models"][0]["output_per_1m"] == 30
    assert view["models"][1]["supported_reasoning_efforts"] == ["low"]
    assert "ping_model" not in view


def test_claude_models_are_selectable_aliases_with_price_references(monkeypatch):
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: _prices())

    view = model_catalog.list_agent_models_view(agent="claude", project="", settings=_Settings())

    ids = [item["id"] for item in view["models"]]
    assert ids == ["fable", "sonnet", "opus", "haiku"]
    assert set(ids) == {"fable", "opus", "sonnet", "haiku"}
    assert "claude-fable-5" not in ids
    haiku = next(item for item in view["models"] if item["id"] == "haiku")
    assert haiku["source"] == "executor_alias"
    assert haiku["availability"] == "known_cli_alias"
    assert [item["id"] for item in view["price_references"]] == [
        "claude-opus-4-1",
        "claude-fable-5",
    ]
    assert view["default_model"] == "sonnet"
    assert "ping_model" not in view
    assert view["source"] == "executor_aliases+local_prices"
    assert view["sort"] == "price_high_to_low"
    assert view["supported_reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert all(
        model["supported_reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
        for model in view["models"]
    )
    assert view["reasoning_effort_source"] == "claude_cli_help_global"
    assert view["warnings"]


def test_antigravity_models_use_live_catalog(monkeypatch):
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: _prices())
    monkeypatch.setattr(antigravity_models.shutil, "which", lambda name: "/bin/agy")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        assert kwargs["stdin"] is antigravity_models.subprocess.DEVNULL
        return types.SimpleNamespace(
            stdout="Gemini 3.5 Flash (High)\nGPT-OSS 120B (Medium)\n",
        )

    monkeypatch.setattr(antigravity_models.subprocess, "run", fake_run)

    view = model_catalog.list_agent_models_view(
        agent="antigravity", project="", settings=_Settings()
    )

    assert view["found"] is True
    model_ids = [item["id"] for item in view["models"]]
    assert model_ids == ["GPT-OSS 120B (Medium)", "Gemini 3.5 Flash (High)"]
    assert {item["availability"] for item in view["models"]} == {"live_catalog"}
    assert view["price_references"][0]["id"] == "gemini-3-flash"
    assert view["source"] == "live_catalog+local_prices"
    assert view["warnings"] == []
    assert view["sort"] == "price_high_to_low"


def test_antigravity_models_empty_when_live_catalog_unavailable(monkeypatch):
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: _prices())
    monkeypatch.setattr(
        antigravity_models,
        "load_antigravity_model_catalog",
        lambda command="": ([], "`agy models` timed out after 30s"),
    )

    view = model_catalog.list_agent_models_view(
        agent="antigravity", project="", settings=_Settings()
    )

    assert view["models"] == []
    assert view["price_references"][0]["id"] == "gemini-3-flash"
    assert view["source"] == "live_catalog_unavailable+local_prices"
    assert view["sort"] == "price_high_to_low"
    assert view["warnings"] == ["`agy models` timed out after 30s; models[] is empty."]


def test_antigravity_slug_catalog_uses_exact_version_price(monkeypatch):
    prices = _prices()
    prices["models"]["gemini-3.5-flash"] = {
        "provider": "antigravity",
        "rates": [
            {
                "effective_date": "2026-05-19",
                "in": 1.5,
                "out": 9.0,
                "cw": 1.5,
                "cr": 0.15,
            }
        ],
    }
    prices["families"]["flash"] = [
        {
            "effective_date": "2026-07-21",
            "in": 1.5,
            "out": 7.5,
            "cw": 1.5,
            "cr": 0.15,
        }
    ]
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: prices)
    monkeypatch.setattr(
        antigravity_models,
        "load_antigravity_model_catalog",
        lambda command="": (
            ["gemini-3.6-flash-low", "gemini-3.5-flash-low"],
            "",
        ),
    )

    view = model_catalog.list_agent_models_view(
        agent="antigravity", project="", settings=_Settings()
    )
    by_id = {item["id"]: item for item in view["models"]}

    assert by_id["gemini-3.5-flash-low"]["output_per_1m"] == 9.0
    assert by_id["gemini-3.6-flash-low"]["output_per_1m"] == 7.5


def test_auto_agent_uses_project_default(monkeypatch):
    monkeypatch.setattr(model_catalog.price_data, "load_price_json", lambda name: _prices())

    view = model_catalog.list_agent_models_view(agent="auto", project="p1", settings=_Settings())

    assert view["agent"] == "claude"


def test_unknown_agent_returns_error():
    view = model_catalog.list_agent_models_view(agent="nope", project="", settings=_Settings())

    assert view["found"] is False
    assert view["models"] == []
