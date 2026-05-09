# module_boundaries.md

# 模块边界设计

## 1. 文档目的

本文档定义 Hermes + DeepSeek BTC 合约策略辅助系统的模块边界。

本文档回答以下问题：

1. 每个模块负责什么。
2. 每个模块不负责什么。
3. 模块之间允许如何调用。
4. 模块之间禁止如何调用。
5. Codex 写代码时应如何拆分文件、类、函数。
6. 如何避免把数据采集、策略、大模型、提醒、数据库逻辑混在一起。

本文档不负责：

1. 具体开发步骤。
2. 具体数据库字段。
3. 具体策略算法。
4. 具体大模型 prompt。
5. 具体部署命令。

具体需求见 `docs/requirements/`。

总体架构见 `docs/architecture/system_architecture.md`。

具体数据流见 `docs/architecture/data_flow.md`。

具体开发计划见 `docs/plans/`。

---

## 2. 总体边界原则

系统必须遵守分层设计。

核心原则：

1. 数据接入层只负责访问外部数据源。
2. 数据采集层只负责编排采集流程。
3. 数据质量层只负责判断数据是否可信。
4. 存储层只负责数据读写。
5. 提醒层只负责构造、记录和发送提醒。
6. 策略层只负责输出独立策略信号。
7. 策略聚合层只负责综合多个策略信号。
8. 大模型层只负责基于结构化输入进行分析。
9. 建议生命周期层只负责管理建议状态和版本链。
10. 复盘评估层只负责追加复盘结果。
11. 脚本入口只负责编排，不写大量核心业务逻辑。
12. 数据库 Repository 只负责数据访问，不写策略判断。
13. 基础告警不得调用大模型。
14. 当前阶段不得实现自动交易。

禁止出现“大杂烩模块”。

禁止把以下逻辑混在同一个函数里：

1. 请求 Binance。
2. 判断 K线质量。
3. 写 MySQL。
4. 写 Redis。
5. 构造微信提醒。
6. 调用 Hermes。
7. 调用 DeepSeek。
8. 生成策略建议。
9. 修改建议生命周期。
10. 执行复盘。

每个模块必须职责清晰，方便后续审查、测试、替换和扩展。

---

## 3. 推荐目录边界

项目推荐目录结构如下：

    app/
      core/
      exchange/
        binance/
      storage/
        mysql/
        redis/
      market_data/
      alerting/
      scheduler/
      monitoring/

      strategy/        # 后期新增
      aggregation/     # 后期新增
      llm/             # 后期新增
      advice/          # 后期新增
      review/          # 后期新增
      admin/           # 后期新增

    configs/
    migrations/
    scripts/
    tests/
    docs/
      requirements/
      architecture/
      decisions/
      plans/
      implementation/

如果当前阶段某些目录尚未创建，Codex 不应为了“完整架构”提前创建所有未来目录。

当前阶段只创建当前 plan 需要的目录。

未来目录应在进入对应开发阶段时再创建。

---

## 4. `app/core` 边界

`app/core` 是系统基础能力层。

负责：

1. 配置读取。
2. 日志初始化。
3. 异常基类。
4. 时间工具。
5. 通用常量。
6. 通用类型。
7. 安全辅助函数。
8. 重试、限流等通用基础能力。

不负责：

1. 请求 Binance 业务接口。
2. 写 MySQL 业务表。
3. 写 Redis 业务 key。
4. 生成 K线。
5. 生成策略信号。
6. 发送 Hermes 提醒。
7. 调用 DeepSeek。
8. 编写具体业务流程。

允许被以下模块调用：

1. 所有业务模块。
2. 所有脚本入口。
3. 所有测试代码。

`app/core` 不应反向依赖业务模块。

禁止：

1. 在 `app/core` 中导入 `market_data`。
2. 在 `app/core` 中导入 `strategy`。
3. 在 `app/core` 中导入 `llm`。
4. 在 `app/core` 中导入 `advice`。
5. 在 `app/core` 中导入 `alerting` 的具体业务发送逻辑。

时间规则：

1. UTC 是业务判断标准。
2. PRC 只用于展示。
3. UTC 与 PRC 转换必须通过统一时间工具。
4. 不允许在业务代码里到处手写 `+8 小时`。

### 用户消息中的时间展示规则

系统内部业务判断统一使用 UTC。

但所有面向用户的消息、Hermes 微信提醒、Admin 展示、人工复盘说明中，如果出现时间，必须明确标注时区。

用户消息中推荐同时展示北京时间和 UTC。

推荐格式：

1. 北京时间：2026-05-06 12:00:00
2. UTC：2026-05-06 04:00:00

用户消息中不得只写没有时区标识的时间，例如：

1. 04:00
2. 12:00
3. 2026-05-06 04:00

策略建议编号、K线周期编号、数据库业务判断仍以 UTC 为准。

例如：

`20260506-BTCUSDT-04` 中的 `04` 表示 UTC 04:00，对应北京时间 12:00。

