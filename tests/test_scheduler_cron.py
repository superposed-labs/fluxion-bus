from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fluxion.scheduler import cron


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_parse_rejects_wrong_field_count():
    with pytest.raises(cron.CronError):
        cron.parse_cron("* * * *")


def test_matches_basic_minute_hour():
    parsed = cron.parse_cron("30 9 * * *")
    assert cron.matches(parsed, _utc(2026, 6, 3, 9, 30))
    assert not cron.matches(parsed, _utc(2026, 6, 3, 9, 31))
    assert not cron.matches(parsed, _utc(2026, 6, 3, 10, 30))


def test_step_and_list_and_range():
    parsed = cron.parse_cron("*/15 * * * *")
    for minute in (0, 15, 30, 45):
        assert cron.matches(parsed, _utc(2026, 6, 3, 1, minute))
    assert not cron.matches(parsed, _utc(2026, 6, 3, 1, 7))

    parsed = cron.parse_cron("0 9-11 * * *")
    assert cron.matches(parsed, _utc(2026, 6, 3, 10, 0))
    assert not cron.matches(parsed, _utc(2026, 6, 3, 12, 0))


def test_day_of_week_sunday_zero_and_seven():
    # 2026-06-07 is a Sunday.
    zero = cron.parse_cron("0 0 * * 0")
    seven = cron.parse_cron("0 0 * * 7")
    sunday = _utc(2026, 6, 7, 0, 0)
    monday = _utc(2026, 6, 8, 0, 0)
    assert cron.matches(zero, sunday)
    assert cron.matches(seven, sunday)
    assert not cron.matches(zero, monday)


def test_dom_dow_or_quirk():
    # When both day-of-month and day-of-week are restricted, EITHER matches.
    parsed = cron.parse_cron("0 0 13 * 5")  # the 13th OR any Friday
    assert cron.matches(parsed, _utc(2026, 6, 13, 0, 0))  # 13th (a Saturday)
    assert cron.matches(parsed, _utc(2026, 6, 5, 0, 0))  # a Friday
    assert not cron.matches(parsed, _utc(2026, 6, 4, 0, 0))  # Thursday, not 13th


def test_occurred_between_window():
    parsed = cron.parse_cron("0 9 * * *")
    # Window straddling 09:00 fires; a window after it does not.
    assert cron.occurred_between(parsed, "UTC", _utc(2026, 6, 3, 8, 59), _utc(2026, 6, 3, 9, 0))
    assert not cron.occurred_between(parsed, "UTC", _utc(2026, 6, 3, 9, 1), _utc(2026, 6, 3, 9, 30))


def test_occurred_between_exclusive_start():
    parsed = cron.parse_cron("0 9 * * *")
    # Start exactly on the boundary is exclusive → no double fire next tick.
    assert not cron.occurred_between(
        parsed,
        "UTC",
        _utc(2026, 6, 3, 9, 0),
        _utc(
            2026,
            6,
            3,
            9,
            0,
        ),
    )


def test_occurred_between_timezone():
    # 09:00 in Asia/Shanghai (UTC+8) == 01:00 UTC.
    parsed = cron.parse_cron("0 9 * * *")
    assert cron.occurred_between(
        parsed, "Asia/Shanghai", _utc(2026, 6, 3, 0, 59), _utc(2026, 6, 3, 1, 0)
    )
    assert not cron.occurred_between(parsed, "UTC", _utc(2026, 6, 3, 0, 59), _utc(2026, 6, 3, 1, 0))
