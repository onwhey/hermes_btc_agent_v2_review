# 第 14 阶段：BTCUSDT 1d 日 K 数据基础补齐

## 1. 阶段背景

后续正式策略分析必须同时使用高周期和主操作周期：

1. `1d` 日 K 用于判断大方向、高周期背景、趋势环境和市场状态。
2. `4h` K线继续作为当前主操作周期。
3. 第 15 阶段才会把 `4h + 1d` 组合成多周期市场上下文快照。

当前系统已经具备较完整的 `BTCUSDT 4h` 数据底座：

1. `market_kline_4h` 独立正式表。
2. Binance REST 官方 K线解析、校验和入库。
3. 手动 4h K线回补。
4. 4h 增量采集。
5. K线质量检查。
6. 每日 K线一致性复核。
7. Hermes 中文告警。
8. scheduler 常驻调度入口。
9. runtime status 只读运行状态检查入口。

但 `BTCUSDT 1d` 日 K 目前只是设计预留，没有真正接入 Binance REST 采集、回补、质量检查、每日复核和运维可见链路。

因此第 14 阶段只补齐 `BTCUSDT 1d` 日 K 数据基础，不做 `MarketContextSnapshot`，不做策略，不生成交易建议。

---

## 2. 阶段目标

第 14 阶段目标是建立 `BTCUSDT 1d` 日 K 的正式数据基础，使系统能够稳定、可复核、可告警地维护 `1d` 已收盘 K线数据，为第 15 阶段 `4h + 1d` 多周期市场上下文快照提供可靠输入。

第 14 阶段完成后，系统应具备：

1. `BTCUSDT 1d` 日 K 独立正式表。
2. 通过 Binance REST 手动回补 `1d` 历史 K线。
3. 通过 scheduler 在日 K 收盘后增量采集最新 `1d` K线。
4. 只写入已收盘日 K，不允许当前未收盘日 K进入正式表。
5. `1d` 数据连续性、重复、缺失、字段合理性检查。
6. `1d` 数据异常通过 Hermes 中文告警。
7. `1d` 健康状态可被后续市场上下文快照读取。
8. `1d` 状态不被 `4h` 健康状态掩盖。

---

## 3. 本阶段非目标

第 14 阶段不做：

1. 不实现第 15 阶段 `MarketContextSnapshot`。
2. 不实现 `4h + 1d` 多周期市场上下文快照。
3. 不实现策略分析。
4. 不生成交易建议。
5. 不调用 DeepSeek、GPT、Claude 或任何大模型。
6. 不读取账户、订单、仓位、杠杆或保证金接口。
7. 不实现自动下单、自动平仓、自动调仓、自动撤单或自动交易。
8. 不新增自动修复。
9. 不新增自动回补无限历史范围。
10. 不允许人工直接修改 K线字段。
11. 不允许 `manual_repair`、`human_edit`、`manual_input`、`system_repair` 作为正式 K线来源或修复方式。
12. 不修改 `4h` 既有业务规则。
13. 不修改 Hermes gateway。

---

## 4. 数据源规则

`BTCUSDT 1d` 正式 K线必须遵守与 `4h` 一致的数据源铁律：

1. 正式 `1d` K线只能来自 Binance REST 官方 K线接口。
2. `BTCUSDT 1d` 正式 K线应通过 Binance USDT-M Futures REST `/fapi/v1/klines` 获取，`interval=1d`。
3. 不允许 WebSocket 聚合数据写入正式 `1d` 表。
4. 不允许第三方行情源写入正式 `1d` 表。
5. 不允许模拟数据写入正式 `1d` 表。
6. 不允许人工编辑 `1d` K线字段。
7. 不允许 `manual_repair`。
8. 如果发现缺失或异常，只能通过 Binance REST 官方已收盘数据进行受控回补。
9. 正式 `1d` 表只能保存已收盘日 K。
10. 当前 UTC 当天尚未收盘的日 K 必须过滤掉。
11. 业务判断时间以 UTC 为准，PRC / 北京时间只用于用户阅读和排查。
12. 如果表中保留 PRC 字段，必须通过 `app/core/time_utils.py` 的统一函数转换，不能在业务代码里手写 `+8 小时`。

---

## 5. 数据表结构要求

### 5.1 独立分表原则

用户明确要求：`1d` 日 K 必须单独分表，不允许与 `4h` 混表。

后续实现必须遵守：

1. `1d` 正式 K线必须使用独立物理表。
2. 不允许把 `1d` 和 `4h` 写入同一张正式 K线表后仅靠 `interval_value` 区分。
3. 不允许把 `1d` 写入 `market_kline_4h`。
4. 不允许新增一张混合 `market_kline` 表承载 `1d` 和 `4h` 正式数据。
5. 可以复用 ORM mixin、字段定义、DTO、parser 或 repository helper，但物理正式表必须分开。

当前 4h 正式表名为：

```text
market_kline_4h
```

因此第 14 阶段建议新增 1d 正式表名：

```text
market_kline_1d
```

如果后续实现时 4h 表命名风格已经调整，则 `1d` 表应使用同风格命名，但仍必须是独立表。

### 5.2 字段结构

`1d` 表结构应尽量与现有 `market_kline_4h` 保持一致，方便复用解析、质量检查、查询和运维能力。

建议字段包括：