如果消息中展示该编号，必须说明编号时间使用 UTC。

禁止在用户消息中单独使用 `CST` 表示北京时间，因为 `CST` 存在歧义。

推荐使用：

1. 北京时间
2. UTC+8
3. PRC

基础规则：

1. 内部计算、排序、连续性判断、建议编号使用 UTC。
2. 用户阅读、微信提醒、Admin 展示优先显示北京时间。
3. 用户消息中如涉及 K线、建议、复盘、异常时间，应同时展示北京时间和 UTC。

---

## 5. `app/exchange/binance` 边界

`app/exchange/binance` 是 Binance 交易所接入层。

负责：

1. Binance U 本位合约 REST 请求。
2. Binance U 本位合约 WebSocket 最新价格连接。
3. Binance API 参数构造。
4. Binance 响应基础校验。
5. Binance 错误包装。
6. 请求重试。
7. 请求超时控制。
8. Binance 原始数据解析为内部基础结构。

不负责：

1. 写 MySQL。
2. 写 Redis。
3. 判断数据库中 K线是否连续。
4. 发送 Hermes 提醒。
5. 生成策略信号。
6. 调用大模型。
7. 生成最终操作建议。
8. 管理建议生命周期。
9. 执行复盘。

REST 客户端可以提供：

1. `get_server_time`
2. `get_klines`
3. `get_mark_price_klines`
4. `get_premium_index_klines`
5. 后续需要的资金费率、持仓量等接口封装。

WebSocket 客户端可以提供：

1. 最新价格订阅。
2. ticker 数据接收。
3. 连接状态管理。
4. 断线重连。
5. 原始价格事件输出。

核心限制：

1. 4h 主 K线必须来自 Binance REST 已收盘 K线。
2. WebSocket 不允许作为 4h 主 K线最终数据源。
3. WebSocket 不允许自己拼接正式 4h K线。
4. WebSocket 只用于最新价格监控和价格事件提醒。
5. Binance 接入层不得知道策略规则。

如果 Binance 请求失败：

1. Binance 接入层只负责抛出明确异常。
2. 是否记录 `collector_event_log` 由上层采集模块决定。
3. 是否发送 Hermes 提醒由上层提醒流程决定。

---

## 6. `app/storage/mysql` 边界

`app/storage/mysql` 是 MySQL 存储层。

负责：

1. SQLAlchemy ORM 模型。
2. 数据库连接。
3. Session 管理。
4. Repository 数据访问。
5. MySQL insert。
6. MySQL update。
7. MySQL upsert。
8. MySQL query。
9. 数据库事务边界辅助。
10. Alembic 迁移相关模型支持。

不负责：

1. 请求 Binance。
2. 判断某个策略是否看多或看空。
3. 判断是否应该开仓。
4. 判断是否应该平仓。
5. 调用 Hermes。
6. 调用 DeepSeek。
7. 调用 OpenAI、Grok 或其他大模型。
8. 直接构造微信消息文本。
9. 根据数据库结果直接产生最终操作建议。
10. 根据账户状态执行操作。

Repository 规则：

1. Repository 只处理数据读写。
2. Repository 可以做必要的数据唯一性处理。
3. Repository 可以做 upsert。
4. Repository 可以封装常见查询。
5. Repository 不做跨模块业务决策。

例如：

允许：

1. 根据 `symbol + interval + open_time_ms` 查询 K线。
2. 批量 upsert K线。
3. 查询数据库最新一根 4h K线。
4. 写入采集事件日志。
5. 写入数据质量检查结果。
6. 写入提醒消息记录。

禁止：

1. 在 Repository 里判断是否要 `long`。
2. 在 Repository 里判断是否要 `short`。
3. 在 Repository 里判断是否要 `wait`。
4. 在 Repository 里调用 Hermes 发送消息。
5. 在 Repository 里调用大模型。
6. 在 Repository 里进行策略复盘。

数据库排序规则：

1. 行情顺序必须基于 `open_time_ms` 或 `open_time_utc`。
2. 禁止依赖数据库自增 id 判断行情顺序。
3. 策略、复盘、质量检查都不得使用 id 顺序代表 K线时间顺序。

---

## 7. `app/storage/redis` 边界

`app/storage/redis` 是 Redis 短期状态层。

负责：

1. Redis 连接。
2. Redis key 命名管理。
3. 最新价格缓存。
4. TTL 设置。
5. 提醒冷却状态。
6. 短期幂等状态。
7. 临时任务状态。

不负责：

1. 保存长期行情历史。
2. 保存长期策略结果。
3. 保存长期复盘数据。
4. 替代 MySQL。
5. 生成策略建议。
6. 调用 Hermes。
7. 调用大模型。

Redis 使用原则：

1. Redis 数据允许过期。
2. Redis 不作为长期分析数据源。
3. 后续一年级别回测、复盘、策略评估必须依赖 MySQL。
4. Redis 只保存短期状态和缓存。

例如：

允许：

