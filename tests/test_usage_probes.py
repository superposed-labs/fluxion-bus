from __future__ import annotations

import json
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest

from fluxion.usage import service as service_mod
from fluxion.usage.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ProviderUsage,
    UsageWindow,
)
from fluxion.usage.probes import (
    AntigravityUsageProbe,
    ClaudeUsageProbe,
    CodexAccountUsageProbe,
    CodexUsageProbe,
    ProbeConfig,
    _normalize_percent,
    _normalize_reset,
)
from fluxion.usage.service import UsageService


def _epoch_to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


# ── _normalize_percent ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 1.0),  # regression: a literal 1 means 1%, NOT 100%
        (0, 0.0),
        (25, 25.0),
        (100, 100.0),
        (46.5, 46.5),
        (150, 100.0),  # clamp high
        (-5, 0.0),  # clamp low
    ],
)
def test_normalize_percent_treats_value_as_percentage(value, expected):
    assert _normalize_percent(value) == expected


@pytest.mark.parametrize("value", [None, True, False, "42", "", object()])
def test_normalize_percent_rejects_non_numbers(value):
    assert _normalize_percent(value) is None


# ── _normalize_reset ───────────────────────────────────────────────
def test_normalize_reset_passthrough_iso():
    iso = "2026-06-02T10:00:00+00:00"
    assert _normalize_reset(iso) == iso


def test_normalize_reset_epoch_to_iso():
    out = _normalize_reset(1780313469)
    assert isinstance(out, str) and out == _epoch_to_iso(1780313469)


@pytest.mark.parametrize("value", [None, True, "", "   "])
def test_normalize_reset_rejects_unusable(value):
    assert _normalize_reset(value) is None


# ── Codex live probe (mocked HTTP) ─────────────────────────────────
_LIVE_PAYLOAD = {
    "plan_type": "plus",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 1,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 900,
            "reset_at": 1780379072,
        },
        "secondary_window": {
            "used_percent": 25,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 467000,
            "reset_at": 1780845710,
        },
    },
}


def test_codex_live_maps_payload(monkeypatch):
    captured_calls = []

    def fake_get(self, url, headers):
        captured_calls.append((url, headers))
        return _LIVE_PAYLOAD

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "plus"
    assert usage.detail == "live"
    assert usage.limit_reached is False
    assert any(call[0] == "https://chatgpt.com/backend-api/wham/usage" for call in captured_calls)
    assert any(
        call[0] == "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
        for call in captured_calls
    )

    # Check headers for first call
    usage_call = next(c for c in captured_calls if "wham/usage" in c[0])
    assert usage_call[1]["Authorization"] == "Bearer tok"
    assert usage_call[1]["ChatGPT-Account-Id"] == "acc-1"

    by_key = {w.key: w for w in usage.windows}
    assert by_key["5h"].used_percent == 1.0  # regression guard
    assert by_key["5h"].window_minutes == 300
    assert by_key["5h"].resets_at == _epoch_to_iso(1780379072)
    assert by_key["7d"].used_percent == 25.0
    assert by_key["7d"].window_minutes == 10080


def test_codex_live_preserves_authoritative_limit_state(monkeypatch):
    payload = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": False,
            "limit_reached": True,
            "primary_window": {
                "used_percent": 100,
                "limit_window_seconds": 18000,
                "reset_at": 1780379072,
            },
        },
    }
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", None))
    monkeypatch.setattr(
        CodexUsageProbe,
        "_http_get_json",
        lambda self, url, headers: {} if "rate-limit-reset-credits" in url else payload,
    )

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.limit_reached is True
    assert usage.detail == "live · limit reached"


def test_codex_live_derives_limit_state_from_allowed_when_needed(monkeypatch):
    payload = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": True,
            "primary_window": {
                "used_percent": 100,
                "limit_window_seconds": 18000,
                "reset_at": 1780379072,
            },
        },
    }
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", None))
    monkeypatch.setattr(
        CodexUsageProbe,
        "_http_get_json",
        lambda self, url, headers: {} if "rate-limit-reset-credits" in url else payload,
    )

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.limit_reached is False


def test_codex_live_maps_weekly_primary_by_duration(monkeypatch):
    payload = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 1,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 580301,
                "reset_at": 1784487578,
            },
            "secondary_window": None,
        },
    }

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(
        CodexUsageProbe,
        "_http_get_json",
        lambda self, url, headers: {} if "rate-limit-reset-credits" in url else payload,
    )

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.status == STATUS_OK
    assert len(usage.windows) == 1
    window = usage.windows[0]
    assert window.key == "7d"
    assert window.label == "Weekly"
    assert window.used_percent == 1.0
    assert window.window_minutes == 10080
    assert window.resets_at == _epoch_to_iso(1784487578)


def test_codex_live_maps_credits(monkeypatch):
    payload = dict(_LIVE_PAYLOAD)
    payload["credits"] = {
        "has_credits": True,
        "balance": "50.0",
        "unlimited": False,
        "overage_limit_reached": False,
    }

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", None))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", lambda self, url, headers: payload)

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()
    assert usage.status == STATUS_OK
    by_key = {w.key: w for w in usage.windows}
    assert by_key["5h"].used_percent == 1.0
    assert by_key["7d"].used_percent == 25.0
    assert by_key["ai_credits"].remaining == 50.0


def test_codex_live_uses_custom_base_url(monkeypatch):
    captured_urls = []
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", None))
    monkeypatch.setattr(
        CodexUsageProbe,
        "_http_get_json",
        lambda self, url, headers: (
            captured_urls.append(url)
            or ({} if "rate-limit-reset-credits" in url else _LIVE_PAYLOAD)
        ),
    )
    cfg = ProbeConfig(codex_usage_mode="live", codex_usage_base_url="https://api.example.com")
    CodexUsageProbe(cfg).probe()
    assert "https://api.example.com/wham/usage" in captured_urls
    assert "https://api.example.com/wham/rate-limit-reset-credits" in captured_urls


def test_codex_live_maps_resets(monkeypatch):
    def fake_get(self, url, headers):
        if "rate-limit-reset-credits" in url:
            return {
                "credits": [
                    {
                        "id": "RateLimitResetCredit_1",
                        "status": "available",
                        "expires_at": "2026-07-18T00:31:36.415054Z",
                    }
                ],
                "available_count": 1,
            }
        return _LIVE_PAYLOAD

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()
    assert usage.status == STATUS_OK
    assert usage.resets is not None
    assert usage.resets["count"] == 1
    assert len(usage.resets["expiries"]) == 1


