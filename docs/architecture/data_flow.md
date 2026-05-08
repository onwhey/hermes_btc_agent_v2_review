# data_flow.md

# 系统数据流设计

## 1. 文档目的

本文档定义 Hermes + DeepSeek BTC 合约策略辅助系统中的主要数据流。

注意：本文档只描述系统中数据应该如何流动，不是具体开发任务清单。

Codex 不得因为本文档描述了某条未来数据流，就在当前阶段提前实现所有相关模块。

具体是否实现、何时实现、实现哪些文件，以当前 `docs/plans/*.md` 为准。

如果本文档与当前 plan 的实现范围不一致，应以当前 plan 的阶段范围为准；如果发现需求或架构冲突，Codex 应停止开发并提示需要先统一文档。

本文档回答以下问题：

1. 数据从哪里来。
2. 数据经过哪些模块。
3. 数据在哪里校验。
4. 数据什么时候可以入库。
5. 数据异常时如何记录和提醒。
6. 当前阶段数据采集如何流转。
7. 未来策略、建议、复盘、人工执行如何流转。
8. 哪些数据流禁止出现。

本文档不负责：

1. 具体数据库字段。
2. 具体代码文件清单。
3. 具体策略算法。
4. 具体大模型 prompt。
5. 具体部署命令。

具体需求见 `docs/requirements/`。

总体架构见 `docs/architecture/system_architecture.md`。

模块边界见 `docs/architecture/module_boundaries.md`。

具体开发计划见 `docs/plans/`。

---

## 2. 总体数据流原则

系统所有数据流必须遵守以下原则：

1. 4h 主 K线以 Binance REST 已收盘 K线为权威数据源。
2. WebSocket 只用于最新价格监控和价格事件提醒，不用于拼接正式 4h 主 K线。
3. 数据质量优先级高于尽快写入。
4. K线未通过质量检查，不得写入正式 K线表。
5. 数据采集失败必须记录事件并通过 Hermes 提醒。
6. 数据质量异常必须记录质量检查结果并通过 Hermes 提醒。
7. 基础系统提醒不得调用 DeepSeek、OpenAI、Grok 或其他大模型。
8. 微信提醒统一通过 Hermes。
9. 长期数据必须进入 MySQL。
10. Redis 只保存短期状态和缓存。
11. 行情顺序必须基于 `open_time_ms` 或 `open_time_utc`。
12. 禁止依赖数据库自增 id 判断行情顺序。
13. 系统内部业务判断统一使用 UTC。
14. 用户消息中涉及时间时，应同时展示北京时间和 UTC。
15. 系统不得实现自动下单、自动平仓、自动调仓或自动读取账户后执行操作。

---

## 3. 当前阶段核心数据流

当前阶段只重点实现以下数据流：

1. 4h K线历史回补数据流。
2. 4h K线增量采集数据流。
3. K线采集失败与数据质量异常告警数据流。
4. 10s 最新价格监控数据流。
5. Redis 最新价格缓存数据流。
6. Hermes 基础提醒数据流。

当前阶段不实现：

1. 策略信号数据流。
2. 策略聚合数据流。
3. DeepSeek 分析数据流。
4. 操作建议生命周期数据流。
5. 复盘评估数据流。
6. 人工执行反馈数据流。
7. Admin 管理后台数据流。
8. 自动交易数据流。

未来数据流可以预留边界，但不得提前混入当前数据采集代码。

当前阶段开发应按基础能力先行的顺序推进。

推荐 plan 顺序如下：

1. `01_project_skeleton.md`
2. `02_core_config_logging.md`
3. `03_infra_mysql_redis.md`
4. `04_alerting_through_hermes.md`
5. `05_binance_rest_client.md`
6. `06_market_kline_4h.md`
7. `07_kline_quality_checker.md`
8. `08_4h_backfill.md`
9. `09_4h_incremental_collector.md`
10. `10_price_monitor_10s.md`

依赖关系如下：

    project_skeleton
        ↓
    core_config_logging
        ↓
    infra_mysql_redis
        ↓
    alerting_through_hermes
        ↓
    binance_rest_client
        ↓
    market_kline_4h
        ↓
    kline_quality_checker
        ↓
    4h_backfill
        ↓
    4h_incremental_collector
        ↓
    price_monitor_10s

后续业务模块不得重复实现这些基础能力。

例如：

1. MySQL 连接只能通过统一 storage/mysql 模块使用。
2. Redis 连接只能通过统一 storage/redis 模块使用。
3. Hermes 微信提醒只能通过统一 alerting 模块使用。
4. Binance REST 请求只能通过统一 exchange/binance 模块使用。
5. UTC 与 PRC 时间转换只能通过统一 core/time_utils 模块使用。
6. 配置读取只能通过统一 core/config 模块使用。
7. 日志只能通过统一 core/logger 模块使用。

---

## 4. 基础能力数据流

基础能力是后续所有业务模块的公共依赖，必须先实现，并且后续模块必须复用。

### 4.1 配置读取数据流

    .env / 环境变量 / configs
        ↓
    app/core/config
        ↓
    settings 对象
        ↓
    各业务模块读取配置

规则：

1. 业务模块不得到处直接读取 `.env`。
2. 业务模块不得硬编码数据库密码、Redis 地址、Hermes secret、Binance 配置。
3. 敏感配置不得写入日志。
4. 配置读取失败应抛出明确异常。
5. APP_DEBUG 等全局开关应由统一配置模块读取。

### 4.2 日志数据流

    app/core/logger
        ↓
    各业务模块记录运行日志
        ↓
    控制台 / 日志文件

规则：

1. 日志用于排查问题。
2. 日志不能替代数据库事件记录。
3. 数据采集失败不能只写日志。
4. 数据质量异常不能只写日志。
5. Hermes 提醒不能只写日志。
6. 日志中不得包含 API Key、Secret、Token、数据库密码。

