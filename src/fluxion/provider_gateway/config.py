"""Configuration loading and validation.

Split in two on purpose. Service settings (host, port, limits) follow the repo's
existing `.env` convention so they behave like every other Fluxion knob. Provider,
model, and policy definitions live in a JSON file instead: they are deeply
nested, and squeezing that structure into environment variables produces
configuration nobody can read or diff.

Everything is validated at load time. A routing config that is wrong should stop
startup with a precise message, not surface as a mysterious 503 on the first
sub-agent someone spawns.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fluxion.provider_gateway.capabilities import ModelCapabilities
from fluxion.provider_gateway.routing import DIMENSIONS, PolicySpec, split_candidate
from fluxion.provider_gateway.sticky import DEFAULT_TTL_SECONDS

SUPPORTED_CONFIG_VERSION = 1

# Protocols a provider entry may declare.
# One value today. Kept as an explicit field rather than dropped so a config
# still says what kind of upstream it declares, and so adding another kind
# later is an additive change to configs already in the wild.
PROTOCOL_LOCAL_AGENT = "local_agent"
SUPPORTED_PROTOCOLS = frozenset({PROTOCOL_LOCAL_AGENT})

# Capabilities a local agent backend absorbs rather than emits.
#
# The flags mean something different on this side of the boundary. An API-backed
# model "supports tool_calling" by emitting `function_call` events for Codex to
# execute; a local agent CLI never emits one — it runs its own tools internally.
# What it *can* do is serve a request that carries tool definitions, which is
# what the filter is actually asking. Granting these automatically keeps users
# from having to encode that subtlety in every config file, and stops the filter
# from rejecting every Codex request (which always carries tools).
#
# This set must cover every capability `derive_requirements` can ask for, and
# `test_local_agent_absorbs_every_derivable_requirement` enforces that. A gap
# here does not degrade gracefully: the router finds no eligible candidate and
# the turn dies with a 503 before any agent runs.
_LOCAL_AGENT_IMPLIED_CAPABILITIES = {
    "streaming": True,
    "tool_calling": True,
    "parallel_tool_calling": True,
    "reasoning": True,
    # Codex asks for a reasoning summary on ordinary turns. We never emit
    # reasoning events, so its reasoning pane stays empty either way — that is
    # already the accepted shape of this mode (see the doc, section 1.1).
    # Refusing the request instead would trade an empty pane for no answer.
    "reasoning_summary": True,
    "image_input": True,
    # A compaction turn is "summarize this conversation", delivered as a normal
    # streaming Responses request since remote_compaction_v2 became the default.
    # A local agent answers it the same way it answers anything else, and text
    # is exactly what Codex reads back.
    "compact": True,
}

# Provider ids Codex reserves for its own built-ins. Reusing one would shadow
# the built-in provider rather than adding ours.
RESERVED_PROVIDER_IDS = frozenset({"openai", "ollama", "lmstudio"})


class ConfigError(ValueError):
    """Configuration is unusable. Raised at load time, never mid-request."""


@dataclass(frozen=True)
class GatewaySettings:
    """Service-level settings, read from the environment."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8787
    token_file: Path = Path("data/provider.token")
    config_file: Path = Path("config/provider_routes.json")
    default_policy: str = "balanced"
    sticky_ttl_seconds: float | None = DEFAULT_TTL_SECONDS
    max_request_bytes: int = 32 * 1024 * 1024
    max_concurrency: int = 12
    # Off by default and deliberately hard to turn on: request bodies contain
    # the user's source code and the model's output.
    log_bodies: bool = False

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> GatewaySettings:
        env = os.environ if env is None else env
        ttl_hours = _int(env.get("FLUXION_PROVIDER_STICKY_TTL_HOURS"), 168)
        return cls(
            enabled=_bool(env.get("FLUXION_PROVIDER_ENABLED"), False),
            host=env.get("FLUXION_PROVIDER_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_port(env.get("FLUXION_PROVIDER_PORT"), 8787),
            token_file=Path(env.get("FLUXION_PROVIDER_TOKEN_FILE", "data/provider.token")),
            config_file=Path(
                env.get("FLUXION_PROVIDER_CONFIG_FILE", "config/provider_routes.json")
            ),
            default_policy=env.get("FLUXION_PROVIDER_DEFAULT_POLICY", "balanced").strip()
            or "balanced",
            # 0 disables expiry entirely, for users who would rather manage
            # routes by hand than have them vanish on a schedule.
            sticky_ttl_seconds=None if ttl_hours <= 0 else ttl_hours * 3600,
            max_request_bytes=_int(env.get("FLUXION_PROVIDER_MAX_REQUEST_BYTES"), 32 * 1024 * 1024),
            max_concurrency=_int(env.get("FLUXION_PROVIDER_MAX_CONCURRENCY"), 12),
            log_bodies=_bool(env.get("FLUXION_PROVIDER_LOG_BODIES"), False),
        )


@dataclass(frozen=True)
class ProviderSpec:
    """One upstream provider as declared in the routing config."""

    provider_id: str
    protocol: str
    enabled: bool
    models: Mapping[str, ModelCapabilities]
    # Which Fluxion executor runs the work.
    executor: str = ""
    # Where the agent runs when the request carries no workspace hint. Absent
    # means "refuse the request" — guessing would point an agent at the wrong
    # repository, and it would start editing before anyone noticed.
    default_workspace: str = ""


@dataclass(frozen=True)
class RoutingConfig:
    """Providers, policies, and role-to-policy routes."""

    providers: Mapping[str, ProviderSpec] = field(default_factory=dict)
    policies: Mapping[str, PolicySpec] = field(default_factory=dict)
    routes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> RoutingConfig:
        file_path = Path(path)
        if not file_path.exists():
            raise ConfigError(
                f"routing config not found at {file_path}. "
                "Run `fluxion provider init` to generate a starter file."
            )
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except ValueError as err:
            raise ConfigError(f"{file_path} is not valid JSON: {err}") from err
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{file_path} must contain a JSON object")
        return cls.parse(raw, source=str(file_path))

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, source: str = "<config>") -> RoutingConfig:
        version = raw.get("version", SUPPORTED_CONFIG_VERSION)
        if version != SUPPORTED_CONFIG_VERSION:
            raise ConfigError(
                f"{source}: unsupported config version {version!r} "
                f"(this build understands {SUPPORTED_CONFIG_VERSION})"
            )

        providers = _parse_providers(raw.get("providers"), source)
        policies = _parse_policies(raw.get("policies"), source)
        routes = _parse_routes(raw.get("routes"), policies, source)
        _validate_candidates(policies, providers, source)
        return cls(providers=providers, policies=policies, routes=routes)

    def enabled_providers(self) -> dict[str, ProviderSpec]:
        return {pid: spec for pid, spec in self.providers.items() if spec.enabled}

    def capability_index(self) -> dict[str, ModelCapabilities]:
        """Flatten to the `provider:model` keys the router scores against."""
        return {
            f"{provider_id}:{model}": capabilities
            for provider_id, spec in self.providers.items()
            if spec.enabled
            for model, capabilities in spec.models.items()
        }


