"""Subscription plan list prices, loaded from `plan_prices.json`.

This is the display-only companion to `model_prices.json`: the Stats panel frames
the API-equivalent token cost against what the user actually pays per month for
their subscription ("≈ N× plan price"). The monthly fee is resolved from the plan
name the quota probe already reports (`ProviderUsage.account_label`, e.g. "pro",
"plus"), so the comparison tracks the real subscription with no hand-configured
numbers. It never touches the token cost estimate.

Sourced via `price_data.load_price_json` (refreshed cache → bundled snapshot),
shared with the standalone repo github.com/superposed-labs/llm-price-table.
"""

from __future__ import annotations

import functools
from typing import Any

from fluxion.usage import price_data

# Safety net if neither cache nor bundled file is readable, so the panel never
# hard-fails.
_FALLBACK_PLAN_PRICES: dict[str, Any] = {
    "plans": {
        "claude": {
            "free": 0,
            "pro": 20,
            "max": 100,
            "max-5x": 100,
            "max-20x": 200,
            "max20x": 200,
            "team": 30,
        },
        "codex": {"free": 0, "plus": 20, "pro": 200, "team": 30, "business": 30},
        "antigravity": {"google ai free": 0, "google ai pro": 20, "google ai ultra": 250},
    },
}


@functools.lru_cache(maxsize=1)
def _load_plan_prices() -> dict[str, Any]:
    data = price_data.load_price_json("plan_prices.json")
    if isinstance(data, dict) and isinstance(data.get("plans"), dict):
        return data
    return _FALLBACK_PLAN_PRICES


def plan_monthly_for(provider: str, account_label: str | None) -> float | None:
    """Official monthly USD for a provider's subscription tier, matched by the
    plan name the quota probe reports. Returns None when the label isn't
    recognised (or absent), so the UI omits the comparison instead of showing a
    wrong figure."""
    if not account_label:
        return None
    table = _load_plan_prices()["plans"].get(provider)
    if not isinstance(table, dict):
        return None
    value = table.get(account_label.strip().lower())
    return float(value) if isinstance(value, (int, float)) else None
