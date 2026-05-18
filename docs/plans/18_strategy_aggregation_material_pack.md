# 18_strategy_aggregation_material_pack.md

## 0. 2026-05-19 boundary correction: analysis hypotheses only

This plan must be read with the following corrected boundary:

1. Stage 18 does not implement real trading strategies.
2. Stage 18 does not independently judge long/short direction from Klines,
   support/resistance, reward/risk, ATR, swing structure, or any indicator.
3. Stage 18 does not generate strategy signals, operation advice, or executable
   trading instructions.
4. `long` / `short` / `wait` / `stop_trading` in this stage are analysis
   hypotheses or direction placeholders projected from existing stage-16 rows
   or test fixtures only.
5. Scenario names should use `long_hypothesis`, `short_hypothesis`,
   `wait_hypothesis`, and `stop_trading_hypothesis`.
6. Every hypothesis must explicitly mark:

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

Real Gann, trend, support/resistance, risk-control, and other strategies must
be developed later as independent plugin-style strategy classes.

## 1. 阶段名称

第 18 阶段：`strategy_aggregation_material_pack`

中文名称：策略聚合、候选场景与大模型数学材料包构建。

本版定位：在原第 18 plans 基础上，强化“策略有效性、判断正确性、后续可验证、后续可复盘”的要求。第 18 不只是把多个策略信号做摘要，而是要把每次候选判断变成可以被后续大模型审查、生命周期层引用、复盘系统评估的结构化证据。

---

## 2. 阶段目标

第 18 阶段在第 16 独立策略信号已经生成、第 17 策略信号调度已经完成之后，负责完成三件事：

1. **策略聚合**：对多个独立策略信号进行确定性聚合，形成候选方向、风险状态、策略一致性、策略冲突、风控否决结果。
2. **候选场景构建**：基于策略信号和市场快照，形成可验证的候选场景，包括成立条件、失效条件、目标观察区、初步风险收益比、主要证据、反方证据。
3. **数学材料包构建**：基于 `MarketContextSnapshot` 对应的 K线窗口，计算 swing、ATR、振幅、支撑压力、结构状态、问题清单，并写入 `analysis_material_pack`，供第 19 大模型分析层使用。

第 18 阶段不是最终建议层，不负责建议生命周期，不调用 DeepSeek / GPT / Claude 等大模型，不自动交易，不读取账户、订单或持仓。

---

## 3. 链路定位

```text
第 15 层：MarketContextSnapshot 市场上下文快照
    ↓
第 16 层：StrategySignalRun / StrategySignalResult 独立策略信号
    ↓
第 17 层：StrategySignalScheduler 策略信号调度编排
    ↓
第 18 层：StrategyAggregationRun + AnalysisMaterialPack 策略聚合、候选场景与数学材料包
    ↓
第 19 层：LLMAnalysisRun 大模型分析
    ↓
第 20 层：AdviceLifecycle 最终建议生命周期
```

第 18 只消费已有结果：

```text
strategy_signal_run
strategy_signal_result
snapshot_id
MarketContextSnapshot 对应的 4h / 1d K线窗口或快照引用范围
```

第 18 不得：

```text
重新跑第 16 策略信号
重新生成第 15 snapshot
请求 Binance REST / WebSocket
调用大模型
生成最终交易建议
管理 active advice 生命周期
```

如果第 15 快照只保存窗口范围或引用关系，而没有保存完整 K线内容，第 18 可以根据 `snapshot_id` 对应的时间范围从本地数据库读取已收盘 K线，但必须满足：

```text
只能读取 snapshot 时点及之前已经确认收盘的数据
不得读取 target close 之后的未来 K线
不得修改任何 K线数据
计算结果必须写入 analysis_material_pack
```

---

## 4. 核心原则

### 4.1 候选方向不是最终建议

第 18 可以输出：

```text
candidate_direction = long / short / wait / stop_trading / neutral / mixed
```

