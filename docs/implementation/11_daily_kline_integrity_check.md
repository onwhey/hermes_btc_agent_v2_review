# 11 每日 K线一致性复核实现说明

## 1. 功能：每日 BTCUSDT 4h K线一致性复核

### 1.1 发起方式

本功能是每日复核任务，不是采集任务，不是回补任务。

人工调试入口：

```bash
python -m scripts.run_daily_kline_integrity_check --trigger-source cli
```

scheduler 正式入口：

```text
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
```

scheduler 必须直接调用 service，不允许调用 `scripts/*.py`。

### 1.2 入口文件

人工 CLI 入口文件：

```text
scripts/run_daily_kline_integrity_check.py
```

入口方法：

```text
main()
```

scheduler job 文件：

```text
app/scheduler/jobs/daily_kline_integrity_check.py
```

job 方法：

```text
run_daily_kline_integrity_check_job()
```

### 1.3 核心 service

核心文件：

```text
app/market_data/kline_integrity/kline_integrity_service.py
```

核心方法：

```text
run_daily_kline_integrity_check()
```

结果格式化与 ID 提取辅助文件：

```text
app/market_data/kline_integrity/results.py
```

核心调用链路：

```text
scripts/run_daily_kline_integrity_check.py::main
    ↓
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
    ↓
app/market_data/kline_quality/service.py::run_recent_kline_integrity_check
    ↓
app/market_data/kline_quality/integrity_checker.py::run_recent_kline_integrity_check
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    ↓
app/market_data/kline_parser.py::parse_binance_klines
    ↓
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::list_by_time_range
    ↓
app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    ↓
app/market_data/kline_quality/service.py::send_quality_alert_if_needed
    ↓
app/alerting/service.py::send_alert
```

scheduler 调用链路：

```text
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
    ↓
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
```

## 2. 数据标准

复核标准只能来自：

```text
Binance REST /fapi/v1/klines
```

复核区间默认是最近 100 根 BTCUSDT 4h 已收盘 K线。

service 会请求 `limit + 1` 根 K线，使用 Binance server time 过滤未收盘 K线，然后取最近 `limit` 根已收盘 K线与 `market_kline_4h` 对齐比较。

本功能不使用 WebSocket K线，不使用第三方行情源，不使用模拟数据，不允许人工输入 K线字段。

## 3. 读取配置

配置统一由 `app/core/config.py::load_settings()` 读取。

新增配置：

```text
DAILY_KLINE_INTEGRITY_ENABLED=true
DAILY_KLINE_INTEGRITY_SYMBOL=BTCUSDT
DAILY_KLINE_INTEGRITY_INTERVAL=4h
DAILY_KLINE_INTEGRITY_LIMIT=100
DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS=true
DAILY_KLINE_INTEGRITY_TRIGGER_SOURCE=scheduler
```

`DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS` 只控制成功健康通知，不控制失败报警。失败报警和异常报警不可由该配置关闭。

## 4. 数据库读写

读取表：

```text
market_kline_4h
```

读取方法：

```text
app/storage/mysql/repositories/market_kline_4h_repository.py::list_by_time_range
```

写入表：

```text
data_quality_check
alert_message
```

`data_quality_check` 写入方法：

```text
app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
```

`alert_message` 只在 Hermes 发送路径中，由 `app/alerting/service.py::send_alert` 通过 `AlertMessageRepository` 写入。

本功能不写入 `market_kline_4h`，不修改 `market_kline_4h`，不删除 `market_kline_4h`。

本阶段没有新增 migration，复用 07 已有 `data_quality_check` 表字段。`started_at`、`finished_at`、`source`、`no_repair_performed` 等每日复核上下文写入 `report_json.metadata`。

## 5. 检查内容

每日复核复用 07 的检查基础能力，并为 11 打开严格数据库行检查：

- Binance 官方最近 N 根已收盘 4h K线连续。
- 数据库对应 `open_time_ms` 不缺失。
- 数据库在官方时间范围内不存在官方没有的多余 K线。
- 数据库核心字段与 Binance 官方字段一致。
- 数据库不存在重复 `open_time_ms`。
- 数据库不存在未收盘误写。
- 数据库 `symbol` 必须是 `BTCUSDT`。
- 数据库 `interval_value` 必须是 `4h`。
- 数据库 `data_source` 必须是当前项目允许的 Binance REST 正式来源：`binance_rest_by_cli` 或 `binance_rest_by_scheduler`。
- 数据库 `trigger_source` 与 `data_source` 必须满足项目映射规则。
- `close_time_ms = open_time_ms + 14400000 - 1`。

