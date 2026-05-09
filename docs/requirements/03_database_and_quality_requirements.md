# 03_database_and_quality_requirements.md

# 数据库与数据质量需求

## 1. 文档目的

本文档定义 Hermes + DeepSeek BTC 合约策略辅助系统在数据库设计、行情数据存储、数据质量检查、异常处理、审计追踪和后续策略扩展方面的需求。

本文档重点约束：

1. 数据库应该保存什么。
2. 数据库不能保存什么。
3. 行情数据如何保证可靠。
4. 数据异常时如何处理。
5. 哪些异常必须阻断后续流程。
6. 哪些异常必须通过 Hermes 提醒。
7. 后续策略、复盘、评估能力需要哪些数据库预留原则。

本文档不负责：

1. 具体 Python 代码实现。
2. 完整 SQL 字段定义。
3. Alembic migration 代码细节。
4. DeepSeek 策略分析逻辑。
5. Admin 后台设计。
6. 自动交易相关任何设计。

具体开发步骤由 `docs/plans/` 下的阶段计划文件定义。

---

## 2. 当前阶段范围

第一阶段只实现数据采集层和提醒基础层相关的数据库能力。

第一阶段必须覆盖：

1. 4h K线数据存储。
2. 4h K线历史回补记录。
3. 4h K线增量采集记录。
4. K线数据质量检查结果。
5. 采集事件日志。
6. 提醒消息记录。
7. Hermes 微信发送结果记录。
8. Alembic 数据库迁移管理。

第一阶段不实现：

1. DeepSeek 策略分析表。
2. 完整 `strategy_signal` 表。
3. 完整 `strategy_advice` 表。
4. 回测系统表。
5. 多策略评估表。
6. Admin 后台表。
7. 自动交易相关任何表。
8. 账户同步表。
9. 订单执行表。
10. 持仓同步表。

但第一阶段数据库设计不得阻碍后续策略、复盘、评估、审计能力扩展。

---

## 3. 数据库设计核心原则

### 3.1 MySQL 负责长期数据

MySQL 用于保存长期历史数据和审计数据，包括：

1. K线数据。
2. 采集日志。
3. 数据质量检查结果。
4. 提醒记录。
5. 后续策略信号。
6. 后续策略建议。
7. 后续策略运行快照。
8. 后续策略复盘结果。
9. 后续人工执行记录。
10. 后续模型调用记录。

Redis 只能保存短期运行状态，不能替代 MySQL。

---

### 3.2 Redis 只保存短期状态

Redis 用于保存：

1. 最新价格。
2. 上一次价格。
3. 实时价格提醒冷却状态。
4. 临时运行状态。
5. 后续当前有效建议摘要。
6. 后续短期风险事件状态。

Redis 数据允许过期。

Redis 数据不能作为长期复盘依据。

---

### 3.3 UTC 是唯一业务时间标准

数据库中的业务时间字段必须以 UTC 为唯一业务时间标准。

以下判断必须基于 UTC：

1. K线排序。
2. K线连续性检查。
3. K线缺口检查。
4. 策略运行时间。
5. 建议编号。
6. 建议生命周期。
7. 回测时间范围。
8. 提醒冷却窗口。
9. 任务调度判断。
10. 数据质量检查时间范围。

禁止使用 PRC 时间作为业务排序、连续性检查、策略回测或建议编号依据。

---

### 3.4 保留 PRC 时间字段用于阅读和排查

为方便用户阅读、人工排查和 Admin 后台展示，数据库中的核心时间字段可以保留对应的 PRC 派生字段。

要求：

1. PRC 字段只能由对应 UTC 字段计算得到。
2. PRC 字段不得作为 K线排序依据。
3. PRC 字段不得作为连续性检查依据。
4. PRC 字段不得作为策略回测依据。
5. PRC 字段不得作为建议编号依据。
6. PRC 字段不得作为提醒冷却判断依据。
7. 如果 UTC 与 PRC 不一致，以 UTC 为准。
8. PRC 字段写入必须统一通过时间工具函数转换。
9. 禁止在业务代码中到处手写 `+8 小时`。

示例：

```text
open_time_utc
open_time_prc

close_time_utc
close_time_prc

created_at_utc
created_at_prc

updated_at_utc
updated_at_prc

sent_at_utc
sent_at_prc
```

---

### 3.5 不依赖数据库 id 判断行情顺序

数据库自增 `id` 只能作为数据库内部主键。

禁止使用 `id` 判断：

1. K线时间顺序。
2. K线是否连续。
3. 行情是否缺失。
4. 跨周期 K线关联。
5. 策略使用了哪些行情。
6. 策略复盘证据链。
7. 最新 K线是哪一根。

K线顺序必须使用：

```text
open_time_ms
open_time_utc
```

K线连续性必须根据周期时间间隔判断。

例如 4h K线连续性判断：

```text
next.open_time_ms - current.open_time_ms == 4 * 60 * 60 * 1000
```

---

### 3.6 可变行情表不能作为唯一复盘证据

行情主表可以被回补、修正、更新。

后续策略复盘不能只依赖当前行情主表。

原因：

