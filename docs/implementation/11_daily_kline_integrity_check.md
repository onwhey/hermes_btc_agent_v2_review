# 11 每日 K线一致性复核实现说明

## 1. 功能：每日 BTCUSDT 4h K线一致性复核

### 1.1 发起方式

本功能是每日复核任务，不是采集任务，不是回补任务。

人工调试入口：

```bash
python -m scripts.check_kline_integrity --check-trigger cli --lookback-count 100
```

scheduler 正式入口：

```text
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
```

scheduler 必须直接调用 service，不允许调用 `scripts/*.py`。

### 1.2 入口文件

人工 CLI 入口文件：

```text
scripts/check_kline_integrity.py
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
app/market_data/kline_integrity/notification_formatter.py
```

核心调用链路：

```text
scripts/check_kline_integrity.py::main
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
app/market_data/kline_integrity/kline_integrity_service.py::_send_daily_result_notification_and_adjust_result
    ↓
app/market_data/kline_integrity/kline_integrity_service.py::_send_daily_result_notification_safely
    ↓
app/market_data/kline_integrity/notification_formatter.py::build_daily_result_alert_event
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
```

`DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS` 只控制 manual CLI 成功健康通知；scheduler / `daily_integrity_check`
每天必须发送一次结果通知，因此不允许用该配置关闭 scheduler 的 `healthy`、`unhealthy`、`unknown` 或 `skipped` 通知。

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
scheduler / daily_integrity_check:
app/market_data/kline_integrity/kline_integrity_service.py::_send_daily_result_notification_and_adjust_result
    ↓
app/alerting/service.py::send_alert

manual CLI compatibility path:
app/market_data/kline_quality/service.py::send_quality_alert_if_needed
app/market_data/kline_quality/service.py::send_quality_task_failure_alert
app/alerting/service.py::send_alert
```

scheduler / `daily_integrity_check` 每次最终只发送一条每日结果通知：

- `report_status=healthy` 使用 `AlertType.KLINE_INTEGRITY_CHECK_PASSED`，severity 为 `info`。
- `report_status=unhealthy` 使用 `AlertType.KLINE_INTEGRITY_CHECK_FAILED`，severity 为 `error`。
- `report_status=unknown` 使用 `AlertType.KLINE_INTEGRITY_CHECK_FAILED`，severity 为 `critical`。
- `report_status=skipped` 使用 `AlertType.KLINE_INTEGRITY_CHECK_FAILED`，severity 为 `warning`。

每日结果通知内容包含 `symbol`、`interval`、`limit`、`trigger_source`、`checked_count`、`issue_count`、`first_issue_type`、`first_issue_message`、`checked_start_time`、`checked_end_time`、`data_quality_check_id`、`source=Binance REST official klines` 和 `no_repair_performed=true`。同一次 scheduler 每日检查不再额外拆分“成功健康通知”和“失败报警”两套 Hermes 通知。

Hermes 发送失败时，service 返回 `exit_code=3` 或在任务异常结果中记录 `alert_status=failed`，不修改正式 K线表。

本功能不调用 DeepSeek 或其他大模型生成报警内容。

## 8. 异常处理

Binance server time 或 Kline 请求失败：

```text
app/market_data/kline_quality/integrity_checker.py::run_recent_kline_integrity_check
    抛出异常
app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
    捕获异常
    尽力写 status=error 的 data_quality_check
    scheduler / daily_integrity_check 场景只发送一次 report_status=unknown 每日结果通知
```

如果 MySQL 或 `data_quality_check` 写入不可用，service 会写 emergency/error 日志，并继续尽力发送
`report_status=unknown` 每日结果通知。

数据库读取失败：

```text
MarketKline4hRepository.list_by_time_range
    抛出异常
run_daily_kline_integrity_check
    捕获异常
    尝试写 status=error 的 data_quality_check
    scheduler / daily_integrity_check 场景只发送一次 report_status=unknown 每日结果通知
```

`data_quality_check` 写入失败：

```text
DataQualityCheckRepository.create_quality_check_record
    抛出异常
run_daily_kline_integrity_check
    捕获异常
    rollback 当前 session
    scheduler / daily_integrity_check 场景只发送一次 report_status=unknown 每日结果通知
