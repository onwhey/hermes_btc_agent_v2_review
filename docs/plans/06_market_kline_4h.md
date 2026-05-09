# 06 Market Kline 4h Plan

## 1. 阶段目标

本阶段实现 BTCUSDT 4h 官方 K线的基础数据模型、数据库表、DTO、parser、基础校验和 Repository。

本阶段目标是让后续 4h K线手动回补、4h 增量采集、每日 K线复核等模块有稳定的数据结构和入库基础。

本阶段负责：

1. 创建 `market_kline_4h` 表。
2. 创建 SQLAlchemy model。
3. 创建 Alembic migration。
4. 创建 4h K线 DTO。
5. 创建 Binance REST 原始 K线 parser。
6. 创建基础字段校验工具。
7. 创建 K线 Repository。
8. 创建基础测试。
9. 创建对应实现说明文件。

本阶段只做 4h K线数据基础层，不做采集任务、不做回补任务、不做复核任务、不做 scheduler。

## 2. 本阶段明确不做

本阶段不得实现 K线采集流程、回补流程、复核流程、报警流程、策略流程或交易流程。

禁止实现：

1. Binance REST 请求。
2. 调用 `BinanceRestClient.get_klines()` 发起真实请求。
3. 4h K线自动增量采集。
4. 4h K线手动回补流程。
5. 每日 K线复核流程。
6. scheduler 定时任务。
7. Hermes 报警。
8. collector_event_log 表。
9. data_quality_check 表。
10. 10s 价格监控。
11. WebSocket。
12. Redis 写入 `bitcoin_price`。
13. DeepSeek 或其他大模型调用。
14. 策略分析。
15. 交易建议。
16. 自动下单、自动平仓、自动调仓。
17. Binance 账户、订单、持仓、杠杆、保证金相关接口。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/02_data_collection_requirements.md`
3. `docs/requirements/03_database_and_quality_requirements.md`
4. `docs/architecture/system_architecture.md`
5. `docs/architecture/module_boundaries.md`
6. `docs/architecture/data_flow.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/decisions/0003-kline-table-splitting.md`
10. `docs/plans/01_project_skeleton.md`
11. `docs/plans/02_core_config_logging.md`
12. `docs/plans/03_infra_mysql_redis.md`
13. `docs/plans/04_alerting_through_hermes.md`
14. `docs/plans/05_binance_rest_client.md`
15. `docs/implementation/01_project_skeleton.md`
16. `docs/implementation/02_core_config_logging.md`
17. `docs/implementation/03_infra_mysql_redis.md`
18. `docs/implementation/04_alerting_through_hermes.md`
19. `docs/implementation/05_binance_rest_client.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`
5. `app/storage/mysql/`
6. `app/exchange/binance/types.py`，如已有合适类型

本阶段不得重复实现配置读取、日志初始化、时间转换、数据库 session 管理和 Binance REST 请求逻辑。

## 4. 建议分支

建议分支名：

`feature/06-market-kline-4h`

分支创建、切换、提交、推送、合并由用户人工执行。

Codex 不应自动执行以下 Git 操作：

1. 创建分支。
2. 切换分支。
3. 合并分支。
4. 推送远程仓库。
5. 删除分支。
6. 强制覆盖工作区。

Codex 只负责在用户已经切换好的当前分支内，根据本 plan 修改文件。

## 5. 需要检查和补齐的目录

本阶段应检查以下目录是否存在，不存在才创建：

```
app/market_data/
app/storage/mysql/
app/storage/mysql/models/
app/storage/mysql/repositories/
migrations/versions/
scripts/
tests/
docs/implementation/
```

目录处理原则：

1. 如果目录已经存在，只检查并保留，不得删除后重建。
2. 不得覆盖、清空、移动已有 `docs/` 内容。
3. 不得删除已有 `requirements/`、`architecture/`、`decisions/`、`plans/` 文件。
4. 只允许补齐当前缺失的目录或占位文件。
5. `.gitkeep` 只在目录为空且需要 Git 跟踪时创建，不得覆盖已有文件。

禁止执行类似以下危险操作：

1. 删除整个 `docs/` 后重建。
2. 清空已有文档目录。
3. 删除 `app/market_data/` 后重建。
4. 删除 `app/storage/mysql/` 后重建。
5. 覆盖已有配置、日志、数据库、报警、Binance REST 模块。
6. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/market_data/__init__.py
app/market_data/kline_dto.py
app/market_data/kline_parser.py
app/market_data/kline_validator.py
app/market_data/kline_constants.py

app/storage/mysql/models/__init__.py
app/storage/mysql/models/market_kline_4h.py
app/storage/mysql/repositories/__init__.py
app/storage/mysql/repositories/market_kline_4h_repository.py

migrations/versions/<revision>_create_market_kline_4h.py

scripts/check_market_kline_4h.py
tests/test_market_kline_4h.py
docs/implementation/06_market_kline_4h.md
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. 4h K线表定位

本阶段创建的 `market_kline_4h` 是 BTCUSDT 4h 官方已收盘 K线事实表。

该表只允许保存：

1. 来自 Binance REST `/fapi/v1/klines` 的官方 K线。
2. 已收盘 K线。
3. 经过 parser 转换后的结构化字段。
4. 经过基础字段校验后的 K线。
5. 通过 Repository 幂等写入的数据。

该表禁止保存：

1. 人工手填 K线。
2. 人工修复 K线。
3. manual_repair 数据。
4. human_edit 数据。
5. manual_input 数据。
6. system_repair 数据。
7. 未收盘 K线。
8. WebSocket 聚合 K线。
9. 第三方行情源 K线。
10. 策略计算结果。
11. 交易建议。
12. 账户、订单、持仓数据。

## 8. `market_kline_4h` 表结构要求

建议表名：

`market_kline_4h`

建议字段：

```
id
symbol
interval_value