1. 同一根 K线可能后续被交易所修正。
2. 历史回补可能更新已有数据。
3. 策略运行时看到的数据，可能与复盘时数据库里的数据不同。
4. 如果不保存当时证据，后续无法解释当时为什么给出某条建议。

因此，后续策略运行时必须保存策略当时看到的行情快照。

例如后续可设计：

```text
strategy_run_kline_snapshot
```

第一阶段不实现该表，但数据库设计必须保留这个方向。

---

## 4. 第一阶段核心数据表

第一阶段至少需要以下数据库表或等价结构：

```text
market_kline_4h
collector_event_log
data_quality_check
alert_message
alembic_version
```

说明：

1. `market_kline_4h`：保存通过质量检查的 4h K线。
2. `collector_event_log`：保存采集任务运行记录。
3. `data_quality_check`：保存数据质量检查结果。
4. `alert_message`：保存提醒消息记录和 Hermes 返回结果。
5. `alembic_version`：由 Alembic 管理数据库迁移版本。

禁止第一阶段创建以下表：

```text
account
order
position
trade_execution
auto_order
auto_close_position
auto_rebalance
```

本项目不允许自动交易。

---

## 5. K线表设计原则

### 5.1 K线按周期分表

K线数据后续按周期分表。

规划表：

```text
market_kline_1m
market_kline_4h
market_kline_1d
```

第一阶段优先实现：

```text
market_kline_4h
```

第一阶段的验收对象是 `market_kline_4h`。

如果后续提前实现 `market_kline_1m` 或 `market_kline_1d`，必须通过独立计划文件和独立 Git 分支完成，不得夹带在 4h 表实现中。

---

### 5.2 4h K线是第一阶段主表

`market_kline_4h` 是第一阶段最重要的行情表。

它用于：

1. 历史 K线回补。
2. 增量 K线采集。
3. 数据质量检查。
4. 后续 4h 主策略评估。
5. 后续策略建议证据来源。
6. 后续策略复盘基础数据。

4h K线必须来自 Binance REST 已收盘 K线。

禁止使用 WebSocket 或 tick 数据自行拼接 4h K线并作为标准行情入库。

---

### 5.3 K线身份唯一键

K线必须有业务唯一键。

唯一键至少包含：

```text
exchange
market_type
symbol
interval
open_time_ms
```

其中第一阶段固定为：

```text
exchange = binance
market_type = um_futures
symbol = BTCUSDT
interval = 4h
```

`open_time_ms` 是 Binance K线开盘时间毫秒时间戳。

同一业务唯一键只能对应一根 K线。

---

### 5.4 K线时间字段

K线表应至少保存：

```text
open_time_ms
open_time_utc
open_time_prc
close_time_ms
close_time_utc
close_time_prc
```

要求：

1. `open_time_ms` 与 `open_time_utc` 必须表达同一时刻。
2. `close_time_ms` 与 `close_time_utc` 必须表达同一时刻。
3. `open_time_prc` 必须由 `open_time_utc` 转换得到。
4. `close_time_prc` 必须由 `close_time_utc` 转换得到。
5. 4h K线的 `open_time_utc` 必须落在 4 小时边界。
6. 4h K线之间的间隔必须为 4 小时。
7. 所有业务判断以 UTC 字段为准。

---

### 5.5 K线价格与数量字段

K线表应保存 Binance 返回的核心字段：

```text
open_price
high_price
low_price
close_price
volume
quote_volume
trade_count
taker_buy_volume
taker_buy_quote_volume
```

字段要求：

1. 价格字段使用高精度 Decimal 类型，不使用 float 作为数据库存储类型。
2. 成交量和成交额使用高精度 Decimal 类型。
3. `trade_count` 使用整数类型。
4. 所有价格字段必须大于 0。
5. 所有数量字段不允许为负数。
6. `high_price` 必须大于等于 `open_price`、`close_price`、`low_price`。
7. `low_price` 必须小于等于 `open_price`、`close_price`、`high_price`。
8. `volume`、`quote_volume`、`taker_buy_volume`、`taker_buy_quote_volume` 不允许为负。

---

### 5.6 数据来源字段

K线表必须保存权威数据来源，并记录实际触发来源。

建议字段：

- `data_source`

第一阶段允许值：

- `binance_rest_by_scheduler`
- `binance_rest_by_cli`

说明：

1. `data_source` 表示“行情数值获取通道 + 实际触发来源”。
2. 4h 标准 K线的数值来源必须是 Binance U 本位合约 REST 接口返回的官方已收盘 K线。
3. `binance_rest_by_scheduler` 表示 `trigger_source = scheduler` 的任务调用 Binance REST 写入。
4. `binance_rest_by_cli` 表示 `trigger_source = cli` 的任务调用 Binance REST 写入。
5. 是否经过 `scripts/*.py` 文件不是判断依据；实际触发来源才是判断依据。
6. 周期信息已经由 `interval` 字段表达，不建议在 `data_source` 中写成 `binance_rest_4h`。

`data_source` 的 scheduler / cli 后缀必须由显式触发来源决定。

如果任务通过脚本启动，脚本必须显式携带 `--trigger-source` 参数。

允许值：

- `trigger_source = scheduler`
- `trigger_source = cli`

映射规则：

- `trigger_source = scheduler` → `data_source = binance_rest_by_scheduler`
- `trigger_source = cli` → `data_source = binance_rest_by_cli`

