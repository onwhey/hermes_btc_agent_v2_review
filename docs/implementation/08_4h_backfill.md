# 08 4h K线手动回补实现说明

## 1. 功能：人工 CLI 回补 4h K线

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.backfill_4h_klines --symbol BTCUSDT --interval 4h --start-open-time-ms 1700006400000 --end-open-time-ms 1700006400000 --trigger-source cli --confirm-write
```

也支持显式 UTC：

```bash
python -m scripts.backfill_4h_klines --start-utc 2023-11-15T04:00:00Z --end-utc 2023-11-15T04:00:00Z --trigger-source cli --dry-run
```

入口文件：`scripts/backfill_4h_klines.py`

入口方法：`main()`

本脚本只允许人工 CLI 触发，不允许 scheduler 调用。`--trigger-source` 必须显式传入 `cli`，不会恢复旧的失败报警开关。

### 1.2 CLI 参数

- `--symbol`：默认 `BTCUSDT`。
- `--interval`：只允许 `4h`。
- `--start-open-time-ms` / `--end-open-time-ms`：UTC open_time 毫秒，闭区间。
- `--start-utc` / `--end-utc`：带 `Z` 或 offset 的 UTC 时间，转换为 open_time 毫秒。
- `--trigger-source`：必填，只允许 `cli`。
- `--dry-run`：执行请求、解析和质量检查，但不写 `market_kline_4h`。
- `--confirm-write`：非 dry-run 写正式表时必须显式传入。
- `--notify-success`：回补成功后发送成功通知；失败报警不受该参数控制。
- `--limit-per-request`：单次 Binance K线请求数量上限。

参数错误返回 `1`。缺少时间边界、UTC 与毫秒参数混用、未按 4h UTC 边界对齐、未传 `--trigger-source cli`、非 dry-run 缺少 `--confirm-write` 都会拒绝执行。

## 2. 核心调用链

```text
scripts/backfill_4h_klines.py::main
    ↓
app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
    ↓
app/core/task_lock.py::RedisTaskLock.acquire_lock
    ↓
app/storage/mysql/repositories/collector_event_log_repository.py::create_running_event
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    ↓
app/market_data/kline_parser.py::parse_binance_klines
    ↓
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    ↓
app/market_data/backfill/kline_4h_backfill_service.py::check_backfill_quality
    ↓
app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert
    ↓
app/storage/mysql/repositories/collector_event_log_repository.py::mark_success
```

异常链路：

```text
任意质量问题、blocked、failed、写入失败、任务异常
    ↓
不写或回滚 market_kline_4h
    ↓
记录 data_quality_check / collector_event_log
    ↓
app/alerting/service.py::send_alert 固定模板报警
    ↓