```

Hermes 每日结果通知发送失败：

```text
_send_daily_result_notification_and_adjust_result
    返回 failed/skipped 或捕获发送异常
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
check_trigger=scheduler
```

本功能不写正式 K线，因此 scheduler 不产生新的正式 K线 `data_source`。它只校验已有数据库行的 `data_source` 是否符合项目规则。

失败是否报警：是，由 service 发送固定模板 Hermes。

是否允许重试：本阶段不在 job 内实现重试。

是否修改正式 K线表：否。

## 10. CLI 边界

CLI 只供人工调试：

```bash
python -m scripts.check_kline_integrity --check-trigger cli --lookback-count 100
```

参数：

- `--symbol`：默认 `BTCUSDT`。
- `--interval`：只允许 `4h`。
- `--lookback-count`：默认 `100`；本阶段只支持最近 N 根复核，`--limit` 仅作为兼容别名。
- `--check-trigger`：必填，只允许 `cli`；`--trigger-source` 仅作为兼容别名。
- `--notify-success`：manual CLI 成功时开启健康通知。
- `--no-notify-success`：manual CLI 成功时关闭健康通知；不影响 scheduler 每日结果通知。

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

- scheduler 最近 100 根复核通过，写 `data_quality_check` passed，只发送一次 `healthy` 结果通知，不写正式 K线表。
- scheduler 发现 Binance 官方有、数据库缺失，返回 failed，只发送一次 `unhealthy` 结果通知，不自动回补。
- scheduler 数据库字段与官方不一致，返回 failed，只发送一次 `unhealthy` 结果通知，不覆盖数据库。
- scheduler 数据库存在多余 K线，返回 failed，只发送一次 `unhealthy` 结果通知，不删除数据库。
- scheduler Binance REST 请求失败，返回 error，只发送一次 `unknown` 结果通知，不写正式 K线表。
- scheduler 数据库读取失败，返回 error，尽力写 error 质量记录并只发送一次 `unknown` 结果通知。
- scheduler `data_quality_check` 写入失败，不静默吞掉，只发送一次 `unknown` 结果通知。
- scheduler 复核锁占用时返回 skipped，只发送一次 `skipped` 结果通知，不请求 Binance、不读 MySQL、不写 `data_quality_check`。
- manual CLI 参数错误不强制发送 Hermes。
- manual CLI 复核锁占用不强制发送 Hermes。
- Hermes 每日结果通知失败，返回 `exit_code=3`，但不会修改正式 K线表。
- scheduler job 直接调用 service，并构造 `check_trigger=scheduler`。
- CLI 只允许 `check_trigger=cli`，不恢复 `--send-alert`。
- 新增源码不引入私有 Binance、自动修复或自动交易能力。

人工检查命令：

```bash
python -m py_compile app/market_data/kline_quality/*.py
python -m py_compile scripts/check_kline_integrity.py

python -m pytest tests/test_daily_kline_integrity_check.py
python -m pytest tests/test_kline_quality_checker.py
python -m pytest tests/test_4h_kline_manual_backfill.py
python -m pytest tests/test_4h_kline_incremental_collector.py
python -m pytest tests/test_price_monitor_10s.py
python -m pytest

python -m scripts.check_project_invariants
```

## 14. 本次验收补充说明

### 14.1 CLI 入口与参数

本分支统一人工入口为：

```bash
python -m scripts.check_kline_integrity --check-trigger cli --lookback-count 100
```

`--trigger-source` 和 `--limit` 仅作为兼容别名保留，用于旧命令过渡；文档、测试和 service 注释统一以
`scripts/check_kline_integrity.py` 为入口名。本阶段不实现 `--start-time` / `--end-time` 范围复核；如果传入这两个
参数，CLI 返回参数错误，不进入 Binance、MySQL、Redis 或 Hermes 调用。

### 14.2 复核任务锁

CLI 与 scheduler 都通过 `run_daily_kline_integrity_check()` 获取同一把 Redis 复核锁。锁 key 格式为：

```text
kline_integrity_check:{symbol}:{interval_value}
```

例如：

```text
kline_integrity_check:BTCUSDT:4h
```

`check_mode` 不进入锁 key，避免手动复核和 scheduler 复核同一 `symbol + interval` 时并发执行、重复记录或重复告警。
锁有 TTL，释放时校验 owner；获取锁失败返回 `skipped`，不会请求 Binance、读取 MySQL 或写 `data_quality_check`。scheduler / `daily_integrity_check` 会发送一次 `report_status=skipped` 每日结果通知；manual CLI 不强制发送 Hermes。

### 14.3 scheduler 边界

本分支提供 scheduler job 入口：

```text
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
```

该 job 直接构造 `DailyKlineIntegrityCheckRequest(check_trigger="scheduler", check_mode="daily_integrity_check")` 并调用 app service。
本分支不新增常驻 scheduler runner，也不在当前仓库内启动自动每日调度；正式调度器接入时必须直接调用该 job 或 service，
不得调用 `scripts/check_kline_integrity.py`。

调度窗口要求：
- 每日复核读取 `market_kline_4h`，但不持有正式 K线写入锁，也不写正式 K线表。
- 正式部署时应让每日复核避开 4h incremental collector 写入窗口，建议在 collector 预期完成并留出缓冲后触发。
- 本阶段未实现“检测正式 K线写入锁并跳过复核”或“跳过最近 1 根已收盘 4h K线”；后续如要进一步降低误报风险，应在 service 层补该能力并增加测试。

### 14.4 异常审计

Binance REST 或 server time 请求失败时，只要 `data_quality_check` repository 可用，service 会写入 `status=error` 的
质量记录，并尽力发送 Hermes 固定模板异常告警。MySQL 或质量记录写入不可用时，service 会写 emergency/error 日志，
并继续尽力发送 Hermes 告警，不会静默丢失任务失败事实。

### 14.5 每日结果通知边界

scheduler / `daily_integrity_check` 场景下，`run_daily_kline_integrity_check()` 每次最终只发送一条 Hermes 固定模板结果通知：

- `report_status=healthy`：复核完成且 K线健康。
- `report_status=unhealthy`：复核完成但发现 K线问题。
- `report_status=unknown`：参数配置错误、Binance 请求失败、数据库读取失败、`data_quality_check` 写入失败等导致无法确认健康状态。
- `report_status=skipped`：复核锁被占用，本次未执行，无法确认本次 K线健康状态。

这四类结果不再拆成“健康通知”和“失败报警”两套 scheduler 通知路径；同一次 scheduler 每日复核最多只产生一条 Hermes 结果通知。通知 `details` 必须包含 `report_status`、`symbol`、`interval`、`limit`、`trigger_source`、可用的检查数量和首个问题信息，以及 `no_repair_performed=true`。

manual CLI 场景下，参数错误可以只返回错误，复核锁占用可以只返回 `skipped`，不强制发送 Hermes。该边界不影响 scheduler 每日结果通知规则。

### 14.6 每日结果微信正文精简

本分支只优化用户可见 Hermes / 微信正文，不修改 K线检查算法、不修改 scheduler slot 状态、不修改 Hermes gateway、不新增数据库迁移。

通知生成链路：

```text
app/market_data/kline_integrity/kline_integrity_service.py::_send_daily_result_notification_safely
    ↓
app/market_data/kline_integrity/notification_formatter.py::build_daily_result_alert_event
    ↓
app/market_data/kline_integrity/notification_formatter.py::_build_daily_result_wechat_visible_body
    ↓
app/alerting/templates.py::render_alert_message
    ↓
app/alerting/service.py::send_alert
```

`build_daily_result_alert_event()` 仍把完整审计字段保留在 `AlertEvent.details` 中，包括：

- `action`
- `check_mode`
- `check_trigger`
- `trigger_source`
- `lock_key`
- `report`
- `report.existing_open_time_ms`
- `report.writable_open_time_ms`
- `report.metadata`
- `requested_binance_limit`
- `enforce_database_source_rules`
- `started_at`
- `finished_at`
- 原始 `source` / `status` / `report_status`

这些字段用于 `alert_message` 结构化上下文、`data_quality_check`、`collector_event_log`、服务日志和排查审计，不直接展示在微信正文。

微信正文只读取 `AlertEvent.details["_wechat_visible_body"]`。`render_alert_message()` 检测到该字段后，不再把 `details` 原始字典逐项渲染到微信，而是只展示精简中文正文。每日复核精简正文已包含“边界声明”，所以通用 K线边界不会重复追加。

#### 14.6.1 健康通过正文

当 `report_status=healthy` 且 `issue_count=0` 时，微信正文包含：

- 中文标题：`每日 K线健康检查通过`。
- 中文级别：`信息`。
- 币种周期：例如 `BTCUSDT 4h`。
- 检查范围：UTC 起止时间。
- 检查数量与问题数量。
- 检查结果：最近 N 根 4h K线连续、无缺失、无重复、未发现数据质量异常。
- 数据来源：`Binance REST 官方 K线`。
- 补充：已过滤未收盘 K线数量。
- 只读边界：不修复、不回补、不写入正式 K线表、没有人工改数、没有自动交易。
- 追踪ID。
- `本提醒不是交易建议`。

示例：

```text
【每日 K线健康检查通过】

级别：信息
币种周期：BTCUSDT 4h

检查范围：
2026-04-28 08:00 UTC ~ 2026-05-14 20:00 UTC

检查数量：100 根
问题数量：0

检查结果：
最近 100 根 4h K线连续、无缺失、无重复、未发现数据质量异常。

数据来源：
Binance REST 官方 K线；本次仅检查，不修复、不回补、不写入正式 K线表。

补充：
已过滤未收盘 K线 1 根，未写入数据库。

边界声明：
本次为只读健康检查：系统没有自动修复、没有人工改数、没有自动回补，也没有执行自动交易。

追踪ID：434449aa0be5405694f3a874c0209ac1

本提醒不是交易建议，不包含自动交易动作。
```

#### 14.6.2 异常正文

当 `report_status=unhealthy` / `unknown` / `skipped` 时，微信正文仍保持原有严重级别映射，不降级：

- `unhealthy`：`error` / 微信显示 `错误`。
- `unknown`：`critical` / 微信显示 `严重`。
- `skipped`：`warning` / 微信显示 `注意`。

异常正文只展示前 1 到 3 个关键问题摘要，不展开完整 `issues`、`existing_open_time_ms`、`metadata` 或 `report` 原始字典。

正文包含：

- 币种周期。
- 检查范围。
- 检查数量。
- 问题数量。
- 前 1 到 3 个中文关键问题摘要。
- 数据质量检查ID。
- 追踪ID。
- 建议动作：检查采集链路、Binance REST 返回、数据库最近 K线；不要人工改数、不要自动修复。
- 只读边界与非交易建议声明。

示例：

```text
【每日 K线健康检查发现异常】

级别：错误
币种周期：BTCUSDT 4h

检查范围：
2026-04-28 08:00 UTC ~ 2026-05-14 20:00 UTC

检查数量：100 根
问题数量：4

关键问题：
1. 数据库缺失 Binance 官方 K线（open time：2026-04-29 04:00 UTC）。
2. 数据库缺失 Binance 官方 K线（open time：2026-04-29 08:00 UTC）。
3. 数据库缺失 Binance 官方 K线（open time：2026-04-29 12:00 UTC）。

数据来源：
Binance REST 官方 K线；本次仅检查，不修复、不回补、不写入正式 K线表。

数据质量检查ID：
1

建议动作：
请检查采集链路、Binance REST 返回、数据库最近 K线；不要人工改数、不要自动修复。

边界声明：
本次为只读健康检查：系统没有自动修复、没有人工改数、没有自动回补，也没有执行自动交易。

追踪ID：434449aa0be5405694f3a874c0209ac1

本提醒不是交易建议，不包含自动交易动作。
```

#### 14.6.3 测试覆盖

新增或更新的测试：

```text
tests/test_daily_kline_integrity_check.py::test_daily_healthy_notification_uses_compact_chinese_visible_body_without_internal_context
tests/test_daily_kline_integrity_check.py::test_daily_notification_keeps_internal_context_structured_but_not_visible
tests/test_daily_kline_integrity_check.py::test_daily_unhealthy_notification_stays_error_and_shows_compact_issue_summary
tests/test_alerting.py::test_kline_related_templates_state_no_auto_repair_or_manual_data_change
```

覆盖内容：

- 健康通过通知使用中文标题和中文级别，包含 `symbol`、`interval`、检查范围、`checked_count`、`issue_count=0`、追踪ID和只读边界。
- 健康通过通知不包含 `report` 原始字典、`existing_open_time_ms`、`action`、`lock_key`、`metadata` 或旧英文标题。
- 异常通知仍保持 `error` / `critical`，只展示前几个关键问题摘要，不展开完整内部上下文。
- 异常通知包含“不要人工改数、不要自动修复”的边界提示。
- 结构化 context 仍保留完整内部字段，只是不进入微信正文。
- K线相关固定模板统一声明没有自动修复、没有人工改数、没有自动回补、没有执行自动交易。

本次不涉及数据库迁移，因为没有新增表、字段、索引或枚举；只修改 Hermes 可见正文构造、固定模板尾部声明和测试。

风险边界：

- 不影响 4h 增量采集：未修改 `app/market_data/incremental` 或正式 K线写入 repository。
- 不影响每日健康检查逻辑：未修改 Binance 拉取、未收盘过滤、连续性检查、数据库对齐比较或质量报告判定。
- 不影响正式 K线写入：每日复核仍只读 `market_kline_4h`，只写 `data_quality_check` 和可选 `alert_message`。
- 不影响 Hermes gateway：未修改 Hermes client、签名、发送、重试或 `channel_response` 保存逻辑。
