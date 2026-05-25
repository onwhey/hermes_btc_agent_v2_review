# 22B Hermes/微信人工执行反馈入口实现说明

## 1. 功能：自然语言生成待确认 intent

### 1.1 发起方式

用户通过 Hermes/微信入口，或本地模拟 CLI：

    python -m scripts.parse_manual_execution_intent --text "开多 BTCUSDT 成交价 60000 金额 300U 保证金 100U advice_id=ADV-1" --trigger-source cli --confirm-write

### 1.2 入口文件

`scripts/parse_manual_execution_intent.py`

入口方法：

`main()`

Hermes app 层入口：

`app/manual_execution/hermes_entry/inbound_handler.py::handle_hermes_manual_execution_inbound_payload`

### 1.3 核心 service

`app/manual_execution/hermes_entry/intent_service.py`

核心方法：

`ManualExecutionIntentService.create_manual_execution_intent`

### 1.4 调用链路

    scripts/parse_manual_execution_intent.py::main
        ↓
    app.manual_execution.hermes_entry.intent_service.py::create_manual_execution_intent
        ↓
    app.manual_execution.hermes_entry.parser.py::parse_manual_execution_intent_text
        ↓
    app.manual_execution.service.py::ManualExecutionService.record_manual_execution (dry-run only)
        ↓
    app.manual_execution.hermes_entry.intent_repository.py::ManualExecutionIntentRepository.create_intent
        ↓
    strategy_advice_manual_execution_intent

### 1.5 数据来源和解析

数据来自用户主动发送的 Hermes/微信文本或 CLI `--text`。

解析器只使用固定规则：

- `开仓 / 开多 / 开空 / 做多 / 做空` -> `open_position`
- `加仓 / 补仓` -> `add_position`
- `减仓 / 部分减仓 / 部分止盈` -> `reduce_position`
- `平仓 / 全平` -> `close_position`
- `止盈` -> `take_profit`
- `止损` -> `stop_loss`

字段解析只识别明确字段：`BTCUSDT/BTC/比特币`、多空方向、`成交价/价格/price`、`金额/名义金额/notional`、`保证金/margin`、`advice_id`、`MP-...`。

本功能不调用 DeepSeek 或其他大模型，不做自然语言写库。解析成功后也只写 pending intent，不写 22A 人工执行表。

### 1.6 入库流程

写入表：

`strategy_advice_manual_execution_intent`

写入字段包括：

- `intent_id`
- `status`
- `source_channel`
- `source_message_id`
- `source_user_id`
- `raw_text`
- `normalized_text`
- `parsed_action`
- `parsed_symbol`
- `parsed_side`
- `parsed_manual_position_id`
- `parsed_advice_id`
- `parsed_price`
- `parsed_notional_usdt`
- `parsed_margin_usdt`
- `parsed_reason`
- `parsed_note`
- `parsed_payload_json`
- `validation_status`
- `validation_error_code`
- `validation_error_message`
- `missing_fields_json`
- `dry_run_snapshot_json`
- `expires_at_utc`
- `trace_id`
- `created_at_utc`
- `updated_at_utc`
- `is_manual`
- `auto_trading_allowed`

唯一键：

`intent_id`

幂等规则：

创建 intent 不按文本去重；用户确认时按 `intent_id` 控制，不允许重复写 22A 表。

字段类型控制：

`raw_text`、`normalized_text`、`parsed_payload_json`、`missing_fields_json`、`dry_run_snapshot_json`、
`parsed_reason`、`parsed_note`、`validation_error_message` 使用 `Text` 类字段，不参与普通索引，
避免 MySQL utf8mb4 下行内 `VARCHAR` 过大导致建表失败。

service 层仍会对写入内容做保守截断，避免把无限膨胀上下文写入数据库。

### 1.7 配置

读取配置：

- `MANUAL_EXECUTION_HERMES_ENTRY_ENABLED`
- `MANUAL_EXECUTION_HERMES_REPLY_SEND_ENABLED`
- `MANUAL_EXECUTION_INTENT_EXPIRE_MINUTES`
- `MANUAL_EXECUTION_FEE_RATE`
- `MANUAL_EXECUTION_RECEIPT_SEND_ENABLED`
- `HERMES_SECRET`

