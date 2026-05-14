# 12 生产运行与调度部署实现说明

## 1. 功能：scheduler 常驻进程

### 1.1 发起方式

由 systemd 或人工运维命令启动：

```bash
python -m scripts.run_scheduler
```

### 1.2 入口文件

`scripts/run_scheduler.py`

入口方法：

`main()`

### 1.3 核心调用链路

```text
scripts/run_scheduler.py::main
    ↓
app/scheduler/config.py::build_scheduler_runtime_config
    ↓
app/scheduler/runner.py::run_scheduler_forever
    ↓
app/scheduler/runner.py::SchedulerRunner.run_once
    ↓
app/scheduler/slot_state.py::RedisSchedulerSlotStore.acquire_slot_for_run
    ↓
app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
```

### 1.4 配置

读取 `.env` 中的 scheduler 配置：

```env
SCHEDULER_ENABLED=true
SCHEDULER_POLL_INTERVAL_SECONDS=30
SCHEDULER_RUNNING_LOCK_TTL_SECONDS=1800
SCHEDULER_COMPLETED_MARKER_TTL_SECONDS=259200
SCHEDULER_STATUS_MARKER_TTL_SECONDS=86400
SCHEDULER_SLOT_LOG_COOLDOWN_SECONDS=300
KLINE_4H_INCREMENTAL_COLLECT_ENABLED=true
KLINE_4H_INCREMENTAL_COLLECT_SYMBOL=BTCUSDT
KLINE_4H_INCREMENTAL_COLLECT_INTERVAL=4h
KLINE_4H_INCREMENTAL_COLLECT_LIMIT=6
KLINE_4H_INCREMENTAL_COLLECT_UTC_MINUTES_AFTER_CLOSE=5
DAILY_KLINE_INTEGRITY_UTC_TIME=00:30
```

11 继续复用既有配置：

```env
DAILY_KLINE_INTEGRITY_ENABLED=true
DAILY_KLINE_INTEGRITY_SYMBOL=BTCUSDT
DAILY_KLINE_INTEGRITY_INTERVAL=4h
DAILY_KLINE_INTEGRITY_LIMIT=100
DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS=true
```

`DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS` 只影响 manual CLI 成功健康通知，不关闭 scheduler 每日结果通知。

12 scheduler 每日复核时间只读取：

```env
DAILY_KLINE_INTEGRITY_UTC_TIME=00:30
```

旧的 `DAILY_KLINE_INTEGRITY_SCHEDULE_HOUR_UTC` 和
`DAILY_KLINE_INTEGRITY_SCHEDULE_MINUTE_UTC` 不再作为配置字段存在，也不作为 scheduler 调度依据。

### 1.5 调度循环

`SchedulerRunner.run_forever()` 使用 UTC sleep 循环：

```text
读取当前 UTC 时间
判断 09 是否已到达最近一个 4h slot，且尚未到下一个 4h slot
判断 11 是否已到达当天 DAILY_KLINE_INTEGRITY_UTC_TIME，且仍在 2 小时补跑窗口内
进入窗口后先检查 Redis completed marker
completed marker 存在时记录 skipped/completed，不执行 app job
completed marker 不存在时尝试写 running lock
running lock 成功时调用对应 app job
running lock 正常存在时跳过并做日志冷却
running lock 异常或超出允许运行时间时标记 stale/expired，并安全重试一次
Redis 状态无法判断时不执行任务并发送 scheduler 系统异常通知
sleep SCHEDULER_POLL_INTERVAL_SECONDS
```

09 的调度窗口不再只依赖 `max(60, SCHEDULER_POLL_INTERVAL_SECONDS)`。例如 00:05 slot 到达后，
如果 scheduler 在 00:06 才启动，且 Redis completed marker
`scheduler:completed:kline_4h_incremental:2026-05-13T00:05Z` 尚不存在，runner 会尝试补跑该 slot；如果已经存在，则跳过。
该 slot 最晚只允许在下一个 4h slot 到来前补跑，例如 04:05 到达后不再补跑 00:05。

11 的每日复核在当天 `DAILY_KLINE_INTEGRITY_UTC_TIME` 到达后有有限补跑窗口，当前为 2 小时。
例如 00:30 slot 到达后，scheduler 在 00:31 启动且当天 Redis completed marker
`scheduler:completed:daily_kline_integrity:2026-05-13` 尚不存在时会执行一次；超过 02:30 后不再补跑当天每日检查。

这里的“补跑”只表示 scheduler 对错过短调度窗口的同一个时间槽进行一次延迟触发。
它不自动修复 K线，不自动执行 08 回补，不自动覆盖正式 K线，不自动交易。
是否执行仍由 Redis slot state 去重控制，确保同一 09 slot 或同一天 11 slot 不并发执行，且已完成 slot 不重复执行。

