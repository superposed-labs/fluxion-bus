from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Minimal 5-field cron matcher: minute hour day-of-month month day-of-week.
# Supports "*", lists "a,b", ranges "a-b", and steps "*/n" / "a-b/n".
# Day-of-week is 0-6 with Sunday=0 (7 is also accepted as Sunday), matching the
# common Vixie-cron convention. No external dependency (kept in line with
# Fluxion's minimal-deps policy).

# Day-of-week accepts 0-7 (both 0 and 7 mean Sunday); 7 is normalized to 0 in
# parse_cron after field parsing.
_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
# Worst-case scan budget when catching up after a long downtime (14 days).
MAX_SCAN_MINUTES = 14 * 24 * 60


class CronError(ValueError):
    pass


def _parse_field(token: str, lo: int, hi: int) -> tuple[set[int], bool]:
    """Return (matching values, is_wildcard)."""
    token = token.strip()
    if token == "":
        raise CronError("empty cron field")
    is_wild = token == "*" or token.startswith("*/")
    values: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            raise CronError("empty cron list element")
        step = 1
        base = part
        if "/" in part:
            base, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise CronError(f"bad cron step: {part}") from exc
            if step <= 0:
                raise CronError(f"cron step must be positive: {part}")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            s_raw, e_raw = base.split("-", 1)
            try:
                start, end = int(s_raw), int(e_raw)
            except ValueError as exc:
                raise CronError(f"bad cron range: {part}") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise CronError(f"bad cron value: {part}") from exc
        if start > end:
            raise CronError(f"cron range out of order: {part}")
        for value in range(start, end + 1, step):
            if value < lo or value > hi:
                raise CronError(f"cron value {value} out of range {lo}-{hi}")
            values.add(value)
    if not values:
        raise CronError(f"cron field matched nothing: {token}")
    return values, is_wild


def parse_cron(expr: str) -> list[tuple[set[int], bool]]:
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"expected 5 cron fields, got {len(fields)}: {expr!r}")
    parsed = [
        _parse_field(tok, lo, hi) for tok, (lo, hi) in zip(fields, _FIELD_RANGES, strict=True)
    ]
    # Normalize day-of-week: accept 7 as Sunday.
    dow_values, dow_wild = parsed[4]
    if 7 in dow_values:
        dow_values = {0 if v == 7 else v for v in dow_values}
        parsed[4] = (dow_values, dow_wild)
    return parsed


def matches(parsed: list[tuple[set[int], bool]], dt: datetime) -> bool:
    minute_v, _ = parsed[0]
    hour_v, _ = parsed[1]
    dom_v, dom_wild = parsed[2]
    month_v, _ = parsed[3]
    dow_v, dow_wild = parsed[4]

    if dt.minute not in minute_v:
        return False
    if dt.hour not in hour_v:
        return False
    if dt.month not in month_v:
        return False

    # cron day-of-week: Sunday=0..Saturday=6.
    cron_dow = dt.isoweekday() % 7
    dom_ok = dt.day in dom_v
    dow_ok = cron_dow in dow_v
    # Vixie-cron quirk: when both day fields are restricted, match on EITHER.
    if dom_wild and dow_wild:
        day_ok = True
    elif dom_wild:
        day_ok = dow_ok
    elif dow_wild:
        day_ok = dom_ok
    else:
        day_ok = dom_ok or dow_ok
    return day_ok


def occurred_between(
    parsed: list[tuple[set[int], bool]],
    tz_name: str,
    start: datetime,
    end: datetime,
) -> bool:
    """True if any matching minute boundary lies in the half-open window
    (start, end]. `start`/`end` are aware datetimes; matching is evaluated in
    the rule's timezone."""
    if end <= start:
        return False
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = UTC

    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    # Clamp an over-long window to the recent budget.
    max_span = timedelta(minutes=MAX_SCAN_MINUTES)
    if end - start > max_span:
        start = end - max_span

    # First whole-minute boundary strictly after `start`.
    cur = start.replace(second=0, microsecond=0)
    while cur <= start:
        cur += timedelta(minutes=1)
    while cur <= end:
        if matches(parsed, cur.astimezone(tz)):
            return True
        cur += timedelta(minutes=1)
    return False
