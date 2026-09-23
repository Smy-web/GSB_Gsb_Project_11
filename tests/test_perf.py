import time
from datetime import datetime, timezone

from sched import next_fire


def test_100k_next_fire_under_2_seconds():
    after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    start = time.perf_counter()
    for _ in range(100_000):
        after = next_fire("*/7 * * * *", "Asia/Shanghai", after, 1)[0]
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"100k next_fire took {elapsed:.2f}s"