PRC 时间不参与调度判断。

## 2. 功能：Redis slot 状态去重

### 2.1 入口文件

`app/scheduler/slot_state.py`

入口方法：

`RedisSchedulerSlotStore.acquire_slot_for_run()`

`app/scheduler/execution_slot.py` 只保留兼容导出，不再承载 Redis 写入逻辑。

### 2.2 Redis key

09 增量采集 slot id 使用 UTC：

```text
2026-05-13T04:05Z
```

11 每日复核 slot id 使用 UTC 日期：

```text
2026-05-13
```

Redis key 分为三类：

```text
scheduler:running:<job>:<slot>
scheduler:completed:<job>:<slot>
scheduler:status:<job>:<slot>
```

示例：

```text
scheduler:running:kline_4h_incremental:2026-05-13T04:05Z
scheduler:completed:kline_4h_incremental:2026-05-13T04:05Z
scheduler:status:kline_4h_incremental:2026-05-13T04:05Z

scheduler:running:daily_kline_integrity:2026-05-13
scheduler:completed:daily_kline_integrity:2026-05-13
scheduler:status:daily_kline_integrity:2026-05-13
```

### 2.3 value 与 TTL

running lock 使用 Redis `SET NX EX`，只表示当前 slot 正在运行。默认 TTL：

```text
SCHEDULER_RUNNING_LOCK_TTL_SECONDS=1800
```

running lock value 是 JSON，至少包含：

```json
{
  "job": "kline_4h_incremental",
  "slot": "2026-05-13T04:05Z",
  "status": "running",
  "owner": "<hostname>:<pid>:<trace>",
  "token": "<uuid>",
  "created_at_utc": "2026-05-13T04:05:00Z",
  "updated_at_utc": "2026-05-13T04:05:00Z",
  "ttl_seconds": 1800
}
```

这里的 `token` 不是密钥，只用于 compare-and-release，防止释放其他进程的 running lock。

completed marker 只表示该 slot 已成功处理。默认 TTL：

```text
SCHEDULER_COMPLETED_MARKER_TTL_SECONDS=259200
```

completed marker value 是 JSON，至少包含：

```json
{
  "job": "kline_4h_incremental",
  "slot": "2026-05-13T04:05Z",
  "status": "completed",
  "owner": "<hostname>:<pid>:<trace>",
  "completed_at_utc": "2026-05-13T04:06:00Z",
  "source": "scheduler"
}
```

failed / skipped / blocked / stale / expired 使用 status marker 记录诊断结果。默认 TTL：

```text
SCHEDULER_STATUS_MARKER_TTL_SECONDS=86400
```

重复 skip 日志使用内存冷却，默认：

```text
SCHEDULER_SLOT_LOG_COOLDOWN_SECONDS=300
```

### 2.4 状态流程

执行一个 slot 时，runner 的状态流程如下：

```text
1. 检查 completed marker。
   存在：返回 skipped，lock_status=completed，不调用 09/11 job。

2. 检查 failed / skipped / blocked status marker。
   存在：返回 skipped，不在冷却窗口内重复刷屏。

3. 尝试写 scheduler:running:<job>:<slot>。
   成功：执行 job。

4. running lock 已存在。
   value 与 TTL 正常：返回 skipped，lock_status=running。
   value 非 JSON、TTL 不存在、TTL=-1、TTL<=0、TTL 大于当前 running TTL 配置、或 created_at_utc 超过允许运行时间：
   写 stale/expired status marker，原 running lock value 匹配时删除，并安全重试一次。

5. job 成功。
   删除 running lock，写 completed marker，返回 completed。

6. job 失败。
   写 failed status marker，删除 running lock，不写 completed marker。

7. job 被数据质量或业务规则阻断。
   写 blocked status marker，删除 running lock，不写 completed marker，不写正式 K线表。
```

slot state 只负责 scheduler 时间窗口去重，不替代 09 的 K线写入锁，也不替代 11 的复核锁。
有限补跑窗口内重复扫描或多实例误启动时，先检查同一个 Redis slot state；completed 时不重复调用 09/11 job，running 时不并发调用。
如果旧 slot 卡住，超过下一个 4h slot 后 runner 会根据 UTC 当前时间判断新的 slot，不会无限盯住旧 slot。

## 3. 功能：09 scheduler job

### 3.1 入口文件

`app/scheduler/jobs/kline_4h_incremental_collect.py`

入口方法：

`run_kline_4h_incremental_collect_job()`

