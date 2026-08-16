"""UI-facing provider routing state and safe route edits.

The desktop app intentionally talks to this module through ``fluxion-provider``
instead of learning the routing JSON schema itself.  That keeps validation,
backup behavior, and model/provider identity in one place.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.availability import PROVIDERS, detect_executor
from fluxion.config.settings import Settings
from fluxion.executors.registry import executor_read_only_support
from fluxion.mcp_server.model_catalog import list_agent_models_view
from fluxion.provider_gateway import codex_config
from fluxion.provider_gateway.config import ConfigError, GatewaySettings, RoutingConfig
from fluxion.provider_gateway.model_catalog import verify_configured_models
from fluxion.provider_gateway.setup import model_lineup, parse_model_family

CORE_ROLES = ("auto", "worker", "explorer", "reviewer")
ROUTE_BACKUP_LIMIT = 10


def preferences_state(
    gateway_settings: GatewaySettings,
    *,
    settings: Settings | None = None,
    include_catalogs: bool = True,
    include_model_health: bool = True,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Return the complete, JSON-safe state consumed by Preferences."""
    config_path = gateway_settings.config_file.expanduser()
    if not config_path.exists():
        return {
            "configured": False,
            "config_file": str(config_path),
            "default_policy": gateway_settings.default_policy,
            "token_available": gateway_settings.token_file.expanduser().exists(),
            "routes": [],
            "providers": [],
            "catalogs": [],
            "executors": _executors_state(None, settings),
            "read_only_roles": sorted(codex_config.read_only_roles()),
            "upgrades": [],
            "model_health": {"missing": [], "unverified": [], "notes": []},
            "codex": _codex_state(codex_home),
        }

    routing = RoutingConfig.load(config_path)
    verification = verify_configured_models(routing) if include_model_health else None
    providers = [
        {
            "id": provider_id,
            "executor": spec.executor,
            "enabled": spec.enabled,
            "models": list(spec.models),
        }
        for provider_id, spec in routing.providers.items()
    ]

    role_order = list(CORE_ROLES)
    role_order.extend(
        role for role in routing.routes if role not in CORE_ROLES and role != "compaction"
    )
    if "compaction" in routing.routes:
        role_order.append("compaction")

    routes: list[dict[str, Any]] = []
    for role in role_order:
        policy_id = routing.routes.get(role, gateway_settings.default_policy)
        policy = routing.policies.get(policy_id)
        if policy is None:
            continue
        routes.append(
            {
                "role": role,
                "policy": policy_id,
                "candidates": list(policy.candidates),
                "fallback": list(policy.fallback),
                "weights": dict(policy.weights),
                "efforts": dict(policy.efforts),
                "inherits_auto": role == "compaction" and policy_id == routing.routes.get("auto"),
            }
        )

    executor_states = _executors_state(routing, settings)

    catalogs: list[dict[str, Any]] = []
    if include_catalogs:
        app_settings = settings or Settings.load()
        # Installed executors are listed too, not only configured ones: the
        # picker offers their models directly, and choosing one is what
        # declares the provider. Without the catalog there would be nothing
        # to choose from, and the user would be back to editing JSON.
        wanted = [spec.executor for spec in routing.providers.values()]
        wanted += [entry["executor"] for entry in executor_states if entry["installed"]]
        catalogs = [
            list_agent_models_view(agent=executor, project="", settings=app_settings)
            for executor in dict.fromkeys(wanted)
        ]
        # Rank each vendor's current models once, here, so the UI never has to
        # infer a tier from a model's codename.
        by_agent = {str(catalog.get("agent", "")): catalog for catalog in catalogs}
        for entry in executor_states:
            catalog = by_agent.get(entry["executor"])
            entry["lineup"] = model_lineup(catalog.get("models") or []) if catalog else []

    return {
        "configured": True,
        "config_file": str(config_path),
        "default_policy": gateway_settings.default_policy,
        "token_available": gateway_settings.token_file.expanduser().exists(),
        "routes": routes,
        "providers": providers,
        "catalogs": catalogs,
        "executors": executor_states,
        "read_only_roles": sorted(codex_config.read_only_roles()),
        "upgrades": _upgrade_offers(routing, routes, catalogs),
        "model_health": {
            "missing": list(verification.missing) if verification else [],
            "unverified": [
                {"candidate": candidate, "reason": reason}
                for candidate, reason in (verification.unverified if verification else ())
            ],
            "notes": list(verification.catalog_notes) if verification else [],
        },
        "codex": _codex_state(codex_home),
    }


