# 26A 策略链路运行观测实现说明

## 1. 功能：最近 N 根 4h K线策略链路只读观测

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.check_strategy_pipeline_status \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 5
```

本功能只允许人工 CLI 检查，不接 scheduler，不修改 25 pipeline 调度链路。

### 1.2 入口文件

入口文件：

`scripts/check_strategy_pipeline_status.py`

入口方法：

`main()`

脚本只解析参数、打开 MySQL session、调用 app service、打印中文报告并返回退出码。

脚本不直接写 SQL，不调用 25 pipeline，不调用模型 provider，不发送 Hermes，不修改正式 K线表，不读取账户或持仓，不生成订单，不自动交易。

### 1.3 核心 service

核心 service 文件：

`app/strategy_observability/service.py`

核心方法：

`StrategyPipelineObservabilityService.check_strategy_pipeline_status()`

便捷入口：

`app/strategy_observability/service.py::check_strategy_pipeline_status`

### 1.4 核心调用链路

```text
scripts/check_strategy_pipeline_status.py::main
    ↓
app/strategy_observability/service.py::check_strategy_pipeline_status
    ↓
app/strategy_observability/service.py::StrategyPipelineObservabilityService.check_strategy_pipeline_status
    ↓
app/strategy_observability/repository.py::list_recent_closed_kline_slots
    ↓
app/strategy_observability/repository.py::list_pipeline_runs_for_slots
    ↓
app/strategy_observability/repository.py::load_link_records_for_pipeline_runs
    ↓