禁止脚本根据运行环境、进程名称、调用方路径自动猜测触发来源。
禁止在缺少 `trigger_source` 的情况下写入正式 K线表。

禁止值：

- `manual_repair`
- `system_repair`
- `binance_websocket`
- `manual_input`
- `human_edit`
- `binance_rest_backfill`
- `binance_rest_incremental`

禁止行为：

1. 禁止人工直接修改正式 K线表中的 OHLCV 等核心行情字段。
2. 禁止人工录入价格、成交量、成交额后写入正式 K线表。
3. 禁止使用 `manual_repair` 作为 K线 `data_source`。
4. 禁止使用 `system_repair` 作为 K线 `data_source`。
5. 禁止使用 WebSocket 作为 4h K线标准来源。
6. 禁止把 `binance_rest_by_cli` 理解为人工改数；它只表示 `trigger_source = cli` 的任务触发脚本，数据仍来自 Binance REST。

如果需要区分采集任务目的，应在 `collector_event_log` 中记录 `collection_mode`。

第一阶段建议允许：

- `incremental`
- `manual_backfill`
- `historical_backfill`

示例：

定时任务触发增量采集：

- `trigger_source = scheduler`
- `data_source = binance_rest_by_scheduler`
- `collection_mode = incremental`

用户命令行手动触发一次增量采集：

- `trigger_source = cli`
- `data_source = binance_rest_by_cli`
- `collection_mode = incremental`

命令行手动回补缺口：

- `trigger_source = cli`
- `data_source = binance_rest_by_cli`
- `collection_mode = manual_backfill`

命令行历史区间回补：

- `trigger_source = cli`
- `data_source = binance_rest_by_cli`
- `collection_mode = historical_backfill`

不得使用：

- `collection_mode = recheck`

原因：

1. 复核任务不是采集任务。
2. 复核任务不写入正式 K线表。
3. 复核任务只对比 Binance REST 与数据库已有 K线。
4. 复核任务发现异常后必须报警，不得自动修复。

复核任务应使用独立检查语义，例如：

- `check_mode = daily_integrity_check`
- `check_mode = manual_integrity_check`
- `check_trigger = scheduler`
- `check_trigger = cli`
- `compare_source = binance_rest`

如果后续需要表结构承载复核结果，应优先写入 `data_quality_check`，或者单独设计 `kline_integrity_check_log`，不得把复核任务伪装成采集写入任务。

### 5.7 K线 Upsert 规则

K线写入必须支持幂等。

同一根 K线重复回补时，不应产生重复数据。

写入行为应区分：

1. 新增。
2. 已存在且无变化。
3. 已存在但关键字段发生变化。
4. 写入失败。

如果同一根 K线已存在但关键字段发生变化，系统必须记录变更事件。

第一阶段可以先记录到 `collector_event_log`。

第一阶段不强制建立独立的 `market_kline_revision` 表。

后续如果需要完整追踪每次 K线修订历史，再单独设计 K线修订历史表。

禁止静默覆盖重要行情数据而不留记录。

如果 MySQL 不可用：
1. 不得假装已落库。
2. 必须写本地 emergency 日志。
3. 如 Redis 可用，可写短期 outbox / failure key。
4. 如 Hermes 可用，必须直接发送“数据库不可用”提醒。
5. MySQL 恢复后，可以由恢复任务补写故障摘要，但不得伪造原始发生时间。

---

### 5.8 K线关键字段变化定义

以下字段发生变化，应视为关键字段变化：

```text
open_price
high_price
low_price
close_price
volume
quote_volume
trade_count
taker_buy_volume
taker_buy_quote_volume
close_time_ms
close_time_utc
```

以下字段变化不应视为行情关键字段变化：

```text
updated_at_utc
updated_at_prc
```

如果关键字段发生变化，必须至少记录：

1. 交易所。
2. 市场类型。
3. 交易对。
4. 周期。
5. open_time_ms。
6. 变化字段。
7. 旧值摘要。
8. 新值摘要。
9. 数据来源。
10. 采集任务 id。
11. 发生时间。

---

## 6. 最小索引要求

第一阶段必须为核心查询建立必要索引。

### 6.1 market_kline_4h 索引要求

`market_kline_4h` 至少需要支持以下查询：

1. 按 `symbol + interval + open_time_ms` 范围查询。
2. 按 `symbol + interval` 查询最新一根 K线。
3. 按 `symbol + interval + open_time_utc` 范围查询。
4. 按 `exchange + market_type + symbol + interval + open_time_ms` 唯一定位一根 K线。

必须有业务唯一索引：

```text
exchange
market_type
symbol
interval
open_time_ms
```

推荐至少建立以下索引或等价索引：

```text
idx_market_kline_4h_symbol_interval_open_time_ms
idx_market_kline_4h_symbol_interval_open_time_utc
```

### 6.2 data_quality_check 索引要求

`data_quality_check` 至少需要支持以下查询：

1. 按 `symbol` 查询。
2. 按 `interval` 查询。
3. 按 `status` 查询。
4. 按 `created_at_utc` 查询。
5. 查询最近失败的数据质量检查。

### 6.3 collector_event_log 索引要求

`collector_event_log` 至少需要支持以下查询：

