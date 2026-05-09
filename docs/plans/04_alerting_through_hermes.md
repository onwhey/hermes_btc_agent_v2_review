# 04 Alerting Through Hermes Plan

## 1. 阶段目标

本阶段实现基础系统报警能力，通过 Hermes 发送固定模板报警。

本阶段目标是让后续模块在发生基础系统异常时，可以统一调用报警服务，例如：

1. Binance REST 请求失败。
2. MySQL 异常。
3. Redis 异常。
4. K线采集失败。
5. K线字段冲突。
6. K线缺失或不连续。
7. K线一致性复核异常。
8. 10s 价格事件。

注意：本阶段只实现通用报警能力，不实现具体业务异常检测。

## 2. 本阶段明确不做

本阶段不得实现行情、采集、复核、策略或交易业务。

禁止实现：

1. Binance REST 请求。
2. 4h K线采集。
3. 4h K线回补。
4. K线一致性复核。
5. 10s 价格监控。
6. MySQL / Redis 健康检查重复实现。
7. DeepSeek 或其他大模型调用。
8. 策略分析。
9. 交易建议。
10. 自动下单、自动平仓、自动调仓。
11. Binance 账户、订单、持仓相关接口。
12. WebSocket 行情接入。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/04_alerting_requirements.md`
3. `docs/architecture/system_architecture.md`
4. `docs/architecture/module_boundaries.md`
5. `docs/architecture/data_flow.md`
6. `docs/decisions/0001-no-auto-trading.md`
7. `docs/decisions/0004-alerting-through-hermes.md`
8. `docs/plans/01_project_skeleton.md`
9. `docs/plans/02_core_config_logging.md`
10. `docs/plans/03_infra_mysql_redis.md`
11. `docs/implementation/01_project_skeleton.md`
12. `docs/implementation/02_core_config_logging.md`
13. `docs/implementation/03_infra_mysql_redis.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`
5. `app/storage/mysql/` 基础设施

不得重复实现配置、日志、时间和数据库连接逻辑。

## 4. 建议分支

建议分支名：

`feature/04-alerting-through-hermes`

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
app/monitoring/
app/alerting/
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

说明：

1. 当前项目骨架中已经存在 `app/monitoring/`。
2. 本阶段建议把报警能力放在 `app/alerting/`。
3. 本阶段正式使用 `app/alerting/` 作为 Hermes 基础报警模块目录。
4. `app/monitoring/` 不承载 Hermes 报警主逻辑。后续如需系统健康状态汇总、任务状态观测，可放入 `app/monitoring/`。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/monitoring/__init__.py
app/alerting/__init__.py
app/alerting/hermes_client.py
app/alerting/templates.py
app/alerting/service.py
app/alerting/types.py
app/alerting/sanitizer.py

app/storage/mysql/models/alert_message.py
app/storage/mysql/repositories/alert_message_repository.py

scripts/check_hermes_alerting.py
tests/test_hermes_alerting.py
docs/implementation/04_alerting_through_hermes.md

.env.example
pyproject.toml
migrations/versions/
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. Hermes 配置要求

本阶段配置必须复用：

`app/core/config.py`

建议支持以下配置项：

```
HERMES_WEBHOOK_URL
HERMES_SECRET
HERMES_TIMEOUT_SECONDS
HERMES_MAX_RETRIES
HERMES_ENABLED
HERMES_DRY_RUN
```

建议默认值：

```
HERMES_TIMEOUT_SECONDS=10
HERMES_MAX_RETRIES=2
HERMES_ENABLED=false
HERMES_DRY_RUN=true
```

要求：

1. `HERMES_WEBHOOK_URL` 不得打印完整值。
2. `HERMES_SECRET` 不得打印。
3. `HERMES_ENABLED=false` 时不得真实发送报警。
4. `HERMES_DRY_RUN=true` 时不得真实发送报警。
5. 测试环境默认不得真实调用 Hermes。
6. 只有显式配置允许时，才可以真实发送。

## 8. `.env.example` 更新要求

如果 `.env.example` 已存在，只允许补齐缺失项，不得清空重写。

建议包含：

```
HERMES_ENABLED=false
HERMES_DRY_RUN=true
HERMES_WEBHOOK_URL=
HERMES_SECRET=
HERMES_TIMEOUT_SECONDS=10
HERMES_MAX_RETRIES=2
```

禁止：

1. 写入真实 Hermes webhook。
2. 写入真实 Hermes secret。
3. 写入真实 token。
4. 写入真实 cookie。
5. 写入完整生产 URL。
6. 覆盖已有有效配置。

## 9. Hermes Client 要求

Hermes 客户端建议放在：

`app/alerting/hermes_client.py`

职责：

1. 读取 Hermes 配置。
2. 构造 Hermes 请求。
3. 发送固定模板报警内容。
4. 处理超时。
5. 处理 HTTP 错误。
6. 处理网络异常。
7. 返回发送结果。
8. 对响应内容做脱敏。

允许：

1. 使用 `requests` 或 `httpx` 发送 HTTP 请求。
2. 设置 timeout。
3. 设置有限重试。
4. 对失败结果返回明确错误。

禁止：

1. 调用 DeepSeek 或其他大模型。
2. 在 Hermes client 中生成交易建议。
3. 在 Hermes client 中读取 Binance。
4. 在 Hermes client 中访问 MySQL 业务数据。
5. 在 Hermes client 中访问 Redis 业务 key。
6. 在日志中打印完整 webhook。
7. 在日志中打印 secret。
8. 在数据库中保存未脱敏响应。

## 10. 报警模板要求

报警模板建议放在：

`app/alerting/templates.py`

本阶段只允许实现基础系统报警模板，不实现策略建议模板。

允许模板类型：

1. `system_error`
2. `infra_error`
3. `binance_rest_error`
4. `mysql_error`
5. `redis_error`
6. `kline_data_quality_error`
7. `kline_integrity_check_failed`
8. `collector_failed`
9. `price_event`
10. `manual_test_alert`

注意：

1. 本阶段可以定义这些模板类型。
2. 本阶段不实现对应业务检测逻辑。
3. 后续模块只负责传入结构化 alert event，由本模块格式化报警内容。

模板内容必须固定，不得调用大模型生成。

报警模板至少包含：

1. alert_type
2. severity
3. title
4. message
5. symbol，可选
6. interval，可选
7. event_time_utc
8. event_time_prc
9. source_module
10. trace_id，可选
11. suggested_check，可选

`kline_integrity_check_failed` 模板必须强调：

1. 复核任务只检查，不修复。
2. 发现异常后提醒用户检查采集代码、调度、数据库写入、Binance REST 访问。
3. 不自动回补。
4. 不自动覆盖。
5. 不自动修改正式 K线表。

## 11. Alert 类型定义要求

建议放在：

`app/alerting/types.py`

可以定义：

1. `AlertSeverity`
2. `AlertType`
3. `AlertEvent`
4. `AlertSendResult`

建议 severity 允许：

```
info
warning
error
critical
```

注意：

1. 不要定义交易建议类型。
2. 不要定义 long / short / close / stop_loss 等策略信号。
3. 不要定义订单执行类型。
4. 不要定义账户或持仓类型。

本阶段 AlertEvent 是系统报警事件，不是交易信号。

## 12. 报警服务要求

报警服务建议放在：

`app/alerting/service.py`

职责：

1. 接收结构化 AlertEvent。
2. 调用模板生成固定报警内容。
3. 写入报警记录。
4. 调用 Hermes Client 发送。
5. 记录发送结果。
6. 处理发送失败。
7. 保证敏感信息脱敏。

建议提供：

1. `send_alert(event: AlertEvent)`
2. `send_test_alert()`
3. `format_alert_message(event: AlertEvent)`

要求：

1. 基础报警必须使用固定模板。
2. 不允许调用 DeepSeek。
3. 不允许调用任何大模型。
4. 不允许生成交易建议。
5. 不允许读取账户信息。
6. 不允许自动执行任何交易动作。

## 13. 报警入库要求

本阶段允许创建基础报警记录表。

建议表名：

`alert_message`

该表用于记录系统报警发送请求和结果。

建议字段：

```
id
alert_type
severity
title
message
source_module
symbol
interval_value
event_time_utc
event_time_prc
status
channel
channel_response
error_message
retry_count
trace_id
created_at_utc
created_at_prc
updated_at_utc
updated_at_prc
```

字段说明：

1. `channel = hermes`
2. `status` 可为 `pending`、`sent`、`failed`、`skipped`
3. `channel_response` 必须脱敏后保存
4. `error_message` 必须脱敏后保存
5. `interval_value` 避免和数据库保留字冲突
6. `message` 保存固定模板生成的报警内容，不保存大模型输出

注意：

1. 本阶段只创建报警记录表。
2. 不创建 K线表。
3. 不创建采集事件表。
4. 不创建数据质量检查表。
5. 不创建策略表。
6. 不创建建议表。

## 14. 迁移要求

本阶段允许新增 Alembic migration，用于创建 `alert_message` 表。

要求：

1. migration 文件名应清楚表达用途。
2. 只创建 `alert_message` 表。
3. 不创建 K线相关表。
4. 不创建策略相关表。
5. 不创建建议相关表。
6. 不插入业务数据。
7. 不写真实密钥。
8. 不硬编码生产数据库连接。

禁止 Codex 自动执行：

```
alembic upgrade head
```

迁移执行由用户人工决定。

Codex 可以生成迁移文件，但不得自动连接数据库执行迁移。

## 15. 脱敏要求

脱敏工具建议放在：

`app/alerting/sanitizer.py`

必须脱敏：

1. Authorization
2. token
3. secret
4. password
5. cookie
6. webhook
7. HERMES_WEBHOOK_URL
8. HERMES_SECRET
9. 完整请求头中的认证信息
10. 完整 channel_response 中的敏感字段

`channel_response` 入库前必须脱敏。

日志输出前也必须脱敏。

禁止保存：

1. 完整 webhook URL
2. 完整 Authorization
3. 完整 token
4. 完整 secret
5. 完整 cookie
6. 完整请求头
7. 完整 `.env`

## 16. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. 报警准备发送。
2. 报警发送成功。
3. 报警发送失败。
4. dry-run 跳过真实发送。
5. Hermes disabled 跳过真实发送。
6. 脱敏后的错误原因。

禁止记录：

1. 完整 Hermes webhook。
2. Hermes secret。
3. Authorization。
4. token。
5. cookie。
6. 完整 channel_response。
7. 完整 `.env`。

## 17. 检查脚本要求

建议创建：

`scripts/check_hermes_alerting.py`

该脚本用于人工检查报警模块。

默认行为必须是 dry-run，不得真实发送。

建议支持参数：

```
--dry-run
--send-test
```

规则：

1. 默认等同于 `--dry-run`。
2. `--dry-run` 只检查模板、配置、数据库记录逻辑，不真实发送 Hermes。
3. `--send-test` 才允许真实发送测试报警。
4. `--send-test` 必须要求 `HERMES_ENABLED=true` 且 `HERMES_DRY_RUN=false`。
5. 如果配置不满足，脚本应拒绝真实发送。
6. 脚本不得被 scheduler 调用。
7. 脚本不得实现业务检测逻辑。

示例：

```
python -m scripts.check_hermes_alerting --dry-run
```

真实发送测试报警必须由用户人工执行：

```
python -m scripts.check_hermes_alerting --send-test
```

禁止该脚本：

1. 请求 Binance。
2. 执行 K线采集。
3. 执行 K线回补。
4. 执行 K线复核。
5. 写入 K线表。
6. 读取账户。
7. 下单。
8. 调用 DeepSeek。
9. 启动 scheduler。

## 18. 测试要求

建议创建：

`tests/test_hermes_alerting.py`

测试必须默认不依赖真实 Hermes。

至少覆盖：

1. AlertEvent 可构造。
2. 固定模板可以生成报警内容。
3. `kline_integrity_check_failed` 模板不会描述自动修复。
4. `kline_integrity_check_failed` 模板不会描述自动回补。
5. Hermes disabled 时不会真实发送。
6. dry-run 时不会真实发送。
7. Hermes client 发送可以被 mock。
8. 发送失败时返回 failed 状态。
9. 发送成功时返回 sent 状态。
10. `channel_response` 会脱敏。
11. 日志和错误信息不会包含 secret。
12. alert_message repository 可被 mock 测试。
13. migration 文件只创建 alert_message 表。

测试禁止：

1. 真实调用 Hermes。
2. 真实调用 Binance。
3. 真实调用 DeepSeek。
4. 真实下单。
5. 依赖生产 `.env`。
6. 依赖真实 webhook。

如果需要真实 Hermes 集成测试，必须使用显式开关，例如：

```
RUN_HERMES_INTEGRATION_TESTS=true
```

默认 `pytest` 不应发送真实报警。

## 19. 数据库影响

本阶段允许：

1. 创建 `alert_message` SQLAlchemy model。
2. 创建 `alert_message` repository。
3. 创建 `alert_message` Alembic migration。
4. 写入报警记录。
5. 更新报警发送状态。

本阶段禁止：

1. 创建 K线表。
2. 创建采集事件表。
3. 创建数据质量检查表。
4. 创建策略表。
5. 创建建议表。
6. 写入 K线数据。
7. 写入策略数据。
8. 写入建议数据。
9. 自动执行迁移。

如果 MySQL 不可用：

1. 报警服务不得假装入库成功。
2. 可以记录本地日志。
3. 如果 Hermes 配置允许，后续可以尝试发送报警。
4. 但本阶段不得引入复杂补偿队列。

## 20. Redis 影响

本阶段不得写入 Redis 业务 key。

本阶段不得创建：

`bitcoin_price`

本阶段不得实现 Redis 报警去重、冷却或限流。

报警去重、冷却或限流如需实现，应在后续具体业务报警阶段处理。

## 21. Binance 影响

本阶段不得请求 Binance。

本阶段不得创建 Binance REST Client。

本阶段不得访问：

1. `/fapi/v1/time`
2. `/fapi/v1/klines`
3. `/fapi/v1/ticker/price`
4. 任何 order/account/position 接口。

Binance 能力应在 `05_binance_rest_client.md` 中实现。

## 22. Hermes 影响

本阶段允许实现 Hermes 发送能力。

但必须满足：

1. 默认 dry-run。
2. 默认不真实发送。
3. 真实发送必须由用户显式启用。
4. 真实测试报警必须由用户人工触发。
5. 不得由 scheduler 自动触发测试报警。
6. 不得调用大模型生成报警内容。
7. 不得发送交易建议。
8. 不得发送自动下单指令。

## 23. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得创建定时任务。

本阶段不得让 scheduler 调用 `scripts/check_hermes_alerting.py`。

后续业务模块可以调用报警 service，但 scheduler 本身不在本阶段实现。

## 24. 交易安全边界

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

## 25. 安全要求

本阶段必须防止报警通道泄密。

禁止：

1. 打印完整 Hermes webhook。
2. 打印 Hermes secret。
3. 打印 Authorization。
4. 打印 token。
5. 打印 cookie。
6. 保存未脱敏 channel_response。
7. 保存完整请求头。
8. 保存完整 `.env`。
9. 在测试快照中保存真实响应。
10. 在实现说明文件中保存真实 webhook。

如果需要展示 Hermes 配置，只能展示：

1. 是否启用。
2. 是否 dry-run。
3. timeout。
4. retry 次数。
5. webhook 是否已配置，但不得展示具体值。

## 26. 交付物要求

本阶段完成后，Codex 必须交付：

1. Hermes client。
2. 报警模板模块。
3. 报警类型定义。
4. 报警 service。
5. 报警脱敏模块。
6. `alert_message` model。
7. `alert_message` repository。
8. `alert_message` Alembic migration。
9. `.env.example` 必要补充。
10. `pyproject.toml` 必要依赖补充。
11. Hermes 报警检查脚本。
12. Hermes 报警测试文件。
13. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/04_alerting_through_hermes.md`

