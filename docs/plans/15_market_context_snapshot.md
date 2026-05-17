# Plan 15：MarketContextSnapshot 多周期市场上下文快照

## 1. 阶段定位

第 15 阶段的目标，是在已经完成 4h 与 1d K线数据基础之后，建立统一、只读、可追溯的市场事实快照：`MarketContextSnapshot`。

本阶段不是策略模块，不生成做多、做空、止盈、止损、仓位建议，不调用 DeepSeek、GPT、Claude 或其他大模型。

本阶段解决的问题是：后续任何策略、模型分析、建议生命周期和复盘，都必须明确“当时到底基于哪一批 4h 与 1d K线事实进行判断”。

正确链路应为：

```text
market_kline_4h / market_kline_1d
↓
MarketContextSnapshot
↓
策略层读取 snapshot
↓
多策略信号
↓
模型分析 / 模型接力
↓
综合建议
↓
建议生命周期 / 复盘
```

第 15 阶段只实现第二层：`MarketContextSnapshot`。

本阶段的产物是事实输入，不是策略结论。

---

## 2. Codex 执行前强制阅读

Codex 在开始修改任何代码之前，必须先阅读并遵守以下文件：

1. `AGENTS.md`
2. `docs/rules/project_invariants.md`
3. `docs/requirements/01_project_scope.md`
4. `docs/requirements/02_data_collection_requirements.md`
5. `docs/requirements/03_database_and_quality_requirements.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/decisions/0003-kline-table-splitting.md`
10. `docs/decisions/0004-alerting-through-hermes.md`
11. `docs/plans/14_1d_kline_collection_and_runtime_status.md`
12. 当前文件：`docs/plans/15_market_context_snapshot.md`

如果第 14 阶段文件实际名称不同，Codex 必须根据仓库中的真实文件名读取对应的 1d K线采集、复核和运行状态计划文档，不允许跳过第 14 阶段上下文。

如果当前 plan 与 `AGENTS.md` 或 `docs/rules/project_invariants.md` 冲突，必须以 `AGENTS.md` 和 `docs/rules/project_invariants.md` 为准。

如果发现冲突，Codex 必须在输出总结中明确说明：

1. 冲突发生在哪些文件之间。
2. 冲突内容是什么。
3. 本次采用了哪个规则。
4. 哪些内容没有实现以及原因。

---

## 3. 为什么需要 MarketContextSnapshot

如果后续策略代码、DeepSeek 分析代码、GPT 风险审查代码都各自直接查询 K线表，会产生几个问题：

1. 不同模块可能查询到不同时间窗口的数据。
2. 同一次建议无法稳定复现当时的市场输入。
3. 后续复盘时无法证明某条建议基于哪些 K线。
4. 策略层可能混入数据查询、数据新鲜度判断和质量判断，边界混乱。
5. 大模型输入无法精确追溯，模型分析质量也难以复盘。
6. 后续模型接力时，前后模型可能不是基于同一批市场事实做分析。

因此，第 15 阶段必须先建立事实快照，再进入策略层。

---

## 4. 本阶段强制边界

### 4.1 本阶段允许做的事情

本阶段允许实现：

1. `MarketContextSnapshot` 的数据库表结构。
2. `MarketContextSnapshot` 的 ORM model。
3. `MarketContextSnapshot` 的 repository。
4. `MarketContextSnapshot` 的 DTO / 类型定义。
5. 4h + 1d K线事实窗口读取。
6. 4h + 1d 数据新鲜度检查。
7. 4h + 1d 最近复核状态读取。
8. snapshot payload 组装。
9. snapshot 记录 K线窗口索引。
10. dry-run 模式。
11. 人工 CLI 验证入口。
12. blocked / failed 场景的中文 Hermes 通知。
13. 单元测试。
14. 实现说明文档。

### 4.2 本阶段禁止做的事情

本阶段禁止：