1. `id`
2. `exchange`
3. `market_type`
4. `symbol`
5. `interval_value`，固定为 `1d`；如果 `4h` 独立表保留该字段，`1d` 也应保留
6. `open_time_ms`
7. `open_time_utc`
8. `open_time_prc`
9. `close_time_ms`
10. `close_time_utc`
11. `close_time_prc`
12. `open_price`
13. `high_price`
14. `low_price`
15. `close_price`
16. `volume`
17. `quote_volume`
18. `trade_count`
19. `taker_buy_base_volume`
20. `taker_buy_quote_volume`
21. `data_source`
22. `trigger_source`
23. `raw_payload_json`
24. `raw_payload_hash`
25. `created_at_utc`
26. `created_at_prc`
27. `updated_at_utc`
28. `updated_at_prc`

正式 `1d` 表不允许出现人工修复来源、人工输入来源或自动修复来源。

### 5.3 唯一约束与索引

唯一约束应防止重复写入。建议沿用当前 4h 风格：

```text
symbol + interval_value + open_time_ms
```

如果后续实现阶段决定把 `exchange`、`market_type` 纳入唯一约束，也必须与现有 4h 风格保持一致，并在 implementation 中写清楚。

索引至少应支持：

1. 按 `symbol + interval_value + open_time_utc` 查询最近 N 根日 K。
2. 按 `symbol + interval_value + open_time_ms` 做幂等写入和单根定位。
3. 按 `created_at_utc` 排查写入历史。
4. 按 `data_source` 或 `trigger_source` 排查来源。

### 5.4 migration 要求

第 14 实现阶段必须通过 Alembic migration 创建独立 `1d` 正式表。

要求：

1. 不允许用临时 raw SQL 创建正式表。
2. migration 文件必须可由 `python -m alembic upgrade head` 执行。
3. migration 不得删除已有表。
4. migration 不得修改或清空 `market_kline_4h`。
5. migration 不得插入真实业务数据。
6. 本计划文档不创建 migration；这里只定义后续实现阶段的迁移原则。

### 5.5 辅助表复用

以下辅助表可以复用现有通用表：

1. `collector_event_log`
2. `data_quality_check`
3. `alert_message`

但必须通过以下字段明确区分 `1d` 与 `4h`：

1. `interval_value = 1d`
2. `event_type`
3. `source` 或 `details_json`
4. `trace_id`
5. `check_type` 或 `check_mode`

禁止让 `1d` 事件复用容易误解为 `4h` 的 `event_type`。

---

## 6. 1d 时间边界规则

Binance `1d` K线按 UTC 自然日收盘：

1. 日 K 开盘时间：UTC `00:00:00`
2. 日 K 收盘边界：次日 UTC `00:00:00`
3. Binance raw Kline `close_time_ms` 应为 `open_time_ms + 86,400,000 - 1`
4. 北京时间对应开盘展示：08:00
5. 北京时间对应收盘边界展示：次日 08:00

`1d` 时间判断必须按日 K 周期调整：

1. `1d` 相邻 open time 差值必须是 `86,400,000` 毫秒。
2. `1d` open_time_utc 必须落在 UTC `00:00:00`。
3. `1d` close_time_ms 必须等于 `open_time_ms + 86,400,000 - 1`。
4. 业务排序、连续性判断、最新 K线边界判断一律以 UTC 为准。
5. PRC 字段只用于展示，不参与排序、连续性判断或策略判断。

后续实现应新增或参数化类似常量：

```text
KLINE_1D_INTERVAL_VALUE = 1d
KLINE_1D_INTERVAL_MS = 86,400,000
```

如果现有 `kline_validator.py`、`kline_quality/rules.py`、`collector/quality.py` 或 `backfill/quality.py` 写死 `4h`，后续实现必须参数化或新增 `1d` 专用入口，不得用 `4h` 间隔校验 `1d`。

---

## 7. Binance server time 规则

过滤未收盘 K线时，应优先沿用现有 4h 逻辑：

1. 调用 `BinanceRestClient.get_server_time()` 获取 Binance server time。
2. 使用 Binance server time 判断 REST 返回 K线是否已收盘。
3. 不应只依赖本机时间。
4. 如果 Binance server time 获取失败，不得继续写入需要判断收盘状态的 `1d` K线。
5. server time 只用于判断收盘边界，不改变 UTC 业务排序规则。

判断规则：

```text
close_time_ms < binance_server_time_ms
```

满足上述条件的 K线才可视为已收盘。当前 UTC 当天尚未收盘的日 K即使由 REST 返回，也不得写入正式表。

---

## 8. 初始历史数据深度

第 15 阶段多周期快照预计需要：

1. `1d` 默认读取 365 根。
2. `1d` 最低可用 120 根。

因此第 14 阶段 `1d` 初始回补不应只回补最近几十天。

要求：

1. 初始 `1d` 回补至少覆盖 365 根以上。
2. 推荐预留缓冲，至少回补 500 根日 K。
3. 如果用户希望更完整的高周期背景，可以回补 2 到 3 年或更长历史。
4. 起止时间必须由 CLI 参数明确指定，不得在代码中写死。
5. 如果 Binance REST 返回的可用历史少于请求范围，应记录实际 `first_open`、`latest_open` 和实际写入数量。
6. 示例命令中的 `start-utc` 只是示例，不代表硬编码起点。