def test_codex_live_records_successful_zero_resets(monkeypatch):
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(
        CodexUsageProbe,
        "_http_get_json",
        lambda self, url, headers: {} if "rate-limit-reset-credits" in url else _LIVE_PAYLOAD,
    )

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.resets == {"count": 0, "expiries": [], "credits": []}
    assert usage.resets_fetch_failed is False


def test_codex_live_marks_reset_credit_failure(monkeypatch, caplog):
    def fake_get(self, url, headers):
        if "rate-limit-reset-credits" in url:
            raise TimeoutError("credits timed out")
        return _LIVE_PAYLOAD

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)
    caplog.set_level("WARNING", logger="fluxion.usage.probes.codex")

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.status == STATUS_OK
    assert usage.resets is None
    assert usage.resets_fetch_failed is True
    assert "Codex reset-credit fetch failed" in caplog.text


def test_codex_live_fetches_quota_and_reset_credits_concurrently(monkeypatch):
    started: set[str] = set()
    started_lock = Lock()
    both_started = Event()

    def fake_get(self, url, headers):
        kind = "resets" if "rate-limit-reset-credits" in url else "usage"
        with started_lock:
            started.add(kind)
            if len(started) == 2:
                both_started.set()
        assert both_started.wait(timeout=1), "Codex requests were started sequentially"
        return {} if kind == "resets" else _LIVE_PAYLOAD

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)

    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()

    assert usage.status == STATUS_OK
    assert started == {"usage", "resets"}


def test_codex_live_does_not_wait_for_slow_reset_credits(monkeypatch):
    reset_release = Event()

    def fake_get(self, url, headers):
        if "rate-limit-reset-credits" in url:
            reset_release.wait(timeout=2)
            return {"available_count": 1, "credits": []}
        return _LIVE_PAYLOAD

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)

    started_at = time.monotonic()
    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()
    elapsed = time.monotonic() - started_at
    reset_release.set()

    assert usage.status == STATUS_OK
    assert usage.resets is None
    assert usage.resets_fetch_failed is True
    assert elapsed < 0.75


def test_codex_live_mode_errors_when_no_token(monkeypatch):
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: None)
    usage = CodexUsageProbe(ProbeConfig(codex_usage_mode="live")).probe()
    assert usage.status in {STATUS_ERROR, STATUS_UNAVAILABLE}
    assert usage.windows == []


def test_codex_account_usage_maps_profile(monkeypatch):
    captured: dict = {}

    def fake_get(self, url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {
            "stats": {
                "lifetime_tokens": 1_000,
                "daily_usage_buckets": [
                    {"start_date": "2026-06-10", "tokens": 400},
                    {"start_date": "2026-06-11T00:00:00Z", "tokens": 600},
                ],
            }
        }

    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: ("tok", "acc-1"))
    monkeypatch.setattr(CodexUsageProbe, "_http_get_json", fake_get)

    usage = CodexAccountUsageProbe(ProbeConfig()).probe()

    assert usage is not None
    assert usage.lifetime_tokens == 1_000
    assert usage.daily_usage_buckets == {"2026-06-10": 400, "2026-06-11": 600}
    assert captured["url"] == "https://chatgpt.com/backend-api/wham/profiles/me"
    assert captured["headers"]["ChatGPT-Account-Id"] == "acc-1"


# ── Codex log fallback ─────────────────────────────────────────────
def _write_rollout(sessions_dir: Path) -> Path:
    day_dir = sessions_dir / "2026" / "06" / "01"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "rollout-2026-06-01T00-00-00-abc.jsonl"
    record = {
        "timestamp": "2026-06-01T10:01:49.434Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {"used_percent": 46, "window_minutes": 300, "resets_at": 1780313469},
                "secondary": {"used_percent": 23, "window_minutes": 10080, "resets_at": 1780845709},
                "plan_type": "plus",
            },
        },
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def test_codex_logs_mode_reads_rollout(tmp_path):
    sessions = tmp_path / "sessions"
    _write_rollout(sessions)
    cfg = ProbeConfig(codex_usage_mode="logs", codex_sessions_dir=sessions)
    usage = CodexUsageProbe(cfg).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "plus"
    assert usage.detail == "from last Codex run"
    assert usage.fetched_at == "2026-06-01T10:01:49.434Z"  # record's own timestamp
    by_key = {w.key: w for w in usage.windows}
    assert by_key["5h"].used_percent == 46.0
    assert by_key["5h"].window_minutes == 300


def test_codex_auto_falls_back_to_logs_when_live_unavailable(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    _write_rollout(sessions)
    monkeypatch.setattr(CodexUsageProbe, "_read_auth", lambda self: None)  # live can't run
    cfg = ProbeConfig(codex_usage_mode="auto", codex_sessions_dir=sessions)
    usage = CodexUsageProbe(cfg).probe()
    assert usage.status == STATUS_OK
    assert "from last Codex run" in usage.detail


def test_codex_logs_mode_unavailable_when_no_data(tmp_path):
    cfg = ProbeConfig(codex_usage_mode="logs", codex_sessions_dir=tmp_path / "empty")
    usage = CodexUsageProbe(cfg).probe()
    assert usage.status == STATUS_UNAVAILABLE


# ── Claude probe (mocked fetch) ────────────────────────────────────
def test_claude_maps_windows(monkeypatch):
    payload = {
        "five_hour": {"utilization": 27.0, "resets_at": "2026-06-02T10:00:00+00:00"},
        "seven_day": {"utilization": 13, "resets_at": "2026-06-03T21:00:00+00:00"},
    }
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)
    cfg = ProbeConfig(claude_usage_token="tok-123")  # short-circuits token resolution
    usage = ClaudeUsageProbe(cfg).probe()

    assert usage.status == STATUS_OK
    by_key = {w.key: w for w in usage.windows}
    assert by_key["5h"].used_percent == 27.0
    assert by_key["5h"].resets_at == "2026-06-02T10:00:00+00:00"
    assert by_key["7d"].used_percent == 13.0