严格检查实现位置：

```text
app/market_data/kline_quality/integrity_checker.py::_check_strict_database_row_invariants
```

该严格检查只读 ORM/fake rows，不写库，不修复数据。

## 6. trigger_source 与 data_source

11 的 `check_trigger_source` 表示复核任务来源：

- CLI 调试入口只允许 `cli`。
- scheduler job 固定传入 `scheduler`。

11 不写正式 K线，因此不会为新 K线生成 `data_source`。

11 会校验数据库已有正式 K线的来源映射：

```text
trigger_source=cli       -> data_source=binance_rest_by_cli
trigger_source=scheduler -> data_source=binance_rest_by_scheduler
```

scheduler job 不调用 scripts，不伪装成 CLI。

## 7. Hermes 报警

报警统一通过：

```text
app/market_data/kline_quality/service.py::send_quality_alert_if_needed
app/market_data/kline_quality/service.py::send_quality_task_failure_alert
app/alerting/service.py::send_alert
```

成功健康通知：

- `AlertType.KLINE_INTEGRITY_CHECK_PASSED`
- severity 为 `info`
- 默认开启。
- 内容包含 `symbol`、`interval`、`checked_count`、`checked_start_time`、`checked_end_time`、`source=Binance REST official klines`、`check_only_no_repair_no_backfill_no_market_kline_write`。

复核失败报警：

- `AlertType.KLINE_INTEGRITY_CHECK_FAILED`
- severity 为 `error` 或 `critical`
- 不受 `notify_success` 控制。
- 内容包含 `symbol`、`interval`、`issue_count`、`first_issue_type`、`first_issue_message`、`checked_start_time`、`checked_end_time`、`data_quality_check_id`、不修复说明。

任务异常报警：

- `AlertType.KLINE_INTEGRITY_CHECK_FAILED`
- severity 为 `critical`
- 用于 Binance 请求失败、数据库读取失败、`data_quality_check` 写入失败、Hermes 发送失败以外的未知异常等无法确认健康状态的场景。

Hermes 发送失败时，service 返回 `exit_code=3` 或在任务异常结果中记录 `alert_status=failed`，不修改正式 K线表。

本功能不调用 DeepSeek 或其他大模型生成报警内容。

## 8. 异常处理

Binance server time 或 Kline 请求失败：

```text
app/market_data/kline_quality/integrity_checker.py::run_recent_kline_integrity_check
    抛出异常
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
    捕获异常
    尽力调用 send_quality_task_failure_alert
```

因为此时还没有进入数据库读取阶段，通常不写 `data_quality_check`。

数据库读取失败：

```text
MarketKline4hRepository.list_by_time_range
    抛出异常
run_daily_kline_integrity_check
    捕获异常
    尝试写 status=error 的 data_quality_check
    尽力发送 Hermes 异常报警
```

`data_quality_check` 写入失败：

```text
DataQualityCheckRepository.create_quality_check_record
    抛出异常
run_daily_kline_integrity_check
    捕获异常
    rollback 当前 session
    尽力发送 Hermes 异常报警
```

Hermes 成功通知或失败报警发送失败：

```text
send_quality_alert_if_needed
    返回 failed/skipped 或抛出异常
run_daily_kline_integrity_check
    返回 exit_code=3
```

这不改变“检查本身已经完成”的事实，也不写入或修改正式 K线。

本功能不重试，不允许 partial formal write，因为它根本不写正式 K线。

## 9. scheduler 边界

定时任务定义：

```text
app/scheduler/jobs/daily_kline_integrity_check.py
```

job 名称：

```text
run_daily_kline_integrity_check_job
```

job 调用：

```text
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
```

scheduler 不调用 scripts，不直接请求 Binance，不直接读写业务表，不直接发送 Hermes。

scheduler 实际传入：

```text
check_trigger_source=scheduler
```

本功能不写正式 K线，因此 scheduler 不产生新的正式 K线 `data_source`。它只校验已有数据库行的 `data_source` 是否符合项目规则。

失败是否报警：是，由 service 发送固定模板 Hermes。

是否允许重试：本阶段不在 job 内实现重试。

