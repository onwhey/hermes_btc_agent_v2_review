# 07 Kline Quality Checker 实现说明

本文件对应 `docs/plans/07_kline_quality_checker.md`。当前阶段只实现 K线质量检查，不实现 08 或后续采集、回补、scheduler、每日复核、10s 价格监控、策略、交易建议或自动交易。

## 1. 功能：批次入库前质量检查

### 1.1 发起入口

后续业务 service 直接调用：

`app/market_data/kline_quality/service.py`

入口方法：

`check_batch_before_persist(klines, server_time_ms, latest_db_kline=None, check_trigger_source="service")`

本阶段没有新增正式 K线写入入口；该方法只返回质量检查结果。

### 1.2 核心调用链路

```text
app/market_data/kline_quality/service.py::check_batch_before_persist
    ↓
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    ↓
app/market_data/kline_quality/rules.py::validate_single_kline_as_quality_issue
    ↓
app/market_data/kline_validator.py::validate_market_kline
```

### 1.3 输入和输出

输入：

- `MarketKlineDTO` 批次。
- `server_time_ms`，必须由调用方传入，来源应为 Binance server time 或测试 fixture。
- `check_trigger_source`，允许 `cli`、`scheduler`、`service`，它只描述质量检查触发来源，不等同于正式 K线写入的 `trigger_source`。

输出：

- `KlineQualityReport`。
- 通过时 `status=passed`，`writable_klines` 为可交给后续写入流程的 K线。
- 失败时 `status=failed`，`issues` 给出明确原因，`writable_klines` 为空。

### 1.4 检查规则

每根 K线先复用 06 的：

`app/market_data/kline_validator.py::validate_market_kline`

随后检查：

- 批次必须按 `open_time_ms` 升序。
- 批次内不得有重复 `open_time_ms`。
- 相邻两根 4h K线 `open_time_ms` 差值必须等于 `14400000`。
- 不得包含未收盘 K线；判断方式是 `close_time_ms < server_time_ms`。
- 未使用本机时间判断收盘。
- UTC / PRC 转换仍由 06 validator 通过 `app/core/time_utils.py` 校验。

### 1.5 外部接口、数据库、Redis、Hermes

本功能不请求外部接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不涉及 scripts。
本功能不写 `market_kline_4h`。
本功能不自动修复、不自动回补、不自动覆盖、不自动删除正式 K线。

## 2. 功能：与数据库已有 K线进行质量检查

### 2.1 发起入口

`app/market_data/kline_quality/service.py`

入口方法：

`check_against_database(db_session, klines, server_time_ms, repository=None, check_trigger_source="service")`

也可直接调用：

`app/market_data/kline_quality/db_checker.py::check_kline_batch_against_database`

### 2.2 核心调用链路

```text
app/market_data/kline_quality/service.py::check_against_database
    ↓
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    ↓
app/market_data/kline_quality/db_checker.py::check_kline_batch_against_database
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::get_latest
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::list_by_open_times
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::find_conflicting_core_fields
```

### 2.3 数据库读取和冲突规则

读取表：

`market_kline_4h`

读取方法：

- `get_latest()`：读取数据库最新 K线，用于判断第一根新 K线是否连续。
- `list_by_open_times()`：读取本批次中已经存在的 open time。

规则：

- 如果数据库已有最新 K线，本批次第一根新 K线必须满足 `first_new.open_time_ms == latest.open_time_ms + 14400000`。
- 如果本批次包含数据库已存在 K线，字段一致时作为连续性上下文，放入 `existing_open_time_ms`，不进入 `writable_klines`。
- 如果已存在 K线字段冲突，返回 `database_conflict` issue，阻断写入，`writable_klines` 为空。
- 字段比较复用 06 repository 的 `find_conflicting_core_fields()`。

### 2.4 写入边界

本功能读取 `market_kline_4h`。
本功能不写入 `market_kline_4h`。
本功能不提交事务。
本功能不请求 Binance。
本功能不读取或写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不自动修复、不自动回补、不自动覆盖、不自动删除正式 K线。

## 3. 功能：近期 K线一致性检查

### 3.1 发起入口

人工 CLI：

```text
python -m scripts.check_kline_quality_4h
```

默认只执行本地 smoke check，不请求 Binance，不连接 MySQL，不写 `data_quality_check`，不发送 Hermes。

真实近期一致性检查必须显式执行：

```text
python -m scripts.check_kline_quality_4h --run-real-check --trigger-source cli --limit 100
```

入口文件：

`scripts/check_kline_quality_4h.py`

入口方法：

`main()`

脚本只允许 `--trigger-source cli`。本阶段不允许 scheduler 调用该脚本。

### 3.2 核心调用链路

