import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sched.runner import Runner  # noqa: E402


@pytest.fixture
def make_runner(tmp_path):
    runners = []

    def factory(jobs, runner_kwargs=None, **config):
        cfg = {"jobs": jobs, "db": str(tmp_path / "sched.db"),
               "clock_rewind_threshold_sec": 10**9}
        cfg.update(config)
        runner = Runner(cfg, **(runner_kwargs or {}))
        runners.append(runner)
        return runner

    yield factory
    for r in runners:
        r.close()


def job(name="j", expr="* * * * *", tz="UTC", cmd="true", **kw):
    base = {"name": name, "expr": expr, "tz": tz, "cmd": cmd}
    base.update(kw)
    return base
