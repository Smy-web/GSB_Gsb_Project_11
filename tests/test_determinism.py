from datetime import datetime, timedelta, timezone

from sched import next_fire
from sched.runner import Runner

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

JOBS = [
    {"name": "a", "expr": "*/2 * * * *", "tz": "UTC", "cmd": "true",
     "misfire": {"policy": "catch_up", "max_catch_up": 2}},
    {"name": "b", "expr": "30 2 * * *", "tz": "America/New_York",
     "cmd": "true", "overlap": "allow"},
]


def test_next_fire_is_deterministic():
    after = datetime(2026, 3, 1, tzinfo=UTC)
    first = next_fire("30 2 * * *", "America/New_York", after, 50)
    second = next_fire("30 2 * * *", "America/New_York", after, 50)
    assert first == second


def _run_scenario(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    runner = Runner({"jobs": JOBS, "db": str(db_path),
                   "clock_rewind_threshold_sec": 10**9})
    try:
        for minute in range(0, 7):
            runner.run_once(T0 + timedelta(minutes=minute))
        return [(j, s, st, ec) for j, s, st, ec in runner.state.runs()]
    finally:
        runner.close()


def test_two_runs_produce_identical_records(tmp_path):
    run1 = _run_scenario(tmp_path / "one" / "sched.db")
    run2 = _run_scenario(tmp_path / "two" / "sched.db")
    assert run1 == run2
    assert len(run1) > 0
