# 23D_breakout_pullback_trigger_strategy.md

## 1. 阶段名称

第 23D 阶段：`breakout_pullback_trigger_strategy`

中文名称：突破 / 跌破 / 回踩 / 假突破确认策略。

---

## 2. 阶段目标

23D 是 23B、23C 之后的关键位行为确认层。

本阶段目标不是生成最终交易建议，而是让系统稳定回答：

```text
1. 价格是否正在突破关键压力区？
2. 价格是否已经有效突破？
3. 价格是否正在跌破关键支撑区？
4. 价格是否已经有效跌破？
5. 突破 / 跌破后是否发生回踩？
6. 回踩是否有效？
7. 当前更像真突破、假突破、真跌破、假跌破，还是证据不足？
8. 成交量是否支持当前判断？
```

23D 输出的是触发状态与过滤结论，不是交易指令。

---

## 3. 核心定位

23D 只负责：

```text
关键位附近的行为确认
```

具体包括：

```text
突破尝试
突破确认
突破失败
跌破尝试
跌破确认
跌破失败
回踩测试
回踩确认
假突破
假跌破
成交量确认 / 降权
```

23D 不负责：

```text
最终 advice
trade_setup
入场价
止损价
止盈价
盈亏比
仓位
自动交易
```

一句话：

```text
23C 告诉系统“关键价格区域在哪里”；
23D 告诉系统“价格在这些区域附近发生了什么”。
```

---

## 4. 方法来源与采用范围

23D 应借鉴成熟交易方法，但只采用与“突破 / 跌破 / 回踩 / 假突破确认”有关的部分。

### 4.1 method_basis

```text
1. Turtle breakout / 海龟突破思想
2. Donchian Channel / 唐奇安通道思想
3. Price Action / 价格行为突破确认
4. Pullback Confirmation / 回踩确认
5. Role Flip / 支撑压力转换
6. False Breakout / 假突破识别
7. Volume Confirmation / 成交量确认
8. 简化 ATR / 百分比突破过滤
```

### 4.2 used_parts

23D 本阶段实际采用：

```text
1. 读取 23C 公开 key_levels，识别当前正在测试的关键区域。
2. 判断收盘价是否有效站上压力区上沿。
3. 判断收盘价是否有效跌破支撑区下沿。
4. 判断刺破后是否收回关键区域内侧。
5. 判断突破 / 跌破后是否发生回踩。
6. 判断回踩后是否重新收回突破方向。
7. 使用成交量相对均量判断放量、缩量、异常放量。
8. 用成交量作为确认因子或降权因子，而不是唯一判断条件。
```

### 4.3 excluded_parts

23D 本阶段明确不采用：

```text
1. 完整海龟交易系统。
2. 海龟仓位单位。
3. 加仓规则。
4. 自动止损规则。
5. 多品种组合交易。
6. 完整订单流分析。
7. 完整流动性清理策略。
8. 最终交易建议。
9. 入场、止损、止盈、盈亏比。
```

---

## 5. 策略角色与能力声明

23D 沿用 23A / 23B / 23C 的策略角色协作架构。

### 5.1 strategy_role

```text
strategy_role = filter
```

原因：

```text
23D 的核心是判断关键位行为是否通过、阻断、不确定或不适用。
```

本阶段不新增 `trigger` 角色。

### 5.2 provides

配置中必须声明：

```yaml
provides:
  - breakout_confirmation
  - breakdown_confirmation
  - pullback_confirmation
  - false_breakout_filter
  - trigger_state
  - volume_confirmation
```

### 5.3 requires / consumes

23D 明确采用方案 B：

```text
先改 runner，使 23D 可以读取同轮 23C 的 common_result.key_levels。
```

23D 必须声明依赖：

```yaml
requires:
  - role: support_resistance
    provides: key_levels
consumes:
  - common_result.key_levels
```

23D 可以消费：

```text
23C common_result.key_levels
```

23D 禁止消费：

```text
23C strategy_payload_json.raw_swing_points
23C strategy_payload_json.merged_level_clusters
23C strategy_payload_json.cluster_scoring_details
23C 内部函数或私有算法
```

---

## 6. Runner / EvidenceContext 最小改造要求

23D 是依赖型策略，不应与 23C 完全无序并行。

本阶段需要对 16 策略 runner 做最小改造，使其支持：