open_time_ms
open_time_utc
open_time_prc

close_time_ms
close_time_utc
close_time_prc

open_price
high_price
low_price
close_price

volume
quote_volume
trade_count

taker_buy_base_volume
taker_buy_quote_volume

data_source
trigger_source

raw_payload_json
raw_payload_hash

created_at_utc
created_at_prc
updated_at_utc
updated_at_prc
```

字段说明：

1. `symbol`：例如 `BTCUSDT`。
2. `interval_value`：例如 `4h`，避免使用数据库保留字 `interval`。
3. `open_time_ms`：Binance K线 open time，毫秒时间戳。
4. `open_time_utc`：由 `open_time_ms` 转换得到的 UTC 时间。
5. `open_time_prc`：由 UTC 时间转换得到的 PRC 展示时间。
6. `close_time_ms`：Binance K线 close time，毫秒时间戳。
7. `close_time_utc`：由 `close_time_ms` 转换得到的 UTC 时间。
8. `close_time_prc`：由 UTC 时间转换得到的 PRC 展示时间。
9. `open_price`、`high_price`、`low_price`、`close_price`：价格字段。
10. `volume`：成交量。
11. `quote_volume`：成交额。
12. `trade_count`：成交笔数。
13. `taker_buy_base_volume`：主动买入成交量。
14. `taker_buy_quote_volume`：主动买入成交额。
15. `data_source`：数据来源。
16. `trigger_source`：触发来源。
17. `raw_payload_json`：可选，保存 Binance 原始 K线数组的 JSON 形式，便于排查。
18. `raw_payload_hash`：原始 payload 的 hash，便于后续冲突检测。
19. `created_at_utc` / `updated_at_utc`：审计时间，以 UTC 为准。
20. `created_at_prc` / `updated_at_prc`：展示辅助时间。

注意：

1. PRC 时间用于用户阅读和排查。
2. 业务排序、连续性判断、策略判断必须以 UTC 或 `open_time_ms` 为准。
3. 不得使用 PRC 时间作为业务排序依据。
4. 不得使用 PRC 时间判断 K线连续性。

## 9. 唯一键与索引要求

必须设置唯一键：

```
unique(symbol, interval_value, open_time_ms)
```

建议索引：

```
index(symbol, interval_value, open_time_ms)
index(symbol, interval_value, open_time_utc)
index(symbol, interval_value, close_time_ms)
index(data_source)
index(trigger_source)
index(created_at_utc)
```

要求：

1. 同一 `symbol + interval_value + open_time_ms` 只能有一条 K线。
2. 重复写入同一官方 K线时，必须走幂等逻辑。
3. 不得插入重复 K线。
4. 不得靠人工删除重复数据解决问题。

## 10. 字段类型要求

建议字段类型：

1. `id`：BIGINT，自增主键。
2. `symbol`：VARCHAR(32)，非空。
3. `interval_value`：VARCHAR(16)，非空。
4. `open_time_ms`：BIGINT，非空。
5. `close_time_ms`：BIGINT，非空。
6. UTC / PRC 时间字段：DATETIME，非空。
7. 价格和数量字段：DECIMAL(38, 18)，非空。
8. `trade_count`：BIGINT 或 INTEGER，非空。
9. `data_source`：VARCHAR(64)，非空。
10. `trigger_source`：VARCHAR(32)，非空。
11. `raw_payload_json`：JSON 或 TEXT，允许为空。
12. `raw_payload_hash`：VARCHAR(128)，允许为空。
13. `created_at_utc`、`updated_at_utc`：DATETIME，非空。
14. `created_at_prc`、`updated_at_prc`：DATETIME，非空。

要求：

1. 价格不得使用 float。
2. 成交量不得使用 float。
3. 金额不得使用 float。
4. Python 内部应优先使用 `Decimal`。
5. 时间字段不得使用字符串长期存储。
6. Binance 毫秒时间戳必须保存为整数。

## 11. `data_source` 与 `trigger_source` 要求

`market_kline_4h` 表必须保存实际触发来源和数据来源。

允许的 `trigger_source`：

```
scheduler
cli
```

允许的 `data_source`：

```
binance_rest_by_scheduler
binance_rest_by_cli
```

映射规则：

```
trigger_source = scheduler
    ↓