1. 不实现江恩策略。
2. 不实现趋势策略。
3. 不实现支撑压力策略。
4. 不实现波动率风控策略。
5. 不实现多策略聚合。
6. 不新增 `app/strategy/` 策略模块。
7. 不实现 DeepSeek 调用。
8. 不实现 GPT / Claude 调用。
9. 不实现模型横向对比。
10. 不实现模型接力。
11. 不生成交易建议。
12. 不生成开仓、平仓、止盈、止损、仓位建议。
13. 不读取账户。
14. 不读取持仓。
15. 不自动交易。
16. 不自动修复 K线。
17. 不自动回补 K线。
18. 不人工改数。
19. 不请求 Binance REST。
20. 不请求 Binance WebSocket。
21. 不写 `market_kline_4h`。
22. 不写 `market_kline_1d`。
23. 不把 1m 纳入主快照。
24. 不修改 Hermes gateway。
25. 不把核心业务逻辑写入 `scripts/`。
26. 不执行 `git checkout`、新建分支、切换分支等 Git 分支操作。

本阶段输出的是“市场事实快照”，不是“策略信号”，也不是“最终建议”。

---

## 5. 核心原则

### 5.1 只读原则

MarketContextSnapshot 只读取正式 K线表、采集事件和数据质量记录。

允许读取：

1. `market_kline_4h`
2. `market_kline_1d`
3. `collector_event_log`
4. `data_quality_check`
5. 必要的运行状态或配置

禁止：

1. 不请求 Binance REST。
2. 不请求 Binance WebSocket。
3. 不写 `market_kline_4h`。
4. 不写 `market_kline_1d`。
5. 不回补 K线。
6. 不修复 K线。
7. 不人工改数。
8. 不自动交易。
9. 不读取账户。
10. 不读取仓位。

### 5.2 UTC 唯一业务时间原则

业务判断、排序、连续性、新鲜度判断，一律以 UTC 为准。

PRC / 北京时间只用于展示。

禁止在业务代码中手写：

```python
+ timedelta(hours=8)
```

如需展示北京时间，必须使用 `app/core/time_utils.py` 中已有 UTC / PRC 辅助函数。

### 5.3 对齐已收盘 K线原则

MarketContextSnapshot 必须基于已收盘 K线，不能使用正在形成中的 4h 或 1d K线。

快照生成时间应尽量贴近最新 4h K线收盘后的采集完成时间，而不是任意墙上时间。

后续策略任务不应在类似 03:30 这种远离新收盘 K线的时间点随意运行。应以 Binance UTC 4h K线边界为准，在 4h 增量采集成功和质量检查完成后再生成快照。

典型节奏：

```text
UTC 00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00：4h K线收盘
UTC 00:05 / 04:05 / 08:05 / 12:05 / 16:05 / 20:05：4h 增量采集
采集成功后：允许生成 MarketContextSnapshot
```

对应北京时间约为：

```text
08:05 / 12:05 / 16:05 / 20:05 / 00:05 / 04:05
```

具体时间以后以 scheduler 配置和 Binance UTC 边界为准。

### 5.4 事实层和策略层分离原则

MarketContextSnapshot 只描述市场事实，不输出策略判断。

允许包含：

1. 4h K线窗口。
2. 1d K线窗口。
3. 最新已收盘 K线时间。
4. 数据新鲜度。
5. 最近数据质量复核状态。
6. 快照使用的 open_time 起止范围、实际数量和质量记录 ID。
7. 基础元数据。
8. K线窗口索引摘要。

禁止包含：

1. 做多结论。
2. 做空结论。
3. 开仓区间。
4. 止盈止损。
5. 仓位建议。
6. 江恩判断。
7. 趋势策略判断。
8. 波动率风控判断。
9. 大模型解释。
10. “建议交易 / 停止交易”等操作建议。

“停止交易”以后可以是策略或风控输出，但不属于第 15 阶段。

### 5.5 可追溯原则

每个 snapshot 必须能追溯：

1. 使用了哪个 symbol。
2. 使用了哪些 interval。
3. 使用了多少根 4h K线。
4. 使用了多少根 1d K线。
5. 4h 起止 open_time。
6. 1d 起止 open_time。
7. 通过 `symbol + interval + start/end open_time_ms + actual_count` 回查正式 K线表。
8. 生成时的 trigger_source。
9. 生成时的 trace_id。
10. 生成时的数据质量状态。
11. 如果 blocked，必须知道具体 blocked 原因。
12. 如果 failed，必须知道错误类型和 trace_id。

---

## 6. 多周期设计

### 6.1 必须包含 4h 与 1d

第 15 阶段必须同时纳入：

