# 15 MarketContextSnapshot 市场上下文快照实现说明

## 1. 本阶段实现模块

本阶段新增 `app/market_context/`，实现 BTCUSDT 4h + 1d 的只读市场事实快照：

1. `snapshot_types.py`：请求、结果、状态、持久化 DTO 和 CLI 摘要格式。
2. `snapshot_service.py`：对外主入口，编排读取、质量检查、payload 构建、持久化和可选告警。
3. `snapshot_repository.py`：读取正式 4h / 1d K线、采集事件、质量复核记录，并只写 snapshot 表。
4. `snapshot_quality.py`：检查初始化、新鲜度、复核状态、数量、已收盘和连续性。
5. `snapshot_builder.py`：组装市场事实 payload 和 K线引用。
6. `snapshot_alerts.py`：为 blocked / failed 生成中文 Hermes 固定模板提醒。
7. `scripts/build_market_context_snapshot.py`：人工 CLI 入口，只解析参数并调用 service。

本阶段新增 Alembic migration：

`migrations/versions/20260516_15_create_market_context_snapshot.py`

本次小修新增 `scripts/check_kline_integrity_1d.py` 作为 1d 每日复核的人工 CLI 入口。该入口只调用既有 `app/market_data/kline_integrity/kline_1d_integrity_service.py::run_daily_1d_kline_integrity_check`，用于生成 snapshot 所需的 1d `data_quality_check` 前置记录，不绕过质量检查。

## 2. MarketContextSnapshot 职责

MarketContextSnapshot 只保存某一时刻 BTCUSDT 4h + 1d 的市场事实输入，便于后续模块基于同一个 `snapshot_id` 追溯当时使用了哪些 K线。

本阶段 MarketContextSnapshot 不生成策略结论，不生成交易建议，不调用大模型，不请求 Binance，不读取账户，不读取持仓，不修改正式 K线表，不执行自动交易。

## 3. 发起方式

用户手动执行：

```bash
python -m scripts.build_market_context_snapshot \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --confirm-write
```

dry-run：

```bash
python -m scripts.build_market_context_snapshot \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --dry-run
```

如果 snapshot 因 1d 每日复核记录缺失而 blocked，用户可以先手动运行只读 1d 复核：

```bash
python -m scripts.check_kline_integrity_1d \
  --symbol BTCUSDT \
  --interval 1d \
  --trigger-source cli \
  --lookback-count 500
```

`scripts/check_kline_integrity_1d.py` 是 cli only；scheduler 不调用该 script。它读取 `market_kline_1d`，通过 app service 写入 `data_quality_check` 与 `collector_event_log`，不会写 `market_kline_1d` / `market_kline_4h`，不会自动修复、自动回补或人工改数，不调用 DeepSeek 或任何大模型，不执行交易。

本阶段没有接入 scheduler；scheduler 不调用 scripts。

## 4. 入口文件

CLI 入口：

`scripts/build_market_context_snapshot.py`

入口方法：

`main()`

核心 service：

`app/market_context/snapshot_service.py`

核心方法：

`build_market_context_snapshot()`

## 5. 核心调用链路

```text
scripts/build_market_context_snapshot.py::main
    ↓
app/market_context/snapshot_service.py::build_market_context_snapshot
    ↓
app/market_context/snapshot_repository.py::list_recent_4h_klines
app/market_context/snapshot_repository.py::list_recent_1d_klines
app/market_context/snapshot_repository.py::get_latest_collector_event
app/market_context/snapshot_repository.py::get_latest_daily_quality_check
    ↓
app/market_context/snapshot_quality.py::check_market_context_snapshot_readiness
    ↓
app/market_context/snapshot_builder.py::build_market_context_snapshot_payload
app/market_context/snapshot_builder.py::build_blocked_snapshot_payload
    ↓
app/market_context/snapshot_repository.py::create_snapshot_with_refs
    ↓
app/market_context/snapshot_alerts.py::send_market_context_snapshot_alert_and_adjust_exit_code
```

Hermes 只有在用户显式传入 `--notify-on-blocked` 或 `--notify-on-failed` 时触发。

## 6. 配置

读取配置：

```text
MARKET_CONTEXT_SYMBOL=BTCUSDT
MARKET_CONTEXT_BASE_INTERVAL=4h
MARKET_CONTEXT_HIGHER_INTERVAL=1d
MARKET_CONTEXT_4H_LOOKBACK_COUNT=180
MARKET_CONTEXT_1D_LOOKBACK_COUNT=365
```

配置读取发生在 `app/core/config.py`，默认值定义在 `app/core/constants.py`。