### 4.3 时间转换数据流

    UTC 时间
        ↓
    app/core/time_utils
        ↓
    PRC / 北京时间展示

规则：

1. 内部判断使用 UTC。
2. 用户展示可以显示北京时间。
3. UTC 与 PRC 转换必须通过统一时间工具。
4. 禁止在业务代码中到处手写 `+8 小时`。
5. 用户消息中涉及时间时，应同时展示北京时间和 UTC。

### 4.4 MySQL 数据流

    app/storage/mysql
        ↓
    SQLAlchemy session
        ↓
    Repository
        ↓
    MySQL 表

规则：

1. MySQL 连接必须统一管理。
2. Repository 只负责数据读写。
3. 业务模块不得绕过 Repository 写复杂 SQL。
4. 数据库异常不得被静默吞掉。
5. 长期数据必须进入 MySQL。

### 4.5 Redis 数据流

    app/storage/redis
        ↓
    Redis client
        ↓
    Redis key 管理
        ↓
    短期缓存 / 冷却 / 临时状态

规则：

1. Redis 只保存短期状态。
2. Redis 不作为长期行情数据库。
3. Redis key 命名应统一管理。
4. TTL 应由业务场景明确指定。
5. 后续模块不得各自创建混乱的 Redis key。

---

## 5. 历史 4h K线回补数据流

历史回补用于系统初始化、补齐较长时间范围的历史 K线，或在明确需要时重新拉取某一段历史区间的官方已收盘 K线。

历史回补可以由 CLI 手动触发，也可以由后续受控任务触发。但无论触发方式如何，正式 4h K线表中的 OHLCV 等核心行情字段只能来自 Binance U 本位合约 REST 官方已收盘 K线。

```text
用户执行历史回补命令
    ↓
scripts/backfill_4h_klines.py
    ↓
app/market_data/4h backfill service
    ↓
校验输入参数
    包括：
        - symbol
        - interval
        - start_time
        - end_time
        - limit / batch_size
    ↓
创建 collector_event_log
    status = running
    symbol = BTCUSDT
    interval = 4h
    data_source = binance_rest_by_cli
    collection_mode = manual_backfill
    started_at_utc = 当前 UTC 时间
    started_at_prc = 当前 PRC 时间
    ↓
调用 Binance REST Client
    ↓
按时间范围分批拉取 Binance 官方 4h K线
    ↓
parser 转换为内部 Kline DTO
    ↓
过滤未收盘 K线
    判断标准：
        Binance server time >= kline.close_time_ms + KLINE_CLOSE_SAFETY_DELAY_MS
    ↓
查询数据库中目标时间范围内已有的 4h K线
    ↓
按 open_time_ms 对齐 REST K线与 DB K线
    ↓
校验重叠 K线是否一致
    如果同一 open_time_ms 同时存在于 REST 和 DB：
        - 核心字段一致：视为正常重叠，不重复写入
        - 核心字段不一致：标记为数据冲突，不静默覆盖
    ↓
校验目标时间范围内 K线是否连续
    包括：
        - REST 返回批次内部是否连续
        - 本次回补后目标区间是否仍存在缺口
    ↓
校验字段合理性
    包括：
        - open_price > 0
        - high_price >= max(open_price, close_price)
        - low_price <= min(open_price, close_price)
        - high_price >= low_price
        - volume >= 0
        - quote_volume >= 0
        - trade_count >= 0
        - open_time_ms / close_time_ms 符合 4h 周期规则
    ↓
计算允许写入集合
    只允许写入：
        - Binance REST 返回的已收盘 K线
        - 数据库中缺失的 K线
        - 通过字段合理性校验的 K线
        - 未与数据库已有 K线产生核心字段冲突的 K线

    不允许写入：
        - 未收盘 K线
        - 已存在且字段一致的重叠 K线
        - 与数据库已有 K线字段冲突的 K线
        - 来源不是 Binance REST 的 K线
        - 人工录入或人工修改的 K线
    ↓
通过 Repository 幂等写入正式 4h K线表
    写入时：
        data_source = binance_rest_by_cli
    ↓
写入 data_quality_check 或质量检查结果
    ↓
根据执行结果更新 collector_event_log

    如果全部成功：
        status = success
        finished_at_utc = 当前 UTC 时间
        finished_at_prc = 当前 PRC 时间
        fetched_count = REST 返回 K线数量
        inserted_count = 新写入 K线数量
        skipped_count = 已存在且一致的 K线数量
        conflict_count = 0
        error_message = null

    如果部分成功：
        status = partial_success
        finished_at_utc = 当前 UTC 时间
        finished_at_prc = 当前 PRC 时间
        fetched_count = REST 返回 K线数量
        inserted_count = 新写入 K线数量
        skipped_count = 已存在且一致的 K线数量
        conflict_count = 冲突数量
        error_message = 部分异常摘要

    如果发现关键字段冲突、非法 data_source、严重连续性异常：
        status = blocked
        finished_at_utc = 当前 UTC 时间
        finished_at_prc = 当前 PRC 时间
        error_message = 阻断原因

    如果 Binance REST 请求失败、解析失败、数据库写入失败：
        status = failed
        finished_at_utc = 当前 UTC 时间
        finished_at_prc = 当前 PRC 时间
        error_message = 失败原因
    ↓
如存在异常，直接通过 Hermes 发送基础系统报警
    不调用 DeepSeek 或其他大模型
    ↓
任务结束
```

成功路径要求：

1. 只写入已收盘 K线。
2. 入库前必须完成本批次连续性检查。
3. 入库前必须完成字段基础校验。
4. 重复执行历史回补时，不得插入重复 K线。
5. upsert 必须基于唯一键，不得依赖自增 id。
6. 回补任务必须记录执行结果。
7. 回补任务不能触发策略建议。
8. 回补任务不能调用大模型。
9. 回补任务不能自动交易。

