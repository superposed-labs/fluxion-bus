from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from fluxion.usage import history
from fluxion.usage.history import (
    UsageEntry,
    _antigravity_entry_from_blob,
    _claude_entry_from_line,
    _parse_antigravity_db,
    _parse_codex_file,
    _pick_rate,
    _streaks,
    aggregate,
    collect_antigravity_entries,
    collect_claude_entries,
    collect_codex_entries,
    compute_usage_stats,
)
from fluxion.usage.probes import CodexAccountUsage

UTC = UTC


# Cost *logic* tests run against a fixed in-test rate table, decoupled from the
# shipped model_prices.json (whose real numbers change over time). _rates_for is
# lru-cached, so swap the loader and clear the cache around the test.
@pytest.fixture
def fixed_prices(monkeypatch):
    table = {
        "models": {
            "gpt-5.4": {
                "rates": [
                    {
                        "effective_date": "2025-01-01",
                        "in": 2.5,
                        "out": 15.0,
                        "cw": 2.5,
                        "cr": 0.25,
                        "context_pricing": {
                            "metric": "input_tokens_total",
                            "short_max": 272000,
                            "short": {"in": 2.5, "out": 15.0, "cw": 2.5, "cr": 0.25},
                            "long": {"in": 5.0, "out": 22.5, "cw": 5.0, "cr": 0.5},
                        },
                    }
                ]
            }
        },
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
                {"effective_date": "2025-01-01", "in": 3.0, "out": 15.0, "cw": 3.75, "cr": 0.30}
            ],
            "haiku": [
                {"effective_date": "2025-01-01", "in": 0.80, "out": 4.0, "cw": 1.0, "cr": 0.08}
            ],
            "mini": [
                {"effective_date": "2025-01-01", "in": 0.25, "out": 2.0, "cw": 0.25, "cr": 0.025}
            ],
        },
        "fast": {
            "claude-opus-4-8": {
                "rates": [
                    {
                        "effective_date": "2025-01-01",
                        "in": 30.0,
                        "out": 150.0,
                        "cw": 37.5,
                        "cr": 3.0,
                    }
                ]
            },
        },
        "providers": {
            "codex": [
                {"effective_date": "2025-01-01", "in": 1.25, "out": 10.0, "cw": 1.5, "cr": 0.125}
            ],
            "claude": [
                {"effective_date": "2025-01-01", "in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5}
            ],
        },
    }
    monkeypatch.setattr(history.pricing, "_load_prices", lambda: table)
    history._rates_for.cache_clear()
    yield table
    history._rates_for.cache_clear()


# ── line parsing ────────────────────────────────────────────────────
def _assistant_line(
    *,
    ts: str,
    model: str = "claude-opus-4-8",
    request_id: str = "req-1",
    message_id: str = "msg-1",
    session_id: str = "sess-1",
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_creation: int = 5,
    cache_read: int = 1000,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "requestId": request_id,
            "sessionId": session_id,
            "uuid": f"u-{request_id}",
            "message": {
                "id": message_id,
                "model": model,
                "role": "assistant",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }
    )


def test_parses_assistant_usage_line():
    e = _claude_entry_from_line(_assistant_line(ts="2026-06-10T04:07:25.123Z"))
    assert e is not None
    assert e.provider == "claude"
    assert e.model == "claude-opus-4-8"
    assert e.session_id == "sess-1"
    assert (e.input_tokens, e.output_tokens, e.cache_creation_tokens, e.cache_read_tokens) == (
        100,
        20,
        5,
        1000,
    )
    assert e.total_tokens == 1125
    assert e.dedup_key == "req-1:msg-1"


def test_parses_cache_creation_ttl_breakdown():
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-06-10T04:07:25Z",
            "requestId": "r",
            "sessionId": "s",
            "message": {
                "id": "m",
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cache_creation_input_tokens": 700,
                    "cache_read_input_tokens": 50,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 700,
                        "ephemeral_5m_input_tokens": 0,
                    },
                },
            },
        }
    )
    e = _claude_entry_from_line(line)
    assert e is not None
    assert e.cache_creation_tokens == 700
    assert e.cache_creation_1h_tokens == 700


def test_skips_non_assistant_and_usageless_lines():
    assert (
        _claude_entry_from_line(json.dumps({"type": "user", "message": {"role": "user"}})) is None
    )
    assert (
        _claude_entry_from_line(json.dumps({"type": "assistant", "message": {"id": "x"}})) is None
    )
    assert _claude_entry_from_line("") is None
    assert _claude_entry_from_line("not json") is None


def test_skips_synthetic_placeholder_turns():
    # Claude Code's locally-injected stubs (model "<synthetic>") aren't API calls.
    line = _assistant_line(ts="2026-06-10T04:07:25Z", model="<synthetic>", input_tokens=0)
    assert _claude_entry_from_line(line) is None


# ── dedup ───────────────────────────────────────────────────────────
def _entry(
    ts: str, key: str, *, tokens: int = 100, model: str = "m", session: str = "s"
) -> UsageEntry:
    return UsageEntry(
        provider="claude",
        ts=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        model=model,
        session_id=session,
        input_tokens=tokens,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        dedup_key=key,
    )


def test_aggregate_dedupes_by_key():
    # Same dedup_key (e.g. a forked session copying a prior turn) counts once.
    entries = [
        _entry("2026-06-10T10:00:00Z", "dup", tokens=100),
        _entry("2026-06-10T11:00:00Z", "dup", tokens=100),
        _entry("2026-06-10T12:00:00Z", "uniq", tokens=50),
    ]
    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["messages"] == 2
    assert out["totals"]["total_tokens"] == 150


def test_aggregate_keeps_largest_duplicate_usage_snapshot():
    entries = [
        _entry("2026-06-10T10:00:00Z", "streamed", tokens=100),
        _entry("2026-06-10T10:00:01Z", "streamed", tokens=150),
    ]

    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))

    assert out["totals"]["messages"] == 1
    assert out["totals"]["total_tokens"] == 150


def test_aggregate_dedupes_to_original_timestamp():
    # An exact copy of a past turn (fork/resume replay) carries a rewritten,
    # newer timestamp; the turn actually ran at the original time, so the copy
    # must not resurface it inside a recent window.
    entries = [
        _entry("2026-05-01T10:00:00Z", "copied", tokens=100),
        _entry("2026-06-10T10:00:00Z", "copied", tokens=100),
    ]

    out = aggregate(entries, window="7d", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))

    assert out["totals"]["messages"] == 0
    all_out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert all_out["totals"]["messages"] == 1
    assert all_out["by_day"][0]["date"] == "2026-05-01"


