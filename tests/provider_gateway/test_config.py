from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluxion.provider_gateway.capabilities import STREAMING, TOOL_CALLING, derive_requirements
from fluxion.provider_gateway.config import (
    ConfigError,
    GatewaySettings,
    RoutingConfig,
)


def routing_dict(**overrides):
    base = {
        "version": 1,
        "providers": [
            {
                "id": "local_primary",
                "protocol": "local_agent",
                "executor": "claude",
                "models": [{"id": "fast", "capabilities": {"max_context_tokens": 400000}}],
            }
        ],
        "policies": {"balanced": {"candidates": ["local_primary:fast"]}},
        "routes": {"auto": "balanced"},
    }
    base.update(overrides)
    return base


def parse(**overrides):
    return RoutingConfig.parse(routing_dict(**overrides))


# ── settings ─────────────────────────────────────────────────────────
def test_settings_have_safe_defaults():
    settings = GatewaySettings.load(env={})
    assert settings.host == "127.0.0.1"
    assert settings.port == 8787
    assert settings.enabled is False
    assert settings.log_bodies is False


def test_settings_read_the_environment():
    settings = GatewaySettings.load(
        env={
            "FLUXION_PROVIDER_ENABLED": "true",
            "FLUXION_PROVIDER_PORT": "9999",
            "FLUXION_PROVIDER_DEFAULT_POLICY": "quality-first",
        }
    )
    assert settings.enabled is True
    assert settings.port == 9999
    assert settings.default_policy == "quality-first"


def test_zero_ttl_disables_expiry():
    """For users who would rather manage routes by hand than have them vanish."""
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_STICKY_TTL_HOURS": "0"})
    assert settings.sticky_ttl_seconds is None


def test_ttl_hours_convert_to_seconds():
    settings = GatewaySettings.load(env={"FLUXION_PROVIDER_STICKY_TTL_HOURS": "2"})
    assert settings.sticky_ttl_seconds == 7200


def test_invalid_port_is_rejected_at_load():
    with pytest.raises(ConfigError, match="between 1 and 65535"):
        GatewaySettings.load(env={"FLUXION_PROVIDER_PORT": "70000"})


def test_blank_host_falls_back_to_loopback():
    assert GatewaySettings.load(env={"FLUXION_PROVIDER_HOST": "  "}).host == "127.0.0.1"


# ── routing config ───────────────────────────────────────────────────
def test_valid_config_parses():
    config = parse()
    assert set(config.providers) == {"local_primary"}
    assert config.capability_index()["local_primary:fast"].supports(TOOL_CALLING)
    assert config.routes == {"auto": "balanced"}


def test_reserved_provider_ids_are_refused():
    providers = routing_dict()["providers"]
    providers[0]["id"] = "openai"
    with pytest.raises(ConfigError, match="reserved by Codex"):
        parse(providers=providers)


def test_unsupported_version_is_refused():
    with pytest.raises(ConfigError, match="unsupported config version"):
        parse(version=99)


def test_candidate_pointing_at_an_unknown_provider_is_caught_at_load():
    """A typo should fail `doctor`, not a sub-agent that mysteriously won't route."""
    with pytest.raises(ConfigError, match="unknown provider"):
        parse(policies={"balanced": {"candidates": ["typo_provider:fast"]}})


def test_candidate_pointing_at_an_undeclared_model_is_caught_at_load():
    with pytest.raises(ConfigError, match="does not declare"):
        parse(policies={"balanced": {"candidates": ["local_primary:nope"]}})


def test_malformed_candidate_is_caught_at_load():
    with pytest.raises(ConfigError, match="provider:model"):
        parse(policies={"balanced": {"candidates": ["no-colon"]}})


def test_route_pointing_at_an_undefined_policy_is_refused():
    with pytest.raises(ConfigError, match="undefined policy"):
        parse(routes={"auto": "nonexistent"})


def test_unknown_weight_dimension_is_refused():
    with pytest.raises(ConfigError, match="unknown weights"):
        parse(
            policies={"balanced": {"candidates": ["local_primary:fast"], "weights": {"vibes": 1.0}}}
        )


def test_fallback_candidates_are_validated_too():
    with pytest.raises(ConfigError, match="unknown provider"):
        parse(
            policies={
                "balanced": {"candidates": ["local_primary:fast"], "fallback": ["ghost:model"]}
            }
        )


