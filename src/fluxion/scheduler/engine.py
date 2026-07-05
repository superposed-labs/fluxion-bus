from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fluxion.scheduler import cron
from fluxion.scheduler.models import (
    CATCH_UP_RUN_ONCE,
    TRIGGER_CRON,
    TRIGGER_QUOTA_REFRESH,
    RuleState,
    ScheduleRule,
    parse_iso,
)
from fluxion.usage.models import STATUS_OK, ProviderUsage

# A refresh resets a window to ~0. We treat it as an edge when used_percent
# drops by this margin AND lands at/below the low ceiling, OR when the reset
# timestamp jumps forward (a new window started). Some providers report an
# estimated rolling "now + window" reset; that pattern is filtered out by
# checking whether the reset-timestamp advance roughly matches the elapsed
# observation time (regardless of usage level).
REFRESH_DROP_MARGIN = 20.0
REFRESH_LOW_CEILING = 15.0
REFRESH_RESET_EPSILON_SEC = 90
REFRESH_ROLLING_TOLERANCE_SEC = 300


@dataclass
class FireDecision:
    fire: bool
    reason: str = ""


def observe_window(
    usage: list[ProviderUsage], provider: str, window_key: str
) -> dict[str, Any] | None:
    """Extract the current {used_percent, resets_at} for a provider window,
    or None when the provider isn't OK or the window is absent."""
    for pu in usage:
        if pu.provider == provider and pu.status == STATUS_OK:
            for w in pu.windows:
                if w.key == window_key:
                    return {
                        "used_percent": w.used_percent,
                        "resets_at": w.resets_at,
                        "observed_at": pu.fetched_at,
                    }
    return None


def evaluate_rule(
    rule: ScheduleRule,
    state: RuleState,
    now: datetime,
    usage: list[ProviderUsage],
    tick_sec: int,
) -> FireDecision:
    """Decide whether `rule` should fire at `now`. Pure: never mutates state."""
    if getattr(rule, "run_now", False):
        return FireDecision(True, "manual trigger")

    # Cooldown — the primary backstop against runaway quota-edge fires.
    if state.last_fired_at:
        last = parse_iso(state.last_fired_at)
        if last and (now - last).total_seconds() < rule.policy.cooldown_sec:
            return FireDecision(False, "cooldown")

    # Daily cap.
    today = now.date().isoformat()
    if state.runs_today_date == today and state.runs_today >= rule.policy.max_runs_per_day:
        return FireDecision(False, "daily cap reached")

    trigger = rule.trigger
    if trigger.type == TRIGGER_CRON:
        return _eval_cron(rule, state, now, tick_sec)
    if trigger.type == TRIGGER_QUOTA_REFRESH:
        return _eval_quota_refresh(rule, state, now, usage)
    return FireDecision(False, f"unknown trigger type: {trigger.type}")


def _eval_cron(rule: ScheduleRule, state: RuleState, now: datetime, tick_sec: int) -> FireDecision:
    trigger = rule.trigger
    try:
        parsed = cron.parse_cron(trigger.cron)
    except cron.CronError as exc:
        return FireDecision(False, f"bad cron: {exc}")

    last_eval = parse_iso(state.last_eval_at) if state.last_eval_at else None
    if rule.policy.catch_up == CATCH_UP_RUN_ONCE and last_eval:
        # Catch up a missed occurrence (once) since the last evaluation.
        start = last_eval
    else:
        # skip: only honor occurrences within roughly the last tick.
        recent = now - timedelta(seconds=tick_sec + 30)
        start = max(last_eval, recent) if last_eval else recent

    if cron.occurred_between(parsed, trigger.timezone or "UTC", start, now):
        return FireDecision(True, f"cron {trigger.cron} [{trigger.timezone}]")
    return FireDecision(False, "no cron match in window")


def _eval_quota_refresh(
    rule: ScheduleRule,
    state: RuleState,
    now: datetime,
    usage: list[ProviderUsage],
) -> FireDecision:
    trigger = rule.trigger
    current = observe_window(usage, trigger.provider, trigger.window_key)
    if current is None:
        return FireDecision(False, f"no usage for {trigger.provider}/{trigger.window_key}")
    prev = state.last_usage
    if not prev:
        # First observation only establishes a baseline; never fire blind.
        return FireDecision(False, "baseline established")

    # Reset timestamp jumped forward → a new window started.
    cur_reset = parse_iso(current.get("resets_at"))
    prev_reset = parse_iso(prev.get("resets_at"))
    if cur_reset and prev_reset:
        reset_advance = (cur_reset - prev_reset).total_seconds()
        prev_observed = parse_iso(prev.get("observed_at"))
        cur_observed = parse_iso(current.get("observed_at"))
        observed_elapsed = (
            (cur_observed - prev_observed).total_seconds()
            if cur_observed and prev_observed
            else None
        )
        rolling_estimate = reset_advance > REFRESH_RESET_EPSILON_SEC and (
            (prev_observed is None and cur_observed is not None)
            or (
                observed_elapsed is not None
                and observed_elapsed > 0
                and abs(reset_advance - observed_elapsed) <= REFRESH_ROLLING_TOLERANCE_SEC
            )
        )
        if reset_advance > REFRESH_RESET_EPSILON_SEC and not rolling_estimate:
            return FireDecision(
                True,
                f"quota_refresh {trigger.provider}/{trigger.window_key} (reset advanced)",
            )

    # Reset timestamp disappeared → the old window expired and the provider
    # no longer reports a countdown (e.g. Claude after a 5h reset).
    if prev_reset and not cur_reset:
        return FireDecision(
            True,
            f"quota_refresh {trigger.provider}/{trigger.window_key} (resets_at cleared)",
        )

    # Used-percent fell off a cliff → window was refreshed.
    cur_used = current.get("used_percent")
    prev_used = prev.get("used_percent")
    if (
        cur_used is not None
        and prev_used is not None
        and (prev_used - cur_used) >= REFRESH_DROP_MARGIN
        and cur_used <= REFRESH_LOW_CEILING
    ):
        return FireDecision(
            True,
            f"quota_refresh {trigger.provider}/{trigger.window_key} "
            f"({prev_used:.0f}%→{cur_used:.0f}%)",
        )

    return FireDecision(False, "no refresh edge")