CLI 返回非 0
```

## 3. 数据来源与 parser

正式 K线数据只能来自：

```text
Binance USDT-M Futures REST /fapi/v1/klines
```

代码只通过 `BinanceRestClient.get_klines()` 获取 K线，不在脚本或 service 中手写 Binance URL。

parser 调用：

```text
app/market_data/kline_parser.py::parse_binance_klines
```

传入：

- `trigger_source = cli`
- `interval_value = 4h`
- `symbol = BTCUSDT` 或 CLI 指定 symbol

parser 自动映射：

```text
trigger_source = cli
data_source = binance_rest_by_cli
```

本模块不接受人工输入 OHLCV，不允许人工修改正式 K线字段。

## 4. 质量检查

写入 `market_kline_4h` 前必须通过 `check_backfill_quality()`。

第一层复用 07：

```text
check_kline_batch_before_persist()
```

检查内容：

- 单根 K线调用 06 `validate_market_kline()`。
- 批次按 `open_time_ms` 升序。
- 批次无重复 `open_time_ms`。
- 相邻 open_time 差值为 `14400000`。
- symbol 一致。
- interval_value 一致。
- 基于 Binance `server_time_ms` 判断已收盘。

第二层是 08 历史回补上下文检查：

- 请求区间内 Binance 返回 open_time 必须覆盖完整 4h 序列。
- 查询数据库中同 open_time 的已有 K线。
- 已存在且字段一致：作为幂等重复，计入 skipped，不重复写入。
- 已存在但核心字段冲突：blocked，不覆盖。
- 查询回补区间前一根数据库 K线：必须与本批第一根连续。
- 查询回补区间后一根数据库 K线：必须与本批最后一根连续。

因此支持历史中间缺口：

```text
DB: 04:00, 08:00, 16:00
回补: 12:00
```

此时检查 12:00 与 08:00、16:00 均连续，可以写入。不会使用 `latest_db_kline = 16:00` 错误阻断历史回补。

质量检查失败返回 `2`，不写正式 K线表，并必须发送 Hermes 固定模板报警。

## 5. 未收盘 K线

本模块先调用：

```text
BinanceRestClient.get_server_time()
```

再用 07 质量检查判断：

```text
close_time_ms < server_time_ms
```

任意未收盘 K线进入本批次都会被标记为 `UNCLOSED_KLINE`，任务 `blocked`，不写 `market_kline_4h`，并发送 Hermes 固定模板报警。

本模块不使用本机时间作为唯一判断依据。

## 6. 写入与事务边界

正式写入只调用：

```text
MarketKline4hRepository.bulk_upsert()
```

写入规则：

- 质量检查完全通过才进入写入。
- `dry-run` 不写 `market_kline_4h`。
- 已存在且字段一致：跳过。
- 不存在且通过检查：插入。
- 已存在但字段冲突：阻断，不覆盖。
- 写入异常：任务 failed，回滚，不保留部分正式 K线写入。

all-or-nothing 方式：

```text
app/market_data/backfill/kline_4h_backfill_service.py::persist_backfill_klines
```

如果 session 支持 `begin_nested()`，正式 K线写入包在 nested transaction/savepoint 中。任何 `bulk_upsert` 异常都会回滚 savepoint，并由 service 记录 failed 与报警。

如果测试替身不支持 nested transaction，service 仍会在异常时调用 `rollback()`，测试覆盖中途写入异常不会留下正式 K线。

## 7. collector_event_log

新增：

- `app/storage/mysql/models/collector_event_log.py`
- `app/storage/mysql/repositories/collector_event_log_repository.py`
- `migrations/versions/20260511_08_create_collector_event_log.py`

表用途：记录回补、采集、复核等任务事件。它不是正式 K线表，不替代 `market_kline_4h`。

本阶段记录：

- `event_type = manual_backfill_4h`
- `symbol`
- `interval_value`
- `trigger_source = cli`
- `data_source = binance_rest_by_cli`
- `status = running / success / blocked / failed / skipped`
- 请求区间、实际区间、请求数量、获取数量、解析数量、已收盘数量
- 插入数量、跳过数量、冲突数量、未收盘过滤/阻断数量
- `quality_check_id`
- `first_issue_type`
- `first_issue_message`
- `error_code`
- `error_message`
- `trace_id`
- `report_json`
- `details_json`

锁已存在时记录 `skipped`，不请求 Binance，不写正式 K线。

Redis 异常时记录 `failed`，不请求 Binance，不写正式 K线，并发送 Hermes 固定模板报警。

## 8. Redis 任务锁

新增：

```text
app/core/task_lock.py::RedisTaskLock
```

锁 key：

```text
kline_write:{symbol}:{interval}
```

owner：

```text
trace_id
```

行为：

- 获取锁使用 Redis `SET key owner NX EX ttl`。
- 获取锁失败：记录 skipped，不写正式 K线。
- Redis 异常：记录 failed，发送 Hermes 固定模板报警，不写正式 K线。
- 释放锁前读取 owner，只删除当前 trace_id 持有的锁。

本模块不写 Redis `bitcoin_price`，不把 Redis 当 K线存储。

## 9. Hermes 报警

08 继承 07 新规则：只要出现 K线质量问题、blocked、failed、数据库写入失败、Redis 锁异常、任务异常或无法确认回补健康状态，必须发送 Hermes 固定模板报警。

报警由：

```text
app/market_data/backfill/kline_4h_backfill_service.py::_send_backfill_alert
    ↓