历史回补的行情数值必须来自 Binance REST。

即便回补任务由用户手动触发，也不得人工直接修改 K线数据。手动触发只表示启动回补流程，不表示人工录入或人工修正 K线值。

写入 4h 主 K线表时，必须按实际触发入口记录 `data_source`：

1. 系统定时任务或服务内自动任务写入：`data_source = binance_rest_by_scheduler`。
2. 用户命令行手动触发脚本写入：`data_source = binance_rest_by_cli`。

无论 `data_source` 是上述哪一个，K线 OHLCV 等核心数值都必须来自 Binance REST 官方已收盘 K线。

禁止用 WebSocket 数据补正式 4h 主 K线。

---

## 6. 4h K线增量采集成功数据流

4h 增量采集用于周期性获取最新已收盘 4h K线。

数据流如下：

```text
    scheduler 或 scripts/collect_4h_klines.py
        ↓
    app/market_data/4h collector service
        ↓
    创建 collector_event_log
        status = running
        symbol = BTCUSDT
        interval = 4h
        data_source = binance_rest_by_scheduler
        collection_mode = incremental
        ↓
    Binance REST client
        ↓
    拉取最近若干根 4h K线
        例如最近 N 根，用于重叠校验、补漏和连续性检查
        ↓
    parser 转换为内部 Kline DTO
        ↓
    过滤未收盘 K线
        判断标准：
        Binance server time >= kline.close_time_ms + KLINE_CLOSE_SAFETY_DELAY_MS
        ↓
    查询数据库中相关 4h K线
        包括：
        1. 当前 REST 返回时间范围内已存在的 DB K线
        2. 当前批次开始前最近一根 DB K线
        ↓
    按 open_time_ms 合并 REST K线与 DB K线
        ↓
    校验重叠 K线是否一致
        如果同一 open_time_ms 同时存在于 REST 和 DB：
            - 核心字段一致：视为正常重叠，不重复写入
            - 核心字段不一致：标记异常，记录质量问题，不静默覆盖
        ↓
    校验受影响区间是否连续
        包括：
            - DB 前置 K线 与 REST 批次第一根 K线是否连续
            - REST 批次内部 K线是否连续
            - 本次写入后整体区间是否连续
        ↓
    校验字段合理性
        包括：
            - open_price > 0
            - high_price >= max(open_price, close_price)
            - low_price <= min(open_price, close_price)
            - high_price >= low_price
            - volume >= 0
            - quote_volume >= 0
            - trade_count >= 0
            - open_time_ms / close_time_ms 符合 4h 周期规则
        ↓
    计算允许写入集合
        只允许写入：
            - Binance REST 返回的已收盘 K线
            - 数据库中缺失的 K线
            - 通过字段合理性校验的 K线
            - 通过连续性检查或被明确标记为可补齐缺口的 K线

        不允许写入：
            - 未收盘 K线
            - 已存在且字段一致的重叠 K线
            - 与数据库已有 K线字段冲突的 K线
            - 来源不是 Binance REST 的 K线
            - 人工录入或人工修改的 K线
        ↓
    通过 Repository 幂等写入正式 4h K线表
        data_source 根据触发入口写入：
            - scheduler 触发：binance_rest_by_scheduler
            - CLI 手动触发：binance_rest_by_cli
        ↓
    写入 data_quality_check 或质量检查结果
        ↓
    根据执行结果更新 collector_event_log
        成功：
            status = success

        部分成功：
            status = partial_success
            例如部分 K线写入成功，但存在冲突或缺口需要人工确认

        阻断：
            status = blocked
            例如发现关键字段冲突、非法 data_source、连续性严重异常

        失败：
            status = failed
            例如 Binance REST 请求失败、数据库写入失败、解析失败
        ↓
    如存在异常，直接通过 Hermes 发送基础系统报警
        不调用 DeepSeek 或其他大模型
        ↓
    任务结束
```

增量采集规则：

1. 每次拉取最近若干根 4h K线，而不是只拉一根。
2. 拉取多根是为了降低网络波动导致漏 K 的风险。
3. 未收盘 K线不得写入正式 4h 表。
4. 数据库已有最新 K线时，必须检查新数据是否与库内最新 K线衔接。
5. 如果发现缺口，不得继续盲目写入。
6. 如果发现重复，必须通过唯一键和 upsert 保证幂等。
7. 如果采集失败或数据不连续，必须走异常数据流。
8. 增量采集不得调用大模型。
9. 增量采集不得生成交易建议。
10. 增量采集不得自动交易。

---

## 7. 4h K线采集失败数据流

采集失败包括但不限于：

1. Binance REST 请求失败。
2. Binance REST 超时。
3. Binance 返回异常状态码。
4. Binance 返回数据格式异常。
5. 网络错误。
6. parser 解析失败。
7. MySQL 写入失败。
8. Redis 状态异常。
9. 程序运行异常。

失败数据流如下：

    Binance REST / parser / storage 发生异常
        ↓
    collector service 捕获异常
        ↓
    写 collector_event_log
        ↓
    构造 AlertEvent
        ↓
    alerting 写 alert_message
        ↓
    HermesWebhookChannel 调用 Hermes Webhook
        ↓
    Hermes 发送微信提醒
        ↓
    Hermes 返回结果写入 channel_response
        ↓
    任务结束并标记失败

失败处理规则：

1. 失败不能静默忽略。
2. 失败不能只写日志。
3. 失败必须写 `collector_event_log`。
4. 失败必须写 `alert_message`。
5. 失败必须通过 Hermes 提醒用户。
6. 基础失败提醒不得调用大模型。
7. Hermes 发送失败时，也要记录发送失败结果。
8. 采集失败不得生成策略建议。
9. 采集失败不得自动重写历史 K线。
10. 采集失败不得自动交易。