1. 保存 `bitcoin_price`。
2. 设置 2 分钟 TTL。
3. 保存价格提醒冷却状态。
4. 保存某类 alert 最近发送时间。

禁止：

1. 只把 4h K线放 Redis。
2. 只把策略建议放 Redis。
3. 只把复盘结果放 Redis。
4. 依赖 Redis 做长期策略统计。

---

## 8. `app/market_data` 边界

`app/market_data` 是行情采集与行情质量业务层。

负责：

1. 4h K线历史回补。
2. 4h K线增量采集。
3. 已收盘 K线过滤。
4. K线连续性检查。
5. K线字段合理性检查。
6. K线缺口识别。
7. 数据质量结果生成。
8. 采集事件生成。
9. 调用存储层写入可靠数据。
10. 调用提醒层发送采集异常或数据质量异常提醒。

不负责：

1. 生成交易策略。
2. 判断是否开仓。
3. 判断是否平仓。
4. 调用 DeepSeek 分析行情。
5. 调用 OpenAI、Grok 或其他大模型。
6. 管理策略建议生命周期。
7. 做正式策略复盘。
8. 读取账户持仓。
9. 自动执行交易。

4h K线采集规则：

1. 只采集已收盘 K线。
2. 采集前后都必须以 UTC 为标准。
3. 入库前必须检查本批次连续性。
4. 入库前必须检查与数据库最新 K线是否衔接。
5. 发现缺口时，不得盲目写入异常批次。
6. 发现缺口时，必须记录数据质量问题。
7. 发现缺口时，必须触发 Hermes 提醒。
8. 采集失败时，必须记录采集事件。
9. 采集失败时，必须触发 Hermes 提醒。

历史回补与增量采集边界：

1. 历史回补负责补齐指定范围内的历史 K线。
2. 增量采集负责周期性获取最新已收盘 K线。
3. 二者可以复用 REST 客户端、解析器、质量检查器。
4. 二者都必须遵守数据质量规则。
5. 二者都不得跳过连续性校验。

1m、4h 和 1d K线：

1. 当前阶段优先实现 4h K线。
2. 当前阶段可以暂不实现 1m 和 1d K线。
3. 但在开发 4h REST K线采集能力时，必须提前考虑 1m 和 1d 的复用空间。
4. 从 Binance REST 接口角度看，1m、4h、1d K线本质上主要是 `interval`、`limit`、时间范围等参数不同。
5. 因此，REST 请求、K线解析、已收盘过滤、基础字段校验、连续性检查等通用能力应尽量设计成可复用方法，不应为 4h 写死一套无法扩展的代码。
6. 通用采集方法可以接收 `symbol`、`interval`、`start_time`、`end_time`、`limit` 等参数。
7. 但不同周期的正式数据表、Repository、质量检查入口和业务用途仍应保持清晰边界。
8. 1m、4h、1d 应按表或明确边界隔离，避免不同周期数据混在一起后难以维护。
9. 不同周期之间通过 `symbol + open_time_ms` 或 UTC 时间范围进行关联。
10. 禁止依赖数据库自增 id 关联不同周期 K线。
11. 4h 是当前主策略周期，4h 数据质量优先级最高。
12. 1m 后续主要用于复盘价格路径、插针、快速波动和细节行情。
13. 1d 后续主要用于大级别市场环境判断。
14. 当前阶段不得因为预留 1m、1d 能力而提前扩大第一阶段开发范围。
15. Codex 实现 4h K线采集时，应优先抽象通用 K线 REST 拉取与解析能力，但不得把 1m、4h、1d 的入库逻辑、质量检查结果和业务语义混写在同一个不可拆分的大函数中。

---

## 9. `app/alerting` 边界

`app/alerting` 是提醒业务层。

负责：

1. 定义提醒事件结构。
2. 构造基础提醒内容。
3. 提醒去重。
4. 提醒冷却。
5. 写入 `alert_message`。
6. 调用 Hermes Webhook。
7. 记录 Hermes 返回结果。
8. 区分提醒类型和级别。

不负责：

1. 采集 Binance 数据。
2. 判断 K线是否连续。
3. 写入正式 K线表。
4. 生成策略信号。
5. 调用大模型解释基础异常。
6. 生成最终交易建议。
7. 自动执行交易。

基础提醒规则：

1. 基础系统提醒不得调用大模型。
2. K线采集失败必须提醒。
3. K线不连续必须提醒。
4. 数据质量异常必须提醒。
5. Binance 接口异常必须提醒。
6. MySQL、Redis 等关键基础设施异常应提醒。
7. 价格波动提醒必须有阈值。
8. 价格波动提醒必须有冷却。
9. 10s 价格监控不能每 10s 刷屏。

允许调用大模型的通知类型只限未来：

1. 策略分析。
2. 操作建议。
3. 建议复盘。
4. 策略总结。
5. 多策略冲突解释。

Hermes 边界：

