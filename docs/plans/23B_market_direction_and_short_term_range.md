# 23B_market_direction_and_short_term_range.md

## 1. 阶段名称

第 23B 阶段：`market_direction_and_short_term_range`

中文名称：市场大方向与短期行情区间识别。

---

## 2. 阶段目标

23B 是 23A 策略公共协议后的第一批真实策略开发。

本阶段目标不是生成最终交易建议，而是让系统稳定回答两个基础问题：

```text
1. 当前市场的大级别方向是什么？
2. 当前短期行情运行区间在哪里？
```

23B 输出的是策略证据，不是交易建议。

---

## 3. 核心定位

23B 只负责：

```text
市场大方向
+
短期结构区间
```

它为后续策略提供基础环境判断，包括：

```text
23C：支撑压力细化
23D：突破 / 回踩触发
23E：波动率与风控否决
23F：角色化聚合增强
```

23B 不负责入场、止损、止盈、盈亏比和最终 advice。

---

## 4. 本阶段核心原则

### 4.1 只做方向与区间

23B 允许输出：

```text
primary_regime
regime_phase
trend_bias
trend_strength
recent_range_high
recent_range_low
range_position
range_quality
confidence_score
reason_codes
reason_text
evidence_items
```

23B 禁止输出：

```text
entry_price
stop_loss
take_profit
risk_reward_ratio
final_advice
trade_setup
建议开多
建议开空
建议加仓
建议减仓
建议平仓
```

### 4.2 主状态与阶段状态分离

23B 必须区分：

```text
primary_regime：大级别主环境
regime_phase：当前所处阶段
```

例如：

```text
primary_regime = downtrend
regime_phase = countertrend_rebound
```

这表示“大级别仍偏空，但当前处于下跌途中的反弹”，不能误判为 `uptrend`，也不能简单理解为立刻追空。

### 4.3 双层状态不是加权信号

`primary_regime` 和 `regime_phase` 不是两个方向信号做加权。

正确理解：

```text
primary_regime 定义主环境
regime_phase 描述当前阶段
```

禁止写成：

```text
primary_regime 得分 60%
regime_phase 得分 40%
加权后输出交易方向
```

### 4.4 短期区间不是正式支撑压力策略

23B 可以识别近期运行区间，例如 recent high / recent low / range position。

但 23B 不做完整支撑压力策略。

正式支撑、压力、失效位、目标观察区，留给 23C。

---

---

## 5. 策略角色与能力声明规则

23B 必须沿用 0005 决策文档中的“策略角色协作 / 策略接力”架构。

### 5.1 每个正式策略必须声明 strategy_role

所有正式接入系统的策略都必须声明：

```text
strategy_role
```

原因：

```text
没有 strategy_role
→ 聚合层不知道它是方向证据、位置证据、风控证据、过滤证据，还是背景证据
→ 策略结果无法被稳定消费
```

### 5.2 strategy_role 不是唯一插槽

`strategy_role` 不是“一类只能有一个策略”的固定坑位。

正确理解：

```text
strategy_role = 证据大类
同一个 strategy_role 下允许多个策略并存
```

例如，后续可以同时存在：

```text
TrendStructureStrategy
strategy_role = directional
provides = [trend_structure, direction_bias]

MovingAverageTrendStrategy
strategy_role = directional
provides = [ma_trend_filter, direction_bias]

MomentumTrendStrategy
strategy_role = directional
provides = [momentum_confirmation, direction_bias]
```

它们都属于 `directional` 证据组，不冲突。

聚合层未来应按：

```text
strategy_role 分组
+
provides / capability 汇总
+
一致性、冲突程度、证据质量评估
```

不得简单按策略数量投票。

### 5.3 role 是大类职责，不是策略名称

禁止把具体策略名称当成 role。

错误示例：

```text
strategy_role = gann
strategy_role = fibonacci
strategy_role = liquidity
strategy_role = whale_tracking
```

正确示例：

```text
strategy_role = context
provides = [gann_structure_projection]

strategy_role = directional
provides = [ma_trend_filter, direction_bias]

strategy_role = filter
provides = [breakout_confirmation, false_breakout_filter]
```

### 5.4 provides / capability 表示具体能力

每个策略配置中必须声明 `provides`。

`provides` 表示该策略提供的具体能力，不改变公共 schema。

例如：

```yaml
strategy_name: market_direction_regime_strategy
enabled: true
strategy_role: context
provides:
  - primary_regime
  - regime_phase
  - market_environment_context
```

```yaml
strategy_name: short_term_range_strategy
enabled: true
strategy_role: context
provides:
  - short_term_range
  - range_position
  - range_quality
```