如果 MySQL 不可用：
1. 不得假装已落库。
2. 必须写本地 emergency 日志。
3. 如 Redis 可用，可写短期 outbox / failure key。
4. 如 Hermes 可用，必须直接发送“数据库不可用”提醒。
5. MySQL 恢复后，可以由恢复任务补写故障摘要，但不得伪造原始发生时间。
---

## 8. 4h K线数据质量异常数据流

数据质量异常包括但不限于：

1. K线不连续。
2. K线重复。
3. K线 open_time 不符合 4h 间隔。
4. K线未收盘。
5. OHLC 价格关系异常。
6. 成交量异常。
7. 新 K线无法与数据库最新 K线衔接。
8. 时间戳异常。
9. 数据源异常。

数据质量异常流如下：

    collector service 获取 K线
        ↓
    parser 转换内部结构
        ↓
    data quality validator 校验失败
        ↓
    停止写入正式 market_kline_4h
        ↓
    写 data_quality_check
        ↓
    写 collector_event_log
        ↓
    构造 AlertEvent
        ↓
    alerting 写 alert_message
        ↓
    HermesWebhookChannel 调用 Hermes
        ↓
    Hermes 微信提醒
        ↓
    channel_response 记录 Hermes 返回
        ↓
    任务结束并标记失败

核心规则：

1. 数据质量异常时，正式 K线表不得写入异常批次。
2. 数据质量异常必须落库记录。
3. 数据质量异常必须 Hermes 提醒。
4. 数据质量异常不能只写日志。
5. 数据质量异常不能由大模型解释后再决定是否提醒。
6. 数据质量异常在未来策略阶段应阻断策略建议生成，或使最终建议降级为 `wait` / `stop_trading`。
7. 数据质量异常解决前，不应生成依赖异常数据的正式建议。

---

## 9. 10s 最新价格监控数据流

10s 最新价格监控与 4h K线采集是并列数据流。

它依赖 Redis 和 Hermes 提醒基础模块，但不依赖 4h K线采集结果。

它不得作为正式 4h K线数据源。

10s 最新价格监控用于基础价格波动提醒，不用于生成正式 4h K线。

数据流如下：
```text
    scheduler 每 10s 触发
        ↓
    WebSocket ticker client 或最新价格服务
        ↓
    获取 BTCUSDT 最新价格 current_price
        ↓
    读取 Redis 中上一轮价格 previous_price
        ↓
    如果 previous_price 存在，计算价格变化幅度
        ↓
    判断是否超过提醒阈值
        ↓
    判断是否处于冷却期
        ↓
    未冷却且达到阈值
        ↓
    构造 AlertEvent
        ↓
    写 alert_message
        ↓
    Hermes 微信提醒
        ↓
    写 channel_response
        ↓
    无论是否提醒，都把 current_price 写入 Redis
        key = bitcoin_price
        TTL = 2 分钟
        ↓
    任务结束
```

价格监控规则：

1. Redis 中 `bitcoin_price` 只保存最新价格。
2. Redis 最新价格用于短期状态，不用于长期行情分析。
3. 每次获取最新价格后应刷新 Redis TTL。
4. 价格波动提醒必须有阈值。
5. 价格波动提醒必须有冷却机制。
6. 不能每 10s 无脑提醒。
7. 价格监控不得拼接正式 4h K线。
8. 价格监控不得生成策略建议。
9. 价格监控不得调用大模型。
10. 价格监控不得自动交易。

未来进入建议生命周期阶段后，10s 价格监控可以用于提醒用户价格接近关键位，例如：

1. 接近入场区。
2. 接近止盈区。
3. 接近减仓区。
4. 接近失效条件。
5. 接近成本线。
6. 接近保护利润位置。

但即使如此，10s 价格监控仍然只负责事件提醒，不负责自动交易。

---

## 10. Hermes 基础提醒数据流

基础提醒统一通过 Hermes 发送。

数据流如下：

    业务模块产生 AlertEvent
        ↓
    alerting dispatcher
        ↓
    写 alert_message
        ↓
    HermesWebhookChannel
        ↓
    HTTP Webhook 请求 Hermes
        ↓
    Hermes direct delivery
        ↓
    微信消息
        ↓
    Hermes 返回响应
        ↓
    更新 alert_message.channel_response

提醒类型包括：

1. 采集失败。
2. 数据质量异常。
3. K线不连续。
4. Binance 接口异常。
5. MySQL 异常。
6. Redis 异常。
7. Hermes 发送异常。
8. 价格波动提醒。
9. 未来价格接近关键位提醒。
10. 未来策略建议提醒。
11. 未来复盘提醒。

基础提醒规则：

1. 基础系统提醒必须由代码模板直接生成。
2. 基础系统提醒不得调用大模型。
3. 提醒必须落库。
4. Hermes 返回结果必须记录。
5. 提醒应有去重和冷却。
6. 同一异常不得无限刷屏。
7. 微信消息中的时间必须明确时区。
8. 用户消息中推荐同时展示北京时间和 UTC。

---

## 11. 时间展示数据流

系统内部时间统一使用 UTC。

内部数据流：

    Binance UTC 时间
        ↓
    parser 保留 UTC
        ↓
    MySQL 业务字段保存 UTC
        ↓
    连续性检查使用 UTC
        ↓
    策略编号使用 UTC
        ↓
    建议生命周期使用 UTC
        ↓
    复盘到期时间使用 UTC

用户展示数据流：

    UTC 时间
        ↓
    app/core/time_utils 统一转换
        ↓
    PRC / 北京时间展示
        ↓
    Hermes 微信消息 / Admin 展示 / 人工复盘说明

用户消息时间规则：

