"""Per-turn cost estimation from the dated model-price table.

Rates are sourced via ``price_data.load_price_json`` (the newer of the
refreshed cache and the bundled snapshot) and resolved with a cascade: exact
model id → family substring (opus/sonnet/haiku/mini/...) → coarse provider
fallback → $0. The parsed table and resolved rates are cached keyed on
``price_data.price_file_stamp``, so a price file rewritten on disk is picked up
on the next lookup without a service restart. This is the one concern with its
own change cadence (prices move; see the update-model-prices skill), so it
lives apart from the parsing/aggregation pipeline.
"""

from __future__ import annotations

import functools
import re
from typing import Any

from fluxion.usage import price_data
from fluxion.usage.history.entry import UsageEntry

# Estimated API rates, USD per 1M tokens. The table is shared with the
# standalone repo github.com/superposed-labs/llm-price-table. Each entry is a
# dated list of {effective_date, in, out, cw, cr}; cost is priced per turn at
# the rate in effect on its date.
#
# `_FALLBACK_PRICES` is the embedded safety net if neither cache nor bundled
# file is readable, so cost estimation never hard-fails.
_FALLBACK_PRICES: dict[str, Any] = {
    "models": {},
    "fast": {},
    "families": {
        "opus": [
            {
                "effective_date": "2025-01-01",
                "in": 15.0,
                "out": 75.0,
                "cw": 18.75,
                "cw1h": 30.0,
                "cr": 1.5,
            }
        ],
        "sonnet": [
            {
                "effective_date": "2025-01-01",
                "in": 3.0,
                "out": 15.0,
                "cw": 3.75,
                "cw1h": 6.0,
                "cr": 0.30,
            }
        ],
        "haiku": [
            {
                "effective_date": "2025-01-01",
                "in": 0.80,
                "out": 4.0,
                "cw": 1.0,
                "cw1h": 1.6,
                "cr": 0.08,
            }
        ],
        "nano": [
            {"effective_date": "2025-01-01", "in": 0.05, "out": 0.40, "cw": 0.05, "cr": 0.005}
        ],
        "mini": [{"effective_date": "2025-01-01", "in": 0.25, "out": 2.0, "cw": 0.25, "cr": 0.025}],
        "gemini": [
            {"effective_date": "2025-01-01", "in": 1.25, "out": 10.0, "cw": 1.5, "cr": 0.31}
        ],
    },
    "providers": {
        "codex": [
            {"effective_date": "2025-01-01", "in": 1.25, "out": 10.0, "cw": 1.5, "cr": 0.125}
        ],
        "claude": [
            {
                "effective_date": "2025-01-01",
                "in": 15.0,
                "out": 75.0,
                "cw": 18.75,
                "cw1h": 30.0,
                "cr": 1.5,
            }
        ],
        "antigravity": [
            {"effective_date": "2025-01-01", "in": 1.25, "out": 10.0, "cw": 1.5, "cr": 0.31}
        ],
    },
}

_GEMINI_DISPLAY_MODEL_RE = re.compile(
    r"^gemini\s+(?P<version>\d+(?:\.\d+)*)\s+"
    r"(?P<tier>flash(?:[-\s]lite)?|pro)(?:\s*\([^()]+\))?$",
    re.IGNORECASE,
)


def _canonical_model_id(model: str) -> str:
    """Return the billing model id for a provider display label.

    Antigravity records labels such as ``Gemini 3.5 Flash (High)``. The
    parenthesized value controls thinking depth and token consumption, not the
    per-token rate, so collapse those labels to Google's official model id
    before exact price lookup. Other providers' model ids are only lowercased.
    """
    low = model.strip().lower()
    match = _GEMINI_DISPLAY_MODEL_RE.fullmatch(low)
    if match is None:
        return low
    tier = re.sub(r"\s+", "-", match.group("tier"))
    return f"gemini-{match.group('version')}-{tier}"


@functools.lru_cache(maxsize=2)
def _load_prices_for_stamp(stamp: tuple) -> dict[str, Any]:
    data = price_data.load_price_json("model_prices.json")
    if isinstance(data, dict):
        for section in ("models", "fast", "families", "providers"):
            data.setdefault(section, {})
        return data
    return _FALLBACK_PRICES


def _load_prices() -> dict[str, Any]:
    """The parsed model-price table, re-read whenever either source file
    changes on disk (the stamp is the cache key), so refreshes and upgrades
    land in a running service without a restart."""
    return _load_prices_for_stamp(price_data.price_file_stamp("model_prices.json"))


def _pick_rate(rate_list: Any, at_date: str | None) -> dict[str, float] | None:
    """Choose a rate from a dated list. `at_date=None` → the current (latest)
    rate; otherwise the latest rate whose effective_date is on or before it."""
    if not isinstance(rate_list, list) or not rate_list:
        return None
    ordered = sorted(rate_list, key=lambda r: str(r.get("effective_date", "")))
    if at_date is None:
        return ordered[-1]
    eligible = [r for r in ordered if str(r.get("effective_date", "")) <= at_date]
    return eligible[-1] if eligible else ordered[0]


