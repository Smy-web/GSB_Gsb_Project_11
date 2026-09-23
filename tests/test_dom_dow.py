from datetime import datetime, timezone

from sched import next_fire

TZ = "Asia/Shanghai"
AFTER = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)


def days(expr, count):
    return [t.strftime("%Y-%m-%d %H:%M")
            for t in next_fire(expr, TZ, AFTER, count)]


def test_both_restricted_means_union():
    # July 2026: Mondays are 6/13/20/27, the 15th is a Wednesday.
    assert days("0 9 15 * 1", 5) == [
        "2026-07-06 09:00",
        "2026-07-13 09:00",
        "2026-07-15 09:00",
        "2026-07-20 09:00",
        "2026-07-27 09:00",
    ]


def test_only_dom_restricted():
    assert days("0 9 15 * *", 2) == ["2026-07-15 09:00", "2026-08-15 09:00"]


def test_only_dow_restricted():
    assert days("0 9 * * 1", 2) == ["2026-07-06 09:00", "2026-07-13 09:00"]


def test_neither_restricted():
    assert days("0 9 * * *", 2) == ["2026-07-01 09:00", "2026-07-02 09:00"]


def test_union_with_named_dow():
    # July 2026: Saturdays are 4/11/18/25; the 10th is a Friday.
    assert days("0 9 10 * sat", 3) == [
        "2026-07-04 09:00", "2026-07-10 09:00", "2026-07-11 09:00"]