1. 面向用户的消息中不得出现无时区标识的时间。
2. 推荐同时展示北京时间和 UTC。
3. 不得单独使用 `CST` 表示北京时间。
4. 推荐使用 `北京时间（UTC+8）`。
5. 策略编号中的小时必须说明使用 UTC。

示例：

    建议编号：20260506-BTCUSDT-04
    说明：04 表示 UTC 04:00，对应北京时间 12:00。

---

## 12. 1m、4h、1d K线复用数据流

当前阶段优先实现 4h K线。

但 4h REST 采集能力设计时，应预留 1m 和 1d 的复用空间。

通用 K线 REST 数据流：

    调用方传入 symbol、interval、start_time、end_time、limit
        ↓
    Binance REST client
        ↓
    Binance Kline API
        ↓
    parser
        ↓
    closed kline filter
        ↓
    basic validator
        ↓
    interval continuity validator
        ↓
    周期专用 service
        ↓
    周期专用 repository
        ↓
    周期专用 MySQL 表

复用原则：

1. REST 请求能力应复用。
2. parser 应复用。
3. 已收盘过滤应复用。
4. 基础字段校验应复用。
5. 连续性检查应基于 interval 参数复用。
6. 不同周期的入库表应保持边界。
7. 不同周期的 Repository 应保持边界。
8. 不同周期的业务用途应保持边界。

周期用途：

1. 4h 是主策略周期。
2. 1m 后续用于复盘价格路径、插针、快速波动和细节行情。
3. 1d 后续用于大级别市场环境判断。

禁止：

1. 为 4h 写死一套无法扩展到 1m、1d 的 REST 逻辑。
2. 把 1m、4h、1d 的入库逻辑混成一个不可拆分大函数。
3. 依赖自增 id 关联不同周期 K线。
4. 当前阶段为了预留 1m、1d 而扩大开发范围。

---

## 13. 未来策略信号数据流

策略信号层不属于当前第一阶段。

未来数据流如下：

    4h K线数据
        ↓
    未来可选 1m / 1d / mark price / funding / open interest 数据
        ↓
    数据质量状态检查
        ↓
    市场环境快照
        ↓
    各独立策略运行
        ↓
    GannStrategy 输出信号
        ↓
    TrendStrategy 输出信号
        ↓
    SupportResistanceStrategy 输出信号
        ↓
    VolatilityRiskStrategy 输出信号
        ↓
    LiquidationPressureStrategy 输出信号
        ↓
    LeastResistanceStrategy 输出信号
        ↓
    保存 strategy_signal
        ↓
    进入策略聚合层

策略信号规则：

1. 每个策略独立运行。
2. 每个策略独立输出信号。
3. 每个策略信号必须保存。
4. 策略信号不得直接发送最终操作建议。
5. 策略信号不得直接调用 Hermes。
6. 策略信号不得直接调用大模型。
7. 策略信号不得自动交易。
8. 策略必须记录策略版本和参数版本。
9. 策略必须记录使用的 K线范围。
10. 策略必须记录关键理由、目标区域和失效条件。

---

## 14. 未来策略聚合与大模型分析数据流

未来策略聚合与大模型分析数据流如下：

    多个 strategy_signal
        ↓
    strategy aggregation
        ↓
    策略一致性分析
        ↓
    策略分歧分析
        ↓
    初步方向候选
        ↓
    风控过滤
        ↓
    结构化上下文构造
        ↓
    DeepSeek / OpenAI / Grok 等大模型分析
        ↓
    保存 llm_advice_candidate
        ↓
    多模型结果对比
        ↓
    最终风控硬校验
        ↓
    生成 final advice candidate
        ↓
    进入 advice lifecycle

大模型输入应包括：

1. 最新行情摘要。
2. 数据质量状态。
3. 各策略独立信号。
4. 策略聚合层摘要。
5. 当前 active 建议状态。
6. 关键价位。
7. 风险边界。
8. 历史建议上下文。
9. 复盘相关上下文。

大模型输出不是最终交易指令。

最终建议必须经过：

1. 策略聚合。
2. 大模型分析。
3. 风控硬校验。
4. 建议生命周期处理。

基础告警不得进入大模型分析流。

---

## 15. 未来建议生命周期数据流

建议生命周期层不属于当前第一阶段。

未来每 4h 主评估数据流如下：

    4h 已收盘 K线完成采集
        ↓
    数据质量检查通过
        ↓
    查询当前 active 建议链
        ↓
    运行策略信号
        ↓
    策略聚合
        ↓
    大模型综合分析
        ↓
    最终风控校验
        ↓
    判断当前周期动作

当前周期动作可能是：

1. 延续当前建议。
2. 更新当前建议，生成新版本。
3. 完成当前建议。
4. 判定当前建议失效。
5. 判定当前建议过期。
6. 关闭当前建议。
7. 创建新建议链。
8. 建议 `wait`。
9. 建议 `stop_trading`。
10. 将结束建议放入复盘队列。
11. 执行到期复盘。

建议版本链数据流：

    A-v1
        ↓ 被新版本替代
    A-v1 status = superseded
        ↓
    A-v2
        ↓ 被新版本替代
    A-v2 status = superseded
        ↓
    A-v3
        ↓ 完成或失效
    A-v3 status = completed / invalidated

规则：

1. 前序版本被替代时状态为 `superseded`。
2. 不得把前序版本错误改为 `completed`。
3. 整条建议链通过 `root_id`、`parent_id`、`path`、`version_no` 关联。
4. 建议编号使用 UTC。
5. 同一 BTCUSDT + 4h 早期只保留一条主 active 建议链。
6. 每次最终建议仍必须保留其他策略和模型的重点信息。
7. 最终建议可以是 `long`、`short`、`wait` 或 `stop_trading`。
8. `wait` 和 `stop_trading` 也是有效策略结果。