### 5.5 新增 role 必须克制

新增策略时，优先使用现有 role：

```text
directional
support_resistance
risk_control
filter
context
placeholder
```

只有出现新的“系统职责类型”，才允许新增 role。

新增 role 必须同时更新：

```text
constants
validator
文档
测试
聚合规则
```

不得因为新增某个具体策略就新增 role。

### 5.6 策略可以增删，但结论等级会变化

后续 23G / 23H / 23I / 23J 等策略都应作为可插拔积木存在。

但规则是：

```text
策略关闭 ≠ 系统失败
策略关闭 = 对应证据减少
```

聚合层未来必须支持：

```text
证据完整 → 可以生成候选交易结构
证据部分缺失 → 只能输出 wait / 观察
关键证据缺失 → no_valid_setup
风控否决 → stop_trading / wait
```

严禁用 null、默认值或伪造字段补齐缺失证据。


## 6. 策略模块

23B 建议实现两个策略模块。

---

## 6.1 MarketDirectionRegimeStrategy

建议文件：

```text
app/strategy/strategies/market_direction_regime_strategy.py
configs/strategies/market_direction_regime_strategy.yaml
```

中文名称：市场大方向识别策略。

建议角色：

```text
strategy_role = context
```

建议能力：

```yaml
provides:
  - primary_regime
  - regime_phase
  - market_environment_context
```

职责：

```text
判断当前市场大级别主状态与当前阶段。
```

### 6.1.1 输出内容

通过 `common_result` 输出：

```text
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
context_summary
not_trading_advice = true
```

通过 `strategy_payload_json` 输出策略私有结构：

```json
{
  "primary_regime": "downtrend",
  "regime_phase": "countertrend_rebound",
  "trend_strength": "0.62",
  "regime_confidence": "0.70",
  "phase_confidence": "0.58",
  "decision_implication": "大级别偏空但当前处于反弹修复，后续空头策略不应直接追空，应等待反弹失败确认。"
}
```

注意：

```text
primary_regime / regime_phase 暂不进入 common_result。
```

后续如果聚合层需要强消费这些字段，应通过独立 adapter 读取 `strategy_payload_json`，不得直接扩大 23A 公共 schema。

### 6.1.2 primary_regime 枚举

```text
uptrend
downtrend
range
volatile
mixed
insufficient_data
unknown
```

| 值 | 含义 |
|---|---|
| uptrend | 大级别上涨趋势 |
| downtrend | 大级别下跌趋势 |
| range | 大级别震荡 |
| volatile | 高波动异常环境 |
| mixed | 多空结构冲突，方向不清 |
| insufficient_data | 数据不足 |
| unknown | 无法判断 |

### 6.1.3 regime_phase 枚举

```text
trend_continuation
pullback_in_uptrend
countertrend_rebound
range_mid_rotation
range_support_rebound
range_resistance_rejection
breakout_attempt
breakdown_attempt
false_breakout
transition
unknown
```

| 值 | 含义 |
|---|---|
| trend_continuation | 趋势延续 |
| pullback_in_uptrend | 上涨趋势中的回调 |
| countertrend_rebound | 下跌趋势中的反弹 |
| range_mid_rotation | 震荡区间中部轮动 |
| range_support_rebound | 震荡区间下沿反弹 |
| range_resistance_rejection | 震荡区间上沿回落 |
| breakout_attempt | 向上突破尝试 |
| breakdown_attempt | 向下跌破尝试 |
| false_breakout | 假突破后回落或收回 |
| transition | 状态切换期 |
| unknown | 无法判断 |

### 6.1.4 判断输入

只允许使用：

```text
MarketContextSnapshot
4h K线窗口
1d K线窗口
StrategyEvaluationInput
```

禁止：

```text
请求 Binance REST
请求 WebSocket
读取账户
读取持仓
调用 Hermes
调用大模型
```

### 6.1.5 初版判断思路

初版使用简单、可解释规则。

可参考：

```text
1d 高低点结构
1d 均线方向
4h 高低点结构
4h 近期反弹 / 回调状态
4h 区间位置
近期振幅变化
```

示例：

```text
1d 高低点抬高 + 4h 高低点抬高
→ primary_regime = uptrend
→ regime_phase = trend_continuation

1d 高低点降低 + 4h 反弹但未突破关键下跌结构
→ primary_regime = downtrend
→ regime_phase = countertrend_rebound

1d 方向不清 + 4h 在明确上下沿之间反复
→ primary_regime = range
→ regime_phase = range_mid_rotation / range_support_rebound / range_resistance_rejection

4h 正在突破近期区间上沿但未确认
→ primary_regime = range 或 mixed
→ regime_phase = breakout_attempt
```