### 3.2 调用链路

```text
app/scheduler/runner.py::SchedulerRunner.run_once
    ↓
app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
    ↓
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection
```

### 3.3 数据与副作用

09 job 只构造 scheduler 请求并调用 09 app service：

```text
trigger_source=scheduler
confirm_write=true
dry_run=false
notify_success=false
```

正式 K线 `data_source` 仍由 09 service 按既有规则映射为 `binance_rest_by_scheduler`。

09 job 不调用 `scripts.collect_4h_klines`，不直接请求 Binance，不直接写 `market_kline_4h`，不直接发送 Hermes，不绕过质量检查、冲突检查、all-or-nothing 写入和 Redis 写入锁。

09 service 仍负责写 `collector_event_log`，并在 `success`、`failed`、`blocked`、`skipped` 中记录采集业务结果。
scheduler 根据 09 返回值写自己的 Redis slot 状态：`success` 写 completed marker；`blocked` / `failed` / `skipped` 写 status marker。
scheduler 不把 blocked 视为 completed，不自动修复 K线，不自动回补，不覆盖正式 K线。

## 4. 功能：11 scheduler job

### 4.1 入口文件

`app/scheduler/jobs/daily_kline_integrity_check.py`

入口方法：

`run_daily_kline_integrity_check_job()`

### 4.2 调用链路

```text
app/scheduler/runner.py::SchedulerRunner.run_once
    ↓
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
    ↓
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
```

### 4.3 数据与副作用

11 job 构造每日复核请求：

```text
check_trigger=scheduler
check_mode=daily_integrity_check
lookback_count=DAILY_KLINE_INTEGRITY_LIMIT
```

11 job 不调用 `scripts.check_kline_integrity`，不直接请求 Binance，不直接读写 repository，不拆分 11 的每日结果通知机制。

11 service 负责写 `data_quality_check`，并保证 scheduler / `daily_integrity_check` 每次最终只发送一条 Hermes 固定模板结果通知：`healthy`、`unhealthy`、`unknown` 或 `skipped`。

scheduler 根据 11 返回值写自己的 Redis slot 状态：健康或已完成的不健康复核写 completed marker；`skipped`、通知失败或 wrapper 异常写 status marker。
scheduler 不直接写 `data_quality_check`，不替代 11 的每日复核结果通知。

## 5. 功能：10 price monitor 独立运行

10 不进入 scheduler。生产部署使用独立 systemd 服务：

```bash
python -m scripts.run_price_monitor_10s --trigger-source systemd
```

10 是否运行由 `hermes-btc-price-monitor.service` 启动、停止、重启控制，不通过
`PRICE_MONITOR_ENABLED` 控制。当前代码没有使用 `PRICE_MONITOR_ENABLED`，12 不新增该配置。

scheduler 不启动、停止、重启 10，不每 10 秒拉起 10，不读取 `bitcoin_price`，不实现 10 的 WebSocket 逻辑。10 仍由 WebSocket `btcusdt@aggTrade` 写 Redis `bitcoin_price`。

未来 detector 只能作为后续阶段扩展边界，12 不实现策略检测、买卖点提醒、DeepSeek 分析或交易建议。

## 6. systemd 模板

新增模板：

```text
deploy/systemd/hermes-btc-scheduler.service.example
deploy/systemd/hermes-btc-price-monitor.service.example
```

示例启动：

```bash
sudo systemctl start hermes-btc-scheduler
sudo systemctl start hermes-btc-price-monitor
```

示例停止：

```bash
sudo systemctl stop hermes-btc-scheduler
sudo systemctl stop hermes-btc-price-monitor
```

示例重启：

```bash
sudo systemctl restart hermes-btc-scheduler
sudo systemctl restart hermes-btc-price-monitor
```

查看日志：

```bash
journalctl -u hermes-btc-scheduler -f
journalctl -u hermes-btc-price-monitor -f
```

模板只使用占位路径 `/opt/hermes_btc_agent_v2` 和占位用户 `hermes`，不包含真实密钥、真实密码或真实 token。

## 7. Hermes 与异常

scheduler 层只处理自身无法安全调度的问题：

```text
启动阶段配置解析失败
Redis slot 状态无法判断
job 包装层抛出异常
completed / status marker 写入失败
running lock 释放失败
```

Redis slot 状态无法判断、job 包装层抛出异常、marker 写入失败和 running lock 释放失败，由
`app/scheduler/runner.py::SchedulerRunner._send_scheduler_system_alert()` 通过
`app/alerting/service.py::send_alert` 发送固定模板系统异常通知。

启动阶段配置解析失败时，入口为：