data_source = binance_rest_by_scheduler

trigger_source = cli
    ↓
data_source = binance_rest_by_cli
```

禁止：

1. 根据是否经过 `scripts/*.py` 猜测 `data_source`。
2. 缺少 `trigger_source` 仍写入 K线表。
3. 非法 `trigger_source` 仍写入 K线表。
4. 使用 `manual_repair`。
5. 使用 `human_edit`。
6. 使用 `manual_input`。
7. 使用 `system_repair`。
8. 手工修改 K线数据。
9. 自动修复 K线数据。
10. 用 WebSocket 数据写入正式 4h K线表。

说明：

本阶段只定义字段和基础校验，不实现 scheduler 或 CLI 回补流程。实际 `trigger_source` 由后续采集、回补模块传入。

## 12. SQLAlchemy Model 要求

建议文件：

`app/storage/mysql/models/market_kline_4h.py`

建议类名：

`MarketKline4h`

要求：

1. 字段与 migration 保持一致。
2. 唯一键与索引必须和 migration 保持一致。
3. 价格、数量、金额字段使用 Decimal 对应类型。
4. 不在 model 中发起 Binance 请求。
5. 不在 model 中发送 Hermes。
6. 不在 model 中实现复杂业务流程。
7. 不在 model 中调用 DeepSeek。
8. 不在 model 中自动修复数据。

Model 只描述数据库表结构和基础 ORM 映射。

## 13. Alembic Migration 要求

本阶段允许新增 Alembic migration，用于创建 `market_kline_4h` 表。

要求：

1. migration 文件名应清楚表达用途。
2. 只创建 `market_kline_4h` 表。
3. 不创建 collector_event_log 表。
4. 不创建 data_quality_check 表。
5. 不创建 alert_message 表。
6. 不创建策略表。
7. 不创建建议表。
8. 不插入业务数据。
9. 不写真实密钥。
10. 不硬编码生产数据库连接。

禁止 Codex 自动执行：

```
alembic upgrade head
```

迁移执行由用户人工决定。

Codex 可以生成 migration 文件，但不得自动连接数据库执行迁移。

## 14. DTO 要求

建议文件：

`app/market_data/kline_dto.py`

建议定义：

1. `MarketKlineDTO`
2. `BinanceRawKlineDTO`，如有必要
3. `KlineParseResult`，如有必要

`MarketKlineDTO` 至少包含：

```
symbol
interval_value

open_time_ms
open_time_utc
open_time_prc

close_time_ms
close_time_utc
close_time_prc

open_price
high_price
low_price
close_price

volume
quote_volume
trade_count

taker_buy_base_volume
taker_buy_quote_volume

data_source
trigger_source

raw_payload_json
raw_payload_hash
```

要求：

1. DTO 不依赖 SQLAlchemy session。
2. DTO 不负责写数据库。
3. DTO 不负责请求 Binance。
4. DTO 不负责发送 Hermes。
5. DTO 不负责判断是否应该采集。
6. DTO 只表达一根 K线的结构化数据。

## 15. Parser 要求

建议文件：

`app/market_data/kline_parser.py`

建议方法：

1. `parse_binance_kline(raw_kline, symbol, interval_value, trigger_source)`
2. `parse_binance_klines(raw_klines, symbol, interval_value, trigger_source)`
3. `calculate_raw_payload_hash(raw_kline)`

Parser 负责：

1. 接收 Binance `/fapi/v1/klines` 返回的一根或多根原始 K线数组。
2. 按 Binance K线字段顺序解析字段。
3. 将字符串价格转换为 Decimal。
4. 将毫秒时间戳转换为 UTC datetime。
5. 调用 `app/core/time_utils.py` 转换 PRC 展示时间。
6. 根据 `trigger_source` 生成正确 `data_source`。
7. 生成 `raw_payload_json`。
8. 生成 `raw_payload_hash`。
9. 返回 `MarketKlineDTO`。

Parser 不负责：

1. 请求 Binance。
2. 查询 MySQL。
3. 写 MySQL。
4. 判断本批次是否连续。
5. 判断和数据库最新 K线是否连续。
6. 自动回补缺失 K线。
7. 自动修复 K线。
8. 发送 Hermes。
9. 调用 DeepSeek。

## 16. Binance 原始 K线字段顺序要求

Parser 必须按 Binance `/fapi/v1/klines` 返回数组顺序解析。

字段顺序：

```
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

要求：

1. 必须忽略第 11 位 ignore 字段。
2. 不得把 ignore 字段入库为业务字段。
3. 如果原始数组长度不足，应抛出解析异常。
4. 如果价格或数量无法转换为 Decimal，应抛出解析异常。
5. 如果时间戳无法转换，应抛出解析异常。

## 17. 基础校验要求

建议文件：

`app/market_data/kline_validator.py`

本阶段只实现单根 K线和基础字段校验，不实现完整采集质量检查。

允许校验：

1. symbol 非空。
2. interval_value 等于 `4h`。
3. open_time_ms 小于 close_time_ms。
4. open_time_utc 小于 close_time_utc。
5. open_price > 0。
6. high_price > 0。
7. low_price > 0。
8. close_price > 0。
9. high_price >= open_price。
10. high_price >= close_price。
11. high_price >= low_price。
12. low_price <= open_price。
13. low_price <= close_price。
14. volume >= 0。
15. quote_volume >= 0。
16. trade_count >= 0。
17. taker_buy_base_volume >= 0。
18. taker_buy_quote_volume >= 0。
19. trigger_source 在允许范围内。
20. data_source 与 trigger_source 映射一致。

本阶段不得实现：

1. 本批次 K线连续性校验。
2. DB 最新 K线与新 K线连续性校验。
3. K线缺失检测。
4. K线重复检测。
5. K线复核对比。
6. 自动回补。
7. 自动修复。
8. Hermes 报警。

完整质量检查应在后续 `07_kline_quality_checker.md` 中实现。

## 18. 4h 时间边界要求

本阶段可以提供基础 4h 时间校验工具。

允许校验：

1. `interval_value = 4h`。
2. `close_time_ms` 应大于 `open_time_ms`。
3. 对于 4h K线，理论周期约为 4 小时。
4. Binance K线 `close_time_ms` 通常为下一个周期开始前 1 毫秒。

但注意：

1. 本阶段不根据当前时间判断 K线是否已收盘。
2. 本阶段不负责过滤未收盘 K线。
3. 未收盘过滤应在后续采集、回补、复核 service 中实现。
4. 本阶段不读取 Binance server time。
5. 本阶段不请求 Binance。

## 19. Repository 要求

建议文件：

`app/storage/mysql/repositories/market_kline_4h_repository.py`

建议类名：

`MarketKline4hRepository`

允许方法：

1. `get_by_open_time(symbol, interval_value, open_time_ms)`
2. `get_latest(symbol, interval_value)`
3. `list_by_time_range(symbol, interval_value, start_open_time_ms, end_open_time_ms)`
4. `list_by_open_times(symbol, interval_value, open_time_ms_list)`
5. `bulk_upsert(klines)`
6. `count_by_time_range(symbol, interval_value, start_open_time_ms, end_open_time_ms)`

Repository 负责：

1. 读写 `market_kline_4h` 表。
2. 根据唯一键执行幂等 upsert。
3. 返回 ORM model 或 DTO。
4. 不直接创建数据库 session，优先由调用方传入 session。
5. 不直接提交事务，除非项目统一约定允许。
6. 不吞掉数据库异常。
7. 不打印敏感信息。

Repository 不负责：

1. 请求 Binance。
2. 解析 Binance 原始响应。
3. 判断采集范围。
4. 判断是否需要回补。
5. 判断是否需要报警。
6. 发送 Hermes。
7. 调用 DeepSeek。
8. 执行 scheduler。
9. 自动修复 K线。

## 20. Upsert 幂等要求

`bulk_upsert()` 必须基于唯一键：

```
symbol + interval_value + open_time_ms
```

幂等规则：

1. 如果数据库不存在该 K线，则插入。
2. 如果数据库已存在且字段一致，则不应制造重复数据。
3. 如果数据库已存在但字段不一致，本阶段不得静默覆盖。
4. 字段冲突时应抛出明确异常或返回冲突结果。
5. 冲突处理不得自动修复。
6. 冲突处理不得人工覆盖。
7. 冲突处理不得调用 Hermes。
8. 冲突报警应由后续采集、回补或复核 service 判断后调用 `app/alerting`。

注意：

本阶段可以实现“检测冲突并拒绝覆盖”的基础能力，但不实现完整数据质量事件记录。

## 21. 数据冲突处理要求

如果同一唯一键下，新 K线与已存在 K线出现字段不一致，本阶段必须遵守：

1. 不自动覆盖旧数据。
2. 不自动删除旧数据。
3. 不自动修复旧数据。
4. 不允许 manual_repair。
5. 不允许 human_edit。
6. 不允许 system_repair。
7. 抛出明确异常或返回冲突结果。
8. 由上层 service 后续决定是否记录事件、是否报警、是否停止任务。

需要比较的核心字段至少包括：

1. open_price
2. high_price
3. low_price
4. close_price
5. volume
6. quote_volume
7. trade_count
8. taker_buy_base_volume
9. taker_buy_quote_volume
10. close_time_ms

## 22. 检查脚本要求

建议创建：

`scripts/check_market_kline_4h.py`

该脚本用于人工检查本阶段 K线模型、parser、validator、repository 是否可导入和基础可用。

允许检查：

1. `MarketKlineDTO` 可构造。
2. parser 可以解析一条模拟 Binance raw kline。
3. validator 可以校验一条合法 4h K线。
4. model 可以正常导入。
5. repository 可以正常导入。
6. Alembic migration 文件存在。

禁止该脚本：

1. 请求 Binance。
2. 写 MySQL 正式数据。
3. 连接 Redis。
4. 写 Redis。
5. 创建 `bitcoin_price`。
6. 发送 Hermes。
7. 启动 scheduler。
8. 执行 K线采集。
9. 执行 K线回补。
10. 执行 K线复核。
11. 调用 DeepSeek。
12. 自动执行 Alembic migration。
13. 下单、撤单、调杠杆、读账户、读持仓。

示例运行方式：

```
python -m scripts.check_market_kline_4h
```

说明：

1. 该脚本是人工 CLI 检查入口。
2. 该脚本不得被 scheduler 调用。
3. 该脚本不得承载业务逻辑。
4. 该脚本不得写正式业务数据。
5. 该脚本不得发送报警。

## 23. 测试要求

建议创建：

`tests/test_market_kline_4h.py`

默认测试不得依赖真实 Binance、真实 MySQL、真实 Redis、真实 Hermes。

至少覆盖：

1. `MarketKlineDTO` 可以正常构造。
2. parser 可以解析 Binance raw kline。
3. parser 正确解析 open_time_ms。
4. parser 正确解析 close_time_ms。
5. parser 正确转换 Decimal 字段。
6. parser 正确转换 UTC / PRC 时间。
7. parser 正确生成 data_source。
8. parser 正确生成 raw_payload_hash。
9. 原始数组长度不足时抛出异常。
10. 非法 Decimal 字段抛出异常。
11. validator 接受合法 K线。
12. validator 拒绝 high_price 小于 close_price 的 K线。
13. validator 拒绝 low_price 大于 open_price 的 K线。
14. validator 拒绝非法 trigger_source。
15. validator 拒绝 data_source 与 trigger_source 不匹配。
16. model 可以正常导入。
17. repository 可以正常导入。
18. repository upsert 冲突检测可以被单元测试覆盖或 mock。
19. migration 只创建 `market_kline_4h` 表。
20. 没有 Binance 请求。
21. 没有 Redis 写入。
22. 没有 Hermes 调用。
23. 没有 scheduler。
24. 没有交易执行相关代码。

如果需要真实 MySQL 集成测试，必须使用显式开关，例如：

```
RUN_MYSQL_INTEGRATION_TESTS=true
```

默认 `pytest` 不应连接真实 MySQL。

## 24. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. parser 解析失败。
2. validator 校验失败。
3. repository 查询失败。
4. repository 写入失败。
5. upsert 冲突。
6. 检查脚本运行结果。

禁止记录：

1. 数据库密码。
2. 完整 `.env`。
3. Hermes webhook。
4. token。
5. secret。
6. Authorization。
7. cookie。
8. 账户信息。
9. 持仓信息。
10. 交易信息。

## 25. 异常要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增基础异常：

1. `KlineError`
2. `KlineParseError`
3. `KlineValidationError`
4. `KlineConflictError`

异常要求：

1. 解析失败必须明确指出字段问题。
2. 校验失败必须明确指出规则问题。
3. 冲突异常必须说明冲突唯一键。
4. 冲突异常不得自动覆盖数据。
5. 异常消息不得包含敏感信息。
6. 不得因为异常自动发送 Hermes。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。

## 26. 数据库影响

本阶段允许：

1. 创建 `market_kline_4h` SQLAlchemy model。
2. 创建 `market_kline_4h` Alembic migration。
3. 创建 `market_kline_4h` repository。
4. 提供幂等写入方法。
5. 提供查询方法。

本阶段禁止：

1. 自动执行 migration。
2. 写入真实 K线数据。
3. 运行真实采集。
4. 创建 collector_event_log 表。
5. 创建 data_quality_check 表。
6. 创建策略表。
7. 创建建议表。
8. 修改 `alert_message` 表。
9. 删除任何已有表。
10. 人工修复 K线字段。

## 27. Redis 影响

本阶段不得连接 Redis。

本阶段不得写 Redis。

本阶段不得读取 Redis。

本阶段不得创建：

`bitcoin_price`

价格监控和 Redis 写入应在后续 WebSocket 价格监控阶段实现。

## 28. Binance 影响

本阶段不得请求 Binance。

本阶段只处理模拟 raw kline 或上层传入的 Binance raw kline 数据。

本阶段不得调用：

1. `BinanceRestClient.get_klines()`
2. `/fapi/v1/klines`
3. `/fapi/v1/time`
4. `/fapi/v1/exchangeInfo`
5. `/fapi/v1/ping`

调用 Binance REST 的采集、回补、复核流程应在后续模块中实现。

## 29. Hermes 影响

本阶段不得调用 Hermes。

本阶段不得发送报警。

本阶段不得写 alert_message。

如果 parser、validator 或 repository 发现异常：

1. 本阶段只抛出异常或返回错误结果。
2. 上层 service 后续决定是否记录事件、是否报警。
3. 本阶段不做报警编排。

## 30. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得创建定时任务。

本阶段不得让 scheduler 调用 `scripts/check_market_kline_4h.py`。

scheduler 与 `trigger_source` 的实际运行逻辑应在后续采集相关 plan 中实现。

## 31. WebSocket 和价格监控边界

本阶段不得实现 WebSocket。

本阶段不得实现 10s 价格监控。

本阶段不得创建或使用：

1. WebSocket client。
2. WebSocket manager。
3. WebSocket price event parser。
4. Price monitor service。
5. Redis `bitcoin_price`。
6. REST 最新价格查询。
7. REST 轮询价格。

10s 价格监控后续必须使用 Binance WebSocket 单独实现。

## 32. K线不可人工修改原则

本阶段必须严格遵守：

1. 不允许 manual_repair。
2. 不允许 human_edit。
3. 不允许 manual_input。
4. 不允许 system_repair。
5. 不允许人工直接修改 K线字段。
6. 不允许程序自动修复正式 K线。
7. 不允许复核任务自动修改正式 K线。
8. 不允许采集任务静默覆盖冲突 K线。

即使数据出现问题，也只能由后续手动 CLI 回补任务通过 Binance REST 官方接口重新获取官方已收盘 K线，并按规则写入。

本阶段只提供表结构和 Repository 基础能力，不提供任何人工改数入口。

## 33. 交易安全边界

本阶段以及后续所有阶段均禁止实现：

1. 自动下单。
2. 自动平仓。
3. 自动调仓。
4. 自动加仓。
5. 自动减仓。
6. 读取账户后自动决策。
7. Binance order 接口。
8. Binance account 接口。
9. Binance position 接口。
10. 杠杆调整接口。
11. 保证金模式调整接口。

如果 Codex 添加任何交易执行相关代码，应直接拒绝合并。

## 34. 交付物要求

本阶段完成后，Codex 必须交付：

1. `market_kline_4h` SQLAlchemy model。
2. `market_kline_4h` Alembic migration。
3. `MarketKlineDTO`。
4. Binance raw kline parser。
5. K线基础 validator。
6. `MarketKline4hRepository`。
7. K线基础检查脚本。
8. K线基础测试文件。
9. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/06_market_kline_4h.md`

说明文件必须描述：

1. 本模块入口。
2. `market_kline_4h` 表结构。
3. 唯一键和索引。
4. DTO 字段。
5. parser 解析流程。
6. validator 校验流程。
7. Repository 查询和 upsert 规则。
8. 冲突处理规则。
9. `data_source` 与 `trigger_source` 映射规则。
10. 不允许人工修改 K线的边界。
11. 本模块不负责的边界。
12. 后续哪些模块会复用本模块。

本阶段 implementation 文档必须遵守 `AGENTS.md` 中的“代码可读性与实现说明强制要求”，按功能写清楚入口文件、方法调用链、数据流、异常处理、测试方式和本模块边界。

本阶段说明文件不需要描述：

1. Binance REST 请求流程。
2. K线采集流程。
3. K线手动回补流程。
4. K线复核流程。
5. Hermes 告警流程。
6. Redis 价格缓存流程。
7. WebSocket 价格监控流程。
8. 策略建议流程。

原因：这些能力本阶段不实现。

## 35. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_market_kline_4h` 可以运行成功。
2. `pytest` 默认可以运行成功。
3. 默认测试不请求 Binance。
4. 默认测试不连接真实 MySQL。
5. 默认测试不连接 Redis。
6. 默认测试不发送 Hermes。
7. `market_kline_4h` migration 只创建 K线表。
8. 未创建 collector_event_log 表。
9. 未创建 data_quality_check 表。
10. 未创建策略表。
11. 未创建建议表。
12. `MarketKlineDTO` 可以正常构造。
13. parser 可以解析模拟 Binance raw kline。
14. parser 使用 `app/core/time_utils.py` 转换 PRC 时间。
15. validator 可以拒绝非法字段。
16. validator 可以拒绝非法 `trigger_source`。
17. validator 可以拒绝 `data_source` 映射错误。
18. repository 可以正常导入。
19. repository 不请求 Binance。
20. repository 不发送 Hermes。
21. repository 不调用 DeepSeek。
22. 未实现自动采集。
23. 未实现手动回补流程。
24. 未实现每日复核流程。
25. 未实现 scheduler。
26. 未实现 WebSocket。
27. 未写入 Redis `bitcoin_price`。
28. 未实现交易建议。
29. 未实现交易执行相关代码。
30. `docs/implementation/06_market_kline_4h.md` 已创建或补齐。

## 36. 人工审查清单

合并前用户应人工检查：

1. 查看 migration 是否只创建 `market_kline_4h` 表。
2. 查看表字段是否包含 UTC 和 PRC 时间字段。
3. 查看表字段是否使用 `interval_value`，而不是数据库保留字 `interval`。
4. 查看价格和数量字段是否使用 Decimal 类型。
5. 查看唯一键是否为 `symbol + interval_value + open_time_ms`。
6. 查看是否存在 manual_repair / human_edit / manual_input / system_repair。
7. 查看 parser 是否只解析 raw kline，不请求 Binance。
8. 查看 validator 是否只做基础字段校验，不做完整采集流程。
9. 查看 repository 是否只读写 `market_kline_4h`。
10. 查看 repository 是否存在静默覆盖冲突 K线的风险。
11. 查看检查脚本是否不请求 Binance、不写真实数据库、不发 Hermes。
12. 查看测试是否默认 mock 或使用纯单元测试。
13. 运行测试。
14. 运行检查脚本。

建议搜索：

```
grep -R "manual_repair" app scripts tests migrations
grep -R "human_edit" app scripts tests migrations
grep -R "manual_input" app scripts tests migrations
grep -R "system_repair" app scripts tests migrations
grep -R "collector_event_log" app scripts tests migrations
grep -R "data_quality_check" app scripts tests migrations
grep -R "alert_message" app scripts tests migrations
grep -R "get_klines" app/market_data app/storage scripts tests
grep -R "BinanceRestClient" app/market_data app/storage scripts tests
grep -R "Hermes" app/market_data app/storage scripts tests
grep -R "DeepSeek" app/market_data app/storage scripts tests
grep -R "bitcoin_price" app scripts tests
grep -R "websocket" app scripts tests
grep -R "ticker/price" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果搜索结果只是文档、注释或允许的说明，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 37. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 请求 Binance。
2. 调用 `BinanceRestClient.get_klines()`。
3. 实现 K线自动采集。
4. 实现 K线手动回补流程。
5. 实现每日 K线复核流程。
6. 实现 scheduler。
7. 调用 Hermes。
8. 调用 DeepSeek。
9. 写入 Redis。
10. 创建 `bitcoin_price`。
11. 实现 WebSocket。
12. 实现 REST 最新价格查询。
13. 创建 collector_event_log 表。
14. 创建 data_quality_check 表。
15. 创建策略表。
16. 创建建议表。
17. 自动执行 Alembic migration。
18. 静默覆盖冲突 K线。
19. 自动修复 K线。
20. 人工修改 K线。
21. 添加 manual_repair。
22. 添加 human_edit。
23. 添加 manual_input。
24. 添加 system_repair。
25. 生成交易建议。
26. 实现任何交易执行代码。
27. 提交真实密钥。
28. 提交真实日志。
29. 提交 `.env`。
30. 删除、清空或覆盖已有文档。
31. 把采集、回补、复核业务流程写进 `scripts`。

## 38. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 运行 K线基础检查：

   python -m scripts.check_market_kline_4h

4. 人工确认 migration 没有创建越界表。

5. 人工确认没有异常删除、覆盖或越界实现。

6. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 4h K线基础数据模型"

7. 用户自行推送分支，并进入代码审查流程。