## 7. 数据来源和数据库读写

读取数据库表：

1. `market_kline_4h`
2. `market_kline_1d`
3. `collector_event_log`
4. `data_quality_check`

写入数据库表：

1. `market_context_snapshot`
2. `market_context_snapshot_kline_ref`

本功能不请求外部接口。
本功能不读取 Redis。
本功能不写入 Redis。
本功能默认不发送 Hermes，只有显式通知参数开启时发送。
本功能不调用 DeepSeek 或任何大模型。
本功能不涉及 scheduler。
本功能不涉及正式 K线写入的 `data_source` 映射。

## 8. repository 职责

`snapshot_repository.py` 负责：

1. 通过现有 4h / 1d repository 读取最近 K线窗口。
2. 读取对应周期最近一次采集事件。
3. 读取对应周期最近一次每日复核记录。
4. 写入 snapshot 主表和 kline_ref 引用表。

repository 不请求 Binance，不发送 Hermes，不读写 Redis，不写正式 K线表，不 commit；commit 由 service 控制。

## 9. quality checker 职责

`snapshot_quality.py` 负责在生成 payload 前检查：

1. 4h 和 1d 是否已初始化。
2. 最新 4h / 1d 是否滞后理论最新已收盘 K线。
3. 最近采集事件是否存在失败或阻断状态。
4. 最近每日复核是否为 `passed` 或 `healthy`。
5. K线数量是否满足 lookback。
6. 所有 K线是否已收盘。
7. `open_time_ms` 是否对齐 UTC 周期边界。
8. `close_time_ms` 是否等于 `open_time_ms + interval_ms - 1`。
9. 相邻 K线 `open_time_ms` 是否连续。
10. 4h 与 1d 最近每日复核记录的 `end_open_time_ms` 是否覆盖当前 snapshot 窗口最新 K线；复核缺失、失败或覆盖范围落后都会返回 blocked。

连续性判断只使用 UTC 毫秒时间戳，不使用 PRC 时间。
该检查不会自动修复、自动回补、人工改数或请求 Binance。

## 10. builder 职责

`snapshot_builder.py` 负责把已通过质量检查的 4h + 1d K线转换为可持久化 payload。

created payload 包含：

1. `snapshot_id`
2. `symbol`
3. `base_interval`
4. `higher_interval`
5. `generated_at_utc`
6. 最新 4h / 1d open time
7. `lookback`
8. `actual_count`
9. `data_freshness`
10. `quality`
11. `kline_ranges`
12. `kline_refs`
13. `klines`
14. `source_tables`
15. `trigger_source`
16. `trace_id`
17. `boundary`

`klines` 只包含市场事实字段，例如 id、open_time、OHLCV、成交笔数和 taker buy 字段。payload 不包含做多、做空、开仓、平仓、止盈、止损、仓位、杠杆或任何策略结论。

blocked payload 是精简结构，不包含完整 payload，也不包含 K线数组。

## 11. 表结构

### 11.1 market_context_snapshot

主表字段：

```text
id
snapshot_id
symbol
base_interval_value
higher_interval_value
status
blocked_reason
error_message
latest_4h_open_time_ms
latest_4h_open_time_utc
latest_1d_open_time_ms
latest_1d_open_time_utc
lookback_4h_count
lookback_1d_count
actual_4h_count
actual_1d_count
start_4h_open_time_ms
end_4h_open_time_ms
start_1d_open_time_ms
end_1d_open_time_ms
latest_4h_data_quality_status
latest_1d_data_quality_status
latest_4h_collector_event_id
latest_1d_collector_event_id
latest_4h_quality_check_id
latest_1d_quality_check_id
snapshot_payload_json
created_by
trigger_source
trace_id
created_at_utc
updated_at_utc
```

唯一键：

`UNIQUE(snapshot_id)`

主要索引：

1. `(symbol, base_interval_value, higher_interval_value, created_at_utc)`
2. `(status, created_at_utc)`
3. `(trace_id)`

### 11.2 market_context_snapshot_kline_ref

引用表字段：

```text
id
snapshot_id
symbol
interval_value
market_kline_id
open_time_ms
open_time_utc
sequence_no
created_at_utc
```

唯一键：

1. `UNIQUE(snapshot_id, interval_value, sequence_no)`
2. `UNIQUE(snapshot_id, interval_value, open_time_ms)`

主要索引：

1. `(snapshot_id)`
2. `(symbol, interval_value, open_time_ms)`

## 12. 状态说明