---

## 9. 1d 手动回补设计

### 9.1 建议入口

后续实现可以新增 `1d` 专用脚本，也可以在清晰参数化后复用已有脚本风格。建议入口示例：

```bash
python -m scripts.backfill_1d_klines \
  --symbol BTCUSDT \
  --interval 1d \
  --start-utc "2025-01-01T00:00:00Z" \
  --end-utc "2026-05-15T00:00:00Z" \
  --trigger-source cli \
  --confirm-write \
  --notify-success
```

脚本命名由后续实现阶段根据代码结构决定，但必须满足：

1. `scripts` 只作为 CLI 入口。
2. 核心逻辑必须放在 `app/market_data` 内部 service。
3. 手动回补只能由 `cli` 触发。
4. `--trigger-source` 必须显式传入 `cli`。
5. 不允许 scheduler 调用手动回补脚本。
6. 脚本不得直接请求 Binance。
7. 脚本不得直接写数据库。
8. 脚本不得直接发送 Hermes。

### 9.2 trigger_source 与 data_source

手动 `1d` 回补必须记录：

```text
trigger_source = cli
data_source = binance_rest_by_cli
```

`data_source` 建议继续使用现有风格 `binance_rest_by_cli`，因为数据来源和触发来源语义与 `4h` 一致。不要为了 `1d` 新增模糊来源值。

### 9.3 回补写入规则

手动 `1d` 回补必须：

1. 只请求 Binance REST 官方 K线。
2. 只写入 `1d` 独立正式表。
3. 只写入已收盘日 K。
4. 对请求范围内 REST 返回批次做连续性检查。
5. 对数据库已有同 open_time 记录做一致性检查。
6. 已存在且字段一致的日 K应跳过，不重复写入。
7. 已存在但字段冲突时必须 blocked，不覆盖。
8. 写入必须幂等。
9. 数据不连续、重复、未收盘、字段异常时必须阻止写入。
10. 质量异常必须记录 `data_quality_check`，并通过 Hermes 中文告警。
11. 不允许自动修复。
12. 不允许人工改数。

---

## 10. 1d 增量采集设计

`1d` 增量采集原则与 `4h` 增量采集一致，但周期调整为 `1d`。

### 10.1 调度时间

建议每天运行：

```text
UTC 00:10
北京时间 08:10
```

原因：

1. 避免刚收盘时 REST 数据尚未稳定。
2. 避免和现有 `4h` 增量采集 UTC `00:05` 冲突。
3. 给 `4h` 采集和基础检查留出时间。

### 10.2 调用链原则

正式 scheduler 触发 `1d` 增量采集时：

```text
scheduler runner
    ↓
app/scheduler/jobs/kline_1d_incremental_collect.py
    ↓
app/market_data/collector/<1d collector service>
```

要求：

1. scheduler 不允许通过 scripts 间接执行采集。
2. scheduler 不允许 subprocess / runpy / `python -m scripts...` 调用采集。
3. scheduler job 必须直接调用 app service。
4. scheduler job 必须显式传入 `trigger_source=scheduler`。
5. 正式写入的 `data_source` 必须是 `binance_rest_by_scheduler`。

### 10.3 采集流程

`1d` 增量采集流程：

1. scheduler 在 UTC `00:10` 触发 `1d` collector service。
2. collector service 获取当前理论上最新已收盘日 K。
3. 查询 `1d` 正式表中 `BTCUSDT` 最新 `open_time_utc`。
4. 通过 Binance REST 拉取足够覆盖 continuity check 的日 K 数据。
5. 过滤当前未收盘日 K。
6. 校验 REST 返回批次内部连续性。
7. 校验 REST 数据与数据库最新日 K是否连续。
8. 校验重叠 K线与数据库已有记录是否一致。
9. 跳过已存在且一致的日 K。
10. 只写入缺失的新已收盘日 K。
11. 写入 `collector_event_log`，记录 `running`、`success`、`failed`、`blocked`、`partial_success` 或 `skipped`。
12. 异常通过 Hermes 中文告警。

要求：

1. 必须有反重入机制，避免同一 `symbol + interval=1d` 并发采集。
2. 手动 `1d` 回补与 scheduler `1d` 增量采集不得同时写正式 `1d` 表。
3. 写入必须幂等。
4. 不允许自动修复。
5. 不允许自动回补不受控的大范围历史。

---

## 11. 1d REST 重叠拉取规则

`1d` 增量采集不得只拉最新一根日 K。

必须保留与 `4h` 一致的重叠拉取思想：拉取多根的目的是检查连续性、发现短期漏采和验证重叠数据一致性，不是重复写库。

示例：

```text
数据库最新日 K：2026-05-13 00:00 UTC
当前理论最新已收盘日 K：2026-05-15 00:00 UTC
```

REST 拉取范围应覆盖：

```text
2026-05-13 00:00 UTC
2026-05-14 00:00 UTC
2026-05-15 00:00 UTC
```

写入规则：

1. `2026-05-13` 已存在且一致，不重复写。
2. `2026-05-14` 如果缺失且质量通过，则写入。
3. `2026-05-15` 如果已收盘、缺失且质量通过，则写入。
4. 如果发现中间缺口、REST 返回不连续或重叠字段冲突，应阻止写入并告警。