但这只是“聚合层候选方向”，不是最终交易建议。

第 18 严禁输出或暗示：

```text
建议开多
建议开空
建议加仓
建议减仓
建议平仓
止盈指令
止损指令
```

第 18 可以输出：

```text
候选成立条件
候选失效条件
候选目标观察区
初步风险收益比
```

但必须明确其性质是候选场景，不是操作指令。

### 4.2 所有候选判断必须可验证

任何 `candidate_direction` 都不能只给一个方向。必须配套保存：

```text
分析假设观察条件 activation_check
分析假设失效检查 invalidation_check
目标观察区 target_observation_zone
初步风险收益比 preliminary_reward_risk_ratio
主要证据 supporting_evidence
反方证据 opposing_evidence
风险说明 risk_notes
后续验证计划 validation_plan
```

示例：

```json
{
  "candidate_direction": "long",
  "activation_check": "仅供后续分析层观察，不是执行触发条件",
  "invalidation_check": "仅供后续分析层检查，不是交易止损指令",
  "target_observation_zone": "最近 swing high 至上方压力区间",
  "preliminary_reward_risk_ratio": 1.8,
  "supporting_evidence": ["趋势结构偏多", "价格仍位于最近 higher low 上方"],
  "opposing_evidence": ["上方压力接近", "短期振幅扩张"],
  "risk_notes": ["该候选方向不能解释为立即开仓指令"],
  "validation_plan": ["后续观察 1 到 6 根 4h K线是否触发成立或失效条件"]
}
```

### 4.3 禁止未来函数

第 18 的所有指标和候选场景只能基于 `snapshot_id` 对应时点可见的数据。

禁止：

```text
读取 target close 之后的 K线参与 swing / ATR / 支撑压力计算
使用后续价格走势反推当时的候选方向
用当前数据库最新 K线污染历史 snapshot 的材料包
```

必须保证：

```text
同一个 strategy_signal_run_id + snapshot_id 在不同时间重跑，第 18 的核心材料应该稳定一致。
```

如果底层 K线发生合法修订或补齐，应通过新的 material version / rerun 机制记录，不得无声覆盖旧材料。

### 4.4 指标、聚合和材料包必须版本化

第 18 必须记录：