1. `BTCUSDT 4h`：作为中期结构、主要计划周期和后续策略分析基础。
2. `BTCUSDT 1d`：作为大方向、主要市场环境和高一级结构背景。

日 K 不是后期再补的附属数据，而是后续策略判断大方向的必要事实来源。

### 6.2 4h 与 1d 的职责边界

4h 负责：

1. 近期结构。
2. 波段节奏。
3. 后续策略触发窗口。
4. 最新交易计划的主要时间框架。

1d 负责：

1. 大方向。
2. 高一级趋势背景。
3. 大级别支撑压力环境。
4. 判断 4h 信号是否顺大周期或逆大周期。

第 15 阶段只保存这些周期事实，不做“顺势 / 逆势”判断。

### 6.3 暂不纳入 1m

此前讨论过 1m 数据，但第 15 阶段暂不纳入 1m。

理由：

1. 当前项目是低频 BTC 合约策略辅助系统，不是高频交易系统。
2. 1m 更适合后续做入场执行细节、价格监控、短期风险提醒。
3. 第 15 阶段目标是建立 4h + 1d 的主事实快照。
4. 过早把 1m 放入快照，会把噪声、执行层和主策略上下文混在一起。

后续如果需要，可新增独立阶段：

```text
短周期执行上下文 / 1m execution context
```

但它不能取代 4h + 1d 的主上下文快照。

---

## 7. 默认窗口数量

窗口数量必须可配置，不应硬编码死在业务逻辑中。

建议默认值：

```text
MARKET_CONTEXT_4H_LOOKBACK_COUNT=180
MARKET_CONTEXT_1D_LOOKBACK_COUNT=365
```

含义：

1. 4h 最近 180 根，约 30 天。
2. 1d 最近 365 根，约 1 年。

这是默认事实窗口，不等于策略永远只能分析这些数量。

后续策略可以根据需要在 snapshot 基础上选择子窗口，例如：

1. 4h 最近 60 根。
2. 4h 最近 120 根。
3. 1d 最近 90 根。
4. 1d 最近 365 根。

但第 15 阶段生成快照时，应记录实际使用的 lookback_count、actual_count 和起止时间，保证旧快照不受后续配置变化影响。

如果现有环境变量命名风格与上述不同，Codex 可以按项目现有配置风格调整名称，但必须保留 4h 与 1d 独立配置能力。

---

## 8. 快照生成前置条件

生成 MarketContextSnapshot 前必须检查：

1. `market_kline_4h` 已初始化。
2. `market_kline_1d` 已初始化。
3. 最新 4h K线不滞后理论最新已收盘 4h K线。
4. 最新 1d K线不滞后理论最新已收盘 1d K线。
5. 最近一次 4h 增量采集成功，或不存在未处理失败。
6. 最近一次 1d 增量采集成功，或不存在未处理失败。
7. 最近一次 4h 每日复核健康。
8. 最近一次 1d 每日复核健康。
9. 读取到的 4h K线数量满足最低窗口要求。
10. 读取到的 1d K线数量满足最低窗口要求。
11. 读取到的 4h K线全部为已收盘 K线。
12. 读取到的 1d K线全部为已收盘 K线。
13. 读取到的 4h K线按 UTC open_time 连续。
14. 读取到的 1d K线按 UTC open_time 连续。

如果不满足条件，不应生成可用于策略的正常快照。

可记录 `blocked` 状态快照或返回 `blocked` 结果，但必须明确 `blocked_reason`。

blocked 不代表系统自动修复，也不代表自动回补。

---

## 9. 快照状态语义

MarketContextSnapshot 结果状态建议包括：

```text
created
blocked
failed
```

### 9.1 created

表示快照成功生成，且满足后续策略读取的基本数据条件。

### 9.2 blocked

表示由于数据前置条件不满足而阻断，例如：

1. 4h 数据未初始化。
2. 1d 数据未初始化。
3. 4h 数据滞后。
4. 1d 数据滞后。
5. 最近复核失败。
6. K线数量不足。
7. 读取到未收盘 K线。
8. K线不连续。
9. 存在未处理的采集失败事件。

blocked 不代表系统自动修复，也不代表自动回补。

### 9.3 failed

表示快照生成过程发生程序错误或存储错误，例如：

1. MySQL 查询失败。
2. JSON 序列化失败。
3. 写入 snapshot 表失败。
4. 未预期异常。

