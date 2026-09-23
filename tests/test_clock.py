from datetime import datetime, timedelta, timezone

from conftest import job

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_clock_rewind_pauses_catch_up(make_runner):
    clock = FakeClock()
    runner = make_runner([job()], clock_rewind_threshold_sec=5,
                        runner_kwargs={"monotonic_fn": clock})
    assert runner.run_once(T0) == 0

    # Wall clock jumps 10 min forward but monotonic only 0.5s: rewind/jump.
    clock.now += 0.5
    code = runner.run_once(T0 + timedelta(minutes=10))
    assert code == 0
    assert runner.state.runs() == []  # no catch-up storm
    kinds = [k for _, _, k, _ in runner.state.events()]
    assert kinds == ["clock_rewind"]

    # Checkpoint moved to the jump target: next normal tick fires once.
    clock.now += 60.0
    assert runner.run_once(T0 + timedelta(minutes=11)) == 0
    assert len(runner.state.runs()) == 1


def test_backward_wall_jump_detected(make_runner):
    clock = FakeClock()
    runner = make_runner([job()], clock_rewind_threshold_sec=5,
                        runner_kwargs={"monotonic_fn": clock})
    assert runner.run_once(T0) == 0
    clock.now += 1.0
    assert runner.run_once(T0 - timedelta(minutes=5)) == 0
    kinds = [k for _, _, k, _ in runner.state.events()]
    assert kinds == ["clock_rewind"]
    assert runner.state.runs() == []


def test_consistent_clocks_no_event(make_runner):
    clock = FakeClock()
    runner = make_runner([job()], clock_rewind_threshold_sec=5,
                        runner_kwargs={"monotonic_fn": clock})
    assert runner.run_once(T0) == 0
    clock.now += 60.0
    assert runner.run_once(T0 + timedelta(minutes=1)) == 0
    assert runner.state.events() == []
    assert len(runner.state.runs()) == 1