def set_route(
    config_path: Path,
    *,
    role: str,
    candidates: list[str],
    fallback: list[str],
    efforts: dict[str, str] | None = None,
    add_missing_models: bool = False,
    declare_providers: dict[str, str] | None = None,
    backup_dir: Path | None = None,
    backup_limit: int = ROUTE_BACKUP_LIMIT,
) -> Path:
    """Update one role's policy, validate, back up, and atomically replace.

    A role gets its own policy when it previously shared a generic one.  This
    prevents editing Worker from silently changing Explorer.  Compaction keeps
    following Auto when it already inherited Auto before the edit.
    """
    role = role.strip()
    if not role:
        raise ConfigError("role must not be empty")
    candidates = _unique_nonempty(candidates)
    fallback = [item for item in _unique_nonempty(fallback) if item not in candidates]
    if not candidates:
        raise ConfigError("at least one primary candidate is required")

    path = config_path.expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    # Validate the starting point before deriving any edit from it.
    before = RoutingConfig.parse(raw, source=str(path))
    if declare_providers:
        _declare_new_providers(raw, before, declare_providers, [*candidates, *fallback])
    if add_missing_models:
        _declare_missing_models(raw, before, [*candidates, *fallback])

    routes = raw.setdefault("routes", {})
    policies = raw.setdefault("policies", {})
    old_policy_id = before.routes.get(role)
    roles_using_old_policy = {
        route_role for route_role, policy_id in before.routes.items() if policy_id == old_policy_id
    }
    can_reuse_policy = len(roles_using_old_policy) <= 1 or (
        role == "auto" and roles_using_old_policy <= {"auto", "compaction"}
    )
    target_policy_id = _target_policy_id(
        role=role,
        old_policy_id=old_policy_id,
        policies=policies,
        can_reuse=can_reuse_policy,
    )

    old_policy = policies.get(old_policy_id, {}) if old_policy_id else {}
    preserved_weights = old_policy.get("weights") if isinstance(old_policy, dict) else None
    preserved_efforts = old_policy.get("efforts") if isinstance(old_policy, dict) else None
    selected_candidates = {*candidates, *fallback}
    merged_efforts = {
        str(candidate): str(effort).strip().lower()
        for candidate, effort in (
            preserved_efforts.items() if isinstance(preserved_efforts, dict) else ()
        )
        if candidate in selected_candidates
    }
    if efforts:
        merged_efforts.update(
            {str(candidate): str(effort).strip().lower() for candidate, effort in efforts.items()}
        )
    policy: dict[str, Any] = {"candidates": candidates}
    if fallback:
        policy["fallback"] = fallback
    if isinstance(preserved_weights, dict) and preserved_weights:
        policy["weights"] = preserved_weights
    if merged_efforts:
        policy["efforts"] = merged_efforts
    policies[target_policy_id] = policy
    routes[role] = target_policy_id

    # "Compaction inherits Auto" is represented by both routes naming the same
    # policy. Preserve that relationship when Auto has to be split out.
    if role == "auto" and old_policy_id and before.routes.get("compaction") == old_policy_id:
        routes["compaction"] = target_policy_id

    RoutingConfig.parse(raw, source=str(path))

    return _backup_and_write(path, raw, backup_dir=backup_dir, backup_limit=backup_limit)