该规则必须在 implementation 中写清楚：重叠拉取是为了检查连续性和一致性，不是为了重复写库。

---

## 12. 未收盘日 K 的处理语义

第 14 阶段必须区分两种完全不同的情况。

### 12.1 预期内未收盘过滤

Binance REST 返回当前 UTC 当天尚未收盘的 `1d` K线时：

1. collector / backfill 必须过滤掉。
2. 不写入正式 `1d` 表。
3. 这本身不应视为 error。
4. 如果用户开启通知，可以作为 notice 或摘要说明。
5. 如果过滤未收盘 K 后，剩余已收盘 K线连续性正常，应允许写入剩余已收盘缺失 K线。

示例：

```text
当前 Binance server time：2026-05-15 12:00 UTC
REST 返回 2026-05-15 00:00 UTC 日 K
该日 K尚未到 2026-05-16 00:00 UTC 收盘边界
系统过滤，不写库，不视为数据质量错误
```

### 12.2 正式表未收盘误写

如果未收盘日 K已经进入正式 `1d` 表，或者数据库最新 K线晚于理论最新已收盘日 K：

1. 必须视为 error / critical 数据质量异常。
2. 必须记录 `data_quality_check`。
3. 必须通过 Hermes 中文告警。
4. 不得自动删除、覆盖或修复该记录。
5. 不得请求 Binance 后静默覆盖。

示例：

```text
当前理论最新已收盘日 K：2026-05-14 00:00 UTC
数据库最新日 K：2026-05-15 00:00 UTC
```

该情况疑似未收盘日 K误写正式表或系统时间异常，必须暴露给用户。

### 12.3 非预期时间边界异常

如果 REST 返回批次中出现非预期的未收盘 K线、open_time 不在 UTC `00:00:00`、close_time 不符合 `1d` 周期，或批次时间边界异常，应阻止写入并告警。

---

## 13. 1d 数据质量检查

`1d` 数据质量检查原则与 `4h` 保持一致，但周期必须按 `1d`。

检查内容至少包括：

1. `open_time_ms` 是否按 `1d` 连续。
2. 不允许重复 `open_time_ms`。
3. 不允许未收盘日 K进入正式表。
4. `high >= max(open, close, low)`。
5. `low <= min(open, close, high)`。
6. `open/high/low/close/volume/quote_volume/trade_count` 等字段不能为空。
7. 价格不能为负数或零。
8. 成交量不能为负数。
9. `close_time_ms` 与 `open_time_ms` 的 interval 必须符合 `1d`。
10. `open_time_utc` 必须是 UTC `00:00:00`。
11. 最新日 K是否明显滞后。
12. 最新日 K是否晚于理论最新已收盘日 K，疑似未收盘误写。
13. `trigger_source` 与 `data_source` 映射必须合法。
14. 不允许人工修复、人工输入或自动修复来源。

实现要求：

1. 可以复用现有通用 K线质量逻辑，但 interval duration 必须可配置。
2. 如果现有 checker 写死 `4h`，后续实现必须参数化或新增 `1d` 版本。
3. 质量异常必须 Hermes 中文告警。
4. 每日健康结果是否发送成功提醒，应与 `4h` 每日复核策略保持一致，至少要能让用户知道 `1d` 是否健康。
5. `1d` 质量检查只读，不修复、不回补、不改表。

---

## 14. 1d 每日复核设计

第 14 阶段应将 `1d` 纳入日常健康检查。

建议：

1. `1d` 每日复核在日 K增量采集后运行。
2. 建议时间为 UTC `00:20` 或 UTC `00:30`，北京时间 `08:20` 或 `08:30`。
3. 具体时间以现有 scheduler 设计为准，但必须避免和 `4h` 关键任务互相阻塞。
4. 复核只读，不修复、不回补、不改表。
5. 复核发现问题直接 Hermes 中文告警。
6. 健康结果可以按现有 `4h` 每日复核规则发送每日摘要，避免用户不知道日 K是否健康。

`1d` 每日复核不是策略任务，不调用大模型，不做交易判断。

---

## 15. Hermes 告警要求

第 14 阶段 `1d` 相关提醒必须中文、精简，并保持现有告警状态语义。

必须覆盖的提醒场景：

1. `1d` 手动回补成功。
2. `1d` 手动回补 blocked。
3. `1d` 手动回补 failed。
4. `1d` 增量采集成功。
5. `1d` 增量采集 blocked。
6. `1d` 增量采集 failed。
7. `1d` 数据质量异常。
8. `1d` 每日复核健康。
9. `1d` 每日复核异常。
10. `1d` 最新已收盘 K线缺失。
11. 当前未收盘日 K被过滤，符合预期时不作为 error，可作为 notice 或摘要说明。

告警内容要求：

1. 标题和正文应明确 `BTCUSDT 1d`，不得让用户误以为是 `4h`。
2. 正文应中文优先，避免大量英文内部字段原样输出。
3. 不展开完整 `report_json`、原始 REST payload、Redis key 列表或内部 dict。
4. 必须保留边界声明：不自动修复、不人工改数、不自动回补、不执行自动交易。
5. 不得调用 DeepSeek 或其他大模型生成基础告警。
6. Hermes HTTP 2xx 只代表 `submitted_to_hermes`，不代表微信最终送达。
7. 不允许使用“微信发送成功”“微信已送达”等无法证明的文案。

