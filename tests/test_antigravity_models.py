from __future__ import annotations

import subprocess

import pytest

from fluxion.executors.antigravity import models as antigravity_models
from fluxion.executors.antigravity.models import select_antigravity_ping_model_from_names


@pytest.fixture(autouse=True)
def clear_antigravity_model_cache():
    antigravity_models._MODEL_CATALOG_CACHE.clear()
    yield
    antigravity_models._MODEL_CATALOG_CACHE.clear()


def test_select_antigravity_ping_model_prefers_low_flash_for_gemini_pool():
    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:gemini",
        model_names=[
            "Gemini 3.1 Pro (Low)",
            "Gemini 3.5 Flash (High)",
            "Gemini 3.5 Flash (Low)",
            "GPT-OSS 120B (Medium)",
        ],
    )

    assert model == "Gemini 3.5 Flash (Low)"


def test_select_antigravity_ping_model_accepts_slugs_and_prefers_cheaper_version():
    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:gemini",
        model_names=[
            "gemini-3.6-flash-high",
            "gemini-3.6-flash-low",
            "gemini-3.5-flash-low",
            "gemini-3.1-pro-low",
            "gpt-oss-120b-medium",
        ],
    )

    assert model == "gemini-3.6-flash-low"


def test_select_antigravity_ping_model_uses_price_before_version(monkeypatch):
    rates = {
        "gemini-3.5-flash": {"in": 0.5, "out": 3.0, "cw": 0.5, "cr": 0.05},
        "gemini-3.6-flash": {"in": 1.5, "out": 7.5, "cw": 1.5, "cr": 0.15},
    }
    monkeypatch.setattr(
        antigravity_models.pricing,
        "current_rates_for",
        lambda provider, model: rates[
            antigravity_models.identify_model(provider, model).billing_id
        ],
    )

    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:gemini",
        model_names=[
            "gemini-3.5-flash-low",
            "gemini-3.6-flash-low",
        ],
    )

    assert model == "gemini-3.5-flash-low"


def test_select_antigravity_ping_model_prefers_low_effort_when_prices_match(monkeypatch):
    monkeypatch.setattr(
        antigravity_models.pricing,
        "current_rates_for",
        lambda provider, model: {"in": 1.0, "out": 2.0, "cw": 1.0, "cr": 0.1},
    )

    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:gemini",
        model_names=[
            "gemini-3.6-flash-high",
            "gemini-3.6-flash-low",
            "gemini-3.6-flash-medium",
        ],
    )

    assert model == "gemini-3.6-flash-low"


def test_select_antigravity_ping_model_prefers_gpt_oss_for_external_pool():
    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:external",
        model_names=[
            "Gemini 3.5 Flash (Low)",
            "Claude Sonnet 4.6 (Thinking)",
            "Claude Opus 4.6 (Thinking)",
            "GPT-OSS 120B (Medium)",
        ],
    )

    assert model == "GPT-OSS 120B (Medium)"


def test_select_antigravity_ping_model_returns_none_without_matching_pool():
    model = select_antigravity_ping_model_from_names(
        pool_key="antigravity:external",
        model_names=["Gemini 3.5 Flash (Low)"],
    )

    assert model is None


def test_load_antigravity_model_catalog_caches_success(monkeypatch):
    now = {"value": 100.0}
    calls = []
    monkeypatch.setattr(antigravity_models.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        antigravity_models,
        "resolve_antigravity_command",
        lambda command="": "/bin/agy",
    )

    def fake_fetch(command):
        calls.append(command)
        return ["Gemini 3.5 Flash (Low)"], ""

    monkeypatch.setattr(antigravity_models, "_fetch_antigravity_model_catalog", fake_fetch)

    first, first_error = antigravity_models.load_antigravity_model_catalog()
    second, second_error = antigravity_models.load_antigravity_model_catalog()
    now["value"] += antigravity_models._AGY_MODEL_CATALOG_TTL_SEC + 1
    third, third_error = antigravity_models.load_antigravity_model_catalog()

    assert first == ["Gemini 3.5 Flash (Low)"]
    assert second == ["Gemini 3.5 Flash (Low)"]
    assert third == ["Gemini 3.5 Flash (Low)"]
    assert first_error == second_error == third_error == ""
    assert calls == ["/bin/agy", "/bin/agy"]


def test_load_antigravity_model_catalog_caches_failure_briefly(monkeypatch):
    now = {"value": 100.0}
    calls = []
    monkeypatch.setattr(antigravity_models.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        antigravity_models,
        "resolve_antigravity_command",
        lambda command="": "/bin/agy",
    )

    def fake_fetch(command):
        calls.append(command)
        return [], f"failure {len(calls)}"

    monkeypatch.setattr(antigravity_models, "_fetch_antigravity_model_catalog", fake_fetch)

    first, first_error = antigravity_models.load_antigravity_model_catalog()
    second, second_error = antigravity_models.load_antigravity_model_catalog()
    now["value"] += antigravity_models._AGY_MODEL_CATALOG_FAILURE_TTL_SEC + 1
    third, third_error = antigravity_models.load_antigravity_model_catalog()

    assert first == second == third == []
    assert first_error == "failure 1"
    assert second_error == "failure 1"
    assert third_error == "failure 2"
    assert calls == ["/bin/agy", "/bin/agy"]


def test_fetch_antigravity_model_catalog_extracts_ids_from_tab_separated_labels(
    monkeypatch,
):
    stdout = "\n".join(
        [
            "gemini-3.6-flash-high\tGemini 3.6 Flash (High)",
            "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)",
        ]
    )
    monkeypatch.setattr(
        antigravity_models.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=stdout,
            stderr="",
        ),
    )

    names, error = antigravity_models._fetch_antigravity_model_catalog("/bin/agy")

    assert names == ["gemini-3.6-flash-high", "gemini-3.6-flash-low"]
    assert error == ""


def test_fetch_antigravity_model_catalog_preserves_legacy_single_column_names(
    monkeypatch,
):
    monkeypatch.setattr(
        antigravity_models.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="Gemini 3.5 Flash (Low)\n",
            stderr="",
        ),
    )

    names, error = antigravity_models._fetch_antigravity_model_catalog("/bin/agy")

    assert names == ["Gemini 3.5 Flash (Low)"]
    assert error == ""
