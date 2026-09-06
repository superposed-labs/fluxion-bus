from __future__ import annotations

import json
import subprocess
from typing import Any

from fluxion.codex_command import resolve_codex_command
from fluxion.config.settings import Settings
from fluxion.executors.antigravity import models as antigravity_models
from fluxion.executors.model_resolution import ModelCatalog
from fluxion.subagent import AUTO_AGENT_VALUES, resolve_agent
from fluxion.usage import price_data
from fluxion.usage.model_rates import resolve_standard_rate

_CLAUDE_EXECUTOR_ALIASES = ("fable", "opus", "sonnet", "haiku")
_CLAUDE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def list_agent_models_view(
    *,
    agent: str,
    project: str,
    settings: Settings,
) -> dict[str, Any]:
    try:
        resolved_agent = _resolve_requested_agent(agent=agent, project=project, settings=settings)
    except Exception as exc:
        return {
            "found": False,
            "agent": agent,
            "project": project,
            "summary": str(exc),
            "models": [],
        }

    prices = _load_price_table()
    if resolved_agent == "codex":
        return _codex_models(settings=settings, prices=prices)
    if resolved_agent == "claude":
        return _claude_models(settings=settings, prices=prices)
    return _antigravity_models(settings=settings, prices=prices)


def _resolve_requested_agent(*, agent: str, project: str, settings: Settings) -> str:
    project_cfg = settings.resolve_project(project or None)
    if (agent or "").strip().lower() in AUTO_AGENT_VALUES and project_cfg is not None:
        project_default = project_cfg.default_executor
    else:
        project_default = ""
    return resolve_agent(
        requested=agent,
        project_default=project_default,
        settings_default=settings.default_executor,
    )


def _load_price_table() -> dict[str, Any]:
    data = price_data.load_price_json("model_prices.json")
    if not isinstance(data, dict):
        return {"models": {}, "families": {}, "providers": {}}
    for section in ("models", "families", "providers"):
        data.setdefault(section, {})
    return data


def _codex_models(*, settings: Settings, prices: dict[str, Any]) -> dict[str, Any]:
    live = _load_codex_debug_models()
    warnings: list[str] = []
    if live:
        models = [
            _model_entry(
                model_id=str(item.get("slug") or item.get("id") or "").strip(),
                provider="codex",
                prices=prices,
                source="live_catalog",
                extra={
                    "supported_reasoning_efforts": _reasoning_efforts(item),
                    "default_reasoning_effort": str(item.get("default_reasoning_level") or ""),
                },
            )
            for item in live
            if str(item.get("slug") or item.get("id") or "").strip()
        ]
        source = "live_catalog+local_prices"
    else:
        models = _price_model_entries(provider="codex", prices=prices, source="local_prices")
        source = "local_prices"
        warnings.append(
            "`codex debug models` did not return a usable catalog; availability is "
            "price-derived and depends on the local Codex CLI/account."
        )
    models = _sorted_models(models)
    return {
        "found": True,
        "agent": "codex",
        "provider": "codex",
        "configured_provider": "codex",
        "supports_model_override": True,
        "default_model": "codex-cli-default",
        "source": source,
        "price_updated_at": prices.get("updated_at", ""),
        "models": models,
        "warnings": warnings,
        "sort": "price_high_to_low",
        "effort_encoding": "separate_flag",
        "catalog_status": "fresh" if live else "unavailable",
        "note": "Codex availability comes from `codex debug models` when available.",
    }


def _claude_models(*, settings: Settings, prices: dict[str, Any]) -> dict[str, Any]:
    models = [
        _model_entry(
            model_id=model_id,
            provider="claude",
            prices=prices,
            source="executor_alias",
            extra={
                "alias": True,
                "availability": "known_cli_alias",
                "note": "Claude Code accepts model aliases via --model.",
                "supported_reasoning_efforts": list(_CLAUDE_REASONING_EFFORTS),
            },
        )
        for model_id in _CLAUDE_EXECUTOR_ALIASES
    ]
    configured = settings.claude_model.strip()
    if configured and configured not in {item["id"] for item in models}:
        models.append(
            _model_entry(
                model_id=configured,
                provider="claude",
                prices=prices,
                source="configured_model",
                extra={
                    "availability": "configured",
                    "supported_reasoning_efforts": list(_CLAUDE_REASONING_EFFORTS),
                },
            )
        )
    models = _sorted_models(models)
    return {
        "found": True,
        "agent": "claude",
        "provider": "claude",
        "configured_provider": settings.claude_provider,
        "supports_model_override": True,
        "default_model": configured or "claude-cli-default",
        "source": "executor_aliases+local_prices",
        "price_updated_at": prices.get("updated_at", ""),
        "models": models,
        "price_references": _sorted_models(
            _price_model_entries(provider="claude", prices=prices, source="local_prices")
        ),
        "supported_reasoning_efforts": list(_CLAUDE_REASONING_EFFORTS),
        "reasoning_effort_source": "claude_cli_help_global",
        "effort_encoding": "separate_flag",
        "catalog_status": "fresh",
        "warnings": [
            "Claude Code does not expose a stable local model catalog command. "
            "models[] contains CLI aliases/configured values intended for --model; "
            "price_references[] is pricing context only and may include unavailable models."
        ],
        "sort": "price_high_to_low",
    }