如果同一时间段多个任务同时失败，Hermes 告警必须能区分是 `4h` 还是 `1d`。

---

## 16. scheduler 要求

第 14 后续实现需要扩展 scheduler，但本次只写计划文档。

设计要求：

1. 新增 `1d` 增量采集任务。
2. 新增或扩展 `1d` 每日复核任务。
3. `1d` 增量采集时间建议 UTC `00:10` / 北京时间 `08:10`。
4. `1d` 复核时间建议 UTC `00:20` 或 UTC `00:30`。
5. scheduler 不通过 scripts 调用采集。
6. scheduler 直接调用 app service。
7. 必须区分 `4h` 与 `1d` 的 scheduler job key。
8. 必须有防重入。
9. `1d` 任务状态和 `4h` 任务状态不能互相覆盖。
10. 如果现有 scheduler key 命名写死 `4h`，后续实现阶段必须调整为 interval-aware 或新增 `1d` 专用 key。

建议 scheduler job 名称：

```text
kline_1d_incremental
daily_kline_1d_integrity
```

具体命名由后续实现阶段根据现有 `app/scheduler/slot_state.py` 风格确定。

---

## 17. UTC 00:00 附近任务顺序

建议任务顺序：

1. UTC `00:05`：现有 `4h` 增量采集。
2. UTC `00:10`：新增 `1d` 增量采集。
3. UTC `00:20` 或 `00:30`：新增 `1d` 健康复核。
4. 现有每日 `4h` integrity check 如已安排在 UTC `00:30`，第 14 实现阶段必须避免互相阻塞，可以错峰到可配置时间。

如果同一时间段多个任务同时失败，Hermes 告警必须能区分：

1. `BTCUSDT 4h` 增量采集失败。
2. `BTCUSDT 1d` 增量采集失败。
3. `BTCUSDT 4h` 每日复核异常。
4. `BTCUSDT 1d` 每日复核异常。

---

## 18. scheduler 补跑语义

`1d` scheduler 补跑规则：

1. 如果 scheduler 在 UTC `00:10` 错过 `1d` 增量采集，恢复后应能补跑最近一次应执行的 `1d` 任务。
2. 补跑不应盲目循环触发多次历史 scheduler job。
3. 具体缺失多少日 K，应由 `1d` collector 根据数据库最新 `open_time` 和理论最新已收盘日 K计算。
4. collector 可以通过 REST 拉取缺失区间，必须做连续性校验。
5. 如果数据库落后多天，collector 可以一次拉取多根已收盘日 K，但必须做连续性校验和重叠一致性检查。
6. 如果落后过多或 REST 返回异常，应 blocked 并 Hermes 告警，不自动进行不受控的大范围修复。
7. scheduler 补跑只表示延迟触发同一个应执行 slot，不表示自动修复数据。

---

## 19. data_source / event_type / lock key 语义

后续实现必须清楚区分 `4h` 与 `1d`。

### 19.1 data_source

`1d` 手动回补：

```text
trigger_source = cli
data_source = binance_rest_by_cli
```

`1d` scheduler 增量采集：

```text
trigger_source = scheduler
data_source = binance_rest_by_scheduler
```

### 19.2 event_type

`collector_event_log` 必须通过 `interval_value=1d`、`event_type` 或 `source` 明确区分 `1d`。

建议：

```text
manual_backfill_1d
kline_1d_incremental_collect
daily_kline_1d_integrity
```

不要复用容易误解的 `manual_backfill_4h`、`kline_4h_incremental_collect` 或 `daily_kline_integrity`，除非字段中有非常清楚的 `interval_value=1d` 且通知标题明确 `1d`。

### 19.3 lock key

Redis / scheduler / collector lock key 必须包含 `symbol` 和 `interval`。

示例：

```text
kline_write:BTCUSDT:1d
kline_integrity_check:BTCUSDT:1d
scheduler:running:kline_1d_incremental:2026-05-15T00:10Z
scheduler:completed:kline_1d_incremental:2026-05-15T00:10Z
scheduler:status:kline_1d_incremental:2026-05-15T00:10Z
```

要求：

1. `1d` 防重入不能和 `4h` 互相阻塞，除非明确是写同一张表。
2. 本阶段要求独立表，因此 `1d` 与 `4h` 应分别加锁。
3. 手动 `1d` backfill 与 scheduler `1d` collector 不得并发写 `1d` 正式表。
4. `1d` 写入锁不得误用 `4h` 锁 key。

---

## 20. runtime status 关系

第 14 实现完成后，应考虑扩展 `scripts.check_runtime_status` 的只读展示能力。

至少能展示：

1. 最新 `BTCUSDT 1d` K线时间。
2. `1d` 数据是否滞后。
3. 最近一次 `1d` 增量采集状态。
4. 最近一次 `1d` 每日复核状态。

该扩展必须只读：

1. 不触发采集。
2. 不触发回补。
3. 不请求 Binance。
4. 不写正式 K线表。
5. 不修改 Redis 状态。
6. 不调用大模型。
7. 不执行交易。

如果为了控制范围不在第 14 代码实现中修改 runtime status，也必须在第 14 implementation 中标记为后续运维扩展项。