1. 按任务名称查询。
2. 按交易对查询。
3. 按周期查询。
4. 按状态查询。
5. 按创建时间倒序查询。
6. 查询最近失败的采集任务。
7. 按 `trigger_source` 查询。

### 6.4 alert_message 索引要求

`alert_message` 至少需要支持以下查询：

1. 按提醒类型查询。
2. 按提醒级别查询。
3. 按发送状态查询。
4. 按交易对查询。
5. 按创建时间倒序查询。
6. 按去重 key 查询。
7. 按关联业务类型和关联业务 id 查询。

---

## 7. 采集事件日志需求

系统必须有采集事件日志表。

建议表名：

```text
collector_event_log
```

该表用于记录采集任务运行情况。

应记录：

1. 采集任务名称。
2. 触发来源 `trigger_source`。
3. 数据来源 `data_source`。
4. 采集任务类型 `collection_mode`。
5. 交易所。
6. 市场类型。
7. 交易对。
8. K线周期。
9. 任务开始时间 UTC。
10. 任务开始时间 PRC。
11. 任务结束时间 UTC。
12. 任务结束时间 PRC。
13. 任务状态。
14. 请求参数摘要。
15. 返回数量。
16. 写入数量。
17. 更新数量。
18. 跳过数量。
19. 异常类型。
20. 异常信息摘要。
21. 错误堆栈摘要。
22. 关联的数据质量检查记录。
23. 关联的提醒记录。
24. 创建时间 UTC。
25. 创建时间 PRC。

`collector_event_log` 必须记录 `trigger_source`。

允许值：

- `scheduler`
- `cli`

含义：

- `scheduler`：由定时任务、scheduler、cron、APScheduler 等系统任务触发。
- `cli`：由用户在命令行手动触发。

要求：

1. `trigger_source` 不得为空。
2. `trigger_source` 不得由程序猜测。
3. 通过脚本触发采集时，必须显式传入 `--trigger-source`。
4. `trigger_source` 必须与 `data_source` 保持一致。
5. `trigger_source = scheduler` 时，`data_source = binance_rest_by_scheduler`。
6. `trigger_source = cli` 时，`data_source = binance_rest_by_cli`。

任务状态至少包括：

```text
success
failed
partial_success
skipped
blocked
```

要求：

1. 采集成功必须记录。
2. 采集失败必须记录。
3. 数据质量异常导致停止写入时，必须记录 `blocked`。
4. 采集日志不能只写本地文件，必须有数据库记录。
5. 错误信息必须脱敏，不能包含密钥。
6. `collector_event_log` 建议支持按 `trigger_source` 查询。

---

## 8. 数据质量检查需求

### 8.1 数据质量检查目标

数据质量检查的目标是防止脏数据进入后续策略系统。

策略建议的质量依赖行情数据质量。

因此，数据质量异常时，系统必须优先保护数据可信度，而不是优先写入更多数据。

---

### 8.2 数据质量检查表

系统必须有数据质量检查结果表。

建议表名：

```text
data_quality_check
```

该表用于保存每次质量检查结果。

应记录：

1. 检查类型。
2. 检查对象。
3. 交易所。
4. 市场类型。
5. 交易对。
6. 周期。
7. 检查时间范围开始 UTC。
8. 检查时间范围开始 PRC。
9. 检查时间范围结束 UTC。
10. 检查时间范围结束 PRC。
11. 检查开始时间 UTC。
12. 检查开始时间 PRC。
13. 检查结束时间 UTC。
14. 检查结束时间 PRC。
15. 检查状态。
16. 问题数量。
17. 问题摘要。
18. 详细结果 JSON。
19. 是否阻断后续流程。
20. 是否触发提醒。
21. 关联提醒记录。
22. 创建时间 UTC。
23. 创建时间 PRC。

检查状态至少包括：

```text
passed
warning
failed
blocked
```

---

### 8.3 必须检查的数据质量规则

4h K线必须检查以下规则。

#### 8.3.1 已收盘检查

只允许已收盘 K线进入主表。

判断依据：

```text
KLINE_CLOSE_SAFETY_DELAY_MS = 30000
Binance server time >= kline.close_time_ms + KLINE_CLOSE_SAFETY_DELAY_MS
```

或等价逻辑。

历史回补和增量采集都必须执行已收盘检查。

增量采集不能假设 REST 返回的最后一根 K线一定已收盘。

禁止将未收盘 K线写入 `market_kline_4h` 主表。

---

#### 8.3.2 时间边界检查

4h K线的 `open_time_utc` 必须落在 4 小时边界。

例如：

```text
00:00
04:00
08:00
12:00
16:00
20:00
```

若出现非 4h 边界时间，应判为异常。

---

#### 8.3.3 本批次连续性检查

每次采集或回补得到的一批 K线，必须先检查本批次是否连续。

4h K线连续性规则：

```text
next.open_time_ms - current.open_time_ms == 4 * 60 * 60 * 1000
```

本批次不连续时，禁止写入行情主表。

---

#### 8.3.4 与数据库已有数据连续性检查

增量采集时，不能只看本批次连续。

还必须检查本批次与数据库已有 K线之间是否连续。

由于每次增量采集可能拉取最近多根 K线，本批次可能和数据库已有数据存在重叠。

