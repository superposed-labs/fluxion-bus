from __future__ import annotations

from fluxion.scheduler.engine import FireDecision
from fluxion.scheduler.messages import build_quota_reset_copy
from fluxion.scheduler.models import Action, ScheduleRule, Trigger


def _rule(provider="claude", window_key="5h", name="Claude Monitor (5h)"):
    return ScheduleRule.new(
        name=name,
        trigger=Trigger(type="quota_refresh", provider=provider, window_key=window_key),
        action=Action(type="ping", agent=provider),
    )


def _decision(kind="resets_cleared", data=None, reason="quota_refresh claude/5h (x)"):
    return FireDecision(True, reason, edge_kind=kind, edge_data=data or {})


def test_copy_headline_replaces_raw_reason():
    copy = build_quota_reset_copy(_rule(), _decision(), "en")
    assert "quota_refresh" not in copy.plain
    assert "resets_at" not in copy.plain
    assert "🧡" in copy.plain
    assert "Claude" in copy.plain
    assert "5h quota has reset" in copy.plain
    assert "Rule: Claude Monitor (5h)" in copy.plain


def test_copy_detection_details_are_humanized():
    cleared = build_quota_reset_copy(_rule(), _decision("resets_cleared"), "en")
    assert "the old window expired" in cleared.plain

    advanced = build_quota_reset_copy(_rule(), _decision("reset_advanced"), "en")
    assert "a new usage window started" in advanced.plain

    dropped = build_quota_reset_copy(
        _rule(), _decision("usage_dropped", {"prev_used": 82.4, "cur_used": 3.2}), "en"
    )
    assert "usage fell from 82% to 3%" in dropped.plain


def test_copy_falls_back_to_raw_reason_without_edge_kind():
    copy = build_quota_reset_copy(_rule(), FireDecision(True, "manual trigger"), "en")
    assert "manual trigger" in copy.plain


def test_copy_locales():
    zh = build_quota_reset_copy(_rule(), _decision(), "zh")
    assert "额度已重置" in zh.plain
    assert "旧窗口已到期" in zh.plain

    ja = build_quota_reset_copy(_rule(), _decision(), "ja")
    assert "クォータがリセットされました" in ja.plain

    # Unknown locale falls back to English.
    fallback = build_quota_reset_copy(_rule(), _decision(), "fr")
    assert "quota has reset" in fallback.plain


def test_copy_slack_blocks_shape():
    copy = build_quota_reset_copy(
        _rule(provider="codex", window_key="7d", name="Codex Weekly"),
        _decision("reset_advanced"),
        "en",
    )
    assert len(copy.slack_blocks) == 2
    assert copy.slack_blocks[0]["type"] == "section"
    assert "*Codex*" in copy.slack_blocks[0]["text"]["text"]
    context = copy.slack_blocks[1]
    assert context["type"] == "context"
    assert "Codex Weekly" in context["elements"][0]["text"]
    # Fallback text is a single line for clients without Block Kit.
    assert "\n" not in copy.slack_fallback
    assert "💜" in copy.slack_fallback


def test_copy_unknown_provider_gets_generic_emoji():
    copy = build_quota_reset_copy(
        _rule(provider="openai", window_key="7d", name="OpenAI Weekly"),
        _decision("reset_advanced"),
        "en",
    )
    assert "🤖" in copy.plain
    assert "Openai" in copy.plain