1. 微信提醒统一通过 Hermes。
2. 不得绕过 Hermes 另做一套微信发送链路。
3. Hermes Webhook 配置应来自 `.env` 或配置文件。
4. Hermes 返回结果应记录到 `channel_response` 或等价字段。
5. Hermes 失败应记录，但不得导致数据被错误写入。
6. Hermes 失败不能触发自动交易。

---

## 10. `app/scheduler` 边界

`app/scheduler` 是任务调度层。

负责：

1. 定义周期性任务。
2. 编排定时任务启动。
3. 调用具体业务模块。
4. 管理任务运行状态。
5. 防止重复运行。
6. 记录任务异常。
7. 必要时触发提醒。

不负责：

1. 直接写复杂业务逻辑。
2. 直接请求 Binance 并解析所有细节。
3. 直接写 SQL。
4. 直接拼接 Hermes 消息。
5. 直接调用 DeepSeek 生成建议。
6. 直接做策略判断。

调度层只能编排，不能膨胀成业务层。

例如：

允许：

1. 每 10s 调用价格监控服务。
2. 每 4h 后延迟一段时间调用 4h K线增量采集。
3. 定时调用数据质量检查服务。
4. 未来定时调用策略主评估服务。

禁止：

1. 在 scheduler 函数里直接写几十行 K线连续性判断。
2. 在 scheduler 函数里直接写 MySQL upsert 逻辑。
3. 在 scheduler 函数里直接写大模型 prompt。
4. 在 scheduler 函数里直接修改建议生命周期。

---

## 11. `app/monitoring` 边界

`app/monitoring` 是运行状态监控层。

负责：

1. 基础设施检查。
2. MySQL 可用性检查。
3. Redis 可用性检查。
4. Binance REST 可用性检查。
5. Hermes Webhook 可用性检查。
6. 任务运行状态检查。
7. 日志和健康状态辅助输出。

不负责：

1. 采集正式 K线。
2. 写入正式行情表。
3. 生成策略建议。
4. 生成大模型分析。
5. 修改建议生命周期。
6. 执行复盘。
7. 自动交易。

检查脚本和 monitoring 服务不能替代正式业务任务。

例如：

1. `check_infra` 只检查基础设施。
2. `check_binance_rest` 只检查 REST 客户端可用性。
3. `check_hermes_webhook` 只检查提醒链路。
4. 这些脚本不应写入正式策略建议表。
5. 这些脚本不应产生正式复盘结果。

---

## 12. `scripts` 边界

`scripts` 是命令行入口层。

负责：

1. 提供手动检查入口。
2. 提供一次性任务入口。
3. 提供调试入口。
4. 提供部署后验收入口。
5. 调用 `app/` 内的正式模块。

不负责：

1. 承载大量核心业务逻辑。
2. 直接写复杂 SQL。
3. 直接写复杂 K线校验。
4. 直接调用大模型生成正式建议。
5. 直接绕过业务层写数据库。
6. 绕过 alerting 模块直接调用 Hermes。

脚本原则：

1. 脚本应尽量短。
2. 脚本负责读取参数。
3. 脚本负责校验 `trigger_source` 或 `check_trigger`。
4. 脚本负责初始化配置和日志。
5. 脚本调用业务服务。
6. 脚本输出结果。
7. 核心逻辑必须下沉到 `app/` 模块。

`scripts` 可以作为人工命令入口，也可以作为受控的定时任务命令入口，但必须显式声明触发来源。

通过 scripts 触发采集类任务时，必须携带 `--trigger-source` 参数：

1. `--trigger-source scheduler`
2. `--trigger-source cli`

禁止：

1. 禁止 scripts 自动猜测触发来源。
2. 禁止不带 `trigger_source` 写入正式 K线表。
3. 禁止 scheduler 随意调用任意 scripts。
4. 禁止 scheduler 调用手动回补脚本。
5. 禁止 scheduler 调用临时脚本或未纳入计划的脚本。
6. 禁止 scripts 承载核心业务逻辑。
7. 禁止在脚本里写完整 Repository。
8. 禁止在脚本里写完整 K线连续性算法。
9. 禁止在脚本里直接拼接复杂微信提醒。
10. 禁止在脚本里直接调用 DeepSeek 生成交易建议。

允许：

1. `scripts/check_infra.py` 调用基础设施检查服务。
2. `scripts/check_binance_rest.py` 调用 Binance REST 客户端。
3. `scripts/collect_4h_klines.py --trigger-source scheduler` 作为定时增量采集入口。
4. `scripts/collect_4h_klines.py --trigger-source cli` 作为用户手动触发一次增量采集入口。
5. `scripts/backfill_4h_klines.py --trigger-source cli` 作为用户手动回补入口。
6. `scripts/check_kline_integrity.py --check-trigger cli` 作为用户手动复核入口。

### 12.1 `scripts` 边界

允许提供以下命令行入口：

- 受控的 4h 增量采集命令
- 手动 K线 REST 回补命令
- K线一致性检测命令
- 基础环境检查命令