因此，连续性检查不能简单判断：

```text
db_latest + 4h == batch_first
```

正确要求：

1. 按 `open_time_ms` 排序本批次数据。
2. 查询数据库中与本批次时间范围重叠的数据。
3. 至少查询本批次开始时间之前的一根已有 K线。
4. 合并数据库已有 K线和本批次 K线。
5. 按 `open_time_ms` 去重和排序。
6. 检查受影响时间范围内是否存在 4h 缺口。
7. 只有连续性通过后，才允许写入行情主表。

---

#### 8.3.5 重复数据检查

同一业务唯一键不允许出现多条记录。

如果出现重复，应判为严重数据异常。

同一批次内部也不允许出现重复业务唯一键。

---

#### 8.3.6 OHLC 合法性检查

必须检查：

```text
high_price >= open_price
high_price >= close_price
high_price >= low_price
low_price <= open_price
low_price <= close_price
low_price <= high_price
```

不符合条件时，禁止写入行情主表。

---

#### 8.3.7 数值合法性检查

必须检查：

1. 价格字段非空。
2. 价格字段大于 0。
3. 成交量字段非负。
4. 成交额字段非负。
5. 成交笔数字段非负。
6. 时间字段非空。

---

#### 8.3.8 缺口检查

系统必须能识别指定时间范围内缺失的 K线。

例如：

```text
04:00
08:00
16:00
```

中间缺失：

```text
12:00
```

这种情况必须被识别为数据缺口。

缺口未通过 Binance REST 回补完成时，后续策略建议必须被阻断或降级。

---

#### 8.3.9 数据来源检查

写入 `market_kline_4h` 的标准数据来源必须是 Binance REST。

如果来源不是允许范围，应判为异常。

---

#### 8.3.10 时区一致性检查

同一条 K线中的毫秒时间戳、UTC datetime、PRC datetime 必须一致。

禁止出现：

1. `open_time_ms` 与 `open_time_utc` 不一致。
2. `close_time_ms` 与 `close_time_utc` 不一致。
3. `open_time_prc` 不是由 `open_time_utc` 转换得到。
4. `close_time_prc` 不是由 `close_time_utc` 转换得到。
5. 使用 PRC 时间误写入 UTC 字段。

---

### 8.4 detailed_result JSON 限制

`data_quality_check.detailed_result` 用于保存质量检查的问题摘要、关键证据和排查线索。

它不应该无限保存完整原始 K线响应，也不应该把大批量异常数据全部塞入 JSON。

当数据质量检查失败时：

1. 未通过质量检查的 K线批次不得写入 `market_kline_*` 主表。
2. `data_quality_check.detailed_result` 应保存检查范围、期望数量、实际数量、缺失数量、异常类型、关键缺失时间点或缺失区间。
3. 如果异常数量很多，应保存压缩后的区间信息和少量样例，而不是保存全部异常明细。
4. `collector_event_log` 应保存本次采集任务的请求参数、返回数量、失败原因和处理动作。
5. 如未来需要保存完整原始响应，应使用独立的采集原始响应归档机制，并设置保留期限，不应塞入 `data_quality_check.detailed_result`。

示例结构：

```json
{
  "check_type": "kline_continuity",
  "symbol": "BTCUSDT",
  "interval": "4h",
  "status": "failed",
  "expected_interval_ms": 14400000,
  "checked_from_utc": "2026-05-01T00:00:00Z",
  "checked_to_utc": "2026-05-06T00:00:00Z",
  "expected_count": 31,
  "actual_count": 29,
  "missing_count": 2,
  "missing_open_times_utc": [
    "2026-05-03T12:00:00Z",
    "2026-05-04T08:00:00Z"
  ],
  "action": "blocked_write_to_market_kline_4h"
}
```

---

## 9. 数据质量异常处理规则

### 9.1 失败关闭原则

数据质量异常时，系统应采用失败关闭原则。

即：

```text
宁可停止写入和提醒用户，也不能让脏数据进入主行情表。
```

---

### 9.2 不能写入行情主表的情况

以下情况禁止写入 `market_kline_4h`：

1. Binance REST 请求失败。
2. Binance REST 返回为空。
3. Binance REST 返回格式异常。
4. 返回数据解析失败。
5. 返回 K线未收盘。
6. 本批次 K线不连续。
7. 本批次与数据库已有 K线不连续。
8. OHLC 字段不合法。
9. 数值字段不合法。
10. 时间字段不合法。
11. K线周期不匹配。
12. 同一批次内部存在重复业务唯一键。
13. 数据来源不符合要求。
14. MySQL 写入前校验失败。

---

### 9.3 异常时必须保留记录

禁止写入行情主表，不等于什么都不写。

异常发生时，必须写入：

1. `collector_event_log`
2. `data_quality_check`
3. `alert_message`

如果异常发生在 K线采集链路，必须通过 Hermes 发送微信提醒。

---

### 9.4 事务边界要求

行情主表写入与异常记录保存必须有明确事务边界。

当数据质量检查失败时：

1. 行情主表写入必须回滚或不执行。
2. 采集事件记录必须保留下来。
3. 数据质量检查结果必须保留下来。
4. 提醒记录必须保留下来。
5. 不能因为主表写入失败导致异常记录也被回滚。
6. 不能因为提醒发送失败导致数据质量检查记录丢失。