说明文件必须描述：

1. 本模块入口。
2. Hermes client 调用方式。
3. 报警模板生成方式。
4. alert_message 入库流程。
5. channel_response 脱敏流程。
6. 异常处理流程。
7. dry-run 和真实发送的区别。
8. 本模块不负责的边界。
9. 后续哪些模块会复用本模块。

本阶段说明文件不需要描述：

1. Binance 请求流程。
2. K线采集流程。
3. K线回补流程。
4. K线复核流程。
5. 价格监控流程。
6. 策略建议流程。

原因：这些能力本阶段不实现。

## 27. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_hermes_alerting --dry-run` 可以运行成功。
2. 默认不真实发送 Hermes 报警。
3. `--send-test` 必须由用户显式执行才会真实发送。
4. Hermes disabled 时不会真实发送。
5. Hermes dry-run 时不会真实发送。
6. `pytest` 默认可以运行成功。
7. 默认测试不会真实调用 Hermes。
8. alert_message model 可以正常导入。
9. alert_message repository 可以正常导入。
10. alert_message migration 只创建报警表。
11. 未创建 K线表。
12. 未创建采集事件表。
13. 未创建数据质量检查表。
14. 未请求 Binance。
15. 未实现 K线采集。
16. 未实现 K线回补。
17. 未实现 K线复核。
18. 未实现 10s 价格监控。
19. 未调用 DeepSeek。
20. 未实现交易建议。
21. 未实现交易执行相关代码。
22. channel_response 入库前已脱敏。
23. 日志不会输出真实 Hermes webhook 或 secret。
24. `docs/implementation/04_alerting_through_hermes.md` 已创建或补齐。