app/alerting/service.py::send_alert
```

使用固定模板：

- `AlertType.KLINE_DATA_QUALITY_ERROR`：质量 blocked。
- `AlertType.COLLECTOR_ERROR`：任务 failed、Redis 锁异常、数据库写入失败。
- `AlertType.KLINE_INTEGRITY_CHECK_PASSED`：仅 `--notify-success` 成功通知。

失败报警不得由 CLI 参数关闭，不存在单独控制失败报警发送的开关。

Hermes 发送失败时：

- 记录日志。
- CLI 返回 `3`。
- 不修改、覆盖、删除正式 K线。

报警内容由代码固定生成，不调用 DeepSeek，不生成交易建议，不包含任何具体交易操作建议。

## 10. 数据库与 Redis 影响

读取 MySQL：

- `market_kline_4h`：查询同 open_time、前一根、后一根。

写入 MySQL：

- `collector_event_log`：记录任务状态。
- `data_quality_check`：记录质量检查结果。
- `market_kline_4h`：仅质量通过且非 dry-run 时插入新 K线。
- `alert_message`：发送 Hermes 时可记录报警发送结果。

读取/写入 Redis：

- 只使用 `kline_write:{symbol}:{interval}` 任务锁。

本模块不写：

- Redis `bitcoin_price`
- 策略表
- 建议表
- 自动交易相关表

## 11. 退出码

- `0`：成功，或 dry-run 检查通过。
- `1`：参数错误。
- `2`：质量检查不通过、blocked、锁已存在导致 skipped。
- `3`：Hermes 报警发送失败。
- `4`：程序异常、Binance 失败、Redis 锁异常等任务失败。
- `5`：正式 K线写入失败或事务异常。

## 12. 本阶段不做

本阶段明确不做：

- 不实现 09 增量采集。
- 不实现 scheduler。
- 不允许 scheduler 调用 `scripts/backfill_4h_klines.py`。
- 不实现每日自动复核。
- 不实现 10s WebSocket 价格监控。
- 不写 Redis `bitcoin_price`。
- 不自动修复 K线。
- 不自动回补更多范围。
- 不覆盖冲突 K线。
- 不删除正式 K线。
- 不人工修改 K线字段。
- 不接受人工输入 OHLCV。
- 不请求 Binance 私有接口。
- 不请求 REST 最新价格。
- 不调用 DeepSeek 或其他大模型。
- 不生成交易建议。
- 不实现自动交易。

## 13. 测试

对应测试文件：

```text
tests/test_4h_kline_manual_backfill.py
```

覆盖内容：

- DB 已有 04、08、16，回补 12 成功插入。
- DB 已有 04、16，回补 08、12 成功插入并接上。
- 已有同 open_time 但字段冲突时 blocked，不写正式 K线。
- Binance 返回批次缺口时 blocked 并报警。
- 回补数据和前一根 DB K线接不上时 blocked 并报警。
- 回补数据和后一根 DB K线接不上时 blocked 并报警。
- 未收盘 K线 blocked 并报警。
- `bulk_upsert` 中途异常时不留下部分正式 K线。
- Redis 锁已存在时不请求 Binance、不写正式 K线。
- Redis 异常时 failed 并报警。
- 不允许恢复旧的失败报警开关。
- 成功但未传 `--notify-success` 不发送成功通知。
- 成功且传 `--notify-success` 发送成功通知。
- 默认测试使用 fake Binance、fake repository、fake task lock、fake alert sender。

已运行：

```bash
.\.venv\Scripts\python.exe -m py_compile app/core/task_lock.py app/market_data/backfill/exceptions.py app/market_data/backfill/types.py app/market_data/backfill/kline_4h_backfill_service.py app/storage/mysql/models/collector_event_log.py app/storage/mysql/repositories/collector_event_log_repository.py scripts/backfill_4h_klines.py tests/test_4h_kline_manual_backfill.py
.\.venv\Scripts\python.exe -m pytest tests/test_4h_kline_manual_backfill.py
.\.venv\Scripts\python.exe -m pytest tests/test_kline_quality_checker.py
.\.venv\Scripts\python.exe -m scripts.backfill_4h_klines --help
```

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用 DeepSeek，不访问交易接口。

## 14. 2026-05-15 补充：手动补 K 未收盘安全阻断通知优化

### 14.1 功能名称

手动 4h K线 backfill 中 `unclosed_kline` 安全阻断的 Hermes/微信通知分级与中文精简模板。

本补充只优化通知分类和用户可见文案，不修改正式 K线写入规则、不绕过质量检查、不新增自动修复、不新增人工改数、不新增自动交易。

### 14.2 发起入口

用户仍然手动执行：

```bash
python -m scripts.backfill_4h_klines --start-utc 2026-05-14T08:00:00Z --end-utc 2026-05-14T20:00:00Z --trigger-source cli --confirm-write
```

入口文件：

`scripts/backfill_4h_klines.py`

入口方法：

`main()`

核心 service 文件：

`app/market_data/backfill/kline_4h_backfill_service.py`

核心 service 方法：

`run_manual_4h_backfill()`

通知构造文件：

`app/market_data/backfill/alerts.py`

通知构造方法：

`_build_backfill_alert_event()`

通用模板文件：

`app/alerting/templates.py`

模板渲染方法：

`render_alert_message()`

### 14.3 核心调用链路

```text
scripts/backfill_4h_klines.py::main
    ↓
