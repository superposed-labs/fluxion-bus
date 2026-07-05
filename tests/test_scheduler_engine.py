from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fluxion.scheduler import engine
from fluxion.scheduler.models import (
    Action,
    Policy,
    RuleState,
    ScheduleRule,
    Trigger,
)
from fluxion.usage.models import STATUS_ERROR, STATUS_OK, ProviderUsage, UsageWindow


def _utc(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _cron_rule(expr="*/5 * * * *", *, catch_up="skip", cooldown=0, max_runs=100):
    return ScheduleRule.new(
        name="t",
        trigger=Trigger(type="cron", cron=expr, timezone="UTC"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=cooldown, catch_up=catch_up, max_runs_per_day=max_runs),
    )


def _refresh_rule(provider="codex", window="7d", *, cooldown=0):
    return ScheduleRule.new(
        name="t",
        trigger=Trigger(type="quota_refresh", provider=provider, window_key=window),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=cooldown),
    )


def _usage(
    provider="codex",
    window="7d",
    *,
    used=None,
    resets_at=None,
    fetched_at="",
    status=STATUS_OK,
):
    return [
        ProviderUsage(
            provider=provider,
            status=status,
            fetched_at=fetched_at,
            windows=[
                UsageWindow(key=window, label="Weekly", used_percent=used, resets_at=resets_at)
            ],
        )
    ]


# --- cron ----------------------------------------------------------------


def test_cron_fires_on_recent_match():
    rule = _cron_rule("*/5 * * * *")
    state = RuleState(last_eval_at=_utc(2026, 6, 3, 9, 4).isoformat())
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 9, 5, 3), [], tick_sec=60)
    assert decision.fire


def test_cron_no_fire_when_no_match():
    rule = _cron_rule("0 9 * * *")
    state = RuleState(last_eval_at=_utc(2026, 6, 3, 9, 1).isoformat())
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 9, 30), [], tick_sec=60)
    assert not decision.fire


def test_cron_skip_ignores_old_missed_occurrence():
    # Daemon was down for an hour; skip policy must NOT fire late.
    rule = _cron_rule("0 9 * * *", catch_up="skip")
    state = RuleState(last_eval_at=_utc(2026, 6, 3, 8, 0).isoformat())
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 10, 0), [], tick_sec=60)
    assert not decision.fire


def test_cron_run_once_catches_up_missed_occurrence():
    rule = _cron_rule("0 9 * * *", catch_up="run_once")
    state = RuleState(last_eval_at=_utc(2026, 6, 3, 8, 0).isoformat())
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 10, 0), [], tick_sec=60)
    assert decision.fire


# --- quota_refresh -------------------------------------------------------


def test_refresh_baseline_does_not_fire():
    rule = _refresh_rule()
    state = RuleState()  # no last_usage
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=10.0), tick_sec=60
    )
    assert not decision.fire


def test_refresh_fires_on_used_percent_cliff():
    rule = _refresh_rule()
    state = RuleState(last_usage={"used_percent": 92.0, "resets_at": None})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=1.0), tick_sec=60
    )
    assert decision.fire


def test_refresh_fires_on_reset_advance():
    rule = _refresh_rule()
    prev_reset = _utc(2026, 6, 3, 0, 0).isoformat()
    new_reset = _utc(2026, 6, 10, 0, 0).isoformat()
    state = RuleState(last_usage={"used_percent": 80.0, "resets_at": prev_reset})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=80.0, resets_at=new_reset), tick_sec=60
    )
    assert decision.fire


def test_refresh_does_not_fire_when_future_reset_rolls_forward():
    rule = _refresh_rule(provider="antigravity", window="External Models (5h)")
    now = _utc(2026, 6, 3, 9, 0)
    prev_observed = now - timedelta(minutes=30)
    prev_reset = (prev_observed + timedelta(hours=5)).isoformat()
    new_reset = (now + timedelta(hours=5)).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 0.0,
            "resets_at": prev_reset,
            "observed_at": prev_observed.isoformat(),
        }
    )

    decision = engine.evaluate_rule(
        rule,
        state,
        now,
        _usage(
            provider="antigravity",
            window="External Models (5h)",
            used=0.0,
            resets_at=new_reset,
            fetched_at=now.isoformat(),
        ),
        tick_sec=60,
    )

    assert not decision.fire


def test_refresh_rebaselines_ambiguous_low_usage_state_without_observed_at():
    rule = _refresh_rule(provider="antigravity", window="External Models (5h)")
    now = _utc(2026, 6, 3, 9, 0)
    state = RuleState(
        last_usage={
            "used_percent": 0.0,
            "resets_at": (now + timedelta(hours=4, minutes=30)).isoformat(),
        }
    )

    decision = engine.evaluate_rule(
        rule,
        state,
        now,
        _usage(
            provider="antigravity",
            window="External Models (5h)",
            used=0.0,
            resets_at=(now + timedelta(hours=5)).isoformat(),
            fetched_at=now.isoformat(),
        ),
        tick_sec=60,
    )

    assert not decision.fire