```text
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

原因：ATR、swing、支撑压力、风险收益比和风控否决规则未来一定会调整。没有版本号，后续复盘无法判断某次候选判断是按哪个算法生成的。

### 4.5 风控可以否决方向

风控类策略和波动率风险策略可以把候选方向从 `long / short` 改为：

```text
wait
stop_trading
```

示例：

```text
趋势结构偏多，但波动率风险极高
=> candidate_direction = wait
=> risk_gate_status = blocked_by_volatility
```

聚合层必须明确告诉用户和后续模型：到底是哪类风险导致等待或停止交易。

### 4.6 记录分歧，不只输出结论

第 18 必须记录：

```text
支持多头的策略
支持空头的策略
支持等待的策略
只提示风险的策略
未实现策略
失败或无效策略
冲突等级
风控否决状态
```

后续复盘时，不只看“聚合最终偏多/偏空”，还要能评估：

```text
哪个策略贡献了正确判断
哪个策略经常制造噪音
风控否决是否真的减少了错误交易
策略之间是否重复表达同一个因子
```

### 4.7 Hermes 完全配置化

第 18 支持 Hermes 通知，但发送与否由 `.env` 控制。用户可以同时开启第 17、第 18、第 19、第 20 的通知，也可以全部关闭。

第 18 不强制只发一条消息，但文档必须提醒：成熟阶段通常建议关闭底层通知，只保留最高决策层通知和异常通知。

---

## 5. 输入数据

第 18 的核心输入：

```text
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
strategy_signal_result 列表
MarketContextSnapshot 对应的 4h / 1d K线窗口或快照引用范围
```

有效输入状态：

```text
strategy_signal_run.status in success / partial_success
```

禁止输入状态：

```text
blocked
failed
skipped
running
```

如果输入的 `strategy_signal_run` 状态不合法，第 18 应返回 `blocked`，并记录原因。

---

## 6. 输出数据

第 18 至少新增两类持久化结果：

```text
strategy_aggregation_run
analysis_material_pack
```

### 6.1 strategy_aggregation_run

职责：记录本轮策略聚合、候选方向、风险否决、冲突情况和候选场景摘要。

建议字段：

```text
id
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
input_strategy_count
input_success_count
input_failed_count
input_invalid_count
input_not_implemented_count
effective_strategy_count
candidate_direction
candidate_direction_confidence
risk_level
risk_gate_status
conflict_level
direction_consensus
supporting_strategies_json
opposing_strategies_json
risk_strategies_json
not_implemented_strategies_json
failed_strategies_json
invalid_strategies_json
candidate_scenarios_json
validation_plan_json
summary_json
message
error_code
error_message
trace_id
trigger_source
created_by
created_at_utc
updated_at_utc
```

建议状态值：

```text
success
partial_success
blocked
failed
skipped
```

说明：

```text
success：聚合成功，输入策略信号质量满足要求。
partial_success：聚合完成，但存在部分策略 failed / invalid / not_implemented。
blocked：输入条件不满足，例如 strategy_signal_run 状态不合法、缺少 snapshot_id、有效策略不足、K线窗口不足。
failed：数据库异常、JSON 序列化异常、代码异常或不可恢复计算异常。
skipped：幂等命中，已有相同输入的聚合结果。
```

### 6.2 analysis_material_pack

职责：记录给第 19 大模型使用的结构化数学材料包和问题清单。

建议字段：

```text
id
material_pack_id
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
schema_version
indicator_version
status
material_json
question_json
summary_json
data_window_json
future_leakage_guard_json
trace_id
created_by
created_at_utc
updated_at_utc
```

`material_json` 保存确定性计算材料。

`question_json` 保存第 19 阶段要问大模型的问题清单。

`data_window_json` 必须记录使用的 K线范围，例如：

```json
{
  "base_interval": "4h",
  "base_open_time_start_utc": "...",
  "base_open_time_end_utc": "...",
  "base_kline_count": 180,
  "higher_interval": "1d",
  "higher_open_time_start_utc": "...",
  "higher_open_time_end_utc": "...",
  "higher_kline_count": 180
}
```

`future_leakage_guard_json` 必须记录防未来函数检查结果，例如：

```json
{
  "max_base_open_time_used_utc": "...",
  "snapshot_target_base_open_time_utc": "...",
  "uses_future_klines": false
}
```

---

## 7. 聚合逻辑第一版

第 18 第一版采用确定性规则，不引入机器学习，不调用大模型。

### 7.1 有效策略识别

根据 `strategy_signal_result.strategy_status` 区分：

```text
success：有效策略信号
failed：策略执行失败
invalid：策略输出无效
not_implemented：策略未实现
```

`not_implemented` 不应导致聚合失败。当前阶段江恩策略可能仍为占位策略，因此 `partial_success` 是可接受状态。

### 7.2 方向归类

聚合层读取策略结果中的：

```text
direction_bias
signal_strength
risk_level
strategy_status
reason_json
evidence_json
```

第一版可以按简单规则归类：

```text
bullish / long_bias：支持多头
bearish / short_bias：支持空头
neutral / range / wait：支持等待或中性
risk_only：只提示风险，不直接参与多空投票
not_implemented：不参与方向投票，但计入未实现策略
failed / invalid：不参与方向投票，但计入质量问题
```

### 7.3 候选方向规则

基础规则：

```text
多头有效策略数量 > 空头有效策略数量，且风控未否决：candidate_direction = long
空头有效策略数量 > 多头有效策略数量，且风控未否决：candidate_direction = short
多空接近或有效策略不足：candidate_direction = wait / mixed
风控极高或风险策略否决：candidate_direction = wait 或 stop_trading
```

候选方向置信度建议：

```text
low / medium / high
```

第一版不要过度精细。置信度只能表示聚合层信号一致性强弱，不代表盈利概率。

### 7.4 风控优先规则

建议字段：

```text
risk_gate_status = pass / caution / blocked_by_volatility / blocked_by_conflict / insufficient_data
```

示例：

```text
趋势偏多，波动率风险高：candidate_direction = wait，risk_gate_status = blocked_by_volatility
趋势偏空，风险可控：candidate_direction = short，risk_gate_status = pass
多空策略严重冲突：candidate_direction = wait，risk_gate_status = blocked_by_conflict
有效数据不足：candidate_direction = wait，risk_gate_status = insufficient_data
```

### 7.5 冲突等级

建议值：

```text
none
low
medium
high
```

第一版规则：

```text
有效策略全部同向：none / low
有一个主要策略相反：medium
多空策略数量接近，且信号强度都不低：high
风控否决方向：medium / high
有效策略过少：medium，且 risk_gate_status = insufficient_data
```

---

## 8. 数学材料包第一版

第 18 第一版至少计算以下材料，并写入 `analysis_material_pack.material_json`。

### 8.1 K线窗口摘要

从 `MarketContextSnapshot` 对应的 4h / 1d K线窗口中提取：

```text
latest_open
latest_high
latest_low
latest_close
latest_volume
recent_base_klines_summary
recent_higher_klines_summary
base_window_count
higher_window_count
```

不得请求 Binance REST 或 WebSocket。

### 8.2 swing high / swing low

第一版使用确定性局部高低点规则。

建议参数：

```text
swing_left_bars = 2
swing_right_bars = 2
```

定义：

```text
swing high：某根 K线 high 高于左侧 N 根和右侧 N 根 high
swing low：某根 K线 low 低于左侧 N 根和右侧 N 根 low
```

输出：

```json
{
  "recent_swing_highs": [],
  "recent_swing_lows": [],
  "structure_labels": ["HH", "HL", "LH", "LL"],
  "structure_state": "uptrend / downtrend / range / mixed / insufficient_data"
}
```

### 8.3 ATR 与波动率

计算 4h 的 ATR_14：

```text
TR = max(high - low, abs(high - previous_close), abs(low - previous_close))
ATR_14 = 最近 14 根 TR 平均值
ATR_PERCENT = ATR_14 / latest_close * 100
```

输出：

```json
{
  "atr_14": 0,
  "atr_percent": 0,
  "volatility_state": "low / normal / expanded / extreme"
}
```

### 8.4 振幅变化

单根振幅：

```text
range_percent = (high - low) / close * 100
```

计算：

```text
最近 3 根平均振幅
最近 6 根平均振幅
最近 20 根平均振幅
```

输出：

```json
{
  "avg_range_percent_3": 0,
  "avg_range_percent_6": 0,
  "avg_range_percent_20": 0,
  "range_expansion_state": "contracting / normal / expanding / extreme"
}
```

### 8.5 支撑压力候选

第一版基于最近 swing high / swing low 生成候选支撑压力：

```text
最近有效 swing lows → support_candidates
最近有效 swing highs → resistance_candidates
```

输出应包含：

```text
price
open_time_utc
source_interval
distance_to_latest_close_percent
source = swing_high / swing_low
```

### 8.6 候选场景

`candidate_scenarios_json` 至少包含：

```json
{
  "candidate_direction": "long / short / wait / stop_trading / mixed",
  "candidate_scenarios": [
    {
      "scenario_type": "long_hypothesis / short_hypothesis / wait_hypothesis / stop_trading_hypothesis",
      "activation_check": "分析假设观察条件",
      "invalidation_check": "分析假设失效检查",
      "target_observation_zone": "候选目标观察区",
      "preliminary_reward_risk_ratio": 0,
      "supporting_evidence": [],
      "opposing_evidence": [],
      "risk_notes": [],
      "validation_plan": []
    }
  ]
}
```

注意：

```text
invalidation_check 不是交易止损指令
target_observation_zone 不是止盈指令
preliminary_reward_risk_ratio 只是候选场景质量评估，不是下单依据
```

### 8.7 大模型问题清单

`question_json` 至少包含：

```text
1. 当前候选方向是否被价格结构支持？
2. 当前波动率是否支持候选失效条件的距离？
3. 当前目标观察区与候选失效条件之间的初步风险收益比是否合理？
4. 当前结构是否存在假突破或追涨/追跌风险？
5. 多个策略是否真正独立，还是重复表达同一个趋势因子？
6. 如果策略信号与风控冲突，应优先等待还是停止交易？
7. 哪些条件必须成立，才允许从 wait 转为 long 或 short？
8. 当前候选场景的反方证据是否足以否决方向？
9. 如果当前候选判断错误，最可能错在哪里？
```

第 19 大模型层必须优先读取这些问题，而不是让模型自由发挥写作文。

---

## 9. 后续评估预留

第 18 不做完整复盘，但必须为后续复盘留下可评估材料。

每个候选场景必须能在后续被评估：

```text
是否满足 activation_check 对应的观察条件
是否先满足 invalidation_check 对应的失效检查
后续 1 / 3 / 6 根 4h K线最大浮盈
后续 1 / 3 / 6 根 4h K线最大浮亏
目标观察区是否到达
风险收益比是否现实
风控否决是否有效
```

第 18 可以在 `validation_plan_json` 中预留：

```json
{
  "evaluation_horizons_base_bars": [1, 3, 6],
  "activation_check": "基于 4h 收盘价判断成立条件是否触发",
  "invalidation_check": "基于 4h 收盘价判断失效条件是否触发",
  "floating_pnl_check": "以后续 K线 high/low 估算最大有利/不利波动",
  "notes": "本阶段只生成验证计划，不执行复盘"
}
```

这不会提前实现复盘系统，但能保证第 18 的输出以后可以被评估。

---

## 10. 自动触发规则

第 18 可以自动接在第 17 后面运行，但必须通过 `.env` 配置控制。

新增配置：

```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

