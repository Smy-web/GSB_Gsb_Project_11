from datetime import datetime, timedelta, timezone

from conftest import job

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def run_window(make_runner, job_cfg, minutes, now=None):
    runner = make_runner([job_cfg])
    assert runner.run_once(T0) == 0  # initialises checkpoint, fires nothing
    code = runner.run_once(now or T0 + timedelta(minutes=minutes))
    return runner, code


def statuses(runner, name="j"):
    return [(s, st) for _, s, st, _ in
            sorted(runner.state.runs(name), key=lambda r: r[1])]


def test_fire_once_fires_only_latest(make_runner):
    runner, code = run_window(make_runner, job(misfire={"policy": "fire_once"}), 5)
    assert code == 1
    done = [s for s, st in statuses(runner) if st == "done"]
    dropped = [s for s, st in statuses(runner) if st == "dropped"]
    assert done == ["2026-01-01T00:05:00+00:00"]
    assert len(dropped) == 4
    assert len(runner.state.issue_events()) == 4


def test_catch_up_respects_max(make_runner):
    cfg = job(misfire={"policy": "catch_up", "max_catch_up": 2},
              overlap="allow")
    runner, code = run_window(make_runner, cfg, 5)
    assert code == 1
    done = sorted(s for s, st in statuses(runner) if st == "done")
    assert done == ["2026-01-01T00:04:00+00:00", "2026-01-01T00:05:00+00:00"]
    assert len([1 for _, st in statuses(runner) if st == "dropped"]) == 3


def test_drop_discards_whole_backlog(make_runner):
    runner, code = run_window(make_runner, job(misfire={"policy": "drop"}), 5)
    assert code == 1
    assert [st for _, st in statuses(runner)] == ["dropped"] * 5


def test_drop_still_fires_single_on_time_run(make_runner):
    runner, code = run_window(make_runner, job(misfire={"policy": "drop"}), 1)
    assert code == 0
    assert [st for _, st in statuses(runner)] == ["done"]


def test_max_delay_drops_old_debt(make_runner):
    cfg = job(misfire={"policy": "catch_up", "max_catch_up": 10},
              max_delay_sec=120, overlap="allow")
    runner, code = run_window(make_runner, cfg, 10)
    assert code == 1
    done = sorted(s for s, st in statuses(runner) if st == "done")
    # 00:08/00:09/00:10 are within 120s of 00:10 (age <= max_delay);
    # 00:01-00:07 are older and dropped.
    assert done == ["2026-01-01T00:08:00+00:00",
                    "2026-01-01T00:09:00+00:00",
                    "2026-01-01T00:10:00+00:00"]
    assert len([1 for _, st in statuses(runner) if st == "dropped"]) == 7


def test_no_backlog_no_issues(make_runner):
    runner = make_runner([job()])
    assert runner.run_once(T0) == 0
    assert runner.run_once(T0 + timedelta(minutes=1)) == 0
    assert runner.run_once(T0 + timedelta(minutes=2)) == 0
    assert [st for _, st in statuses(runner)] == ["done", "done"]