def test_claude_maps_scoped_weekly_limit(monkeypatch):
    payload = {
        "five_hour": {"utilization": 67.0, "resets_at": "2026-07-08T04:30:00+00:00"},
        "seven_day": {"utilization": 98.0, "resets_at": "2026-07-08T21:00:00+00:00"},
        "seven_day_oauth_apps": {"utilization": 5.0, "resets_at": "2026-07-09T12:00:00+00:00"},
        "limits": [
            # Unscoped entries duplicate the top-level windows: skipped.
            {"kind": "session", "group": "session", "percent": 67, "scope": None},
            {"kind": "weekly_all", "group": "weekly", "percent": 98, "scope": None},
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 100,
                "severity": "critical",
                "resets_at": "2026-07-08T21:00:00+00:00",
                "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
                "is_active": True,
            },
        ],
    }
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)
    usage = ClaudeUsageProbe(ProbeConfig(claude_usage_token="tok-123")).probe()

    assert usage.status == STATUS_OK
    # The scoped cap subdivides the weekly window, so it renders right after it.
    assert [w.key for w in usage.windows] == ["5h", "7d", "scoped_fable", "agent_sdk"]
    fable = usage.windows[2]
    assert fable.label == "Fable"
    assert fable.used_percent == 100.0
    assert fable.resets_at == "2026-07-08T21:00:00+00:00"
    assert fable.window_minutes == 10080


def test_claude_ignores_session_scoped_limits(monkeypatch):
    # A hypothetical session-scoped cap must not become a window: the
    # schedulers' 5h heuristics would mistake it for the account 5h window.
    payload = {
        "five_hour": {"utilization": 10.0, "resets_at": "2026-07-08T04:30:00+00:00"},
        "limits": [
            {
                "kind": "session_scoped",
                "group": "session",
                "percent": 50,
                "resets_at": "2026-07-08T04:30:00+00:00",
                "scope": {"model": {"display_name": "Fable"}},
            },
        ],
    }
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)
    usage = ClaudeUsageProbe(ProbeConfig(claude_usage_token="tok-123")).probe()

    assert [w.key for w in usage.windows] == ["5h"]


def test_claude_ignores_extra_usage_spend_cap(monkeypatch):
    payload = {
        "five_hour": {"utilization": 27.0, "resets_at": "2026-06-02T10:00:00+00:00"},
        "seven_day": {"utilization": 13, "resets_at": "2026-06-03T21:00:00+00:00"},
        "seven_day_oauth_apps": {"utilization": 5.0, "resets_at": "2026-06-04T12:00:00+00:00"},
        "extra_usage": {
            "is_enabled": True,
            "monthly_limit": 4000,
            "used_credits": 1000,
            "decimal_places": 2,
        },
    }
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_profile", lambda self, token: {})
    cfg = ProbeConfig(claude_usage_token="tok-123")
    usage = ClaudeUsageProbe(cfg).probe()

    assert usage.status == STATUS_OK
    by_key = {w.key: w for w in usage.windows}
    assert by_key["5h"].used_percent == 27.0
    assert by_key["7d"].used_percent == 13.0
    assert by_key["agent_sdk"].used_percent == 5.0
    assert by_key["agent_sdk"].resets_at == "2026-06-04T12:00:00+00:00"
    assert "ai_credits" not in by_key


def test_claude_fetches_oauth_prepaid_credits_and_caches_org(monkeypatch):
    payload = {
        "five_hour": {"utilization": 27.0, "resets_at": "2026-06-02T10:00:00Z"},
        "seven_day": {"utilization": 13.0, "resets_at": "2026-06-03T21:00:00Z"},
        "extra_usage": {"is_enabled": True},
    }
    calls = {"profile": 0, "credits": 0}

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)

    def fake_profile(self, token):
        calls["profile"] += 1
        return {
            "organization": {"uuid": "org-123"},
            "has_extra_usage_enabled": True,
        }

    def fake_credits(self, token, organization_uuid):
        calls["credits"] += 1
        assert organization_uuid == "org-123"
        return {
            "amount": 10_000,
            "balance_credits": 100,
            "currency": "usd",
            "next_expires_at": "2026-09-19T00:00:00Z",
        }

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_profile", fake_profile)
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_prepaid_credits", fake_credits)
    probe = ClaudeUsageProbe(ProbeConfig(claude_usage_token="tok-123"))

    first = probe.probe()
    second = probe.probe()

    assert first.status == STATUS_OK
    credits = {w.key: w for w in first.windows}["ai_credits"]
    assert credits.label == "Usage Credits"
    assert credits.remaining == 100.0
    assert credits.currency == "USD"
    assert credits.expires_at == "2026-09-19T00:00:00Z"
    assert credits.enabled is True
    assert {w.key: w for w in second.windows}["ai_credits"].remaining == 100.0
    assert calls == {"profile": 1, "credits": 2}


def test_claude_prepaid_credits_prioritizes_amount_over_balance_credits():
    # Conflict case: amount=9689 (minor units -> 96.89) vs truncated balance_credits=96
    window = ClaudeUsageProbe._map_credits_window(
        {"amount": 9689, "balance_credits": 96, "currency": "USD"}, enabled=True
    )
    assert window is not None
    assert window.remaining == 96.89

    # Numeric string for amount
    window_str = ClaudeUsageProbe._map_credits_window(
        {"amount": "9689", "balance_credits": 96}, enabled=True
    )
    assert window_str is not None
    assert window_str.remaining == 96.89

    # Fallback to balance_credits when amount is missing
    window_fallback = ClaudeUsageProbe._map_credits_window(
        {"balance_credits": 96, "currency": "USD"}, enabled=True
    )
    assert window_fallback is not None
    assert window_fallback.remaining == 96.0

    # Bool inputs are rejected
    assert ClaudeUsageProbe._map_credits_window({"amount": True}, enabled=True) is None
    assert ClaudeUsageProbe._map_credits_window({"balance_credits": True}, enabled=True) is None


def test_claude_credits_failure_does_not_break_usage_windows(monkeypatch):
    payload = {
        "five_hour": {"utilization": 10.0, "resets_at": "2026-06-02T10:00:00Z"},
        "extra_usage": {"is_enabled": True},
    }
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_fetch_profile",
        lambda self, token: {"organization": {"uuid": "org-123"}},
    )
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_fetch_prepaid_credits",
        lambda self, token, org: (_ for _ in ()).throw(
            urllib.error.HTTPError("url", 500, "error", None, None)
        ),
    )

    usage = ClaudeUsageProbe(ProbeConfig(claude_usage_token="tok-123")).probe()

    assert usage.status == STATUS_OK
    assert [w.key for w in usage.windows] == ["5h"]


