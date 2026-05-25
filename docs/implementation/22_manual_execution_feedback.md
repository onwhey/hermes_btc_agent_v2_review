# 22A 人工执行反馈基础版实现说明

## 1. 功能：记录人工执行反馈

### 1.1 发起方式

用户手动执行：

    python -m scripts.record_manual_execution --action open_position --advice-id ADV-xxx --symbol BTCUSDT --side long --price 60000 --notional-usdt 300 --margin-usdt 100 --trigger-source cli --confirm-write

### 1.2 入口文件

`scripts/record_manual_execution.py`

入口方法：

`main()`

### 1.3 核心 service

`app/manual_execution/service.py`

核心方法：

`record_manual_execution()`

### 1.4 调用链路

    scripts/record_manual_execution.py::main
        ↓
    app/manual_execution/service.py::record_manual_execution
        ↓
    app/manual_execution/service.py::ManualExecutionService.record_manual_execution
        ↓
    app/manual_execution/repository.py::ManualExecutionRepository.get_advice_by_id
        ↓
    app/manual_execution/calculations.py::calculate_open_position
        或 app/manual_execution/calculations.py::calculate_existing_position_action
        ↓
    app/manual_execution/repository.py::ManualExecutionRepository.create_manual_position
        或 app/manual_execution/repository.py::ManualExecutionRepository.update_manual_position_from_payload
        ↓
    app/manual_execution/repository.py::ManualExecutionRepository.create_execution_record

关闭仓位后追加：

    app/manual_execution/service.py::ManualExecutionService._send_close_receipt
        ↓
    app/manual_execution/receipt.py::render_manual_execution_close_receipt
        ↓
    app/alerting/service.py::send_alert
        ↓
    app/storage/mysql/repositories/alert_message_repository.py::create_pending_alert_message
        ↓
    app/alerting/hermes_client.py::HermesClient.send_alert_message
        ↓
    app/storage/mysql/repositories/alert_message_repository.py::update_alert_message_result

### 1.5 读取配置

- `MANUAL_EXECUTION_FEE_RATE`，默认 `0.0002`。
- `MANUAL_EXECUTION_RECEIPT_SEND_ENABLED`，默认 `false`。
- Hermes 真实发送仍受 `HERMES_ENABLED`、`HERMES_DRY_RUN`、`HERMES_WEBHOOK_URL` 等统一配置控制。

### 1.6 外部接口

本功能不请求 Binance。
本功能不读取 Binance 账户。
本功能不同步真实仓位。
本功能不调用 DeepSeek 或其他大模型。
本功能只在关闭人工仓位后通过统一 alerting 模块可选提交 Hermes 回执。

### 1.7 数据库读写

读取：

- `strategy_advice`：校验 `advice_id` 必须存在。
- `strategy_advice_lifecycle_review`：如果可由 `advice_id` 唯一推导，则写入 `review_id`。
- `strategy_advice_trade_setup`：如果可由 `advice_id` 唯一推导，则写入 `setup_id`。
- `strategy_advice_manual_position`：查找目标 open 人工仓位。
- `strategy_advice_execution_record`：关闭回执渲染操作链。

写入：

- `strategy_advice_manual_position`。
- `strategy_advice_execution_record`。
- `alert_message`，仅用于人工执行回执或 manual_position_id 错误提醒。

本功能不写入 `market_kline_4h`。
本功能不写入 `market_kline_1d`。
本功能不修改 `strategy_advice` 生命周期状态。

### 1.8 数据流

数据来源是用户主动通过 CLI 输入的结构化字段，不支持自然语言写库。

输入先经过 `ManualExecutionRequest`，再由 service 校验：

- `advice_id` 必填且必须存在。
- `symbol` 非空。
- `side` 只能是 `long` 或 `short`。
- `execution_action` 只能是 `open_position`、`add_position`、`reduce_position`、`close_position`、`take_profit`、`stop_loss`。
- `trigger_source` 只能是 `cli`。
- 所有金额、价格、数量和手续费计算使用 `Decimal`，显式拒绝 float。

计算规则在 `app/manual_execution/calculations.py`：

- `open_position` 初始化平均成本、当前数量、当前成本基准、保证金基准、手续费和净已实现盈亏。
- `add_position` 使用加权平均成本，`margin_usdt=0` 时保证金基准不变，有效杠杆上升。
- `reduce_position` 不改变平均成本，不调整保证金基准，按价格差乘数量计算已实现盈亏。
- `close_position` / `take_profit` / `stop_loss` 按当前剩余数量全平，并将状态改为 `closed`。

最终通过 repository 写入两张表。唯一键：

- `strategy_advice_manual_position.manual_position_id`。
- `strategy_advice_execution_record.execution_id`。

幂等规则：

22A 不把同一 `advice_id` 设为唯一；同一 advice 可被多条执行流水引用。重复执行 CLI 会追加新的用户反馈记录，不自动合并、不自动修正、不自动删除。

### 1.9 Hermes 回执

由 `app/manual_execution/service.py::ManualExecutionService._send_close_receipt` 决定是否生成回执。

使用固定模板类型：

- `manual_execution_receipt`
- `manual_execution_error`

严重级别：

- 关闭回执：`notice`
- manual_position_id 错误：`warning`

回执内容包含：

- `manual_position_id`
- `symbol / side`
- 操作链概要
- 开仓价、平均成本、平仓价
- 总开仓金额、总退出金额
- `margin_basis_usdt`
- `effective_leverage`
- 总手续费
- 账面已实现盈亏
- 实际净已实现盈亏
- 按保证金收益率
- 关联 `advice_id` 列表