failed 应记录 trace_id 和错误信息，并可按配置发送 Hermes 告警。

---

## 10. 建议数据表

第 15 阶段只保留一张快照主表。

必须使用 Alembic migration 管理表结构。

### 10.1 market_context_snapshot

用于记录一次快照主记录。

建议字段：

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

说明：

1. `snapshot_id` 应具有业务可读性和唯一性。
2. `snapshot_payload_json` 只保存摘要和元数据，不保存完整 K线数组。
3. 不在本表中保存策略结论。
4. 不在本表中保存大模型输出。
5. 不在本表中保存交易建议。
6. `created_at_utc` 和 `updated_at_utc` 必须使用 UTC 语义。
7. 如果项目当前时间字段使用 naive UTC，则保持项目一致，不在本阶段强行改为 timezone-aware datetime。
8. 本表是 K线窗口索引，不是第二份 K线库；正式事实源仍是 `market_kline_4h` / `market_kline_1d`。

`snapshot_id` 命名建议：

```text
MCS-BTCUSDT-4H-1D-YYYYMMDDTHHMMSSZ
```

建议约束：

```text
UNIQUE(snapshot_id)
INDEX(symbol, base_interval_value, higher_interval_value, created_at_utc)
INDEX(status, created_at_utc)
INDEX(trace_id)
```

### 10.2 不再使用逐根 kline_ref 表

本阶段不再创建或写入 `market_context_snapshot_kline_ref`。

原因：

1. `MarketContextSnapshot` 只记录本次使用哪段 4h + 1d 窗口。
2. `market_kline_4h` / `market_kline_1d` 是唯一正式 K线事实源。
3. 逐根引用表会让快照接近第二份 K线库，增加重复存储和追溯复杂度。
4. 后续策略层如需完整 K线，应基于 snapshot 主表中的 `symbol + interval + start/end open_time_ms` 回查正式 K线表，并校验查询数量等于 `actual_count`。
5. 未来如果需要保存关键证据 K线，应在策略层单独设计 evidence 表，不属于第 15 阶段。

---

## 11. Snapshot payload 建议结构

`snapshot_payload_json` 建议包含：

```json
{
  "snapshot_id": "MCS-BTCUSDT-4H-1D-20260516T160000Z",
  "symbol": "BTCUSDT",
  "base_interval": "4h",
  "higher_interval": "1d",
  "generated_at_utc": "2026-05-16T16:00:00Z",
  "latest_4h_open_time_utc": "...",
  "latest_1d_open_time_utc": "...",
  "lookback_4h_count": 180,
  "lookback_1d_count": 365,
  "actual_4h_count": 180,
  "actual_1d_count": 365,
  "start_4h_open_time_ms": 0,
  "end_4h_open_time_ms": 0,
  "start_1d_open_time_ms": 0,
  "end_1d_open_time_ms": 0,
  "data_freshness": {
    "4h": "fresh",
    "1d": "fresh"
  },
  "quality": {
    "4h": "healthy",
    "1d": "healthy"
  },
  "source_tables": {
    "4h": "market_kline_4h",
    "1d": "market_kline_1d"
  },
  "boundary": {
    "no_binance_request": true,
    "no_decision_content": true,
    "fact_snapshot_only": true
  }
}
```

payload 禁止包含完整 K线数组或每根 K线的 OHLCV 明细，例如：

```text
open
high
low
close
volume
quote_volume
trade_count
```

后续策略层需要完整 K线时，通过 snapshot 主表的时间窗口回查正式 K线表。

禁止在 payload 中加入策略判断字段，例如：

```text
trend=bullish
signal=long
entry_price
stop_loss
take_profit
position_size
leverage
stop_trading
```

如果需要中文展示摘要，应另设展示字段，但不得引入策略结论。

---

## 12. 模块结构建议

建议新增模块：

```text
app/market_context/
  __init__.py
  snapshot_types.py
  snapshot_service.py
  snapshot_repository.py
  snapshot_builder.py
  snapshot_quality.py
  snapshot_alerts.py
```

说明：

1. `snapshot_types.py`：定义 DTO、状态枚举、错误类型。
2. `snapshot_service.py`：对外主入口，负责生成快照。
3. `snapshot_repository.py`：负责写入 / 查询 snapshot 表。
4. `snapshot_builder.py`：负责组装 4h + 1d payload。
5. `snapshot_quality.py`：负责数据前置条件判断。
6. `snapshot_alerts.py`：负责 blocked / failed 时的 Hermes 中文提醒。