def test_aggregate_totals_and_breakdowns(fixed_prices):
    entries = [
        UsageEntry(
            "claude", datetime(2026, 6, 10, 10, tzinfo=UTC), "opus", "s1", 10, 2, 3, 100, "a"
        ),
        UsageEntry(
            "claude", datetime(2026, 6, 10, 14, tzinfo=UTC), "sonnet", "s2", 20, 4, 1, 200, "b"
        ),
    ]
    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    t = out["totals"]
    assert t["sessions"] == 2
    assert t["input_tokens"] == 30
    assert t["output_tokens"] == 6
    assert t["cache_creation_tokens"] == 4
    assert t["cache_read_tokens"] == 300
    assert t["total_tokens"] == 340
    assert t["generated_tokens"] == 40  # input + output + cache_creation (ex cache read)
    # cache_hit = cache_read / (cache_read + input + cache_write) = 300 / 334
    assert t["cache_hit"] == round(300 / 334, 4)
    assert t["top_model"] == "sonnet"  # 225 > 115
    # by_model carries provider + generated for the redesigned UI.
    assert out["by_model"][0]["provider"] == "claude"
    assert out["by_model"][0]["generated_tokens"] == 25  # sonnet: 20+4+1
    # Cost is per model *family*: "opus" and "sonnet" price differently even
    # though both are claude.
    opus_cost = (10 * 15 + 2 * 75 + 3 * 18.75 + 100 * 1.5) / 1e6
    sonnet_cost = (20 * 3 + 4 * 15 + 1 * 3.75 + 200 * 0.30) / 1e6
    assert t["cost"] == round(opus_cost + sonnet_cost, 2)
    assert t["peak_hour"] in (10, 14)
    # by_hour has 24 buckets; the two active hours carry the messages.
    hours = {row["hour"]: row["messages"] for row in out["by_hour"]}
    assert hours[10] == 1 and hours[14] == 1
    models = {m["model"]: m for m in out["by_model"]}
    assert models["sonnet"]["total_tokens"] == 225
    assert out["by_model"][0]["model"] == "sonnet"  # sorted desc