---

## 16. 未来复盘评估数据流

复盘评估层不属于当前第一阶段。

未来复盘数据流如下：

    建议链 completed / invalidated / expired / closed
        ↓
    生成 review_due_at_utc
        ↓
    放入复盘队列
        ↓
    等待约 1 天或 6 根 4h K线
        ↓
    4h 主调度发现复盘到期
        ↓
    读取建议链和版本历史
        ↓
    读取建议后的行情数据
        ↓
    计算最大有利波动
        ↓
    计算最大不利波动
        ↓
    计算 R 倍数
        ↓
    判断是否先触及目标
        ↓
    判断是否先触发失效
        ↓
    评估策略信号
        ↓
    评估聚合层
        ↓
    评估大模型候选
        ↓
    评估最终建议
        ↓
    评估人工执行偏离
        ↓
    写 review 记录
        ↓
    写 alert_message
        ↓
    Hermes 发送复盘提醒

复盘规则：

1. 建议结束后不立即复盘。
2. 默认等待约 1 天或 6 根 4h K线。
3. 10s 价格监控不得触发正式复盘。
4. 每条建议链只允许发送一次到期复盘提醒。
5. 复盘只能追加记录。
6. 复盘不得篡改原始策略信号。
7. 复盘不得篡改原始大模型输出。
8. 复盘不得覆盖原始建议。
9. 复盘不得自动修改策略配置。
10. 复盘不得自动调整策略权重。

---

## 17. 未来人工执行反馈数据流

人工执行反馈层不属于当前第一阶段。

未来人工执行数据流如下：

    用户收到策略建议
        ↓
    用户手动决定是否交易
        ↓
    用户通过微信 / Admin / 命令录入执行反馈
        ↓
    系统校验反馈格式
        ↓
    写 manual_execution
        ↓
    关联 strategy_advice
        ↓
    复盘时读取人工执行记录
        ↓
    区分策略质量和人工执行质量

人工执行记录可以包括：

1. 是否执行。
2. 执行方向。
3. 执行价格。
4. 执行仓位。
5. 杠杆倍数。
6. 执行时间。
7. 是否按建议执行。
8. 偏离原因。
9. 减仓记录。
10. 平仓记录。
11. 用户备注。

规则：

1. 系统不自动读取账户。
2. 系统不自动同步持仓。
3. 系统不自动下单。
4. 系统不自动平仓。
5. 系统不自动调仓。
6. 人工执行记录只用于复盘。
7. 用户正常严格执行策略时，杠杆原则上不超过 5 倍。
8. 杠杆超过 5 倍时，可在复盘中标记人工执行风险。
9. 系统不得基于杠杆自动执行任何账户操作。

---

## 18. 数据质量异常对未来策略的阻断数据流

未来进入策略层后，数据质量状态必须进入策略决策链路。

数据流如下：

    4h K线采集完成
        ↓
    data_quality_check
        ↓
    数据质量状态 = passed / failed / warning
        ↓
    策略主评估读取数据质量状态
        ↓
    如果 passed
        ↓
    允许策略分析
        ↓
    如果 failed
        ↓
    阻断策略建议生成
        ↓
    或最终建议降级为 wait / stop_trading
        ↓
    Hermes 告知用户原因

规则：

1. 数据质量异常不能被策略层忽略。
2. 数据质量异常不能交给大模型自由判断是否重要。
3. 数据质量异常应作为风控硬条件。
4. 如果数据质量不可靠，不能给出强操作建议。
5. 如果因为数据质量导致 `wait` 或 `stop_trading`，必须明确告诉用户原因。

---

## 19. MySQL 与 Redis 数据流边界

MySQL 数据流：

    需要长期保存的数据
        ↓
    MySQL

包括：

1. K线数据。
2. 采集事件。
3. 数据质量检查。
4. 提醒消息。
5. 未来策略信号。
6. 未来建议。
7. 未来大模型输入输出。
8. 未来复盘结果。
9. 未来人工执行记录。

Redis 数据流：

    短期状态 / 缓存 / 冷却
        ↓
    Redis
        ↓
    TTL 过期

包括：

1. 最新价格。
2. 提醒冷却状态。
3. 临时幂等状态。
4. 任务短期状态。

禁止：

1. Redis 替代 MySQL 保存长期行情。
2. Redis 替代 MySQL 保存策略建议。
3. Redis 替代 MySQL 保存复盘结果。
4. Redis 作为一年级别回测数据源。

---

## 20. 幂等数据流

所有采集和提醒任务都必须考虑重复执行。

4h K线幂等数据流：

    重复执行回补或增量采集
        ↓
    解析出相同 K线
        ↓
    Repository 根据唯一键 upsert
        ↓
    不产生重复 K线

唯一键原则：

1. `exchange`
2. `market_type`
3. `symbol`
4. `interval`
5. `open_time_ms`

或在周期专用表中使用等价唯一约束。

提醒幂等数据流：

    同一异常重复出现
        ↓
    alerting 检查冷却 / 去重状态
        ↓
    未冷却则不重复刷屏
        ↓
    冷却结束后可再次提醒

规则：

1. 幂等不能只依赖内存变量。
2. 数据库唯一约束必须兜底。
3. Redis 可以辅助冷却。
4. 重复执行任务不能产生错误数据。
5. 同一复盘提醒不能重复发送。

---

## 21. 禁止的数据流

Codex 不得实现以下数据流：