```text
scripts/check_kline_quality_4h.py::main
    ↓
app/storage/mysql/session.py::session_scope
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
```

### 3.3 数据来源

近期一致性检查中的官方对照数据只能来自：

`Binance REST /fapi/v1/klines`

server time 来自：

`Binance REST /fapi/v1/time`

本阶段不会把 Binance 返回的 K线写入正式 K线表，只用于对照检查。

近期一致性检查会请求 `limit + 1` 根原始 K线，然后基于 Binance `server_time_ms`
过滤 `close_time_ms >= server_time_ms` 的未收盘 K线，只取最后 `limit` 根已收盘 K线继续比对。
当前未收盘 K线不会被记为数据质量异常。
如果过滤后已收盘 K线数量不足 `limit`，返回 `insufficient_closed_klines` issue，不会静默通过。

### 3.4 比对规则

- 官方 K线先解析为 `MarketKlineDTO`。
- 官方 K线先过滤未收盘 K线，只有已收盘 K线进入批次检查。
- 官方已收盘 K线再通过批次检查，包含 06 单根校验、连续性、重复、未收盘判断。
- 数据库通过 `list_by_time_range()` 读取同一 open time 范围。
- 官方存在但数据库缺失，返回 `missing_in_database`。
- 数据库存在但官方近期范围没有返回，返回 `extra_in_database`。
- 同一 `open_time_ms` 字段不一致，返回 `database_field_mismatch`。
- 不自动修复缺口。
- 不自动回补缺口。
- 不自动覆盖冲突 K线。
- 不自动删除异常 K线。

### 3.5 外部接口、数据库、Redis、Hermes

本功能在人工执行真实脚本时会请求 Binance 公共 REST：

- `/fapi/v1/time`
- `/fapi/v1/klines`

默认 pytest 不请求 Binance，测试使用 fake client 和内存对象。

本功能读取数据库表：

- `market_kline_4h`

本功能写入数据库表：

- `data_quality_check`

本功能不写入正式 K线表。
本功能不读取 Redis。
本功能不写入 Redis。
本功能默认 smoke check 不发送 Hermes。
真实检查 `--run-real-check` 发现质量问题时默认通过 `app/alerting/service.py::send_alert` 使用固定模板发送 Hermes 失败通知。
真实检查成功时默认不发送成功通知；只有 `--send-success-alert` 或 `--daily-health-report` 会发送成功通知。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。

## 4. 功能：data_quality_check 记录

### 4.1 文件和方法

Model：

`app/storage/mysql/models/data_quality_check.py::DataQualityCheck`

Repository：

`app/storage/mysql/repositories/data_quality_check_repository.py::DataQualityCheckRepository`

创建记录方法：

`create_quality_check_record(db_session, report)`

Migration：

`migrations/versions/20260511_07_create_data_quality_check.py`

### 4.2 写入字段

写入字段包括：

- `check_type`
- `symbol`
- `interval_value`
- `check_trigger_source`
- `status`
- `severity`
- `checked_count`
- `issue_count`
- `start_open_time_ms` / `start_open_time_utc` / `start_open_time_prc`
- `end_open_time_ms` / `end_open_time_utc` / `end_open_time_prc`
- `report_json`
- `first_issue_type`
- `first_issue_message`
- `alert_sent`
- `alert_message_id`
- `created_at_utc` / `created_at_prc`
- `updated_at_utc` / `updated_at_prc`

PRC 时间只用于阅读和排查，由 `app/core/time_utils.py::utc_aware_to_prc_aware` 生成。

### 4.3 数据库边界

Repository 只写 `data_quality_check`。
Repository 不 commit。
Repository 不写 `market_kline_4h`。
Repository 不读取或写入 Redis。
Repository 不发送 Hermes。
Repository 不请求 Binance。
Repository 不调用 DeepSeek 或其他大模型。
Repository 不自动修复、不自动覆盖、不自动删除正式 K线。

Migration 只创建 `data_quality_check` 表，不创建其他业务表，不插入业务数据。

未执行：

```text
alembic upgrade head
```

## 5. Hermes 边界

质量检查失败后可以由：

`app/market_data/kline_quality/service.py::send_quality_alert_if_needed`

构造固定模板 `AlertEvent`，并调用：

`app/alerting/service.py::send_alert`

默认 smoke check 不发送 Hermes。

近期一致性检查脚本只有在 `--run-real-check` 时进入真实检查路径。真实检查失败默认请求真实 Hermes 失败通知；真实检查成功默认不通知，只有 `--send-success-alert` 或 `--daily-health-report` 会请求真实 Hermes 成功通知。所有真实发送仍受 04 阶段 Hermes 配置约束。

本阶段不调用 DeepSeek，不生成交易建议。

## 6. 异常处理