默认：

- Hermes 入口关闭。
- Hermes 回复真实发送关闭。
- intent 10 分钟过期。

### 1.8 Hermes 回复

由 22B service 构造中文确认消息，并通过统一 alerting 发送：

`app.alerting.service.py::send_alert`

报警类型：

`manual_execution_intent`

真实发送由 `MANUAL_EXECUTION_HERMES_REPLY_SEND_ENABLED` 控制。发送失败不回滚已提交的 intent。

### 1.9 异常处理

解析失败：

`parser.py::parse_manual_execution_intent_text` 返回错误码，`intent_service.py::create_manual_execution_intent` 写入 `parse_failed` 或 `validation_failed` intent，并返回中文提醒。

字段缺失：

`intent_service.py::create_manual_execution_intent` 不调用 22A 写库，只写 blocked intent 并返回中文缺字段提醒。

manual_position_id / advice_id 错误：

22B 只调用 22A dry-run 校验。22A 返回 blocked 时，22B 写入 `validation_failed` intent，不写 execution_record / manual_position。

Hermes 回复失败：

intent 已提交后再发送回复；回复失败只记录日志/返回状态，不回滚 intent。

### 1.10 本功能不负责

- 不自动下单。
- 不读取 Binance 账户。
- 不同步真实持仓。
- 不修改 K 线表。
- 不修改 strategy_advice 生命周期状态。
- 不调用 DeepSeek 或其他大模型。
- 不做纠错、作废、修改、删除执行流水。
- 不做资金费率、滑点字段、浮盈浮亏。

## 2. 功能：确认 MEI 后调用 22A 写库

### 2.1 发起方式

用户回复：

    确认 MEI-XXXXXXXXXXXX

或本地模拟：

    python -m scripts.confirm_manual_execution_intent --intent-id MEI-XXXXXXXXXXXX --action confirm_intent --trigger-source cli --confirm-write

### 2.2 入口文件

`scripts/confirm_manual_execution_intent.py`

入口方法：

`main()`

### 2.3 核心 service

`app/manual_execution/hermes_entry/intent_service.py`

核心方法：

`ManualExecutionIntentService.confirm_manual_execution_intent`

### 2.4 调用链路

    scripts/confirm_manual_execution_intent.py::main
        ↓
    app.manual_execution.hermes_entry.intent_service.py::confirm_manual_execution_intent
        ↓
    app.manual_execution.hermes_entry.intent_repository.py::ManualExecutionIntentRepository.get_intent_by_id
        ↓
    app.manual_execution.service.py::ManualExecutionService.record_manual_execution
        ↓
    app.manual_execution.repository.py::ManualExecutionRepository
        ↓
    strategy_advice_manual_position / strategy_advice_execution_record
        ↓
    app.manual_execution.hermes_entry.intent_repository.py::ManualExecutionIntentRepository.mark_status

### 2.5 数据写入

读取：

- `strategy_advice_manual_execution_intent`
- 22A service 读取 `strategy_advice`
- 22A service 按需读取 `strategy_advice_manual_position`

写入：

- `strategy_advice_manual_execution_intent`
- 用户确认后由 22A service 写入 `strategy_advice_manual_position`
- 用户确认后由 22A service 写入 `strategy_advice_execution_record`
- 需要发送提醒时通过 alerting 写入 `alert_message`

22B 不直接写 22A 两张表，必须经过 `ManualExecutionService.record_manual_execution`。

### 2.6 幂等规则

`pending_confirmation` 才能确认。

如果 intent 已经是 `executed`，重复确认返回已执行提示，不再次调用 22A service 写库。

如果 intent 是 `cancelled`、`expired`、`validation_failed`、`execution_failed` 等状态，确认会被阻断。

### 2.7 过期规则

默认 10 分钟过期，由 `MANUAL_EXECUTION_INTENT_EXPIRE_MINUTES` 控制。

