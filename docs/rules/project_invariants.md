# Project Invariants

本文档记录 Hermes + DeepSeek BTC 合约策略辅助系统的项目铁律。

所有 requirements、architecture、decisions、plans、implementation、代码实现、测试和脚本都必须遵守本文档。

如果任何文档或代码与本文档冲突，以本文档为准。

如果 Codex 在实现过程中发现本文档与某个 plan、architecture、decision 或现有代码存在冲突，必须停止实现并提示用户确认，不得自行猜测或绕过。

---

## 1. 项目根本边界

1. 本系统是 BTCUSDT 合约策略辅助系统，不是自动交易系统。
2. 系统目标是辅助用户做长期可验证的交易判断，不是追求短期暴利。
3. 系统只提供数据采集、数据质量检查、价格监控、提醒、后续策略分析和建议辅助。
4. 系统不得自动执行任何真实交易动作。
5. 用户始终保留最终人工决策权。
6. 所有会影响交易判断的数据、策略、建议、提醒都必须可追溯。
7. 系统设计必须优先保证数据可信、边界清晰、行为可审计，而不是追求短期开发速度。

---

## 2. 自动交易铁律

系统永远禁止实现以下能力：

1. 自动下单。
2. 自动平仓。
3. 自动调仓。
4. 自动加仓。
5. 自动减仓。
6. 自动撤单。
7. 自动调整杠杆。
8. 自动调整保证金模式。
9. 自动读取账户后自行做交易决策。
10. 自动根据策略结果执行 Binance 交易接口。

禁止使用或封装以下 Binance 能力：

1. order endpoint。
2. account endpoint。
3. position endpoint。
4. leverage endpoint。
5. margin endpoint。
6. listenKey。
7. private user data stream。

如果代码中出现上述接口、能力或类似封装，默认视为严重违规。

---

## 3. K线数据来源铁律

1. 正式 K线数据只能来自 Binance REST 官方 K线接口。
2. BTCUSDT 4h 正式 K线必须来自 Binance REST `/fapi/v1/klines`。
3. 正式 K线表不得写入 WebSocket 聚合出来的 K线。
4. 正式 K线表不得写入第三方行情源数据。
5. 正式 K线表不得写入人工输入数据。
6. 正式 K线表不得写入手工编辑数据。
7. 正式 K线表不得写入模拟数据。
8. 正式 K线表只允许保存已收盘 K线。
9. 未收盘 K线不得写入正式 K线表。
10. 判断 K线是否收盘时，应优先使用 Binance server time，不得只依赖本机时间。

---

## 4. 禁止人工修复 K线

系统永远禁止以下数据来源或修复方式：

1. `manual_repair`
2. `human_edit`
3. `manual_input`
4. `system_repair`
5. 人工直接修改 K线字段。
6. 人工手填 K线字段。
7. 程序自动修复正式 K线。
8. 程序自动覆盖冲突 K线。
9. 程序自动删除异常 K线。
10. 复核任务自动修改正式 K线。

即使发现 K线数据错误，也只能通过手动 CLI 回补任务重新请求 Binance REST 官方已收盘 K线，并按规则校验后写入。

手动回补的含义是：

```text
用户手动触发 CLI
    ↓
系统请求 Binance REST 官方 K线
    ↓
系统解析、校验、记录、写入
```

手动回补不是：

```text
用户手动输入 K线字段
    ↓
系统写入数据库
```

后者绝对禁止。

---

## 5. trigger_source 与 data_source 铁律

所有 K线写入必须明确记录真实触发来源。

允许的 `trigger_source`：

```text
cli
scheduler
```

允许的 `data_source`：

```text
binance_rest_by_cli
binance_rest_by_scheduler
```

映射规则：

```text
trigger_source = cli
    ↓
data_source = binance_rest_by_cli
```

```text
trigger_source = scheduler
    ↓
data_source = binance_rest_by_scheduler
```

禁止：

1. 缺少 `trigger_source` 仍写入正式 K线表。
2. 非法 `trigger_source` 仍继续执行。
3. 自动猜测触发来源。
4. 根据是否经过 `scripts/*.py` 猜测 `data_source`。
5. scheduler 触发却记录为 `cli`。
6. CLI 触发却记录为 `scheduler`。
7. 手动回补伪装成定时采集。
8. 定时采集伪装成手动回补。

---

## 6. 时间规则铁律

