# 18 Strategy Aggregation Material Pack 实现说明

## 0. 阶段边界：只输出分析假设

第 18 阶段不实现真实策略，不独立判断真实多空方向，不生成策略信号，不生成操作建议，不生成可执行交易字段。

第 18 阶段中的 `long` / `short` / `wait` / `stop_trading` 只表示后续分析使用的 hypothesis 或方向占位。它们不是策略结论，不是交易建议，不可执行。

`candidate_scenarios_json` 使用：

```text
long_hypothesis
short_hypothesis
wait_hypothesis
stop_trading_hypothesis
```

每个 hypothesis 都必须携带：

```text
scenario_semantics = analysis_hypothesis_only
is_strategy_signal = false
is_trading_advice = false
is_executable = false
source = fixture_or_existing_signal_projection
strategy_logic_implemented = false
promotion_allowed = false
promotion_requires_future_strategy_and_llm_stage = true
```

持久化方向字段为：

```text
analysis_hypothesis_direction
analysis_hypothesis_confidence
```

并配套保存：

```text
analysis_hypothesis_semantics = analysis_hypothesis_only
direction_projection_source = fixture_or_existing_signal_projection
stop_trading_source
risk_gate_projection_source
is_strategy_signal = false
is_trading_advice = false
is_executable = false
strategy_logic_implemented = false
promotion_allowed = false
promotion_requires_future_strategy_and_llm_stage = true
```

`context_upside_downside_ratio` 是支撑压力上下文观察比值，不是入场/出场指标，不是止损止盈依据，不是策略胜率或盈亏比判断，也不是最终建议依据。

`stop_trading_hypothesis` 只在已有 stage-16 fake/mock 行、已有风险占位信号或风险闸门投影明确提供来源时输出，并标记：

```text
stop_trading_source = upstream_risk_gate_projection
```

第 18 仍然只是材料包、问题清单、验证计划和方向假设占位；真实 Gann、趋势、支撑压力、风控等策略会在后续独立阶段以插件化策略类单独开发。

## 1. 功能：策略聚合与材料包构建

### 1.1 发起方式

手动 dry-run：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <strategy_signal_run.run_id> \
  --trigger-source cli
```

手动确认写入：

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

自动触发只在第 17 返回 `success` / `partial_success` 且 `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true` 时发生。`waiting_upstream` / `blocked` / `failed` / `skipped` 不触发第 18。

### 1.2 入口文件

手动入口：

```text
scripts/run_strategy_aggregation.py
```

入口方法：

```text
main()
```

核心 service：

```text
app/strategy/aggregation/service.py
```

核心方法：

```text
StrategyAggregationService.run_strategy_aggregation()
```

## 2. 核心调用链路

```text
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_existing_aggregation
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_strategy_signal_run
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.list_strategy_signal_results
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.restore_snapshot_kline_windows
    -> app/strategy/aggregation/material_builder.py::build_future_leakage_guard
    -> app/strategy/aggregation/rules.py::classify_strategy_results
    -> app/strategy/aggregation/rules.py::build_aggregation_decision
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

## 3. 输入与边界

第 18 读取：

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

## 4. 新增表与模型

Migration：

```text
migrations/versions/20260518_18_create_strategy_aggregation_material_pack.py
```

ORM：

```text
app/storage/mysql/models/strategy_aggregation.py
```

### 4.1 strategy_aggregation_run

用途：保存一次第 18 聚合运行、输入质量、分析假设方向、风险门禁、冲突、证据、候选场景和通知状态。

关键字段：

