# 18 Strategy Aggregation Material Pack 实现说明

## 1. 功能：策略聚合与材料包构建

### 1.1 发起方式

手动验证：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <strategy_signal_run.run_id> \
  --trigger-source cli
```

确认写入：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <strategy_signal_run.run_id> \
  --trigger-source cli \
  --confirm-write
```

第 17 后置自动触发：

```text
app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_aggregation_post_signal_if_needed
    -> app/scheduler/jobs/strategy_aggregation_job.py::run_strategy_aggregation_after_signal_job
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
```

自动触发只在第 17 返回 `success` / `partial_success` 且
`STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true` 时发生。
`waiting_upstream` / `blocked` / `failed` / `skipped` 不触发第 18。

### 1.2 入口文件

手动入口：

`scripts/run_strategy_aggregation.py`

入口方法：

`main()`

核心 service：

`app/strategy/aggregation/service.py`

核心方法：

`StrategyAggregationService.run_strategy_aggregation()`

## 2. 输入和边界

第 18 只读取：

1. `strategy_signal_run`
2. `strategy_signal_result`
3. `snapshot_id` 对应的 `MarketContextSnapshot`
4. snapshot 还原出的 `market_kline_4h` / `market_kline_1d` 已收盘窗口

第 18 不请求外部接口。
第 18 不读取 Redis。
第 18 不写入 Redis。
第 18 不修改 `market_kline_4h`。
第 18 不修改 `market_kline_1d`。
第 18 不调用 DeepSeek、GPT、Claude 或其他大模型。
第 18 不生成最终交易建议。
第 18 不读取账户、订单、持仓或 API 私钥。
第 18 不自动交易。

第 18 与前后阶段边界：

1. 与第 17：第 17 仍只负责策略信号 scheduler 编排。第 18 不改变第 17 target 绑定、event log 语义或第 17 调用第 16 的方式。
2. 与第 16：第 18 只读取已落库的第 16 运行结果，不重新运行 StrategySignalService。
3. 与第 15：第 18 只调用 snapshot repository 的只读还原能力，不重新生成 MarketContextSnapshot。
4. 与第 19：第 18 只生成 `analysis_material_pack` 和问题清单，不调用大模型。
5. 与第 20：第 18 不进入 advice lifecycle，不创建、更新或关闭最终建议。

`candidate_direction` 只是聚合层候选方向，不是 `final_advice`。

## 3. 核心调用链路

```text
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_strategy_signal_run
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.list_strategy_signal_results
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.restore_snapshot_kline_windows
    -> app/strategy/aggregation/material_builder.py::build_future_leakage_guard
    -> app/strategy/aggregation/service.py::_classify_strategy_results
    -> app/strategy/aggregation/service.py::_build_aggregation_decision
    -> app/strategy/aggregation/candidate_scenario_builder.py::build_candidate_scenarios
    -> app/strategy/aggregation/material_builder.py::build_material_pack
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.create_aggregation_run
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.create_material_pack
```

Hermes 开启时额外调用：

```text
app/strategy/aggregation/service.py::_send_or_skip_hermes
    -> app/strategy/aggregation/hermes_formatter.py::build_strategy_aggregation_visible_body
    -> app/alerting/service.py::send_alert
```

## 4. 新增表

Migration：

`migrations/versions/20260518_18_create_strategy_aggregation_material_pack.py`

ORM：

`app/storage/mysql/models/strategy_aggregation.py`

### 4.1 strategy_aggregation_run

用途：保存一次第 18 聚合运行、候选方向、风险门禁、冲突、证据、候选场景和通知状态。

关键字段：

```text
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol / base_interval / higher_interval
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
status
candidate_direction
risk_level
risk_gate_status
conflict_level
input_*_count
effective_strategy_count
long_strategies_json
short_strategies_json
neutral_strategies_json
risk_strategies_json
candidate_scenarios_json
summary_json
evidence_json
conflict_json
validation_plan_json
message / error_message
hermes_* fields
created_at_utc / updated_at_utc
```

幂等唯一键：

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