app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
    ↓
app/market_data/backfill/quality.py::check_backfill_quality
    ↓
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    ↓
发现请求区间最后一根 K线未收盘，返回 unclosed_kline
    ↓
app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    ↓
app/storage/mysql/repositories/collector_event_log_repository.py::mark_blocked
    ↓
app/market_data/backfill/alerts.py::send_failure_alert_and_adjust_exit_code
    ↓
app/market_data/backfill/alerts.py::_build_backfill_alert_event
    ↓
app/alerting/service.py::send_alert
    ↓
app/alerting/templates.py::render_alert_message
```

### 14.4 安全阻断判定

仅当同时满足以下条件时，通知降级为安全阻断提醒：

- `event_type = manual_backfill_4h`
- `trigger_source = cli`
- `status = blocked`
- `first_issue_type = unclosed_kline`
- 质量问题只来自请求区间最后一根未收盘 K线
- `inserted_count = 0`
- `formal_write_performed = False`
- 本次任务没有写入 `market_kline_4h`
- 系统没有自动修复、没有人工改数、没有自动交易

满足条件时：

- `alert_type = manual_backfill_notice`
- `severity = notice`
- 微信中文展示为 `级别：提醒`
- 标题为 `手动补 K 已安全阻断`

以下场景不降级，仍按 `error` 或 `critical` 发送：

- 历史已收盘区间缺失、断档、不连续
- 正式库已有 K线与 Binance 官方 K线字段冲突
- Binance REST 返回结构异常
- Redis 任务锁异常
- MySQL 写入失败
- 程序异常或无法确认任务是否安全
- scheduler 增量采集相关异常
- 每日一致性复核发现正式库数据异常

### 14.5 微信正文模板

安全阻断消息示例：

```text
【手动补 K 已安全阻断】

级别：提醒
币种周期：BTCUSDT 4h
请求区间：2026-05-14 08:00 UTC ~ 2026-05-14 20:00 UTC

原因：
请求区间包含尚未收盘的 4h K线：2026-05-14 20:00 UTC。

结果：
系统已阻断写入，正式 K线表未被修改。

建议：
如需重试，请将结束时间参数 end-utc 改为最近一根已收盘 K线，例如：
2026-05-14T16:00:00Z

追踪ID：761e04f5167f49efb6e3431897e9ff51