def test_provider_hour_series_is_trailing_seven_days_and_provider_scoped():
    entries = [
        _entry("2026-06-05T14:00:00Z", "c1"),
        _entry("2026-06-06T14:10:00Z", "c2"),
        _entry("2026-06-07T14:20:00Z", "c3"),
        _entry("2026-06-08T10:00:00Z", "c4"),
        UsageEntry(
            provider="codex",
            ts=datetime(2026, 6, 6, 9, tzinfo=UTC),
            model="gpt",
            session_id="s",
            input_tokens=100,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            dedup_key="x1",
        ),
        UsageEntry(
            provider="codex",
            ts=datetime(2026, 6, 7, 9, tzinfo=UTC),
            model="gpt",
            session_id="s",
            input_tokens=100,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            dedup_key="x2",
        ),
        # Outside the trailing seven local days and therefore excluded.
        _entry("2026-06-03T14:00:00Z", "old"),
    ]

    out = aggregate(entries, window="1d", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    rows = {(row["provider"], row["hour"]): row for row in out["by_provider_hour"]}

    assert rows[("claude", 14)]["messages"] == 3
    assert rows[("claude", 10)]["messages"] == 1
    assert rows[("codex", 9)]["messages"] == 2


def test_cost_resolves_per_model_family(fixed_prices):
    # Same provider, three tiers — each priced from its own family rate, so the
    # cheap model isn't billed at the flagship's rate.
    base = dict(tzinfo=UTC)
    entries = [
        UsageEntry(
            "claude",
            datetime(2026, 6, 10, 10, **base),
            "claude-opus-4-8",
            "s",
            1_000_000,
            0,
            0,
            0,
            "a",
        ),
        UsageEntry(
            "claude",
            datetime(2026, 6, 10, 10, **base),
            "claude-haiku-4-5",
            "s",
            1_000_000,
            0,
            0,
            0,
            "b",
        ),
        UsageEntry(
            "codex", datetime(2026, 6, 10, 10, **base), "gpt-5-mini", "s2", 1_000_000, 0, 0, 0, "c"
        ),
        UsageEntry(
            "codex",
            datetime(2026, 6, 10, 10, **base),
            "gpt-5.3-codex",
            "s2",
            1_000_000,
            0,
            0,
            0,
            "d",
        ),
    ]
    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    cost = {m["model"]: m["cost"] for m in out["by_model"]}
    assert cost["claude-opus-4-8"] == 15.0  # 1M input × $15
    assert cost["claude-haiku-4-5"] == 0.8  # haiku family, not opus
    assert cost["gpt-5-mini"] == 0.25  # mini family
    assert cost["gpt-5.3-codex"] == 1.25  # no family match → codex provider fallback


def test_cache_write_split_by_ttl(fixed_prices):
    # Cache-write tokens are billed by TTL: the 1h portion at cw1h ($30), the
    # rest at the 5m cw ($18.75). A turn with 1M cache-write, 600k of it 1h:
    #   600k×$30 + 400k×$18.75 = $25.50
    e = UsageEntry(
        "claude",
        datetime(2026, 6, 10, 10, tzinfo=UTC),
        "claude-opus-4-8",
        "s",
        0,
        0,
        1_000_000,
        0,
        "a",
        cache_creation_1h_tokens=600_000,
    )
    out = aggregate([e], window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["cost"] == 25.5


def test_cache_write_falls_back_to_cw_without_cw1h(fixed_prices):
    # A family with no cw1h (mini) prices the whole cache-write at cw, even the
    # 1h portion — so providers without a 1h fee are unaffected.
    e = UsageEntry(
        "codex",
        datetime(2026, 6, 10, 10, tzinfo=UTC),
        "gpt-5-mini",
        "s",
        0,
        0,
        1_000_000,
        0,
        "a",
        cache_creation_1h_tokens=1_000_000,
    )
    out = aggregate([e], window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["cost"] == 0.25  # 1M × cw $0.25


def test_fast_mode_prices_at_premium(fixed_prices):
    # A fast-mode Opus turn resolves to the `fast` table ($30/$150), not the
    # standard opus family ($15/$75). A standard turn stays at $15.
    base = dict(tzinfo=UTC)
    fast = UsageEntry(
        "claude",
        datetime(2026, 6, 10, 10, **base),
        "claude-opus-4-8",
        "s",
        1_000_000,
        0,
        0,
        0,
        "a",
        is_fast=True,
    )
    std = UsageEntry(
        "claude",
        datetime(2026, 6, 10, 10, **base),
        "claude-opus-4-8",
        "s",
        1_000_000,
        0,
        0,
        0,
        "b",
        is_fast=False,
    )
    out = aggregate([fast, std], window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["cost"] == 45.0  # 1M×$30 (fast) + 1M×$15 (standard)


def test_fast_mode_falls_back_to_standard_without_fast_rate(fixed_prices):
    # A fast turn on a model with no fast rate (haiku) just uses the standard
    # rate — the fast flag never silently zeroes a cost.
    e = UsageEntry(
        "claude",
        datetime(2026, 6, 10, 10, tzinfo=UTC),
        "claude-haiku-4-5",
        "s",
        1_000_000,
        0,
        0,
        0,
        "a",
        is_fast=True,
    )
    out = aggregate([e], window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["cost"] == 0.8  # haiku family $0.80, unaffected by fast flag


def test_long_context_prices_from_billed_input_total(fixed_prices):
    short = UsageEntry(
        "codex",
        datetime(2026, 6, 10, 10, tzinfo=UTC),
        "gpt-5.4",
        "s",
        100_000,
        0,
        0,
        160_000,
        "a",
        billed_input_tokens_total=260_000,
    )
    long = UsageEntry(
        "codex",
        datetime(2026, 6, 10, 10, tzinfo=UTC),
        "gpt-5.4",
        "s",
        100_000,
        0,
        0,
        180_000,
        "b",
        billed_input_tokens_total=280_000,
    )

    out = aggregate([short, long], window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))

    # short: 100k fresh × $2.5 + 160k cached × $0.25 = $0.29
    # long:  100k fresh × $5.0 + 180k cached × $0.5  = $0.59
    assert out["totals"]["cost"] == 0.88
    assert out["by_model"][0]["model"] == "gpt-5.4"
    assert out["by_model"][0]["cost"] == 0.88


def test_fast_flag_parsed_from_speed():
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-06-10T04:07:25Z",
            "requestId": "r",
            "sessionId": "s",
            "message": {
                "id": "m",
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 10, "output_tokens": 2, "speed": "fast"},
            },
        }
    )
    e = _claude_entry_from_line(line)
    assert e is not None and e.is_fast is True
    # standard / missing speed → not fast
    assert _claude_entry_from_line(_assistant_line(ts="2026-06-10T04:07:25Z")).is_fast is False


def test_uncosted_tokens_surfaced(fixed_prices):
    # A local model with no rate contributes its tokens to uncosted_tokens.
    entries = [
        UsageEntry(
            "ollama", datetime(2026, 6, 10, 10, tzinfo=UTC), "qwen", "s", 100, 50, 0, 0, "a"
        ),
        UsageEntry(
            "claude",
            datetime(2026, 6, 10, 10, tzinfo=UTC),
            "claude-opus-4-8",
            "s",
            1_000_000,
            0,
            0,
            0,
            "b",
        ),
    ]
    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["uncosted_tokens"] == 150  # only the ollama turn
    assert out["totals"]["cost"] == 15.0  # the opus turn (1M × $15) still costs


def test_family_match_respects_word_boundary():
    # Regression: "mini" must not match inside "gemini" (substring collision),
    # but "flash" must still match "gemini-3-flash".
    assert history._family_match("mini", "gpt-5.4-mini") is True
    assert history._family_match("mini", "gemini-3-pro") is False
    assert history._family_match("flash", "gemini-3-flash") is True
    assert history._family_match("flash-lite", "gemini-2.5-flash-lite") is True
    assert history._family_match("opus", "claude-opus-4-8") is True
    assert history._family_match("gemini", "gemini-3-pro") is True


def test_gemini_thinking_level_uses_the_canonical_model_price(fixed_prices):
    fixed_prices["models"].update(
        {
            "gemini-3.5-flash": {
                "rates": [
                    {
                        "effective_date": "2026-05-19",
                        "in": 1.5,
                        "out": 9.0,
                        "cw": 1.5,
                        "cr": 0.15,
                    }
                ]
            }
        }
    )
    fixed_prices["families"]["flash"] = [
        {
            "effective_date": "2026-07-21",
            "in": 1.5,
            "out": 7.5,
            "cw": 1.5,
            "cr": 0.15,
        }
    ]
    history._rates_for.cache_clear()

    high = history._rates_for("antigravity", "Gemini 3.5 Flash (High)", "2026-07-22")
    low = history._rates_for("antigravity", "Gemini 3.5 Flash(Low)", "2026-07-22")
    latest = history._rates_for("antigravity", "gemini-3.6-flash", "2026-07-22")

    assert high is not None and high["out"] == 9.0
    assert low == high
    assert latest is not None and latest["out"] == 7.5


def test_pick_rate_by_effective_date():
    # A model whose price was halved for a promo window, then restored.
    rates = [
        {"effective_date": "2025-01-01", "in": 15.0},
        {"effective_date": "2026-03-01", "in": 7.5},  # promo starts
        {"effective_date": "2026-04-01", "in": 15.0},  # promo ends
    ]
    assert _pick_rate(rates, None)["in"] == 15.0  # latest = current price
    assert _pick_rate(rates, "2026-02-15")["in"] == 15.0  # before promo
    assert _pick_rate(rates, "2026-03-15")["in"] == 7.5  # during promo → half price
    assert _pick_rate(rates, "2026-05-01")["in"] == 15.0  # after promo
    assert _pick_rate(rates, "2024-06-01")["in"] == 15.0  # before earliest → oldest rate
    assert _pick_rate([], "2026-01-01") is None


def test_unknown_provider_costs_zero():
    # A provider with no rate entry (e.g. a local model) contributes no cost.
    entries = [
        UsageEntry(
            "ollama", datetime(2026, 6, 10, 10, tzinfo=UTC), "qwen", "s", 100, 50, 0, 0, "a"
        ),
    ]
    out = aggregate(entries, window="all", tz=UTC, now=datetime(2026, 6, 10, 23, tzinfo=UTC))
    assert out["totals"]["cost"] == 0.0
    assert out["by_model"][0]["cost"] == 0.0


# ── window filtering ────────────────────────────────────────────────
def test_window_filtering_7d_30d_all():
    now = datetime(2026, 6, 10, 12, tzinfo=UTC)
    entries = [
        _entry("2026-06-10T10:00:00Z", "today"),
        _entry("2026-06-05T10:00:00Z", "d5"),  # 5 days ago — inside 7d
        _entry("2026-05-20T10:00:00Z", "d21"),  # 21 days ago — inside 30d only
        _entry("2026-01-01T10:00:00Z", "old"),  # inside all only
    ]
    assert aggregate(entries, window="7d", tz=UTC, now=now)["totals"]["messages"] == 2
    assert aggregate(entries, window="30d", tz=UTC, now=now)["totals"]["messages"] == 3
    assert aggregate(entries, window="all", tz=UTC, now=now)["totals"]["messages"] == 4


# ── streaks ─────────────────────────────────────────────────────────
def test_streaks_current_and_longest():
    today = date(2026, 6, 10)
    active = {
        date(2026, 6, 10),
        date(2026, 6, 9),
        date(2026, 6, 8),  # current run of 3
        date(2026, 6, 1),
        date(2026, 5, 31),
        date(2026, 5, 30),
        date(2026, 5, 29),  # run of 4
    }
    current, longest = _streaks(active, today)
    assert current == 3
    assert longest == 4


def test_streak_lapsed_when_latest_too_old():
    today = date(2026, 6, 10)
    active = {date(2026, 6, 5), date(2026, 6, 4)}  # latest is 5 days ago
    current, longest = _streaks(active, today)
    assert current == 0
    assert longest == 2


def test_streak_counts_yesterday_as_current():
    today = date(2026, 6, 10)
    active = {date(2026, 6, 9), date(2026, 6, 8)}  # ends yesterday
    current, longest = _streaks(active, today)
    assert current == 2


# ── collection + incremental cache ──────────────────────────────────
def test_collect_and_incremental_cache(tmp_path: Path):
    projects = tmp_path / "projects"
    proj = projects / "demo"
    proj.mkdir(parents=True)
    f = proj / "sess-1.jsonl"
    f.write_text(
        "\n".join(
            [
                _assistant_line(ts="2026-06-10T10:00:00Z", request_id="r1", message_id="m1"),
                _assistant_line(ts="2026-06-10T11:00:00Z", request_id="r2", message_id="m2"),
            ]
        ),
        encoding="utf-8",
    )

    cache: dict = {"version": 4, "files": {}}
    entries = collect_claude_entries(projects, cache=cache)
    assert len(entries) == 2
    assert str(f) in cache["files"]["claude"]
    # The fixture has no trailing newline, so the second record is held as the
    # uncommitted tail; both are cached, just in their respective buckets.
    cached_file = cache["files"]["claude"][str(f)]
    assert len(cached_file["entries"]) + len(cached_file.get("tail", [])) == 2

    # Second pass with an unchanged file serves from cache (no re-parse needed).
    entries2 = collect_claude_entries(projects, cache=cache)
    assert len(entries2) == 2

    # A deleted file is dropped from the cache.
    f.unlink()
    entries3 = collect_claude_entries(projects, cache=cache)
    assert entries3 == []
    assert str(f) not in cache["files"]["claude"]


def _proto_varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _proto_int_field(number: int, value: int) -> bytes:
    return _proto_varint(number << 3) + _proto_varint(value)


def _proto_bytes_field(number: int, value: bytes) -> bytes:
    return _proto_varint((number << 3) | 2) + _proto_varint(len(value)) + value


def _antigravity_blob(
    *,
    timestamp: int = 1_781_107_200,
    model: str = "gemini-3-flash-a",
    display_model: str | None = None,
    input_tokens: int = 3949,
    output_tokens: int = 160,
    cache_read_tokens: int = 16289,
) -> bytes:
    usage = b"".join(
        (
            _proto_int_field(2, input_tokens),
            _proto_int_field(3, output_tokens),
            _proto_int_field(5, cache_read_tokens),
        )
    )
    protobuf_timestamp = _proto_int_field(1, timestamp)
    chat = b"".join(
        (
            _proto_bytes_field(4, usage),
            _proto_bytes_field(9, _proto_bytes_field(4, protobuf_timestamp)),
            _proto_bytes_field(19, model.encode()),
            *(
                (_proto_bytes_field(21, display_model.encode()),)
                if display_model is not None
                else ()
            ),
        )
    )
    return _proto_bytes_field(1, chat)


def _write_antigravity_db(path: Path, blobs: list[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)"
        )
        connection.executemany(
            "INSERT INTO gen_metadata (idx, data, size) VALUES (?, ?, ?)",
            [(index, blob, len(blob)) for index, blob in enumerate(blobs)],
        )
        connection.commit()
    finally:
        connection.close()


def test_parses_antigravity_generator_metadata():
    entry = _antigravity_entry_from_blob(_antigravity_blob(), session_id="cascade-1", row_index=7)

    assert entry is not None
    assert entry.provider == "antigravity"
    assert entry.model == "gemini-3-flash-a"
    assert entry.session_id == "cascade-1"
    assert entry.ts == datetime(2026, 6, 10, 16, tzinfo=UTC)
    assert (entry.input_tokens, entry.output_tokens, entry.cache_read_tokens) == (
        3949,
        160,
        16289,
    )
    assert entry.total_tokens == 20398
    assert entry.dedup_key == "antigravity:cascade-1:7"


def test_antigravity_prefers_selected_model_over_backend_response_model():
    entry = _antigravity_entry_from_blob(
        _antigravity_blob(
            model="gemini-3-flash-a",
            display_model="Gemini 3.5 Flash (High)",
        ),
        session_id="cascade-1",
        row_index=7,
    )

    assert entry is not None
    assert entry.model == "Gemini 3.5 Flash (High)"


def test_antigravity_parser_skips_invalid_or_usageless_metadata():
    assert _antigravity_entry_from_blob(b"\x0a\xff", session_id="x", row_index=0) is None
    assert (
        _antigravity_entry_from_blob(
            _antigravity_blob(input_tokens=0, output_tokens=0, cache_read_tokens=0),
            session_id="x",
            row_index=0,
        )
        is None
    )


def test_collect_antigravity_sqlite_and_incremental_cache(tmp_path: Path):
    conversations = tmp_path / "conversations"
    database = conversations / "cascade-1.db"
    _write_antigravity_db(
        database,
        [
            _antigravity_blob(),
            _antigravity_blob(
                timestamp=1_781_110_800,
                model="gemini-2.5-pro",
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=500,
            ),
        ],
    )

    parsed = _parse_antigravity_db(database)
    assert len(parsed) == 2
    assert {entry.model for entry in parsed} == {"gemini-3-flash-a", "gemini-2.5-pro"}

    cache: dict = {"version": 7, "files": {}}
    entries = collect_antigravity_entries((conversations,), cache=cache)
    assert len(entries) == 2
    assert str(database) in cache["files"]["antigravity"]
    assert len(collect_antigravity_entries((conversations,), cache=cache)) == 2

    database.unlink()
    assert collect_antigravity_entries((conversations,), cache=cache) == []
    assert str(database) not in cache["files"]["antigravity"]


def test_collect_antigravity_reads_active_wal(tmp_path: Path):
    conversations = tmp_path / "conversations"
    database = conversations / "active.db"
    conversations.mkdir()
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)"
        )
        writer.commit()
        blob = _antigravity_blob()
        writer.execute(
            "INSERT INTO gen_metadata (idx, data, size) VALUES (?, ?, ?)",
            (0, blob, len(blob)),
        )
        writer.commit()

        cache: dict = {"version": 7, "files": {}}
        entries = collect_antigravity_entries((conversations,), cache=cache)
        signature = cache["files"]["antigravity"][str(database)]["signature"]

        assert len(entries) == 1
        assert any(part[0] == "active.db-wal" for part in signature)
    finally:
        writer.close()