def test_claude_credits_403_refreshes_org_and_retries_once(monkeypatch):
    payload = {
        "five_hour": {"utilization": 10.0, "resets_at": "2026-06-02T10:00:00Z"},
        "extra_usage": {"is_enabled": True},
    }
    calls = {"profile": 0, "credits": 0}
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", lambda self, token: payload)

    def fake_profile(self, token):
        calls["profile"] += 1
        return {"organization": {"uuid": "org-refreshed"}}

    def fake_credits(self, token, org):
        calls["credits"] += 1
        if calls["credits"] == 1:
            raise urllib.error.HTTPError("url", 403, "forbidden", None, None)
        return {"balance_credits": 25, "currency": "USD"}

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_profile", fake_profile)
    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_prepaid_credits", fake_credits)

    usage = ClaudeUsageProbe(ProbeConfig(claude_usage_token="tok-123")).probe()

    assert {w.key: w for w in usage.windows}["ai_credits"].remaining == 25.0
    assert calls == {"profile": 2, "credits": 2}


def test_claude_unavailable_without_token(monkeypatch):
    monkeypatch.setattr(ClaudeUsageProbe, "_resolve_token", lambda self: "")
    usage = ClaudeUsageProbe(ProbeConfig()).probe()
    assert usage.status == STATUS_UNAVAILABLE
    assert usage.windows == []


def test_claude_never_reads_keychain_by_default(monkeypatch):
    called = {"keychain": False}

    def boom(self, service):
        called["keychain"] = True
        return "leaked"

    monkeypatch.setattr(ClaudeUsageProbe, "_token_from_keychain", boom)
    # No env token + a non-existent credentials file → keychain would be the only
    # remaining source, but it must NOT be consulted with the default config.
    cfg = ProbeConfig(claude_credentials_path=Path("/nonexistent/creds.json"))
    usage = ClaudeUsageProbe(cfg).probe()
    assert called["keychain"] is False
    assert usage.status == STATUS_UNAVAILABLE


def test_claude_reads_keychain_only_when_opted_in(monkeypatch):
    import fluxion.usage.probes as probes_mod
    from fluxion.usage.probes.claude import _ClaudeCredential

    monkeypatch.setattr(probes_mod.sys, "platform", "darwin")
    # Mock the method the resolver actually calls (_credential_from_keychain),
    # not _token_from_keychain — otherwise this shells out to `security` and only
    # passes on a macOS dev box that happens to have a real Claude keychain entry.
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_credential_from_keychain",
        lambda self, service: _ClaudeCredential(
            access_token="tok-from-keychain", source="keychain"
        ),
    )
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_fetch",
        lambda self, token: {"five_hour": {"utilization": 10, "resets_at": "2026-06-02T10:00:00Z"}},
    )
    cfg = ProbeConfig(
        claude_credentials_path=Path("/nonexistent/creds.json"), claude_use_keychain=True
    )
    usage = ClaudeUsageProbe(cfg).probe()
    assert usage.status == STATUS_OK


def test_claude_auto_refreshes_file_credential_after_401(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "expired-access",
                    "refreshToken": "refresh-123",
                    "expiresAt": 4_102_444_800_000,
                    "subscriptionType": "pro",
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    credit_tokens: list[str] = []

    def fake_fetch(self, token):
        calls.append(token)
        if token == "expired-access":
            raise urllib.error.HTTPError(
                url="https://api.anthropic.com/api/oauth/usage",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )
        return {
            "five_hour": {"utilization": 10, "resets_at": "2026-06-02T10:00:00Z"},
            "extra_usage": {"is_enabled": True},
        }

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch", fake_fetch)
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_fetch_refreshed_tokens",
        lambda self, refresh: {
            "access_token": "fresh-access",
            "refresh_token": "refresh-456",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        ClaudeUsageProbe, "_organization_uuid_from_local_account", lambda self: "org-123"
    )

    def fake_credits(self, token, org):
        credit_tokens.append(token)
        assert org == "org-123"
        return {"balance_credits": 100, "currency": "USD"}

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_prepaid_credits", fake_credits)
    cfg = ProbeConfig(claude_credentials_path=creds, claude_auto_refresh=True)
    usage = ClaudeUsageProbe(cfg).probe()

    assert usage.status == STATUS_OK
    assert calls == ["expired-access", "fresh-access"]
    assert credit_tokens == ["fresh-access"]
    saved = json.loads(creds.read_text(encoding="utf-8"))
    oauth = saved["claudeAiOauth"]
    assert oauth["accessToken"] == "fresh-access"
    assert oauth["refreshToken"] == "refresh-456"
    assert oauth["expiresAt"] > 1


def test_claude_does_not_refresh_when_auto_refresh_disabled(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    original = {
        "claudeAiOauth": {
            "accessToken": "expired-access",
            "refreshToken": "refresh-123",
            "expiresAt": 1,
        }
    }
    creds.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(
        ClaudeUsageProbe,
        "_fetch",
        lambda self, token: (_ for _ in ()).throw(
            urllib.error.HTTPError("url", 401, "Unauthorized", None, None)
        ),
    )
    called = {"refresh": False}

    def fake_refresh(self, refresh):
        called["refresh"] = True
        return {"access_token": "fresh-access"}

    monkeypatch.setattr(ClaudeUsageProbe, "_fetch_refreshed_tokens", fake_refresh)
    usage = ClaudeUsageProbe(ProbeConfig(claude_credentials_path=creds)).probe()

    assert usage.status == STATUS_ERROR
    assert "HTTP 401" in usage.detail
    assert called["refresh"] is False
    assert json.loads(creds.read_text(encoding="utf-8")) == original


# ── Antigravity probe (mocked sidecar) ─────────────────────────────
_ANTIGRAVITY_STATUS = {
    "userStatus": {
        "name": "X",
        "email": "e@example.com",
        "planStatus": {"planInfo": {"planName": "Pro"}},
        "userTier": {"availableCredits": [{"creditType": "GOOGLE_ONE_AI", "creditAmount": 1000}]},
        "cascadeModelConfigData": {
            "clientModelConfigs": [
                {
                    "label": "Gemini 3.1 Pro (High)",
                    "quotaInfo": {"remainingFraction": 1, "resetTime": "2026-06-02T11:08:52Z"},
                },
                {
                    "label": "Claude Opus 4.6 (Thinking)",
                    "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2026-06-02T11:08:52Z"},
                },
                {  # not in the default shortlist → excluded
                    "label": "GPT-OSS 120B (Medium)",
                    "quotaInfo": {"remainingFraction": 1, "resetTime": "2026-06-02T11:08:52Z"},
                },
            ]
        },
    }
}


def test_antigravity_maps_credits_and_grouped_models(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))
    monkeypatch.setattr(
        AntigravityUsageProbe, "_get_user_status", lambda self, port, csrf: _ANTIGRAVITY_STATUS
    )
    cfg = ProbeConfig()
    usage = AntigravityUsageProbe(cfg).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "Pro"
    by_key = {w.key: w for w in usage.windows}

    assert by_key["ai_credits"].remaining == 1000.0

    # We should have exactly the two grouped models
    assert by_key["Gemini"].used_percent == 0.0
    assert by_key["Gemini"].resets_at == "2026-06-02T11:08:52Z"

    assert by_key["External Models"].used_percent == 50.0
    assert by_key["External Models"].resets_at == "2026-06-02T11:08:52Z"

    # Individual names should be absent
    assert "Gemini 3.1 Pro (High)" not in by_key
    assert "Claude Opus 4.6 (Thinking)" not in by_key