1. Binance 原始时间以 UTC 为准。
2. 业务排序以 UTC 时间或 Binance 毫秒时间戳为准。
3. K线连续性判断以 `open_time_ms` 为准。
4. 4h K线相邻 open time 差值必须是 `14400000` 毫秒。
5. PRC 时间只用于用户阅读、排查和展示。
6. PRC 时间不得作为业务排序依据。
7. PRC 时间不得作为 K线连续性判断依据。
8. 程序中不得到处手写 `+ timedelta(hours=8)`。
9. UTC 转 PRC 必须统一调用 `app/core/time_utils.py` 中的辅助函数。
10. 数据库中保留 PRC 时间字段时，必须明确它只是展示辅助字段。

---

## 7. 4h K线与 10s 价格监控边界

4h K线主线：

```text
Binance REST /fapi/v1/klines
    ↓
官方已收盘 4h K线
    ↓
MySQL market_kline_4h
```

10s 实时价格监控主线：

```text
Binance WebSocket aggTrade
    ↓
最新成交价
    ↓
Redis bitcoin_price
    ↓
Hermes 价格波动提醒
```

禁止混用：

1. 10s 价格监控不得写入 `market_kline_4h`。
2. 4h K线采集不得写入 Redis `bitcoin_price`。
3. 10s 价格监控不得使用 REST 每 10 秒轮询价格。
4. 10s 价格监控必须使用 Binance WebSocket。
5. 4h 正式 K线不得使用 WebSocket 生成。
6. `bitcoin_price` 不是长期历史行情库。
7. MySQL K线表不是实时价格缓存。

---

## 8. REST 与 WebSocket 边界

Binance REST 用于：

1. 官方已收盘 K线采集。
2. 手动 K线回补。
3. K线完整性复核。
4. Binance server time。
5. exchange info、ping 等基础检查。

Binance WebSocket 用于：

1. 10s 实时价格监控。
2. 最新成交价事件。
3. 后续可能的实时行情事件。

禁止：

1. 用 REST 每 10 秒轮询最新价格。
2. 用 WebSocket 数据写正式 4h K线表。
3. 用第三方行情源替代 Binance 官方 K线。
4. 在业务模块中绕过 `BinanceRestClient` 手写 REST 请求。
5. 在脚本中直接拼 Binance URL 请求核心数据。

---

## 9. scripts 边界

1. `scripts/*.py` 只能作为 CLI 入口、进程启动入口或检查入口。
2. `scripts/*.py` 不得承载核心业务逻辑。
3. `scripts/*.py` 不得直接写复杂业务流程。
4. `scripts/*.py` 应解析参数、初始化配置、调用 `app/` 内 service。
5. 核心逻辑必须放在 `app/` 内对应模块。
6. 每个 `scripts/*.py` 都必须在对应 plan 和文件顶部明确声明允许的触发方式，例如 `cli only`、`cli + scheduler`、`systemd/supervisor`。
7. 如果脚本允许 scheduler 调用，scheduler 必须显式传入 `--trigger-source scheduler`，不得伪装成 `cli`。
8. 如果脚本声明为 `cli only`，则只允许 `--trigger-source cli`；如果收到 `--trigger-source scheduler`，必须拒绝执行并返回非 0 状态码。
9. 脚本不得绕过 service 直接请求 Binance。
10. 脚本不得绕过 repository 直接写业务表。

---

## 10. scheduler 边界

1. scheduler 只能调用明确允许的 job 或 service。
2. scheduler 调用采集任务时必须记录 `trigger_source = scheduler`。
3. scheduler 不得伪装成人工 CLI。
4. scheduler 不得直接写业务数据库。
5. scheduler 不得直接请求 Binance 核心数据。
6. scheduler 不得直接发送业务报警。
7. scheduler job 应尽量薄，只负责触发 service。
8. 4h K线采集不应每 10 秒执行。
9. 10s 价格监控不应由 scheduler 每 10 秒反复启动脚本。
10. 10s 价格监控应由常驻进程维护 WebSocket 连接。

---

## 11. MySQL 边界

MySQL 用于保存：

1. 正式 4h K线。
2. 数据质量检查记录。
3. 采集事件记录。
4. Hermes 报警记录。
5. 后续策略信号、建议生命周期、复盘结果。

MySQL 禁止保存：

1. 人工修复 K线。
2. 自动修复后的不透明 K线。
3. 10s 实时价格缓存。
4. Binance 账户敏感数据。
5. API secret。
6. 交易执行状态，除非后续明确是人工执行记录且不接入自动交易。

正式 K线表禁止：

1. 静默覆盖冲突数据。
2. 自动删除异常数据。
3. 人工直接修改字段。
4. 未通过质量检查强行写入。

---

## 12. Redis 边界

Redis 用于：

1. 10s 实时价格状态。
2. `bitcoin_price`。
3. 短期缓存。
4. 后续可能的报警冷却状态。
5. K线写入任务锁。