1. WebSocket 最新价格 → 拼接 4h K线 → 写正式 4h K线表。
2. Binance client → 直接写 MySQL。
3. Binance client → 直接调用 Hermes。
4. Repository → 调用 Hermes。
5. Repository → 调用 DeepSeek。
6. 基础告警 → 调用 DeepSeek 解释异常。
7. 数据采集失败 → 静默写日志 → 不提醒用户。
8. K线不连续 → 继续写正式 K线表。
9. K线顺序判断 → 使用数据库自增 id。
10. Redis 最新价格 → 长期策略回测。
11. 策略信号 → 直接发送最终建议。
12. 大模型输出 → 直接成为最终交易指令。
13. 复盘结果 → 自动修改策略配置。
14. 人工执行记录 → 自动触发下单或平仓。
15. Admin 操作 → 绕过生命周期直接执行交易。
16. 任何模块 → 自动下单。
17. 任何模块 → 自动平仓。
18. 任何模块 → 自动调仓。
19. 任何模块 → 自动读取账户后执行操作。

---

## 22. 当前阶段最小数据闭环

当前阶段必须先完成两个最小闭环。

### 21.1 4h K线采集闭环

    Binance REST
        ↓
    4h 已收盘 K线
        ↓
    parser
        ↓
    validator
        ↓
    market_kline_4h
        ↓
    collector_event_log / data_quality_check
        ↓
    异常时 alert_message
        ↓
    Hermes 微信提醒

### 21.2 10s 价格提醒闭环

    Binance WebSocket / ticker
        ↓
    最新价格
        ↓
    Redis bitcoin_price
        ↓
    价格变化判断
        ↓
    冷却判断
        ↓
    alert_message
        ↓
    Hermes 微信提醒

这两个闭环稳定后，才进入后续策略层开发。

---

## 23. Codex 开发约束

Codex 实现数据流相关功能时，必须遵守：

1. 先读 `AGENTS.md`。
2. 再读相关 requirements。
3. 再读 `system_architecture.md`。
4. 再读 `module_boundaries.md`。
5. 再读本文档。
6. 最后读当前 plan。
7. 不得擅自扩大当前阶段范围。
8. 不得把未来策略数据流混进当前采集代码。
9. 不得让基础告警调用大模型。
10. 不得绕过 Hermes 发送微信。
11. 不得跳过数据质量检查。
12. 不得在数据质量失败时写正式 K线表。
13. 不得用 WebSocket 拼接正式 4h K线。
14. 不得用 Redis 作为长期行情库。
15. 不得用数据库 id 判断行情顺序。
16. 不得实现自动交易。
17. 不得提交 `.env`、密钥、日志文件或本地缓存文件。
18. 每完成一个业务模块，必须在 `docs/implementation/` 中说明该模块实际数据流和调用链路。

## 24. 手动 CLI 触发 K线 REST 回补数据流

手动 CLI 回补用于用户发现某段 4h K线缺失、采集失败或复核异常后，手动执行命令触发 Binance REST 重新拉取官方已收盘 K线。

手动 CLI 回补不是人工修复。用户只能输入交易对、周期、起止时间等查询参数，不允许输入价格、成交量、成交额等行情字段。

流程：

1. 用户执行 CLI 回补命令。

2. 进入命令入口：

   - `scripts/backfill_4h_klines.py`

3. CLI 入口解析参数，包括：

   - `symbol`
   - `interval`
   - `start_time`
   - `end_time`
   - `limit`
   - `batch_size`

4. CLI 入口调用 `app/market_data/4h backfill service`。

   注意：

   - CLI 不得直接请求 Binance。
   - CLI 不得直接写数据库。
   - CLI 不得直接拼接 SQL。
   - CLI 不得接受任何人工输入的 OHLCV 行情字段。

5. 创建 `collector_event_log`，状态为 `running`。

   建议记录：

   - `status = running`
   - `symbol = BTCUSDT`
   - `interval = 4h`
   - `data_source = binance_rest_by_cli`
   - `collection_mode = manual_backfill`
   - `triggered_by = cli`
   - `started_at_utc`
   - `started_at_prc`

6. 调用 Binance REST Client 拉取官方已收盘 K线。

7. parser 转换为内部 Kline DTO。

8. 过滤未收盘 K线。

   判断标准：

   - `Binance server time >= kline.close_time_ms + KLINE_CLOSE_SAFETY_DELAY_MS`

9. 执行数据质量检查，包括：

   - 时间字段是否合法。
   - K线周期是否为 4h。
   - 同批次 K线是否连续。
   - OHLC 字段是否合理。
   - 成交量、成交额、成交笔数是否非负。
   - K线是否为 Binance REST 返回的官方已收盘 K线。

10. 查询数据库中目标范围已有 K线。

11. 按 `open_time_ms` 对齐 REST K线与 DB K线。

12. 计算允许写入集合。

    允许写入：

    - DB 缺失，REST 存在，且 REST K线已收盘并通过校验。

    跳过写入：

    - DB 已存在，REST 也存在，核心字段一致。

    阻断写入：

    - DB 已存在，REST 也存在，但核心字段不一致。
    - REST 未返回但 DB 存在异常 K线。
    - REST 返回未收盘 K线试图写入。
    - 来源不是 Binance REST。
    - 存在人工录入或人工修改迹象。

13. 通过 Repository 幂等写入正式 4h K线表。

    写入时：

    - `data_source = binance_rest_by_cli`

14. 写入 `data_quality_check` 或质量检查结果。

15. 根据执行结果更新 `collector_event_log`。

    全部成功时：

    - `status = success`

    部分成功时：

    - `status = partial_success`

    发现关键冲突或严重质量问题时：

    - `status = blocked`

    任务执行失败时：

    - `status = failed`

    建议同时记录：

    - `finished_at_utc`
    - `finished_at_prc`
    - `fetched_count`
    - `inserted_count`
    - `skipped_count`
    - `conflict_count`
    - `error_message`

16. 如果出现异常，直接通过 Hermes 发送基础系统报警。

    必须报警的情况包括：

    - Binance REST 请求失败。
    - MySQL 写入失败。
    - 数据库已有 K线与 Binance REST 返回值冲突。
    - 目标范围内仍存在缺口。
    - 出现非法 `data_source`。
    - 出现未收盘 K线试图写入正式表。
    - 回补任务本身失败。