### 4.2 analysis_material_pack

用途：保存第 19 使用的确定性数学材料包和问题清单。

关键字段：

```text
material_pack_id
aggregation_run_id
strategy_signal_run_id
snapshot_id
material_schema_version
indicator_version
material_json
question_json
validation_plan_json
summary_json
data_window_json
future_leakage_guard_json
status
created_at_utc / updated_at_utc
```

本表不保存大模型输出，不保存最终建议，不保存账户或交易执行数据。

## 5. 聚合规则

第一版是确定性规则：

1. `strategy_signal_run.status` 只接受 `success` / `partial_success`。
2. `strategy_signal_result.strategy_status=success/no_signal` 视为可参与聚合。
3. `not_implemented` 不导致聚合失败，会计入 `partial_success` 背景。
4. `failed` / `invalid` 不参与方向投票，但写入质量问题分组。
5. `bullish_bias` 归入多头候选证据。
6. `bearish_bias` 归入空头候选证据。
7. `not_applicable` 且带风险等级的策略归入风险策略。
8. 高/极高风险优先否决方向，候选方向降级为 `wait` 或 `stop_trading`。
9. 多空明显冲突时 `conflict_level=high`，候选方向倾向 `wait`。
10. 有效策略数量为 0 时 blocked。

候选方向输出：

```text
long
short
wait
stop_trading
```

候选场景保存成立条件、失效条件、目标观察区、初步风险收益比、主要证据、反方证据、风控状态和验证计划。
这些字段只用于后续验证和第 19 分析，不是开仓、平仓、加仓、减仓、止盈或止损指令。

## 6. 数学材料包

`app/strategy/aggregation/material_builder.py::build_material_pack()` 使用 snapshot 还原出的 4h / 1d 窗口确定性计算：

1. 最近 swing high / swing low。
2. HH / HL / LH / LL 结构状态。
3. ATR_14。
4. ATR 百分比。
5. 最近 3 / 6 / 20 根平均振幅。
6. 振幅扩张状态。
7. 基于 swing 的支撑压力候选。
8. 候选方向。
9. 候选失效条件。
10. 候选目标观察区。
11. 初步风险收益比。
12. 策略冲突点。
13. 反方证据。
14. 给第 19 的问题清单。

第 18 不把这些指标交给 prompt 临时计算。

## 7. 禁止未来函数

防未来函数检查发生在：

`app/strategy/aggregation/material_builder.py::build_future_leakage_guard()`

检查内容：

1. `max_base_open_time_used_ms <= market_context_snapshot.end_4h_open_time_ms`
2. `max_higher_open_time_used_ms <= market_context_snapshot.end_1d_open_time_ms`

若发现 snapshot 之后的 K线进入还原窗口，第 18 返回：

```text
status=blocked
error_code=future_leakage_guard_failed
```

并且不会写入 `analysis_material_pack`。

正常材料包会写入 `future_leakage_guard_json`，记录最大使用 K线时间、snapshot 目标边界和 `uses_future_klines=false`。

## 8. dry-run 与 confirm-write

dry-run：

1. 默认模式。
2. 读取第 16 run/result 和 snapshot K线窗口。
3. 计算聚合候选和材料包。
4. 不写 `strategy_aggregation_run`。
5. 不写 `analysis_material_pack`。
6. 不发送 Hermes。

confirm-write：

1. 必须显式传入 `--confirm-write`。
2. 写入 `strategy_aggregation_run`。
3. 成功或部分成功时写入 `analysis_material_pack`。
4. blocked 时只写聚合审计行，不写材料包。
5. 写入后按配置决定是否发送 Hermes。
6. Hermes 失败只记录通知状态，不改变聚合状态。

## 9. 自动接入第 17

新增配置：

```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

接入位置：

`app/scheduler/runner.py::SchedulerRunner._run_strategy_aggregation_post_signal_if_needed()`

触发条件：

1. 第 17 已完成。
2. 第 17 状态为 `success` 或 `partial_success`。
3. 第 17 结果包含 `run_id`。
4. `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true`。

自动触发使用：

```text
trigger_source=scheduler
dry_run=false
confirm_write=true
created_by=strategy_signal_scheduler
```

第 18 自动失败不会改写第 17 event log，也不会改写 collector 结果。

## 10. Hermes 配置

新增配置：

```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