def test_antigravity_sidecar_quota_summary_adds_weekly_windows(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))
    monkeypatch.setattr(
        AntigravityUsageProbe, "_get_user_status", lambda self, port, csrf: _ANTIGRAVITY_STATUS
    )
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_get_quota_summary",
        lambda self, port, csrf: {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "window": "weekly",
                            "remainingFraction": 0.75,
                            "resetTime": "2026-06-20T02:14:13Z",
                        },
                        {
                            "bucketId": "gemini-5h",
                            "window": "5h",
                            "remainingFraction": 0.5,
                            "resetTime": "2026-06-13T07:14:13Z",
                        },
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {
                            "bucketId": "3p-weekly",
                            "window": "weekly",
                            "remainingFraction": 0.9,
                            "resetTime": "2026-06-19T13:18:34Z",
                        },
                        {
                            "bucketId": "3p-5h",
                            "window": "5h",
                            "remainingFraction": 1,
                            "resetTime": "2026-06-13T09:21:57Z",
                        },
                    ],
                },
            ]
        },
    )

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    assert usage.source == "sidecar"
    by_key = {window.key: window for window in usage.windows}
    assert by_key["ai_credits"].remaining == 1000.0
    assert by_key["Gemini (5h)"].used_percent == 50.0
    assert by_key["Gemini (Weekly)"].used_percent == 25.0
    assert by_key["External Models (5h)"].used_percent == 0.0
    assert by_key["External Models (Weekly)"].used_percent == 10.0
    assert "Gemini" not in by_key
    assert "External Models" not in by_key


def test_antigravity_old_sidecar_without_quota_summary_still_works(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))
    monkeypatch.setattr(
        AntigravityUsageProbe, "_get_user_status", lambda self, port, csrf: _ANTIGRAVITY_STATUS
    )
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_get_quota_summary",
        lambda self, port, csrf: (_ for _ in ()).throw(
            urllib.error.HTTPError("", 404, "", {}, None)
        ),
    )

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    by_key = {window.key: window for window in usage.windows}
    assert "Gemini" in by_key
    assert "External Models" in by_key
    assert not any("Weekly" in key for key in by_key)


def test_antigravity_grouping_treats_reset_only_gemini_as_exhausted(monkeypatch):
    payload = json.loads(json.dumps(_ANTIGRAVITY_STATUS))
    configs = payload["userStatus"]["cascadeModelConfigData"]["clientModelConfigs"]
    configs[0]["quotaInfo"] = {"resetTime": "2026-06-02T11:08:52Z"}

    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))
    monkeypatch.setattr(AntigravityUsageProbe, "_get_user_status", lambda self, port, csrf: payload)

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    by_key = {w.key: w for w in usage.windows}
    assert by_key["Gemini"].used_percent == 100.0
    assert by_key["Gemini"].resets_at == "2026-06-02T11:08:52Z"
    assert by_key["External Models"].used_percent == 50.0


def test_antigravity_unavailable_when_sidecar_missing(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: None)
    config = ProbeConfig(antigravity_sidecar_path=Path("/nonexistent"))
    usage = AntigravityUsageProbe(config).probe()
    assert usage.status == STATUS_UNAVAILABLE
    assert "not running" in usage.detail


def test_antigravity_spawns_sidecar_on_demand_and_cleans_up(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: None)

    # Mock existence of the binary
    mock_binary = Path("/mock/sidecar/binary")
    original_exists = Path.exists
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: True if str(self) == "/mock/sidecar/binary" else original_exists(self),
    )
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: True if str(self) == "/mock/sidecar/binary" else original_is_file(self),
    )

    spawned = []
    terminated = []

    def mock_spawn_sidecar(self, binary):
        spawned.append(binary)
        return ("mock_csrf", [50222]), "mock_proc_obj"

    def mock_get_user_status(self, port, csrf):
        assert port == 50222
        assert csrf == "mock_csrf"
        return {
            "userStatus": {
                "planStatus": {"planInfo": {"planName": "Google AI Pro"}},
                "userTier": {
                    "availableCredits": [{"creditType": "GOOGLE_ONE_AI", "creditAmount": "100"}]
                },
            }
        }

    def mock_terminate_process_instance(self, proc):
        assert proc == "mock_proc_obj"
        terminated.append(proc)

    monkeypatch.setattr(AntigravityUsageProbe, "_spawn_sidecar", mock_spawn_sidecar)
    monkeypatch.setattr(AntigravityUsageProbe, "_get_user_status", mock_get_user_status)
    monkeypatch.setattr(
        AntigravityUsageProbe, "_terminate_process_instance", mock_terminate_process_instance
    )

    config = ProbeConfig(antigravity_sidecar_path=mock_binary)
    usage = AntigravityUsageProbe(config).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "Google AI Pro"
    assert len(spawned) == 1
    assert len(terminated) == 1


def test_antigravity_cloud_api_success(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_read_active_token", lambda self: "mock_token")

    def mock_query_cloud_api(self, token):
        assert token == "mock_token"
        summary = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly Limit",
                            "window": "weekly",
                            "resetTime": "2026-06-10T14:39:21Z",
                            "remainingFraction": 0.5,
                        }
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {
                            "bucketId": "3p-5h",
                            "displayName": "Five Hour Limit",
                            "window": "5h",
                            "resetTime": "2026-06-06T18:45:05Z",
                            "remainingFraction": 0.9,
                        }
                    ],
                },
            ]
        }
        assist = {
            "paidTier": {
                "id": "g1-pro-tier",
                "name": "Google AI Pro",
            }
        }
        return summary, assist

    monkeypatch.setattr(AntigravityUsageProbe, "_query_cloud_api", mock_query_cloud_api)

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "Google AI Pro"
    assert usage.source == "cloud"
    by_key = {w.key: w for w in usage.windows}

    assert "ai_credits" not in by_key
    assert by_key["Gemini (Weekly)"].used_percent == 50.0
    assert by_key["Gemini (Weekly)"].resets_at == "2026-06-10T14:39:21Z"
    assert by_key["External Models (5h)"].used_percent == 10.0
    assert by_key["External Models (5h)"].resets_at == "2026-06-06T18:45:05Z"