def _backup_and_write(
    path: Path,
    raw: dict[str, Any],
    *,
    backup_dir: Path | None,
    backup_limit: int,
) -> Path:
    """Copy the current file aside, then atomically replace it.

    Callers validate ``raw`` before getting here: the backup only helps if the
    file it replaces was the last good one.
    """
    target_backup_dir = (backup_dir or path.parent).expanduser()
    target_backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = target_backup_dir / f"{path.name}.bak.{stamp}"
    suffix = 1
    while backup.exists():
        backup = target_backup_dir / f"{path.name}.bak.{stamp}.{suffix}"
        suffix += 1
    shutil.copy2(path, backup)
    _prune_route_backups(
        target_backup_dir,
        config_name=path.name,
        keep=max(1, backup_limit),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.chmod(temporary, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return backup


_DEFAULT_PROVIDER_IDS = {
    "claude": "local_claude",
    "codex": "local_codex",
    "antigravity": "local_agy",
}


def add_provider(
    config_path: Path,
    *,
    executor: str,
    provider_id: str = "",
    models: list[str] | None = None,
    settings: Settings | None = None,
    backup_dir: Path | None = None,
    backup_limit: int = ROUTE_BACKUP_LIMIT,
) -> dict[str, Any]:
    """Declare an executor in the routing config without routing anything to it.

    Policies and routes are deliberately left alone. Adding an agent and
    deciding what it should run are separate decisions, and silently changing
    which model serves a role is worse than making the user pick.
    """
    executor = executor.strip().lower()
    if executor not in PROVIDERS:
        raise ConfigError(f"unknown executor {executor!r}; supported: {', '.join(PROVIDERS)}")

    path = config_path.expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(
            f"routing config not found at {path}. Run `fluxion-provider init` first."
        ) from error
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    before = RoutingConfig.parse(raw, source=str(path))

    existing = [pid for pid, spec in before.providers.items() if spec.executor == executor]
    if existing:
        raise ConfigError(
            f"executor {executor!r} is already configured as provider {existing[0]!r}"
        )

    provider_id = provider_id.strip() or _DEFAULT_PROVIDER_IDS.get(executor, f"local_{executor}")
    if provider_id in before.providers:
        base = provider_id
        suffix = 2
        while provider_id in before.providers:
            provider_id = f"{base}_{suffix}"
            suffix += 1

    seeded = _unique_nonempty(models or []) or _seed_models(executor, settings)
    entry = {
        "id": provider_id,
        "protocol": "local_agent",
        "executor": executor,
        "enabled": True,
        "default_workspace": "",
        "models": [{"id": model_id, "capabilities": {}} for model_id in seeded],
    }
    raw.setdefault("providers", []).append(entry)
    RoutingConfig.parse(raw, source=str(path))

    backup = _backup_and_write(path, raw, backup_dir=backup_dir, backup_limit=backup_limit)
    return {"provider_id": provider_id, "models": seeded, "backup": str(backup)}


def _seed_models(executor: str, settings: Settings | None) -> list[str]:
    """One model, only so the provider entry satisfies the schema.

    The picker lists the live catalog rather than this file (config entries only
    supplement it), so the seed is never what the user chooses from — it just
    has to be valid, since a provider declaring no model is a config error.
    Taking the cheapest keeps the accident cheap if it ever does get routed;
    picking a "best" model would mean encoding a version ranking here, and
    version-shaped knowledge in code is what rots.
    """
    view = list_agent_models_view(agent=executor, project="", settings=settings or Settings.load())
    catalog = view.get("models") or []
    if not catalog:
        warnings = "; ".join(view.get("warnings") or []) or "the CLI returned no catalog"
        raise ConfigError(
            f"cannot seed a model for {executor!r}: {warnings}. "
            "Pass --models explicitly once you know which id to use."
        )
    # Sorted price high to low, so the last entry is the cheapest.
    return [str(catalog[-1]["id"])]


def _prune_route_backups(backup_dir: Path, *, config_name: str, keep: int) -> None:
    """Keep recent recovery points without letting routine UI saves grow forever."""
    backups = sorted(
        backup_dir.glob(f"{config_name}.bak.*"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
    )
    for stale in backups[:-keep]:
        try:
            stale.unlink()
        except OSError:
            # A stale read-only backup should not make an otherwise valid route
            # update fail. It can be cleaned up manually later.
            pass


def _target_policy_id(
    *,
    role: str,
    old_policy_id: str | None,
    policies: dict[str, Any],
    can_reuse: bool,
) -> str:
    if old_policy_id and can_reuse:
        return old_policy_id
    if role not in policies:
        return role
    base = f"{role}_route"
    candidate = base
    suffix = 2
    while candidate in policies:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _unique_nonempty(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def _declare_new_providers(
    raw: dict[str, Any],
    routing: RoutingConfig,
    declared: dict[str, str],
    candidates: list[str],
) -> None:
    """Create provider entries for executors the route now points at.

    The UI offers the models of any installed agent CLI, so a route can name a
    provider that the config has never declared. The executor is passed in
    rather than inferred from the provider id: guessing which CLI ``local_agy``
    means would be a rename away from pointing a route at the wrong agent.

    The seed is whatever the user just picked, so no default model has to be
    invented here.
    """
    entries = raw.setdefault("providers", [])
    for provider_id, executor in declared.items():
        provider_id = provider_id.strip()
        executor = executor.strip().lower()
        if not provider_id or provider_id in routing.providers:
            continue
        if executor not in PROVIDERS:
            raise ConfigError(f"unknown executor {executor!r} for provider {provider_id!r}")
        models = [
            candidate.partition(":")[2]
            for candidate in candidates
            if candidate.partition(":")[0] == provider_id and candidate.partition(":")[2]
        ]
        if not models:
            raise ConfigError(
                f"provider {provider_id!r} would be declared with no model; "
                "nothing in this route selects one"
            )
        entries.append(
            {
                "id": provider_id,
                "protocol": "local_agent",
                "executor": executor,
                "enabled": True,
                "default_workspace": "",
                "models": [
                    {"id": model_id, "capabilities": {}} for model_id in dict.fromkeys(models)
                ],
            }
        )


def _declare_missing_models(
    raw: dict[str, Any],
    routing: RoutingConfig,
    candidates: list[str],
) -> None:
    """Declare live-catalog models selected by the UI in their existing provider."""
    provider_entries = {
        str(entry.get("id", "")): entry
        for entry in raw.get("providers", [])
        if isinstance(entry, dict)
    }
    for candidate in candidates:
        provider_id, separator, model_id = candidate.partition(":")
        if not separator or not provider_id or not model_id:
            continue
        provider = routing.providers.get(provider_id)
        if provider is None:
            continue
        if model_id in provider.models:
            continue
        entry = provider_entries.get(provider_id)
        if entry is None:
            continue
        models = entry.setdefault("models", [])
        models.append({"id": model_id, "capabilities": {}})


def _upgrade_offers(
    routing: RoutingConfig,
    routes: list[dict[str, Any]],
    catalogs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Routes pinned to a model the vendor has since superseded.

    The desktop app used to carry this as a named pair of versions — a function
    and eight localized strings that said "Gemini 3.7 Flash is available" —
    so every release needed an app build before anyone was told. Deriving it
    from the catalog means the offer appears when the vendor ships, and the
    app renders whatever it is handed.

    Only same-or-lower price is offered. A newer model that costs more is a
    decision about budget, not an upgrade, and this is a suggestion the user
    has not asked for.
    """
    by_agent = {str(catalog.get("agent", "")): catalog for catalog in catalogs}
    offers: dict[tuple[str, str], dict[str, Any]] = {}

    for route in routes:
        if route["inherits_auto"]:
            # It shows whatever Auto resolves to, so upgrading Auto covers it.
            continue
        for candidate in route["candidates"]:
            provider_id, _, model_id = candidate.partition(":")
            spec = routing.providers.get(provider_id)
            if spec is None or not spec.enabled:
                continue
            catalog = by_agent.get(spec.executor)
            if catalog is None:
                continue
            newer = _newer_same_family(model_id, catalog.get("models") or [])
            if newer is None:
                continue

            key = (provider_id, model_id)
            offer = offers.get(key)
            if offer is None:
                current = _catalog_entry(model_id, catalog.get("models") or [])
                offer = {
                    "provider_id": provider_id,
                    "executor": spec.executor,
                    "from_model": model_id,
                    "to_model": str(newer["id"]),
                    "input_per_1m": newer.get("input_per_1m"),
                    "output_per_1m": newer.get("output_per_1m"),
                    "price_delta": (
                        "unchanged"
                        if current
                        and current.get("input_per_1m") == newer.get("input_per_1m")
                        and current.get("output_per_1m") == newer.get("output_per_1m")
                        else "cheaper"
                    ),
                    "roles": [],
                }
                offers[key] = offer
            if route["role"] not in offer["roles"]:
                offer["roles"].append(route["role"])

    return list(offers.values())


def _catalog_entry(model_id: str, models: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((model for model in models if str(model.get("id")) == model_id), None)


def _newer_same_family(
    model_id: str,
    models: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The highest newer version of this exact variant, if it costs no more."""
    parsed = parse_model_family(model_id)
    current = _catalog_entry(model_id, models)
    if parsed is None or current is None:
        return None
    prefix, version, suffix = parsed
    current_input = current.get("input_per_1m")
    current_output = current.get("output_per_1m")
    if current_input is None or current_output is None:
        return None

    best: tuple[tuple[int, ...], dict[str, Any]] | None = None
    for model in models:
        other = parse_model_family(str(model.get("id", "")))
        if other is None:
            continue
        other_prefix, other_version, other_suffix = other
        if (other_prefix, other_suffix) != (prefix, suffix) or other_version <= version:
            continue
        model_input = model.get("input_per_1m")
        model_output = model.get("output_per_1m")
        if model_input is None or model_output is None:
            continue
        if model_input > current_input or model_output > current_output:
            continue
        if best is None or other_version > best[0]:
            best = (other_version, model)
    return best[1] if best else None


def _executors_state(
    routing: RoutingConfig | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """One entry per known executor, whether or not the config mentions it.

    Preferences used to derive the executor list from the config alone, so an
    agent CLI that was installed but absent from ``provider_routes.json`` had no
    representation anywhere in the UI — it simply did not appear, which reads as
    "unsupported" rather than "not set up yet". Reporting every known executor
    with an explicit state lets the UI offer to add the missing ones.

    Detection is the same filesystem-only probe the run path uses, so this stays
    cheap enough for a state call the window makes on every refresh.

    The two command overrides are read straight from the environment rather than
    through ``Settings.load()``: building settings also loads ``.env`` into the
    process, and this runs on the local-only state path whose whole point is to
    touch nothing. ``fluxion-provider`` already loads ``.env`` at startup, so a
    configured command is still visible here.
    """
    overrides = {
        "claude": os.environ.get("FLUXION_CLAUDE_COMMAND", "").strip(),
        "antigravity": os.environ.get("FLUXION_ANTIGRAVITY_COMMAND", "").strip(),
    }
    if settings is not None:
        overrides["claude"] = settings.claude_command
        overrides["antigravity"] = settings.antigravity_command
    read_only_support = executor_read_only_support()

    configured: dict[str, list[tuple[str, bool]]] = {}
    for provider_id, spec in routing.providers.items() if routing else ():
        configured.setdefault(spec.executor, []).append((provider_id, spec.enabled))

    entries: list[dict[str, Any]] = []
    for executor in PROVIDERS:
        probe = detect_executor(executor, configured_command=overrides.get(executor, ""))
        installed = probe.status == "available"
        providers = configured.get(executor, [])
        enabled_ids = [provider_id for provider_id, enabled in providers if enabled]

        if not providers:
            # A CLI that is present but unrouted is the one state worth acting
            # on: the UI turns it into an "add" affordance.
            state = "available" if installed else "not_installed"
        elif not enabled_ids:
            # Deliberately switched off, so a missing CLI is not worth flagging.
            state = "disabled"
        elif not installed:
            state = "cli_missing"
        else:
            state = "ready"

        entries.append(
            {
                "executor": executor,
                "state": state,
                "installed": installed,
                "detect_status": probe.status,
                "detect_detail": probe.detail,
                "path": probe.path,
                "provider_ids": [provider_id for provider_id, _ in providers],
                "enabled_provider_ids": enabled_ids,
                # The id to create if the UI routes to this executor before it
                # has a provider entry. Naming it here keeps the convention in
                # one place instead of rebuilt on the Swift side.
                "default_provider_id": _DEFAULT_PROVIDER_IDS.get(executor, f"local_{executor}"),
                "enforces_read_only": read_only_support.get(executor, False),
                # Filled in by preferences_state once catalogs are read; the
                # local-only path has no catalog and so offers no ranking.
                "lineup": [],
            }
        )
    return entries


def _codex_state(codex_home: Path | None) -> dict[str, Any]:
    home = (codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex"))).expanduser()
    config_path = home / "config.toml"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    managed = codex_config.BEGIN_MARKER in text and codex_config.END_MARKER in text
    roles: list[dict[str, Any]] = []
    for role in codex_config.DEFAULT_ROLES:
        role_path = home / "agents" / f"{role}.toml"
        model = ""
        provider = ""
        readable = False
        error = ""
        if role_path.exists():
            try:
                parsed = tomllib.loads(role_path.read_text(encoding="utf-8"))
                model = str(parsed.get("model", ""))
                provider = str(parsed.get("model_provider", ""))
                readable = True
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
                error = str(exc)
        roles.append(
            {
                "role": role,
                "installed": role_path.exists(),
                "readable": readable,
                "error": error,
                "model": model,
                "provider": provider,
                "path": str(role_path),
            }
        )
    return {
        "home": str(home),
        "config_path": str(config_path),
        "managed_block": managed,
        "installed": managed and all(item["installed"] and item["readable"] for item in roles),
        "roles": roles,
    }