def test_compute_usage_stats_end_to_end(tmp_path: Path):
    projects = tmp_path / "projects"
    proj = projects / "demo"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(
        "\n".join(
            [
                _assistant_line(
                    ts="2026-06-10T10:00:00Z", request_id="r1", message_id="m1", session_id="s1"
                ),
                # duplicate request — must not double count
                _assistant_line(
                    ts="2026-06-10T10:00:00Z", request_id="r1", message_id="m1", session_id="s1"
                ),
                _assistant_line(
                    ts="2026-06-09T10:00:00Z", request_id="r2", message_id="m2", session_id="s1"
                ),
            ]
        ),
        encoding="utf-8",
    )
    cache_path = tmp_path / "cache.json"
    out = compute_usage_stats(
        window="all",
        projects_dir=projects,
        cache_path=cache_path,
        tz=UTC,
        now=datetime(2026, 6, 10, 23, tzinfo=UTC),
    )
    assert out["totals"]["messages"] == 2  # deduped
    assert out["totals"]["sessions"] == 1
    assert out["totals"]["active_days"] == 2
    assert out["providers"] == ["claude"]
    assert cache_path.exists()


# ── Codex rollout parsing ───────────────────────────────────────────
def _codex_lines(
    turns: list[dict], *, session_id: str = "sess-x", model: str = "gpt-5-codex"
) -> str:
    lines = [
        json.dumps({"type": "session_meta", "payload": {"type": "session_meta", "id": session_id}})
    ]
    for turn in turns:
        lines.append(
            json.dumps(
                {"type": "turn_context", "payload": {"type": "turn_context", "model": model}}
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": turn["ts"],
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": turn["input"],
                                "cached_input_tokens": turn["cached"],
                                "output_tokens": turn["output"],
                                "total_tokens": turn["input"] + turn["output"],
                            }
                        },
                    },
                }
            )
        )
    return "\n".join(lines)