app/strategy_observability/types.py::format_strategy_pipeline_status_report_lines
```

## 2. 读取配置

26A 只读取非敏感开关，用于解释 blocked 是否合理：

- `STRATEGY_PIPELINE_ENABLED`
- `STRATEGY_PIPELINE_SCHEDULER_ENABLED`
- `STRATEGY_EVIDENCE_AGGREGATION_ENABLED`
- `STRATEGY_PIPELINE_REAL_MODEL_ENABLED`
- `STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST`
- `MODEL_REVIEW_REAL_MODEL_ENABLED`
- `STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED`
- `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED`

`当前真实模型` 只有在 pipeline 真实模型开关、模型总闸和成本确认都为 true 时显示为开启。

`当前真实 Hermes` 只有在 pipeline 通知发送开关和 advice 通知发送开关都为 true 时显示为开启。

本功能不读取密钥，不打印 `.env`，不输出 webhook、token、secret 或数据库密码。

## 3. 数据来源

本功能不请求外部接口。

本功能不请求 Binance。

本功能不读取 Redis。

本功能不发送 Hermes。

本功能不调用 DeepSeek、OpenAI、Claude 或其他大模型。

本功能不读取账户、订单、持仓、杠杆或保证金相关数据。

只读 MySQL 表：

- `market_kline_4h`：按 `symbol + interval_value` 读取最近 N 根正式已收盘 4h K线 slot。
- `strategy_pipeline_event_log`：按 `symbol + base_interval + higher_interval + kline_slot_utc` 读取每个 slot 的 pipeline 记录。
- `strategy_signal_run`：核验 SSR 是否存在。
- `strategy_evidence_aggregation_result`：核验 SEA 是否存在。
- `analysis_material_pack`：核验 AMP 是否存在。
- `model_review_aggregation_run`：核验 MRAG 是否存在。
- `strategy_advice_lifecycle_review`：核验 ADVR 是否存在。

第一版不依赖 `alert_message` 判断真实发送状态，因为 25 pipeline event 已保存 `hermes_real_sent` 与 `notification_status`。Hermes 关闭导致未真实发送不会被判定为失败。

### 3.1 K线 slot 观测边界

26A 第一版只检查“已入库 K线对应的 pipeline 状态”：

- `app/strategy_observability/repository.py::list_recent_closed_kline_slots` 只从 `market_kline_4h` 读取最近 N 根已入库、已收盘 4h K线 slot。
- 26A 不识别“最新理论上应收盘但 `market_kline_4h` 缺失”的 K线 slot。
- 26A 不请求 Binance REST，不通过 server time 或本地时间推断理论应收盘 slot。
- 26A 输出中的 `missing` 只表示“该 4h K线已入库，但未找到对应 25 pipeline”，不表示“K线本身缺失”。
- K线本身是否漏采、是否连续，仍由 07/11 K线质量检查负责。

## 4. 数据写入

本功能不写入数据库。

本功能不写入：

- `market_kline_4h`
- `strategy_pipeline_event_log`
- `strategy_signal_run`
- `strategy_evidence_aggregation_result`
- `analysis_material_pack`
- `model_review_aggregation_run`
- `strategy_advice_lifecycle_review`
- `alert_message`

本功能不提交事务，不生成 migration，不修改任何业务表结构。

## 5. Slot 级判断规则

26A 按最近 N 根正式 4h K线 slot 判断，而不是只查最近一条 pipeline。

这些 slot 全部来自 `market_kline_4h` 已入库记录；26A 不生成理论 K线时间轴，也不扩展为新的 K线质量检查模块。

每个 slot 的状态分类：

- `healthy`：pipeline 为 `success`，且 SP / SSR / SEA / AMP / MRAG / ADVR 关键 ID 均可确认存在。
- `expected_blocked`：pipeline 为 `blocked`，停在 `20c_19_20a_model_review`，错误码为模型结果缺失或真实模型关闭类错误，且当前真实模型相关开关处于安全关闭状态。
- `failed`：pipeline 为 `failed`，或 blocked 但不属于当前配置下的合理阻断。
- `missing`：该 4h K线已入库，但没有对应的 25 pipeline；不代表理论应存在的 K线缺失。
- `duplicate`：同一 slot 有多条 pipeline_run。
- `unknown`：pipeline 状态无法归类，或 success 但关键链路 ID 不完整。

合理阻断第一版支持以下错误码：

- `no_model_review_result`
- `real_model_disabled`
- `model_review_expired_but_real_model_disabled`
- `model_review_real_model_disabled`
- `model_review_scheduler_worker_disabled`
- `model_review_auto_run_disabled`
- `cli_real_model_cost_not_confirmed`

如果真实模型开关已全部开启，`no_model_review_result` 不会判定为 `expected_blocked`，而是判定为 `failed`，提示需要排查。

## 6. 输出与退出码

CLI 输出中文报告，包括：

- 观测范围：26A 只观测已入库 K线对应的策略链路。
- 质量边界：K线本身是否漏采、是否连续，仍由 07/11 K线质量检查负责；26A 不请求 Binance REST 推断理论应收盘 slot。
- 汇总计数。
- 当前真实模型 / 当前真实 Hermes 开关状态。
- 关键配置开关。
- 每个 slot 的状态、说明、SP / SSR / SEA / AMP / MRAG / ADVR。
- `pipeline_status`
- `current_step`
- `real_model_called`
- `hermes_real_sent`
- `error_code`
- `error_message`
- `blocked 是否合理`

报告最后固定输出：

```text
本检查只用于策略链路运行观测，不是交易建议；不自动交易，不读取账户，不生成订单。
```

退出码：

- `0`：所有 slot 均为 `healthy` 或 `expected_blocked`。
- `1`：存在 `missing`、`failed`、`duplicate` 或 `unknown`。
- `2`：参数错误或数据库查询失败。

## 7. 异常处理

参数错误发生在：

`scripts/check_strategy_pipeline_status.py::_validate_request`

处理方式：

- 打印中文参数错误。
- 返回 exit_code=2。
- 不打开 MySQL 写入流程。
- 不调用模型、不发送 Hermes、不修改数据。

数据库查询失败可能发生在：

- `app/strategy_observability/repository.py::list_recent_closed_kline_slots`
- `app/strategy_observability/repository.py::list_pipeline_runs_for_slots`
- `app/strategy_observability/repository.py::load_link_records_for_pipeline_runs`

捕获层：

`scripts/check_strategy_pipeline_status.py::main`

处理方式：

- 打印“数据库查询失败或观测失败”。
- 返回 exit_code=2。
- 不重试。
- 不写事件日志。
- 不发送 Hermes。
- 不触发 25 pipeline。
- 不允许 partial_success，因为本功能没有写入事务。

业务观测异常如 `missing`、`duplicate`、`failed`、`unknown` 不抛异常，作为报告状态输出，并返回 exit_code=1。

## 8. Scheduler、scripts、trigger_source 与 data_source

本功能涉及 scripts：

- `scripts/check_strategy_pipeline_status.py`

该脚本只允许人工 CLI 调用，不允许 scheduler 调用。

本功能不涉及 `trigger_source` 写入，因为它不写正式 K线，也不写 pipeline 事件。

本功能不涉及 `data_source`，不会写 `market_kline_4h`，不会使用 `binance_rest_by_cli` 或 `binance_rest_by_scheduler`。

## 9. Redis、Hermes、大模型与交易边界

Redis：

本功能不读取 Redis，不写入 Redis，不获取任何锁。

Hermes：

本功能不调用 `app/alerting`，不写 `alert_message`，不发送真实 Hermes。输出中的 `hermes_real_sent` 只来自已存在 pipeline 审计记录。

大模型：

本功能不调用 stage 19，不调用 20C worker，不调用 DeepSeek、OpenAI、Claude 或任何模型 provider。输出中的 `real_model_called` 只来自已存在 pipeline 审计记录。

交易：

本功能不读取账户，不读取持仓，不生成订单，不下单，不平仓，不调仓，不撤单，不调整杠杆，不自动交易。

## 10. 对应测试

新增测试文件：

`tests/strategy_observability/test_strategy_pipeline_status.py`

覆盖内容：

- 安全模式 `no_model_review_result` 判定为 `expected_blocked`。
- 真实模型开启时 `no_model_review_result` 不判定为 `expected_blocked`，而是 `failed`。
- 有 K线但缺 pipeline 判定为 `missing`。
- 同一 slot 多个 pipeline 判定为 `duplicate`。
- pipeline failed 判定为 `failed`。
- 输出包含 SP / SSR / SEA / AMP / MRAG / ADVR。
- 输出包含 `real_model_called` / `hermes_real_sent`。
- CLI 参数错误返回 exit_code=2。
- monkeypatch 模型和 Hermes 发送入口为 forbidden 后，26A service 仍不会触发它们。

默认测试不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用真实模型，不访问交易接口。

人工运行：

```bash
python -m pytest tests/strategy_observability -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/scheduler -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_advice -q
```

本地使用项目虚拟环境时：

```bash
.\.venv\Scripts\python.exe -m pytest tests\strategy_observability -q
```

## 11. 本功能不负责

26A 不负责：

- 不新增策略。
- 不修改 23B/23C/23D/23E/23F 算法。
- 不修改 18 材料包生成逻辑。
- 不修改 19 prompt 或 provider。
- 不修改 20 模型审查聚合/worker 逻辑。
- 不修改 21 建议生命周期或通知逻辑。
- 不修改 25 pipeline 调度逻辑。
- 不调用真实模型。
- 不发送 Hermes。
- 不读取账户或持仓。
- 不生成订单。
- 不自动交易。

## 12. 字段确认与人工审查提示

已确认使用的关键字段：

- `market_kline_4h.open_time_utc`
- `market_kline_4h.open_time_ms`
- `strategy_pipeline_event_log.pipeline_run_id`
- `strategy_pipeline_event_log.kline_slot_utc`
- `strategy_pipeline_event_log.status`
- `strategy_pipeline_event_log.current_step`
- `strategy_pipeline_event_log.strategy_signal_run_id`
- `strategy_pipeline_event_log.strategy_evidence_aggregation_id`
- `strategy_pipeline_event_log.material_pack_id`
- `strategy_pipeline_event_log.review_aggregation_run_id`
- `strategy_pipeline_event_log.review_id`
- `strategy_pipeline_event_log.notification_status`
- `strategy_pipeline_event_log.real_model_called`
- `strategy_pipeline_event_log.hermes_real_sent`
- `strategy_pipeline_event_log.error_code`
- `strategy_pipeline_event_log.error_message`
- `strategy_signal_run.run_id`
- `strategy_evidence_aggregation_result.aggregation_id`
- `analysis_material_pack.material_pack_id`
- `model_review_aggregation_run.review_aggregation_run_id`
- `strategy_advice_lifecycle_review.review_id`

无法确认项：无。第一版未读取 `alert_message`，因为 25 pipeline event 已保存真实发送摘要字段。

危险关键词说明：

- 代码中的 `order_by(...)` 只表示 SQLAlchemy 查询排序，不是交易订单能力。
- 文档中的“订单”只出现在禁止说明和边界声明中。
- 本功能未新增 Binance private endpoint、账户、持仓、杠杆、listenKey 或自动交易能力。

## 13. project_invariants 自检

本实现不违反 `docs/rules/project_invariants.md`：

- 自动交易：未实现。
- K线数据来源：未修改，仅只读正式 4h K线 slot。
- manual_repair / human_edit / manual_input / system_repair：未引入业务路径。
- REST / WebSocket 边界：未请求 REST 或 WebSocket。
- trigger_source / data_source：不写正式 K线，不涉及写入映射。
- scripts 边界：脚本只做参数解析、session 创建、调用 service 和输出。
- scheduler 边界：不接 scheduler。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：未发送 Hermes。
- MySQL / Redis 边界：MySQL 只读；Redis 不使用。
- 敏感信息提交：未提交密钥或真实日志。
