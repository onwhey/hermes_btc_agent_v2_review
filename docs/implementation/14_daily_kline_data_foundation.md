# 14 BTCUSDT 1d 日 K 数据基础实现说明

## 1. 当前实现范围

第 14 阶段当前已完成两个小步：

1. 14-1：创建 BTCUSDT 1d 独立正式 K线表、ORM model、基础 repository。
2. 14-2：实现 BTCUSDT 1d 日 K 人工 CLI 回补。

本实现说明只描述已经落地的能力。第 14 阶段后续的 1d scheduler 增量采集、1d 每日复核、1d runtime status 展示和第 15 阶段 MarketContextSnapshot 尚未实现。

## 2. 功能：1d 独立正式 K线表

### 2.1 数据表

正式 1d K线写入独立物理表：

```text
market_kline_1d
```

该表不与 4h 正式表混用，不把 1d 数据写入 `market_kline_4h`，也不通过混合表加 `interval_value` 区分周期。

### 2.2 ORM 与 repository

ORM model：

```text
app/storage/mysql/models/market_kline_1d.py::MarketKline1d
```

Repository：

```text
app/storage/mysql/repositories/market_kline_1d_repository.py::MarketKline1dRepository
```

主要能力：

1. 按 `symbol + open_time_ms` 查询单根 1d K线。
2. 查询某个 symbol 最新 1d K线。
3. 查询某个 symbol 最近 N 根 1d K线。
4. 幂等批量写入缺失 1d K线。
5. 查询写入范围前后邻居，用于 1d 回补连续性检查。

### 2.3 唯一键与幂等

`market_kline_1d` 使用 `symbol + open_time_ms` 唯一约束防止重复写入。

Repository 的批量写入接口只插入缺失记录，不覆盖已存在正式 K线。如果已存在记录与 REST 返回字段冲突，上层 1d 回补 service 会在写入前 blocked，不会静默覆盖。

### 2.4 Alembic migration

迁移文件：

```text
migrations/versions/20260516_14_create_market_kline_1d.py
```

该 migration 只创建 `market_kline_1d`，不修改 4h 表结构，不新增策略表、建议表或快照表。

## 3. 功能：人工 CLI 回补 1d K线

### 3.1 发起方式

用户手动执行：

```powershell
python -m scripts.backfill_1d_klines `
  --symbol BTCUSDT `
  --interval 1d `
  --start-utc "2025-01-01T00:00:00Z" `
  --end-utc "2026-05-15T00:00:00Z" `
  --trigger-source cli `
  --confirm-write `
  --notify-success
```

`scripts/backfill_1d_klines.py` 只允许人工 CLI 调用，不允许 scheduler 调用。`--trigger-source` 只接受 `cli`。

### 3.2 入口文件

入口文件：

```text
scripts/backfill_1d_klines.py
```

入口方法：

```text
main()
```

脚本只负责解析参数、校验 UTC 边界、创建依赖并调用 app service。脚本不直接请求 Binance，不直接写 MySQL，不直接发送 Hermes，不承载核心回补流程。

### 3.3 核心调用链路

```text
scripts/backfill_1d_klines.py::main
    ↓
app/market_data/backfill/kline_1d_backfill_service.py::run_manual_1d_backfill
    ↓
app/market_data/backfill/kline_1d_pipeline.py::fetch_raw_1d_klines_for_backfill
    ↓
app/exchange/binance/client.py::BinanceRestClient.get_klines
    ↓
app/market_data/backfill/kline_1d_pipeline.py::parse_1d_backfill_klines
    ↓
app/market_data/kline_parser.py::parse_binance_klines
    ↓
app/market_data/backfill/kline_1d_quality.py::check_1d_backfill_quality
    ↓
app/storage/mysql/repositories/market_kline_1d_repository.py::MarketKline1dRepository
    ↓
app/market_data/backfill/kline_1d_persistence.py::persist_1d_backfill_klines
```

如果启用提醒：

```text
app/market_data/backfill/kline_1d_backfill_service.py::run_manual_1d_backfill
    ↓