是否修改正式 K线表：否。

## 10. CLI 边界

CLI 只供人工调试：

```bash
python -m scripts.run_daily_kline_integrity_check --trigger-source cli
```

参数：

- `--symbol`：默认 `BTCUSDT`。
- `--interval`：只允许 `4h`。
- `--limit`：默认 `100`。
- `--trigger-source`：必填，只允许 `cli`。
- `--notify-success`：开启成功健康通知。
- `--no-notify-success`：关闭成功健康通知。

CLI 不支持 `--send-alert`。

CLI 不请求 Binance，不写 repository，不直接发送 Hermes，只解析参数、创建 request、打开 session、调用 service、打印结果、返回退出码。

退出码：

- `0`：复核成功且必要通知成功。
- `1`：参数错误。
- `2`：复核完成但发现 K线质量问题。
- `3`：Hermes 通知发送失败。
- `4`：任务异常，无法确认 K线健康。

## 11. 与 07/08/09/10 的关系

07 提供 K线质量检查基础能力，包括 Binance 官方 recent Kline 拉取、未收盘过滤、批次连续性检查、数据库对齐比较、`data_quality_check` 写入和固定模板报警能力。

08 是用户手动触发的 4h K线回补。

09 是 4h K线增量采集。

10 是 WebSocket 10 秒实时价格监控，不写正式 K线表。

11 是每日独立复核任务，只确认数据库已有 K线是否仍与 Binance REST 官方已收盘 4h K线一致。

11 不调用 08，不调用 09，不触碰 10 WebSocket 数据，不自动修复、自动回补、覆盖或删除任何正式 K线。

## 12. 本功能明确不负责

- 不写入 `market_kline_4h`。
- 不修改 `market_kline_4h`。
- 不删除 `market_kline_4h`。
- 不自动修复 K线。
- 不自动回补 K线。
- 不覆盖冲突字段。
- 不接受人工输入 K线字段。
- 不使用 WebSocket K线作为复核标准。
- 不使用第三方数据源作为复核标准。
- 不使用模拟数据作为正式复核标准。
- 不调用 DeepSeek。
- 不生成策略分析或交易建议。
- 不实现自动交易。
- 不读取账户、订单、仓位、杠杆等私有能力。

文档中的 `manual_repair`、`human_edit`、`manual_input`、`system_repair` 只作为禁止项说明出现，代码路径没有实现这些能力。

## 13. 测试与检查

对应测试文件：

```text
tests/test_daily_kline_integrity_check.py
```

默认测试使用 fake/mock：

- 不请求真实 Binance。
- 不连接真实 MySQL。
- 不连接真实 Redis。
- 不发送真实 Hermes。
- 不调用 DeepSeek。
- 不访问任何交易接口。

覆盖范围：

- 最近 100 根复核通过，写 `data_quality_check` passed，发送成功健康通知，不写正式 K线表。
- Binance 官方有、数据库缺失，返回 failed，发送失败报警，不自动回补。
- 数据库字段与官方不一致，返回 failed，发送失败报警，不覆盖数据库。
- 数据库存在多余 K线，返回 failed，发送失败报警，不删除数据库。
- Binance REST 请求失败，返回 error，发送异常报警，不写正式 K线表。
- 数据库读取失败，返回 error，尽力写 error 质量记录并发送异常报警。
- `data_quality_check` 写入失败，不静默吞掉，发送异常报警。
- Hermes 成功通知失败，返回 `exit_code=3`，但质量记录仍为 passed。
- `notify_success=false` 时成功不通知，失败仍报警。
- scheduler job 直接调用 service，并传入 `check_trigger_source=scheduler`。
- CLI 只允许 `check_trigger_source=cli`，不恢复 `--send-alert`。
- 新增源码不引入私有 Binance、自动修复或自动交易能力。

人工检查命令：

```bash
python -m py_compile app/market_data/kline_quality/*.py
python -m py_compile scripts/run_daily_kline_integrity_check.py

python -m pytest tests/test_daily_kline_integrity_check.py
python -m pytest tests/test_kline_quality_checker.py
python -m pytest tests/test_4h_kline_manual_backfill.py
python -m pytest tests/test_4h_kline_incremental_collector.py
python -m pytest tests/test_price_monitor_10s.py
python -m pytest

python -m scripts.check_project_invariants
```