Redis 不用于：

1. 长期历史 K线存储。
2. 策略复盘长期数据。
3. 正式审计数据。
4. 替代 MySQL K线表。
5. 保存密钥。
6. 保存账户敏感信息。

`bitcoin_price` 规则：

1. 数据来源必须是 Binance WebSocket 成交价。
2. 默认 TTL 为 2 分钟。
3. 每次监控判断都应刷新 TTL。
4. 它只是实时状态，不是正式历史行情。

---

## 13. Hermes 报警边界

1. 基础系统报警必须使用固定模板。
2. K线采集异常、质量异常、系统异常、价格波动异常，不得调用 DeepSeek 生成报警内容。
3. Hermes 报警必须通过 `app/alerting` 统一发送。
4. 底层 Binance client 不得直接发送 Hermes。
5. MySQL repository 不得直接发送 Hermes。
6. Redis repository 不得直接发送 Hermes。
7. 报警必须说明事件类型、symbol、interval、触发来源、异常摘要。
8. K线异常报警必须明确系统没有自动修复、没有人工改数、没有自动交易。
9. 价格波动报警必须明确这不是交易建议。
10. Hermes 发送失败不得导致修改正式 K线表。

---

## 14. DeepSeek 与大模型边界

1. 第一批数据底座阶段不得调用 DeepSeek。
2. 基础系统报警不得调用 DeepSeek。
3. K线采集不得调用 DeepSeek。
4. K线回补不得调用 DeepSeek。
5. K线复核不得调用 DeepSeek。
6. 10s 价格监控不得调用 DeepSeek。
7. DeepSeek 后续只用于策略解释、建议分析、模型对比和复盘辅助。
8. DeepSeek 不得直接执行交易。
9. DeepSeek 不得直接写正式 K线。
10. DeepSeek 输出必须可追溯、可记录、可复盘。

---

## 15. 策略边界

1. 第一批 plans 不实现策略。
2. 第一批 plans 不实现江恩策略。
3. 第一批 plans 不实现趋势策略。
4. 第一批 plans 不实现多策略聚合。
5. 第一批 plans 不实现交易建议。
6. 第一批 plans 不实现建议生命周期。
7. 第一批 plans 不实现策略复盘。
8. 策略能力必须在数据底座稳定后再开发。
9. 后续策略必须插件化、面向对象设计。
10. 多策略必须先分别保存独立信号，再由聚合层生成综合结论。
11. 多策略严重分歧或风控不满足时，系统必须允许输出 wait 或 stop_trading。
12. 停止交易是一种有效建议，不是系统失败。

---

## 16. 数据质量边界

1. K线写入前必须做基础字段校验。
2. K线写入前必须做批次连续性校验。
3. K线写入前必须做未收盘过滤。
4. K线写入前必须做数据库已有数据冲突检查。
5. 质量检查失败时不得写入正式 K线表。
6. 质量检查失败时可以记录 `data_quality_check`。
7. 质量检查失败时可以通过 Hermes 报警。
8. 质量检查不得自动修复。
9. 质量检查不得自动回补。
10. 质量检查不得修改正式 K线表。

每日复核的目的：

```text
检查过去 K线是否存在错误、缺失、不连续、未收盘误写、与 Binance 官方 REST 不一致
```

每日复核不是：

```text
自动修复 K线
自动回补 K线
自动覆盖 K线
```

---

## 17. collector_event_log 边界

1. `collector_event_log` 用于记录采集、回补、复核等任务事件。
2. 它不是正式 K线表。
3. 它不得替代 `market_kline_4h`。
4. 它必须记录 `trigger_source`。
5. 它必须记录 `data_source`，如果任务涉及正式 K线写入。
6. 它必须记录任务状态。
7. 它必须能帮助排查任务何时开始、何时结束、成功还是失败。
8. 它不得保存未脱敏密钥、token、webhook。
9. 它不得保存人工伪造 K线。
10. 它不得被用作自动修复依据。

---

## 18. alert_message 边界

1. `alert_message` 用于记录报警发送结果。
2. 报警内容必须脱敏。
3. `channel_response` 必须避免保存敏感信息。
4. 报警失败不得导致正式数据被修改。
5. 报警模块不得调用交易接口。
6. 报警模块不得调用 DeepSeek 生成基础系统报警。
7. 报警模块不得绕过 Hermes 统一通道。

---

## 19. 数据库迁移铁律

