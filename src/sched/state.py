"""SQLite-backed run state (WAL mode).

Idempotency key: ``(job, scheduled_time)``.  A run row is claimed with
``INSERT OR IGNORE`` *before* the command starts, so a process killed
mid-execution leaves a ``running`` row behind; on restart that row is
marked ``interrupted`` and the same ``scheduled_time`` is never executed
again, nor is its record lost.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    job            TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    status         TEXT NOT NULL,
    exit_code      INTEGER,
    started_at     TEXT,
    finished_at    TEXT,
    PRIMARY KEY (job, scheduled_time)
);
CREATE TABLE IF NOT EXISTS job_state (
    job          TEXT PRIMARY KEY,
    last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    job    TEXT,
    kind   TEXT NOT NULL,
    detail TEXT
);
"""

ISSUE_KINDS = ("misfire_drop", "overlap_conflict")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def from_str(text: str) -> datetime:
    return datetime.fromisoformat(text)


class StateStore:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, isolation_level=None,
                                    check_same_thread=False)
        self._lock = threading.RLock()
        self._execute("PRAGMA journal_mode=WAL")
        self._execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def _execute(self, *args):
        with self._lock:
            return self.conn.execute(*args)

    def close(self):
        with self._lock:
            self.conn.close()

    # -- run records -----------------------------------------------------
    def claim_run(self, job: str, scheduled: datetime, status: str = "running") -> bool:
        """Atomically claim (job, scheduled_time).  Returns True if this
        process won the claim, False if the run was already recorded."""
        cur = self._execute(
            "INSERT OR IGNORE INTO runs(job, scheduled_time, status, started_at)"
            " VALUES (?, ?, ?, ?)",
            (job, to_str(scheduled), status, _utcnow()),
        )
        return cur.rowcount == 1

    def has_run(self, job: str, scheduled: datetime) -> bool:
        cur = self._execute(
            "SELECT 1 FROM runs WHERE job=? AND scheduled_time=?",
            (job, to_str(scheduled)),
        )
        return cur.fetchone() is not None

    def finish_run(self, job: str, scheduled: datetime, status: str,
                   exit_code: int | None):
        self._execute(
            "UPDATE runs SET status=?, exit_code=?, finished_at=?"
            " WHERE job=? AND scheduled_time=?",
            (status, exit_code, _utcnow(), job, to_str(scheduled)),
        )

    def set_run_status(self, job: str, scheduled: datetime, status: str):
        self._execute(
            "UPDATE runs SET status=? WHERE job=? AND scheduled_time=?",
            (status, job, to_str(scheduled)),
        )

    def recover_interrupted(self) -> int:
        """Mark rows left in 'running' by a killed process as interrupted.
        They are never re-executed (the primary key still blocks re-claim)."""
        cur = self._execute(
            "UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'",
            (_utcnow(),),
        )
        return cur.rowcount

    def run_count(self, job: str | None = None) -> int:
        if job is None:
            return self._execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        return self._execute(
            "SELECT COUNT(*) FROM runs WHERE job=?", (job,)
        ).fetchone()[0]

    def runs(self, job: str | None = None):
        sql = "SELECT job, scheduled_time, status, exit_code FROM runs"
        args: tuple = ()
        if job is not None:
            sql += " WHERE job=?"
            args = (job,)
        return self._execute(sql + " ORDER BY job, scheduled_time", args).fetchall()

    # -- scheduler checkpoint ---------------------------------------------
    def get_last_checked(self, job: str) -> datetime | None:
        row = self._execute(
            "SELECT last_checked FROM job_state WHERE job=?", (job,)
        ).fetchone()
        return from_str(row[0]) if row else None

    def set_last_checked(self, job: str, when: datetime):
        self._execute(
            "INSERT INTO job_state(job, last_checked) VALUES (?, ?)"
            " ON CONFLICT(job) DO UPDATE SET last_checked=excluded.last_checked",
            (job, to_str(when)),
        )

    # -- events -------------------------------------------------------------
    def add_event(self, kind: str, job: str | None = None, detail: str = ""):
        self._execute(
            "INSERT INTO events(ts, job, kind, detail) VALUES (?, ?, ?, ?)",
            (_utcnow(), job, kind, detail),
        )

    def events(self):
        return self._execute(
            "SELECT ts, job, kind, detail FROM events ORDER BY id"
        ).fetchall()

    def issue_events(self):
        return self._execute(
            "SELECT ts, job, kind, detail FROM events"
            " WHERE kind IN ('misfire_drop', 'overlap_conflict') ORDER BY id"
        ).fetchall()