def _parse_providers(raw: Any, source: str) -> dict[str, ProviderSpec]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'providers' must be a list")
    providers: dict[str, ProviderSpec] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{source}: providers[{index}] must be an object")
        provider_id = str(entry.get("id", "")).strip()
        if not provider_id:
            raise ConfigError(f"{source}: providers[{index}] is missing 'id'")
        if provider_id in RESERVED_PROVIDER_IDS:
            raise ConfigError(
                f"{source}: provider id {provider_id!r} is reserved by Codex; "
                "pick another name such as 'fluxion_openai'"
            )
        if provider_id in providers:
            raise ConfigError(f"{source}: duplicate provider id {provider_id!r}")
        protocol = str(entry.get("protocol", PROTOCOL_LOCAL_AGENT)).strip() or PROTOCOL_LOCAL_AGENT
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ConfigError(
                f"{source}: provider {provider_id!r} declares unknown protocol {protocol!r}; "
                f"supported: {sorted(SUPPORTED_PROTOCOLS)}"
            )
        executor = str(entry.get("executor", "")).strip()
        if not executor:
            raise ConfigError(
                f"{source}: provider {provider_id!r} must name an 'executor' "
                "(claude, codex, antigravity, ...)"
            )
        providers[provider_id] = ProviderSpec(
            provider_id=provider_id,
            protocol=protocol,
            enabled=bool(entry.get("enabled", True)),
            models=_parse_models(entry.get("models"), provider_id, source),
            executor=executor,
            default_workspace=str(entry.get("default_workspace", "")).strip(),
        )
    if not providers:
        raise ConfigError(f"{source}: at least one provider must be configured")
    return providers