def test_antigravity_cloud_api_dedupes_starter_repeated_buckets(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_read_active_token", lambda self: "mock_token")

    duplicate_reset = "2026-07-12T08:01:46Z"
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_query_cloud_api",
        lambda self, token: (
            {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": f"gemini-5h-{idx}",
                                "description": "Quota resets in 6 days, 23 hours.",
                                "resetTime": duplicate_reset,
                                "remainingFraction": 1,
                            }
                            for idx in range(5)
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": f"3p-5h-{idx}",
                                "description": "Quota resets in 6 days, 23 hours.",
                                "resetTime": duplicate_reset,
                                "remainingFraction": 1,
                            }
                            for idx in range(3)
                        ],
                    },
                ]
            },
            {"paidTier": {"name": "Antigravity Starter Quota"}},
        ),
    )

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "Antigravity Starter Quota"
    assert usage.source == "cloud"
    assert [(w.key, w.used_percent, w.resets_at) for w in usage.windows] == [
        ("Gemini (Weekly)", 0.0, duplicate_reset),
        ("External Models (Weekly)", 0.0, duplicate_reset),
    ]
    assert [w.window_minutes for w in usage.windows] == [10080, 10080]


def test_antigravity_cloud_api_supports_legacy_top_level_buckets(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_read_active_token", lambda self: "mock_token")
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_query_cloud_api",
        lambda self, token: (
            {
                "buckets": [
                    {
                        "bucketId": "gemini-5h",
                        "window": "5h",
                        "resetTime": "2026-06-10T14:39:21Z",
                        "remainingFraction": 0.75,
                    }
                ]
            },
            {},
        ),
    )

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    assert usage.status == STATUS_OK
    assert usage.windows[0].key == "Gemini (5h)"
    assert usage.windows[0].used_percent == 25.0


def test_antigravity_cloud_api_without_quota_buckets_falls_back_to_sidecar(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_read_active_token", lambda self: "mock_token")
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_query_cloud_api",
        lambda self, token: (
            {"groups": []},
            {"paidTier": {"id": "g1-pro-tier", "name": "Google AI Pro"}},
        ),
    )
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))
    monkeypatch.setattr(
        AntigravityUsageProbe,
        "_get_user_status",
        lambda self, port, csrf: {
            "userStatus": {
                "planStatus": {"planInfo": {"planName": "Google AI Pro"}},
                "userTier": {
                    "availableCredits": [{"creditType": "GOOGLE_ONE_AI", "creditAmount": "500"}]
                },
            }
        },
    )

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    assert usage.status == STATUS_OK
    assert usage.account_label == "Google AI Pro"
    assert usage.source == "sidecar"
    assert usage.source_reason == "cloud response contained no recognized quota buckets"
    by_key = {w.key: w for w in usage.windows}
    assert by_key["ai_credits"].remaining == 500.0


def test_antigravity_cloud_api_401_fallback_to_sidecar(monkeypatch):
    monkeypatch.setattr(AntigravityUsageProbe, "_read_active_token", lambda self: "mock_token")

    def mock_query_cloud_api_401(self, token):
        # Raise 401 Unauthorized HTTPError
        fp = None
        raise urllib.error.HTTPError("https://mock", 401, "Unauthorized", {}, fp)

    monkeypatch.setattr(AntigravityUsageProbe, "_query_cloud_api", mock_query_cloud_api_401)
    monkeypatch.setattr(AntigravityUsageProbe, "_discover", lambda self: ("csrf", [1234]))

    def mock_get_user_status(self, port, csrf):
        return {
            "userStatus": {
                "planStatus": {"planInfo": {"planName": "Google AI Pro"}},
                "userTier": {
                    "availableCredits": [{"creditType": "GOOGLE_ONE_AI", "creditAmount": "500"}]
                },
            }
        }

    monkeypatch.setattr(AntigravityUsageProbe, "_get_user_status", mock_get_user_status)

    usage = AntigravityUsageProbe(ProbeConfig()).probe()

    # Verify we fell back to the sidecar response
    assert usage.status == STATUS_OK
    assert usage.account_label == "Google AI Pro"
    assert usage.source == "sidecar"
    assert usage.source_reason == "cloud HTTP 401"
    by_key = {w.key: w for w in usage.windows}
    assert by_key["ai_credits"].remaining == 500.0


# ── UsageService TTL cache + last-good fallback ────────────────────
class _ScriptedProbe:
    def __init__(self, provider: str, results: list[ProviderUsage], counter: dict) -> None:
        self._provider = provider
        self._results = results
        self._counter = counter
        self._i = 0

    def provider(self) -> str:
        return self._provider

    def probe(self) -> ProviderUsage:
        self._counter["n"] += 1
        result = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return result


def _ok(provider: str) -> ProviderUsage:
    return ProviderUsage(
        provider=provider,
        status=STATUS_OK,
        windows=[UsageWindow(key="5h", label="5-hour", used_percent=10.0)],
    )


def _err(provider: str) -> ProviderUsage:
    return ProviderUsage(provider=provider, status=STATUS_ERROR, detail="boom")