```text
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
status
analysis_hypothesis_direction
analysis_hypothesis_confidence
analysis_hypothesis_semantics
direction_projection_source
stop_trading_source
risk_gate_projection_source
is_strategy_signal
is_trading_advice
is_executable
strategy_logic_implemented
promotion_allowed
promotion_requires_future_strategy_and_llm_stage
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

实现文件：

```text
app/strategy/aggregation/rules.py
```

第一版是确定性规则：

1. `strategy_signal_run.status` 只接受 `success` / `partial_success`。
2. `strategy_signal_result.strategy_status=success/no_signal` 可参与聚合。
3. `not_implemented` 不导致聚合失败，会进入 `partial_success` 背景。
4. `failed` / `invalid` 不参与方向投影，但写入质量问题分组。
5. `bullish_bias` 归入上游偏多证据。
6. `bearish_bias` 归入上游偏空证据。
7. `not_applicable` 且带风险等级的结果归入风险策略。
8. 高风险倾向 wait；极高风险可通过风险闸门投影 `stop_trading_hypothesis`。
9. 多空明显冲突时 `conflict_level=high`，分析假设方向倾向 wait。
10. 有效策略数量为 0 时 blocked。

聚合规则只投影已有 stage-16 行，不实现真实策略，不根据 K线材料自行生成 long/short。

## 6. candidate_scenarios_json

实现文件：

```text
app/strategy/aggregation/candidate_scenario_builder.py
```

输出结构包含：

```text
analysis_hypothesis_direction
analysis_hypothesis_semantics
requested_analysis_hypothesis_direction
analysis_hypothesis_confidence
risk_gate_status
conflict_level
direction_projection_source
stop_trading_source
risk_gate_projection_source
candidate_scenarios
boundary
```

每个 scenario 使用：

```text
scenario_type = long_hypothesis / short_hypothesis / wait_hypothesis / stop_trading_hypothesis
scenario_semantics = analysis_hypothesis_only
hypothesis_direction
source = fixture_or_existing_signal_projection
direction_projection_source
stop_trading_source
risk_gate_projection_source
projected_from_existing_stage16_signal = true
supporting_evidence
opposing_evidence
risk_notes
validation_plan
context_upside_downside_ratio
context_upside_downside_ratio_semantics
```

`validation_plan` 保留结构化 key：

```text
evaluation_horizons_base_bars
activation_check
invalidation_check
floating_range_check
target_observation_check
notes
```

不得将 `validation_plan` 转成无字段名 list。

## 7. 数学材料包

实现文件：

```text
app/strategy/aggregation/material_builder.py
```

`build_material_pack()` 使用 snapshot 还原出的 4h / 1d 窗口确定性计算：

1. 最近 swing high / swing low。
2. HH / HL / LH / LL 结构状态。
3. ATR_14。
4. ATR 百分比。
5. 最近 3 / 6 / 20 根平均振幅。
6. 振幅扩张状态。
7. 基于 swing 的支撑压力候选。
8. 分析假设方向。
9. 假设失效观察条件。
10. 假设目标观察区。
11. 上下文观察比值 `context_upside_downside_ratio`。
12. 策略冲突点。
13. 反方证据。
14. 给第 19 的问题清单。

这些指标都由代码确定性计算，不交给 prompt 临时计算。

## 8. 禁止未来函数

实现文件：

```text
app/strategy/aggregation/material_builder.py::build_future_leakage_guard()
```

检查内容：

1. `max_base_open_time_used_ms <= market_context_snapshot.end_4h_open_time_ms`
2. `max_higher_open_time_used_ms <= market_context_snapshot.end_1d_open_time_ms`

如果发现 snapshot 之后的 K线进入还原窗口，第 18 返回：

```text
status = blocked
error_code = future_leakage_guard_failed
```

并且不会写入 `analysis_material_pack`。

正常材料包会写入 `future_leakage_guard_json`，记录最大使用 K线时间、snapshot 边界和 `uses_future_klines=false`。

## 9. dry-run 与 confirm-write

dry-run：

1. 默认模式。
2. 读取第 16 run/result 和 snapshot K线窗口。
3. 计算聚合分析假设和材料包。
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

## 10. 自动接入第 17

配置：

```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

接入位置：

```text
app/scheduler/runner.py::SchedulerRunner._run_strategy_aggregation_post_signal_if_needed()
```

触发条件：

1. 第 17 已完成。
2. 第 17 状态为 `success` 或 `partial_success`。
3. 第 17 结果包含 `run_id`。
4. `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true`。

自动触发调用：