也可以按现有项目风格调整路径，但必须保持：

1. 不放进 strategy 目录。
2. 不放进 llm 目录。
3. 不混入 collector 业务逻辑。
4. 不让 scripts 承载核心逻辑。
5. scheduler 后续必须直接调用 app service，不得调用 scripts。

---

## 13. Service 职责边界

`snapshot_service.py` 是对外主入口。

建议职责：

1. 接收 symbol、base_interval、higher_interval、lookback_count、trigger_source、dry_run、confirm_write。
2. 调用 `snapshot_quality.py` 检查前置条件。
3. 如果 blocked，返回 blocked 结果，可按参数写入 blocked snapshot。
4. 如果满足条件，调用 `snapshot_builder.py` 组装 payload。
5. 如果不是 dry-run 且 confirm-write 为 true，调用 repository 写入 snapshot 主表。
6. 如果 failed，记录错误并按参数发送 Hermes 告警。
7. 返回结构化结果给 CLI 或 scheduler。

禁止职责：

1. 不直接请求 Binance。
2. 不直接修改 K线表。
3. 不直接写策略结论。
4. 不调用 DeepSeek。
5. 不调用策略模块。
6. 不自动触发回补。

---

## 14. CLI 入口建议

新增人工验证入口：

```bash
python -m scripts.build_market_context_snapshot \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --confirm-write
```

可选参数：

```text
--lookback-4h 180
--lookback-1d 365
--dry-run
--notify-on-blocked
--notify-on-failed
```

要求：

1. 不带 `--confirm-write` 时，不写 snapshot 表。
2. `--dry-run` 不写 snapshot 表。
3. dry-run 可以输出将要生成的快照摘要。
4. CLI 只解析参数并调用 app service。
5. CLI 不直接查表。
6. CLI 不直接写表。
7. CLI 不直接发 Hermes。
8. CLI 不直接请求 Binance。
9. scheduler 后续如果要生成 snapshot，必须直接调用 app service，不得通过 scripts 间接调用。

CLI 输出必须避免打印完整 payload 和完整 K线数组。

建议输出摘要：

```text
snapshot_id
symbol
base_interval
higher_interval
status
lookback_4h_count
lookback_1d_count
actual_4h_count
actual_1d_count
latest_4h_open_time_utc
latest_1d_open_time_utc
blocked_reason
trace_id
```

---

## 15. 调度设计

第 15 阶段默认只实现 CLI + service，不强制接入 scheduler。

如果 Codex 本次执行没有收到用户明确要求，不要接入 scheduler。

如果后续单独接入 scheduler，应遵守：

1. 必须在 4h 增量采集成功后生成。
2. 必须基于最新已收盘 4h K线。
3. 必须检查 1d 数据新鲜度。
4. 不得在任意时间生成用于策略的快照。
5. scheduler 必须直接调用 app service，不得调用 scripts。
6. job key 必须明确区分快照任务，例如：

```text
scheduler:job:market_context_snapshot:BTCUSDT:4h:1d:YYYY-MM-DDTHH:MMZ
```

本计划建议：

```text
15-1 到 15-4：完成表结构、service、CLI、blocked / failed 通知。
15-5：另开后续任务，单独评估 scheduler 接入。
```

---

## 16. Hermes 提醒

MarketContextSnapshot 通知不是每日固定健康提醒。

建议只在以下场景发送 Hermes：

1. 快照生成 failed。
2. 快照生成 blocked，且会影响后续策略分析。
3. 人工 CLI 显式要求通知。

通知必须中文、精简、明确原因。

示例：

```text
【市场上下文快照生成受阻】
级别：注意
币种周期：BTCUSDT 4h + 1d
结果：blocked
原因：1d 日K落后理论最新已收盘日K 1 根
动作：请先检查 1d 增量采集与每日复核；如需补齐，只能通过 Binance REST 手动回补，禁止人工改数。
追踪ID：...

本提醒不是交易建议，不包含自动交易动作。
系统没有自动修复数据，没有人工改数，也没有执行自动交易。
```

要求：