---

## 6.2 ShortTermRangeStrategy

建议文件：

```text
app/strategy/strategies/short_term_range_strategy.py
configs/strategies/short_term_range_strategy.yaml
```

中文名称：短期行情区间识别策略。

建议角色：

```text
strategy_role = context
```

建议能力：

```yaml
provides:
  - short_term_range
  - range_position
  - range_quality
```

职责：

```text
识别当前短期行情运行区间，不做正式支撑压力判断。
```

### 6.2.1 输出内容

通过 `common_result` 输出：

```text
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
context_summary
not_trading_advice = true
```

通过 `strategy_payload_json` 输出策略私有结构：

```json
{
  "recent_range_high": "108000",
  "recent_range_low": "102000",
  "range_mid": "105000",
  "range_width_pct": "5.88",
  "range_position": "upper_half",
  "range_quality": "clear",
  "range_basis": "recent_4h_swing_window"
}
```

### 6.2.2 range_position 枚举

```text
above_range
upper_edge
upper_half
middle
lower_half
lower_edge
below_range
unknown
```

| 值 | 含义 |
|---|---|
| above_range | 价格已在短期区间上方 |
| upper_edge | 接近短期区间上沿 |
| upper_half | 位于短期区间上半部 |
| middle | 位于短期区间中部 |
| lower_half | 位于短期区间下半部 |
| lower_edge | 接近短期区间下沿 |
| below_range | 价格已在短期区间下方 |
| unknown | 无法判断 |

### 6.2.3 range_quality 枚举

```text
clear
weak
wide
narrow
noisy
insufficient_data
unknown
```

| 值 | 含义 |
|---|---|
| clear | 区间相对清晰 |
| weak | 区间证据较弱 |
| wide | 区间过宽，参考价值下降 |
| narrow | 区间过窄，容易被噪声击穿 |
| noisy | 插针和来回穿越较多 |
| insufficient_data | 数据不足 |
| unknown | 无法判断 |

### 6.2.4 区间识别边界

23B 的短期区间只表示近期行情运行范围。

允许：

```text
recent_range_high
recent_range_low
range_mid
range_position
range_width_pct
range_quality
```

禁止：

```text
强支撑
强压力
入场位
止损位
目标位
突破确认位
正式失效位
```

这些留给 23C 支撑压力策略。

---

## 7. TrendStructureStrategy 是否在 23B 升级

如果当前已有 `TrendStructureStrategy` 简易版，23B 可进行轻量升级，但不得扩大成完整交易策略。

允许升级：

```text
1. 使用 23B 的市场状态与区间结果作为背景参考。
2. 输出更清晰的 trend_bias。
3. 输出更清晰的 reason_codes / reason_text / evidence_items。
4. 保持 strategy_role = directional。
5. 在配置中声明 provides，例如 [trend_structure, direction_bias]。
```

禁止升级为：

```text
入场策略
止损策略
目标策略
最终 advice 策略
```

如果升级复杂度较高，23B 可只新增 MarketDirectionRegimeStrategy 与 ShortTermRangeStrategy，TrendStructureStrategy 留到后续单独增强。

---

## 8. 字段边界

23B 必须遵守 23A 三段结构：

```text
common_result
strategy_model_material_json
strategy_payload_json
```

### 8.1 common_result

只放角色化公共证据，例如：

```text
market_bias
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
context_summary
not_trading_advice
```

### 8.2 strategy_payload_json

放 23B 策略私有结构，例如：

```text
primary_regime
regime_phase
trend_strength
regime_confidence
phase_confidence
recent_range_high
recent_range_low
range_position
range_quality
range_width_pct
decision_implication
```

### 8.3 strategy_model_material_json

放后续模型层可读材料摘要，不作为公共聚合字段。

例如：

```text
状态判断摘要
短期区间摘要
主要证据
反方证据
注意事项
```

### 8.4 公共 schema 不得按策略名膨胀

新增策略不得要求修改 `common_result` 公共结构。

禁止：

```text
common_result.gann_angle
common_result.fibonacci_618
common_result.liquidity_sweep_detail
common_result.short_term_range_private_detail
```

允许：

```text
strategy_payload_json.gann_angle
strategy_payload_json.fibonacci_618
strategy_payload_json.liquidity_sweep_detail
strategy_payload_json.short_term_range_private_detail
```

如果未来聚合层确实需要读取某个策略私有字段，必须通过独立 adapter 做可选解析。

adapter 缺失、失败或策略关闭时，只能降级为 `evidence_missing / wait`，不得导致主链路失败。