---

## 10. K线采集异常强制提醒规则

K线数据是本系统后续策略判断、建议生成、复盘验证的核心数据源。

因此，只要 K线采集链路出现关键异常，系统必须通过 Hermes 发送高级别提醒，并写入 `alert_message`。

以下情况必须提醒：

1. Binance REST 请求失败。
2. Binance REST 返回数据为空。
3. Binance REST 返回格式异常。
4. K线解析失败。
5. 返回 K线未收盘。
6. 本批次 K线不连续。
7. 本批次与数据库已有 K线不连续。
8. 发现历史 K线缺口。
9. OHLC 数据不合法。
10. 数值字段异常。
11. 时间字段异常。
12. 数据来源异常。
13. 同一批次存在重复业务唯一键。
14. K线写入 MySQL 失败。
15. 数据质量检查失败。
16. 手动 CLI 回补任务失败。
17. 连续多次采集任务失败。

要求：

1. 异常时禁止静默失败。
2. 异常时必须写入 `collector_event_log`。
3. 异常时必须写入 `data_quality_check`。
4. 异常时必须写入 `alert_message`。
5. 异常时必须通过 Hermes 发送微信提醒。
6. 同一异常事件可以做去重和冷却，避免重复刷屏。
7. 去重和冷却不能导致异常完全不提醒。
8. K线采集异常默认视为高级别告警。

提醒发送、去重、冷却、重试和 `channel_response` 记录规则，由 `04_alerting_requirements.md` 详细定义。

---

## 11. 提醒记录表需求

系统必须有提醒记录表。

建议表名：

```text
alert_message
```

该表主要负责保存所有提醒消息的发送记录。

应记录：

1. 提醒类型。
2. 提醒级别。
3. 提醒标题。
4. 提醒内容。
5. 交易所。
6. 市场类型。
7. 交易对。
8. 关联业务类型。
9. 关联业务 id。
10. 发送通道。
11. 发送状态。
12. 发送时间 UTC。
13. 发送时间 PRC。
14. Hermes 返回结果。
15. 失败原因。
16. 重试次数。
17. 去重 key。
18. 冷却窗口。
19. 创建时间 UTC。
20. 创建时间 PRC。
21. 更新时间 UTC。
22. 更新时间 PRC。

必须保存 Hermes 返回结果。

建议字段：

```text
channel_response
```

`channel_response` 应使用 JSON 类型或等价 JSON 文本字段。

不得只保存 `success` 或 `failed`。

`channel_response` 应尽量保存：

1. Hermes 返回状态。
2. Hermes 返回消息摘要。
3. HTTP 状态码。
4. 错误类型。
5. 错误摘要。
6. 响应耗时。
7. 发送目标通道。
8. route 名称。
9. 请求时间。
10. 响应时间。

注意：

1. `alert_message` 可以保留 PRC 时间字段，方便人工排查微信提醒时间。
2. 提醒排序、去重、冷却判断仍必须基于 UTC。
3. `channel_response` 中不得保存密钥。

---

### 11.1 alert_message 不能替代 strategy_advice

`alert_message` 是提醒记录表，不是策略建议主表。

后续策略建议必须使用独立表，例如：

```text
strategy_advice
```

当某条提醒是策略建议提醒时，`alert_message` 应通过关联字段指向对应 `strategy_advice`。

第一阶段可以只保留通用关联字段，例如：

```text
related_type
related_id
```

第一阶段不实现完整 `strategy_advice`。

---

## 12. 后续策略数据预留原则

第一阶段不实现策略系统，但数据库设计必须避免堵死后续能力。

本节只定义数据库预留原则，不展开完整策略生命周期和评估模型。

完整策略规则由以下文档定义：

```text
docs/requirements/05_future_strategy_requirements.md
docs/requirements/06_advice_lifecycle_requirements.md
docs/requirements/07_strategy_evaluation_requirements.md
```

---

### 12.1 strategy_signal 与 strategy_advice 必须分离

后续多策略系统中，每个策略应独立输出信号。

例如：

```text
BaseStrategy
GannStrategy
TrendStrategy
SupportResistanceStrategy
VolatilityRiskStrategy
```

这些独立策略输出的是：

```text
strategy_signal
```

最终展示给用户的是：

```text
strategy_advice
```

多个策略不能各自直接给用户发送最终操作建议。

---

### 12.2 策略建议必须支持版本链

后续 `strategy_advice` 必须支持建议生命周期和版本链。

至少需要支持以下字段：

1. `root_advice_id`
2. `parent_id`
3. `path`
4. `version_no`
5. `advice_code`
6. `status`

如后续需要单独记录整条建议链的汇总状态，可以增加：

1. `chain_status`

但 `chain_status` 不能替代单条建议版本自身的 `status`。

例如一条建议链可能包含：

1. A-v1
2. A-v2
3. A-v3
4. A-v4

其中状态应类似：

1. A-v1 status = `superseded`
2. A-v2 status = `superseded`
3. A-v3 status = `superseded`
4. A-v4 status = `completed` / `invalidated` / `expired` / `closed`

如果保留 `chain_status`，则建议取值为：