1. 不要在微信中输出完整 payload。
2. 不要在微信中输出 K线数组。
3. 不要在微信中输出内部 Python 对象。
4. 不要写“微信发送成功”或“微信已送达”。
5. 不要调用大模型生成通知。
6. 通知由代码模板生成。

如果微信机器人 24 小时交互窗口失效，Hermes 侧可能提交超时或投递失败；这不能直接判定为 MarketContextSnapshot 生成失败。

---

## 17. 与第 14 阶段的关系

第 15 阶段强依赖第 14 阶段：

1. 1d 表已存在。
2. 1d 手动回补已完成。
3. 1d 增量采集已接入。
4. 1d 每日复核已接入。
5. runtime status 已能展示 4h 与 1d。

如果第 14 阶段线上观察未通过，第 15 阶段不应贸然上线 snapshot scheduler。

如果代码层面暂时没有完整 1d 数据能力，Codex 不允许降级成“只做 4h snapshot”并假装完成第 15 阶段。正确处理方式是：

1. 明确说明 1d 前置依赖不足。
2. 返回 blocked 或暂不实现相关写入。
3. 不伪造 1d payload。
4. 不绕过 1d 质量检查。

---

## 18. 与后续策略层的关系

后续策略层必须优先读取 `MarketContextSnapshot`，而不是散乱直接查询 K线表。

策略层可以基于 snapshot 生成：

1. 江恩策略信号。
2. 趋势策略信号。
3. 支撑压力策略信号。
4. 波动率 / 风控否决信号。

但这些都不属于第 15 阶段。

第 15 阶段只保证：

```text
后续策略拿到的是统一、干净、可追溯的市场事实输入。
```

第 15 阶段禁止创建或实现：

```text
app/strategy/
BaseStrategy
StrategyRunner
Trend4hStrategy
VolatilityRisk4hStrategy
GannStrategy
```

这些属于后续策略信号框架阶段。

---

## 19. 测试要求

至少覆盖以下场景。

### 19.1 快照生成成功

1. 4h 与 1d 均已初始化。
2. 数据新鲜。
3. 最近复核健康。
4. 生成 `status=created`。
5. 写入 `market_context_snapshot`。
6. 不写入 `market_context_snapshot_kline_ref`。
7. payload 中包含 4h 与 1d 窗口摘要。
8. 不包含策略字段。
9. 不请求 Binance。
10. 不修改 K线表。
11. payload 不包含完整 K线数组或 OHLCV 明细数组。
12. 可以通过 snapshot 的 start/end/count 回查正式 K线窗口。

### 19.2 4h 未初始化

1. 不生成 created 快照。
2. 返回 blocked。
3. blocked_reason 明确说明 4h 未初始化。
4. 不请求 Binance。
5. 不回补。

### 19.3 1d 未初始化

1. 不生成 created 快照。
2. 返回 blocked。
3. blocked_reason 明确说明 1d 未初始化。
4. 不自动执行 1d backfill。
5. 不伪造 1d 数据。

### 19.4 4h / 1d 滞后

1. 最新 4h 落后时 blocked。
2. 最新 1d 落后时 blocked。
3. blocked_reason 应能区分具体周期。
4. 不自动触发回补。

### 19.5 最近复核失败

1. 4h 复核失败时 blocked。
2. 1d 复核失败时 blocked。
3. 不生成可用于策略的 created 快照。
4. 不自动修复。

### 19.6 K线数量不足

1. 4h 数量不足时 blocked。
2. 1d 数量不足时 blocked。
3. blocked_reason 明确说明实际数量与要求数量。

### 19.7 未收盘 K线

1. 读取到未收盘 4h K线时 blocked。
2. 读取到未收盘 1d K线时 blocked。
3. 不把未收盘 K线写入 payload。

### 19.8 K线不连续

1. 4h 窗口不连续时 blocked。
2. 1d 窗口不连续时 blocked。
3. blocked_reason 明确说明不连续周期。

### 19.9 dry-run

1. dry-run 不写 snapshot 表。
2. dry-run 不写任何逐根 K线引用表。
3. dry-run 输出摘要。
4. dry-run 不发 Hermes，除非显式要求通知。

### 19.10 只读边界

确认不会：