```text
app/scheduler/jobs/strategy_aggregation_job.py::run_strategy_aggregation_after_signal_job
```

该 job 不调用 scripts，不调用第 16 service，不调用第 15 service，不请求 Binance，不修改正式 K线表。

## 11. Hermes

配置：

```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

发送判断在：

```text
app/strategy/aggregation/service.py::_send_or_skip_hermes
```

可见内容由以下文件生成：

```text
app/strategy/aggregation/hermes_formatter.py::build_strategy_aggregation_visible_body
```

Hermes 内容明确说明：

1. 这是策略聚合分析假设。
2. 不是最终交易建议。
3. 未调用大模型。
4. 未进入建议生命周期。
5. 未自动交易。
6. `analysis_hypothesis_direction` 只是分析假设投影。

## 12. 异常处理

1. 请求参数非法：返回 `failed`，不写数据库。
2. 已有同版本聚合：返回 `skipped`，不重复生成。
3. stage-16 run 不存在：返回 `blocked`。
4. stage-16 run 状态不允许：返回 `blocked`。
5. stage-16 result 为空：返回 `blocked`。
6. snapshot 无法还原：返回 `blocked`。
7. 未来函数检查失败：返回 `blocked`。
8. 材料包输入不足：返回 `blocked`。
9. 未预期异常：返回 `failed` 并 rollback。
10. Hermes 发送失败：记录 `hermes_status=failed`，不改变聚合状态。

## 13. 测试

对应测试：

```text
tests/strategy_aggregation/test_strategy_aggregation_service.py
tests/scheduler/test_strategy_aggregation_auto_hook.py
tests/strategy/test_strategy_signal_framework.py
```

覆盖内容：

1. success / partial_success 可以聚合。
2. blocked / failed 不允许聚合。
3. Gann placeholder 不导致聚合失败。
4. 有效策略不足 blocked 或 wait。
5. 上游偏多投影 `long_hypothesis`。
6. 上游偏空投影 `short_hypothesis`。
7. 极高风险投影 wait 或 `stop_trading_hypothesis`。
8. 策略冲突提升 `conflict_level`。
9. material pack 包含 swing、ATR、振幅、支撑压力、问题清单。
10. 禁止未来函数。
11. 同一 stage-16 run 不重复生成 aggregation。
12. Hermes 关闭时不发送。
13. Hermes 开启时发送并记录状态。
14. CLI dry-run 不写数据库。
15. CLI confirm-write 写数据库。
16. 第 18 不调用大模型。
17. 第 18 不生成最终交易建议。
18. 第 18 不实例化真实策略类。
19. long/short hypothesis 带有非信号、非建议、不可执行字段。
20. `validation_plan` 保留 key。
21. `context_upside_downside_ratio` 不命名为 reward/risk。
22. `stop_trading_hypothesis` 有明确来源字段。

默认 pytest 不请求 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

## 14. 人工查看结果

查看聚合行：

```sql
select
  aggregation_run_id,
  strategy_signal_run_id,
  snapshot_id,
  status,
  analysis_hypothesis_direction,
  analysis_hypothesis_semantics,
  direction_projection_source,
  stop_trading_source,
  is_strategy_signal,
  is_trading_advice,
  is_executable,
  risk_level,
  risk_gate_status,
  conflict_level,
  message,
  error_message
from strategy_aggregation_run
order by id desc
limit 20;
```

查看材料包：

```sql
select
  material_pack_id,
  aggregation_run_id,
  strategy_signal_run_id,
  snapshot_id,
  status,
  material_schema_version,
  indicator_version,
  created_at_utc
from analysis_material_pack
order by id desc
limit 20;
```

## 15. 本功能不负责

第 18 不负责：

1. 不实现真实策略。
2. 不生成真实 long/short 策略信号。
3. 不生成最终交易建议。
4. 不调用 DeepSeek / GPT / Claude。
5. 不管理建议生命周期。
6. 不自动下单。
7. 不读取账户、订单、持仓。
8. 不请求 Binance REST / WebSocket。
9. 不修改 `market_kline_4h` / `market_kline_1d`。
10. 不修复 K线。