def test_refresh_fires_when_future_reset_jumps_to_new_window():
    rule = _refresh_rule()
    now = _utc(2026, 6, 3, 9, 0)
    prev_observed = now - timedelta(minutes=1)
    prev_reset = (now + timedelta(days=3)).isoformat()
    new_reset = (now + timedelta(days=10)).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 5.0,
            "resets_at": prev_reset,
            "observed_at": prev_observed.isoformat(),
        }
    )

    decision = engine.evaluate_rule(
        rule,
        state,
        now,
        _usage(used=5.0, resets_at=new_reset, fetched_at=now.isoformat()),
        tick_sec=60,
    )

    assert decision.fire


def test_refresh_rolling_estimate_suppressed_even_with_high_usage():
    """Rolling resets_at should be suppressed regardless of usage level.

    Regression: a ping at 19:22 consumed quota (usage 0%→47%), then the
    rolling "now + 5h" estimate advanced resets_at by ~40 min on the next
    observation.  The old code only suppressed this when usage was low,
    causing a false second fire at 20:02.
    """
    rule = _refresh_rule(provider="antigravity", window="External Models (5h)")
    now = _utc(2026, 6, 3, 9, 0)
    prev_observed = now - timedelta(minutes=40)
    prev_reset = (prev_observed + timedelta(hours=5)).isoformat()
    new_reset = (now + timedelta(hours=5)).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 47.0,
            "resets_at": prev_reset,
            "observed_at": prev_observed.isoformat(),
        }
    )

    decision = engine.evaluate_rule(
        rule,
        state,
        now,
        _usage(
            provider="antigravity",
            window="External Models (5h)",
            used=47.0,
            resets_at=new_reset,
            fetched_at=now.isoformat(),
        ),
        tick_sec=60,
    )

    assert not decision.fire


def test_refresh_fires_when_resets_at_cleared():
    """When resets_at goes from a timestamp to null, the window has reset.

    Some providers (e.g. Claude) stop reporting resets_at after a window
    resets and usage drops to 0%.  The engine should detect the disappearing
    countdown as a reset event.
    """
    rule = _refresh_rule(provider="claude", window="5h")
    now = _utc(2026, 6, 3, 12, 0)
    prev_reset = _utc(2026, 6, 3, 11, 20).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 19.0,
            "resets_at": prev_reset,
            "observed_at": (now - timedelta(minutes=5)).isoformat(),
        }
    )

    decision = engine.evaluate_rule(
        rule,
        state,
        now,
        _usage(
            provider="claude",
            window="5h",
            used=0.0,
            resets_at=None,
            fetched_at=now.isoformat(),
        ),
        tick_sec=60,
    )

    assert decision.fire
    assert "resets_at cleared" in decision.reason


def test_refresh_no_fire_on_small_change():
    rule = _refresh_rule()
    state = RuleState(last_usage={"used_percent": 50.0, "resets_at": None})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=55.0), tick_sec=60
    )
    assert not decision.fire


def test_refresh_fires_on_medium_drop_light_user():
    rule = _refresh_rule()
    # Drop from 30% to 5% (drop is 25% which is >= 20% margin, and current 5% <= 15% ceiling)
    state = RuleState(last_usage={"used_percent": 30.0, "resets_at": None})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=5.0), tick_sec=60
    )
    assert decision.fire


def test_refresh_no_fire_on_too_small_drop():
    rule = _refresh_rule()
    # Drop from 25% to 10% (drop is 15% which is < 20% margin)
    state = RuleState(last_usage={"used_percent": 25.0, "resets_at": None})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=10.0), tick_sec=60
    )
    assert not decision.fire


def test_refresh_no_usage_when_provider_errored():
    rule = _refresh_rule()
    state = RuleState(last_usage={"used_percent": 90.0, "resets_at": None})
    decision = engine.evaluate_rule(
        rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=1.0, status=STATUS_ERROR), tick_sec=60
    )
    assert not decision.fire


# --- safety rails --------------------------------------------------------


def test_cooldown_blocks_refire():
    rule = _cron_rule("*/5 * * * *", cooldown=3600)
    state = RuleState(
        last_eval_at=_utc(2026, 6, 3, 9, 4).isoformat(),
        last_fired_at=_utc(2026, 6, 3, 9, 0).isoformat(),
    )
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 9, 5, 3), [], tick_sec=60)
    assert not decision.fire
    assert "cooldown" in decision.reason


def test_daily_cap_blocks_fire():
    rule = _cron_rule("*/5 * * * *", max_runs=2)
    state = RuleState(
        last_eval_at=_utc(2026, 6, 3, 9, 4).isoformat(),
        runs_today=2,
        runs_today_date="2026-06-03",
    )
    decision = engine.evaluate_rule(rule, state, _utc(2026, 6, 3, 9, 5, 3), [], tick_sec=60)
    assert not decision.fire


def test_observe_window_extracts_current():
    obs = engine.observe_window(
        _usage(
            used=42.0,
            resets_at="2026-06-10T00:00:00+00:00",
            fetched_at="2026-06-03T00:00:00+00:00",
        ),
        "codex",
        "7d",
    )
    assert obs == {
        "used_percent": 42.0,
        "resets_at": "2026-06-10T00:00:00+00:00",
        "observed_at": "2026-06-03T00:00:00+00:00",
    }
    assert engine.observe_window(_usage(used=42.0), "claude", "7d") is None