1. 写 `market_kline_4h`。
2. 写 `market_kline_1d`。
3. 请求 Binance。
4. 调用大模型。
5. 生成交易建议。
6. 执行自动交易。
7. 读取账户。
8. 读取持仓。

### 19.11 Hermes 通知

1. blocked 通知中文、精简。
2. failed 通知中文、精简。
3. 不输出完整 payload。
4. 不输出 K线数组。
5. 不出现“微信发送成功”“微信已送达”。
6. 通知内容包含“本提醒不是交易建议”。

### 19.12 快照还原校验

1. 根据 snapshot_id 可以读取 `market_context_snapshot`。
2. 4h 可通过 `symbol + start_4h_open_time_ms + end_4h_open_time_ms` 回查 `market_kline_4h`。
3. 1d 可通过 `symbol + start_1d_open_time_ms + end_1d_open_time_ms` 回查 `market_kline_1d`。
4. 4h 查询数量必须等于 `actual_4h_count`。
5. 1d 查询数量必须等于 `actual_1d_count`。
6. 查询结果必须按 `open_time_ms` 升序。
7. 数量不一致时必须返回明确错误或抛出明确异常。
8. 该能力只读，不请求 Binance、不修复、不回补、不写正式 K线表。

---

## 20. 建议实施拆分

### 20.1 15-1：snapshot 表结构、ORM、repository

完成：

1. Alembic migration。
2. ORM model。
3. repository。
4. 基础写入 / 查询测试。

不做：

1. CLI。
2. scheduler。
3. Hermes。
4. 策略。

### 20.2 15-2：snapshot builder 与 service

完成：

1. 读取 4h / 1d。
2. 检查新鲜度。
3. 检查最近复核状态。
4. 检查 K线数量。
5. 检查已收盘。
6. 检查连续性。
7. 组装 payload。
8. dry-run。
9. created / blocked / failed 状态。

### 20.3 15-3：CLI 验证入口

完成：

1. `scripts.build_market_context_snapshot`。
2. 参数校验。
3. dry-run / confirm-write。
4. 手动验证。

### 20.4 15-4：Hermes blocked / failed 通知

完成：

1. 中文精简通知。
2. 不输出 payload 和 K线数组。
3. 不影响既有 4h / 1d 通知。
4. 不调用大模型。

### 20.5 15-5：可选 scheduler 接入

只有在第 15-1 到 15-4 稳定后，才考虑 scheduler 接入。

本次 Codex 任务默认不做 15-5，除非用户另行明确要求。

scheduler 必须直接调用 app service，不得调用 scripts。

---

## 21. 验收命令

Codex 完成后，至少运行：

```bash
python -m pytest tests/market_context
```

如果项目已有完整测试集，也运行：

```bash
python -m pytest
```

如果项目已有规则检查脚本，也运行：

```bash
python -m scripts.check_project_invariants
```

如果新增 Alembic migration，需要验证：

```bash
python -m alembic upgrade head
```

如果某些命令当前环境无法运行，Codex 必须在总结中说明：

1. 哪个命令无法运行。
2. 失败原因。
3. 是否是环境问题。
4. 是否影响本阶段代码正确性判断。

---

## 22. 实现说明文档要求

必须新增：

```text
docs/implementation/15_market_context_snapshot.md
```

文档至少包含：

1. 本阶段实现了哪些模块。
2. MarketContextSnapshot 的职责。
3. snapshot service 入口在哪里。
4. repository 职责。
5. builder 职责。
6. quality checker 职责。
7. snapshot 表结构说明。
8. 明确本阶段不再使用逐根 kline_ref 表。
9. payload 字段说明。
10. created / blocked / failed 状态说明。
11. dry-run 与 confirm-write 行为说明。
12. blocked / failed Hermes 通知行为说明。
13. 本阶段没有实现哪些内容。
14. 如何运行测试。
15. 后续策略层如何基于 snapshot_id 和 snapshot 记录的窗口范围读取事实输入。
16. 只读快照还原校验能力。

必须明确写入：

```text
本阶段不生成最终交易建议。
本阶段不实现策略模块。
本阶段不调用 DeepSeek。
本阶段不发送策略建议。
本阶段不接入自动交易。
本阶段不请求 Binance。
本阶段不修改正式 K线表。
```

---

## 23. Codex 输出总结要求

Codex 完成后，输出总结必须包含：