def test_usage_service_caches_within_ttl(monkeypatch):
    counter = {"n": 0}
    probe = _ScriptedProbe("codex", [_ok("codex")], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(providers=["codex"], probe_config=ProbeConfig(), refresh_sec=60)
    svc.get_usage()
    svc.get_usage()
    assert counter["n"] == 1  # second call served from cache

    svc.get_usage(force=True)
    assert counter["n"] == 2  # force bypasses the cache


def test_usage_service_serves_last_good_on_error(monkeypatch):
    counter = {"n": 0}
    probe = _ScriptedProbe("codex", [_ok("codex"), _err("codex")], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(providers=["codex"], probe_config=ProbeConfig(), refresh_sec=60)
    first = svc.get_usage(force=True)
    assert first[0].status == STATUS_OK

    second = svc.get_usage(force=True)  # probe now errors
    assert second[0].status == STATUS_OK  # last-good snapshot retained


def test_usage_service_retains_codex_resets_only_when_secondary_fetch_failed(monkeypatch):
    counter = {"n": 0}
    initial = _ok("codex")
    initial.resets = {"count": 2, "expiries": [], "credits": []}
    partial = ProviderUsage(
        provider="codex",
        status=STATUS_OK,
        windows=[UsageWindow(key="5h", label="5-hour", used_percent=20.0)],
        resets_fetch_failed=True,
    )
    confirmed_zero = ProviderUsage(
        provider="codex",
        status=STATUS_OK,
        windows=[UsageWindow(key="5h", label="5-hour", used_percent=30.0)],
        resets={"count": 0, "expiries": [], "credits": []},
    )
    probe = _ScriptedProbe("codex", [initial, partial, confirmed_zero], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(providers=["codex"], probe_config=ProbeConfig(), refresh_sec=60)
    assert svc.get_usage(force=True)[0].resets["count"] == 2

    after_failure = svc.get_usage(force=True)[0]
    assert after_failure.windows[0].used_percent == 20.0
    assert after_failure.resets["count"] == 2

    after_success = svc.get_usage(force=True)[0]
    assert after_success.windows[0].used_percent == 30.0
    assert after_success.resets["count"] == 0


def test_usage_service_restores_resets_before_secondary_fetch_failure(monkeypatch, tmp_path):
    cache_file = tmp_path / "usage_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "providers": [
                    {
                        "provider": "codex",
                        "status": STATUS_OK,
                        "account_label": "Plus",
                        "windows": [
                            {
                                "key": "5h",
                                "label": "5-hour",
                                "used_percent": 10.0,
                            }
                        ],
                        "resets": {"count": 2, "expiries": [], "credits": []},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    partial = ProviderUsage(
        provider="codex",
        status=STATUS_OK,
        windows=[UsageWindow(key="5h", label="5-hour", used_percent=20.0)],
        resets_fetch_failed=True,
    )
    counter = {"n": 0}
    probe = _ScriptedProbe("codex", [partial], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(
        providers=["codex"],
        probe_config=ProbeConfig(),
        refresh_sec=60,
        cache_file=cache_file,
    )
    usage = svc.get_usage(force=True)[0]

    assert usage.windows[0].used_percent == 20.0
    assert usage.resets == {"count": 2, "expiries": [], "credits": []}


def test_usage_service_logs_antigravity_source_changes_and_401_once_per_episode(
    monkeypatch, caplog
):
    sidecar_401 = ProviderUsage(
        provider="antigravity",
        status=STATUS_OK,
        windows=[UsageWindow(key="Gemini", label="Gemini", used_percent=10.0)],
        source="sidecar",
        source_reason="cloud HTTP 401",
    )
    cloud = ProviderUsage(
        provider="antigravity",
        status=STATUS_OK,
        windows=[UsageWindow(key="Gemini (Weekly)", label="Gemini Weekly", used_percent=5.0)],
        source="cloud",
    )
    counter = {"n": 0}
    probe = _ScriptedProbe(
        "antigravity",
        [sidecar_401, sidecar_401, cloud, sidecar_401],
        counter,
    )
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)
    caplog.set_level("INFO", logger="fluxion.usage.service")

    svc = UsageService(providers=["antigravity"], probe_config=ProbeConfig(), refresh_sec=60)
    for _ in range(4):
        svc.get_usage(force=True)

    messages = [record.getMessage() for record in caplog.records]
    assert (
        messages.count(
            "Antigravity cloud quota API returned HTTP 401; falling back to local sidecar."
        )
        == 2
    )
    assert "Antigravity quota source selected: sidecar" in messages
    assert "Antigravity quota source changed: sidecar -> cloud" in messages
    assert "Antigravity quota source changed: cloud -> sidecar" in messages


def test_usage_service_per_provider_backoff(monkeypatch):
    counter = {"codex": 0, "claude": 0}

    class ScriptedMultiProbe:
        def __init__(self, provider: str) -> None:
            self._provider = provider

        def provider(self) -> str:
            return self._provider

        def probe(self) -> ProviderUsage:
            counter[self._provider] += 1
            if self._provider == "claude":
                return _err("claude")
            return _ok("codex")

    probes = {"codex": ScriptedMultiProbe("codex"), "claude": ScriptedMultiProbe("claude")}
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probes.get(provider))

    svc = UsageService(providers=["codex", "claude"], probe_config=ProbeConfig(), refresh_sec=1)

    # First query (polls both)
    svc.get_usage()
    assert counter["codex"] == 1
    assert counter["claude"] == 1

    now = 100.0
    monkeypatch.setattr(service_mod.time, "monotonic", lambda: now)

    # Setup explicit next probe times
    svc._next_probe_at["codex"] = 101.0
    svc._next_probe_at["claude"] = 102.0
    svc._current_intervals["codex"] = 1.0
    svc._current_intervals["claude"] = 2.0

    # Test time = 101.5 (only codex should be polled)
    now = 101.5
    svc.get_usage()
    assert counter["codex"] == 2
    assert counter["claude"] == 1

    # Test time = 102.5 (claude should be polled. since it fails, interval doubles to 4.0)
    now = 102.5
    svc.get_usage()
    assert counter["codex"] == 3
    assert counter["claude"] == 2

    assert svc._current_intervals["claude"] == 4.0


def test_usage_service_persistent_cache_and_claude_limit(tmp_path, monkeypatch):
    cache_file = tmp_path / "usage_cache.json"
    now_iso = datetime.now(UTC).isoformat()
    cache_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "providers": [
                    {
                        "provider": "claude",
                        "status": "ok",
                        "account_label": "pro",
                        "windows": [
                            {
                                "key": "5h",
                                "label": "5-hour",
                                "used_percent": 10.0,
                                "resets_at": None,
                            }
                        ],
                        "fetched_at": now_iso,
                        "detail": "",
                    },
                    {
                        "provider": "codex",
                        "status": "ok",
                        "account_label": "plus",
                        "windows": [],
                        "fetched_at": now_iso,
                        "detail": "",
                    },
                ],
                "generated_at": now_iso,
            }
        ),
        encoding="utf-8",
    )

    counter = {"claude": 0, "codex": 0}

    class MockProbe:
        def __init__(self, provider):
            self._provider = provider

        def provider(self):
            return self._provider

        def probe(self):
            counter[self._provider] += 1
            return _ok(self._provider)

    probes = {"claude": MockProbe("claude"), "codex": MockProbe("codex")}
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probes.get(provider))

    # Initializing UsageService with the cache file. Refresh interval set to 90 seconds.
    svc = UsageService(
        providers=["claude", "codex"],
        probe_config=ProbeConfig(),
        refresh_sec=90,
        cache_file=cache_file,
    )

    # 1. Verify cache loaded successfully
    assert "claude" in svc._cached_usages
    assert svc._cached_usages["claude"].account_label == "pro"
    assert "codex" in svc._cached_usages

    # 2. Check next probe times initialized from cached fetched_at (which is now)
    # Claude interval should be max(CLAUDE_MIN_REFRESH_SEC, 90.0)
    # Codex interval should be 90.0
    assert svc._current_intervals["claude"] == service_mod.CLAUDE_MIN_REFRESH_SEC
    assert svc._current_intervals["codex"] == 90.0

    # 3. Simulate calling get_usage immediately (both should be skipped because now < next_probe_at)
    res = svc.get_usage()
    assert counter["claude"] == 0
    assert counter["codex"] == 0
    assert len(res) == 2

    # 4. Simulate time moving forward by 100 seconds (Codex should be queried, Claude skipped)
    mon = 1000.0
    monkeypatch.setattr(service_mod.time, "monotonic", lambda: mon)
    svc._next_probe_at["codex"] = mon - 5.0  # expired
    svc._next_probe_at["claude"] = mon + 800.0  # still far in future

    res = svc.get_usage()
    assert counter["codex"] == 1
    assert counter["claude"] == 0


