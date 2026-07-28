from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from fluxion.usage.history import pricing
from fluxion.usage.model_identity import identify_model
from fluxion.usage.model_rates import short_request_price_rank

_AGY_MODEL_TIMEOUT_SEC = 30.0
_AGY_MODEL_CATALOG_TTL_SEC = 3600.0
_AGY_MODEL_CATALOG_FAILURE_TTL_SEC = 30.0


@dataclass(frozen=True)
class _ModelCatalogCacheEntry:
    names: tuple[str, ...]
    error: str
    expires_at: float


_MODEL_CATALOG_CACHE: dict[str, _ModelCatalogCacheEntry] = {}


def load_antigravity_model_names(command: str = "") -> list[str]:
    names, _error = load_antigravity_model_catalog(command)
    return names


def load_antigravity_model_catalog(command: str = "") -> tuple[list[str], str]:
    resolved_command = resolve_antigravity_command(command)
    now = time.monotonic()
    cached = _MODEL_CATALOG_CACHE.get(resolved_command)
    if cached is not None and cached.expires_at > now:
        return list(cached.names), cached.error

    names, error = _fetch_antigravity_model_catalog(resolved_command)
    ttl = _AGY_MODEL_CATALOG_TTL_SEC if names else _AGY_MODEL_CATALOG_FAILURE_TTL_SEC
    _MODEL_CATALOG_CACHE[resolved_command] = _ModelCatalogCacheEntry(
        names=tuple(names),
        error=error,
        expires_at=now + ttl,
    )
    return names, error


def _fetch_antigravity_model_catalog(resolved_command: str) -> tuple[list[str], str]:
    try:
        result = subprocess.run(
            [resolved_command, "models"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=_AGY_MODEL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return [], f"`agy models` timed out after {_AGY_MODEL_TIMEOUT_SEC:g}s"
    except Exception as exc:
        return [], f"`agy models` failed: {type(exc).__name__}: {exc}"

    names: list[str] = []
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    if names:
        return names, ""
    return [], "`agy models` returned no model names"


def select_antigravity_ping_model(*, pool_key: str, command: str = "") -> str | None:
    return select_antigravity_ping_model_from_names(
        pool_key=pool_key,
        model_names=load_antigravity_model_names(command),
    )


def select_antigravity_ping_model_from_names(
    *,
    pool_key: str,
    model_names: list[str],
) -> str | None:
    pool = _pool_name(pool_key)
    if not pool:
        return None
    candidates = [name for name in model_names if _model_pool(name) == pool]
    if not candidates:
        return None
    return sorted(candidates, key=lambda name: _ping_model_rank(name, pool))[0]


def resolve_antigravity_command(command: str = "") -> str:
    configured = command.strip()
    if configured and configured != "agy":
        return configured
    resolved = shutil.which("agy")
    if resolved:
        return resolved
    for candidate in _command_search_candidates():
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            return str(path)
    return configured or "agy"


def _command_search_candidates() -> list[str]:
    return [
        "~/.local/bin/agy",
        "/opt/homebrew/bin/agy",
        "/usr/local/bin/agy",
    ]


def _pool_name(pool_key: str) -> str:
    key = (pool_key or "").lower()
    if "gemini" in key:
        return "gemini"
    if "external" in key:
        return "external"
    return ""


def _model_pool(model_name: str) -> str:
    return identify_model("antigravity", model_name).quota_pool


def _ping_model_rank(
    model_name: str,
    pool: str,
) -> tuple[tuple[int, float, float, float, float], int, int, int, tuple[int, ...], str]:
    name = model_name.lower()
    if pool == "gemini":
        family_rank = _first_match_rank(
            name,
            (
                ("flash", 0),
                ("pro", 2),
            ),
            default=1,
        )
    else:
        family_rank = _first_match_rank(
            name,
            (
                ("gpt-oss", 0),
                ("haiku", 1),
                ("sonnet", 2),
                ("opus", 3),
            ),
            default=4,
        )
    return (
        _price_rank(model_name),
        family_rank,
        _effort_rank(name),
        _thinking_rank(name),
        _newest_version_rank(name),
        name,
    )


def _price_rank(model_name: str) -> tuple[int, float, float, float, float]:
    """Rank a live model by its current short-request token rates.

    Auto-ping prompts have a non-trivial fixed input and a tiny output, so input
    price is the primary cost signal and output price breaks an input-price tie.
    Cache rates follow as deterministic secondary price keys. Missing or
    malformed price data ranks after every model with a usable rate, where the
    existing family/effort heuristics remain the fallback.
    """
    return short_request_price_rank(pricing.current_rates_for("antigravity", model_name))


def _newest_version_rank(name: str) -> tuple[int, ...]:
    return tuple(-part for part in identify_model("antigravity", name).version)


def _effort_rank(name: str) -> int:
    effort = identify_model("antigravity", name).effort
    return {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}.get(effort, 5)


def _thinking_rank(name: str) -> int:
    return 1 if "thinking" in name else 0


def _first_match_rank(name: str, items: tuple[tuple[str, int], ...], *, default: int) -> int:
    for marker, rank in items:
        if marker in name:
            return rank
    return default