1. 修改了哪些文件。
2. 新增了哪些文件。
3. 是否读取并遵守了 `AGENTS.md` 和 `docs/rules/project_invariants.md`。
4. 是否发现规则冲突。
5. 是否新增 Alembic migration。
6. 新增了哪些表。
7. 是否实现 dry-run。
8. 是否实现 confirm-write。
9. 是否实现 blocked / failed。
10. 是否发送 Hermes 通知，以及触发条件。
11. 测试命令和测试结果。
12. 如果测试失败，必须说明失败原因。
13. 是否存在未完成项。
14. 是否存在需要人工确认的地方。

输出总结中必须明确写：

```text
本阶段没有实现策略模块。
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
```

---

## 24. 最终验收标准

第 15 阶段完成后，应满足：

1. 可以生成一次 BTCUSDT 4h + 1d 市场上下文快照。
2. 快照能追溯到具体 4h 与 1d K线。
3. 快照记录实际 lookback_count、起止时间、最新 K线时间。
4. 快照能识别 4h 或 1d 数据滞后。
5. 快照能识别最近复核失败。
6. 快照能识别 K线数量不足。
7. 快照能识别未收盘 K线。
8. 快照能识别 K线不连续。
9. 快照生成不会请求 Binance。
10. 快照生成不会修改 K线表。
11. 快照中不包含策略结论。
12. 快照中不包含大模型输出。
13. 快照中不包含交易建议。
14. blocked / failed 能通过中文 Hermes 通知说明原因。
15. 后续策略层可以明确基于 snapshot_id 追溯输入事实。
16. CLI 只作为人工入口，核心逻辑在 app service。
17. scheduler 默认不在本阶段接入。
18. 测试覆盖核心 created / blocked / failed 路径。
19. 新增实现说明文档。
20. 不新增 `app/strategy/` 策略模块。

---

## 25. 本阶段不解决的问题

以下问题后续再做：

1. 不设计完整建议生命周期。
2. 不设计完整策略聚合算法。
3. 不实现策略信号框架。
4. 不实现江恩策略。
5. 不实现趋势策略。
6. 不实现支撑压力策略。
7. 不实现波动率风控策略。
8. 不接入 DeepSeek。
9. 不接入 GPT / Claude 等其他模型。
10. 不实现模型横向对比。
11. 不实现模型接力分析。
12. 不实现策略复盘数据库表。
13. 不实现建议复盘数据库表。
14. 不实现自动回测系统。
15. 不实现 Admin 后台。
16. 不实现人工执行记录入口。
17. 不实现 Hermes 策略建议推送。
18. 不实现 10 秒价格触发策略建议。
19. 不实现 1m execution context。
20. 不接入 snapshot scheduler。

本阶段只把 4h + 1d 市场事实快照打牢。

---

## 26. 给 Codex 的简短执行指令

你现在在 `hermes_btc_agent_v2` 项目中工作，本次任务是实现：

```text
docs/plans/15_market_context_snapshot.md
```

开始前必须先阅读：

1. `AGENTS.md`
2. `docs/rules/project_invariants.md`
3. `docs/requirements/01_project_scope.md`
4. `docs/requirements/02_data_collection_requirements.md`
5. `docs/requirements/03_database_and_quality_requirements.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/decisions/0003-kline-table-splitting.md`
10. `docs/decisions/0004-alerting-through-hermes.md`
11. 第 14 阶段 1d K线相关计划文档
12. `docs/plans/15_market_context_snapshot.md`

然后严格按第 15 阶段 plan 实现。

重点边界：

1. 本阶段只做 4h + 1d 市场事实快照。
2. 不做策略模块。
3. 不新增 `app/strategy/`。
4. 不生成交易建议。
5. 不调用 DeepSeek 或任何大模型。
6. 不请求 Binance。
7. 不修改正式 K线表。
8. 不自动回补。
9. 不人工改数。
10. 不读取账户或持仓。
11. 不自动交易。
12. 不接入 scheduler，除非用户另行明确要求。
13. scripts 只能作为 CLI 入口，核心逻辑必须在 app service。
14. 不执行 git checkout、新建分支、切换分支等 Git 分支操作。

如果本 plan 与 `AGENTS.md` 或 `docs/rules/project_invariants.md` 冲突，以后两者为准，并在总结中说明冲突点和处理方式。