def test_usage_service_seeds_reenabled_provider_from_retired_snapshot(tmp_path, monkeypatch):
    # A provider that was disabled (its snapshot parked under _retired_providers)
    # and is now enabled again starts from that snapshot: it's served as cached
    # data and registered as last-good, so an immediate probe failure falls
    # back to it instead of surfacing an error.
    cache_file = tmp_path / "usage_cache.json"
    now_iso = datetime.now(UTC).isoformat()
    cache_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "providers": [],
                "_retired_providers": [
                    {
                        "provider": "codex",
                        "status": "ok",
                        "account_label": "plus",
                        "windows": [
                            {
                                "key": "5h",
                                "label": "5-hour",
                                "used_percent": 40.0,
                                "resets_at": None,
                            }
                        ],
                        "fetched_at": now_iso,
                        "detail": "",
                    }
                ],
                "generated_at": now_iso,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        service_mod,
        "build_probe",
        lambda provider, config: object() if provider == "codex" else None,
    )

    svc = UsageService(
        providers=["codex"],
        probe_config=ProbeConfig(),
        refresh_sec=90,
        cache_file=cache_file,
    )

    assert svc._cached_usages["codex"].account_label == "plus"
    assert svc._last_good["codex"].windows[0].used_percent == 40.0


def test_usage_service_restores_cross_process_backoff(tmp_path, monkeypatch):
    cache_file = tmp_path / "usage_cache.json"
    now_iso = datetime.now(UTC).isoformat()
    next_allowed = (datetime.now(UTC) + timedelta(minutes=4)).isoformat()
    cache_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "providers": [
                    {
                        "provider": "claude",
                        "status": "ok",
                        "account_label": "pro",
                        "windows": [],
                        "fetched_at": now_iso,
                        "detail": "",
                    }
                ],
                "_collector_state": {
                    "claude": {
                        "interval_sec": 360.0,
                        "next_allowed_at": next_allowed,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    counter = {"n": 0}
    probe = _ScriptedProbe("claude", [_ok("claude")], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(
        providers=["claude"],
        probe_config=ProbeConfig(),
        refresh_sec=60,
        cache_file=cache_file,
    )
    svc.get_usage()

    assert counter["n"] == 0
    assert svc._current_intervals["claude"] == 360.0


def test_usage_service_auth_error_no_fallback(monkeypatch):
    counter = {"n": 0}

    # 1. First returns successful usage
    ok_usage = _ok("claude")
    ok_usage.fetched_at = datetime.now(UTC).isoformat()

    # 2. Second returns 401 auth error
    auth_error = ProviderUsage(
        provider="claude",
        status=STATUS_ERROR,
        detail="usage endpoint returned HTTP 401 Invalid credentials",
    )

    probe = _ScriptedProbe("claude", [ok_usage, auth_error], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(providers=["claude"], probe_config=ProbeConfig(), refresh_sec=60)

    first = svc.get_usage(force=True)
    assert first[0].status == STATUS_OK

    second = svc.get_usage(force=True)
    # Auth error should not be covered by last-good fallback
    assert second[0].status == STATUS_ERROR
    assert "401" in second[0].detail


def test_usage_service_stale_cache_no_fallback(monkeypatch):
    counter = {"n": 0}

    # 1. First returns successful usage but with old timestamp (e.g. 40 minutes ago)
    old_time = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
    ok_usage = ProviderUsage(
        provider="claude",
        status=STATUS_OK,
        windows=[UsageWindow(key="5h", label="5-hour", used_percent=10.0)],
        fetched_at=old_time,
    )

    # 2. Second returns a normal network error
    network_error = ProviderUsage(
        provider="claude",
        status=STATUS_ERROR,
        detail="connection reset",
    )

    probe = _ScriptedProbe("claude", [ok_usage, network_error], counter)
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probe)

    svc = UsageService(providers=["claude"], probe_config=ProbeConfig(), refresh_sec=60)

    first = svc.get_usage(force=True)
    assert first[0].status == STATUS_OK

    second = svc.get_usage(force=True)
    # Stale cache (40 mins old) should not be used as fallback
    assert second[0].status == STATUS_ERROR
    assert second[0].detail == "connection reset"


def test_usage_service_force_providers(monkeypatch):
    counter = {"claude": 0, "codex": 0}

    class MockProbe:
        def __init__(self, provider):
            self._provider = provider

        def provider(self):
            return self._provider

        def probe(self):
            counter[self._provider] += 1
            return _ok(self._provider)

    probes = {"claude": MockProbe("claude"), "codex": MockProbe("codex")}
    monkeypatch.setattr(service_mod, "build_probe", lambda provider, config: probes.get(provider))

    svc = UsageService(providers=["claude", "codex"], probe_config=ProbeConfig(), refresh_sec=60)

    mon = 1000.0
    monkeypatch.setattr(service_mod.time, "monotonic", lambda: mon)
    svc._next_probe_at["claude"] = mon + 5000.0
    svc._next_probe_at["codex"] = mon + 5000.0
    svc._cached_usages["claude"] = _ok("claude")
    svc._cached_usages["codex"] = _ok("codex")

    svc.get_usage(force_providers={"claude"})
    assert counter["claude"] == 1
    assert counter["codex"] == 0
