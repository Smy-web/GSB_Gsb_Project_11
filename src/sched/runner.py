"""Job runner: scheduling pass, misfire/overlap policies, crash recovery.

Scheduling is always computed from the wall-clock cron expression and the
last checkpoint -- never accumulated from a job's previous finish time, so
slow jobs cannot skew later fire times.

Fault-injection hook (documented in README): set env var
``SCHED_CRASH_AFTER_CLAIM`` to ``1``/``*`` or a comma-separated list of job
names, or pass ``crash_after_claim`` to the constructor.  The process then
dies via ``os._exit(42)`` immediately after a run row is claimed, simulating
a kill in the middle of execution.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .cronexpr import CronError, parse
from .nextfire import GAP_POLICIES, next_fire
from .state import StateStore

MISFIRE_POLICIES = ("fire_once", "catch_up", "drop")
OVERLAP_POLICIES = ("forbid", "allow", "queue")
_DUE_BATCH = 256
_MAX_DUE = 100_000


class ConfigError(ValueError):
    """Invalid scheduler configuration (CLI exit code 2)."""


@dataclass
class JobConfig:
    name: str
    expr: str
    tz: str
    cmd: str
    timeout_sec: float = 60.0
    misfire_policy: str = "fire_once"
    max_catch_up: int = 3
    overlap: str = "forbid"
    max_delay_sec: float = 3600.0
    gap_policy: str = "skip"


def _job_from_dict(raw: dict, defaults: dict) -> JobConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"job entry must be an object, got {raw!r}")
    for key in ("name", "expr", "tz", "cmd"):
        if key not in raw:
            raise ConfigError(f"job is missing required key {key!r}: {raw!r}")
    name = str(raw["name"])
    expr = str(raw["expr"])
    try:
        parse(expr)
    except CronError as exc:
        raise ConfigError(f"job {name!r}: bad cron expression: {exc}") from None
    tz = str(raw["tz"])
    try:
        ZoneInfo(tz)
    except Exception:
        raise ConfigError(f"job {name!r}: unknown timezone {tz!r}") from None
    misfire = raw.get("misfire", {})
    if isinstance(misfire, str):
        misfire = {"policy": misfire}
    policy = misfire.get("policy", defaults.get("misfire_policy", "fire_once"))
    if policy not in MISFIRE_POLICIES:
        raise ConfigError(f"job {name!r}: unknown misfire policy {policy!r}")
    overlap = raw.get("overlap", defaults.get("overlap", "forbid"))
    if overlap not in OVERLAP_POLICIES:
        raise ConfigError(f"job {name!r}: unknown overlap policy {overlap!r}")
    gap_policy = raw.get("gap_policy", defaults.get("gap_policy", "skip"))
    if gap_policy not in GAP_POLICIES:
        raise ConfigError(f"job {name!r}: unknown gap policy {gap_policy!r}")
    return JobConfig(
        name=name,
        expr=expr,
        tz=tz,
        cmd=str(raw["cmd"]),
        timeout_sec=float(raw.get("timeout_sec", defaults.get("timeout_sec", 60))),
        misfire_policy=policy,
        max_catch_up=int(misfire.get("max_catch_up",
                                     defaults.get("max_catch_up", 3))),
        overlap=overlap,
        max_delay_sec=float(raw.get("max_delay_sec",
                                    defaults.get("max_delay_sec", 3600))),
        gap_policy=gap_policy,
    )


def load_config(config: dict) -> tuple[list[JobConfig], dict]:
    if not isinstance(config, dict) or not isinstance(config.get("jobs"), list):
        raise ConfigError("config must be an object with a 'jobs' list")
    defaults = config.get("defaults", {})
    jobs = [_job_from_dict(j, defaults) for j in config["jobs"]]
    names = [j.name for j in jobs]
    if len(set(names)) != len(names):
        raise ConfigError("duplicate job names in config")
    return jobs, config


class Runner:
    def __init__(self, config: dict, db_path: str | None = None,
                 monotonic_fn=time.monotonic, crash_after_claim=None):
        self.jobs, raw = load_config(config)
        self.db_path = db_path or raw.get("db", "sched.db")
        self.state = StateStore(self.db_path)
        self.state.recover_interrupted()
        self.rewind_threshold = float(raw.get("clock_rewind_threshold_sec", 5.0))
        self._monotonic = monotonic_fn
        self._executor = ThreadPoolExecutor(max_workers=int(raw.get("max_workers", 8)))
        self._meta_lock = threading.Lock()
        self._forbid_locks = {j.name: threading.Lock() for j in self.jobs}
        self._queues: dict[str, deque] = {j.name: deque() for j in self.jobs}
        self._queue_active = {j.name: False for j in self.jobs}
        self._futures: list = []
        self._last_wall: datetime | None = None
        self._last_mono: float | None = None
        self._issues = 0
        if crash_after_claim is None:
            env = os.environ.get("SCHED_CRASH_AFTER_CLAIM", "")
            crash_after_claim = env if env else False
        self._crash_after_claim = crash_after_claim

    # -- public API --------------------------------------------------------
    def close(self):
        self._executor.shutdown(wait=True)
        self.state.close()

    def run_once(self, now: datetime | None = None) -> int:
        """Run one scheduling pass.  Returns 0, or 1 if any backlog was
        dropped or any overlap conflict occurred."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self._issues = 0
        mono = self._monotonic()
        if self._clock_rewound(now, mono):
            self._last_wall, self._last_mono = now, mono
            return 0
        self._last_wall, self._last_mono = now, mono
        for job in self.jobs:
            self._process_job(job, now)
        self._wait_all()
        return 1 if self._issues else 0

    # -- internals ---------------------------------------------------------
    def _clock_rewound(self, now: datetime, mono: float) -> bool:
        if self._last_wall is None:
            return False
        wall_dt = (now - self._last_wall).total_seconds()
        mono_dt = mono - (self._last_mono or 0.0)
        if wall_dt < -self.rewind_threshold or abs(wall_dt - mono_dt) > self.rewind_threshold:
            self.state.add_event(
                "clock_rewind", None,
                f"wall advanced {wall_dt:.3f}s but monotonic advanced "
                f"{mono_dt:.3f}s; catch-up paused to avoid a fire storm",
            )
            for job in self.jobs:
                self.state.set_last_checked(job.name, now)
            return True
        return False

    def _due_times(self, job: JobConfig, last: datetime, now: datetime):
        due = []
        cursor = last
        while len(due) < _MAX_DUE:
            batch = next_fire(job.expr, job.tz, cursor, count=_DUE_BATCH,
                              gap_policy=job.gap_policy)
            for t in batch:
                if t > now:
                    return due
                due.append(t)
            cursor = batch[-1]
        return due

    def _process_job(self, job: JobConfig, now: datetime):
        last = self.state.get_last_checked(job.name)
        if last is None:
            self.state.set_last_checked(job.name, now)
            return
        if last >= now:
            return
        due = self._due_times(job, last, now)
        self.state.set_last_checked(job.name, now)
        if not due:
            return
        fresh, stale = [], []
        for t in due:
            if (now - t).total_seconds() > job.max_delay_sec:
                stale.append(t)
            else:
                fresh.append(t)
        for t in stale:
            self._drop(job, t, f"older than max_delay_sec={job.max_delay_sec:g}")
        if job.misfire_policy == "fire_once":
            keep, drop = fresh[-1:], fresh[:-1]
        elif job.misfire_policy == "catch_up":
            keep = fresh[-job.max_catch_up:] if job.max_catch_up > 0 else []
            drop = fresh[: len(fresh) - len(keep)]
        else:  # drop: never catch up a backlog; a single on-time run still fires
            keep, drop = (fresh, []) if len(fresh) <= 1 else ([], fresh)
        for t in drop:
            self._drop(job, t, f"misfire policy {job.misfire_policy!r}")
        for t in keep:
            self._submit(job, t)

    def _drop(self, job: JobConfig, t: datetime, reason: str):
        self.state.claim_run(job.name, t, status="dropped")
        self.state.set_run_status(job.name, t, "dropped")
        self.state.add_event("misfire_drop", job.name,
                             f"{t.isoformat()} dropped: {reason}")
        self._issues += 1

    def _maybe_crash(self, job: JobConfig):
        hook = self._crash_after_claim
        if not hook:
            return
        if hook is True or hook in ("1", "*"):
            os._exit(42)
        names = [n.strip() for n in str(hook).split(",") if n.strip()]
        if job.name in names:
            os._exit(42)

    def _submit(self, job: JobConfig, t: datetime):
        if job.overlap == "forbid":
            lock = self._forbid_locks[job.name]
            if not lock.acquire(blocking=False):
                self.state.claim_run(job.name, t, status="skipped_overlap")
                self.state.set_run_status(job.name, t, "skipped_overlap")
                self.state.add_event(
                    "overlap_conflict", job.name,
                    f"{t.isoformat()} skipped: previous run still active")
                self._issues += 1
                return
            if not self.state.claim_run(job.name, t):
                lock.release()
                return
            self._maybe_crash(job)
            self._futures.append(
                self._executor.submit(self._exec, job, t, lock))
        elif job.overlap == "allow":
            if not self.state.claim_run(job.name, t):
                return
            self._maybe_crash(job)
            self._futures.append(
                self._executor.submit(self._exec, job, t, None))
        else:  # queue
            if not self.state.claim_run(job.name, t, status="queued"):
                return
            self._maybe_crash(job)
            with self._meta_lock:
                self._queues[job.name].append(t)
                if not self._queue_active[job.name]:
                    self._queue_active[job.name] = True
                    self._futures.append(
                        self._executor.submit(self._queue_worker, job))

    def _queue_worker(self, job: JobConfig):
        while True:
            with self._meta_lock:
                if not self._queues[job.name]:
                    self._queue_active[job.name] = False
                    return
                t = self._queues[job.name].popleft()
            self.state.set_run_status(job.name, t, "running")
            self._exec(job, t, None)

    def _exec(self, job: JobConfig, t: datetime, lock):
        try:
            try:
                proc = subprocess.run(
                    job.cmd, shell=True, timeout=job.timeout_sec,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                status = "done" if proc.returncode == 0 else "error"
                self.state.finish_run(job.name, t, status, proc.returncode)
            except subprocess.TimeoutExpired:
                self.state.finish_run(job.name, t, "timeout", None)
        finally:
            if lock is not None:
                lock.release()

    def _wait_all(self):
        futures, self._futures = self._futures, []
        for f in futures:
            f.result()