1. `completed`
2. `invalidated`
3. `expired`
4. `closed`

前序版本被新版本替代时，不应被错误标记为：

1. `completed`
2. `invalidated`
3. `failed`

前序版本的正确状态应是：

1. `superseded`

原因：

1. 前序版本并没有独立完成。
2. 前序版本也不一定独立失效。
3. 前序版本只是被后续版本替代。
4. 如果把前序版本统一改成 `completed` 或 `failed`，会破坏历史事实。
5. 后续复盘时，应区分单个建议版本状态和整条建议链最终结果。

---

### 12.3 建议编号必须直观

后续建议编号应包含：

1. 日期。
2. 交易对。
3. 4h 周期标识。

示例：

```text
20260506-BTCUSDT-04
```

编号时间必须基于 UTC，不使用本地 PRC 时间。

---

### 12.4 策略复盘不能重复提醒

后续每条建议链关闭、完成、失效或过期后，不应立即复盘，而应进入复盘队列。

系统应根据 `review_due_at_utc` 判断是否到达复盘时间。

到期后，只应触发一次复盘，并只发送一次复盘提醒。


数据库中必须有机制记录：

1. 是否已复盘。
2. 复盘时间。
3. 是否已发送复盘提醒。
4. 复盘提醒对应的 `alert_message`。

避免同一条已关闭建议被每 4h 重复复盘和重复提醒。

---

### 12.5 停止交易也是有效建议

后续 `strategy_advice` 的最终动作不能只有：

```text
long
short
```

还必须支持：

```text
wait
stop_trading
```

其中：

```text
wait = 条件未到，等待更好位置
stop_trading = 多策略严重分歧或风险条件不满足，当前禁止交易
```

停止交易不是系统失败，而是风险控制结果。

---

## 13. 长期盈利能力评估的数据预留

项目根本目标不是单纯减少错误操作，而是追求长期可验证的盈利能力。

因此后续数据库必须支持评估以下指标：

1. 建议总数。
2. 有效建议数。
3. 触发建议数。
4. 未触发建议数。
5. 胜率。
6. 平均盈利。
7. 平均亏损。
8. 盈亏比。
9. 期望值。
10. 最大浮盈。
11. 最大浮亏。
12. 最大回撤。
13. 单笔风险收益比。
14. 建议风险敞口。
15. 单位风险收益。
16. 连续亏损次数。
17. 连续盈利次数。
18. 最大连续亏损。
19. 最大连续盈利。
20. 是否先触发止损。
21. 是否达到目标区。
22. 建议从生成到触发的时间。
23. 建议从触发到结束的时间。
24. 不同策略版本表现。
25. 不同市场环境表现。
26. 多策略一致时的表现。
27. 多策略严重分歧但停止交易时是否避免亏损。
28. 用户是否按建议执行。
29. 用户人工执行偏差。
30. 用户偏离建议后的结果。

这些指标主要由 `07_strategy_evaluation_requirements.md` 详细定义。

本文档只要求第一阶段数据库设计不能阻碍这些指标的后续记录。

---

## 14. Alembic 迁移要求

项目必须使用 Alembic 管理数据库结构。

禁止通过 Navicat 或手写 SQL 直接修改正式项目结构后不留迁移记录。

每次数据库结构变化必须：

1. 新增 migration 文件。
2. 明确 upgrade。
3. 明确 downgrade。
4. 本地执行验证。
5. 服务器执行验证。
6. 通过 Git 提交。
7. 由审查后合并。

要求：

1. 不允许修改已经合并到 master 并在服务器执行过的历史 migration。
2. 新增字段必须通过新 migration。
3. 新增索引必须通过新 migration。
4. 删除字段必须谨慎，需要在文档中说明原因。
5. 表结构变更必须和需求文档一致。
6. 一个模块一个分支，数据库迁移也必须跟随对应模块分支提交。

---

## 15. 数据库安全要求

### 15.1 敏感信息禁止入库

以下信息不得明文写入数据库：

1. API key。
2. API secret。
3. Hermes Webhook secret。
4. DeepSeek API key。
5. 服务器密码。
6. 数据库密码。
7. Redis 密码。
8. 用户个人敏感凭证。

如果必须保存配置引用，只能保存配置项名称或脱敏摘要。

---

### 15.2 日志和错误信息脱敏

数据库中的错误信息和日志摘要不得包含完整密钥。

错误记录可以保存：

1. 错误类型。
2. 错误代码。
3. 简短错误消息。
4. 脱敏后的请求地址。
5. 脱敏后的响应摘要。

---

### 15.3 最小可用字段原则

第一阶段数据库只保存当前阶段需要的数据和后续明确必须预留的字段。

不要为了未来想象出来的复杂功能提前创建大量空表。

---

## 16. 第一阶段数据库验收标准

第一阶段完成后，数据库层至少满足：