1. 数据库结构变更必须通过 Alembic migration。
2. 不允许通过 Navicat、手写 SQL、临时脚本直接修改生产表结构。
3. Codex 只允许生成 migration 文件，不得自动执行 `alembic upgrade head`。
4. migration 不得删除已有业务表。
5. migration 不得删除已有业务字段。
6. migration 不得清空业务数据。
7. migration 不得插入真实业务数据。
8. 如需破坏性变更，必须单独写 decision 文档并由用户确认。
9. Alembic migration 文件必须可追溯、可回滚，或明确说明不可回滚原因。
10. 生产环境执行 migration 必须由用户人工触发。

---

## 20. 任务并发铁律

1. 同一 `symbol + interval` 的 K线采集任务不得并发运行。
2. 同一 `symbol + interval` 的手动回补任务不得与增量采集任务同时写入正式 K线表。
3. scheduler 触发任务前必须具备防重入机制。
4. 所有写入正式 K线表的任务启动前，必须先获取同一 `symbol + interval` 的任务锁。
5. 如果任务锁已存在，本次任务必须拒绝或跳过，并记录 `collector_event_log`，不得继续写入正式 K线表。
6. 任务锁必须具备 TTL，避免进程异常退出后永久阻塞。
7. 释放任务锁时必须校验锁 owner，禁止误删其他任务持有的锁。
8. 仅检查 `collector_event_log.status = running` 不足以防并发，不能作为唯一并发控制手段。
9. 不得因为并发任务导致重复写入、乱序写入或冲突覆盖。
10. 所有写入正式 K线表的任务必须具备幂等能力。
11. 所有任务重复执行后，结果必须可解释、可追溯。

说明：`collector_event_log` 用于审计追踪，不用于充当唯一并发锁。并发控制必须依赖具备原子性的任务锁，例如 Redis `SET key value NX EX seconds`，或后续明确设计的 MySQL 原子锁机制。

---

## 21. 外部请求铁律

1. 所有 Binance REST 请求必须设置超时时间。
2. 所有 Hermes 请求必须设置超时时间。
3. 所有外部请求失败必须记录明确错误类型。
4. 不得无限重试。
5. 重试次数、退避策略必须可配置。
6. Binance REST 请求失败不得写入正式 K线表。
7. Binance server time 获取失败时，不得继续写入需要判断收盘状态的 K线。
8. WebSocket 断线可以重连，但不得切换为 REST 轮询价格，除非后续 plan 明确允许。
9. 外部请求错误日志不得包含密钥、token、webhook、Authorization。

---

## 22. 删除操作铁律

1. 默认禁止删除正式业务数据。
2. 禁止删除 `market_kline_4h` 中的正式 K线。
3. 禁止通过脚本批量删除业务表数据。
4. 禁止 Codex 生成清库脚本、重置脚本、truncate 脚本。
5. 测试数据清理只能作用于测试环境。
6. 如确需删除数据，必须单独写 decision 文档并由用户人工确认。
7. 删除操作不得伪装成修复操作。

---

## 23. 配置与环境铁律

1. 所有环境变量必须通过统一配置模块读取。
2. 不得在业务代码中直接散落读取 `os.getenv`，除非位于配置模块内部。
3. 不得在代码中硬编码数据库地址、Redis 地址、Hermes 地址等环境差异配置。
4. `.env.example` 只能写示例值，不得写真实密钥。
5. 新增配置项必须同步更新 `.env.example`。
6. 生产、测试、开发环境配置必须可区分。
7. `APP_DEBUG` 不得在生产环境默认开启。
8. 日志级别必须可配置。

---

## 24. 依赖管理铁律

1. 新增 Python 依赖必须写入 `pyproject.toml`。
2. 不得随意引入大型依赖解决小问题。
3. 不得引入来历不明、长期不维护或安全风险高的依赖。
4. 不得在代码中要求用户手动 `pip install` 未记录的包。
5. Codex 新增依赖时，必须说明用途和必要性。
6. 不得把开发依赖用于生产核心逻辑。
7. 项目必须在虚拟环境中运行，不得依赖系统全局 Python 包。

---

## 25. 审计与 trace_id 铁律

1. 关键任务必须生成 `trace_id`。
2. K线采集、手动回补、质量复核、价格监控报警必须记录 `trace_id`。
3. `collector_event_log`、`data_quality_check`、`alert_message` 之间应尽量通过 `trace_id` 或关联 ID 串联。
4. 日志中必须包含 `trace_id`，便于排查完整链路。
5. 同一次任务的 Binance 请求、质量检查、数据库写入、Hermes 报警必须能通过 `trace_id` 追踪。
6. `trace_id` 不得包含密钥、账户信息或敏感数据。

---

## 26. 代码结构边界