app/market_data/backfill/kline_1d_alerts.py::send_1d_success_alert_and_adjust_exit_code
或
app/market_data/backfill/kline_1d_alerts.py::send_1d_failure_alert_and_adjust_exit_code
    ↓
app/alerting/service.py::send_alert_event
```

### 3.4 参数规则

`--interval` 必须为 `1d`。

`--trigger-source` 必须为 `cli`。

`--start-utc` 与 `--end-utc` 必须是 UTC `00:00:00` 边界，例如：

```text
2025-01-01T00:00:00Z
```

当前实现使用 inclusive open-time 边界：`start-utc` 和 `end-utc` 都表示 1d K线开盘时间。按 14-2 验收要求，`start-utc` 必须早于 `end-utc`，`start >= end` 会被拒绝。

未携带 `--confirm-write` 且不是 `--dry-run` 时，service 会拒绝执行，不请求 Binance、不写正式表。

### 3.5 数据来源

1d 正式 K线只能来自：

```text
Binance REST /fapi/v1/klines
```

请求统一通过：

```text
app/exchange/binance/client.py::BinanceRestClient.get_klines
```

本功能不使用 WebSocket K线，不使用第三方行情源，不使用人工输入数据，不请求交易私有接口。

### 3.6 写入表与数据来源字段

写入表：

```text
market_kline_1d
```

不会写入：

```text
market_kline_4h
```

正式 K线写入时：

```text
trigger_source = cli
data_source = binance_rest_by_cli
interval_value = 1d
```

## 4. 未收盘日 K 过滤语义

1d 回补优先使用 Binance server time 判断 K线是否已收盘。

正常情况：

1. 如果 Binance REST 返回当前 UTC 当天尚未收盘的 1d K线，`check_1d_backfill_quality()` 会过滤该 K线。
2. 当前未收盘日 K 被过滤不视为 error。
3. 如果过滤后剩余已收盘 K线连续且字段合理，允许写入剩余缺失日 K。
4. 成功摘要中会记录 `filtered_unclosed_count`。

异常情况：

1. 如果未收盘日 K 已经进入 `market_kline_1d`，质量检查会 blocked。
2. 如果 REST 返回批次中出现非预期未收盘 K线或时间边界异常，质量检查会 blocked。
3. blocked 不会写入正式 1d 表，不会自动删除、覆盖或修复数据。

## 5. 连续性与字段质量检查

写库前由：

```text
app/market_data/backfill/kline_1d_quality.py::check_1d_backfill_quality
```

检查：

1. `open_time_ms` 必须按 86,400,000 毫秒连续。
2. `open_time_utc` 必须落在 UTC `00:00:00`。
3. `close_time_ms` 必须等于 `open_time_ms + 86,400,000 - 1`。
4. REST 批次内不得重复 open time。
5. 与数据库前后邻居必须连续。
6. 已存在正式 1d K线若字段冲突，blocked。
7. 价格字段必须大于 0。
8. 成交量不得为负数。
9. high/low/open/close 关系必须合理。

检查失败时不会写入 `market_kline_1d`。

## 6. 幂等写入规则

写入由：

```text
app/market_data/backfill/kline_1d_persistence.py::persist_1d_backfill_klines
```

通过：

```text
app/storage/mysql/repositories/market_kline_1d_repository.py::MarketKline1dRepository.bulk_upsert_missing
```

完成。

规则：

1. 已存在 `symbol + open_time_ms` 的 1d K线跳过。
2. 缺失且通过质量检查的已收盘 1d K线才写入。
3. 不覆盖已存在正式 K线。
4. 重复执行同一回补命令不会产生重复记录。
5. 统计会返回 `inserted_count`、`skipped_existing_count`、`filtered_unclosed_count`。

## 7. 事件记录

1d 手动回补使用 collector event 记录运行结果。

事件类型：

```text
manual_backfill_1d
```

关键字段：

```text
symbol = BTCUSDT
interval_value = 1d
trigger_source = cli
data_source = binance_rest_by_cli
requested_start_open_time_ms
requested_end_open_time_ms
fetched_count
parsed_count
filtered_unclosed_count
skipped_existing_count
inserted_count
issue_count
trace_id
```

状态：

1. `success`：质量检查通过，写入或 dry-run 完成。
2. `blocked`：质量检查失败、时间不连续、字段异常、已有正式数据冲突或正式表存在未收盘日 K。
3. `failed`：Binance 请求、解析、Redis lock、MySQL 写入或其他任务异常。
4. `skipped`：同一 `symbol + interval=1d` 回补任务正在运行，当前任务跳过。

## 8. Hermes 提醒

提醒由：

```text
app/market_data/backfill/kline_1d_alerts.py
```

构造，并通过 `app/alerting` 统一发送。

提醒特点：

1. 中文标题与正文明确写明 `1d 日 K`。
2. blocked / failed 使用异常级别。
3. `--notify-success` 时成功结果可发送摘要提醒。
4. 正常过滤当前未收盘日 K不作为 error。
5. 正文不展开完整内部 context、完整 REST 数据或完整 SQL 结果。
6. 不写“微信发送成功”或“微信已送达”。
7. 保留边界声明：系统没有自动修复、没有人工改数、没有自动回补，也没有执行自动交易。
8. dry-run 的 blocked / failed 结果不提交真实 Hermes；真实写入模式下 blocked / failed 会提交固定模板提醒。

Hermes HTTP 2xx 只代表已提交 Hermes，不代表微信最终送达。

## 9. 异常处理

参数错误：

1. `scripts/backfill_1d_klines.py::_resolve_utc_bounds` 或 `validate_1d_backfill_request()` 拒绝。
2. 不请求 Binance，不写 MySQL，不写 Redis。
3. 返回参数错误退出码。

Redis lock 异常：

1. `run_manual_1d_backfill()` 捕获。
2. 记录 failed 事件。
3. 可发送 Hermes failed 提醒。
4. 不写正式 1d K线。

Binance 请求或解析异常：

1. `fetch_raw_1d_klines_for_backfill()` 或 `parse_1d_backfill_klines()` 抛出。
2. `run_manual_1d_backfill()` 捕获并记录 failed。
3. 可发送 Hermes failed 提醒。
4. 不写正式 1d K线。

质量检查失败：

1. `check_1d_backfill_quality()` 返回 blocked report。
2. `run_manual_1d_backfill()` 记录 data_quality_check 与 collector_event_log。
3. 可发送 Hermes blocked 提醒。
4. 不写正式 1d K线。

MySQL 写入异常：

1. `persist_1d_backfill_klines()` 捕获并 rollback 当前写入 savepoint 或事务。
2. `run_manual_1d_backfill()` 记录 failed。
3. 可发送 Hermes failed 提醒。
4. 不自动重试，不自动修复。

Hermes 发送失败：

1. 不回滚已经完成的正式 1d K线写入。
2. 返回 alert failed 退出码。
3. 不重复发送，不自动重试。

## 10. 本功能不负责

本功能不实现 1d scheduler。

本功能不实现 1d 增量采集。

本功能不实现 1d 每日复核。

本功能不修改 runtime status。

本功能不实现 MarketContextSnapshot。

本功能不生成策略建议。

本功能不调用 DeepSeek、GPT、Claude 或其他大模型。

本功能不执行自动交易。

本功能不修改 `market_kline_4h`。

本功能不修改 Hermes gateway。

## 11. 测试

对应测试：

```text
tests/test_market_kline_1d.py
tests/test_1d_kline_manual_backfill.py
tests/test_4h_kline_manual_backfill.py
tests/test_kline_quality_checker.py
tests/test_alerting.py
tests/test_runtime_status.py
```

默认 pytest 使用 fake Binance client、fake repository、fake lock、fake alert sender 或 SQLite，不请求真实 Binance，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

常用检查命令：

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest tests/test_market_kline_1d.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_1d_kline_manual_backfill.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_4h_kline_manual_backfill.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_kline_quality_checker.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_alerting.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_status.py -q
```
