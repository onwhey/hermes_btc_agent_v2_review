# 18_strategy_aggregation_material_pack.md

## 0. 阶段边界修正：只输出分析假设

本 plan 必须按以下边界理解：

1. 第 18 阶段不实现真实交易策略。
2. 第 18 阶段不根据 K线、支撑压力、ATR、swing 结构、上下文观察比值或其他指标独立判断真实多空方向。
3. 第 18 阶段不生成策略信号、不生成操作建议、不生成可执行交易指令。
4. 第 18 阶段的 `long` / `short` / `wait` / `stop_trading` 只能表示后续分析用的方向假设或方向占位。
5. 方向假设只能投影自已有 stage-16 结果、fake/mock fixture，或明确的风险闸门投影。
6. 场景名称使用 `long_hypothesis`、`short_hypothesis`、`wait_hypothesis`、`stop_trading_hypothesis`。
7. 真实 Gann、趋势、支撑压力、风控等策略必须在后续独立阶段以插件化策略类单独开发。

每个 hypothesis 必须显式携带边界字段：

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

持久化方向字段必须使用：

```text
analysis_hypothesis_direction
analysis_hypothesis_confidence
```

这些字段不是 strategy signal，不是 trading advice，不是 executable decision，不能被 Hermes、Admin、复盘模块或后续模块直接当成交易方向。

支撑压力相关比值必须使用弱语义字段，例如：

```text
context_upside_downside_ratio
context_upside_downside_ratio_semantics = support_resistance_context_only_not_entry_exit_signal
```

禁止在第 18 阶段使用 `reward_risk_ratio`、`entry`、`exit`、`stop_loss`、`take_profit`、`position_size`、`leverage` 等容易被理解为交易建议的字段。

`stop_trading_hypothesis` 只能来自上游 stage-16 fake/mock 信号、已有 stage-16 风险占位信号，或第 18 对上游 `risk_gate_status` 的投影。第 18 不得读取 K线、波动率或支撑压力后自行判断“应该停止交易”。

## 1. 阶段名称

第 18 阶段：`strategy_aggregation_material_pack`

中文名称：策略聚合材料包 / 大模型分析材料准备。

第 18 只做两件事：

1. 聚合已有 stage-16 策略信号结果。
2. 构建给第 19 大模型分析层使用的确定性数学材料包。

第 18 不调用大模型，不生成最终交易建议，不管理建议生命周期，不自动交易。

## 2. 输入边界

第 18 只能消费已有数据：

```text
strategy_signal_run
strategy_signal_result
snapshot_id 对应的 MarketContextSnapshot / K线窗口
已入库的 4h / 1d K线数据
```

第 18 不得：

```text
重新跑第 16 策略信号
重新生成第 15 snapshot
请求 Binance REST
请求 Binance WebSocket
修改 market_kline_4h
修改 market_kline_1d
直接调用 DeepSeek / GPT / Claude
读取账户、订单、持仓、API 私钥
自动交易
```

如果第 15 snapshot 只保存窗口范围或引用关系，第 18 可以根据 `snapshot_id` 从本地数据库读取 snapshot 时点之前已经收盘的 K线窗口，但不得读取 target close 之后的数据。

## 3. 链路定位

```text
第 15 层：MarketContextSnapshot 市场上下文快照
    -> 第 16 层：StrategySignalRun / StrategySignalResult 独立策略信号
    -> 第 17 层：StrategySignalScheduler 调度编排
    -> 第 18 层：StrategyAggregationRun + AnalysisMaterialPack 分析材料准备
    -> 第 19 层：LLMAnalysisRun 大模型分析
    -> 第 20 层：AdviceLifecycle 最终建议生命周期
```

第 18 与前后阶段边界：

1. 与第 17：第 18 只能在第 17 成功或部分成功后被配置触发，不改变第 17 target 绑定、event log 语义或第 17 调用第 16 的方式。
2. 与第 16：第 18 只读取已入库的 stage-16 run/result，不重新运行 StrategySignalService。
3. 与第 15：第 18 只通过 repository 恢复 snapshot K线窗口，不重新生成 MarketContextSnapshot。
4. 与第 19：第 18 只生成材料包和问题清单，不调用大模型。
5. 与第 20：第 18 不进入 advice lifecycle，不创建、更新或关闭最终建议。