```text
1. 按 requires / consumes 识别策略依赖。
2. 先运行 SupportResistanceStrategy。
3. 将 SupportResistanceStrategy 的 common_result.key_levels 放入本轮公开 EvidenceContext。
4. 再运行 BreakoutPullbackTriggerStrategy。
5. 23D 只读取 EvidenceContext 中的公开 key_levels。
```

建议数据流：

```text
StrategyEvaluationInput
↓
23C SupportResistanceStrategy
↓
common_result.key_levels
↓
EvidenceContext.public_role_outputs.support_resistance.key_levels
↓
23D BreakoutPullbackTriggerStrategy
↓
StrategyResult
```

限制：

```text
1. 本阶段只做 23D 所需的最小依赖传递。
2. 不做 23F 深度角色化聚合。
3. 不做 role_coverage_matrix。
4. 不做 evidence_missing 总表。
5. 不做 key_level_conflict 融合。
```

如果 23C 关闭、失败、或 key_levels 为空，23D 不得报错，应输出：

```text
trigger_state = insufficient_key_levels
filter_decision = not_applicable
reason_code = missing_support_resistance_key_levels
```

---

## 7. 新增策略模块

建议新增：

```text
app/strategy/strategies/breakout_pullback_trigger_strategy.py
configs/strategies/breakout_pullback_trigger_strategy.yaml
```

中文名称：突破回踩触发确认策略。

职责：

```text
基于 23C 公开 key_levels 和 4h / 1d K线窗口，判断关键位附近的突破、跌破、回踩、假突破与成交量确认状态。
```

---

## 8. 输入数据边界

23D 只允许使用主框架传入的统一输入与同轮公开证据。

允许：

```text
StrategyEvaluationInput
MarketContextSnapshot
4h K线窗口
1d K线窗口
snapshot_id
symbol
base_interval
higher_interval
EvidenceContext.public_role_outputs
23C common_result.key_levels
```

禁止：

```text
策略内部自行查询数据库
策略内部自行查询 MarketContextSnapshot
策略内部自行调用 23C 策略类
策略内部读取 23C strategy_payload_json
请求 Binance REST
请求 WebSocket
读取账户
读取持仓
调用 Hermes
调用大模型
调用交易接口
```

快照最新性、合格性、懒生成逻辑由 15 / 16 主框架负责，不得下放到 23D 策略内部。

---

## 9. 核心状态定义

### 9.1 trigger_state

```text
breakout_attempt
breakout_confirmed
breakout_failed
breakdown_attempt
breakdown_confirmed
breakdown_failed
pullback_testing
pullback_confirmed
pullback_failed
false_breakout
false_breakdown
no_clear_trigger
insufficient_key_levels
insufficient_data
unknown
```

说明：

```text
breakout：向上突破压力区。
breakdown：向下跌破支撑区。
pullback：突破 / 跌破后的回踩或反抽测试。
false_breakout：刺破压力但收回，偏假突破。
false_breakdown：跌破支撑但收回，偏假跌破。
```

### 9.2 filter_decision

```text
passed
blocked
uncertain
not_applicable
```

建议含义：

```text
passed：关键位行为通过基础确认。
blocked：出现明显假突破 / 假跌破 / 失败信号。
uncertain：有尝试但证据不足。
not_applicable：缺少 key_levels 或数据不足。
```

注意：

```text
passed 不是最终交易建议。
blocked 不是最终禁止交易。
最终决策由后续聚合层 / 建议层处理。
```

### 9.3 volume_state

```text
expanding
contracting
normal
spike
insufficient
unknown
```

含义：

```text
expanding：放量。
contracting：缩量。
normal：正常。
spike：异常放量。
insufficient：成交量数据不足。
unknown：无法判断。
```

### 9.4 volume_confirmation

```text
confirming
weakening
rejection_signal
neutral
insufficient
unknown
```

含义：

```text
confirming：成交量支持当前突破 / 跌破 / 回踩判断。
weakening：成交量不足，降低置信度。
rejection_signal：放量刺破后收回，偏假突破 / 假跌破证据。
neutral：成交量没有明显结论。
insufficient：成交量数据不足。
unknown：无法判断。
```

---

## 10. 核心算法流程

23D 初版采用简单、可解释、可复盘的规则。