关键要求：不能让 `1d` 异常被 `4h` 健康状态掩盖。

---

## 21. 模块边界与复用原则

后续实现应优先复用现有成熟能力，但不能牺牲周期边界。

可以复用：

1. `BinanceRestClient.get_server_time()`
2. `BinanceRestClient.get_klines()`
3. Kline DTO，前提是 DTO 没有业务上写死 `4h`
4. `parse_binance_klines()`，前提是传入 `interval_value=1d`
5. UTC / PRC 时间工具
6. `app/alerting` 固定模板链路
7. scheduler runner 框架
8. `collector_event_log`、`data_quality_check`、`alert_message` 辅助表

必须先审查的硬编码点：

1. 表名是否写死 `market_kline_4h`。
2. repository 是否只访问 `market_kline_4h`。
3. `interval_value` 是否写死 `4h`。
4. `KLINE_4H_INTERVAL_MS = 14,400,000` 是否被用于连续性判断。
5. validator 是否写死 `interval_value must be 4h`。
6. quality checker 是否写死 4h 周期。
7. alert title 是否写死 `4h`。
8. `event_type` 是否写死 `4h`。
9. scheduler job name 和 slot key 是否写死 `4h`。
10. tests 是否只覆盖 `4h`。

如果现有代码写死 `4h`，后续实现不得假设它已经完全通用。可以选择：

1. 参数化通用函数，显式传入 interval duration 和 repository。
2. 新增 `1d` 专用 service / repository / checker，复用底层纯函数。
3. 使用共享 mixin 或 helper 降低重复，但入口、表和 repository 边界必须清晰。

---

## 22. 测试要求

第 14 实现阶段必须补充测试。默认测试不得请求真实 Binance、不得连接真实 MySQL、不得连接真实 Redis、不得发送真实 Hermes、不得调用 DeepSeek、不得访问交易接口。

### 22.1 1d 表结构迁移测试

至少覆盖：

1. 创建独立 `1d` 表。
2. 不与 `4h` 混表。
3. 唯一约束防止重复 `open_time`。
4. migration 不修改 `market_kline_4h`。

### 22.2 1d 手动回补测试

至少覆盖：

1. 正常写入缺失日 K。
2. 已存在日 K跳过，不重复写。
3. REST 返回未收盘日 K时过滤。
4. 数据不连续时 blocked。
5. 字段异常时 blocked。
6. `trigger_source=cli`。
7. `data_source=binance_rest_by_cli`。
8. 不写 `market_kline_4h`。

### 22.3 1d 增量采集测试

至少覆盖：

1. 理论最新已收盘日 K能被识别。
2. 每日 UTC `00:10` 后采集。
3. 重叠拉取用于连续性检查。
4. 只写新缺失日 K。
5. 不重复写。
6. 采集失败记录 `collector_event_log`。
7. 质量异常 Hermes 中文告警。

### 22.4 1d 未收盘过滤测试

至少覆盖：

1. 当前 UTC 当天的日 K未收盘时不能写入正式表。
2. 未收盘过滤符合预期时不应作为 error。
3. 正式表误写未收盘 K时必须作为 error / critical。
4. 数据库最新 K线晚于理论最新已收盘日 K时必须报错。

### 22.5 1d 质量检查测试

至少覆盖：

1. 连续性检查按 `1d`。
2. 重复检查。
3. 缺失检查。
4. 字段合理性检查。
5. 最新日 K滞后检查。
6. 最新日 K过新检查。
7. 不使用 `4h` 间隔判断 `1d` 连续性。

### 22.6 1d scheduler 测试

至少覆盖：

1. job key 区分 `1d`。
2. 防重入。
3. 不通过 scripts 调用采集。
4. 不影响 `4h` 任务。
5. 补跑最近一次应执行任务。
6. 不盲目循环补跑大量历史 scheduler job。

### 22.7 Hermes 告警测试

至少覆盖：

1. `1d` 异常中文告警。
2. `1d` 健康摘要。
3. 不输出“微信发送成功”。
4. 不输出大量内部字段。
5. `4h` 与 `1d` 告警可区分。
6. 边界声明包含不自动修复、不人工改数、不自动回补、不执行自动交易。

### 22.8 表隔离测试

至少覆盖：

1. `1d` 测试不得依赖 `4h` 正式表。
2. `4h` 测试不得因为新增 `1d` 表而失败。
3. `1d` repository / model 测试必须验证只访问 `1d` 表。
4. `1d` 写入测试必须验证不会向 `4h` 表写入任何记录。
5. 如果使用 shared helper，必须覆盖 `interval=4h` 与 `interval=1d` 的行为差异，尤其是 interval duration。

### 22.9 回归测试

至少覆盖：

1. 不影响 `4h` backfill。
2. 不影响 `4h` incremental collector。
3. 不影响 `4h` daily integrity。
4. 不影响 10s price monitor。
5. 不影响 runtime status。
6. 不影响 Hermes gateway。

---

## 23. 验收标准

第 14 实现完成后必须满足：