## 4. 新增数据表

### 4.1 strategy_aggregation_run

用途：记录一次第 18 聚合运行、输入质量、分析假设方向、风险门禁、冲突、证据、场景摘要和 Hermes 状态。

核心字段包括：

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
input_strategy_count
input_success_count
input_failed_count
input_invalid_count
input_not_implemented_count
effective_strategy_count
summary_json
evidence_json
conflict_json
validation_plan_json
message
error_message
created_at_utc
updated_at_utc
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

用途：保存第 19 大模型分析层使用的确定性材料包。

核心字段包括：

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
created_at_utc
updated_at_utc
```

本表不保存大模型输出，不保存最终建议，不保存账户或交易执行数据。

## 5. 分析假设要求

第 18 允许输出：

```text
analysis_hypothesis_direction = long / short / wait / stop_trading
```

但必须明确：这是分析假设方向，不是最终建议，不是策略信号，不可执行。

分析假设必须可验证，至少包含：

1. 成立观察条件。
2. 失效观察条件。
3. 目标观察区。
4. 上下文观察比值。
5. 主要证据。
6. 反方证据。
7. 风控是否投影为否决。
8. 后续验证计划 `validation_plan_json`。

禁止输出：

```text
开仓建议
平仓建议
加仓建议
减仓建议
止盈止损操作指令
仓位
杠杆
订单字段
最终交易建议
```

## 6. 数学材料包要求

第一版至少写入 `analysis_material_pack.material_json`：

1. 最近 swing high / swing low。
2. HH / HL / LH / LL 结构判断。
3. ATR_14。
4. ATR 百分比。
5. 最近 3 / 6 / 20 根平均振幅。
6. 振幅是否扩张。
7. 基于 swing 的支撑压力候选。
8. `analysis_hypothesis_direction`。
9. 假设失效观察条件。
10. 假设目标观察区。
11. `context_upside_downside_ratio`。
12. 策略冲突点。
13. 反方证据。
14. 给第 19 大模型的问题清单。

这些指标必须由代码确定性计算，不得放到 prompt 中临时计算。

## 7. 禁止未来函数

第 18 构建材料包时，只能使用 snapshot 对应时点之前已经收盘的数据。

禁止：

1. 读取 target close 之后的 K线参与 swing / ATR / 支撑压力 / 波动率计算。
2. 使用后续价格走势反推当时的分析假设方向。
3. 用数据库最新 K线污染历史 snapshot 材料包。

材料包必须能追溯到：

```text
snapshot_id
strategy_signal_run_id
K线窗口边界
aggregation_version
material_schema_version
indicator_version
```

## 8. 聚合规则第一版

第一版使用确定性规则，不做机器学习，不实现真实策略。

至少覆盖：

1. 上游 stage-16 明确偏多且风险低/中：可投影 `long_hypothesis`。
2. 上游 stage-16 明确偏空且风险低/中：可投影 `short_hypothesis`。
3. 趋势方向不明或有效策略不足：`wait_hypothesis` 或 blocked。
4. 波动风险高或冲突明显：倾向 `wait_hypothesis`。
5. 风险极高且来源于上游风险信号或风险闸门投影：可投影 `stop_trading_hypothesis`。
6. 策略明显冲突：`conflict_level` 升高，并倾向 `wait_hypothesis`。
7. Gann placeholder / `not_implemented` 不得导致聚合失败。

必须记录：

1. 哪些 stage-16 结果支持多。
2. 哪些 stage-16 结果支持空。
3. 哪些 stage-16 结果中性。
4. 哪些 stage-16 结果只提示风险。
5. 哪些策略未实现。
6. 哪些策略失败或 invalid。
7. 风控是否否决。
8. 为什么分析假设方向不是最终建议。

## 9. 自动接入第 17

第 18 可以通过配置自动接在第 17 后面：

```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

触发条件：

1. 第 17 / 第 16 结果为 `success` 或 `partial_success`。
2. 第 17 结果包含 `run_id`。
3. `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true`。

不触发条件：