### 10.1 选择正在测试的 key_level

从 23C 的 `common_result.key_levels` 中选择当前最相关区域。

优先级：

```text
1. nearest_resistance
2. nearest_support
3. range_upper_boundary
4. range_lower_boundary
5. role_flip_candidate
6. major_resistance
7. major_support
```

筛选依据：

```text
1. 距离当前价格近。
2. current_relevance_score 高。
3. confidence_score 高。
4. zone_quality 不是 outlier。
5. level_type 与当前价格行为匹配。
```

23D 可以同时评估多个 key_level，但最终 common_result 中应给出最主要的 `tested_level_summary`。

### 10.2 向上突破判断

基础条件：

```text
1. 测试对象为 resistance / range_upper_boundary / resistance_to_support 相关区域。
2. 最新或最近确认 K线收盘价站上 zone_high。
3. 突破幅度超过 min_breakout_pct 或 ATR 阈值。
4. 不是单纯长上影刺破。
```

可输出：

```text
价格进入或略破 zone，但未站稳 → breakout_attempt
收盘站上 zone_high 且幅度足够 → breakout_confirmed
刺破 zone_high 后收回 zone 内或 zone 下方 → false_breakout / breakout_failed
```

### 10.3 向下跌破判断

基础条件：

```text
1. 测试对象为 support / range_lower_boundary / support_to_resistance 相关区域。
2. 最新或最近确认 K线收盘价跌破 zone_low。
3. 跌破幅度超过 min_breakdown_pct 或 ATR 阈值。
4. 不是单纯长下影刺破。
```

可输出：

```text
价格进入或略破 zone，但未跌稳 → breakdown_attempt
收盘跌破 zone_low 且幅度足够 → breakdown_confirmed
刺破 zone_low 后收回 zone 内或 zone 上方 → false_breakdown / breakdown_failed
```

### 10.4 回踩确认判断

向上突破后的回踩：

```text
1. 近期发生过 breakout_confirmed 或价格已站上原压力区。
2. 后续价格回踩原压力区。
3. 收盘没有重新跌回 zone_low 下方。
4. 回踩后重新向上收回。
5. 回踩时缩量，反弹时放量，则置信度提高。
```

可输出：

```text
pullback_testing
pullback_confirmed
pullback_failed
```

向下跌破后的反抽：

```text
1. 近期发生过 breakdown_confirmed 或价格已跌破原支撑区。
2. 后续价格反抽原支撑区。
3. 收盘没有重新站回 zone_high 上方。
4. 反抽后重新向下。
```

本阶段可仍用 `pullback_testing / pullback_confirmed / pullback_failed` 表达，细节放在 payload 中标记 direction。

### 10.5 假突破 / 假跌破识别

假突破典型条件：

```text
1. high 刺破 zone_high。
2. close 收回 zone_high 下方或 zone 内。
3. 上影线明显。
4. 成交量放大或异常放大。
5. 后续 K线没有延续。
```

假跌破典型条件：

```text
1. low 刺破 zone_low。
2. close 收回 zone_low 上方或 zone 内。
3. 下影线明显。
4. 成交量放大或异常放大。
5. 后续 K线没有延续。
```

注意：

```text
放量 + 刺破 + 收回 = 可能是假突破证据。
放量 + 收盘站稳 + 后续延续 = 可能是真突破证据。
成交量是证据，不是结论。
```

### 10.6 成交量确认逻辑

计算：

```text
volume_ratio = 当前确认 K线成交量 / 最近 N 根平均成交量
```

建议：

```text
volume_ratio >= volume_spike_ratio → spike
volume_ratio >= volume_expand_ratio → expanding
volume_ratio <= volume_contract_ratio → contracting
否则 → normal
```

成交量解释：

```text
突破站稳 + 放量 → 提高 breakout_confirmed 置信度
突破站稳 + 缩量 → 降低置信度，可能 uncertain
刺破收回 + 放量 → 提高 false_breakout / false_breakdown 置信度
回踩缩量 + 反弹放量 → 提高 pullback_confirmed 置信度
```

边界：

```text
成交量不得作为唯一判断条件。
成交量缺失时不得抛异常，应输出 volume_state = insufficient。
```

---

## 11. 输出字段设计

23D 必须遵守 23A 三段结构：

```text
common_result
strategy_model_material_json
strategy_payload_json
```