1. 存在独立 `BTCUSDT 1d` 正式 K线表。
2. 可以手动回补 `1d` 历史 K线。
3. 可以每日自动采集最新已收盘 `1d` K线。
4. `1d` K线写入幂等。
5. `1d` 未收盘 K线不会写入正式表。
6. `1d` 数据连续性、重复、字段合理性可检查。
7. `1d` 异常能 Hermes 中文告警。
8. `1d` 健康结果可以被用户看到。
9. 运行状态检查或后续运维入口不会让 `1d` 异常被 `4h` 健康状态掩盖。
10. 不允许人工改数。
11. 不允许自动交易。
12. 不调用大模型。
13. 不生成策略建议。
14. `4h` 原有功能不受影响。
15. 第 15 阶段可以基于 `4h + 1d` 构建 `MarketContextSnapshot`。

---

## 24. 与第 15 阶段关系

第 14 阶段只解决 `1d` 数据基础。

第 15 阶段才做：

1. `4h + 1d` 多周期市场上下文快照。
2. `4h` 主操作周期。
3. `1d` 高周期背景。
4. `1m` 结构预留。
5. `MarketContextSnapshot`。
6. `formal_strategy_usable` 判断。

第 14 阶段不做这些内容。

第 15 阶段必须遵守：

1. 不允许为了生成快照临时请求 Binance REST 拉取 `1d`。
2. 只能读取第 14 阶段已经维护好的 `1d` 正式表。
3. 如果第 14 的 `1d` 数据不可用，第 15 快照必须标记 `1d context unavailable`。
4. 如果 `1d` 上下文不可用，`formal_strategy_usable=false`。
5. 第 15 不得绕过第 14 的数据质量状态。

这样可以保证策略层只读取可追溯、可复核、可告警的正式数据，而不是在策略执行时临时拉取不可审计的外部数据。

---

## 25. 第 14 阶段交付物建议

后续实现阶段建议交付：

1. `market_kline_1d` ORM model。
2. `market_kline_1d` Alembic migration。
3. `MarketKline1dRepository` 或清晰的周期隔离 repository。
4. `1d` 手动回补 CLI 薄入口。
5. `1d` 手动回补 app service。
6. `1d` 增量采集 app service。
7. `1d` scheduler job。
8. `1d` 质量检查或参数化通用质量检查。
9. `1d` 每日复核入口。
10. `1d` Hermes 中文告警模板。
11. `1d` 测试。
12. `docs/implementation/14_daily_kline_data_foundation.md`。

implementation 文档必须写清楚：

1. 每个入口文件和入口方法。
2. 核心 service 文件和方法。
3. Binance REST 调用链。
4. 读取和写入哪些数据库表。
5. 读取和写入哪些 Redis key。
6. 是否发送 Hermes。
7. `trigger_source` 与 `data_source` 映射。
8. `1d` 与 `4h` 表隔离。
9. 未收盘过滤与正式表误写未收盘的区别。
10. 异常如何处理。
11. 哪些事情明确不做。
12. 对应测试和人工检查命令。

---

## 26. 人工审查清单

第 14 实现合并前，用户应重点检查：

1. 是否创建独立 `market_kline_1d`，而不是写入 `market_kline_4h`。
2. 是否存在任何混合正式 K线表。
3. `1d` 连续性是否使用 `86,400,000` 毫秒。
4. `1d` open time 是否校验 UTC `00:00:00`。
5. 是否使用 Binance server time 过滤未收盘日 K。
6. 预期内未收盘过滤是否没有误报为 error。
7. 正式表未收盘误写是否会报 error / critical。
8. scheduler 是否直接调用 app service。
9. scheduler job key 是否区分 `1d` 和 `4h`。
10. 手动回补是否只允许 `trigger_source=cli`。
11. scheduler 采集是否记录 `trigger_source=scheduler`。
12. `data_source` 是否正确映射。
13. `collector_event_log` 是否能区分 `1d` 与 `4h`。
14. Hermes 文案是否中文、精简、可区分 `1d` 与 `4h`。
15. 是否没有自动修复、自动回补、人工改数或自动交易。
16. 第 15 是否只能读取正式 `1d` 表，不临时请求 Binance REST。

建议搜索危险和边界关键词时，只把本计划中的禁止说明作为允许出现的文档说明，不得在代码路径中实现这些能力。

---

## 27. 实施前补充约束

本节用于补充第 14 阶段后续实现前必须遵守的边界，防止 `1d` 数据基础在上线、初始化、调度和运维阶段出现语义混乱。

### 27.1 空表初始化语义

`market_kline_1d` 首次上线时可能为空。空表状态必须与数据异常区分处理。

要求：

1. 如果 `market_kline_1d` 为空，scheduler 增量采集不得自动进行大范围历史初始化。
2. 正式 `1d` 表的首次初始化必须通过人工 CLI backfill 完成。
3. scheduler 检测到 `1d` 表为空时，应记录 `skipped` 或 `blocked`，并用中文说明：`1d 数据尚未初始化，请先执行手动回补`。
4. 不允许 scheduler 自动拉取 365 / 500 根历史日 K 完成初始化。
5. 不允许把 `not_initialized` 直接等同于数据质量 `error`。
6. 如果用户尚未完成首次手动回补，运行状态检查应明确显示 `1d 未初始化`，而不是误报为系统故障。

### 27.2 第 14 阶段上线顺序

第 14 阶段实现完成后，不得直接启用正式 `1d` 调度任务。推荐上线顺序：