def test_duplicate_provider_ids_are_refused():
    providers = routing_dict()["providers"]
    with pytest.raises(ConfigError, match="duplicate provider"):
        parse(providers=providers + providers)


def test_empty_providers_are_refused():
    with pytest.raises(ConfigError, match="at least one provider"):
        parse(providers=[])


def test_disabled_providers_are_excluded_from_the_index():
    providers = routing_dict()["providers"]
    providers[0]["enabled"] = False
    config = RoutingConfig.parse(routing_dict(providers=providers))
    assert config.enabled_providers() == {}
    assert config.capability_index() == {}


def test_capability_index_is_keyed_by_provider_and_model():
    index = parse().capability_index()
    assert set(index) == {"local_primary:fast"}
    assert index["local_primary:fast"].supports(STREAMING)


# ── file loading ─────────────────────────────────────────────────────
def test_missing_file_points_at_the_init_command(tmp_path):
    with pytest.raises(ConfigError, match="fluxion provider init"):
        RoutingConfig.load(tmp_path / "absent.json")


def test_malformed_json_names_the_file(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        RoutingConfig.load(path)


def test_file_round_trips(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(routing_dict()))
    assert set(RoutingConfig.load(path).providers) == {"local_primary"}


def test_shipped_example_config_still_loads():
    """The example is documentation users copy; a stale one teaches a broken shape.

    It is not loaded by any runtime path, so nothing else would catch a schema
    change that invalidates it.
    """
    example = Path(__file__).resolve().parents[2] / "config" / "provider_routes.example.json"
    config = RoutingConfig.load(example)

    # Every route must resolve to a policy whose candidates exist and are
    # enabled — an example that parses but cannot route is still broken.
    available = set(config.capability_index())
    for role, policy_id in config.routes.items():
        candidates = config.policies[policy_id].ordered_candidates()
        assert available.intersection(candidates), f"route {role!r} has no usable candidate"


def test_shipped_example_config_declares_no_inline_secret():
    example = Path(__file__).resolve().parents[2] / "config" / "provider_routes.example.json"
    raw = json.loads(example.read_text(encoding="utf-8"))

    for provider in raw["providers"]:
        assert "api_key" not in provider, f"{provider['id']} embeds a key in a copied-around file"


def test_local_agent_absorbs_every_derivable_requirement():
    """A capability the filter can require but a local agent lacks is fatal.

    There is no graceful degradation: the router simply finds no eligible
    candidate and the turn dies with a 503 before any agent starts. This is a
    maximal Codex-shaped request — streaming, tools, parallel tools, reasoning
    with a summary, an image, and flagged as compaction — so a newly derivable
    capability fails here rather than in front of a user.
    """
    config = RoutingConfig.parse(
        {
            "version": 1,
            "providers": [
                {
                    "id": "local",
                    "protocol": "local_agent",
                    "executor": "claude",
                    "models": [{"id": "opus"}],
                }
            ],
            "policies": {"p": {"candidates": ["local:opus"]}},
        }
    )
    body = {
        "stream": True,
        "tools": [{"type": "function", "name": "shell"}],
        "parallel_tool_calls": True,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:,"}]}],
    }
    requirements = derive_requirements(body, is_compaction=True)

    assert requirements.required, "the sample request must exercise the filter"
    assert requirements.unmet_by(config.providers["local"].models["opus"]) == ()


def test_doctor_flags_a_read_only_role_no_executor_can_serve():
    """Found at config time beats a sub-agent dying mid-task."""
    from fluxion.provider_gateway.cli import _read_only_problems

    class CannotEnforce:
        def name(self):
            return "antigravity"

    config = RoutingConfig.parse(
        {
            "version": 1,
            "providers": [
                {
                    "id": "agy",
                    "protocol": "local_agent",
                    "executor": "antigravity",
                    "models": [{"id": "gemini"}],
                }
            ],
            "policies": {"p": {"candidates": ["agy:gemini"]}},
            "routes": {"explorer": "p", "worker": "p"},
        }
    )
    problems = _read_only_problems(config, {"antigravity": CannotEnforce()}, lambda executor: False)

    assert len(problems) == 1, "only the read-only role should be flagged"
    assert "explorer" in problems[0]
