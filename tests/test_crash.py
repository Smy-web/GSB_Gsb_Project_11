"""Kill the process mid-execution; restart must neither re-execute the same
scheduled_time nor lose its record."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent


def _config(tmp_path, marker):
    cfg = {
        "db": str(tmp_path / "sched.db"),
        "jobs": [{
            "name": "boom",
            "expr": "* * * * *",
            "tz": "UTC",
            "cmd": f"printf run >> {marker}",
        }],
    }
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(cfg))
    return path


def _run_cli(config_path, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "sched", "run", "--config", str(config_path),
         "--once"],
        env=env, capture_output=True, text=True, cwd=ROOT)


def _rewind_checkpoint(db_path, minutes=2):
    """Move the checkpoint a couple of minutes into the past so that the
    next pass has exactly one run to fire (fire_once default)."""
    when = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE job_state SET last_checked=? WHERE job='boom'", (when,))
    conn.commit()
    conn.close()


def test_crash_after_claim_no_rerun_no_lost_record(tmp_path):
    marker = tmp_path / "marker"
    config = _config(tmp_path, marker)
    db = tmp_path / "sched.db"

    first = _run_cli(config)  # clean first tick: initialises checkpoint only
    assert first.returncode == 0, first.stderr
    _rewind_checkpoint(db)

    # This pass claims the run row, then dies as if killed mid-execution.
    crashed = _run_cli(config, {"SCHED_CRASH_AFTER_CLAIM": "1"})
    assert crashed.returncode == 42
    assert not marker.exists()  # command body never executed

    # The claim row survived the kill: nothing is lost.  (The rewound
    # checkpoint produced two due times; fire_once dropped the older one
    # and claimed the latest, which is the row left "running" by the kill.)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT status FROM runs WHERE job='boom' ORDER BY scheduled_time"
    ).fetchall()
    conn.close()
    assert rows == [("dropped",), ("running",)]

    # Restart: the claimed row is marked interrupted and its scheduled_time
    # is NOT executed again.
    again = _run_cli(config)
    assert again.returncode == 0, again.stderr  # no new work due
    assert not marker.exists()
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT status FROM runs WHERE job='boom' ORDER BY scheduled_time"
    ).fetchall()
    conn.close()
    assert rows == [("dropped",), ("interrupted",)]


def test_crash_hook_scoped_to_named_jobs(tmp_path):
    marker = tmp_path / "marker"
    config = _config(tmp_path, marker)
    db = tmp_path / "sched.db"
    assert _run_cli(config).returncode == 0
    _rewind_checkpoint(db)
    # Hook names a different job -> no crash, command executes.
    # (exit 1: the rewound checkpoint also produced one dropped backlog entry)
    ok = _run_cli(config, {"SCHED_CRASH_AFTER_CLAIM": "someone_else"})
    assert ok.returncode == 1
    assert marker.read_text() == "run"
