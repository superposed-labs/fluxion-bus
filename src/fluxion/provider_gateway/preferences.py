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

from fluxion.config.settings import Settings
from fluxion.mcp_server.model_catalog import list_agent_models_view
from fluxion.provider_gateway import codex_config
from fluxion.provider_gateway.config import ConfigError, GatewaySettings, RoutingConfig
from fluxion.provider_gateway.model_catalog import verify_configured_models

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

    catalogs: list[dict[str, Any]] = []
    if include_catalogs:
        app_settings = settings or Settings.load()
        executors = list(dict.fromkeys(spec.executor for spec in routing.providers.values()))
        catalogs = [
            list_agent_models_view(agent=executor, project="", settings=app_settings)
            for executor in executors
        ]

    return {
        "configured": True,
        "config_file": str(config_path),
        "default_policy": gateway_settings.default_policy,
        "token_available": gateway_settings.token_file.expanduser().exists(),
        "routes": routes,
        "providers": providers,
        "catalogs": catalogs,
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