确认时如果 `expires_at_utc <= now_utc()`，service 将 pending intent 标记为 `expired`，不调用 22A service。

时间处理统一使用 UTC aware datetime，不手写 PRC +8 转换。

### 2.8 trigger_source

CLI 模拟入口只接受 `--trigger-source cli`。

Hermes/微信确认后传给 22A service 的执行记录 `trigger_source` 为 `hermes`。

本功能不涉及 K 线写入，因此不涉及 K 线 `data_source`。

### 2.9 异常处理

intent 不存在：

`confirm_manual_execution_intent` 返回中文提醒，提示重新输入正确 `MEI-xxx`，不写 22A 表。

已取消：

返回 blocked，不写 22A 表。

已过期：

更新 intent 状态为 `expired`，不写 22A 表。

22A 写库失败或 blocked：

intent 标记为 `execution_failed`，不伪装成功。

22A 写库成功但 22A Hermes 回执失败：

22A 返回 `database_written=true` 时，22B 仍将 intent 标记为 `executed`，不重复写库。

## 3. 功能：取消 MEI

### 3.1 发起方式

用户回复：

    取消 MEI-XXXXXXXXXXXX

或本地模拟：

    python -m scripts.confirm_manual_execution_intent --intent-id MEI-XXXXXXXXXXXX --action cancel_intent --trigger-source cli --confirm-write

### 3.2 调用链路

    scripts/confirm_manual_execution_intent.py::main
        ↓
    app.manual_execution.hermes_entry.intent_service.py::cancel_manual_execution_intent
        ↓
    app.manual_execution.hermes_entry.intent_repository.py::ManualExecutionIntentRepository.mark_status
        ↓
    strategy_advice_manual_execution_intent

取消只更新 intent，不调用 22A service，不写人工执行流水。

## 4. Hermes 入站接入

本仓库当前没有 HTTP router 框架。22B 提供 app 层适配函数：

`app/manual_execution/hermes_entry/inbound_handler.py::handle_hermes_manual_execution_inbound_payload`

该函数负责：

- 校验传入的 `provided_secret` 与 `HERMES_SECRET`。
- 转换为 `InboundManualExecutionMessage`。
- 调用 `handle_inbound_manual_execution_message`。

真实 Web route 可在后续已有 HTTP 框架中薄封装该函数。route 层不得写业务表，不得直接发送 Hermes，不得直接调用 22A repository。

## 5. 数据库 migration

新增 migration：

`migrations/versions/20260531_22b_manual_execution_intent.py`

创建表：

`strategy_advice_manual_execution_intent`

本 migration 不插入业务数据，不修改 K 线表，不修改 15-21 阶段表结构。

## 6. 测试

新增测试目录：

`tests/manual_execution_hermes`

覆盖：

- 自然语言开仓解析成功，只生成 pending intent，不写 22A 表。
- 确认 MEI 后调用 22A 写库成功。
- 重复确认不重复写库。
- 取消后不能确认。
- 过期后不能确认。
- 缺少 price / advice_id / notional_usdt 等字段时 blocked。
- manual_position_id 错误时 blocked。
- add_position 保证金 0U 可解析为 `margin_usdt=0`。
- close_position 不要求 `notional_usdt` / `margin_usdt`。
- 22B parser/service 不引用大模型供应商。

测试命令：

    .\.venv\Scripts\python.exe -m pytest tests\manual_execution tests\manual_execution_hermes -q
    .\.venv\Scripts\python.exe -m pytest tests -q

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不真实发送 Hermes，不调用 DeepSeek，不访问交易接口。

## 7. 本阶段明确没有实现

- 没有自动交易。
- 没有读取 Binance 账户、订单、持仓、杠杆、保证金接口。
- 没有同步真实持仓。
- 没有 Hermes 自然语言直接写 execution_record / manual_position。
- 没有 Admin 后台。
- 没有纠错、作废、修改、删除执行流水。
- 没有资金费率、滑点字段、浮盈浮亏。
- 没有 reduce_ratio。
- 没有大模型调用。
- 没有修改 strategy_advice 生命周期状态。
- 没有修改正式 K 线表。