`channel_response` 由现有 alerting repository 脱敏保存。

如果执行记录和人工仓位已写入，但 Hermes 提交失败：

- 不回滚 `strategy_advice_manual_position`。
- 不回滚 `strategy_advice_execution_record`。
- `alert_message` 记录失败结果或日志记录失败原因。
- CLI 输出 `数据库已写入，但 Hermes 回执失败`。

## 2. 功能：查询 open 人工仓位

### 2.1 发起方式

用户手动执行：

    python -m scripts.check_manual_positions --symbol BTCUSDT --status open --trigger-source cli

### 2.2 入口文件

`scripts/check_manual_positions.py`

入口方法：

`main()`

### 2.3 核心 service

`app/manual_execution/service.py`

核心方法：

`list_manual_positions()`

### 2.4 调用链路

    scripts/check_manual_positions.py::main
        ↓
    app/manual_execution/service.py::list_manual_positions
        ↓
    app/manual_execution/service.py::ManualExecutionService.list_manual_positions
        ↓
    app/manual_execution/repository.py::ManualExecutionRepository.list_manual_positions

### 2.5 数据访问

本功能不请求外部接口。
本功能读取 `strategy_advice_manual_position`。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek。
本功能不涉及 scheduler。
本功能不修改正式 K 线表。

输出最小字段：

- `manual_position_id`
- `symbol`
- `side`
- `status`
- `avg_entry_price`
- `current_quantity_base_asset`
- `current_cost_basis_usdt`
- `margin_basis_usdt`
- `effective_leverage`
- `opened_at_utc`
- `opened_by_advice_id`

## 3. 迁移

迁移文件：

`migrations/versions/20260530_22a_manual_execution_feedback.py`

创建表：

- `strategy_advice_manual_position`
- `strategy_advice_execution_record`

迁移不插入业务数据，不修改 K 线表，不删除已有表，不破坏 15-21 阶段结构。

Decimal 字段使用 `Numeric(38, 18)`。

## 4. 异常处理

参数校验失败：

- 发生在 `app/manual_execution/service.py::ManualExecutionService._validate_common_request` 或 `app/manual_execution/calculations.py`。
- service 返回 `blocked`。
- 不写 `execution_record`。
- 不更新 `manual_position`。

`advice_id` 不存在：

- 发生在 `ManualExecutionRepository.get_advice_by_id` 返回空时。
- service 返回 `blocked`。
- 不写任何 22A 业务表。

`manual_position_id` 不存在、已 closed、symbol/side 不匹配：

- 发生在 `ManualExecutionService._resolve_manual_position`。
- service 返回 `blocked`。
- 不写 `execution_record`。
- 不更新 `manual_position`。
- confirm-write 模式下通过 `manual_execution_error` 生成中文错误提醒；dry-run 不写库不发提醒。

数据库写入失败：

- repository 或 session 抛出异常。
- service rollback。
- 返回 `failed`。
- 不发送关闭回执。

Hermes 回执失败：

- 发生在 `ManualExecutionService._send_alert_and_commit`。
- 不回滚已提交的人工仓位和流水。
- 返回结果标记 `receipt_failed=true`。
- CLI 明确提示数据库已写入但回执失败。

本功能不重试业务写入。
本功能不允许 `partial_success` 写入半条执行记录。
本功能不自动修复数据。
本功能不删除、修改、作废既有执行记录。

## 5. 测试

对应测试文件：

`tests/manual_execution/test_manual_execution_service.py`

覆盖：

- open_position 成功写入。
- advice_id 必填。
- open_position `margin_usdt <= 1` blocked。
- add_position `margin_usdt=0` 和 `margin_usdt>0`。
- wrong manual_position_id blocked 并触发错误提醒。
- reduce_position 不改变平均成本、不调整保证金基准。
- long / short 盈亏公式。
- 超量 reduce blocked。
- close_position / take_profit / stop_loss 全平。
- 手续费累计和净已实现盈亏。
- advice_id 可复用。
- dry-run 不写库。
- Hermes 回执关闭不真实发送。
- Hermes 回执失败不回滚数据库。
- check_manual_positions 查询 open 仓位。
- 多笔 open 仓位时不猜 manual_position_id。
- float 输入 blocked，确保 Decimal 口径。

默认 pytest 不请求 Binance，不连接真实 MySQL，不连接 Redis，不真实发送 Hermes，不调用 DeepSeek，不访问交易接口。

人工检查命令：

    python -m scripts.record_manual_execution --help
    python -m scripts.check_manual_positions --help
    python -m pytest tests/manual_execution/test_manual_execution_service.py -q

## 6. 本功能明确不负责

- 不自动交易。
- 不读取 Binance 账户。
- 不同步真实仓位。
- 不接 Hermes 自然语言输入。
- 不实现 Admin 后台。
- 不实现纠错、作废、修改、删除。
- 不计算资金费率。
- 不保存滑点字段。
- 不计算浮盈浮亏。
- 不实现 reduce_ratio。
- 不调用 DeepSeek / OpenAI / Claude 等大模型。
- 不修改 `strategy_advice` 生命周期状态。
- 不修改 K 线表。

## 7. 危险关键词说明

本阶段代码和表名必须出现 `manual_position`、`open_position`、`close_position`、`effective_leverage` 等词，因为 plan 明确要求人工仓位反馈和有效杠杆字段。

这些字段只表示用户主动反馈的人工执行记录，不是交易所真实仓位，不读取账户，不调用交易接口，不自动下单，不自动平仓。