## 28. 人工审查清单

合并前用户应人工检查：

1. 查看 `app/alerting/` 是否只包含报警相关模块。
2. 查看 Hermes client 是否默认 dry-run 或 disabled。
3. 查看模板是否为固定模板。
4. 查看是否存在 DeepSeek 或大模型调用。
5. 查看是否存在交易建议内容。
6. 查看是否存在 Binance 请求。
7. 查看 migration 是否只创建 `alert_message` 表。
8. 查看 channel_response 是否脱敏。
9. 查看 `.env.example` 是否没有真实 webhook 或 secret。
10. 查看检查脚本是否默认 dry-run。
11. 运行测试。
12. 运行 dry-run 检查脚本。
13. 如需真实测试，由用户人工执行 `--send-test`。

建议搜索：

```
grep -R "DeepSeek" app scripts tests migrations
grep -R "openai" app scripts tests migrations
grep -R "klines" app scripts tests migrations
grep -R "fapi" app scripts tests migrations
grep -R "market_kline" app scripts tests migrations
grep -R "collector_event_log" app scripts tests migrations
grep -R "data_quality_check" app scripts tests migrations
grep -R "bitcoin_price" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果搜索结果只是文档、注释或允许的模板名，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 29. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 请求 Binance。
2. 实现 K线采集。
3. 实现 K线回补。
4. 实现 K线复核。
5. 实现 10s 价格监控。
6. 创建 K线表。
7. 创建采集事件表。
8. 创建数据质量检查表。
9. 创建策略表。
10. 创建建议表。
11. 调用 DeepSeek。
12. 调用其他大模型生成报警。
13. 生成交易建议。
14. 实现 scheduler。
15. 自动发送真实测试报警。
16. 自动执行 Alembic migration。
17. 保存未脱敏 channel_response。
18. 打印真实 webhook。
19. 打印 secret。
20. 实现任何交易执行代码。
21. 提交真实密钥。
22. 提交真实日志。
23. 提交 `.env`。
24. 删除、清空或覆盖已有文档。
25. 把业务检测逻辑写进 `scripts`。

## 30. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 运行 dry-run 检查：

   python -m scripts.check_hermes_alerting --dry-run

4. 如用户确认需要真实测试 Hermes，再人工执行：

   python -m scripts.check_hermes_alerting --send-test

5. 人工确认没有异常删除、覆盖或越界实现。

6. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 Hermes 基础报警能力"

7. 用户自行推送分支，并进入代码审查流程。
