from __future__ import annotations

from fluxion.usage.plan_prices import plan_monthly_for


def test_resolves_known_plans_case_insensitively():
    assert plan_monthly_for("claude", "pro") == 20.0
    assert plan_monthly_for("claude", "Max-20x") == 200.0
    assert plan_monthly_for("codex", "plus") == 20.0
    assert plan_monthly_for("codex", "pro") == 200.0  # same label, different provider price
    assert plan_monthly_for("antigravity", "Google AI Pro") == 20.0


def test_unknown_or_missing_label_returns_none():
    assert plan_monthly_for("claude", "enterprise-custom") is None
    assert plan_monthly_for("claude", "") is None
    assert plan_monthly_for("claude", None) is None
    assert plan_monthly_for("nonsuch", "pro") is None