def test_parse_codex_maps_cached_input_to_cache_read(tmp_path: Path):
    f = tmp_path / "rollout-2026-06-10T10-00-00-abc.jsonl"
    f.write_text(
        _codex_lines(
            [
                {"ts": "2026-06-10T10:00:00.000Z", "input": 15800, "cached": 4992, "output": 351},
                {"ts": "2026-06-10T10:05:00.000Z", "input": 9265, "cached": 7936, "output": 504},
            ]
        ),
        encoding="utf-8",
    )
    entries = _parse_codex_file(f)
    assert len(entries) == 2
    e0 = entries[0]
    assert e0.provider == "codex"
    assert e0.model == "gpt-5-codex"
    assert e0.session_id == "sess-x"
    # Codex input_tokens includes the cached re-read; the cached part maps to
    # cache_read so the parts mirror Claude and the total is preserved.
    assert e0.input_tokens == 15800 - 4992
    assert e0.cache_read_tokens == 4992
    assert e0.output_tokens == 351
    assert e0.billed_input_tokens_total == 15800
    assert e0.cache_creation_tokens == 0
    assert e0.total_tokens == 15800 + 351
    # Distinct per-turn dedup keys.
    assert entries[0].dedup_key != entries[1].dedup_key


def test_parse_codex_skips_duplicate_cumulative_token_events(tmp_path: Path):
    f = tmp_path / "rollout-2026-06-10T10-00-00-abc.jsonl"
    lines = _codex_lines(
        [{"ts": "2026-06-10T10:00:00Z", "input": 100, "cached": 40, "output": 10}]
    ).splitlines()
    token_event = json.loads(lines[-1])
    token_event["timestamp"] = "2026-06-10T10:00:01Z"
    token_event["payload"]["info"]["total_token_usage"] = dict(
        token_event["payload"]["info"]["last_token_usage"]
    )
    original_event = json.loads(lines[-1])
    original_event["payload"]["info"]["total_token_usage"] = dict(
        original_event["payload"]["info"]["last_token_usage"]
    )
    lines[-1] = json.dumps(original_event)
    lines.append(json.dumps(token_event))
    f.write_text("\n".join(lines), encoding="utf-8")

    entries = _parse_codex_file(f)

    assert len(entries) == 1
    assert entries[0].total_tokens == 110


def test_parse_codex_compaction_as_billed_usage(tmp_path: Path):
    f = tmp_path / "rollout-2026-06-10T10-00-00-abc.jsonl"
    f.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"type": "session_meta", "id": "sess-x"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {"type": "turn_context", "model": "gpt-5.4"},
                    }
                ),
                json.dumps({"type": "compacted", "payload": {"message": ""}}),
                json.dumps({"type": "event_msg", "payload": {"type": "context_compacted"}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2026-06-10T10:00:00.000Z",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 0,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 5986,
                                }
                            },
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    entries = _parse_codex_file(f)

    assert len(entries) == 1
    e0 = entries[0]
    assert e0.model == "gpt-5.4"
    assert e0.input_tokens == 5986
    assert e0.cache_read_tokens == 0
    assert e0.output_tokens == 0
    assert e0.billed_input_tokens_total == 5986
    assert e0.total_tokens == 5986


def test_collect_codex_incremental_cache(tmp_path: Path):
    sessions = tmp_path / "sessions" / "2026" / "06" / "10"
    sessions.mkdir(parents=True)
    f = sessions.parent.parent.parent / "2026" / "06" / "10" / "rollout-x.jsonl"
    f.write_text(
        _codex_lines([{"ts": "2026-06-10T10:00:00Z", "input": 100, "cached": 40, "output": 10}]),
        encoding="utf-8",
    )
    root = tmp_path / "sessions"
    cache: dict = {"version": 4, "files": {}}
    assert len(collect_codex_entries(root, cache=cache)) == 1
    # Claude and Codex live in separate cache buckets.
    assert "codex" in cache["files"]
    assert len(collect_codex_entries(root, cache=cache)) == 1  # served from cache


def test_collect_claude_incremental_append_parses_only_new_bytes(tmp_path: Path):
    projects = tmp_path / "projects"
    proj = projects / "p"
    proj.mkdir(parents=True)
    f = proj / "sess.jsonl"
    # First record terminated with a newline, so the offset commits past it.
    f.write_text(
        _assistant_line(ts="2026-06-10T10:00:00Z", request_id="r1", message_id="m1") + "\n",
        encoding="utf-8",
    )
    cache: dict = {"version": 4, "files": {}}
    assert len(collect_claude_entries(projects, cache=cache)) == 1
    committed = cache["files"]["claude"][str(f)]["offset"]
    assert committed == f.stat().st_size  # whole (newline-terminated) file consumed

    # Append a second record; only the new bytes should be parsed and merged.
    with f.open("a", encoding="utf-8") as handle:
        handle.write(
            _assistant_line(ts="2026-06-10T11:00:00Z", request_id="r2", message_id="m2") + "\n"
        )
    entries = collect_claude_entries(projects, cache=cache)
    assert {e.dedup_key for e in entries} == {"r1:m1", "r2:m2"}
    assert cache["files"]["claude"][str(f)]["offset"] > committed


def test_collect_claude_trailing_unterminated_line_not_double_counted(tmp_path: Path):
    projects = tmp_path / "projects"
    proj = projects / "p"
    proj.mkdir(parents=True)
    f = proj / "sess.jsonl"
    # No trailing newline: the record is emitted but the offset stays before it.
    f.write_text(
        _assistant_line(ts="2026-06-10T10:00:00Z", request_id="r1", message_id="m1"),
        encoding="utf-8",
    )
    cache: dict = {"version": 4, "files": {}}
    assert {e.dedup_key for e in collect_claude_entries(projects, cache=cache)} == {"r1:m1"}
    assert cache["files"]["claude"][str(f)]["offset"] == 0  # nothing committed yet

    # The writer finishes that line and appends another. The first record must
    # be counted exactly once, not re-emitted on top of the cached copy.
    with f.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            + _assistant_line(ts="2026-06-10T11:00:00Z", request_id="r2", message_id="m2")
            + "\n"
        )
    entries = collect_claude_entries(projects, cache=cache)
    keys = [e.dedup_key for e in entries]
    assert sorted(keys) == ["r1:m1", "r2:m2"]
    assert len(keys) == 2