scripts 只能作为命令入口，不得直接拼接业务 SQL，不得绕过 Repository 修改正式 K线表。

scripts 必须调用 app 层已有模块：

- Binance REST Client
- Kline Parser
- Kline Repository
- Kline Quality Checker
- Alert Service

即使由 scheduler 调用 scripts，scripts 仍只能做参数解析、配置初始化、日志初始化和调用 app service，不得直接请求 Binance、不得直接写数据库、不得直接拼接 SQL。

禁止在 scripts 中重复实现 Binance 请求、K线解析、数据库写入、报警逻辑。

---

## 13. `configs` 边界

`configs` 是非敏感配置目录。

负责：

1. 默认应用配置。
2. 策略参数配置模板。
3. 采集参数配置模板。
4. 告警阈值配置模板。
5. 非敏感配置示例。

不负责：

1. 保存真实 API Key。
2. 保存真实 Secret。
3. 保存真实 Token。
4. 保存生产数据库密码。
5. 保存生产 Hermes 密钥。

敏感信息必须放在：

1. `.env`
2. 服务器环境变量
3. 安全密钥管理系统

禁止提交：

1. `.env`
2. 生产密钥
3. 真实数据库密码
4. 真实 API Key
5. 真实 webhook secret

策略配置文件后续可以放在：

    configs/strategies/

例如：

1. `configs/strategies/gann.yaml`
2. `configs/strategies/trend.yaml`
3. `configs/strategies/liquidation_pressure.yaml`

策略配置变化必须升级 `parameter_version`。

---

## 14. `migrations` 边界

`migrations` 是数据库结构迁移层。

负责：

1. 创建表。
2. 修改表。
3. 创建索引。
4. 创建唯一约束。
5. 数据库结构版本管理。
6. 支持本地和服务器一致升级。

不负责：

1. 写业务采集逻辑。
2. 写策略逻辑。
3. 写 Hermes 提醒逻辑。
4. 写大模型分析逻辑。
5. 存放临时 SQL 草稿。

迁移规则：

1. 数据库结构变更必须通过 Alembic migration。
2. 不应直接依赖 Navicat 手工改生产表结构。
3. 每次 migration 应目的明确。
4. migration 文件不应包含密钥。
5. migration 不应写入大量业务数据。
6. migration 应能在服务器通过 `python -m alembic upgrade head` 执行。

如果需求变化导致表结构变化：

1. 先修改对应需求文档。
2. 再修改架构或计划文档。
3. 最后新增 migration。
4. 不得只改数据库不改文档。

---

## 15. `tests` 边界

`tests` 是测试目录。

负责：

1. 单元测试。
2. 集成测试。
3. Mock 测试。
4. 数据质量规则测试。
5. Repository 测试。
6. 采集流程测试。
7. 提醒流程测试。
8. 后续策略规则测试。

不负责：

1. 保存生产数据。
2. 保存真实密钥。
3. 调用真实交易接口下单。
4. 依赖真实账户状态。
5. 生成正式策略建议。

测试原则：

1. 外部接口应尽量 mock。
2. 数据质量规则必须可测试。
3. K线连续性规则必须可测试。
4. 采集失败路径必须可测试。
5. Hermes 提醒失败路径必须可测试。
6. 不得因为测试方便而绕过正式业务边界。

---

## 16. 未来 `app/strategy` 边界

`app/strategy` 是未来策略信号层。

当前第一阶段不实现。

未来负责：

1. 定义 `BaseStrategy`。
2. 实现 `GannStrategy`。
3. 实现 `TrendStrategy`。
4. 实现 `SupportResistanceStrategy`。
5. 实现 `VolatilityRiskStrategy`。
6. 实现 `LiquidationPressureStrategy`。
7. 实现 `LeastResistanceStrategy`。
8. 输出独立策略信号。

不负责：

1. 直接发送微信提醒。
2. 直接调用 Hermes。
3. 直接调用 DeepSeek。
4. 直接生成最终建议。
5. 直接管理建议生命周期。
6. 直接执行复盘。
7. 自动下单。

策略层输出必须是结构化信号。

策略层必须记录：

1. 策略名称。
2. 策略版本。
3. 参数版本。
4. 使用的 K线范围。
5. 方向。
6. 目标区域。
7. 失效条件。
8. 关键理由。
9. 风险提示。

多个策略必须独立文件、独立类、独立配置。

禁止把江恩、趋势、清算压力等策略混在一个大函数里。

---

## 17. 未来 `app/aggregation` 边界

`app/aggregation` 是未来策略聚合层。

当前第一阶段不实现。

未来负责：

1. 接收多个策略信号。
2. 分析策略一致性。
3. 分析策略分歧。
4. 生成初步方向候选。
5. 判断是否需要 `wait`。
6. 判断是否需要 `stop_trading`。
7. 输出聚合理由。
8. 输出哪些策略影响了聚合结果。

不负责：