质量检查内部的业务失败默认返回 `KlineQualityReport`：

- `empty_batch`
- `invalid_kline`
- `batch_symbol_mismatch`
- `batch_interval_mismatch`
- `batch_not_sorted`
- `duplicate_open_time`
- `batch_not_continuous`
- `unclosed_kline`
- `insufficient_closed_klines`
- `database_not_continuous`
- `database_conflict`
- `missing_in_database`
- `extra_in_database`
- `database_field_mismatch`

无法继续执行的输入错误或依赖错误使用：

- `KlineQualityError`
- `KlineIntegrityCheckError`
- `KlineContinuityError`
- `KlineDataMismatchError`
- `KlineUnclosedError`

数据库读取失败由 repository 或 SQLAlchemy 向上抛出。本阶段不吞异常，不自动重试，不 partial write 正式 K线，不自动修复数据。

## 7. 对应测试

测试文件：

`tests/test_kline_quality_checker.py`

覆盖：

- 批次连续通过。
- 批次断档失败。
- 批次重复失败。
- 未收盘 K线失败。
- 批次内 symbol 不一致失败。
- 批次内 interval_value 不一致失败。
- `open_time_ms` 不连续失败。
- 近期一致性检查过滤最后一根未收盘 K线。
- 过滤未收盘后已收盘 K线数量不足时返回 `insufficient_closed_klines`。
- 数据库最新 K线与本批次连续。
- 数据库最新 K线与本批次断档。
- 数据库已存在且字段一致的 K线可作为上下文，不重复写入。
- 数据库已存在但字段冲突时阻断。
- 质量失败时不调用正式 K线写入。
- 不调用自动修复、回补、覆盖相关入口。
- `data_quality_check` repository 可用 fake session 写入测试记录。
- migration 只创建 `data_quality_check`。
- 脚本默认 `main()` 只做纯本地 smoke check。
- 脚本只有 `--run-real-check` 才进入真实检查路径。

默认测试：

- 不连接真实 MySQL。
- 不请求真实 Binance。
- 不连接 Redis。
- 不发送真实 Hermes。
- 不调用 DeepSeek 或其他大模型。
- 不访问任何交易接口。

## 8. 本地检查命令

新增测试：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_kline_quality_checker.py
```

全量测试：

```text
.\.venv\Scripts\python.exe -m pytest
```

纯本地脚本 smoke check 可由测试覆盖：

```text
python -c "from scripts.check_kline_quality_4h import collect_kline_quality_4h_errors; print(collect_kline_quality_4h_errors())"
```

真实近期一致性检查需要用户人工决定是否执行：

```text
python -m scripts.check_kline_quality_4h --run-real-check --trigger-source cli --limit 100
```

该命令会请求 Binance 公共 REST，并读取 MySQL、写入 `data_quality_check`，本次 Codex 未执行。

## 9. 本阶段明确不负责

- 不实现 08 或后续 plans。
- 不实现手动回补。
- 不实现增量采集。
- 不实现 scheduler。
- 不实现每日复核。
- 不实现 10s WebSocket 价格监控。
- 不写 Redis。
- 不写正式 K线表。
- smoke check 不发送真实 Hermes；`--run-real-check` 失败默认发送 Hermes 失败通知，成功默认不发送成功通知。
- 不调用 DeepSeek 或其他大模型。
- 不实现策略、交易建议、建议生命周期。
- 不实现自动下单、自动平仓、自动调仓、自动撤单、自动调整杠杆或保证金模式。
- 不执行 `alembic upgrade head`。
- 不执行任何 Git 操作。

## 10. 2026-05-12 补充：真实质量检查与 Hermes 健康通知策略

### 10.1 功能名称

真实 K 线质量检查的固定模板 Hermes 微信通知策略。

本补充只修正 07 质量检查与报警策略，不引用、不合并、不依赖 08 手动回补代码。

### 10.2 发起入口

本地 smoke check：

```text
python -m scripts.check_kline_quality_4h
```

本地 smoke check 只解析内置样例并执行本地校验，不请求 Binance，不连接 MySQL，不写 `data_quality_check`，不写 `alert_message`，不发送 Hermes。

真实最近 N 根检查：

```text
python -m scripts.check_kline_quality_4h --run-real-check --trigger-source cli --limit 100
```

每日健康报告：

```text
python -m scripts.check_kline_quality_4h --run-real-check --trigger-source cli --limit 100 --daily-health-report
```

`--run-real-check` 模式下，只要检查结果失败，默认发送 Hermes 失败通知。检查成功默认不发送成功通知。`--daily-health-report` 模式下，成功和失败都会发送 Hermes 通知。`--send-success-alert` 可用于只为成功结果开启成功通知。

### 10.3 入口文件与方法

入口文件：

`scripts/check_kline_quality_4h.py`

入口方法：

`main()`

核心 service：

`app/market_data/kline_quality/service.py`

核心方法：

- `run_recent_kline_integrity_check()`
- `send_quality_alert_if_needed()`
- `send_quality_task_failure_alert()`

### 10.4 核心调用链

```text
scripts/check_kline_quality_4h.py::main
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
    ↓