def test_collect_codex_incremental_append_resumes_session_state(tmp_path: Path):
    sessions = tmp_path / "sessions" / "2026" / "06" / "10"
    sessions.mkdir(parents=True)
    f = sessions / "rollout-x.jsonl"
    f.write_text(
        _codex_lines([{"ts": "2026-06-10T10:00:00Z", "input": 100, "cached": 40, "output": 10}])
        + "\n",
        encoding="utf-8",
    )
    root = tmp_path / "sessions"
    cache: dict = {"version": 4, "files": {}}
    first = collect_codex_entries(root, cache=cache)
    assert len(first) == 1

    # Append only a token_count event (no fresh session_meta/turn_context, as a
    # mid-session append looks): the parser must resume the cached session id and
    # model from state rather than falling back to defaults.
    appended = json.dumps(
        {
            "type": "event_msg",
            "timestamp": "2026-06-10T10:05:00Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 50,
                        "output_tokens": 20,
                        "total_tokens": 220,
                    }
                },
            },
        }
    )
    with f.open("a", encoding="utf-8") as handle:
        handle.write(appended + "\n")
    entries = collect_codex_entries(root, cache=cache)
    assert len(entries) == 2
    new = entries[-1]
    assert new.model == "gpt-5-codex"  # resumed from cached state, not "unknown"
    assert new.session_id == "sess-x"
    assert new.input_tokens == 200 - 50
    assert len({e.dedup_key for e in entries}) == 2  # distinct keys across the boundary


def _codex_meta_line(session_id: str, forked_from: str | None = None) -> str:
    meta: dict = {"type": "session_meta", "id": session_id}
    if forked_from:
        meta["forked_from_id"] = forked_from
    return json.dumps({"type": "session_meta", "payload": meta})


def _codex_rollout(
    session_id: str,
    turns: list[dict],
    *,
    forked_from: str | None = None,
    model: str = "gpt-5-codex",
) -> str:
    """Codex rollout lines with realistic cumulative `total_token_usage`."""
    lines = [
        _codex_meta_line(session_id, forked_from),
        json.dumps({"type": "turn_context", "payload": {"type": "turn_context", "model": model}}),
    ]
    total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for turn in turns:
        last = {
            "input_tokens": turn["input"],
            "cached_input_tokens": turn["cached"],
            "output_tokens": turn["output"],
            "total_tokens": turn["input"] + turn["output"],
        }
        total = {k: total[k] + last[k] for k in total}
        lines.append(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": turn["ts"],
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": last, "total_token_usage": dict(total)},
                    },
                }
            )
        )
    return "\n".join(lines) + "\n"


def _codex_fork(session_id: str, parent_text: str, fork_ts: str, new_turns: list[dict]) -> str:
    """Reproduce what Codex writes when forking: the fork's own session_meta,
    then a byte-copy of the parent rollout (ancestor session_meta lines
    included) with every timestamp rewritten to the fork instant, then any
    genuinely new turns continuing the cumulative totals."""
    parent_lines = parent_text.strip().split("\n")
    parent_meta = json.loads(parent_lines[0])["payload"]
    replayed = []
    total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for raw in parent_lines:
        event = json.loads(raw)
        if "timestamp" in event:
            event["timestamp"] = fork_ts
        info = (event.get("payload") or {}).get("info") or {}
        if isinstance(info.get("total_token_usage"), dict):
            total = dict(info["total_token_usage"])
        replayed.append(json.dumps(event))
    out = [_codex_meta_line(session_id, parent_meta["id"]), *replayed]
    for turn in new_turns:
        last = {
            "input_tokens": turn["input"],
            "cached_input_tokens": turn["cached"],
            "output_tokens": turn["output"],
            "total_tokens": turn["input"] + turn["output"],
        }
        total = {k: total[k] + last[k] for k in total}
        out.append(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": turn["ts"],
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": last, "total_token_usage": dict(total)},
                    },
                }
            )
        )
    return "\n".join(out) + "\n"


def test_codex_fork_replay_dedups_against_parent(tmp_path: Path):
    """Forking a Codex session copies the ancestor rollout's entire history into
    the new file (ancestor session_meta lines included) with all timestamps
    rewritten to the fork instant. Replayed turns must aggregate once — on
    their original day — through a fork-of-a-fork, while each fork's genuinely
    new turns count on their own day."""
    sessions = tmp_path / "sessions"
    root_day = sessions / "2026" / "06" / "10"
    fork_day = sessions / "2026" / "06" / "15"
    root_day.mkdir(parents=True)
    fork_day.mkdir(parents=True)

    root_turns = [
        {"ts": "2026-06-10T10:00:00.000Z", "input": 1000, "cached": 400, "output": 50},
        {"ts": "2026-06-10T11:00:00.000Z", "input": 2000, "cached": 1500, "output": 80},
    ]
    root_text = _codex_rollout("sess-root", root_turns)
    (root_day / "rollout-2026-06-10T10-00-00-root.jsonl").write_text(root_text, encoding="utf-8")

    child_turn = {"ts": "2026-06-15T09:05:00.000Z", "input": 3000, "cached": 2500, "output": 120}
    child_text = _codex_fork("sess-child", root_text, "2026-06-15T09:00:00.100Z", [child_turn])
    (fork_day / "rollout-2026-06-15T09-00-00-child.jsonl").write_text(child_text, encoding="utf-8")

    # Fork of the fork: replays root + child history through two hops.
    grand_turn = {"ts": "2026-06-15T10:10:00.000Z", "input": 4000, "cached": 3500, "output": 200}
    (fork_day / "rollout-2026-06-15T10-00-00-grand.jsonl").write_text(
        _codex_fork("sess-grand", child_text, "2026-06-15T10:00:00.100Z", [grand_turn]),
        encoding="utf-8",
    )

    entries = collect_codex_entries(sessions)
    now = datetime(2026, 6, 15, 23, tzinfo=UTC)
    payload = aggregate(entries, window="all", tz=UTC, now=now)

    totals = payload["totals"]
    all_turns = root_turns + [child_turn, grand_turn]
    expected = sum(t["input"] + t["output"] for t in all_turns)
    assert totals["total_tokens"] == expected  # every replay counted once
    assert totals["messages"] == len(all_turns)

    by_day = {row["date"]: row for row in payload["by_day"]}
    # Replayed turns keep their original day; only the genuinely new post-fork
    # turns land on the fork day.
    assert by_day["2026-06-10"]["total_tokens"] == sum(t["input"] + t["output"] for t in root_turns)
    assert by_day["2026-06-15"]["total_tokens"] == sum(
        t["input"] + t["output"] for t in (child_turn, grand_turn)
    )

    # The SQL store must reproduce the pure aggregation, whichever file syncs first.
    from fluxion.usage.history.store import UsageStore

    store = UsageStore(tmp_path / "usage.db")
    store.sync(projects_dir=tmp_path / "none", sessions_dir=sessions, antigravity_dirs=(), tz=UTC)
    got = store.aggregate("all", tz=UTC, now=now)
    reference = dict(payload)
    assert got == reference