1. 直接调用 Binance。
2. 直接写 K线表。
3. 直接调用 Hermes。
4. 直接自动交易。
5. 隐藏单个策略观点。
6. 篡改单个策略信号。

聚合层必须保留每个策略的独立观点。

即使最终聚合结果是 `wait`，也要能说明：

1. 江恩策略怎么看。
2. 趋势策略怎么看。
3. 支撑压力策略怎么看。
4. 清算压力策略怎么看。
5. 风控策略为什么否决或降级。

---

## 18. 未来 `app/llm` 边界

`app/llm` 是未来大模型分析层。

当前第一阶段不实现。

未来负责：

1. DeepSeek 接入。
2. OpenAI 接入。
3. Grok 接入。
4. 其他大模型接入。
5. prompt 模板管理。
6. 大模型输入构造。
7. 大模型响应解析。
8. 大模型原始输入输出保存。
9. 大模型建议候选生成。

不负责：

1. 直接请求 Binance。
2. 直接写 K线表。
3. 直接发送基础系统告警。
4. 直接自动下单。
5. 直接修改策略配置。
6. 直接修改复盘结果。
7. 直接覆盖最终建议。

大模型输入必须是结构化上下文。

大模型输出必须进入后续建议生成和风控校验流程。

大模型不得直接成为最终交易指令。

基础告警不得调用大模型。

例如：

1. K线采集失败不调用 DeepSeek。
2. MySQL 连接失败不调用 DeepSeek。
3. Redis 连接失败不调用 DeepSeek。
4. Binance 接口失败不调用 DeepSeek。
5. 数据不连续不调用 DeepSeek 解释。

这些基础异常由代码模板直接生成 Hermes 提醒。

---

## 19. 未来 `app/advice` 边界

`app/advice` 是未来建议生命周期层。

当前第一阶段不实现。

未来负责：

1. 生成最终操作建议。
2. 管理 active 建议。
3. 管理建议版本链。
4. 管理 `root_advice_id`。
5. 管理 `parent_id`。
6. 管理 `path`。
7. 管理 `version_no`。
8. 管理 `advice_code`。
9. 管理建议状态。
10. 生成复盘到期时间。
11. 将结束建议放入复盘队列。

不负责：

1. 请求 Binance 原始数据。
2. 直接执行交易。
3. 自动读取账户持仓。
4. 自动平仓。
5. 自动调仓。
6. 自动修改策略配置。
7. 覆盖历史建议。

建议生命周期层必须保证：

1. 每条建议可追溯。
2. 每个版本可追溯。
3. 前序版本被替代时状态为 `superseded`。
4. 不得把前序版本错误标记为 `completed`。
5. 不得重复复盘同一建议链。
6. 不得每 4h 无脑生成全新建议。

---

## 20. 未来 `app/review` 边界

`app/review` 是未来复盘评估层。

当前第一阶段不实现。

未来负责：

1. 执行到期复盘。
2. 计算最大有利波动。
3. 计算最大不利波动。
4. 计算 R 倍数。
5. 判断是否先触及目标。
6. 判断是否先触发失效。
7. 评估 `wait`。
8. 评估 `stop_trading`。
9. 评估策略信号。
10. 评估策略聚合层。
11. 评估大模型分析。
12. 评估人工执行偏离。
13. 追加复盘记录。
14. 生成复盘提醒。

不负责：

1. 修改原始策略信号。
2. 修改原始大模型输出。
3. 覆盖原始建议。
4. 自动修改策略配置。
5. 自动修改策略权重。
6. 自动执行交易。

复盘只能追加记录，不得篡改历史。

复盘不得在建议刚结束后立即执行。

默认等待约 1 天或 6 根 4h K线后执行。

---

## 21. 未来 `app/admin` 边界

`app/admin` 是未来管理后台层。

当前第一阶段不实现。

未来负责：

1. 查看行情采集状态。
2. 查看数据质量异常。
3. 查看提醒记录。
4. 查看策略信号。
5. 查看最终建议。
6. 查看建议生命周期。
7. 查看复盘结果。
8. 查看人工执行记录。
9. 管理策略配置。
10. 人工确认参数调整。
11. 人工录入执行反馈。

不负责：

1. 自动下单。
2. 自动平仓。
3. 自动调仓。
4. 自动读取账户后执行操作。
5. 绕过生命周期直接生成交易动作。

Admin 只能提供查看、管理、人工录入、人工确认能力。

不能变成交易终端。

---

## 22. 模块调用方向

推荐调用方向如下：

    scripts
      ↓
    scheduler / monitoring
      ↓
    market_data / alerting
      ↓
    exchange / storage / core

未来策略阶段：

    scheduler
      ↓
    advice
      ↓
    aggregation
      ↓
    strategy
      ↓
    storage / core

未来大模型阶段：

    advice
      ↓
    llm
      ↓
    storage / core

未来复盘阶段：

    scheduler
      ↓
    review
      ↓
    storage / alerting / core

基础方向：