1. Alembic 可以正常初始化。
2. Alembic 可以正常执行到最新版本。
3. MySQL 中存在 `market_kline_4h`。
4. `market_kline_4h` 存在业务唯一键。
5. 4h K线可以重复回补且不产生重复数据。
6. 4h K线按 `open_time_ms` 或 `open_time_utc` 排序正确。
7. 不使用 `id` 判断 K线连续性。
8. 未收盘 K线不会写入主表。
9. 本批次 K线不连续时不会写入主表。
10. 与数据库已有 K线不连续时不会写入主表。
11. 数据质量检查结果可以落库。
12. 采集事件可以落库。
13. 采集失败可以落库。
14. K线不连续可以落库。
15. K线采集链路异常必须写入 `alert_message`。
16. K线采集链路异常必须通过 Hermes 发送高级别微信提醒。
17. Hermes 返回结果可以写入 `channel_response`。
18. `alert_message` 保留 UTC 和 PRC 时间字段。
19. 所有业务判断使用 UTC。
20. PRC 字段只用于阅读和排查。
21. 数据库中不存在自动交易相关表。
22. 数据库中不存在账户同步、订单执行、持仓同步相关表。
23. 数据库错误记录不包含明文密钥。
24. 数据质量检查失败时，异常记录不会因为主表写入回滚而丢失。

---

## 17. Codex 开发约束

Codex 在实现数据库和数据质量模块时，必须遵守：

1. 先阅读本文档。
2. 不得实现自动交易相关表。
3. 不得实现账户同步、订单执行、持仓同步相关表。
4. 不得使用 WebSocket 作为 4h K线主表数据源。
5. 不得使用数据库 `id` 判断 K线连续性。
6. 不得跳过数据质量检查直接写主表。
7. 不得在采集失败时静默失败。
8. K线采集链路异常必须写入 `alert_message`。
9. K线采集链路异常必须通过 Hermes 发送高级别提醒。
10. 不得将 `alert_message` 当成 `strategy_advice` 使用。
11. 不得提前实现完整策略系统。
12. 不得修改已经合并并执行过的历史 migration。
13. 不得提交 `.env`、密钥、日志文件或本地缓存文件。
14. PRC 时间字段只能由 UTC 转换得到。
15. 业务排序、连续性检查、提醒冷却、策略判断必须使用 UTC。
16. Hermes 具体发送规则由 `04_alerting_requirements.md` 定义。

## 18. K线一致性复核

系统应支持 K线一致性复核任务，用于检查过去已入库 K线是否存在数据错误、不连续、缺失、未收盘误写入、非法 `data_source` 等问题。

复核任务分为两类：

1. 每日自动复核
2. CLI 手动复核

### 18.1 每日自动复核

系统应支持每日定时执行 K线一致性复核任务。

第一阶段检测范围：

- `symbol = BTCUSDT`
- `interval = 4h`
- 检测最近 100 根已收盘 K线
- 对照来源为 Binance U 本位合约 REST 官方 K线接口

建议记录：

- `check_mode = daily_integrity_check`
- `check_trigger = scheduler`
- `compare_source = binance_rest`
- `lookback_count = 100`

### 18.2 CLI 手动复核

系统应支持用户通过 CLI 手动触发指定范围的 K线一致性复核。

手动复核只允许输入：

- `symbol`
- `interval`
- `start_time`
- `end_time`
- `limit`

手动复核不允许输入：

- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `quote_volume`
- `trade_count`
- 任何用于人工改写 K线数值的字段

建议记录：

- `check_mode = manual_integrity_check`
- `check_trigger = cli`
- `compare_source = binance_rest`

### 18.3 复核任务的职责

复核任务只负责：

1. 调用 Binance REST 获取官方已收盘 K线作为对照。
2. 查询数据库中相同范围的正式 4h K线。
3. 按 `open_time_ms` 对齐比较。
4. 检查字段一致性、时间连续性、缺失、重复、未收盘误写入、非法 `data_source` 等问题。
5. 记录检查结果。
6. 如果发现异常，直接通过 Hermes 发送基础系统报警。

复核任务不得执行以下行为：

1. 不得写入正式 4h K线表。
2. 不得自动回补缺失 K线。
3. 不得自动覆盖已有 K线。
4. 不得自动修复字段不一致的 K线。
5. 不得把复核任务伪装成采集任务。
6. 不得使用 `collection_mode = recheck`。
7. 不得调用 DeepSeek 或其他大模型生成报警内容。

### 18.4 检测字段

检测字段至少包括：

- `open_time_ms`
- `close_time_ms`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `quote_volume`
- `trade_count`
- `taker_buy_volume`
- `taker_buy_quote_volume`

### 18.5 异常类型

判断异常类型包括：

- 数据库缺失某根 K线
- 数据库存在 Binance REST 未返回的异常 K线
- 同一 `open_time_ms` 下核心字段不一致
- K线时间不连续
- K线未收盘却被写入正式表
- `data_source` 不符合允许值
- 检测任务自身执行失败
- Binance REST 无法访问导致无法完成复核

### 18.6 结果记录

复核任务应记录检测结果，包括：

- 检测时间
- `symbol`
- `interval`
- 检测起止 `open_time_ms`
- 检测根数
- 异常数量
- 异常类型
- 示例异常 K线
- 是否已触发 Hermes 报警

复核结果建议写入 `data_quality_check`，或者后续单独设计 `kline_integrity_check_log`。

如果 MySQL 不可用，复核任务不得假装已落库，应写本地 emergency 日志，并尽量直接通过 Hermes 发送“复核任务无法完整记录”的系统报警。