规则：

```text
第 17 status = success / partial_success
    ↓
且 STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true
    ↓
自动调用第 18 StrategyAggregationService
```

以下第 17 状态不得自动运行第 18：

```text
waiting_upstream
blocked
failed
skipped
running
```

第 18 自动触发时，不得影响第 17 的 event log 状态。第 17 与第 18 必须保持独立审计链路。

---

## 11. CLI 手动入口

新增手动入口：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --dry-run
```

确认写入：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --confirm-write
```

CLI 规则：

```text
默认 dry-run
dry-run 不写 strategy_aggregation_run
dry-run 不写 analysis_material_pack
confirm-write 才允许写入
不允许 CLI 直接调用第 15
不允许 CLI 直接调用第 16
不允许 CLI 请求 Binance
不允许 CLI 修改 K线
```

CLI 输出至少包含：

```text
status
exit_code
aggregation_run_id
material_pack_id
strategy_signal_run_id
snapshot_id
candidate_direction
risk_gate_status
conflict_level
message
error_message
```

---

## 12. Hermes 通知

第 18 支持 Hermes 通知，但必须配置化。

新增配置：

```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

第 18 Hermes 通知内容定位：

```text
策略聚合结果通知
```

不得包装成最终交易建议。

通知内容必须明确：

```text
这是策略聚合层候选判断，不是最终交易建议。
未调用大模型。
未进入建议生命周期。
系统未自动交易。
```

允许用户通过 `.env` 同时开启第 17 和第 18 通知。一个 4h 周期可能收到第 17 策略信号通知和第 18 聚合通知。是否开启由用户自己控制。

Hermes 发送结果必须写入 `strategy_aggregation_run` 或配套通知字段，例如：

```text
hermes_enabled
hermes_status
hermes_error
hermes_sent_at_utc
```

---

## 13. 幂等规则

第 18 必须防止重复聚合。

唯一身份建议：

```text
strategy_signal_run_id + aggregation_version + material_schema_version + indicator_version + candidate_scenario_version
```

如果已经存在 `success / partial_success` 的聚合结果，则不重复写入。

第一版对 `blocked / failed` 不自动重跑。后续如需重跑，应增加明确的人工重跑入口或 retry 策略。

---

## 14. 状态与错误处理

### 14.1 blocked 场景

以下情况应 blocked：

```text
strategy_signal_run 不存在
strategy_signal_run.status 不是 success / partial_success
strategy_signal_run 没有 snapshot_id
strategy_signal_result 为空
有效策略数量不足
snapshot 对应 K线窗口不足以计算基础材料
防未来函数检查失败
本地数据库缺少必要已收盘 K线
```

### 14.2 failed 场景

以下情况应 failed：

```text
数据库异常
JSON 序列化异常
代码运行异常
不可恢复的计算异常
```

### 14.3 partial_success 场景

以下情况可以 partial_success：

```text
聚合主流程完成
但部分策略 failed / invalid / not_implemented
材料包主字段生成成功，但某些非核心材料不足
候选场景生成成功，但风险收益比因缺少目标位只能标记为 null
```

---

## 15. 禁止事项

第 18 阶段严禁：

```text
调用 DeepSeek / GPT / Claude 等大模型
生成最终交易建议
管理 active advice 生命周期
开仓、平仓、加仓、减仓、撤单
读取账户、订单、持仓、API 私钥
请求 Binance REST
请求 Binance WebSocket
修改 market_kline_4h 或 market_kline_1d 正式 K线表
新增 manual_repair
人工修改 K线数据
重新跑第 16 策略信号
重新生成第 15 MarketContextSnapshot
降低第 15 快照质量门禁
修改第 16 dry-run / confirm-write 语义
使用 target close 之后的未来 K线
```

---

## 16. 建议代码结构

可按现有项目结构调整，建议新增或修改：

```text
app/strategy/aggregation/
  types.py
  service.py
  repository.py
  material_builder.py
  indicators.py
  candidate_scenario_builder.py
  hermes_formatter.py

