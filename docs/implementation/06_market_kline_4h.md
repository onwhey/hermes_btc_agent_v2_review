# 06 Market Kline 4h 实现说明

## 1. 功能：4h K线 DTO、parser 与基础 validator

### 1.1 发起方式

本阶段不提供采集入口，不请求 Binance。

本阶段只提供纯本地解析、校验和结构化数据对象，供后续 4h 手动回补、增量采集、每日复核和质量检查模块复用。

人工检查入口：

    python -m scripts.check_market_kline_4h

### 1.2 入口文件

DTO：

`app/market_data/kline_dto.py`

入口类：

`MarketKlineDTO`

Parser：

`app/market_data/kline_parser.py`

入口方法：

- `parse_binance_kline()`
- `parse_binance_klines()`
- `calculate_raw_payload_hash()`
- `data_source_from_trigger_source()`

Validator：

`app/market_data/kline_validator.py`

入口方法：

`validate_market_kline()`

常量：

`app/market_data/kline_constants.py`

### 1.3 核心调用链路

    上层后续 service 或本阶段检查脚本
        ↓
    app/market_data/kline_parser.py::parse_binance_kline
        ↓
    app/core/time_utils.py::timestamp_ms_to_utc_datetime
        ↓
    app/core/time_utils.py::utc_aware_to_prc_aware
        ↓
    app/market_data/kline_validator.py::validate_market_kline

本阶段检查脚本链路：

    scripts/check_market_kline_4h.py::main
        ↓
    scripts/check_market_kline_4h.py::collect_market_kline_4h_errors
        ↓
    app/market_data/kline_parser.py::parse_binance_kline
        ↓
    app/market_data/kline_validator.py::validate_market_kline

### 1.4 数据来源

Parser 接收的 raw kline 必须是上层传入的 Binance REST `/fapi/v1/klines` 原始数组。

本阶段不请求外部接口。
本阶段不调用 `BinanceRestClient.get_klines()`。
本阶段不读取数据库。
本阶段不写入数据库。
本阶段不读取 Redis。
本阶段不写入 Redis。
本阶段不发送 Hermes。
本阶段不调用 DeepSeek 或其他大模型。

### 1.5 Binance 原始字段解析

`parse_binance_kline()` 按以下顺序解析：

```text
0  open_time_ms
1  open_price
2  high_price
3  low_price
4  close_price
5  volume
6  close_time_ms
7  quote_volume
8  trade_count
9  taker_buy_base_volume
10 taker_buy_quote_volume
11 ignore
```

第 11 位 ignore 字段只参与 `raw_payload_json` 与 `raw_payload_hash`，不作为业务字段入库。

价格、成交量和成交额全部转换为 `Decimal`，不使用 float。

UTC 时间由 `app/core/time_utils.py::timestamp_ms_to_utc_datetime()` 统一转换。

PRC 展示时间由 `app/core/time_utils.py::utc_aware_to_prc_aware()` 统一转换，PRC 时间只用于阅读和排查，不用于排序或连续性判断。

### 1.6 trigger_source 与 data_source

允许的 `trigger_source`：

- `cli`
- `scheduler`

映射规则：

```text
trigger_source = cli
    ↓
data_source = binance_rest_by_cli

trigger_source = scheduler
    ↓
data_source = binance_rest_by_scheduler
```

Parser 不根据脚本路径、进程名或调用方自动猜测来源。

### 1.7 validator 校验范围

`validate_market_kline()` 只做本阶段允许的单根 K线基础字段校验：

时间完整性校验包括：

- `close_time_ms == open_time_ms + KLINE_4H_INTERVAL_MS - 1`。
- `open_time_ms % KLINE_4H_INTERVAL_MS == 0`，即 open time 必须落在 UTC 4h 边界。
- `open_time_utc` 必须与 `open_time_ms` 经 `timestamp_ms_to_utc_datetime()` 转换后的结果一致。
- `close_time_utc` 必须与 `close_time_ms` 经 `timestamp_ms_to_utc_datetime()` 转换后的结果一致。
- `open_time_prc` 必须等于 `utc_aware_to_prc_aware(open_time_utc)`。
- `close_time_prc` 必须等于 `utc_aware_to_prc_aware(close_time_utc)`。

- `symbol` 非空。
- `interval_value = 4h`。
- `open_time_ms < close_time_ms`。
- `open_time_utc < close_time_utc`。
- OHLC 价格大于 0。
- `high_price` 不小于 open、close、low。
- `low_price` 不大于 open、close。
- 成交量、成交额、成交笔数非负。
- `trigger_source` 在允许范围内。
- `data_source` 与 `trigger_source` 映射一致。

本阶段 validator 不做：