def test_compute_stats_merges_claude_codex_and_antigravity(tmp_path: Path):
    projects = tmp_path / "projects" / "p"
    projects.mkdir(parents=True)
    (projects / "s.jsonl").write_text(
        _assistant_line(ts="2026-06-10T10:00:00Z", session_id="c1"), encoding="utf-8"
    )
    sessions = tmp_path / "sessions"
    day = sessions / "2026" / "06" / "10"
    day.mkdir(parents=True)
    (day / "rollout-y.jsonl").write_text(
        _codex_lines(
            [{"ts": "2026-06-10T11:00:00Z", "input": 100, "cached": 40, "output": 10}],
            session_id="x1",
        ),
        encoding="utf-8",
    )
    conversations = tmp_path / "conversations"
    _write_antigravity_db(conversations / "a1.db", [_antigravity_blob()])
    out = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "projects",
        sessions_dir=sessions,
        antigravity_dirs=(conversations,),
        tz=UTC,
        now=datetime(2026, 6, 10, 23, tzinfo=UTC),
    )
    assert out["providers"] == ["antigravity", "claude", "codex"]
    assert out["totals"]["sessions"] == 3
    assert out["totals"]["messages"] == 3
    models = {m["model"] for m in out["by_model"]}
    assert models == {"gemini-3-flash-a", "gpt-5-codex", "claude-opus-4-8"}


def test_history_service_reconciles_local_codex_with_server_total(tmp_path: Path):
    sessions = tmp_path / "sessions" / "2026" / "06" / "10"
    sessions.mkdir(parents=True)
    (sessions / "rollout-x.jsonl").write_text(
        _codex_lines([{"ts": "2026-06-10T10:00:00Z", "input": 100, "cached": 40, "output": 10}]),
        encoding="utf-8",
    )

    class AccountProbe:
        def probe(self):
            return CodexAccountUsage(lifetime_tokens=220, fetched_at="2026-06-11T00:00:00Z")

    from fluxion.usage.history import UsageHistoryService

    service = UsageHistoryService(
        projects_dir=tmp_path / "projects",
        sessions_dir=tmp_path / "sessions",
        antigravity_dirs=(),
        codex_account_usage_probe=AccountProbe(),
    )
    out = service.get("all")

    assert out["by_provider"][0]["provider"] == "codex"
    assert out["codex_reconciliation"] == {
        "status": "ok",
        "local_tokens": 110,
        "server_tokens": 220,
        "unclassified_tokens": 110,
        "excess_local_tokens": 0,
        "coverage": 0.5,
        "fetched_at": "2026-06-11T00:00:00Z",
    }