scripts/run_strategy_aggregation.py

tests/strategy_aggregation/
  test_strategy_aggregation_service.py
  test_material_builder.py
  test_indicators.py
  test_candidate_scenario_builder.py
  test_strategy_aggregation_cli.py
```

如项目已有更合适的目录规范，优先遵守 `AGENTS.md` 和现有模块边界。

---

## 17. 迁移要求

新增 Alembic migration，创建：

```text
strategy_aggregation_run
analysis_material_pack
```

表结构必须支持：

```text
run_id 追踪
snapshot_id 追踪
strategy_signal_run_id 追踪
trace_id 追踪
版本字段
JSON 材料保存
候选场景保存
验证计划保存
状态保存
错误信息保存
Hermes 投递状态保存
幂等约束
```

不得直接手写生产数据库 SQL 绕过 Alembic。

---

## 18. 测试要求

至少覆盖：

```text
1. success 的 strategy_signal_run 可以进入聚合。
2. partial_success 的 strategy_signal_run 可以进入聚合。
3. blocked / failed 的 strategy_signal_run 不允许聚合。
4. strategy_signal_run 缺失 snapshot_id 时 blocked。
5. strategy_signal_result 为空时 blocked。
6. Gann placeholder / not_implemented 不导致聚合失败。
7. 趋势偏多 + 风险低，candidate_direction 可以为 long。
8. 趋势偏多 + 风险极高，candidate_direction 应变成 wait 或 stop_trading。
9. 多空策略冲突时 conflict_level 升高。
10. material_pack 包含 swing high / swing low。
11. material_pack 包含 ATR_14 和 ATR_PERCENT。
12. material_pack 包含 3 / 6 / 20 根平均振幅。
13. material_pack 包含支撑压力候选。
14. material_pack 包含候选场景。
15. 候选场景包含成立条件、失效条件、目标观察区、初步风险收益比。
16. material_pack 包含大模型问题清单。
17. material_pack 包含 data_window_json。
18. material_pack 包含 future_leakage_guard_json。
19. 防未来函数检查能阻止使用 target close 之后的 K线。
20. 同一个 strategy_signal_run_id + 版本组合不重复生成聚合结果。
21. dry-run 不写 strategy_aggregation_run。
22. dry-run 不写 analysis_material_pack。
23. confirm-write 才写入。
24. Hermes 关闭时不发送。
25. Hermes 开启时发送策略聚合通知并记录发送结果。
26. 第 18 不调用第 15。
27. 第 18 不调用第 16。
28. 第 18 不调用大模型。
29. 第 18 不请求 Binance。
30. 第 18 不生成最终交易建议字段。
```

---

## 19. 验收命令

开发完成后至少运行：

```bash
python -m compileall app migrations scripts tests
python -m pytest tests/strategy_aggregation tests/strategy tests/scheduler
python -m scripts.check_project_invariants
python -m alembic upgrade head
```

如 `tests/strategy_aggregation` 尚不存在，应创建对应测试目录。

---

## 20. 手动验证流程

第 16 / 第 17 已经生成 `strategy_signal_run` 后，可以手动运行：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --dry-run
```