通知类型：

`AlertType.STRATEGY_AGGREGATION`

通知内容明确说明：

1. 这是策略聚合结果。
2. `candidate_direction` 只是候选方向。
3. 不是最终交易建议。
4. 未调用大模型。
5. 未进入建议生命周期。
6. 系统未自动交易。

Hermes 结果写回 `strategy_aggregation_run.hermes_status`、`hermes_message`、`hermes_error`、`hermes_sent_at_utc`。

## 11. 异常处理

blocked：

1. `strategy_signal_run` 不存在。
2. `strategy_signal_run.status` 不是 `success` / `partial_success`。
3. 缺少 `snapshot_id`。
4. `strategy_signal_result` 为空。
5. 有效策略数量为 0。
6. snapshot 还原失败。
7. snapshot K线窗口不足。
8. 防未来函数检查失败。

failed：

1. 数据库查询异常。
2. JSON 序列化异常。
3. 材料计算出现不可恢复代码异常。
4. 持久化异常。

partial_success：

1. 聚合和材料包生成成功。
2. 但输入里存在 `failed` / `invalid` / `not_implemented` 策略。

skipped：

同一个：

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

已有第 18 记录时跳过。第一版不会自动反复重跑 blocked / failed。

## 12. 查看结果

```sql
SELECT *
FROM strategy_aggregation_run
ORDER BY id DESC
LIMIT 5;
```

```sql
SELECT *
FROM analysis_material_pack
ORDER BY id DESC
LIMIT 5;
```

重点查看：

1. `strategy_signal_run_id`
2. `snapshot_id`
3. `candidate_direction`
4. `risk_gate_status`
5. `conflict_level`
6. `candidate_scenarios_json`
7. `validation_plan_json`
8. `material_json`
9. `question_json`
10. `future_leakage_guard_json`

## 13. 本阶段明确没有实现

本阶段没有重新运行第 16 策略信号。
本阶段没有重新生成第 15 snapshot。
本阶段没有请求 Binance REST。
本阶段没有请求 Binance WebSocket。
本阶段没有修改正式 K线表。
本阶段没有调用 DeepSeek、GPT、Claude 或其他大模型。
本阶段没有生成最终交易建议。
本阶段没有管理 active advice 生命周期。
本阶段没有自动交易。
本阶段没有读取账户、订单、持仓或 API 私钥。

## 14. 测试

新增测试：

```text
tests/strategy_aggregation/test_strategy_aggregation_service.py
tests/scheduler/test_strategy_aggregation_auto_hook.py
```

覆盖内容：

1. `success` / `partial_success` 的 strategy_signal_run 可以聚合。
2. `blocked` / `failed` 不允许聚合。
3. Gann placeholder / not_implemented 不导致聚合失败。
4. 有效策略不足会 blocked。
5. 趋势偏多 + 风险低/中生成 long candidate。
6. 趋势偏空生成 short candidate。
7. 趋势偏多 + 风险极高降级 wait / stop_trading。
8. 多空冲突提升 conflict_level。
9. material pack 包含 swing、ATR、振幅、支撑压力、候选场景和问题清单。
10. future-leakage guard 阻断 snapshot 之后的 K线。
11. 同一版本组合不重复生成。
12. Hermes 关闭不发送，开启发送并记录状态。
13. CLI dry-run 不写库，confirm-write 才写库。
14. 第 18 不调用第 15 service、不调用第 16 service、不请求 Binance、不调用大模型。
15. 第 18 不生成 `final_advice` 字段。
16. scheduler 只在第 17 success / partial_success 后按配置触发第 18。

默认测试使用 fake repository、fake session 和 fake alert sender，不访问真实 MySQL、Redis、Binance、Hermes 或大模型。

运行：

```bash
python -m pytest tests/strategy_aggregation tests/scheduler tests/strategy
```
