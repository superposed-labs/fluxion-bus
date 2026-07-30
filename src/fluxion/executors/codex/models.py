"""The Codex CLI's live model catalog.

Shaped to match `executors/antigravity/models.py` so callers can treat both
CLIs the same way: a `(names, error)` pair where a non-empty error means the
catalog could not be read, which is a different fact from "the catalog is
readable and this model is not in it". Only the second one justifies calling a
configured model dead.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from fluxion.codex_command import resolve_codex_command

# Longer than the 5s used on request-path helpers: this runs from `doctor` and a
# scheduled check, where a slow-but-correct answer beats a timeout that reads as
# "catalog unavailable" and hides a genuinely retired model.
_CODEX_MODEL_TIMEOUT_SEC = 15.0
_CODEX_MODEL_CATALOG_TTL_SEC = 3600.0
_CODEX_MODEL_CATALOG_FAILURE_TTL_SEC = 30.0


@dataclass(frozen=True)
class _ModelCatalogCacheEntry:
    names: tuple[str, ...]
    error: str
    expires_at: float


_MODEL_CATALOG_CACHE: dict[str, _ModelCatalogCacheEntry] = {}


def load_codex_model_names(command: str = "") -> list[str]:
    names, _error = load_codex_model_catalog(command)
    return names


def load_codex_model_catalog(command: str = "") -> tuple[list[str], str]:
    """Model slugs `codex debug models` reports, and why it failed if it did."""
    resolved_command = resolve_command(command)
    now = time.monotonic()
    cached = _MODEL_CATALOG_CACHE.get(resolved_command)
    if cached is not None and cached.expires_at > now:
        return list(cached.names), cached.error

    names, error = _fetch_codex_model_catalog(resolved_command)
    # A failure expires quickly so a transient CLI problem does not keep a stale
    # "unavailable" answer around for an hour.
    ttl = _CODEX_MODEL_CATALOG_TTL_SEC if names else _CODEX_MODEL_CATALOG_FAILURE_TTL_SEC
    _MODEL_CATALOG_CACHE[resolved_command] = _ModelCatalogCacheEntry(
        names=tuple(names),
        error=error,
        expires_at=now + ttl,
    )
    return names, error


def _fetch_codex_model_catalog(resolved_command: str) -> tuple[list[str], str]:
    try:
        result = subprocess.run(
            [resolved_command, "debug", "models"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=_CODEX_MODEL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return [], f"`codex debug models` timed out after {_CODEX_MODEL_TIMEOUT_SEC:g}s"
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        return [], f"`codex debug models` failed: {type(exc).__name__}: {exc}"

    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        return [], f"`codex debug models` returned output that is not JSON: {exc}"

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return [], "`codex debug models` returned no models list"

    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if slug and slug not in names:
            names.append(slug)
    if names:
        return names, ""
    return [], "`codex debug models` returned no model slugs"


def resolve_command(command: str = "") -> str:
    configured = command.strip()
    if configured and configured != "codex":
        return configured
    return resolve_codex_command() or "codex"