---

## 9. 本阶段不做

23B 明确不做：

```text
不做完整支撑压力策略
不做突破 / 回踩触发策略
不做江恩策略
不做斐波那契策略
不做流动性清理策略
不做资金费率 / OI 策略
不做最终 advice
不做 trade_setup
不做 Hermes 通知
不调用大模型
不读取账户
不读取持仓
不请求 Binance
不做自动交易
不做人工执行反馈
不做完整复盘系统
```

---

## 10. 数据库与迁移

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

如 Codex 发现必须新增字段，必须先汇报，不得擅自新增 migration。

---

## 11. 与 16 / 18 链路关系

23B 策略必须能通过 16 阶段独立运行并落库。

23B 不要求重构完整 18 聚合层。

但必须保证：

```text
1. strategy_role 能正确落库。
2. common_payload_json 能正确落库。
3. strategy_payload_json 能正确落库。
4. strategy_payload_json 私有字段不会进入 common_result。
5. 当前 18 链路读取新策略结果时不崩溃。
```

如果当前 18 对新角色字段消费不充分，只做最小兼容修复，不做大规模聚合重构。

未来 23F 聚合层应按：

```text
strategy_role
+
provides / capability
+
证据一致性
+
证据冲突程度
+
缺失证据情况
```

汇总，而不是按具体策略名称硬编码。

---

## 12. 配置要求

每个策略必须有独立配置文件。

配置至少包含：

```text
enabled
strategy_name
strategy_version
strategy_role
provides
base_interval
higher_interval
lookback_bars
minimum_required_bars
thresholds
```

配置不得写死在业务代码中。

示例：

```yaml
enabled: true
strategy_name: market_direction_regime_strategy
strategy_version: "23B-1"
strategy_role: context
provides:
  - primary_regime
  - regime_phase
  - market_environment_context
base_interval: 4h
higher_interval: 1d
lookback_bars:
  base: 180
  higher: 365
minimum_required_bars:
  base: 120
  higher: 120
thresholds:
  trend_strength_min: "0.55"
```

---

## 13. 测试要求

至少新增或更新测试：

```text
1. MarketDirectionRegimeStrategy 输出 context 角色 StrategyResult。
2. MarketDirectionRegimeStrategy 配置声明 provides。
3. ShortTermRangeStrategy 输出 context 角色 StrategyResult。
4. ShortTermRangeStrategy 配置声明 provides。
5. TrendStructureStrategy 如本阶段升级，必须继续输出 directional 角色 StrategyResult。
6. 同一个 strategy_role 下允许多个策略并存，runner 不应因为 role 重复而失败。
7. context 成功时必须有 context_summary 或 evidence_items。
8. strategy_payload_json 中包含 primary_regime / regime_phase 等私有字段时，common_result 不包含这些字段。
9. ShortTermRangeStrategy 能输出 recent_range_high / recent_range_low / range_position / range_quality。
10. 数据不足时输出 insufficient_data，不得抛异常。
11. 策略关闭后 runner 不报错。
12. 单个策略失败不影响其他策略运行。
13. run_strategy_signals 能正常落库新字段。
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

## 14. 验收标准

23B 完成后，系统应能回答：

```text
1. 当前市场大级别主状态是什么？
2. 当前处于主状态中的哪个阶段？
3. 当前短期运行区间高低点在哪里？
4. 当前价格处于短期区间的什么位置？
5. 区间质量是否清晰？
6. 为什么这么判断？
7. 有哪些主要证据？
8. 有哪些反方证据或不确定性？
9. 这是否只是市场证据，而不是交易建议？
```

验收通过条件：

```text
1. 新策略均使用 StrategyResult 输出。
2. strategy_role 正确。
3. 每个策略配置声明 provides。
4. 同一个 strategy_role 下允许多个策略并存。
5. common_result / strategy_payload_json 边界清楚。
6. strategy_payload_json 私有字段不进入 common_result。
7. 不生成最终 advice。
8. 不生成 trade_setup。
9. 不发送 Hermes。
10. 不调用大模型。
11. 不新增自动交易语义。
12. 不新增数据库迁移，除非提前说明并经人工确认。
13. tests/strategy 通过。
14. tests/strategy_aggregation 通过或说明当前无对应测试范围。
```

---

## 15. 后续阶段建议

23B 完成后，建议继续：

```text
23C：支撑压力策略
23D：突破 / 回踩触发策略
23E：波动率与风控否决策略
23F：角色化聚合层增强
```

23B 不应贪多。

本阶段只建立：

```text
市场大方向
+
短期行情区间
```

这是后续交易结构生成的基础。
