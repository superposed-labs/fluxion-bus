"""Model + reasoning-effort resolution, over recorded CLI catalogs.

The catalogs below are the real shape of `agy models` and `codex debug models`
on a machine with both installed, trimmed to the fields resolution reads. They
are recorded rather than invented because the whole point of resolving through a
catalog is that the ids come from the vendor: a hand-written fixture that agreed
with the code but not with the CLI would test nothing.
"""

from __future__ import annotations

import pytest

from fluxion.executors.model_resolution import (
    ModelResolutionError,
    model_catalog_from_view,
    resolve_model_target,
)

# `agy models`, verbatim (id TAB display label).
AGY_MODELS_STDOUT = (
    "gemini-3.7-flash-high\tGemini 3.7 Flash (High)\n"
    "gemini-3.7-flash-medium\tGemini 3.7 Flash (Medium)\n"
    "gemini-3.7-flash-low\tGemini 3.7 Flash (Low)\n"
    "gemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
    "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
    "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
)

# `codex debug models`, the two fields the catalog reads.
CODEX_DEBUG_MODELS = [
    {
        "slug": "gpt-5.6-luna",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low"},
            {"effort": "medium"},
            {"effort": "high"},
            {"effort": "xhigh"},
            {"effort": "max"},
        ],
    },
    {
        "slug": "gpt-5.6-sol",
        "default_reasoning_level": "low",
        "supported_reasoning_levels": [
            {"effort": "low"},
            {"effort": "medium"},
            {"effort": "high"},
            {"effort": "xhigh"},
            {"effort": "max"},
            {"effort": "ultra"},
        ],
    },
]


def _agy_catalog(monkeypatch, stdout: str = AGY_MODELS_STDOUT):
    import types

    from fluxion.executors.antigravity import models as antigravity_models
    from fluxion.mcp_server import model_catalog

    antigravity_models._MODEL_CATALOG_CACHE.clear()
    antigravity_models._MODEL_CATALOG_LAST_GOOD.clear()
    monkeypatch.setattr(antigravity_models.shutil, "which", lambda name: "/bin/agy")
    monkeypatch.setattr(
        antigravity_models.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout=stdout),
    )
    monkeypatch.setattr(
        model_catalog.price_data,
        "load_price_json",
        lambda name: {"models": {}, "families": {}, "providers": {}},
    )
    view = model_catalog.list_agent_models_view(
        agent="antigravity", project="", settings=_Settings()
    )
    return model_catalog_from_view(view)


def _codex_catalog(monkeypatch, live=CODEX_DEBUG_MODELS):
    from fluxion.mcp_server import model_catalog

    monkeypatch.setattr(model_catalog, "_load_codex_debug_models", lambda: live)
    monkeypatch.setattr(
        model_catalog.price_data,
        "load_price_json",
        lambda name: {"models": {}, "families": {}, "providers": {}},
    )
    view = model_catalog.list_agent_models_view(agent="codex", project="", settings=_Settings())
    return model_catalog_from_view(view)


class _Settings:
    default_executor = "antigravity"
    antigravity_command = "agy"
    claude_command = "claude"
    claude_model = ""
    claude_provider = "anthropic"

    def resolve_project(self, name):  # noqa: ARG002
        return None


def test_codex_effort_leaves_as_a_flag_beside_the_model(monkeypatch):
    catalog = _codex_catalog(monkeypatch)

    target = resolve_model_target(catalog=catalog, model="gpt-5.6-luna", reasoning_effort="high")

    assert target.model_id == "gpt-5.6-luna"
    assert target.reasoning_effort == "high"
    assert target.source == "family+effort"