1. `app/core` 放配置、日志、异常、时间工具、任务锁等基础能力。
2. `app/exchange/binance` 只封装 Binance 公开行情接口、公开 WebSocket 连接和基础连接检查。
3. `app/storage/mysql` 只负责 MySQL model、session、repository。
4. `app/storage/redis` 只负责 Redis 客户端和 Redis 状态读写。
5. `app/market_data` 放行情数据采集、解析、校验、回补、复核、价格监控业务逻辑。
6. `app/alerting` 放 Hermes 报警业务层。
7. `app/scheduler` 放定时任务入口和 job。
8. `scripts` 只放人工入口、进程启动入口或检查入口。
9. `docs/implementation` 必须按功能记录实现说明。
10. 禁止把多个模块职责混进一个大文件。

---

## 27. 实现说明边界

每个阶段完成后，必须创建或更新：

```text
docs/implementation/<plan编号>_<模块名>.md
```

implementation 必须写清楚：

1. 功能入口。
2. 入口文件。
3. 入口方法。
4. 核心 service。
5. 调用链。
6. 读取哪些配置。
7. 请求哪些外部接口。
8. 读写哪些数据库表。
9. 读写哪些 Redis key。
10. 是否发送 Hermes。
11. 异常如何处理。
12. 哪些事情明确不做。
13. 对应测试。
14. 人工检查命令。

禁止只写：

```text
实现了某功能
```

必须写到用户能追踪一次完整运行流程。

---

## 28. 安全与密钥铁律

禁止提交：

1. `.env`
2. 真实数据库密码。
3. 真实 Redis 密码。
4. Hermes webhook。
5. Hermes secret。
6. Binance API key。
7. Binance secret key。
8. token。
9. Authorization。
10. cookie。
11. 生产日志。
12. 账户、订单、持仓敏感信息。

日志中禁止打印：

1. 完整 `.env`。
2. 密码。
3. secret。
4. token。
5. webhook。
6. Authorization。
7. cookie。
8. 账户敏感信息。

---

## 29. 测试边界

1. 默认 `pytest` 不应请求真实 Binance。
2. 默认 `pytest` 不应连接真实 MySQL。
3. 默认 `pytest` 不应连接真实 Redis。
4. 默认 `pytest` 不应发送真实 Hermes。
5. 默认 `pytest` 不应调用 DeepSeek。
6. 默认 `pytest` 不应访问交易接口。
7. 真实集成测试必须使用显式开关。
8. mock 测试必须覆盖关键边界。
9. 测试必须覆盖禁止项，例如不写 K线表、不调用 DeepSeek、不使用交易接口。
10. 每个 plan 必须有对应测试或检查脚本。

---

## 30. Codex 实现铁律

Codex 实现时必须：

1. 先读取当前 plan。
2. 先读取本文档。
3. 检查是否与已有文件冲突。
4. 只做当前阶段明确允许的事情。
5. 不得越界实现后续阶段能力。
6. 不得删除已有文档。
7. 不得清空已有目录。
8. 不得覆盖用户已有文件。
9. 修改已有文件前必须基于现有内容最小修改。
10. 不确定时必须停止并提示用户确认。

Codex 禁止：

1. 自行创建分支。
2. 自行切换分支。
3. 自行合并分支。
4. 自行推送远程仓库。
5. 自行删除分支。
6. 自行执行生产迁移。
7. 自行连接生产数据库。
8. 自行提交真实密钥。
9. 自行扩大功能范围。
10. 自行解释并绕过本文档。

---

## 31. 人工审查铁律

每次 Codex 完成一个阶段后，用户应至少检查：

1. 是否违反本文档。
2. 是否只实现当前 plan。
3. 是否改错其他模块。
4. 是否出现自动交易代码。
5. 是否出现人工修复 K线代码。
6. 是否出现 REST / WebSocket 边界混乱。
7. 是否出现 DeepSeek 越界调用。
8. 是否出现敏感信息。
9. 是否有 implementation 文档。
10. 是否有测试或检查脚本。
11. 是否能通过 grep 搜索危险词。
12. 是否能通过 `pytest`。
13. 是否能运行对应 help 或 check 命令。

如果发现任何硬规则被违反，应拒绝合并。

---

## 32. 文档冲突处理规则

如果 requirements、architecture、decisions、plans、implementation 之间发生冲突：

1. 优先遵守本文档。
2. 其次遵守 decisions。
3. 再遵守 requirements。
4. 再遵守 architecture。
5. 再遵守 plans。
6. implementation 只能描述实际实现，不得反过来改写规则。

如果本文档本身存在错误，应由用户明确修改本文档后，再继续开发。

Codex 不得自行绕过本文档。