确认输出合理后再执行：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --confirm-write
```

然后查库：

```sql
SELECT * FROM strategy_aggregation_run ORDER BY id DESC LIMIT 5;
SELECT * FROM analysis_material_pack ORDER BY id DESC LIMIT 5;
```

重点确认：

```text
aggregation_run.strategy_signal_run_id 正确
aggregation_run.snapshot_id 正确
candidate_direction 合理
risk_gate_status 合理
conflict_level 合理
candidate_scenarios_json 非空
validation_plan_json 非空
analysis_material_pack.material_json 非空
analysis_material_pack.question_json 非空
analysis_material_pack.data_window_json 非空
analysis_material_pack.future_leakage_guard_json 显示未使用未来 K线
没有最终交易建议字段
没有自动交易行为
```

---

## 21. 与后续阶段关系

第 18 的输出将作为第 19 大模型分析层的输入。

第 19 不应重新从 K线表临时拼接核心数学材料，而应读取：

```text
analysis_material_pack.material_json
analysis_material_pack.question_json
strategy_aggregation_run.summary_json
strategy_aggregation_run.candidate_scenarios_json
```

第 20 最终建议生命周期层再读取：

```text
strategy_signal_run
strategy_aggregation_run
analysis_material_pack
llm_analysis_run
```

最终决定：

```text
new / continue / update / close / invalidate / complete / wait
```

第 18 不做这些生命周期动作。

---

## 22. 结束标准

第 18 阶段完成标准：

```text
1. 可以基于已有 strategy_signal_run 生成 strategy_aggregation_run。
2. 可以基于 snapshot / K线窗口生成 analysis_material_pack。
3. 可以生成可验证的 candidate_scenarios_json。
4. 可以生成 validation_plan_json，为后续复盘预留依据。
5. 支持 CLI dry-run 与 confirm-write。
6. 支持第 17 后置自动触发，但受 .env 控制。
7. 支持 Hermes 策略聚合通知，但受 .env 控制。
8. 幂等规则有效，同一 strategy_signal_run + 版本组合不重复生成聚合结果。
9. 单元测试覆盖成功、阻断、失败、幂等、Hermes、材料计算、防未来函数、候选场景。
10. 不调用大模型，不生成最终建议，不自动交易。
```