def _family_match(family: str, model_low: str) -> bool:
    """True if `family` appears in the model id at a word boundary — i.e. at the
    start or right after a non-letter (`-`, `.`, digit). This stops "mini" from
    matching inside "gemini"; "flash" still matches "gemini-3-flash"."""
    start = 0
    while True:
        i = model_low.find(family, start)
        if i == -1:
            return False
        if i == 0 or not model_low[i - 1].isalpha():
            return True
        start = i + 1


def _resolve_fast(
    prices: dict[str, Any], model: str, low: str, at_date: str | None
) -> dict[str, float] | None:
    """A fast-mode turn (Anthropic Fast mode) is the same model id at a premium
    rate, so it gets its own override table: exact id first, then family. Returns
    None when the model has no fast rate (most don't), letting the caller fall
    back to the standard rate."""
    fast = prices.get("fast") or {}
    entry = fast.get(model) or fast.get(low)
    if isinstance(entry, dict):
        rate = _pick_rate(entry.get("rates"), at_date)
        if rate is not None:
            return rate
    for family, rate_list in fast.items():
        if isinstance(rate_list, list) and _family_match(family, low):
            rate = _pick_rate(rate_list, at_date)
            if rate is not None:
                return rate
    return None


@functools.lru_cache(maxsize=8192)
def _rates_for_stamp(
    stamp: tuple, provider: str, model: str, at_date: str | None, fast: bool
) -> dict[str, float] | None:
    prices = _load_prices()
    low = _canonical_model_id(model)
    if fast:
        fast_rate = _resolve_fast(prices, model, low, at_date)
        if fast_rate is not None:
            return fast_rate
    exact = prices["models"].get(model) or prices["models"].get(low)
    if isinstance(exact, dict):
        rate = _pick_rate(exact.get("rates"), at_date)
        if rate is not None:
            return rate
    for family, rate_list in prices["families"].items():
        if _family_match(family, low):
            rate = _pick_rate(rate_list, at_date)
            if rate is not None:
                return rate
    return _pick_rate(prices["providers"].get(provider), at_date)


def _rates_for(
    provider: str, model: str, at_date: str | None = None, fast: bool = False
) -> dict[str, float] | None:
    """Resolve the rate for a model, optionally as-of a date (YYYY-MM-DD) so a
    token is priced at whatever rate was in effect when it was used, and
    optionally at the Fast-mode premium. Cached across the many entries that
    share a (provider, model, date, fast); the file stamp in the key makes a
    price file rewritten on disk take effect without a restart."""
    stamp = price_data.price_file_stamp("model_prices.json")
    return _rates_for_stamp(stamp, provider, model, at_date, fast)


# The resolver cache lives on the stamped inner function; expose its clearer on
# the public name so callers (tests, price_data._invalidate_loaders) don't
# depend on the two-layer split.
_rates_for.cache_clear = _rates_for_stamp.cache_clear  # type: ignore[attr-defined]


def _rate_for_entry(e: UsageEntry, rate: dict[str, float] | None) -> dict[str, float] | None:
    """Resolve any per-entry context tier from a dated base rate record.

    Most models are flat-priced, so `rate` is already the usable leaf. Models
    with long-context pricing instead carry a `context_pricing` block whose
    threshold is checked against the request's original billed input."""
    if rate is None:
        return None
    context = rate.get("context_pricing")
    if not isinstance(context, dict):
        return rate
    metric = str(context.get("metric") or "input_tokens_total")
    if metric != "input_tokens_total":
        return rate
    short_max = int(context.get("short_max") or 0)
    short = context.get("short")
    long = context.get("long")
    if not isinstance(short, dict) or not isinstance(long, dict) or short_max <= 0:
        return rate
    billed_input = e.billed_input_tokens_total or (e.input_tokens + e.cache_read_tokens)
    return short if billed_input <= short_max else long


def _context_tier_for_entry(e: UsageEntry, rate: dict[str, float] | None) -> str | None:
    """Return the selected context tier label when the rate carries one."""
    if rate is None:
        return None
    context = rate.get("context_pricing")
    if not isinstance(context, dict):
        return None
    metric = str(context.get("metric") or "input_tokens_total")
    short_max = int(context.get("short_max") or 0)
    if metric != "input_tokens_total" or short_max <= 0:
        return None
    billed_input = e.billed_input_tokens_total or (e.input_tokens + e.cache_read_tokens)
    return "short" if billed_input <= short_max else "long"


def _entry_cost(e: UsageEntry, rate: dict[str, float] | None) -> float:
    """Estimated USD for one turn at the given rate (USD per 1M tokens). A turn
    whose model resolves to no rate (e.g. a local model) costs 0.

    Cache-write tokens are split by TTL: the 1-hour portion prices at `cw1h`
    (Anthropic 2x input), while the rest uses the provider's default `cw` rate.
    Providers without a distinct 1h rate just fall back to `cw`."""
    rate = _rate_for_entry(e, rate)
    if rate is None:
        return 0.0
    cw = rate["cw"]
    cw1h = rate.get("cw1h", cw)
    cc_1h = min(e.cache_creation_1h_tokens, e.cache_creation_tokens)
    cc_5m = e.cache_creation_tokens - cc_1h
    return (
        e.input_tokens * rate["in"]
        + e.output_tokens * rate["out"]
        + cc_5m * cw
        + cc_1h * cw1h
        + e.cache_read_tokens * rate["cr"]
    ) / 1_000_000