def _parse_models(raw: Any, provider_id: str, source: str) -> dict[str, ModelCapabilities]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{source}: provider {provider_id!r} must declare at least one model")
    models: dict[str, ModelCapabilities] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{source}: {provider_id}.models[{index}] must be an object")
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            raise ConfigError(f"{source}: {provider_id}.models[{index}] is missing 'id'")
        if model_id in models:
            raise ConfigError(f"{source}: duplicate model {model_id!r} in provider {provider_id!r}")
        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, Mapping):
            capabilities = {}
        # Explicit config still wins, so a user can switch one of these off.
        capabilities = {**_LOCAL_AGENT_IMPLIED_CAPABILITIES, **capabilities}
        models[model_id] = ModelCapabilities.from_config(capabilities)
    return models


def _parse_policies(raw: Any, source: str) -> dict[str, PolicySpec]:
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigError(f"{source}: 'policies' must be a non-empty object")
    policies: dict[str, PolicySpec] = {}
    for policy_id, entry in raw.items():
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{source}: policy {policy_id!r} must be an object")
        candidates = _string_list(entry.get("candidates"), f"{source}: {policy_id}.candidates")
        if not candidates:
            raise ConfigError(f"{source}: policy {policy_id!r} needs at least one candidate")
        weights = entry.get("weights", {})
        if not isinstance(weights, Mapping):
            raise ConfigError(f"{source}: policy {policy_id!r} weights must be an object")
        unknown = set(weights) - set(DIMENSIONS)
        if unknown:
            raise ConfigError(
                f"{source}: policy {policy_id!r} has unknown weights {sorted(unknown)}; "
                f"valid dimensions are {list(DIMENSIONS)}"
            )
        policies[str(policy_id)] = PolicySpec(
            policy_id=str(policy_id),
            candidates=tuple(candidates),
            fallback=tuple(_string_list(entry.get("fallback"), f"{source}: {policy_id}.fallback")),
            weights={name: float(value) for name, value in weights.items()},
        )
    return policies


def _parse_routes(raw: Any, policies: Mapping[str, PolicySpec], source: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{source}: 'routes' must be an object")
    routes: dict[str, str] = {}
    for role, policy_id in raw.items():
        policy_name = str(policy_id)
        if policy_name not in policies:
            raise ConfigError(
                f"{source}: route {role!r} points at undefined policy {policy_name!r}"
            )
        routes[str(role)] = policy_name
    return routes


def _validate_candidates(
    policies: Mapping[str, PolicySpec],
    providers: Mapping[str, ProviderSpec],
    source: str,
) -> None:
    """Every candidate must name a real provider and model.

    Checked here rather than at request time so a typo in a candidate id is
    caught by `fluxion provider doctor`, not by a sub-agent that mysteriously
    fails to route.
    """
    for policy in policies.values():
        for candidate in policy.ordered_candidates():
            try:
                provider_id, model = split_candidate(candidate)
            except ValueError as err:
                raise ConfigError(f"{source}: policy {policy.policy_id!r}: {err}") from err
            spec = providers.get(provider_id)
            if spec is None:
                raise ConfigError(
                    f"{source}: policy {policy.policy_id!r} references unknown provider "
                    f"{provider_id!r} in candidate {candidate!r}"
                )
            if model not in spec.models:
                raise ConfigError(
                    f"{source}: policy {policy.policy_id!r} references model {model!r} "
                    f"which provider {provider_id!r} does not declare"
                )


def _string_list(raw: Any, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError(f"{label} must be a list of strings")
    return [item.strip() for item in raw if item.strip()]


def _bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _port(value: str | None, default: int) -> int:
    port = _int(value, default)
    if not 1 <= port <= 65535:
        raise ConfigError(f"FLUXION_PROVIDER_PORT must be between 1 and 65535, got {port}")
    return port
