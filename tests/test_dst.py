"""Hand-computed DST expectations for four timezones plus Feb 29."""
from datetime import datetime, timezone

from sched import next_fire

UTC = timezone.utc


def one(expr, tz, after, policy="skip"):
    return next_fire(expr, tz, after, 1, gap_policy=policy)[0].isoformat()


# ---- America/New_York: spring gap 2026-03-08 02:00 -> 03:00 --------------
AFTER_NY_SPRING = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)


def test_ny_spring_skip():
    # 2026-03-08 02:30 does not exist; skip jumps to the next day.
    assert one("30 2 * * *", "America/New_York", AFTER_NY_SPRING, "skip") == \
        "2026-03-09T02:30:00-04:00"


def test_ny_spring_shift_forward():
    # First valid local time after the gap is 03:00 EDT.
    assert one("30 2 * * *", "America/New_York", AFTER_NY_SPRING,
               "shift_forward") == "2026-03-08T03:00:00-04:00"


def test_ny_spring_use_first_utc():
    # 02:30 at the pre-transition offset (-05:00) == 03:30 EDT.
    assert one("30 2 * * *", "America/New_York", AFTER_NY_SPRING,
               "use_first_utc") == "2026-03-08T02:30:00-05:00"


def test_ny_normal_day_unaffected():
    assert one("30 2 * * *", "America/New_York",
               datetime(2026, 3, 5, 12, 0, tzinfo=UTC),
               "skip") == "2026-03-06T02:30:00-05:00"


def test_ny_fall_fold_first_occurrence():
    # 2026-11-01 01:30 happens twice; we take fold=0 (EDT, -04:00).
    after = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    fires = next_fire("30 1 * * *", "America/New_York", after, 2)
    assert fires[0].isoformat() == "2026-11-01T01:30:00-04:00"
    assert fires[0].fold == 0
    assert fires[1].isoformat() == "2026-11-02T01:30:00-05:00"


def test_ny_fall_no_duplicate_fire():
    # A minutely cron across the fold must stay strictly increasing and
    # must not fire the repeated hour twice.
    after = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)  # 00:30 EDT
    fires = next_fire("* * * * *", "America/New_York", after, 200)
    stamps = [t.astimezone(UTC) for t in fires]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    # 01:30 occurs only once (fold=0), not again at fold=1.
    locals_0130 = [t for t in fires
                   if (t.hour, t.minute) == (1, 30) and t.day == 1]
    assert len(locals_0130) == 1 and locals_0130[0].utcoffset().total_seconds() == -4 * 3600


# ---- Europe/Berlin: spring gap 2026-03-29 02:00 -> 03:00 -----------------
AFTER_BERLIN_SPRING = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)


def test_berlin_spring_policies():
    assert one("30 2 * * *", "Europe/Berlin", AFTER_BERLIN_SPRING, "skip") == \
        "2026-03-30T02:30:00+02:00"
    assert one("30 2 * * *", "Europe/Berlin", AFTER_BERLIN_SPRING,
               "shift_forward") == "2026-03-29T03:00:00+02:00"
    assert one("30 2 * * *", "Europe/Berlin", AFTER_BERLIN_SPRING,
               "use_first_utc") == "2026-03-29T02:30:00+01:00"


def test_berlin_fall_fold():
    # 2026-10-25 02:30 is ambiguous; fold=0 is CEST (+02:00).
    after = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    fires = next_fire("30 2 * * *", "Europe/Berlin", after, 2)
    assert fires[0].isoformat() == "2026-10-25T02:30:00+02:00"
    assert fires[1].isoformat() == "2026-10-26T02:30:00+01:00"


# ---- Australia/Lord_Howe: 30-minute DST ----------------------------------
# Spring: 2026-10-04 02:00+10:30 -> 02:30+11:00 (gap 02:00-02:29).
AFTER_LH_SPRING = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def test_lord_howe_spring_policies():
    assert one("15 2 * * *", "Australia/Lord_Howe", AFTER_LH_SPRING, "skip") == \
        "2026-10-05T02:15:00+11:00"
    assert one("15 2 * * *", "Australia/Lord_Howe", AFTER_LH_SPRING,
               "shift_forward") == "2026-10-04T02:30:00+11:00"
    assert one("15 2 * * *", "Australia/Lord_Howe", AFTER_LH_SPRING,
               "use_first_utc") == "2026-10-04T02:15:00+10:30"


def test_lord_howe_fall_fold():
    # Fall: 2026-04-05 02:00+11:00 -> 01:30+10:30, so 01:30-01:59 repeat.
    after = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
    fires = next_fire("45 1 * * *", "Australia/Lord_Howe", after, 2)
    assert fires[0].isoformat() == "2026-04-05T01:45:00+11:00"  # fold=0
    assert fires[1].isoformat() == "2026-04-06T01:45:00+10:30"


# ---- Asia/Shanghai: no DST ------------------------------------------------
def test_shanghai_no_dst():
    after = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    fires = next_fire("0 9 * * *", "Asia/Shanghai", after, 3)
    assert [t.isoformat() for t in fires] == [
        "2026-06-01T09:00:00+08:00",
        "2026-06-02T09:00:00+08:00",
        "2026-06-03T09:00:00+08:00",
    ]
    assert all(t.utcoffset().total_seconds() == 8 * 3600 for t in fires)


# ---- February 29 -----------------------------------------------------------
def test_feb29_leap_years():
    after = datetime(2026, 1, 1, tzinfo=UTC)
    fires = next_fire("0 0 29 2 *", "Asia/Shanghai", after, 3)
    assert [t.strftime("%Y-%m-%d") for t in fires] == \
        ["2028-02-29", "2032-02-29", "2036-02-29"]


def test_feb29_skips_non_leap_century():
    # 2100 is not a leap year: 2096 -> 2104.
    after = datetime(2096, 3, 1, tzinfo=UTC)
    fire = next_fire("0 0 29 2 *", "UTC", after, 1)[0]
    assert fire.strftime("%Y-%m-%d") == "2104-02-29"
