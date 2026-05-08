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

## 5. 4h K线历史回补成功数据流

4h 历史回补用于补齐指定历史时间范围内的官方已收盘 K线。

数据流如下：

    用户或部署脚本触发回补任务
        ↓
    scripts/backfill_4h_klines.py
        ↓
    app/market_data/backfill service
        ↓
    Binance REST client
        ↓
    Binance /fapi/v1/klines
        ↓
    Binance 原始 K线数组
        ↓
    parser 转换为内部 Kline DTO
        ↓
    过滤未收盘 K线
        ↓
    校验本批次 K线连续性
        ↓
    校验 K线字段合理性
        ↓
    查询 MySQL 中目标时间范围已有数据
        ↓
    判断是否需要 insert / upsert
        ↓
    Repository 写入 market_kline_4h
        ↓
    写 collector_event_log
        ↓
    任务结束

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

历史回补的数据来源必须是 Binance REST。

禁止用 WebSocket 数据补正式 4h 主 K线。

---

## 6. 4h K线增量采集成功数据流

4h 增量采集用于周期性获取最新已收盘 4h K线。

数据流如下：

    scheduler 或 scripts/collect_4h_klines.py
        ↓
    app/market_data/4h collector service
        ↓
    Binance REST client
        ↓
    拉取最近若干根 4h K线
        ↓
    parser 转换为内部 Kline DTO
        ↓
    过滤未收盘 K线
        ↓
    获取数据库中最新一根 4h K线
        ↓
    校验新 K线与数据库最新 K线是否连续
        ↓
    校验本批次 K线是否连续
        ↓
    校验字段合理性
        ↓
    校验通过
        ↓
    Repository upsert market_kline_4h
        ↓
    写 collector_event_log
        ↓
    任务结束

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

    scheduler 每 10s 触发
        ↓
    获取 BTCUSDT 最新价格 current_price
        ↓
    读取 Redis 中上一轮价格 previous_price
        ↓
    如果 previous_price 存在，计算变化幅度
        ↓
    判断是否超过阈值、是否处于冷却期
        ↓
    必要时构造 AlertEvent 并通过 Hermes 提醒
        ↓
    无论是否提醒，都把 current_price 写入 Redis
        ↓
    刷新 TTL，例如 2 分钟

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