def _antigravity_models(*, settings: Settings, prices: dict[str, Any]) -> dict[str, Any]:
    catalog = antigravity_models.antigravity_model_catalog(settings.antigravity_command)
    reason = catalog.error or "`agy models` did not return a usable catalog"
    warnings: list[str] = []
    if catalog.options:
        models = _sorted_models(_collapsed_antigravity_models(catalog, prices=prices))
        source = "live_catalog+local_prices"
        if catalog.status == "stale":
            warnings.append(f"{reason}; models[] is the last catalog that loaded.")
    else:
        models = []
        source = "live_catalog_unavailable+local_prices"
        warnings.append(f"{reason}; models[] is empty.")
    return {
        "found": True,
        "agent": "antigravity",
        "provider": "antigravity",
        "configured_provider": "antigravity",
        "supports_model_override": True,
        "default_model": "antigravity-cli-default",
        "source": source,
        "price_updated_at": prices.get("updated_at", ""),
        "models": models,
        "price_references": _sorted_models(
            _price_model_entries(provider="antigravity", prices=prices, source="local_prices")
        ),
        "warnings": warnings,
        "sort": "price_high_to_low",
        "effort_encoding": "model_id_suffix",
        "catalog_status": catalog.status,
        "note": (
            "Antigravity availability comes from `agy models` when available. Reasoning "
            "effort is part of the model id, so pass `variants[effort]` to --model, or "
            "pass id + reasoning_effort and let Fluxion select the variant."
        ),
    }


def _collapsed_antigravity_models(
    catalog: ModelCatalog,
    *,
    prices: dict[str, Any],
) -> list[dict[str, Any]]:
    """Price-annotate one row per product.

    `agy models` lists every (model, effort) pair as its own line — eleven lines
    for six products, every effort variant of a model priced identically. The
    grouping itself lives with the executor that owns the CLI; here it only picks
    up prices, so a picker row and a usage row cannot disagree about what a
    product is called.
    """
    return [
        _model_entry(
            model_id=option.family,
            provider="antigravity",
            prices=prices,
            source="live_catalog",
            extra={
                "label": option.label,
                "availability": "live_catalog",
                "supported_reasoning_efforts": list(option.efforts),
                "default_reasoning_effort": option.default_effort,
                # The ids `agy --model` actually accepts, keyed by effort. Empty
                # for a model with no effort axis, whose `id` is launchable as-is.
                "variants": {
                    effort: model_id for effort, model_id in option.variants.items() if effort
                },
            },
        )
        for option in catalog.options
    ]


def _load_codex_debug_models() -> list[dict[str, Any]]:
    command = _resolve_codex_command()
    try:
        result = subprocess.run(
            [command, "debug", "models"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5.0,
        )
        payload = json.loads(result.stdout)
    except Exception:
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict)]


def _resolve_codex_command() -> str:
    return resolve_codex_command() or "codex"


def _price_derived_models(
    *,
    agent: str,
    provider: str,
    prices: dict[str, Any],
    default_model: str,
    configured_provider: str,
    source: str,
    warnings: list[str],
    extra_model_ids: tuple[str, ...],
) -> dict[str, Any]:
    models = _price_model_entries(provider=provider, prices=prices, source="local_prices")
    seen = {item["id"] for item in models}
    for model_id in extra_model_ids:
        if model_id in seen:
            continue
        models.append(
            _model_entry(
                model_id=model_id,
                provider=provider,
                prices=prices,
                source="executor_alias",
                extra={"alias": True, "note": "Known executor CLI alias."},
            )
        )
    models = _sorted_models(models)
    return {
        "found": True,
        "agent": agent,
        "provider": provider,
        "configured_provider": configured_provider,
        "supports_model_override": True,
        "default_model": default_model or f"{agent}-cli-default",
        "source": source,
        "price_updated_at": prices.get("updated_at", ""),
        "models": models,
        "warnings": warnings,
        "sort": "price_high_to_low",
    }


def _price_model_entries(
    *,
    provider: str,
    prices: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for model_id, payload in (prices.get("models") or {}).items():
        if not isinstance(payload, dict) or payload.get("provider") != provider:
            continue
        entries.append(
            _model_entry(model_id=model_id, provider=provider, prices=prices, source=source)
        )
    return entries


def _model_entry(
    *,
    model_id: str,
    provider: str,
    prices: dict[str, Any],
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rate = _rate_for_model(model_id=model_id, provider=provider, prices=prices)
    input_price = float(rate.get("in", 0.0)) if rate else 0.0
    output_price = float(rate.get("out", 0.0)) if rate else 0.0
    entry: dict[str, Any] = {
        "id": model_id,
        "label": model_id,
        "provider": provider,
        "source": source,
        "input_per_1m": input_price,
        "output_per_1m": output_price,
    }
    if rate and rate.get("source"):
        entry["price_source"] = rate.get("source")
    if extra:
        entry.update(extra)
    return entry


def _rate_for_model(*, model_id: str, provider: str, prices: dict[str, Any]) -> dict[str, Any]:
    return resolve_standard_rate(prices, provider=provider, model=model_id) or {}


def _reasoning_efforts(item: dict[str, Any]) -> list[str]:
    values = item.get("supported_reasoning_levels")
    if not isinstance(values, list):
        return []
    efforts: list[str] = []
    for value in values:
        if isinstance(value, dict):
            effort = str(value.get("effort") or "").strip()
        else:
            effort = str(value or "").strip()
        if effort and effort not in efforts:
            efforts.append(effort)
    return efforts


def _sorted_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for model in models:
        model_id = str(model.get("id") or "")
        if model_id:
            deduped[model_id] = model
    return sorted(
        deduped.values(),
        key=lambda item: (
            -float(item.get("output_per_1m") or 0.0),
            -float(item.get("input_per_1m") or 0.0),
            item["id"],
        ),
    )