- 不做本批次连续性校验。
- 不做数据库最新 K线衔接校验。
- 不做 K线缺口检测。
- 不做重复 K线检测。
- 不做每日复核对比。
- 不做自动回补。
- 不做自动修复。
- 不发送 Hermes。

未收盘过滤需要 Binance server time。本阶段不请求 Binance server time，因此不在 parser/validator 中实现未收盘过滤。后续采集、回补、复核 service 写入正式表前必须执行已收盘判断。

### 1.8 异常处理

异常类位于：

`app/core/exceptions.py`

新增异常：

- `KlineError`
- `KlineParseError`
- `KlineValidationError`
- `KlineConflictError`

异常路径：

1. `parse_binance_kline()` 发现 raw 数组长度不足、Decimal 字段非法、时间戳非法时抛出 `KlineParseError`。
2. `data_source_from_trigger_source()` 发现非法 `trigger_source` 时抛出 `KlineValidationError`。
3. `validate_market_kline()` 发现字段关系非法或来源映射错误时抛出 `KlineValidationError`。
4. 检查脚本捕获异常并返回非 0 状态码。

本功能不写入事件日志。
本功能不发送 Hermes。
本功能不重试。
本功能不允许 `partial_success`。
本功能不修改正式数据。
本功能不自动修复。

## 2. 功能：market_kline_4h ORM model 与 Alembic migration

### 2.1 入口文件

ORM model：

`app/storage/mysql/models/market_kline_4h.py`

入口类：

`MarketKline4h`

Migration：

`migrations/versions/20260511_06_create_market_kline_4h.py`

Alembic metadata 挂载：

`migrations/env.py`

### 2.2 表定位

`market_kline_4h` 是 BTCUSDT 4h 官方已收盘 K线事实表。

该表只允许保存来自 Binance REST `/fapi/v1/klines` 的官方 K线，并且后续实际写入服务必须保证只写已收盘 K线。

本阶段只创建结构，不执行 `alembic upgrade head`，不连接真实数据库执行迁移。

### 2.3 表字段

核心字段：

- `id`
- `exchange`
- `market_type`
- `symbol`
- `interval_value`
- `open_time_ms`
- `open_time_utc`
- `open_time_prc`
- `close_time_ms`
- `close_time_utc`
- `close_time_prc`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `quote_volume`
- `trade_count`
- `taker_buy_base_volume`
- `taker_buy_quote_volume`
- `data_source`
- `trigger_source`
- `raw_payload_json`
- `raw_payload_hash`
- `created_at_utc`
- `created_at_prc`
- `updated_at_utc`
- `updated_at_prc`

`exchange` 与 `market_type` 用于承接上层文档中的审计身份要求。当前默认由 repository 写入：

- `exchange = binance`
- `market_type = um_futures`

### 2.4 字段类型

- `id`：`BIGINT` 自增主键。
- `open_time_ms` / `close_time_ms`：`BIGINT`。
- UTC / PRC 时间：`DateTime(timezone=True)`。
- 价格、成交量、成交额：`Numeric(38, 18)`。
- `trade_count`：`BIGINT`。
- `raw_payload_json`：`TEXT`。
- `raw_payload_hash`：`VARCHAR(128)`。

价格和数量字段不使用 float。

### 2.5 唯一键与索引

唯一键：

```text
uq_market_kline_4h_symbol_interval_open_time_ms
    symbol + interval_value + open_time_ms
```

索引：

- `idx_market_kline_4h_symbol_interval_open_time_utc`
- `idx_market_kline_4h_symbol_interval_close_time_ms`
- `idx_market_kline_4h_data_source`
- `idx_market_kline_4h_trigger_source`
- `idx_market_kline_4h_created_at_utc`

K线排序、查询最新 K线、范围查询和后续连续性判断都必须基于 `open_time_ms` 或 UTC 字段，不得依赖数据库自增 `id`。

### 2.6 Migration 边界

Migration 只创建 `market_kline_4h` 表。

Migration 不创建：

- `collector_event_log`
- `data_quality_check`
- 策略表
- 建议表
- Redis 结构
- scheduler job
- 交易相关表

Migration 不插入业务数据。
Migration 不写真实密钥。
Migration 不硬编码生产连接。
Migration 不自动执行。

## 3. 功能：MarketKline4hRepository 查询与幂等写入

### 3.1 入口文件

`app/storage/mysql/repositories/market_kline_4h_repository.py`

入口类：

`MarketKline4hRepository`

入口方法：

- `get_by_open_time()`
- `get_latest()`
- `list_by_time_range()`
- `list_by_open_times()`
- `count_by_time_range()`
- `bulk_upsert()`
- `find_conflicting_core_fields()`

### 3.2 核心调用链路

后续服务写入链路：

    future service
        ↓
    app/market_data/kline_parser.py::parse_binance_kline
        ↓
    app/market_data/kline_validator.py::validate_market_kline
        ↓
    app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert
        ↓
    app/storage/mysql/models/market_kline_4h.py::MarketKline4h
        ↓
    caller-provided SQLAlchemy session

