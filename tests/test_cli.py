import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"))


def cli(*args):
    return subprocess.run([sys.executable, "-m", "sched", *args],
                          env=ENV, capture_output=True, text=True, cwd=ROOT)


def test_validate_ok():
    r = cli("validate", "*/5 0-6 * jan mon-fri")
    assert r.returncode == 0 and "OK" in r.stdout


def test_validate_invalid_exit_2():
    r = cli("validate", "99 * * * *")
    assert r.returncode == 2
    assert "field 1" in r.stderr and "99" in r.stderr


def test_next_command():
    r = cli("next", "--expr", "0 9 * * *", "--tz", "Asia/Shanghai",
            "--after", "2026-06-01T00:00:00+00:00", "--count", "2")
    assert r.returncode == 0
    assert r.stdout.splitlines() == [
        "2026-06-01T09:00:00+08:00",
        "2026-06-02T09:00:00+08:00",
    ]


def test_next_bad_expr_exit_2():
    r = cli("next", "--expr", "nope", "--tz", "UTC")
    assert r.returncode == 2


def test_run_bad_config_exit_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"jobs": [{"name": "x"}]}))
    r = cli("run", "--config", str(bad), "--once")
    assert r.returncode == 2


def test_run_and_inspect_exit_codes(tmp_path):
    db = tmp_path / "sched.db"
    cfg = tmp_path / "jobs.json"
    cfg.write_text(json.dumps({
        "db": str(db),
        "jobs": [{"name": "j", "expr": "* * * * *", "tz": "UTC",
                  "cmd": "true"}],
    }))
    # First tick: initialise checkpoint.
    assert cli("run", "--config", str(cfg), "--once").returncode == 0
    # Rewind checkpoint far into the past to force dropped backlog.
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE job_state SET last_checked=? WHERE job='j'", (old,))
    conn.commit()
    conn.close()
    # fire_once default: backlog dropped -> exit 1.
    assert cli("run", "--config", str(cfg), "--once").returncode == 1
    # inspect reports the drops -> exit 1.
    r = cli("inspect", "--db", str(db))
    assert r.returncode == 1
    assert "misfire_drop" in r.stdout


def test_inspect_clean_db_exit_0(tmp_path):
    db = tmp_path / "sched.db"
    cfg = tmp_path / "jobs.json"
    cfg.write_text(json.dumps({
        "db": str(db),
        "jobs": [{"name": "j", "expr": "* * * * *", "tz": "UTC",
                  "cmd": "true"}],
    }))
    assert cli("run", "--config", str(cfg), "--once").returncode == 0
    r = cli("inspect", "--db", str(db))
    assert r.returncode == 0