### 11.1 common_result

`common_result` 只放公开可复用摘要。

建议包含：

```text
trigger_state
filter_decision
tested_level_summary
volume_state
volume_confirmation
confidence_score
reason_codes
reason_text
evidence_items
not_trading_advice = true
```

示例：

```json
{
  "trigger_state": "breakout_attempt",
  "filter_decision": "uncertain",
  "tested_level_summary": {
    "level_id": "SR-4H-RESISTANCE-002",
    "level_type": "resistance",
    "level_group": "nearest_resistance",
    "zone_low": "103800",
    "zone_high": "104300",
    "distance_from_current_price_pct": "0.4"
  },
  "volume_state": "expanding",
  "volume_confirmation": "neutral",
  "confidence_score": "0.58",
  "reason_codes": [
    "price_near_resistance_zone",
    "close_not_firmly_above_zone",
    "volume_expanding_but_no_confirmed_close"
  ],
  "reason_text": "价格接近 4h 最近压力区并出现放量，但尚未收盘有效站上压力区上沿，因此仅视为突破尝试。",
  "not_trading_advice": true
}
```

### 11.2 strategy_payload_json

私有计算细节放入 `strategy_payload_json`。

例如：

```text
tested_level_id
tested_level_type
tested_level_group
breakout_distance_pct
breakdown_distance_pct
close_position_relative_to_zone
wick_rejection_ratio
confirmation_bars
pullback_depth_pct
volume_ratio
volume_ma_period
breakout_bar_volume
average_volume
volume_confirmation_result
false_breakout_details
false_breakdown_details
pullback_detection_details
calculation_params
selected_key_level_candidates
```

禁止把这些私有细节塞进 `common_result`。

### 11.3 strategy_model_material_json

放后续模型层可读摘要。

例如：

```text
关键位行为摘要
突破 / 跌破 / 回踩证据
成交量证据
反方证据
不确定性
需要模型重点审查的问题
```

---

## 12. 配置要求

新增配置文件：

```text
configs/strategies/breakout_pullback_trigger_strategy.yaml
```

建议内容：

```yaml
enabled: true
strategy_name: breakout_pullback_trigger_strategy
strategy_version: "23D-1"
strategy_role: filter
provides:
  - breakout_confirmation
  - breakdown_confirmation
  - pullback_confirmation
  - false_breakout_filter
  - trigger_state
  - volume_confirmation
requires:
  - role: support_resistance
    provides: key_levels
consumes:
  - common_result.key_levels
base_interval: 4h
higher_interval: 1d
lookback_bars:
  base: 80
  higher: 120
minimum_required_bars:
  base: 40
  higher: 60
thresholds:
  min_breakout_pct: "0.003"
  min_breakdown_pct: "0.003"
  zone_touch_tolerance_pct: "0.004"
  wick_rejection_ratio: "0.45"
  pullback_max_depth_pct: "0.015"
  confirmation_bars: 2
  recent_trigger_lookback_bars: 6
volume:
  enabled: true
  volume_ma_period: 20
  volume_expand_ratio: "1.30"
  volume_spike_ratio: "2.00"
  volume_contract_ratio: "0.80"
output_limits:
  tested_levels: 5
```

配置不得写死在业务代码中。

---

## 13. 与 23B / 23C 的关系

### 13.1 与 23B

23B 输出：

```text
市场大方向
短期运行区间
当前阶段
```

23D 不直接依赖 23B 私有字段。

如果后续需要用 23B 的主状态过滤突破方向，应在 23F 聚合层处理。

### 13.2 与 23C

23C 输出：

```text
key_levels
support_zones
resistance_zones
range_boundaries
role_flip_candidates
```

23D 必须消费 23C 的公开 `common_result.key_levels`。

23D 不得重复实现完整支撑压力计算。

23D 不得读取 23C 私有 payload。

---

## 14. 与 18 链路关系

23D 策略必须能通过 16 阶段独立运行并落库。

必须保证：

```text
1. strategy_role = filter 能正确落库。
2. provides 能正确读取。
3. requires / consumes 能被 runner 或配置层识别。
4. common_payload_json 能保存 trigger_state / filter_decision / volume_state 摘要。
5. strategy_payload_json 能保存私有计算细节。
6. 当前 18 链路读取 23D 结果不崩溃。
```