`created`：4h + 1d 输入全部通过前置检查，且非 dry-run + confirm-write 时会写入主表和引用表。

`blocked`：数据前置条件不满足，例如未初始化、数据滞后、复核失败、数量不足、未收盘或不连续。blocked 不代表系统自动修复，也不代表自动回补。

`failed`：数据库读取、payload 构建、写入或其他未预期异常。failed 可按参数发送 Hermes。
如果调用方显式传入 confirm-write 且当前数据库仍可写，service 会尽力写入一条精简 `status=failed` 主表记录；如果该记录也写入失败，则回滚并保留原始 failed 返回结果。

## 13. dry-run 与 confirm-write

dry-run：

1. 执行同样的读取和质量检查。
2. 可以返回 created 或 blocked 摘要。
3. 不写入 `market_context_snapshot`。
4. 不写入 `market_context_snapshot_kline_ref`。
5. 默认不发送 Hermes，除非显式开启通知参数。

confirm-write：

1. 非 dry-run 写入必须显式传入。
2. created 时写入主表和 kline_ref。
3. blocked 时可写入 blocked 主表记录，但不写入 kline_ref。
4. 写入失败返回 failed。

## 14. Hermes 通知

blocked 通知触发条件：

`--notify-on-blocked` 显式开启，且结果为 blocked。

failed 通知触发条件：

`--notify-on-failed` 显式开启，且结果为 failed。

通知实现：

`app/market_context/snapshot_alerts.py::send_market_context_snapshot_alert_and_adjust_exit_code`

调用：

`app/alerting/service.py::send_alert`

通知特点：

1. 中文固定模板。
2. blocked 使用 warning 级别。
3. failed 使用 error 级别。
4. 不输出完整 payload。
5. 不输出 K线数组。
6. 不输出内部 Python 对象。
7. 不宣称微信已送达或发送成功。
8. Hermes 失败只调整 exit_code，不改变原始 snapshot 状态。
9. 不调用 DeepSeek 或任何大模型。

## 15. 异常处理

参数非法：

`snapshot_service.py::_validate_market_context_snapshot_request` 返回 `failed` 和参数错误退出码，不读写业务表。

前置质量阻断：

`snapshot_quality.py::check_market_context_snapshot_readiness` 返回 blocked reason，service 返回 `blocked`；如果 confirm-write 且非 dry-run，则写入 blocked 主表记录。

读取或写入异常：

`snapshot_service.py::build_market_context_snapshot` 捕获异常，执行 session rollback，返回 `failed`；如果 confirm-write 且非 dry-run，会尝试写入精简 failed 主表记录；如果开启 failed 通知，则尝试发送 Hermes。

Hermes 异常：

由 `app/alerting` 返回发送结果；service 保留原始 `blocked` 或 `failed` 状态，仅把退出码调整为告警失败。

本阶段不允许 partial_success；任何 created payload 写入失败都返回 failed。

## 16. 本阶段明确没有实现

本阶段没有实现策略模块。
本阶段没有新增 `app/strategy/`。
本阶段没有实现自动交易。
本阶段没有读取账户。
本阶段没有读取持仓。
本阶段没有下单。
本阶段没有调用 DeepSeek。
本阶段没有调用任何大模型。
本阶段没有生成交易建议。
本阶段没有请求 Binance。
本阶段没有修改正式 K线表。
本阶段没有让 scheduler 调用 scripts。

## 17. 测试

对应测试目录：

`tests/market_context/`

覆盖内容：

1. 快照生成成功。
2. 4h 未初始化 blocked。
3. 1d 未初始化 blocked。
4. 4h / 1d 数据滞后 blocked。
5. 最近复核失败 blocked。
6. K线数量不足 blocked。
7. 未收盘 K线 blocked。
8. K线不连续 blocked。
9. dry-run 不写表。
10. 不请求 Binance。
11. 不修改正式 K线表。
12. 不生成交易建议。
13. Hermes blocked / failed 通知为中文模板，且不输出完整 payload 或 K线数组。
14. 1d quality 缺失时 blocked。
15. 1d quality 为 healthy / passed 且 `end_open_time_ms` 覆盖最新 1d K线时，snapshot 可继续。
16. 1d integrity CLI 只允许人工 `cli` 触发，不允许 scheduler 通过 script 触发。

默认测试使用 fake repository、fake session 和 fake alert sender，不访问真实 MySQL、Redis、Binance、Hermes 或大模型。

运行：

```bash
python -m pytest tests/market_context
```

项目虚拟环境可运行：

```bash
.\.venv\Scripts\python.exe -m pytest tests\market_context
```
