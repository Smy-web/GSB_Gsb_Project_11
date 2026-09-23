import time
from datetime import datetime, timedelta, timezone

from conftest import job

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

SLOW = "sleep 0.5"
CATCH_UP_3 = {"misfire": {"policy": "catch_up", "max_catch_up": 3}}


def run_slow(make_runner, overlap):
    cfg = job(cmd=SLOW, overlap=overlap, **CATCH_UP_3)
    runner = make_runner([cfg])
    assert runner.run_once(T0) == 0
    start = time.monotonic()
    code = runner.run_once(T0 + timedelta(minutes=3))  # 3 due runs at once
    elapsed = time.monotonic() - start
    return runner, code, elapsed


def statuses(runner):
    return sorted(st for _, _, st, _ in runner.state.runs("j"))


def test_forbid_skips_while_running(make_runner):
    runner, code, _ = run_slow(make_runner, "forbid")
    assert code == 1
    assert statuses(runner) == ["done", "skipped_overlap", "skipped_overlap"]
    kinds = [k for _, _, k, _ in runner.state.events()]
    assert kinds.count("overlap_conflict") == 2


def test_allow_runs_concurrently(make_runner):
    runner, code, elapsed = run_slow(make_runner, "allow")
    assert code == 0
    assert statuses(runner) == ["done", "done", "done"]
    assert elapsed < 1.2  # 3 x 0.5s in parallel, not 1.5s serial


def test_queue_runs_sequentially_without_skew(make_runner):
    runner, code, elapsed = run_slow(make_runner, "queue")
    assert code == 0
    assert statuses(runner) == ["done", "done", "done"]
    assert elapsed >= 1.4  # 3 x 0.5s serial
    # Scheduled times come from the wall-clock expression, not from when
    # the previous run finished.
    sched = sorted(s for _, s, _, _ in runner.state.runs("j"))
    assert sched == [
        "2026-01-01T00:01:00+00:00",
        "2026-01-01T00:02:00+00:00",
        "2026-01-01T00:03:00+00:00",
    ]


def test_slow_job_does_not_skew_future_schedule(make_runner):
    runner = make_runner([job(cmd=SLOW, overlap="allow")])
    assert runner.run_once(T0) == 0
    for minute in (1, 2, 3):
        assert runner.run_once(T0 + timedelta(minutes=minute)) == 0
    sched = sorted(s for _, s, _, _ in runner.state.runs("j"))
    assert sched == [
        "2026-01-01T00:01:00+00:00",
        "2026-01-01T00:02:00+00:00",
        "2026-01-01T00:03:00+00:00",
    ]
