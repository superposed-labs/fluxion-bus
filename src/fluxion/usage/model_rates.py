from __future__ import annotations

import datetime
from typing import Any

from fluxion.usage.model_identity import billing_model_id

_UNKNOWN_PRICE = float("inf")


def pick_rate(rate_list: Any, at_date: str | None) -> dict[str, Any] | None:
    """Choose the latest rate effective by ``at_date`` (or today if unspecified)."""
    if not isinstance(rate_list, list) or not rate_list:
        return None
    entries = [item for item in rate_list if isinstance(item, dict)]
    if not entries:
        return None
    ordered = sorted(entries, key=lambda rate: str(rate.get("effective_date", "")))
    effective_cutoff = at_date if at_date is not None else datetime.date.today().isoformat()
    eligible = [rate for rate in ordered if str(rate.get("effective_date", "")) <= effective_cutoff]
    return eligible[-1] if eligible else ordered[0]


def family_match(family: str, model_low: str) -> bool:
    """Match a family at a non-letter boundary, avoiding mini/gemini collisions."""
    start = 0
    while True:
        index = model_low.find(family, start)
        if index == -1:
            return False
        if index == 0 or not model_low[index - 1].isalpha():
            return True
        start = index + 1


def resolve_standard_rate(
    prices: dict[str, Any],
    *,
    provider: str,
    model: str,
    at_date: str | None = None,
) -> dict[str, Any] | None:
    """Resolve exact model, family, then provider fallback pricing."""
    canonical = billing_model_id(provider, model)
    models = prices.get("models") or {}
    exact = models.get(model.strip()) or models.get(canonical)
    if isinstance(exact, dict):
        rate = pick_rate(exact.get("rates"), at_date)
        if rate is not None:
            return rate
    for family, rate_list in (prices.get("families") or {}).items():
        if family_match(str(family), canonical):
            rate = pick_rate(rate_list, at_date)
            if rate is not None:
                return rate
    return pick_rate((prices.get("providers") or {}).get(provider), at_date)


def short_request_price_rank(
    rate: dict[str, Any] | None,
) -> tuple[int, float, float, float, float]:
    """Return a cheapest-first price key for small fixed-input requests."""
    if not isinstance(rate, dict):
        return (1, _UNKNOWN_PRICE, _UNKNOWN_PRICE, _UNKNOWN_PRICE, _UNKNOWN_PRICE)
    context = rate.get("context_pricing")
    if isinstance(context, dict) and isinstance(context.get("short"), dict):
        rate = context["short"]
    try:
        return (
            0,
            float(rate["in"]),
            float(rate["out"]),
            float(rate.get("cw", rate["in"])),
            float(rate.get("cr", rate["in"])),
        )
    except (KeyError, TypeError, ValueError):
        return (1, _UNKNOWN_PRICE, _UNKNOWN_PRICE, _UNKNOWN_PRICE, _UNKNOWN_PRICE)