def test_antigravity_effort_leaves_fused_into_the_model_id(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    target = resolve_model_target(
        catalog=catalog, model="gemini-3.7-flash", reasoning_effort="high"
    )

    # The same request as the Codex one above, resolved to the shape agy takes.
    assert target.model_id == "gemini-3.7-flash-high"
    assert target.reasoning_effort == ""
    assert target.source == "family+effort"


def test_exact_variant_id_is_passed_through_untouched(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    target = resolve_model_target(catalog=catalog, model="gemini-3.7-flash-low")

    assert target.model_id == "gemini-3.7-flash-low"
    assert target.source == "requested_override"


def test_bare_product_name_resolves_to_the_models_default_effort(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    target = resolve_model_target(catalog=catalog, model="gemini-3.7-flash")

    # `gemini-3.7-flash` is not a launchable id, so a variant must be chosen.
    assert target.model_id == "gemini-3.7-flash-low"
    assert target.source == "family+default_effort"


def test_explicit_effort_overrides_an_effort_already_in_the_id(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    target = resolve_model_target(
        catalog=catalog, model="gemini-3.7-flash-high", reasoning_effort="low"
    )

    assert target.model_id == "gemini-3.7-flash-low"


def test_unpublished_effort_is_refused_and_names_what_exists(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    with pytest.raises(ModelResolutionError) as excinfo:
        resolve_model_target(catalog=catalog, model="gemini-3.1-pro", reasoning_effort="medium")

    payload = excinfo.value.payload
    # gemini-3.1-pro ships high and low only. Snapping to a neighbour would run
    # at an effort nobody asked for, invisibly.
    assert payload["reason"] == "effort-unavailable"
    assert payload["available_reasoning_efforts"] == ["low", "high"]
    assert payload["next_tools"] == ["list_agent_models"]


def test_unknown_model_is_refused_where_the_id_must_carry_the_effort(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    with pytest.raises(ModelResolutionError) as excinfo:
        resolve_model_target(catalog=catalog, model="gemini-9.9-pro", reasoning_effort="high")

    assert excinfo.value.payload["reason"] == "model-unknown"


def test_unknown_model_still_runs_where_effort_is_a_separate_flag(monkeypatch):
    catalog = _codex_catalog(monkeypatch)

    # A model the local catalog has not heard of yet is the CLI's business, not
    # ours: effort is orthogonal to the id, so nothing has to be constructed.
    target = resolve_model_target(
        catalog=catalog, model="gpt-5.7-unreleased", reasoning_effort="high"
    )

    assert target.model_id == "gpt-5.7-unreleased"
    assert target.reasoning_effort == "high"


def test_no_model_and_no_effort_leaves_the_executor_default_alone(monkeypatch):
    catalog = _codex_catalog(monkeypatch)

    target = resolve_model_target(catalog=catalog)

    assert target == type(target)(model_id="", reasoning_effort="", source="executor_runtime")


def test_effort_without_a_model_is_refused(monkeypatch):
    catalog = _codex_catalog(monkeypatch)

    with pytest.raises(ModelResolutionError) as excinfo:
        resolve_model_target(catalog=catalog, reasoning_effort="high")

    assert excinfo.value.payload["reason"] == "model-required"


def test_agy_model_without_an_effort_axis_resolves_to_its_own_id(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    target = resolve_model_target(catalog=catalog, model="claude-sonnet-4-6")

    assert target.model_id == "claude-sonnet-4-6"


def test_codex_default_effort_is_read_from_the_live_catalog(monkeypatch):
    catalog = _codex_catalog(monkeypatch)

    luna = next(item for item in catalog.options if item.family == "gpt-5.6-luna")
    sol = next(item for item in catalog.options if item.family == "gpt-5.6-sol")

    assert luna.default_effort == "medium"
    assert sol.default_effort == "low"
    assert "ultra" in sol.efforts
    assert "ultra" not in luna.efforts


def _agy_executor(monkeypatch, tmp_path, stdout: str = AGY_MODELS_STDOUT):
    import types

    from fluxion.executors.antigravity import models as antigravity_models
    from fluxion.executors.antigravity.executor import AntiGravityExecutor

    antigravity_models._MODEL_CATALOG_CACHE.clear()
    antigravity_models._MODEL_CATALOG_LAST_GOOD.clear()
    monkeypatch.setattr(antigravity_models.shutil, "which", lambda name: "/bin/agy")
    monkeypatch.setattr(
        antigravity_models.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout=stdout),
    )
    return AntiGravityExecutor(
        timeout_sec=60,
        command="agy",
        sandbox=False,
        dangerously_skip_permissions=False,
        print_timeout_sec=60,
        logs_dir=tmp_path,
    )


def _task(metadata, tmp_path):
    from datetime import UTC, datetime

    from fluxion.core.models.task import Task

    return Task(
        id="t",
        channel="local",
        user_id="u",
        text="hi",
        workspace=tmp_path,
        created_at=datetime.now(UTC),
        metadata=metadata,
    )


def test_executor_resolves_a_product_name_set_straight_onto_the_task(monkeypatch, tmp_path):
    """`use model gemini-3.7-flash` and the provider gateway both set the model
    on the Task without passing through submit-time resolution. The catalog lists
    product names, and a product name is not something `agy --model` accepts."""
    executor = _agy_executor(monkeypatch, tmp_path)

    resolved = executor._model_with_effort(_task({"model": "gemini-3.7-flash"}, tmp_path))

    assert resolved == "gemini-3.7-flash-low"


def test_executor_folds_a_requested_effort_into_the_id(monkeypatch, tmp_path):
    executor = _agy_executor(monkeypatch, tmp_path)

    resolved = executor._model_with_effort(
        _task({"model": "gemini-3.7-flash", "reasoning_effort": "high"}, tmp_path)
    )

    assert resolved == "gemini-3.7-flash-high"


def test_executor_leaves_an_unlisted_model_untouched(monkeypatch, tmp_path):
    # agy is the authority on what it accepts, and the catalog can be stale.
    executor = _agy_executor(monkeypatch, tmp_path)

    resolved = executor._model_with_effort(_task({"model": "gemini-9.9-pro"}, tmp_path))

    assert resolved == "gemini-9.9-pro"


def test_executor_refuses_an_effort_the_model_does_not_publish(monkeypatch, tmp_path):
    executor = _agy_executor(monkeypatch, tmp_path)

    with pytest.raises(ModelResolutionError):
        executor._model_with_effort(
            _task({"model": "gemini-3.1-pro", "reasoning_effort": "medium"}, tmp_path)
        )


def test_asking_for_effort_on_a_model_that_has_none_says_so(monkeypatch):
    catalog = _agy_catalog(monkeypatch)

    with pytest.raises(ModelResolutionError) as excinfo:
        resolve_model_target(catalog=catalog, model="claude-sonnet-4-6", reasoning_effort="high")

    # Not "effort-unavailable with []": the model has no effort axis at all.
    assert excinfo.value.payload["reason"] == "no-effort-axis"


def test_a_model_with_no_effort_axis_is_listed_by_its_launchable_id(monkeypatch):
    """agy publishes Claude Opus only as `-thinking`. The product row has to stay
    launchable, so it keeps the published id rather than a derived family name."""
    catalog = _agy_catalog(
        monkeypatch,
        stdout=AGY_MODELS_STDOUT + "claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n",
    )

    opus = next(item for item in catalog.options if item.label == "Claude Opus 4.6")

    assert opus.family == "claude-opus-4-6-thinking"
    assert opus.variants == {}
    assert resolve_model_target(catalog=catalog, model=opus.family).model_id == opus.family


def test_every_published_id_stays_routable_after_the_catalog_collapse(monkeypatch):
    """Routes store a launchable id, so collapsing the picker's rows must not
    shrink the set the provider gateway can pin a route to."""
    from fluxion.executors.antigravity import models as antigravity_models
    from fluxion.mcp_server import model_catalog
    from fluxion.provider_gateway.setup import _routable_models

    _agy_catalog(monkeypatch)  # installs the recorded `agy models` output
    view = model_catalog.list_agent_models_view(
        agent="antigravity", project="", settings=_Settings()
    )
    published = {
        model_id for model_id, _label in antigravity_models.load_antigravity_model_entries()[0]
    }

    routable = {str(item["id"]) for item in _routable_models(view)}

    assert routable == published


def test_routable_models_pass_effort_choices_through_for_flag_style_agents(monkeypatch):
    from fluxion.mcp_server import model_catalog
    from fluxion.provider_gateway.setup import _routable_models

    monkeypatch.setattr(model_catalog, "_load_codex_debug_models", lambda: CODEX_DEBUG_MODELS)
    monkeypatch.setattr(
        model_catalog.price_data,
        "load_price_json",
        lambda name: {"models": {}, "families": {}, "providers": {}},
    )
    view = model_catalog.list_agent_models_view(agent="codex", project="", settings=_Settings())

    luna = next(item for item in _routable_models(view) if item["id"] == "gpt-5.6-luna")

    # Effort is a runtime flag here, so the route keeps it as a choice.
    assert luna["efforts"] == ["low", "medium", "high", "xhigh", "max"]
