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


def _step(rule, state, now, usage, tick_sec=60):
    """Evaluate one tick, then mirror the daemon's post-eval baseline update
    (``engine.advance_baseline``) so multi-tick refresh scenarios — including
    the two-observation debounce and the backward-resets_at quarantine — can be
    driven from tests."""
    decision = engine.evaluate_rule(rule, state, now, usage, tick_sec=tick_sec)
    if rule.trigger.type == "quota_refresh":
        obs = engine.observe_window(usage, rule.trigger.provider, rule.trigger.window_key)
        if obs is not None:
            engine.advance_baseline(state, obs)
    return decision


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
    # First edge only arms the debounce latch; it must be confirmed next tick.
    first = _step(rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=1.0))
    assert not first.fire
    assert "pending confirmation" in first.reason
    # The drop persists on the following observation → confirmed fire.
    second = _step(rule, state, _utc(2026, 6, 3, 9, 1), _usage(used=1.0))
    assert second.fire


def test_refresh_fires_on_reset_advance():
    rule = _refresh_rule()
    prev_reset = _utc(2026, 6, 3, 0, 0).isoformat()
    new_reset = _utc(2026, 6, 10, 0, 0).isoformat()
    state = RuleState(last_usage={"used_percent": 80.0, "resets_at": prev_reset})
    first = _step(rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=80.0, resets_at=new_reset))
    assert not first.fire
    second = _step(rule, state, _utc(2026, 6, 3, 9, 1), _usage(used=80.0, resets_at=new_reset))
    assert second.fire


def test_refresh_glitch_reverting_next_poll_never_fires():
    """A single-poll upstream glitch — the usage endpoint briefly serving a
    stale "fresh window" snapshot that reverts on the next poll — must not fire.
    Regression for Codex /wham/usage intermittently reporting 7d at ~1% with a
    forward-jumped reset for one observation, then back to the real value."""
    rule = _refresh_rule()
    real_reset = _utc(2026, 6, 12, 0, 0).isoformat()
    phantom_reset = _utc(2026, 6, 15, 0, 0).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 72.0,
            "resets_at": real_reset,
            "observed_at": _utc(2026, 6, 3, 8, 59).isoformat(),
        }
    )
    # Glitch sample: looks like a reset (used 72→1, reset jumps forward).
    glitch = _step(
        rule,
        state,
        _utc(2026, 6, 3, 9, 0),
        _usage(used=1.0, resets_at=phantom_reset, fetched_at=_utc(2026, 6, 3, 9, 0).isoformat()),
    )
    assert not glitch.fire
    # Next poll reverts to the real value → the pending edge is discarded.
    recovered = _step(
        rule,
        state,
        _utc(2026, 6, 3, 9, 1),
        _usage(used=72.0, resets_at=real_reset, fetched_at=_utc(2026, 6, 3, 9, 1).isoformat()),
    )
    assert not recovered.fire
    assert state.pending_refresh is None


def test_refresh_backward_glitch_rebound_never_fires():
    """A one-sample stale snapshot with an *earlier* resets_at must not poison
    the baseline: without quarantine, the recovery poll (real value again)
    looks like a forward reset jump against the glitch, and the debounce then
    "confirms" it against the glitch baseline while reality stays stable.
    Regression for the 2026-07-11 codex/5h false "(reset advanced)" alerts;
    values taken from the real anchor log."""
    rule = _refresh_rule(window="5h")
    real_reset = _utc(2026, 7, 11, 4, 27, 54).isoformat()
    phantom_reset = _utc(2026, 7, 11, 4, 6, 6).isoformat()
    state = RuleState(
        last_usage={
            "used_percent": 100.0,
            "resets_at": real_reset,
            "observed_at": _utc(2026, 7, 11, 2, 47, 59).isoformat(),
        }
    )
    # Glitch sample: used falls, resets_at moves backward → quarantined.
    glitch = _step(
        rule,
        state,
        _utc(2026, 7, 11, 2, 48, 59),
        _usage(
            window="5h",
            used=26.0,
            resets_at=phantom_reset,
            fetched_at=_utc(2026, 7, 11, 2, 48, 59).isoformat(),
        ),
    )
    assert not glitch.fire
    assert state.suspect_usage is not None
    assert state.last_usage["resets_at"] == real_reset  # baseline held
    # Recovery: identical to the held baseline → no edge, quarantine cleared.
    for minute, second in ((50, 3), (51, 4)):
        recovered = _step(
            rule,
            state,
            _utc(2026, 7, 11, 2, minute, second),
            _usage(
                window="5h",
                used=100.0,
                resets_at=real_reset,
                fetched_at=_utc(2026, 7, 11, 2, minute, second).isoformat(),
            ),
        )
        assert not recovered.fire
        assert state.pending_refresh is None
    assert state.suspect_usage is None


def test_refresh_backward_state_adopted_after_two_polls_and_real_reset_fires():
    """A backward resets_at that *persists* two consecutive polls is real (the
    provider re-reported the window) — it must be adopted as the baseline, and
    a genuine reset accompanying it (used cliff) must still notify."""
    rule = _refresh_rule(window="5h")
    old_reset = _utc(2026, 7, 11, 4, 27, 54).isoformat()
    earlier_reset = _utc(2026, 7, 11, 4, 6, 6).isoformat()
    state = RuleState(last_usage={"used_percent": 90.0, "resets_at": old_reset})
    # Backward + used cliff: quarantined for the baseline, but the used-cliff
    # edge (vs the held baseline) arms the debounce latch as usual.
    first = _step(
        rule,
        state,
        _utc(2026, 7, 11, 3, 0),
        _usage(window="5h", used=1.0, resets_at=earlier_reset),
    )
    assert not first.fire
    assert "pending confirmation" in first.reason
    assert state.last_usage["resets_at"] == old_reset
    # Persists → confirmed fire, and the baseline adopts the backward state.
    second = _step(
        rule,
        state,
        _utc(2026, 7, 11, 3, 1),
        _usage(window="5h", used=1.0, resets_at=earlier_reset),
    )
    assert second.fire
    assert state.suspect_usage is None
    assert state.last_usage["resets_at"] == earlier_reset


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

    first = _step(
        rule, state, now, _usage(used=5.0, resets_at=new_reset, fetched_at=now.isoformat())
    )
    assert not first.fire
    later = now + timedelta(minutes=1)
    second = _step(
        rule, state, later, _usage(used=5.0, resets_at=new_reset, fetched_at=later.isoformat())
    )
    assert second.fire


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

    cleared = _usage(
        provider="claude", window="5h", used=0.0, resets_at=None, fetched_at=now.isoformat()
    )
    first = _step(rule, state, now, cleared)
    assert not first.fire
    second = _step(rule, state, now + timedelta(minutes=1), cleared)
    assert second.fire
    assert "resets_at cleared" in second.reason


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
    first = _step(rule, state, _utc(2026, 6, 3, 9, 0), _usage(used=5.0))
    assert not first.fire
    second = _step(rule, state, _utc(2026, 6, 3, 9, 1), _usage(used=5.0))
    assert second.fire


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