本阶段没有 service 自动调用该链路。

### 3.3 Repository 读写边界

Repository 负责：

- 读取 `market_kline_4h`。
- 根据 `symbol + interval_value + open_time_ms` 查询单根 K线。
- 按 `open_time_ms` 查询最新 K线和时间范围。
- 根据唯一键执行幂等写入。
- 已存在且核心字段一致时跳过。
- 已存在但核心字段冲突时抛出异常。

Repository 不负责：

- 不创建数据库 session。
- 不提交事务。
- 不请求 Binance。
- 不解析 raw Kline。
- 不判断采集范围。
- 不判断是否需要回补。
- 不判断是否需要报警。
- 不发送 Hermes。
- 不调用 DeepSeek。
- 不执行 scheduler。
- 不自动修复 K线。

### 3.4 bulk_upsert 幂等规则

唯一键：

```text
symbol + interval_value + open_time_ms
```

规则：

1. 数据库不存在该 K线时，`bulk_upsert()` 新增一行。
2. 数据库已存在且核心字段一致时，视为幂等重复执行，跳过写入。
3. 数据库已存在但核心字段不一致时，抛出 `KlineConflictError`。
4. 冲突时不覆盖旧数据。
5. 冲突时不删除旧数据。
6. 冲突时不修复旧数据。
7. 冲突时不发送 Hermes。
8. 冲突时由后续上层 service 决定是否记录事件、质量结果或报警。

比较的核心字段：

- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `quote_volume`
- `trade_count`
- `taker_buy_base_volume`
- `taker_buy_quote_volume`
- `close_time_ms`

### 3.5 数据写入字段

新增行由 `_model_from_dto()` 将 `MarketKlineDTO` 转换为 `MarketKline4h`。

新增时写入：

- DTO 中的时间、OHLCV、source、raw payload 字段。
- `exchange = binance`
- `market_type = um_futures`
- `created_at_utc` / `updated_at_utc` 使用 `app/core/time_utils.py::now_utc()`。
- `created_at_prc` / `updated_at_prc` 使用 `app/core/time_utils.py::utc_aware_to_prc_aware()`。

Repository 不使用 PRC 时间进行排序、连续性判断或唯一性判断。

### 3.6 异常处理

异常路径：

1. `bulk_upsert()` 会先调用 `validate_market_kline()`；基础字段非法时抛出 `KlineValidationError`。
2. `get_by_open_time()` 查询数据库失败时，SQLAlchemy 或底层数据库异常向上抛出。
3. 已存在记录核心字段不一致时，`bulk_upsert()` 抛出 `KlineConflictError`，消息包含唯一键和冲突字段。
4. `db_session.add()` 或 `flush()` 失败时，底层数据库异常向上抛出。

Repository 不捕获并吞掉数据库异常。
Repository 不写 `collector_event_log`。
Repository 不写 `data_quality_check`。
Repository 不写 `alert_message`。
Repository 不重试。
Repository 不提交事务。
Repository 不允许 `partial_success`。
Repository 不修改冲突旧数据。
Repository 不自动修复。

## 4. 功能：本地检查脚本

### 4.1 发起方式

用户手动执行：

    python -m scripts.check_market_kline_4h

该脚本只允许 CLI 手动触发，不允许 scheduler 调用。

### 4.2 入口文件

`scripts/check_market_kline_4h.py`

入口方法：

- `main()`
- `collect_market_kline_4h_errors()`

### 4.3 检查内容

脚本检查：

1. `MarketKlineDTO` 可通过 parser 构造。
2. parser 可以解析一条模拟 Binance raw kline。
3. validator 可以校验一条合法 4h K线。
4. `MarketKline4h` model 可以导入。
5. `MarketKline4hRepository` 可以导入。
6. migration 文件存在。

### 4.4 脚本不负责

脚本不请求 Binance。
脚本不写 MySQL 正式数据。
脚本不连接 Redis。
脚本不写 Redis。
脚本不创建 `bitcoin_price`。
脚本不发送 Hermes。
脚本不启动 scheduler。
脚本不执行 K线采集。
脚本不执行 K线回补。
脚本不执行 K线复核。
脚本不调用 DeepSeek。
脚本不自动执行 Alembic migration。
脚本不执行任何自动交易。

## 5. 数据流说明

本阶段允许的数据流：

```text
模拟 raw Binance Kline
    ↓
app/market_data/kline_parser.py::parse_binance_kline
    ↓
MarketKlineDTO
    ↓
app/market_data/kline_validator.py::validate_market_kline
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert
    ↓
market_kline_4h
```

本阶段默认测试只使用模拟 raw kline 和 fake session，不连接真实 MySQL。