def _seed_histories(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a varied multi-provider, multi-day history and return its dirs."""
    projects = tmp_path / "projects" / "p"
    projects.mkdir(parents=True)
    (projects / "s.jsonl").write_text(
        "\n".join(
            [
                _assistant_line(
                    ts="2026-06-08T09:00:00Z",
                    request_id="a1",
                    message_id="m1",
                    session_id="c1",
                    model="claude-opus-4-8",
                    input_tokens=300,
                ),
                _assistant_line(
                    ts="2026-06-09T14:00:00Z",
                    request_id="a2",
                    message_id="m2",
                    session_id="c1",
                    model="claude-haiku",
                    input_tokens=120,
                ),
                _assistant_line(
                    ts="2026-06-10T14:30:00Z",
                    request_id="a3",
                    message_id="m3",
                    session_id="c2",
                    model="claude-opus-4-8",
                    input_tokens=80,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    day = sessions / "2026" / "06" / "10"
    day.mkdir(parents=True)
    (day / "rollout-y.jsonl").write_text(
        _codex_lines(
            [
                {"ts": "2026-06-09T11:00:00Z", "input": 500, "cached": 40, "output": 10},
                {"ts": "2026-06-10T11:00:00Z", "input": 700, "cached": 60, "output": 20},
            ],
            session_id="x1",
        )
        + "\n",
        encoding="utf-8",
    )
    conversations = tmp_path / "conversations"
    _write_antigravity_db(conversations / "a1.db", [_antigravity_blob()])
    return tmp_path / "projects", sessions, conversations


@pytest.mark.parametrize("window", ["all", "30d", "7d", "1d"])
def test_store_aggregate_matches_pure_aggregate(tmp_path: Path, window: str):
    from fluxion.usage.history.store import UsageStore

    projects, sessions, conversations = _seed_histories(tmp_path)
    now = datetime(2026, 6, 10, 23, tzinfo=UTC)

    reference = compute_usage_stats(
        window=window,
        projects_dir=projects,
        sessions_dir=sessions,
        antigravity_dirs=(conversations,),
        tz=UTC,
        now=now,
    )
    reference.pop("generated_at", None)  # the service stamps this, not aggregate

    store = UsageStore(tmp_path / "usage.db")
    store.sync(
        projects_dir=projects, sessions_dir=sessions, antigravity_dirs=(conversations,), tz=UTC
    )
    got = store.aggregate(window, tz=UTC, now=now)

    assert got == reference


def test_store_incremental_sync_matches_full_after_append_and_delete(tmp_path: Path):
    from fluxion.usage.history.store import UsageStore

    projects, sessions, conversations = _seed_histories(tmp_path)
    now = datetime(2026, 6, 10, 23, tzinfo=UTC)
    store = UsageStore(tmp_path / "usage.db")
    store.sync(
        projects_dir=projects, sessions_dir=sessions, antigravity_dirs=(conversations,), tz=UTC
    )

    # Append a fresh turn to the existing Claude transcript (the append-only path)
    with (projects / "p" / "s.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            _assistant_line(
                ts="2026-06-10T16:00:00Z",
                request_id="a4",
                message_id="m4",
                session_id="c2",
                model="claude-opus-4-8",
                input_tokens=999,
            )
            + "\n"
        )
    # And drop the Codex history entirely.
    (sessions / "2026" / "06" / "10" / "rollout-y.jsonl").unlink()

    store.sync(
        projects_dir=projects, sessions_dir=sessions, antigravity_dirs=(conversations,), tz=UTC
    )
    got = store.aggregate("all", tz=UTC, now=now)

    reference = compute_usage_stats(
        window="all",
        projects_dir=projects,
        sessions_dir=sessions,
        antigravity_dirs=(conversations,),
        tz=UTC,
        now=now,
    )
    reference.pop("generated_at", None)
    assert got == reference
    assert "codex" not in got["providers"]


def test_empty_when_no_projects_dir(tmp_path: Path):
    out = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "missing",
        tz=UTC,
        now=datetime(2026, 6, 10, tzinfo=UTC),
    )
    assert out["totals"]["messages"] == 0
    assert out["totals"]["top_model"] is None
    assert out["totals"]["peak_hour"] is None


def test_codex_archived_sessions_active_only(tmp_path: Path):
    from fluxion.usage.history import UsageHistoryService

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "rollout-1.jsonl").write_text(
        _codex_lines(
            [{"ts": "2026-06-10T12:00:00Z", "input": 100, "cached": 0, "output": 10}],
            session_id="s1",
        ),
        encoding="utf-8",
    )

    # 1. compute_usage_stats path
    out = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        tz=UTC,
        now=datetime(2026, 6, 10, 13, tzinfo=UTC),
    )
    assert out["totals"]["messages"] == 1
    assert out["totals"]["input_tokens"] == 100
    assert out["totals"]["output_tokens"] == 10

    # 2. UsageHistoryService path
    service = UsageHistoryService(
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        antigravity_dirs=(),
        db_path=tmp_path / "usage.db",
    )
    got = service.get("all")
    assert got["totals"]["messages"] == 1
    assert got["totals"]["input_tokens"] == 100
    assert got["totals"]["output_tokens"] == 10


def test_codex_archived_sessions_archived_only(tmp_path: Path):
    from fluxion.usage.history import UsageHistoryService

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    archived = tmp_path / "archived_sessions"
    archived.mkdir()
    (archived / "rollout-1.jsonl").write_text(
        _codex_lines(
            [{"ts": "2026-06-10T12:00:00Z", "input": 200, "cached": 0, "output": 20}],
            session_id="s1",
        ),
        encoding="utf-8",
    )

    # 1. compute_usage_stats path
    out = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        tz=UTC,
        now=datetime(2026, 6, 10, 13, tzinfo=UTC),
    )
    assert out["totals"]["messages"] == 1
    assert out["totals"]["input_tokens"] == 200
    assert out["totals"]["output_tokens"] == 20

    # 2. UsageHistoryService path
    service = UsageHistoryService(
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        antigravity_dirs=(),
        db_path=tmp_path / "usage.db",
    )
    got = service.get("all")
    assert got["totals"]["messages"] == 1
    assert got["totals"]["input_tokens"] == 200
    assert got["totals"]["output_tokens"] == 20


def test_codex_archived_sessions_move_active_to_archived(tmp_path: Path):
    from fluxion.usage.history import UsageHistoryService

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    archived = tmp_path / "archived_sessions"
    archived.mkdir()

    active_path = sessions / "rollout-1.jsonl"
    archived_path = archived / "rollout-1.jsonl"

    content = _codex_lines(
        [{"ts": "2026-06-10T12:00:00Z", "input": 300, "cached": 0, "output": 30}], session_id="s1"
    )
    active_path.write_text(content, encoding="utf-8")

    db_path = tmp_path / "usage.db"
    service = UsageHistoryService(
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        antigravity_dirs=(),
        db_path=db_path,
    )

    # First sync (active-only)
    initial_got = service.get("all", force=True)
    assert initial_got["totals"]["messages"] == 1
    assert initial_got["totals"]["input_tokens"] == 300

    # Verify db paths
    import sqlite3

    conn = sqlite3.connect(db_path)
    assert (
        conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (str(active_path),)).fetchone()[0]
        == 1
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM entries WHERE path = ?", (str(active_path),)).fetchone()[
            0
        ]
        == 1
    )
    conn.close()

    # Move active -> archived
    active_path.rename(archived_path)

    # Second sync (archived-only)
    moved_got = service.get("all", force=True)
    assert moved_got["totals"]["messages"] == 1
    assert moved_got["totals"]["input_tokens"] == 300
    assert moved_got["totals"]["cost"] == initial_got["totals"]["cost"]

    # Verify db paths migrated
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (str(active_path),)).fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM entries WHERE path = ?", (str(active_path),)).fetchone()[
            0
        ]
        == 0
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (str(archived_path),)).fetchone()[
            0
        ]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM entries WHERE path = ?", (str(archived_path),)
        ).fetchone()[0]
        == 1
    )
    conn.close()

    # Test cache path key migration in compute_usage_stats
    cache_path = tmp_path / "cache.json"
    active_path.write_text(content, encoding="utf-8")
    if archived_path.exists():
        archived_path.unlink()

    # Initial one-shot sync
    out1 = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        cache_path=cache_path,
        tz=UTC,
        now=datetime(2026, 6, 10, 13, tzinfo=UTC),
    )
    assert out1["totals"]["messages"] == 1

    # Verify cache has active path
    import json

    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert str(active_path) in cache_data["files"]["codex"]
    assert str(archived_path) not in cache_data["files"]["codex"]

    # Move active -> archived
    active_path.rename(archived_path)

    # One-shot sync after move
    out2 = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        cache_path=cache_path,
        tz=UTC,
        now=datetime(2026, 6, 10, 13, tzinfo=UTC),
    )
    assert out2["totals"]["messages"] == 1
    assert out2["totals"]["input_tokens"] == 300

    # Verify cache migrated path
    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert str(active_path) not in cache_data["files"]["codex"]
    assert str(archived_path) in cache_data["files"]["codex"]


def test_codex_archived_sessions_duplicate_rollout_in_both(tmp_path: Path):
    from fluxion.usage.history import UsageHistoryService

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    archived = tmp_path / "archived_sessions"
    archived.mkdir()

    active_path = sessions / "rollout-1.jsonl"
    archived_path = archived / "rollout-1.jsonl"

    content_active = _codex_lines(
        [
            {"ts": "2026-06-10T12:00:00Z", "input": 400, "cached": 0, "output": 40},
            {"ts": "2026-06-10T12:10:00Z", "input": 500, "cached": 0, "output": 50},
        ],
        session_id="s1",
    )
    content_archived = content_active

    # A copy can briefly exist in both roots while Codex archives it.
    active_path.write_text(content_active, encoding="utf-8")
    archived_path.write_text(content_archived, encoding="utf-8")

    # One-shot sync path
    out = compute_usage_stats(
        window="all",
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        tz=UTC,
        now=datetime(2026, 6, 10, 13, tzinfo=UTC),
    )
    # Identical physical copies still represent one logical rollout.
    assert out["totals"]["messages"] == 2
    assert out["totals"]["input_tokens"] == 900

    # Service sync path
    service = UsageHistoryService(
        projects_dir=tmp_path / "empty_projects",
        sessions_dir=sessions,
        archived_sessions_dir=archived,
        antigravity_dirs=(),
        db_path=tmp_path / "usage.db",
    )
    got = service.get("all", force=True)
    assert got["totals"]["messages"] == 2
    assert got["totals"]["input_tokens"] == 900

    # Removing the preferred active copy must transparently fall back to the
    # archived copy without either losing or duplicating its stored entries.
    active_path.unlink()
    archived_got = service.get("all", force=True)
    assert archived_got["totals"]["messages"] == 2
    assert archived_got["totals"]["input_tokens"] == 900
    assert archived_got["totals"]["cost"] == got["totals"]["cost"]
