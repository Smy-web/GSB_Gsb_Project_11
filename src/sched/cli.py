"""Command line interface.

Exit codes: 0 = ok, 1 = conflicts or dropped backlog, 2 = usage/config error.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .cronexpr import CronError, parse
from .nextfire import GAP_POLICIES, next_fire
from .runner import ConfigError, Runner
from .state import StateStore

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sched", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a cron expression")
    v.add_argument("expr")

    n = sub.add_parser("next", help="print upcoming fire times")
    n.add_argument("--expr", required=True)
    n.add_argument("--tz", required=True)
    n.add_argument("--after", default=None,
                   help="ISO-8601 datetime (naive treated as UTC); default: now")
    n.add_argument("--count", type=int, default=1)
    n.add_argument("--gap-policy", default="skip", choices=GAP_POLICIES)

    r = sub.add_parser("run", help="run the scheduler")
    r.add_argument("--config", required=True, help="path to jobs.json")
    r.add_argument("--db", default=None, help="override state db path")
    r.add_argument("--once", action="store_true",
                   help="run a single scheduling pass and exit")

    i = sub.add_parser("inspect", help="print drops, skips and conflict events")
    i.add_argument("--db", required=True)
    return p


def _cmd_validate(args) -> int:
    try:
        spec = parse(args.expr)
    except CronError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"OK: {spec.source}")
    return EXIT_OK


def _cmd_next(args) -> int:
    try:
        if args.after is None:
            after = datetime.now(timezone.utc)
        else:
            after = datetime.fromisoformat(args.after)
            if after.tzinfo is None:
                after = after.replace(tzinfo=timezone.utc)
        times = next_fire(args.expr, args.tz, after, count=args.count,
                          gap_policy=args.gap_policy)
    except CronError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # e.g. unknown timezone
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    for t in times:
        print(t.isoformat())
    return EXIT_OK


def _cmd_run(args) -> int:
    try:
        with open(args.config, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load config: {exc}", file=sys.stderr)
        return EXIT_USAGE
    runner = None
    try:
        runner = Runner(config, db_path=args.db)
    except (ConfigError, CronError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.once:
            return runner.run_once()
        return _run_loop(runner)
    finally:
        runner.close()


def _run_loop(runner: Runner) -> int:
    import time

    worst = EXIT_OK
    try:
        while True:
            code = runner.run_once()
            worst = max(worst, code)
            time.sleep(1.0)
    except KeyboardInterrupt:
        return worst


def _cmd_inspect(args) -> int:
    try:
        store = StateStore(args.db)
    except Exception as exc:
        print(f"ERROR: cannot open db: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        events = store.events()
        if not events:
            print("no events")
        for ts, job, kind, detail in events:
            print(f"{ts}  {kind:<18}  {job or '-':<20}  {detail}")
        dropped = [r for r in store.runs() if r[2] in ("dropped", "skipped_overlap")]
        if dropped:
            print("\ndropped/skipped runs:")
            for job, sched, status, _ in dropped:
                print(f"  {job}  {sched}  {status}")
        issues = [e for e in events if e[2] in ("misfire_drop", "overlap_conflict")]
        return EXIT_ISSUES if issues else EXIT_OK
    finally:
        store.close()


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    handler = {
        "validate": _cmd_validate,
        "next": _cmd_next,
        "run": _cmd_run,
        "inspect": _cmd_inspect,
    }[args.command]
    return handler(args)
