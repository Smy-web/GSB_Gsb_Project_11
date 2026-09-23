from datetime import datetime, timezone

import pytest

from sched import next_fire
from sched.cronexpr import CronError

UTC = timezone.utc


def test_count_and_ordering():
    after = datetime(2026, 1, 1, tzinfo=UTC)
    fires = next_fire("*/15 * * * *", "UTC", after, 4)
    assert [t.isoformat() for t in fires] == [
        "2026-01-01T00:15:00+00:00",
        "2026-01-01T00:30:00+00:00",
        "2026-01-01T00:45:00+00:00",
        "2026-01-01T01:00:00+00:00",
    ]
    assert all(t.tzinfo is not None for t in fires)


def test_after_is_exclusive():
    after = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    assert next_fire("*/15 * * * *", "UTC", after, 1)[0] == \
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC)


def test_after_in_other_timezone():
    after = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    fire = next_fire("0 9 * * *", "Asia/Shanghai", after, 1)[0]
    assert fire.isoformat() == "2026-01-02T09:00:00+08:00"


def test_alias_expression():
    after = datetime(2026, 1, 1, tzinfo=UTC)
    assert next_fire("@hourly", "UTC", after, 1)[0] == \
        datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


def test_invalid_inputs_raise_cronerror():
    after = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(CronError):
        next_fire("not a cron", "UTC", after, 1)
    with pytest.raises(CronError):
        next_fire("* * * * *", "UTC", datetime(2026, 1, 1), 1)  # naive after
    with pytest.raises(CronError):
        next_fire("* * * * *", "UTC", after, 0)
    with pytest.raises(CronError):
        next_fire("* * * * *", "UTC", after, 1, gap_policy="bogus")
