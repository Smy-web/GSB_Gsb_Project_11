"""Trigger-time computation with real DST handling.

The search works on *local wall-clock* fields (month/day/hour/minute cascade)
and then resolves the naive candidate against the timezone:

* unambiguous time        -> used as-is
* ambiguous (fall back)   -> the FIRST occurrence is used (``fold=0``)
* nonexistent (spring gap)-> handled by the configured gap policy:
    - ``skip``          : do not fire; continue with the next cron match
    - ``shift_forward`` : fire at the first valid local time after the gap
    - ``use_first_utc`` : interpret the wall time with the UTC offset in
                          effect *before* the transition (PEP 495 fold=0)

Day-of-month / day-of-week follow POSIX cron semantics: when both fields
are restricted the day matches if EITHER matches (union); when only one is
restricted it alone decides (intersection with the always-true ``*``).
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .cronexpr import CronError, parse

GAP_POLICIES = ("skip", "shift_forward", "use_first_utc")

_ONE_MIN = timedelta(minutes=1)
_MAX_YEARS = 400  # full Gregorian cycle; guards against impossible expressions


@lru_cache(maxsize=512)
def _compiled(expr: str):
    spec = parse(expr)
    return (
        sorted(spec.minutes),
        sorted(spec.hours),
        frozenset(spec.dom),
        sorted(spec.months),
        frozenset(spec.dow),
        spec.dom_any,
        spec.dow_any,
    )


@lru_cache(maxsize=128)
def _zone(tz) -> ZoneInfo:
    if isinstance(tz, ZoneInfo):
        return tz
    return ZoneInfo(str(tz))


def _resolve(naive: datetime, tz: ZoneInfo, gap_policy: str):
    """Resolve a naive local time to an aware datetime.

    Returns ``(aware, advance)``: ``aware`` is the datetime to fire (or
    ``None`` when the policy says to skip); ``advance`` is how far the
    search cursor must move when ``aware`` is ``None``.
    """
    aware0 = naive.replace(tzinfo=tz, fold=0)
    off0 = aware0.utcoffset()
    off1 = naive.replace(tzinfo=tz, fold=1).utcoffset()
    if off0 == off1:
        return aware0, None
    if off0 > off1:
        # Ambiguous local time (autumn fold): take the first occurrence.
        return aware0, None
    # Nonexistent local time (spring gap).
    if gap_policy == "skip":
        return None, _ONE_MIN
    if gap_policy == "use_first_utc":
        # fold=0 on a gap applies the pre-transition UTC offset.
        return aware0, None
    # shift_forward: first valid local time at/after the naive time.
    probe = naive
    for _ in range(60 * 48):  # gaps are far smaller than 48h in practice
        probe += _ONE_MIN
        p0 = probe.replace(tzinfo=tz, fold=0).utcoffset()
        p1 = probe.replace(tzinfo=tz, fold=1).utcoffset()
        if p0 == p1:
            return probe.replace(tzinfo=tz), None
    raise CronError(-1, naive.isoformat(), "could not find end of DST gap")


def _next_one(compiled, tz: ZoneInfo, cursor: datetime, gap_policy: str):
    minutes, hours, dom, months, dow, dom_any, dow_any = compiled
    month_set = frozenset(months)
    hour_set = frozenset(hours)
    minute_set = frozenset(minutes)

    local = cursor.astimezone(tz)
    base = local.replace(second=0, microsecond=0) + _ONE_MIN
    y, mo, d, h, mi = base.year, base.month, base.day, base.hour, base.minute

    limit = y + _MAX_YEARS
    while y <= limit:
        # --- month ---
        if mo not in month_set:
            i = bisect_left(months, mo)
            if i == len(months):
                y, mo, d, h, mi = y + 1, months[0], 1, 0, 0
            else:
                mo, d, h, mi = months[i], 1, 0, 0
            continue
        # --- day (POSIX dom/dow rule) ---
        dom_hit = d in dom
        dow_hit = ((date(y, mo, d).weekday() + 1) % 7) in dow
        if dom_any and dow_any:
            day_ok = True
        elif dom_any:
            day_ok = dow_hit
        elif dow_any:
            day_ok = dom_hit
        else:
            day_ok = dom_hit or dow_hit
        if not day_ok:
            nxt = date(y, mo, d) + timedelta(days=1)
            y, mo, d, h, mi = nxt.year, nxt.month, nxt.day, 0, 0
            continue
        # --- hour ---
        if h not in hour_set:
            i = bisect_left(hours, h)
            if i == len(hours):
                nxt = date(y, mo, d) + timedelta(days=1)
                y, mo, d, h, mi = nxt.year, nxt.month, nxt.day, 0, 0
            else:
                h, mi = hours[i], 0
            continue
        # --- minute ---
        if mi not in minute_set:
            i = bisect_left(minutes, mi)
            if i == len(minutes):
                h += 1
                if h > 23:
                    nxt = date(y, mo, d) + timedelta(days=1)
                    y, mo, d, h, mi = nxt.year, nxt.month, nxt.day, 0, 0
                else:
                    mi = 0
            else:
                mi = minutes[i]
            continue
        # --- candidate found; resolve against DST ---
        naive = datetime(y, mo, d, h, mi)
        aware, advance = _resolve(naive, tz, gap_policy)
        if aware is not None and aware > cursor:
            return aware
        step = advance if advance is not None else _ONE_MIN
        base = naive + step
        y, mo, d, h, mi = base.year, base.month, base.day, base.hour, base.minute
    raise CronError(
        -1, str(cursor), f"no fire time within {_MAX_YEARS} years"
    )


def next_fire(expr: str, tz, after: datetime, count: int = 1,
              gap_policy: str = "skip") -> list[datetime]:
    """Return the next ``count`` fire times for ``expr`` strictly after
    ``after`` (an aware datetime), as aware datetimes in timezone ``tz``.
    """
    if gap_policy not in GAP_POLICIES:
        raise CronError(-1, gap_policy, f"unknown gap policy {gap_policy!r}")
    if count < 1:
        raise CronError(-1, str(count), "count must be >= 1")
    if not isinstance(after, datetime) or after.tzinfo is None:
        raise CronError(-1, repr(after), "'after' must be an aware datetime")
    compiled = _compiled(expr)
    zone = _zone(tz)
    out = []
    cursor = after
    for _ in range(count):
        cursor = _next_one(compiled, zone, cursor, gap_policy)
        out.append(cursor)
    return out
