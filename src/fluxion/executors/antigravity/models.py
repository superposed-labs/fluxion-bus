from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from fluxion.executors.model_resolution import ModelCatalog, ModelOption
from fluxion.usage.history import pricing
from fluxion.usage.model_identity import identify_model, parse_model_name
from fluxion.usage.model_rates import short_request_price_rank

# Canonical effort ordering, cheapest first. Vendors publish subsets of it.
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")

_AGY_MODEL_TIMEOUT_SEC = 30.0
_AGY_MODEL_CATALOG_TTL_SEC = 3600.0
_AGY_MODEL_CATALOG_FAILURE_TTL_SEC = 30.0


@dataclass(frozen=True)
class _ModelCatalogCacheEntry:
    entries: tuple[tuple[str, str], ...]
    error: str
    expires_at: float
    stale: bool = False


_MODEL_CATALOG_CACHE: dict[str, _ModelCatalogCacheEntry] = {}
# Last catalog that actually came back, kept past its TTL. `agy models` shells
# out and can time out; without this a transient failure would make effort
# resolution — which can only pick from published ids — refuse runs that were
# perfectly launchable a minute earlier.
_MODEL_CATALOG_LAST_GOOD: dict[str, tuple[tuple[str, str], ...]] = {}


def load_antigravity_model_names(command: str = "") -> list[str]:
    names, _error = load_antigravity_model_catalog(command)
    return names


def load_antigravity_model_catalog(command: str = "") -> tuple[list[str], str]:
    entries, error, _status = load_antigravity_model_entries(command)
    return [model_id for model_id, _label in entries], error


def load_antigravity_model_entries(
    command: str = "",
) -> tuple[list[tuple[str, str]], str, str]:
    """Return `(id, display label)` pairs, the last error, and catalog freshness.

    The display label is agy's own second column. It is kept because a model
    family this code has never seen still renders correctly from the vendor's
    name, where deriving one from the id would leak the raw slug into the UI.
    """
    resolved_command = resolve_antigravity_command(command)
    now = time.monotonic()
    cached = _MODEL_CATALOG_CACHE.get(resolved_command)
    if cached is not None and cached.expires_at > now:
        return list(cached.entries), cached.error, _status_of(cached)

    entries, error = _fetch_antigravity_model_catalog(resolved_command)
    stale = False
    if entries:
        _MODEL_CATALOG_LAST_GOOD[resolved_command] = tuple(entries)
        ttl = _AGY_MODEL_CATALOG_TTL_SEC
    else:
        last_good = _MODEL_CATALOG_LAST_GOOD.get(resolved_command)
        if last_good:
            entries = list(last_good)
            stale = True
        ttl = _AGY_MODEL_CATALOG_FAILURE_TTL_SEC
    entry = _ModelCatalogCacheEntry(
        entries=tuple(entries),
        error=error,
        expires_at=now + ttl,
        stale=stale,
    )
    _MODEL_CATALOG_CACHE[resolved_command] = entry
    return list(entries), error, _status_of(entry)


def _status_of(entry: _ModelCatalogCacheEntry) -> str:
    if not entry.entries:
        return "unavailable"
    return "stale" if entry.stale else "fresh"


def _fetch_antigravity_model_catalog(resolved_command: str) -> tuple[list[tuple[str, str]], str]:
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

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in result.stdout.splitlines():
        # Newer agy releases print a tab-separated display label after the
        # selectable model id, for example:
        #   gemini-3.6-flash-high\tGemini 3.6 Flash (High)
        # Only the first column is accepted by `agy --model`.
        model_id, _, label = raw.partition("\t")
        model_id = model_id.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        entries.append((model_id, label.strip() or model_id))
    if entries:
        return entries, ""
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


def antigravity_model_catalog(command: str = "") -> ModelCatalog:
    """Group the published ids into one option per product.

    `agy models` lists every (model, effort) pair on its own line. Grouping them
    here — rather than in each consumer — is what lets the model picker, the
    executor and the resolver agree on which efforts a model actually ships.
    """
    entries, error, status = load_antigravity_model_entries(command)
    options: dict[str, dict] = {}
    for model_id, display_label in entries:
        parsed = parse_model_name("antigravity", model_id)
        option = options.setdefault(
            parsed.family,
            {
                # agy's own label, minus the effort it repeats: a family this
                # code has never seen still reads as the vendor named it.
                "label": parse_model_name("antigravity", display_label).label,
                "efforts": [],
                "variants": {},
            },
        )
        option["variants"][parsed.effort] = model_id
        if parsed.effort:
            option["efforts"].append(parsed.effort)

    built: list[ModelOption] = []
    for family, option in options.items():
        efforts = _ordered_efforts(option["efforts"])
        variants = {effort: model_id for effort, model_id in option["variants"].items() if effort}
        if not efforts:
            # No effort axis (agy's Claude entries): the published id is the only
            # thing to launch, so it stands in as the product id rather than a
            # derived name that `--model` would reject.
            family = next(iter(option["variants"].values()), family)
            variants = {}
        built.append(
            ModelOption(
                family=family,
                label=option["label"],
                efforts=tuple(efforts),
                # agy publishes no per-model default, so a bare product name
                # resolves to the cheapest effort it ships.
                default_effort=efforts[0] if efforts else "",
                variants=variants,
            )
        )
    return ModelCatalog(
        agent="antigravity",
        options=tuple(built),
        encodes_effort_in_id=True,
        status=status,
        error=error,
    )


def _ordered_efforts(efforts: list[str]) -> list[str]:
    rank = {name: index for index, name in enumerate(EFFORT_ORDER)}
    unique = {effort for effort in efforts if effort}
    return sorted(unique, key=lambda effort: (rank.get(effort, len(rank)), effort))