```text
scripts/run_scheduler.py::main
    ↓
scripts/run_scheduler.py::_send_scheduler_startup_config_error_alert
    ↓
app/alerting/service.py::send_alert
```

如果 `get_settings()` 已经成功、alerting 可初始化，脚本会尽力发送 `AlertType.SYSTEM_ERROR`
固定模板通知，说明 scheduler 未启动、原因是配置错误，并在 `details` 中记录
`no_auto_repair=true`、`no_auto_backfill=true`、`no_trading=true`、`scheduler_started=false`。
该通知不调用 DeepSeek，不包含交易建议，不自动修复，不自动回补，不自动交易。

如果配置错误发生在 `get_settings()` 阶段，或 alerting 初始化 / 发送本身失败，脚本只记录清晰错误日志并返回非 0 退出码，不强行发送，也不切换到其他通知通道。

为避免 systemd 反复重启导致 Hermes 刷屏，启动配置异常通知使用轻量本地冷却标记；
冷却命中时只记录日志，不重复发送 Hermes。

为避免 scheduler slot running/completed/status 重复扫描导致日志刷屏，同一个 `job + slot + reason`
在 `SCHEDULER_SLOT_LOG_COOLDOWN_SECONDS` 内最多输出一次诊断日志。日志包含：

```text
job
slot
lock_key
completed_key
status_key
lock_status
ttl
owner
created_at_utc
action
reason
```

如果 09 或 11 service 已经返回业务结果或业务通知，scheduler 不重复发送同一业务事件通知。

## 8. 本阶段不负责

12 不负责：

- 不修改 07 / 08 / 09 / 10 / 11 核心业务逻辑。
- 不把 10 放进 scheduler。
- 不调用任何 scripts 作为内部 job。
- 不使用子进程方式触发内部任务。
- 不新增 `--send-alert`。
- 不新增 `PRICE_MONITOR_SEND_ALERT`。
- 不新增 `KLINE_4H_COLLECT_SEND_ALERT`。
- 不调用 DeepSeek 或其他大模型。
- 不生成交易建议。
- 不读取账户、订单、仓位、杠杆。
- 不调用 Binance 私有接口。
- 不实现自动下单、平仓、调仓或调杠杆。
- 不新增数据库迁移。本次只新增 Redis scheduler 状态 key、配置和测试；正式 K线表、`collector_event_log`、`data_quality_check` 表结构不变。

## 9. 测试

对应测试文件：

```text
tests/test_runtime_scheduler_deployment.py
```

测试覆盖：

- scheduler 配置加载。
- `PRICE_MONITOR_ENABLED` 不作为 12 必需配置出现。
- `DAILY_KLINE_INTEGRITY_UTC_TIME` 是 12 scheduler 每日复核时间的唯一生效来源。
- scheduler disabled 时不运行 09 / 11。
- 09 enabled / disabled 边界。
- 11 enabled / disabled 边界。
- 09 准点触发，以及 scheduler 晚启动时在下一个 4h slot 前补跑同一 slot。
- 11 准点触发，以及 scheduler 晚启动时在每日 2 小时窗口内补跑当天 slot。
- 11 超过每日 2 小时补跑窗口后不再补跑。
- 09 job 传 `trigger_source=scheduler`。
- 11 job 传 `check_trigger=scheduler` 和 `check_mode=daily_integrity_check`。
- scheduler 不调用 scripts，不使用内部任务进程包装，不拉起 10。
- completed marker 存在时不重复执行 slot，返回 skipped/completed，不输出 `already reserved`。
- running lock 未过期时不并发执行，返回 skipped/running。
- token-only 或 TTL 异常的 running lock 会识别为 stale/expired，写 status marker，并安全重试一次。
- job 成功后删除 running lock，并写 completed marker。
- job failed / blocked 后删除 running lock，不写 completed marker，写 status marker。
- 补跑扫描不重复执行 completed slot，不无限撞同一个 running slot，也不因为旧 slot 卡住而阻塞下一 4h slot 判断。
- 同一个 `job + slot + reason` 在日志冷却窗口内不会每 30 秒重复刷屏。
- Redis slot 状态无法判断时不执行任务并发送固定模板系统异常通知。
- `scripts/run_scheduler.py` 启动配置错误时，alerting 可初始化则发送固定模板系统异常通知；无法初始化则记录并非 0 退出。
- systemd example 不包含真实密钥，且 scheduler 与 price monitor 独立。

默认 pytest 不访问真实 Binance、MySQL、Redis、Hermes、DeepSeek 或交易接口。

本次人工检查命令：

```bash
.\.venv\Scripts\python.exe -m pytest
```

结果：

```text
224 passed
```