17. Hermes 报警必须使用固定模板，不允许调用 DeepSeek 或其他大模型。

18. 任务结束。

手动 CLI 回补的禁止事项：

1. 禁止 CLI 参数中接受 `open_price`、`high_price`、`low_price`、`close_price`。
2. 禁止 CLI 参数中接受 `volume`、`quote_volume`、`trade_count`。
3. 禁止 CLI 直接修改正式 K线表。
4. 禁止 CLI 直接拼接 SQL。
5. 禁止使用 `manual_repair`、`system_repair`、`human_edit`、`manual_input` 作为 `data_source`。
6. 禁止将 WebSocket 数据写入正式 4h K线表。
7. 禁止静默覆盖与 Binance REST 返回值冲突的已有 K线。
8. 禁止把手动回补理解为人工修复。

---

## 25. K线一致性复核数据流

K线一致性复核用于检查过去已入库 K线是否存在数据错误、缺失、不连续、未收盘误写入、非法 `data_source` 等问题。

复核任务不是回补任务，不是修复任务，不写正式 K线表。

复核任务分为两类：

1. scheduler 每日自动复核。
2. CLI 手动指定范围复核。

### 25.1 每日自动复核数据流

每日自动复核用于每天定时检查最近 100 根已收盘 4h K线，确认数据库中的正式 K线是否与 Binance REST 官方 K线一致。

流程：

1. Scheduler 每日触发复核任务。

2. 计算最近 100 根已收盘 4h K线范围。

3. 创建复核任务记录。

   建议记录：

   - `check_mode = daily_integrity_check`
   - `check_trigger = scheduler`
   - `compare_source = binance_rest`
   - `lookback_count = 100`
   - `symbol = BTCUSDT`
   - `interval = 4h`
   - `started_at_utc`
   - `started_at_prc`

4. 调用 Binance REST Client 拉取对应范围官方已收盘 K线。

5. 查询数据库中相同范围的正式 4h K线。

6. 按 `open_time_ms` 对齐 REST K线与 DB K线。

7. 比较时间连续性与核心字段一致性。

8. 检查是否存在以下异常：

   - 数据库缺失 K线。
   - 数据库 K线字段与 Binance REST 不一致。
   - K线时间不连续。
   - 未收盘 K线被写入正式表。
   - 非法 `data_source`。
   - 数据库存在 Binance REST 未返回的异常 K线。
   - Binance REST 无法访问导致无法完成复核。
   - MySQL 或 Redis 异常导致复核结果无法完整记录。

9. 生成复核结果。

10. 写入 `data_quality_check`，或者后续独立的 `kline_integrity_check_log`。

11. 如果存在异常，直接通过 Hermes 发送基础系统报警。

12. 更新复核任务记录。

    无异常时：

    - `status = success`
    - `issue_count = 0`

    有异常并已报警时：

    - `status = failed`
    - `issue_count > 0`
    - `alert_sent = true`

    复核任务自身执行失败时：

    - `status = failed`
    - `error_message = 失败原因`

13. 任务结束。

### 25.2 CLI 手动复核数据流

CLI 手动复核用于用户主动检查指定时间范围内的数据库 K线是否与 Binance REST 官方 K线一致。

手动复核只检查，不修复，不回补，不覆盖正式 K线表。

流程：

1. 用户执行 CLI 复核命令。

2. 进入命令入口：

   - `scripts/check_kline_integrity.py`

3. CLI 入口解析参数，包括：

   - `symbol`
   - `interval`
   - `start_time`
   - `end_time`
   - `limit`

4. CLI 入口调用 `app/market_data/kline integrity check service`。

   注意：

   - CLI 不得直接请求 Binance。
   - CLI 不得直接写数据库。
   - CLI 不得直接拼接 SQL。
   - CLI 不得接受任何人工输入的 OHLCV 行情字段。

5. 创建复核任务记录。

   建议记录：

   - `check_mode = manual_integrity_check`
   - `check_trigger = cli`
   - `compare_source = binance_rest`
   - `symbol = BTCUSDT`
   - `interval = 4h`
   - `started_at_utc`
   - `started_at_prc`

6. 调用 Binance REST Client 拉取对应范围官方已收盘 K线。

7. 查询数据库中相同范围的正式 4h K线。

8. 按 `open_time_ms` 对齐比较。

9. 检查字段一致性、时间连续性、缺失、重复、未收盘误写入和非法 `data_source`。

10. 生成复核结果。

11. 写入 `data_quality_check`，或者后续独立的 `kline_integrity_check_log`。

12. 如果存在异常，直接通过 Hermes 发送报警。

13. 更新复核任务记录。

14. 任务结束。

### 25.3 复核任务禁止事项

复核任务禁止：

1. 禁止写入正式 4h K线表。
2. 禁止自动回补缺失 K线。
3. 禁止自动覆盖已有 K线。
4. 禁止自动修复字段不一致的 K线。
5. 禁止把复核任务伪装成采集任务。
6. 禁止使用 `collection_mode = recheck`。
7. 禁止调用 DeepSeek 或其他大模型生成报警内容。
8. 禁止静默吞掉复核异常。
9. 禁止复核报警触发自动修复流程。

### 25.4 复核报警目的

复核任务发现异常后，报警目的只是提醒用户检查：

1. Binance REST 访问状态。
2. 采集代码。
3. 调度器。
4. 数据库写入逻辑。
5. K线连续性检查逻辑。
6. 未收盘 K线过滤逻辑。
7. 数据库唯一键与幂等写入逻辑。
8. Hermes 报警链路。

复核任务本身不负责修复数据。是否需要执行手动 CLI 回补，应由用户看到报警后人工决定。