本阶段不做 18 深度聚合增强。

23F 再处理：

```text
filter_summary
trigger_summary
role_coverage_matrix
evidence_missing
trigger_conflict
最终候选交易结构组合
```

---

## 15. 数据库与迁移

原则上不新增数据库表。

优先复用 23A 已有字段：

```text
strategy_role
common_payload_json
strategy_model_material_json
strategy_payload_json
validation_status
validation_errors_json
```

如果 Codex 认为必须新增字段或 migration，必须先汇报，不得擅自新增。

---

## 16. 本阶段不做

23D 明确不做：

```text
不做最终 advice
不做 trade_setup
不做入场价
不做止损价
不做止盈价
不做盈亏比
不做仓位计算
不做风控否决
不做江恩
不做斐波那契
不做完整流动性清理
不做订单流
不调用大模型
不发送 Hermes
不请求 Binance
不读取账户
不读取持仓
不做自动交易
不做人工执行反馈
不做完整复盘系统
不做 18 深度聚合重构
不做 23F 角色化聚合
```

---

## 17. 测试要求

至少新增或更新测试：

```text
1. BreakoutPullbackTriggerStrategy 输出 filter 角色 StrategyResult。
2. 配置声明 strategy_role = filter。
3. 配置声明 provides。
4. 配置声明 requires / consumes。
5. runner 能先运行 23C，并将 common_result.key_levels 传给 23D。
6. 23C 关闭或 key_levels 缺失时，23D 输出 insufficient_key_levels / not_applicable，不抛异常。
7. 能识别 breakout_attempt。
8. 能识别 breakout_confirmed。
9. 能识别 false_breakout。
10. 能识别 breakdown_attempt。
11. 能识别 breakdown_confirmed。
12. 能识别 false_breakdown。
13. 能识别 pullback_testing。
14. 能识别 pullback_confirmed。
15. 能计算 volume_ratio。
16. 放量站稳时提高确认置信度。
17. 放量刺破收回时提高假突破 / 假跌破置信度。
18. 缩量突破时输出 uncertain 或降低 confidence_score。
19. common_result 中有 trigger_state / filter_decision / volume_state。
20. strategy_payload_json 中有私有计算细节。
21. 私有细节不进入 common_result。
22. 数据不足时输出 insufficient_data，不抛异常。
23. 策略关闭后 runner 不报错。
24. 单个策略失败不影响其他策略运行。
25. run_strategy_signals 能正常落库 23D 结果。
26. 18 链路读取 23D 结果不崩溃。
```

建议运行：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```

如时间允许，再运行：

```bash
python -m pytest tests -q
```

---

## 18. 验收标准

23D 完成后，系统应能回答：

```text
1. 当前是否正在测试某个关键支撑 / 压力？
2. 测试的是哪个 key_level？
3. 是突破尝试、突破确认，还是假突破？
4. 是跌破尝试、跌破确认，还是假跌破？
5. 是否发生回踩测试？
6. 回踩是否确认？
7. 成交量是确认、削弱，还是提示假突破风险？
8. 当前 filter_decision 是 passed、blocked、uncertain，还是 not_applicable？
9. 这些结论是否只是触发状态，不是最终交易建议？
```

验收通过条件：

```text
1. BreakoutPullbackTriggerStrategy 已实现。
2. strategy_role = filter。
3. provides 正确。
4. requires / consumes 正确。
5. runner 支持 23D 消费 23C common_result.key_levels。
6. 23D 不读取 23C strategy_payload_json。
7. common_result / strategy_payload_json 边界清楚。
8. 不生成最终 advice。
9. 不生成 trade_setup。
10. 不输出 entry / stop_loss / take_profit。
11. 不发送 Hermes。
12. 不调用大模型。
13. 不请求 Binance。
14. 不读取账户或持仓。
15. 不新增自动交易语义。
16. 原则上不新增数据库迁移。
17. tests/strategy 通过。
18. tests/strategy_aggregation 通过或说明当前无对应测试范围。
```

---

## 19. 后续阶段建议

23D 完成后，建议继续：

```text
23E：波动率与风控否决策略
23F：角色化聚合层增强
```

23D 的核心不是告诉用户“该不该交易”，而是筛出：

```text
关键位附近是否出现可解释的触发行为
```