1. 执行 Alembic migration，创建独立 `1d` 正式 K线表。
2. 人工执行 `1d` 历史回补，至少满足第 15 阶段所需的最低日 K 数量。
3. 执行 `1d` 数据质量检查，确认连续性、字段合理性和未收盘过滤逻辑正常。
4. 确认 `runtime status` 或后续运维入口可以看到 `1d` 状态。
5. 再启用 `1d` scheduler 增量采集。
6. 最后启用 `1d` 每日复核任务。

要求：

1. 不允许在 `1d` 表未初始化时直接启用正式 `1d` scheduler。
2. 如果必须先部署代码再回补数据，scheduler 应保持禁用，或者在空表状态下只记录 `not_initialized` / `skipped`，不得自动初始化。
3. 上线文档中必须说明手动回补、质量检查、启用调度的顺序。

### 27.3 start-utc / end-utc 边界语义

手动回补参数必须明确时间边界，避免把当前未收盘日 K 当成异常。

要求：

1. `--start-utc` 和 `--end-utc` 必须对齐 UTC `00:00:00`。
2. 如果传入时间不在 UTC `00:00:00`，脚本应拒绝执行，并给出中文错误说明。
3. `start-utc` / `end-utc` 的 inclusive / exclusive 语义必须与现有 `4h` backfill 保持一致，并在 implementation 文档中写清楚。
4. 如果 `end-utc` 指向当前未收盘日 K，系统应按未收盘过滤处理，不应误报为 `error`。
5. 如果 `end-utc` 明显晚于可用的理论最新已收盘日 K，系统应只写入已收盘日 K，并在结果摘要中说明过滤了未收盘部分。
6. 所有边界判断必须以 UTC 为准，PRC 只用于展示。

### 27.4 多年回补分页与整体连续性

如果用户回补 2 到 3 年或更长历史，Binance REST 可能无法在单次请求中返回完整数据。

要求：

1. 当请求范围超过 Binance REST 单次返回上限时，必须按时间分批拉取。
2. 每一批 REST 返回数据都要做批次内部连续性检查。
3. 多个批次合并后，还要做整体连续性检查。
4. 不得因为分页导致中间缺口被忽略。
5. 不得只检查每一页内部连续，却不检查页与页之间的衔接。
6. 如果分页过程中某一批失败，应阻止最终写入或明确进入 `partial_success` 语义，不得静默跳过。
7. 分页回补仍然必须遵守幂等写入、未收盘过滤、重叠校验和不覆盖正式数据的规则。

### 27.5 1d 健康通知与微信限流

第 14 阶段引入 `1d` 后，UTC `00:00` 附近可能同时存在：

1. `4h` 增量采集。
2. `1d` 增量采集。
3. `1d` 每日复核。
4. 既有 `4h` 相关健康检查或运行状态提醒。

要求：

1. `1d` 健康摘要和 `4h` 健康摘要应避免在短时间内产生大量重复通知。
2. 后续可以考虑把 `4h + 1d` 健康结果合并为一条每日健康摘要。
3. 如果暂不合并，也必须使用精简中文模板，避免长报文触发微信限流或造成阅读负担。
4. 如果多个任务连续失败，告警必须能区分 `4h` 与 `1d`，但不得用大量英文内部字段刷屏。
5. Hermes HTTP 2xx 仍然只表示 `submitted_to_hermes`，不表示微信最终送达。
6. 不能使用 `微信发送成功`、`微信已送达` 这类无法由 BTC Agent 证明的文案。

### 27.6 runtime status 的 1d 状态级别

第 14 实现完成后，运行状态检查不应只显示“有无 1d 数据”，而应区分状态级别。

建议至少支持：

```text
not_initialized：1d 表为空，尚未完成首次手动回补
healthy：最新已收盘日 K存在，且最近质量检查通过
stale：最新日 K落后理论最新已收盘日 K
error：质量检查失败、未收盘误写、字段异常、采集失败或网关告警失败
```

要求：

1. `not_initialized` 表示尚未完成初始化，不应直接等同于系统错误。
2. `stale` 表示数据滞后，需要用户检查采集链路或手动回补。
3. `error` 表示已经发现明确异常，应进入错误结论或至少严重警告。
4. 不能让 `1d` 异常被 `4h` 健康状态掩盖。
5. 如果第 14 代码实现阶段暂不扩展 `runtime status`，必须在 implementation 或后续计划中明确标记为运维扩展项。
6. 第 15 阶段构建 MarketContextSnapshot 时，若 `1d` 状态为 `not_initialized`、`stale` 或 `error`，不得标记为可用于正式策略分析。

### 27.7 第 15 阶段对第 14 的强依赖

第 15 阶段不允许绕过第 14 的数据基础。

要求：

1. 第 15 阶段的 MarketContextSnapshot 只能读取第 14 维护好的 `1d` 正式表。
2. 第 15 阶段不允许临时请求 Binance REST 拉取 `1d`。
3. 如果 `1d` 表为空，快照应显示 `1d context not_initialized`，并标记 `formal_strategy_usable=false`。
4. 如果 `1d` 滞后或质量失败，快照应显示对应原因，并标记 `formal_strategy_usable=false`。
5. 不允许为了让策略看起来完整而临时补拉日 K 或忽略 `1d` 缺失。
6. `1d` 是正式策略分析的大周期背景必需输入，不是可有可无的装饰字段。