1. 上层可以调用下层。
2. 下层不应调用上层。
3. 基础设施不应依赖业务模块。
4. 存储层不应依赖采集层。
5. 交易所接入层不应依赖存储层。
6. 策略层不应依赖提醒层。
7. 大模型层不应依赖采集脚本。
8. Repository 不应调用 Hermes。
9. REST client 不应写 MySQL。
10. WebSocket client 不应写正式 K线表。

---

## 23. 禁止的跨模块调用

Codex 不得实现以下调用关系：

1. `app/exchange/binance` 调用 `app/storage/mysql` 写库。
2. `app/exchange/binance` 调用 `app/alerting` 发送提醒。
3. `app/storage/mysql` 调用 `app/alerting`。
4. `app/storage/mysql` 调用 `app/strategy`。
5. `app/storage/mysql` 调用 `app/llm`。
6. `app/storage/redis` 调用 `app/strategy`。
7. `app/alerting` 调用 `app/llm` 处理基础告警。
8. `app/market_data` 调用 `app/llm` 分析行情。
9. `app/market_data` 生成最终操作建议。
10. `app/strategy` 直接调用 Hermes。
11. `app/strategy` 直接写最终建议表。
12. `app/llm` 直接写最终建议表。
13. `scripts` 绕过业务层直接写复杂 SQL。
14. `scheduler` 直接写复杂业务逻辑。
15. `admin` 自动执行交易。

如果确实需要跨模块能力，应通过上层服务编排，而不是下层模块互相调用。

---

## 24. 错误处理边界

错误处理必须分层。

### 24.1 外部接口错误

例如 Binance 请求失败。

处理方式：

1. Binance client 抛出明确异常。
2. market_data 捕获异常。
3. market_data 写采集事件。
4. market_data 生成告警事件。
5. alerting 写 alert_message。
6. alerting 调用 Hermes。
7. Hermes 结果回写。

Binance client 不直接写库、不直接提醒。

### 24.2 数据质量错误

例如 K线不连续。

处理方式：

1. market_data 发现不连续。
2. market_data 停止写入异常批次。
3. market_data 写 data_quality_check。
4. market_data 写 collector_event_log。
5. market_data 调用 alerting。
6. alerting 发送 Hermes 提醒。

数据质量错误不得被静默忽略。

### 24.3 提醒发送错误

例如 Hermes Webhook 失败。

处理方式：

1. alerting 记录发送失败。
2. alerting 保存错误响应。
3. 必要时记录日志。
4. 不得因为 Hermes 失败而篡改行情数据。
5. 不得因为 Hermes 失败而自动交易。

### 24.4 数据库错误

例如 MySQL 写入失败。

处理方式：

1. 存储层抛出明确异常。
2. 上层业务决定是否重试。
3. 上层业务决定是否提醒。
4. 不得吞掉异常继续假装成功。

---

## 25. 数据对象与 DTO 边界

系统不同模块之间传递数据时，应尽量使用清晰的数据对象，而不是直接到处传递交易所原始数组、数据库 ORM 对象或随意拼接的 dict。

推荐数据流：

1. Binance 原始响应。
2. Parser 转换后的内部 Kline DTO。
3. Validator 校验后的可入库 Kline 数据。
4. Repository 写入 ORM 模型。
5. Service 返回业务执行结果。

原则：

1. Binance 原始数组只应停留在 `exchange/binance` 或 parser 附近。
2. ORM 模型主要用于存储层，不应在所有业务层到处传递。
3. 业务层应使用结构清晰的 DTO 或 dataclass。
4. Alerting 层应接收结构化 AlertEvent，而不是随意拼接字符串。
5. 未来 Strategy 层应接收结构化行情数据和市场环境数据，而不是直接读取 Binance 原始响应。

禁止：

1. 在多个模块中重复解析 Binance 原始数组。
2. 把 ORM 对象直接传给大模型层。
3. 把未校验的原始 K线直接写入正式 K线表。
4. 用自由 dict 在多个模块之间传递关键业务数据但没有字段约束。

如果字段含义重要，应定义明确的数据结构。

---

## 26. 事务边界

数据库事务边界应由业务 Service 或上层编排模块控制。

Repository 负责数据读写，但不应在每个方法内部随意提交事务，除非该方法被明确设计为独立事务操作。

原则：

1. Service 决定一个业务流程内哪些写入应处于同一事务。
2. Repository 提供 insert、update、upsert、query 等数据访问能力。
3. Repository 不应擅自吞掉数据库异常。
4. 采集事件、数据质量记录、K线写入、告警记录之间的事务边界必须清晰。
5. 如果 K线质量检查失败，正式 K线不得写入。
6. 如果正式 K线写入成功但 Hermes 提醒失败，不得回滚已经确认可靠的 K线数据。
7. 如果数据库写入失败，不能假装采集成功。

例如：

4h K线增量采集时：

