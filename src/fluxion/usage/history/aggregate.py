"""Rolling a stream of :class:`UsageEntry` into the console payload.

Global de-duplication, time-window filtering, per-day/hour/model/provider
buckets, streaks, peak hour, and per-turn cost (priced as-of each turn's date).
Pure: no I/O, given the entries and a clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from fluxion.usage.history import pricing
from fluxion.usage.history.entry import UsageEntry

# Supported analytics windows. "all" = no cutoff; the others are trailing days
# counted in the local timezone (inclusive of today).
WINDOW_DAYS: dict[str, int | None] = {"all": None, "30d": 30, "7d": 7, "1d": 1}


@dataclass
class _Bucket:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    messages: int = 0
    sessions: set[str] = field(default_factory=set)

    def add(self, e: UsageEntry) -> None:
        self.input_tokens += e.input_tokens
        self.output_tokens += e.output_tokens
        self.cache_creation_tokens += e.cache_creation_tokens
        self.cache_read_tokens += e.cache_read_tokens
        self.messages += 1
        if e.session_id:
            self.sessions.add(e.session_id)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    @property
    def generated_tokens(self) -> int:
        # "New" tokens — what was actually produced/sent this period, excluding
        # cache re-reads (which are repeated context, not fresh work).
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens

    @property
    def cache_hit(self) -> float:
        # Share of all *input-side* context that came from cache rather than
        # being freshly sent: cache_read / (cache_read + input + cache_write).
        denom = self.cache_read_tokens + self.input_tokens + self.cache_creation_tokens
        return round(self.cache_read_tokens / denom, 4) if denom else 0.0

    def token_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
            "generated_tokens": self.generated_tokens,
        }


def _streaks(active_days: set[date], today: date) -> tuple[int, int]:
    """Return (current_streak, longest_streak) over a set of active dates.

    The current streak is the run of consecutive days ending at the most recent
    active day, but only counts as "current" if that day is today or yesterday
    (so a streak that already lapsed reports 0)."""
    if not active_days:
        return 0, 0
    ordered = sorted(active_days)
    longest = run = 1
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)

    latest = ordered[-1]
    if (today - latest).days > 1:
        return 0, longest
    current = 1
    cursor = latest
    members = active_days
    while (cursor - timedelta(days=1)) in members:
        cursor -= timedelta(days=1)
        current += 1
    return current, longest


def aggregate(
    entries: Iterable[UsageEntry],
    *,
    window: str = "all",
    tz: timezone | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Roll a stream of UsageEntry into the console JSON for one window.

    Day/hour grouping uses local time (`tz`, default system local) so "peak
    hour" and the heatmap line up with what the user sees on the clock."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    local_now = now.astimezone(tz) if tz is not None else now.astimezone()
    today = local_now.date()

    days = WINDOW_DAYS.get(window, None)
    cutoff = today - timedelta(days=days - 1) if days else None

    total = _Bucket()
    by_day: dict[str, _Bucket] = {}
    by_hour: list[_Bucket] = [_Bucket() for _ in range(24)]
    by_model: dict[str, _Bucket] = {}
    model_provider: dict[str, str] = {}
    model_cost: dict[str, float] = {}
    model_context_counts: dict[str, dict[str, int]] = {}
    by_provider: dict[str, _Bucket] = {}
    total_cost = 0.0
    uncosted_tokens = 0
    total_context_counts = {"short": 0, "long": 0}
    providers: set[str] = set()
    # Claude transcripts can contain both partial and final usage snapshots for
    # the same streamed message, as well as exact copies after fork/resume.
    # Keep the largest snapshot before applying the time window so aggregation
    # uses the final usage record and cannot discard an in-window copy because
    # an out-of-window duplicate happened to be encountered first. On equal
    # snapshots prefer the *earliest* timestamp: a Codex fork replays the parent
    # history with every timestamp rewritten to the fork instant, and only the
    # original copy carries the day the turn actually ran.
    unique_entries: dict[str, UsageEntry] = {}
    for e in entries:
        current = unique_entries.get(e.dedup_key)
        if (
            current is None
            or e.total_tokens > current.total_tokens
            or (e.total_tokens == current.total_tokens and e.ts < current.ts)
        ):
            unique_entries[e.dedup_key] = e

    by_day_full: dict[str, _Bucket] = {}
    for e in unique_entries.values():
        local = e.ts.astimezone(tz) if tz is not None else e.ts.astimezone()
        day = local.date()
        by_day_full.setdefault(day.isoformat(), _Bucket()).add(e)

        if cutoff is not None and day < cutoff:
            continue
        providers.add(e.provider)
        total.add(e)
        by_provider.setdefault(e.provider, _Bucket()).add(e)
        by_day.setdefault(day.isoformat(), _Bucket()).add(e)
        by_hour[local.hour].add(e)
        by_model.setdefault(e.model, _Bucket()).add(e)
        model_provider.setdefault(e.model, e.provider)
        # Price each turn at the rate in effect on the day it ran, so a past
        # price change (or promo) is reflected instead of back-pricing
        # everything at today's rate. Fast-mode turns price at the premium.
        rate = pricing._rates_for(e.provider, e.model, day.isoformat(), e.is_fast)
        cost = pricing._entry_cost(e, rate)
        context_tier = pricing._context_tier_for_entry(e, rate)
        total_cost += cost
        model_cost[e.model] = model_cost.get(e.model, 0.0) + cost
        if context_tier is not None:
            total_context_counts[context_tier] += 1
            per_model = model_context_counts.setdefault(e.model, {"short": 0, "long": 0})
            per_model[context_tier] += 1
        if rate is None:
            # No rate resolved (local/unrecognised model) — its tokens are real
            # but uncosted, so surface the volume rather than hide it as $0.
            uncosted_tokens += e.total_tokens

    active_dates_full = {date.fromisoformat(d) for d in by_day_full}
    current_streak, longest_streak = _streaks(active_dates_full, today)

    if days:
        active_days = sum(1 for d in active_dates_full if (today - d).days < days)
        span_days = days
    else:
        active_days = len(active_dates_full)
        span_days = (today - min(active_dates_full)).days + 1 if active_dates_full else 0

    peak_hour = max(range(24), key=lambda h: by_hour[h].messages)
    if by_hour[peak_hour].messages == 0:
        peak_hour = None  # type: ignore[assignment]

    top_model = max(by_model, key=lambda m: by_model[m].total_tokens, default=None)

    model_rows = []
    for model, b in by_model.items():
        model_rows.append(
            {
                "model": model,
                "provider": model_provider.get(model, ""),
                "messages": b.messages,
                "sessions": len(b.sessions),
                "cost": round(model_cost.get(model, 0.0), 4),
                "context_tier_breakdown": model_context_counts.get(model, {"short": 0, "long": 0}),
                **b.token_dict(),
            }
        )
    model_rows.sort(key=lambda row: row["total_tokens"], reverse=True)

    day_list_full = sorted(by_day_full)
    first_day = day_list_full[0] if day_list_full else None
    return {
        "window": window,
        "providers": sorted(providers),
        "prices_updated_at": pricing._load_prices().get("updated_at"),
        "totals": {
            "sessions": len(total.sessions),
            "messages": total.messages,
            "active_days": active_days,
            "span_days": span_days,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "peak_hour": peak_hour,
            "top_model": top_model,
            "cache_hit": total.cache_hit,
            "cost": round(total_cost, 2),
            "uncosted_tokens": uncosted_tokens,
            "context_tier_breakdown": total_context_counts,
            "first_day": first_day,
            "last_day": day_list_full[-1] if day_list_full else None,
            **total.token_dict(),
        },
        "by_day": [
            {
                "date": d,
                "messages": by_day_full[d].messages,
                "total_tokens": by_day_full[d].total_tokens,
            }
            for d in day_list_full
        ],
        "by_hour": [
            {"hour": h, "messages": by_hour[h].messages, "total_tokens": by_hour[h].total_tokens}
            for h in range(24)
        ],
        "by_model": model_rows,
        "by_provider": [
            {"provider": provider, **bucket.token_dict()}
            for provider, bucket in sorted(by_provider.items())
        ],
    }