本提醒不是交易建议，不包含自动交易动作。
系统没有自动修复数据，没有人工改数，也没有执行自动交易。
```

用户可见微信正文只保留人工判断必需信息：

- 标题
- 中文级别
- 币种周期
- 请求区间
- 原因
- 结果
- 建议动作
- 追踪ID
- 非交易建议和无自动交易声明

### 14.6 内部字段保留与微信移除

以下字段仍保留在 `collector_event_log`、`data_quality_check.report_json`、`ManualKlineBackfillResult.details` 或 `AlertEvent.details._internal_context` 中，用于审计和排查：

- `formal_write_performed`
- `requested_start_open_time_ms`
- `requested_end_open_time_ms`
- `quality_summary`
- `writable_count`
- `parsed_count`
- `fetched_count`
- `requested_count`
- `first_issue_message`
- `action`
- `data_source`
- `trigger_source`
- `dry_run`

以上字段不再原样渲染到微信正文。`app/alerting/templates.py::render_alert_message()` 在发现 `_wechat_visible_body` 时，只渲染中文精简正文，不展开 `_internal_context`。手动回补属于 K线相关通知；如果精简正文未自行包含边界声明，模板会追加“系统没有自动修复数据，没有人工改数，没有自动回补，也没有执行自动交易”。

### 14.7 数据库、Redis、外部接口与 Hermes

本功能请求外部接口：

- 仍只通过 `BinanceRestClient.get_server_time()` 请求 Binance server time。
- 仍只通过 `BinanceRestClient.get_klines()` 请求 Binance REST `/fapi/v1/klines`。

本功能读取数据库：

- `market_kline_4h`，用于历史回补上下文、冲突和连续性检查。

本功能写入数据库：

- `collector_event_log`，记录 blocked/failed/success/skipped 等任务事件。
- `data_quality_check`，记录质量检查报告。
- `alert_message`，记录 Hermes 通知内容和发送结果。
- `market_kline_4h` 只在质量检查通过且非 dry-run 时写入；本次通知优化没有改变该规则。

本功能读取或写入 Redis：

- 只使用 `kline_write:{symbol}:{interval}` 任务锁。
- 不读取 Redis `bitcoin_price`。
- 不写入 Redis `bitcoin_price`。

本功能发送 Hermes：

- 由 `app/market_data/backfill/alerts.py::send_failure_alert_and_adjust_exit_code()` 决定发送。
- 通过 `app/alerting/service.py::send_alert()` 统一发送。
- 安全阻断使用 `AlertType.MANUAL_BACKFILL_NOTICE`。
- 真正质量异常继续使用 `AlertType.KLINE_DATA_QUALITY_ERROR`。
- 任务失败继续使用 `AlertType.COLLECTOR_ERROR`。
- Hermes `channel_response` 仍由 `alert_message` 记录，并经过脱敏。

本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不新增 scripts。
本功能不涉及自动交易。

### 14.8 异常处理

安全阻断路径：

```text
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    报告 unclosed_kline
app/market_data/backfill/kline_4h_backfill_service.py::_handle_quality_blocked
    捕获质量报告并记录 collector_event_log = blocked
app/market_data/backfill/alerts.py::_build_backfill_alert_event
    判断为安全阻断，生成 notice 提醒
```

该路径不写正式 K线表，不允许 `partial_success`，不重试，不自动修复。

真正异常路径保持不变：

- 历史 K线不连续：`_build_backfill_alert_event()` 继续生成 `severity = error`。
- 数据库写入失败：`_handle_task_failure()` 继续生成 `severity = critical`。
- Redis 或程序异常：继续生成 failed 结果并发送 `COLLECTOR_ERROR`。

Hermes 发送失败时，CLI 仍返回 `3`，不因此修改正式 K线表。

### 14.9 对应测试

测试文件：

`tests/test_4h_kline_manual_backfill.py`

新增或调整覆盖：

- 请求区间包含最后一根未收盘 K线时，任务 blocked、不写正式 K线表、`inserted_count = 0`。
- 安全阻断通知 `alert_type = manual_backfill_notice`。
- 安全阻断通知 `severity = notice`，微信展示 `级别：提醒`。
- 安全阻断微信正文包含中文原因、结果、建议和追踪ID。
- 安全阻断微信正文不包含 `formal_write_performed`、`requested_start_open_time_ms`、`quality_summary`、`action` 等内部字段。
- 历史 K线断档仍然是 `severity = error`，不被降级。
- 数据库写入失败仍然是 `severity = critical`。
- 成功和 dry-run 成功通知改为中文精简正文。

已运行：

```bash
.\.venv\Scripts\python.exe -m py_compile app\alerting\types.py app\alerting\templates.py app\market_data\backfill\alerts.py tests\test_4h_kline_manual_backfill.py
.\.venv\Scripts\python.exe -m pytest tests\test_4h_kline_manual_backfill.py
.\.venv\Scripts\python.exe -m pytest tests\test_alerting.py
.\.venv\Scripts\python.exe -m pytest
```

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用 DeepSeek，不访问交易接口。

### 14.10 本补充不负责

- 不修改正式 K线写入规则。
- 不降低真正历史数据质量异常的 severity。
- 不绕过 `batch_before_persist` 检查。
- 不新增自动修复 K线。
- 不新增人工改数能力。
- 不新增自动交易。
- 不让大模型参与基础告警。
- 不修改 scheduler slot 状态模型。
- 不修改 Hermes gateway 服务本身。
- 不执行数据库迁移。