```text
blocked
failed
skipped
waiting_upstream
```

第 18 自动触发不得改变第 17 既有语义：

1. 不得改变第 17 `target_base_open_time_ms` 绑定逻辑。
2. 不得改变第 17 event log 语义。
3. 不得改变第 17 调用第 16 的方式。
4. 不得修改第 17 latest K线判断规则。
5. 不得借实现第 18 修改第 15 / 第 16 质量门禁。

## 10. Hermes

第 18 支持 Hermes 聚合通知，但必须通过 `.env` 控制：

```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

通知内容必须明确：

1. 这是策略聚合材料结果。
2. 不是最终交易建议。
3. 未调用大模型。
4. 未自动交易。
5. `analysis_hypothesis_direction` 只是分析假设方向。

## 11. 幂等

同一个：

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

只能生成一套聚合结果和材料包。

已有 `success` / `partial_success` / `blocked` / `failed` 时第一版均跳过，不自动反复重跑，必须清晰输出 skipped 和原因。

## 12. CLI 手动入口

新增手动入口：

```bash
python -m scripts.run_strategy_aggregation --strategy-signal-run-id <run_id> --trigger-source cli
```

默认 dry-run，不写数据库。

确认写入：

```bash
python -m scripts.run_strategy_aggregation --strategy-signal-run-id <run_id> --trigger-source cli --confirm-write
```

CLI 只能调用第 18 service：

1. 不直接调用第 16。
2. 不直接调用第 15。
3. 不请求 Binance。
4. 不发送 Hermes。
5. 不承载核心调度逻辑。

输出至少包含：

```text
aggregation_run_id
material_pack_id
status
analysis_hypothesis_direction
risk_level
conflict_level
message
error_message
```

## 13. 测试要求

至少覆盖：

1. `success` / `partial_success` 的 stage-16 run 可以聚合。
2. `blocked` / `failed` 的 stage-16 run 不允许聚合。
3. Gann placeholder 不导致聚合失败。
4. 有效策略不足时 blocked 或 wait。
5. 上游偏多 + 风险低/中 => `long_hypothesis`。
6. 上游偏空 + 风险低/中 => `short_hypothesis`。
7. 上游偏多 + 风险极高 => `wait_hypothesis` 或 `stop_trading_hypothesis`。
8. 策略冲突时 `conflict_level` 升高。
9. material pack 包含 swing、ATR、振幅、支撑压力、问题清单。
10. 禁止未来函数：不得使用 snapshot 之后的 K线。
11. 同一 stage-16 run 不重复生成 aggregation。
12. Hermes 关闭时不发送。
13. Hermes 开启时发送并记录状态。
14. CLI dry-run 不写数据库。
15. CLI confirm-write 写数据库。
16. 第 18 不调用大模型。
17. 第 18 不生成最终交易建议。
18. 第 18 不实例化 GannStrategy、TrendStrategy、SupportResistanceStrategy、RiskControlStrategy。
19. long/short hypothesis 必须带有非信号、非建议、不可执行字段。
20. `validation_plan` 必须保留结构化 key。

## 14. 文档要求

实现后必须更新：

1. `docs/implementation/18_strategy_aggregation_material_pack.md`
2. `.env.example`
3. README 或相关运行说明

文档必须说明：

1. 第 18 的输入。
2. 第 18 的输出。
3. 第 18 与第 17 / 第 19 / 第 20 的边界。
4. `analysis_hypothesis_direction` 不是 `final_advice`。
5. 如何手动运行 CLI。
6. 如何配置自动接入第 17。
7. 如何配置 Hermes。
8. 如何查看 aggregation 和 material pack 结果。

## 15. 验收命令

完成后运行：

```bash
python -m compileall app migrations scripts tests
python -m pytest tests/scheduler tests/strategy
python -m pytest tests/strategy_aggregation
python -m scripts.check_project_invariants
```

## 16. 明确不做

第 18 不实现：

1. 真实 Gann 策略。
2. 真实趋势策略。
3. 真实支撑压力策略。
4. 真实风控策略。
5. 大模型分析。
6. 最终交易建议。
7. 建议生命周期。
8. 自动交易。
9. 账户、订单、持仓读取。