1. REST 获取数据。
2. Parser 解析数据。
3. Validator 校验数据。
4. 校验通过后，Service 控制事务写入正式 K线表。
5. 校验失败时，不写正式 K线表。
6. 校验失败记录 data_quality_check 和 collector_event_log。
7. 告警发送由 alerting 模块处理，告警失败不得篡改 K线质量判断。

事务边界必须在模块说明文件中写清楚。

---

## 27. 幂等边界

所有采集任务和告警任务都必须考虑重复执行。

重复执行同一个任务时，系统不应产生错误数据。

幂等规则：

1. K线表必须通过唯一约束防止重复 K线。
2. K线 upsert 必须基于 `exchange + market_type + symbol + interval + open_time_ms` 或对应周期表的唯一键。
3. 不得依赖自增 id 判断是否重复。
4. 历史回补任务重复执行不应插入重复 K线。
5. 增量采集任务重复执行不应插入重复 K线。
6. 告警任务应有去重和冷却机制。
7. 同一个数据质量异常不应无限刷屏。
8. 同一条建议链后续复盘提醒只能发送一次。

幂等逻辑应放在合适层级：

1. 数据唯一性由数据库唯一约束兜底。
2. upsert 由 Repository 提供。
3. 是否允许重复提醒由 alerting 层判断。
4. 是否重复执行任务由 scheduler 或 service 层判断。

禁止只依赖代码内存状态保证幂等。

---

## 28. 配置读取边界

系统配置必须通过统一配置模块读取。

禁止在业务代码中到处直接读取 `.env`。

原则：

1. `.env` 只作为配置来源之一。
2. `app/core/config` 或等价模块负责统一读取、校验和暴露配置。
3. 业务模块通过 settings 对象获取配置。
4. 敏感配置不得写入日志。
5. 敏感配置不得写入模块说明文件。
6. 测试环境应能通过测试配置或 mock 配置运行。

禁止：

1. 在多个业务文件中重复调用 dotenv。
2. 在 Repository 中读取 `.env`。
3. 在 Strategy 中读取数据库密码。
4. 在 Alerting 中硬编码 Hermes secret。
5. 在代码中硬编码 Binance API 地址以外的敏感配置。

---

## 29. 日志边界

日志用于排查问题，但不能替代数据库事件记录和告警记录。

日志负责：

1. 记录程序运行过程。
2. 记录异常堆栈。
3. 记录关键业务节点。
4. 辅助本地和服务器排查。

日志不负责：

1. 替代 collector_event_log。
2. 替代 data_quality_check。
3. 替代 alert_message。
4. 替代 strategy_advice。
5. 替代 review 记录。

原则：

1. 数据采集失败必须写事件表，不能只写日志。
2. 数据质量异常必须写质量检查表，不能只写日志。
3. Hermes 提醒必须写 alert_message，不能只写日志。
4. 日志不得包含 API Key、Secret、Token、数据库密码。
5. 日志中的时间必须明确使用 UTC 或 PRC。
6. 面向用户的时间展示必须标注时区。

---

## 30. 注释与实现说明要求

Codex 编写模块时，必须遵守 `AGENTS.md` 中的注释要求。

每个重要类、方法、脚本入口必须说明：

1. 负责什么功能。
2. 关键参数。
3. 返回值。
4. 失败场景。
5. 是否访问外部服务。
6. 是否写 MySQL。
7. 是否写 Redis。
8. 是否发送 Hermes 提醒。

Codex 每完成一个业务模块，必须在 `docs/implementation/` 下提交对应模块说明文件。

模块说明文件必须说明：

1. 模块目的。
2. 入口文件。
3. 入口方法。
4. 主要调用链路。
5. 关键文件和关键类。
6. 数据从哪里来。
7. 数据经过哪些校验。
8. 数据写入哪里。
9. 失败时如何记录。
10. 是否会触发 Hermes 微信提醒。
11. 本模块不负责什么。

如果只提交代码、不提交模块说明文件，视为任务未完成。

---

## 31. Codex 开发约束

Codex 开发时必须遵守：

1. 先读 `AGENTS.md`。
2. 再读相关 requirements。
3. 再读相关 architecture。
4. 再读相关 decisions。
5. 再读当前 plan。
6. 每次只实现当前 plan 范围。
7. 不得擅自扩大范围。
8. 不得把多个未来阶段提前混在一起实现。
9. 不得为了方便写成一个大脚本。
10. 不得为了方便绕过 Repository。
11. 不得为了方便绕过 alerting。
12. 不得为了方便绕过数据质量检查。
13. 不得为了方便跳过 Hermes 统一提醒通道。
14. 不得实现自动交易。
15. 不得实现自动读取账户后执行操作。
16. 不得把基础告警接入大模型。
17. 不得使用 WebSocket 拼接正式 4h 主 K线。
18. 不得用 Redis 保存长期行情。
19. 不得用数据库 id 判断行情顺序。
20. 不得在下层模块中推翻上层需求。

如果开发过程中发现需求、架构、计划与代码实现冲突，Codex 应停止开发并提示需要先修改文档。

不得在代码中偷偷改变需求方向。