本阶段不包含真实外部请求数据流。

## 6. 本阶段明确不负责

- 不请求真实 Binance。
- 不调用 `BinanceRestClient.get_klines()`。
- 不实现 K线采集流程。
- 不实现手动回补流程。
- 不实现增量采集流程。
- 不实现每日 K线复核流程。
- 不实现完整 K线质量检查。
- 不实现未收盘过滤 service。
- 不实现 scheduler。
- 不实现 WebSocket。
- 不实现 10s 价格监控。
- 不读取 Redis。
- 不写入 Redis。
- 不创建 `bitcoin_price`。
- 不发送 Hermes。
- 不写 `alert_message`。
- 不调用 DeepSeek 或其他大模型。
- 不实现策略分析。
- 不生成交易建议。
- 不实现自动下单、自动平仓、自动调仓或任何交易执行。
- 不执行 `alembic upgrade head`。

## 7. 对应测试

测试文件：

`tests/test_market_kline_4h.py`

覆盖内容：

- `MarketKlineDTO` 可以正常构造。
- parser 可以解析模拟 Binance raw kline。
- parser 正确解析 `open_time_ms` 和 `close_time_ms`。
- parser 正确转换 Decimal 字段。
- parser 正确转换 UTC / PRC 时间。
- parser 正确生成 `data_source`。
- parser 正确生成 `raw_payload_hash`。
- 原始数组长度不足时抛出 `KlineParseError`。
- 非法 Decimal 字段抛出 `KlineParseError`。
- validator 接受合法 K线。
- validator 拒绝 OHLC 关系非法。
- validator 拒绝非法 `trigger_source`。
- validator 拒绝 `data_source` 映射错误。
- validator 拒绝 `close_time_ms` 不等于 `open_time_ms + 4h - 1ms`。
- validator 拒绝 `open_time_ms` 不在 UTC 4h 边界。
- validator 拒绝 UTC 时间字段与毫秒时间戳不一致。
- validator 拒绝 PRC 时间字段不是由 UTC 时间字段统一转换得到。
- model 可以正常导入。
- repository 可以正常导入。
- repository upsert 插入、跳过和冲突检测使用 fake session 覆盖。
- migration 只创建 `market_kline_4h` 表。
- AST import 检查确认本阶段模块不导入 Binance REST client、alerting、Redis、scheduler。

测试类型：

- 全部是本地单元测试。
- 默认 pytest 不访问真实 Binance。
- 默认 pytest 不连接真实 MySQL。
- 默认 pytest 不连接真实 Redis。
- 默认 pytest 不发送真实 Hermes。
- 默认 pytest 不调用 DeepSeek。
- 默认 pytest 不访问交易接口。

本阶段没有真实 MySQL 集成测试。如后续需要，应使用显式开关，例如：

    RUN_MYSQL_INTEGRATION_TESTS=true

## 8. 人工运行检查

推荐命令：

    python -m scripts.check_market_kline_4h
    python -m pytest tests/test_market_kline_4h.py -q
    python -m pytest

如果使用仓库内虚拟环境，Windows 下可执行：

    .\.venv\Scripts\python.exe -m scripts.check_market_kline_4h
    .\.venv\Scripts\python.exe -m pytest tests/test_market_kline_4h.py -q

## 9. 后续模块复用

- `07_kline_quality_checker.md`：复用 DTO、validator 和 repository 的只读查询能力。
- `08_4h_backfill.md`：复用 parser、validator、repository，并在 service 中补充已收盘过滤、批次连续性检查和事件记录。
- `09_4h_incremental_collector.md`：复用 parser、validator、repository，并在 service 中补充重叠窗口、缺口识别和任务来源处理。
- `11_daily_kline_integrity_check.md`：复用 parser 和 repository 查询，对照 Binance REST 官方数据但不写正式 K线表。

## 10. 边界自检

- 自动交易：未实现。
- K线数据来源：本阶段只解析模拟或上层传入的 Binance REST raw kline，不请求外部数据。
- manual_repair / human_edit / manual_input / system_repair：未作为代码能力实现，未作为允许 data_source。
- REST / WebSocket 边界：未实现 REST 请求，未实现 WebSocket。
- trigger_source / data_source：已实现 `cli`、`scheduler` 到允许 data_source 的显式映射，不自动猜测。
- scripts 边界：检查脚本只做本地导入、解析和校验，不承载采集、回补或复核业务流程。
- scheduler 边界：未实现 scheduler，检查脚本不允许 scheduler 调用。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：本阶段不发送 Hermes。
- MySQL / Redis 边界：仅定义 ORM、migration 和 repository；默认检查与测试不连接真实 MySQL，不读取或写入 Redis。
- 敏感信息提交：未提交 `.env`、真实密钥或真实日志。
