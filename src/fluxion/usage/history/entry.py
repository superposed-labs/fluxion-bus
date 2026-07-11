"""The :class:`UsageEntry` record plus its cache (de)serialization.

``UsageEntry`` is the shared currency of this package: every parser produces
it, and pricing/aggregation consume it. The primitive coercion helpers and the
compact cache encoding live here next to the type they serve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class UsageEntry:
    """One de-dupable assistant turn with its token usage."""

    provider: str
    ts: datetime  # tz-aware (UTC)
    model: str
    session_id: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    dedup_key: str
    # The 1-hour portion of cache_creation_tokens (the rest uses the provider's
    # default cache-write rate). Anthropic charges 2x input for the 1h write vs
    # 1.25x for the 5m default, so the split must be priced separately; Claude
    # Code uses the 1h cache almost exclusively. Codex/others don't break this
    # out, so it stays 0.
    cache_creation_1h_tokens: int = 0
    # Anthropic Fast mode bills Opus at a premium (e.g. Opus 4.8 $10/$50 vs
    # $5/$25). The transcript's usage.speed marks these turns, so they can be
    # priced from the `fast` rate table instead of the standard one.
    is_fast: bool = False
    # The provider's original billed input for this request before Fluxion maps
    # any cached re-read into `cache_read_tokens`. This is the right basis for
    # long-context tier checks because providers price the whole request window,
    # not just the fresh-input subset.
    billed_input_tokens_total: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _entry_to_cache(e: UsageEntry) -> dict[str, Any]:
    return {
        "t": e.ts.isoformat(),
        "m": e.model,
        "s": e.session_id,
        "i": e.input_tokens,
        "o": e.output_tokens,
        "cc": e.cache_creation_tokens,
        "cc1h": e.cache_creation_1h_tokens,
        "cr": e.cache_read_tokens,
        "k": e.dedup_key,
        **({"bi": e.billed_input_tokens_total} if e.billed_input_tokens_total else {}),
        **({"f": 1} if e.is_fast else {}),
    }


def _entry_from_cache(provider: str, d: dict[str, Any]) -> UsageEntry | None:
    ts = _parse_ts(d.get("t"))
    if ts is None:
        return None
    return UsageEntry(
        provider=provider,
        ts=ts,
        model=str(d.get("m") or "unknown"),
        session_id=str(d.get("s") or ""),
        input_tokens=_int(d.get("i")),
        output_tokens=_int(d.get("o")),
        cache_creation_tokens=_int(d.get("cc")),
        cache_read_tokens=_int(d.get("cr")),
        dedup_key=str(d.get("k") or ""),
        cache_creation_1h_tokens=_int(d.get("cc1h")),
        is_fast=bool(d.get("f")),
        billed_input_tokens_total=_int(d.get("bi")),
    )