app/storage/mysql/repositories/alert_message_repository.py
```

检查任务自身异常时，不会生成正常 `KlineQualityReport`，脚本会进入异常路径：

```text
scripts/check_kline_quality_4h.py::main
    ↓
app/market_data/kline_quality/service.py::send_quality_task_failure_alert
    ↓
app/alerting/service.py::send_alert
    ↓
app/storage/mysql/repositories/alert_message_repository.py
```

### 10.5 输入与输出

输入：

- `symbol`，默认 `BTCUSDT`。
- `interval`，当前只允许 `4h`。
- `limit`，最近 N 根官方已收盘 K 线。
- `trigger_source`，07 脚本只允许 `cli`。
- `--run-real-check`，显式开启真实检查。
- `--daily-health-report`，每日健康报告模式，成功和失败都通知。
- `--send-success-alert`，检查成功时也通知。

输出：

- smoke check 返回本地检查结果。
- 真实检查返回 `KlineQualityReport` 的文本摘要。
- 检查成功返回退出码 `0`。
- 检查失败且报警发送成功返回退出码 `2`。
- 检查报告已生成但 Hermes 发送失败返回退出码 `3`。
- 检查任务自身异常返回退出码 `4`，并尝试发送任务失败通知。

### 10.6 报警规则

真实质量检查只要发现任何 K 线问题，必须发送 Hermes 失败通知，包括：

- Binance REST 返回批次不连续。
- 新数据与数据库最新 K 线接不上。
- 最近 N 根官方 K 线中数据库缺失。
- 数据库已有 K 线与 Binance 官方 K 线字段冲突。
- 数据库 K 线重复、乱序、时间间隔异常、未收盘误写。
- parser 或 06 validator 发现字段非法。
- 检查任务自身异常，导致无法确认健康状态。

最近 N 根检查通过时，只有 `--daily-health-report` 或 `--send-success-alert` 会发送成功通知。成功通知使用 `AlertType.KLINE_INTEGRITY_CHECK_PASSED`，失败通知使用 `AlertType.KLINE_INTEGRITY_CHECK_FAILED` 或 `AlertType.KLINE_DATA_QUALITY_ERROR`。

报警内容由 `app/alerting/templates.py` 和 `app/market_data/kline_quality/service.py` 固定生成，不调用 DeepSeek，不调用任何大模型，不生成交易建议，不包含做多、做空、开仓、平仓、止损、止盈等交易建议内容。

### 10.7 Hermes 失败处理

Hermes 发送失败不得静默吞掉：

- `app/alerting/service.py::send_alert` 会在传入 MySQL session 和 repository 时写入并更新 `alert_message` 状态。
- `scripts/check_kline_quality_4h.py::main` 会记录日志。
- 检查报告已生成但报警失败时，CLI 返回 `3`。
- 检查任务自身异常时，即使任务失败通知也发送失败，CLI 仍返回 `4`，避免任务系统误判为完全成功。

### 10.8 数据影响与边界

本功能请求外部接口：仅在 `--run-real-check` 时通过统一 Binance REST client 请求 `/fapi/v1/time` 和 `/fapi/v1/klines`。

本功能读取数据库：仅在 `--run-real-check` 时读取 `market_kline_4h` 做对比。

本功能写入数据库：仅在 `--run-real-check` 时写入 `data_quality_check`，并在真实报警路径写入或更新 `alert_message`。

本功能不写入正式 K 线表 `market_kline_4h`。

本功能不读取 Redis。

本功能不写入 Redis。

本功能不实现 scheduler。

本功能不实现采集、回补、复核、自动修复、自动覆盖、自动删除。

本功能不发送 smoke check 报警。

本功能不调用 DeepSeek 或任何大模型。

本功能不涉及账户、订单、持仓、杠杆、保证金或任何自动交易能力。

### 10.9 对应测试

测试文件：

`tests/test_kline_quality_checker.py`

新增覆盖：

- 最近 N 根健康时，`--daily-health-report` 发送成功通知。
- 最近 N 根缺失时，`--run-real-check` 默认发送失败通知。
- 批次不连续时，发送失败通知。
- 数据库接不上时，发送失败通知。
- Hermes 发送失败时，命令返回非 `0`，不会被误判为完全成功。
- smoke check 不发送 Hermes。

测试命令：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_kline_quality_checker.py tests/test_alerting.py
```
