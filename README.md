# sched — 单机任务调度核心

纯标准库实现（`zoneinfo` / `sqlite3` / `concurrent.futures`），无第三方依赖。
Python 3.14+。

```
src/sched/{cronexpr,nextfire,state,runner,cli,__main__}.py
tests/        # pytest 套件
```

运行方式（包在 `src/` 下）：

```bash
export PYTHONPATH=src
python3 -m sched validate '*/15 0-6 1,15 jan,mar mon-fri'
python3 -m sched next --expr '30 2 * * *' --tz America/New_York \
    --after 2026-03-07T00:00:00+00:00 --count 3 --gap-policy skip
python3 -m sched run --config jobs.json --once
python3 -m sched inspect --db sched.db
python3 -m pytest tests/ -q
```

退出码：`0` 正常；`1` 有 overlap 冲突或欠账被 drop；`2` 用法或配置错误。

## cron 表达式

5 段（分 时 日 月 周），支持 `*`、列表 `,`、范围 `-`、步长 `/`（含 `a/n` 表示
a-max 步进）、月份名 `JAN`-`DEC`、星期名 `SUN`-`SAT`（大小写不敏感，`0` 和
`7` 都是周日），以及别名 `@hourly @daily @midnight @weekly @monthly
@yearly @annually`。

校验失败抛出 `CronError`，携带 `field_index`（第几段，0 起）、`token`
（出错的 token）、`reason`（原因），不会向调用方抛原生异常：

```
field 1 (minute): invalid token '99': value 99 out of range [0, 59]
```

### 日/周 规则（POSIX 语义）

- `dom` 与 `dow` **都受限**（都不是 `*`）：任一天满足即触发（**或**，并集）。
- **只有一边受限**：由受限的一边决定（另一边 `*` 恒真，等价于**与**）。

例：`0 9 15 * 1` 在 2026 年 7 月触发于 7/6、7/13、7/15、7/20、7/27
（周一 6/13/20/27 与 15 日的并集）。见 `tests/test_dom_dow.py`。

## 触发时间与 DST

`next_fire(expr, tz, after, count=N, gap_policy=...)` 返回接下来 N 个 aware
datetime（严格晚于 `after`）。调度按**本地墙钟字段**级联搜索，再对候选时刻做
DST 解析：

- **普通时刻**：直接使用。
- **秋季回拨（歧义时刻）**：取**第一次出现**（`fold=0`，即回拨前的偏移，
  如纽约取 EDT/-04:00）。同一歧义时刻只触发一次，不会重复点火。
- **春季前跳（不存在的时刻）**：按 `gap_policy` 三选一：
  - `skip`：本次不触发，继续找下一个匹配时刻；
  - `shift_forward`：平移到空洞结束后的第一个合法本地时刻
    （如 02:30 → 03:00）；
  - `use_first_utc`：用跳变**前**的 UTC 偏移解释墙钟时间
    （如 02:30 EST = 03:30 EDT）。

### 取舍一：DST 默认策略选 `skip`

理由：(1) 只在表达式真正匹配的时刻点火，绝不发明表达式之外的时刻——
`shift_forward`/`use_first_utc` 都会在 03:00/03:30 这种 cron 并未声明的
时刻触发，对审计和幂等都不友好；(2) 与 Vixie cron/cronie 对春季空洞的
主流处理一致，用户预期最低惊讶；(3) 空洞每年每时区只发生一次，丢一次的
代价远小于"在意想不到的时刻跑一次"的代价。需要准点执行的任务（如计费）
应显式配置 `shift_forward`。

### 取舍二：misfire 默认策略选 `fire_once`

理由：调度器宕机期间的欠账通常**不需要逐次补齐**——大多数任务（备份、
清理、报表）是幂等或近幂等的，补跑最新一次即可恢复世界状态；`catch_up`
会在长时间宕机后引发"补跑风暴"（N 个任务同时启动争抢资源），`drop` 又
会让欠账无声消失。`fire_once` 是两者折中：至多补一次、只补最新，被丢弃
的欠账全部记事件可审计。确实需要逐次补齐的（如按次的批处理）再显式开
`catch_up` 并设 `max_catch_up` 上限。

## jobs.json

```json
{
  "db": "sched.db",
  "clock_rewind_threshold_sec": 5,
  "defaults": {"timeout_sec": 60, "max_delay_sec": 3600},
  "jobs": [
    {
      "name": "backup",
      "expr": "0 3 * * *",
      "tz": "Asia/Shanghai",
      "cmd": "/usr/local/bin/backup.sh",
      "timeout_sec": 600,
      "misfire": {"policy": "catch_up", "max_catch_up": 3},
      "overlap": "forbid",
      "gap_policy": "skip"
    }
  ]
}
```

- **misfire**：`fire_once`（默认，只补最新一次）/ `catch_up`（最多补
  `max_catch_up` 次）/ `drop`（欠账全丢，单次准点仍触发）。超过
  `max_delay_sec` 的欠账**一律 drop** 并记 `misfire_drop` 事件。
- **overlap**：`forbid`（默认，上次未跑完则跳过并记 `overlap_conflict`）/
  `allow`（并发跑）/ `queue`（排队串行）。调度时刻永远由墙钟表达式算出，
  不从上次结束时间累加，慢任务不会把后续触发推歪。
- **时钟回跳**：每次调度比较墙钟与单调时钟的推进量，偏差超过
  `clock_rewind_threshold_sec` 时记 `clock_rewind` 事件并暂停本轮补跑，
  避免回拨后的补跑风暴。

## 状态与幂等

SQLite（WAL 模式）。运行记录表 `runs` 的主键是 **`(job, scheduled_time)`**
而不是自增 id，原因：

1. **幂等是调度语义，不是行号**。同一 scheduled_time 应否再执行，是业务
   问题；自增 id 回答不了"这次触发跑过没有"，还得再加唯一索引，等于把
   `(job, scheduled_time)` 又声明了一遍。
2. **崩溃恢复免费**。执行前先 `INSERT OR IGNORE` 认领（状态 `running`），
   进程在执行中途被杀，重启后主键冲突天然阻止同一 scheduled_time 重复
   执行；遗留的 `running` 行在启动时改记为 `interrupted`，既不重跑也不
   漏记。自增 id 方案在崩溃点会留下语义不明的孤儿行。
3. **跨进程/跨重启可判定**。任何时刻、任何进程都能用纯查询回答
   "job X 的 T 时刻到底跑没跑"，无需额外状态机。

### 故障注入钩子

模拟"执行中崩溃"有两种等价方式（测试见 `tests/test_crash.py`）：

- 环境变量 `SCHED_CRASH_AFTER_CLAIM=1`（或 `*`，或逗号分隔的 job 名）；
- 构造函数参数 `Runner(cfg, crash_after_claim=...)`。

钩子生效时，进程在**认领运行记录之后、命令体执行之前**以
`os._exit(42)` 自杀，等价于执行中途被 `kill -9`。

## inspect

`python3 -m sched inspect --db sched.db` 打印全部事件（欠账 drop、overlap
冲突、时钟回跳）以及被丢弃/跳过的运行记录；存在 drop 或冲突事件时退出码
为 1，否则为 0。
