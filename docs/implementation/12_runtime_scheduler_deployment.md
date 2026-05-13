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
app/scheduler/execution_slot.py::SchedulerExecutionSlotStore.reserve_execution_slot
    ↓
app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
```

### 1.4 配置

读取 `.env` 中的 scheduler 配置：

```env
SCHEDULER_ENABLED=true
SCHEDULER_POLL_INTERVAL_SECONDS=30
SCHEDULER_JOB_SLOT_TTL_SECONDS=90000
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

### 1.5 调度循环

`SchedulerRunner.run_forever()` 使用 UTC sleep 循环：

```text
读取当前 UTC 时间
判断 09 是否进入 UTC 00:05 / 04:05 / 08:05 / 12:05 / 16:05 / 20:05 窗口
判断 11 是否进入 DAILY_KLINE_INTEGRITY_UTC_TIME 窗口
进入窗口后先写 Redis 执行槽
执行槽成功时调用对应 app job
执行槽已存在时跳过
执行槽异常时不执行任务并发送 scheduler 系统异常通知
sleep SCHEDULER_POLL_INTERVAL_SECONDS
```

PRC 时间不参与调度判断。

## 2. 功能：Redis 执行槽去重

### 2.1 入口文件

`app/scheduler/execution_slot.py`

入口方法：

`SchedulerExecutionSlotStore.reserve_execution_slot()`

### 2.2 Redis key

09 增量采集执行槽：

```text
scheduler:job:kline_4h_incremental:2026-05-13T04:05Z
```

11 每日复核执行槽：

```text
scheduler:job:daily_kline_integrity:2026-05-13
```

### 2.3 去重规则

执行前使用 Redis `SET key owner NX EX ttl`：

```text
写入成功：允许执行 job
key 已存在：跳过本时间窗口
Redis 异常：不执行 job，记录日志并发送固定模板系统异常通知
```

执行槽只负责 scheduler 时间窗口去重，不替代 09 的写入锁，也不替代 11 的复核锁。

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
app/market_data/incremental_collector/service.py::run_incremental_4h_collection
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

## 5. 功能：10 price monitor 独立运行

10 不进入 scheduler。生产部署使用独立 systemd 服务：

```bash
python -m scripts.run_price_monitor_10s --trigger-source systemd
```

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
Redis 执行槽无法判断
job 包装层抛出异常
```

这些异常由 `app/scheduler/runner.py::SchedulerRunner._send_scheduler_system_alert()` 通过 `app/alerting/service.py::send_alert` 发送固定模板系统异常通知。

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

## 9. 测试

对应测试文件：

```text
tests/test_runtime_scheduler_deployment.py
```

测试覆盖：

- scheduler 配置加载。
- scheduler disabled 时不运行 09 / 11。
- 09 enabled / disabled 边界。
- 11 enabled / disabled 边界。
- 09 job 传 `trigger_source=scheduler`。
- 11 job 传 `check_trigger=scheduler` 和 `check_mode=daily_integrity_check`。
- scheduler 不调用 scripts，不使用内部任务进程包装，不拉起 10。
- Redis 执行槽已存在时同窗口不重复执行。
- Redis 执行槽写入失败时不执行任务并发送固定模板系统异常通知。
- systemd example 不包含真实密钥，且 scheduler 与 price monitor 独立。

默认 pytest 不访问真实 Binance、MySQL、Redis、Hermes、DeepSeek 或交易接口。
