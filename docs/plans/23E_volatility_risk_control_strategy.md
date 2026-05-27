# 23E_volatility_risk_control_strategy.md

## 1. 阶段名称

第 23E 阶段：`volatility_risk_control_strategy`

中文名称：波动率与风控闸门策略。

---

## 2. 阶段目标

23E 是 23B、23C、23D 之后的风险控制层。

本阶段不负责寻找机会，而是回答：

```text
当前风险条件是否允许把交易候选继续往后推进？
```

23E 需要判断：

```text
1. 当前市场整体风险是否过高。
2. 当前候选方向是否存在追单风险。
3. 当前价格到关键支撑 / 压力的空间是否足够。
4. 理论空间扣除手续费 / 滑点缓冲后是否仍有意义。
5. 23D 的突破 / 跌破 / 回踩触发是否应该被风控降级或否决。
6. 是否应该等待更好的价格、等待回踩、阻断某个方向，或阻断全部候选。
```

23E 输出的是风控证据，不是最终交易建议。

---

## 3. 核心定位

23E 是：

```text
风险过滤 / 风控闸门
```

一句话：

```text
23B 判断市场状态；
23C 判断关键价格区域；
23D 判断关键位行为；
23E 判断这个机会是否值得继续推进。
```

23E 可以否决或降级 23D 的触发状态，但不得生成最终 advice，不得生成 trade_setup。

---

## 4. 方法来源与采用范围

### 4.1 method_basis

```text
1. ATR / Average True Range / 平均真实波幅
2. Volatility Filter / 波动率过滤
3. Reward Risk Filter / 盈亏空间过滤
4. Chase Risk Filter / 追单风险过滤
5. Regime-aware Risk Control / 市场状态感知型风控
6. Fee and Slippage Buffer / 手续费与滑点缓冲
7. Conservative Risk Gate / 保守风控闸门
```

### 4.2 本阶段采用

```text
1. 用 ATR / 近期振幅判断波动率状态。
2. 用 23B 公开市场状态动态选择风控政策。
3. 用 23C 公开 key_levels 评估上下空间。
4. 用 23D 公开 trigger_state / filter_decision 判断候选触发质量。
5. 区分市场整体风险和当前候选风险。
6. 区分 long / short 方向的空间可行性。
7. 对追单过远、空间不足、极端波动、假突破风险进行降级或阻断。
8. 用手续费 / 滑点缓冲做粗过滤。
```

### 4.3 本阶段不采用

```text
1. 正式仓位计算。
2. 杠杆建议。
3. 保证金建议。
4. 最终入场价。
5. 最终止损价。
6. 最终止盈价。
7. 账户权益风控。
8. 资金费率风控。
9. 交易所强平模型。
10. 自动交易风控。
```

---

## 5. 策略角色与能力声明

### 5.1 strategy_role

```text
strategy_role = risk_control
```

### 5.2 provides

配置中必须声明：

```yaml
provides:
  - volatility_risk
  - trade_permission_filter
  - risk_gate_decision
  - reward_risk_feasibility
  - chase_risk
  - stop_distance_reference
  - market_state_aware_risk_policy
```

说明：

```text
stop_distance_reference 只是风险距离参考，不是正式止损价。
reward_risk_feasibility 只是空间可行性，不是正式盈亏比方案。
```

### 5.3 requires / consumes

23E 需要消费前序策略的公开输出：

```yaml
requires:
  - role: context
    provides:
      - primary_regime
      - regime_phase
      - market_environment_context
  - role: support_resistance
    provides:
      - key_levels
  - role: filter
    provides:
      - trigger_state
      - breakout_confirmation
      - breakdown_confirmation
      - pullback_confirmation
      - volume_confirmation
consumes:
  - common_result.primary_regime
  - common_result.regime_phase
  - common_result.market_environment_context
  - common_result.key_levels
  - common_result.trigger_state
  - common_result.filter_decision
  - common_result.tested_level_summary
  - common_result.volume_state
  - common_result.volume_confirmation
```

23E 禁止消费：

```text
23B strategy_payload_json
23C strategy_payload_json
23D strategy_payload_json
任何其他策略私有 payload
其他策略内部函数
其他策略私有算法
```

---

## 6. 市场状态感知型风控

23E 不能把市场状态写死成几个固定枚举后结束。

硬规则：

```text
1. 23E 不固定穷举所有市场形态。
2. 23E 必须消费 23B 的公开市场状态摘要。
3. 23E 根据 primary_regime、regime_phase、trend_strength、decision_implication 等公开字段动态选择风控政策。
4. 23B 后续升级并输出更细的市场阶段时，23E 应能通过配置或映射规则吸收新状态，而不是重写核心代码。
5. 未知状态一律保守处理，不默认放行。
```

### 6.1 risk_policy_profiles

建议使用配置化风控策略档案：

```yaml
risk_policy_profiles:
  default_conservative:
    unknown_regime_action: wait
    unknown_phase_action: wait
    max_chase_risk: low

  trend_following_favorable:
    countertrend_action: block_current_candidate
    chase_risk_limit: medium
    require_pullback_when_extended: true

  countertrend_caution:
    require_stronger_trigger: true
    chase_risk_limit: low
    default_action: wait

  range_caution:
    breakout_requires_volume: true
    middle_zone_action: wait
    edge_chase_action: block_current_candidate

  volatile_defensive:
    default_action: wait
    extreme_action: block_all_candidates
```

### 6.2 默认映射原则

配置可以根据 23B 输出动态映射：

```text
趋势延续倾向 → trend_following_favorable
趋势回调 / 反弹倾向 → countertrend_caution
震荡 / 箱体倾向 → range_caution
极端波动倾向 → volatile_defensive
未知 / 缺数据 → default_conservative
```

这些只是默认映射，不应把所有市场形态硬编码进代码。

---

## 7. 风险类型拆分

23E 不得只输出一个笼统风险等级。

至少拆成两类：

```text
global_market_risk
candidate_risk
```

### 7.1 global_market_risk

表示市场整体风险，枚举建议：

```text
normal
elevated
high
extreme
insufficient_data
unknown
```

来源：

```text
ATR
近期振幅
长影线比例
连续大阳 / 大阴
波动扩张程度
23B 市场状态
```

### 7.2 candidate_risk

表示当前候选触发风险，枚举建议：

```text
low
medium
high
extreme
not_applicable
unknown
```

来源：

```text
23D trigger_state
23D filter_decision
当前价格与 tested_level 的距离
当前价格与 23C key_levels 的距离
追单距离
上方 / 下方空间
假突破风险
成交量确认状态
```

---

## 8. 风控作用范围

23E 的否决必须说明作用范围，不得只写一个含糊的 `block`。

### 8.1 risk_gate_decision

建议枚举：

```text
allow
allow_with_caution
wait
block_long_candidate
block_short_candidate
block_current_candidate
block_all_candidates
insufficient_context
unknown
```

说明：

```text
allow：风险允许候选继续推进。
allow_with_caution：允许但需要谨慎，后续聚合层应降低权重。
wait：等待更好的触发、回踩或价格。
block_long_candidate：阻断多头候选。
block_short_candidate：阻断空头候选。
block_current_candidate：阻断当前候选，但不阻断所有方向。
block_all_candidates：极端风险下阻断所有候选。
insufficient_context：上下文不足，不能放行。
```

### 8.2 risk_scope

建议输出：

```text
long_only
short_only
current_candidate
all_candidates
none
unknown
```

极端波动、数据异常、结构严重冲突时，才允许 `all_candidates`。

---

## 9. 方向化空间评估

空间评估必须区分多空方向。

### 9.1 long_feasibility

多头方向关注：

```text
当前价格到上方压力 / 目标观察区的空间
当前价格到下方支撑 / 失效参考区的风险距离
是否已经远离支撑过多
是否接近上方压力
是否扣除手续费 / 滑点后空间不足
```

### 9.2 short_feasibility

空头方向关注：

```text
当前价格到下方支撑 / 目标观察区的空间
当前价格到上方压力 / 失效参考区的风险距离
是否已经远离压力过多
是否接近下方支撑
是否扣除手续费 / 滑点后空间不足
```

### 9.3 feasibility 枚举

```text
favorable
acceptable
poor
invalid
unknown
insufficient_context
```

注意：

```text
这里不是正式止盈止损计算。
这里只是判断空间是否值得继续推进。
```

---

## 10. 核心算法流程

23E 初版采用可解释的规则，不追求复杂优化。

### 10.1 读取公开上下文

读取：

```text
23B common_result 市场状态摘要
23C common_result.key_levels
23D common_result.trigger_state / filter_decision / tested_level_summary / volume_state / volume_confirmation
4h / 1d K线窗口
```

缺任一关键上下文时：

```text
不得默认 allow。
应输出 insufficient_context / wait / unknown。
```

### 10.2 计算波动率状态

计算：

```text
ATR
ATR_pct
recent_range_pct
average_range_pct
range_expansion_ratio
latest_bar_range_pct
wick_risk_score
```

输出：

```text
volatility_state:
  low_volatility
  normal_volatility
  high_volatility
  extreme_volatility
  insufficient_data
  unknown
```

规则示例：

```text
ATR_pct 明显高于均值 → high_volatility
单根 4h 振幅极端 → extreme_volatility
连续大阳 / 大阴后离关键位过远 → chase_risk 上升
```

### 10.3 根据 23B 选择风控 profile

根据 23B 公开状态选择：

```text
risk_policy_profile
```

如果 23B 缺失或未知：

```text
risk_policy_profile = default_conservative
risk_gate_decision 不得为 allow
```

### 10.4 计算追单风险

判断：

```text
当前价格距离 tested_level 是否过远
当前价格是否已经远离突破 / 跌破位
当前价格是否接近下一关键压力 / 支撑
连续上涨后追多
连续下跌后追空
```

输出：

```text
chase_risk:
  low
  medium
  high
  extreme
  unknown
```

### 10.5 计算空间可行性

基于 23C key_levels：

```text
distance_to_nearest_support_pct
distance_to_nearest_resistance_pct
long_room_to_resistance_pct
long_risk_to_support_pct
short_room_to_support_pct
short_risk_to_resistance_pct
rough_long_reward_risk_ratio
rough_short_reward_risk_ratio
```

加入缓冲：

```text
fee_buffer_pct
slippage_buffer_pct
min_net_room_pct
```

如果扣除缓冲后空间太小：

```text
long_feasibility / short_feasibility = poor 或 invalid
```

### 10.6 对 23D 触发进行风控降级

示例：

```text
23D = breakout_confirmed
但 chase_risk = high
→ risk_gate_decision = wait 或 block_current_candidate
```

```text
23D = false_breakout
→ risk_gate_decision = block_current_candidate
```

```text
23D = breakout_confirmed
但 volatility_state = extreme_volatility
→ risk_gate_decision = wait 或 block_all_candidates
```

```text
23D = pullback_confirmed
且空间可行、波动正常
→ risk_gate_decision = allow 或 allow_with_caution
```

注意：

```text
23E 可以否决或降级 23D。
23E 不得生成最终交易建议。
```

---

## 11. 输出字段设计

23E 必须遵守 23A 三段结构：

```text
common_result
strategy_model_material_json
strategy_payload_json
```

### 11.1 common_result

`common_result` 只放公开风控摘要。

建议包含：

```text
risk_gate_decision
risk_scope
global_market_risk
candidate_risk
volatility_state
chase_risk
long_feasibility
short_feasibility
selected_risk_policy_profile
confidence_score
reason_codes
reason_text
evidence_items
not_trading_advice = true
```

示例：

```json
{
  "risk_gate_decision": "wait",
  "risk_scope": "current_candidate",
  "global_market_risk": "elevated",
  "candidate_risk": "high",
  "volatility_state": "high_volatility",
  "chase_risk": "high",
  "long_feasibility": "poor",
  "short_feasibility": "unknown",
  "selected_risk_policy_profile": "trend_following_favorable",
  "confidence_score": "0.71",
  "reason_codes": [
    "breakout_confirmed_but_extended",
    "price_far_from_tested_level",
    "next_resistance_room_insufficient"
  ],
  "reason_text": "23D 显示突破确认，但当前价格已经明显远离测试位，上方到下一压力的净空间不足，风控建议等待回踩或新的确认。",
  "not_trading_advice": true
}
```

### 11.2 strategy_payload_json

私有计算细节放入 `strategy_payload_json`。

例如：

```text
atr_value
atr_pct
recent_range_pct
average_range_pct
range_expansion_ratio
latest_bar_range_pct
wick_risk_score
distance_to_nearest_support_pct
distance_to_nearest_resistance_pct
long_room_to_resistance_pct
long_risk_to_support_pct
short_room_to_support_pct
short_risk_to_resistance_pct
rough_long_reward_risk_ratio
rough_short_reward_risk_ratio
fee_buffer_pct
slippage_buffer_pct
min_net_room_pct
risk_policy_mapping_details
risk_scoring_details
calculation_params
```

禁止把这些私有细节塞进 `common_result`。

### 11.3 strategy_model_material_json

放后续模型层可读摘要：

```text
市场状态下的风控解释
当前触发是否被降级
空间是否足够
追单风险说明
波动率风险说明
反方证据
不确定性
需要模型重点审查的问题
```

---

## 12. 配置要求

新增配置文件：

```text
configs/strategies/volatility_risk_control_strategy.yaml
```

建议内容：

```yaml
enabled: true
strategy_name: volatility_risk_control_strategy
strategy_version: "23E-1"
strategy_role: risk_control
provides:
  - volatility_risk
  - trade_permission_filter
  - risk_gate_decision
  - reward_risk_feasibility
  - chase_risk
  - stop_distance_reference
  - market_state_aware_risk_policy
requires:
  - role: context
    provides:
      - primary_regime
      - regime_phase
      - market_environment_context
  - role: support_resistance
    provides:
      - key_levels
  - role: filter
    provides:
      - trigger_state
      - breakout_confirmation
      - breakdown_confirmation
      - pullback_confirmation
      - volume_confirmation
consumes:
  - common_result.primary_regime
  - common_result.regime_phase
  - common_result.market_environment_context
  - common_result.key_levels
  - common_result.trigger_state
  - common_result.filter_decision
  - common_result.tested_level_summary
  - common_result.volume_state
  - common_result.volume_confirmation
base_interval: 4h
higher_interval: 1d
lookback_bars:
  base: 80
  higher: 120
minimum_required_bars:
  base: 40
  higher: 60
atr:
  period: 14
thresholds:
  high_atr_pct: "0.035"
  extreme_atr_pct: "0.060"
  high_range_expansion_ratio: "1.60"
  extreme_range_expansion_ratio: "2.30"
  high_chase_distance_pct: "0.020"
  extreme_chase_distance_pct: "0.035"
  min_rough_reward_risk_ratio: "1.50"
  min_net_room_pct: "0.008"
  fee_buffer_pct: "0.0004"
  slippage_buffer_pct: "0.0010"
risk_policy_profiles:
  default_conservative:
    default_decision: wait
    unknown_context_decision: insufficient_context
  trend_following_favorable:
    default_decision: allow_with_caution
    countertrend_action: block_current_candidate
    max_chase_risk: medium
  countertrend_caution:
    default_decision: wait
    require_stronger_trigger: true
    max_chase_risk: low
  range_caution:
    default_decision: wait
    breakout_requires_volume: true
    middle_zone_action: wait
  volatile_defensive:
    default_decision: wait
    extreme_action: block_all_candidates
```

配置不得写死在业务代码中。

---

## 13. Runner / EvidenceContext 要求

23E 是依赖型策略。

需要确认 23D 已引入的 EvidenceContext 能继续支持：

```text
1. 23B / 23C / 23D 先运行。
2. 23E 后运行。
3. 23E 只读取前序策略 common_result 中的公开字段。
4. 23E 不读取任何前序策略 strategy_payload_json。
```

建议执行顺序：

```text
context 角色
↓
support_resistance 角色
↓
filter 角色
↓
risk_control 角色
```

本阶段只做 23E 需要的最小依赖读取，不做 23F 深度聚合。

---

## 14. 与 23B / 23C / 23D 的关系

### 14.1 与 23B

23E 需要消费 23B 公开市场状态，用于动态选择风控 profile。

23E 不应硬编码所有市场形态。

### 14.2 与 23C

23E 需要消费 23C 公开 key_levels，用于计算当前价格到支撑 / 压力的空间，评估多空方向空间可行性。

### 14.3 与 23D

23E 需要消费 23D 公开触发状态，用于判断突破 / 跌破 / 回踩是否值得继续推进，并对追单、假突破、缩量突破、极端波动进行降级或阻断。

---

## 15. 与 18 链路关系

23E 策略必须能通过 16 阶段独立运行并落库。

必须保证：

```text
1. strategy_role = risk_control 能正确落库。
2. provides 能正确读取。
3. requires / consumes 能被 runner 或配置层识别。
4. common_payload_json 能保存 risk_gate_decision / volatility_state / chase_risk 等摘要。
5. strategy_payload_json 能保存私有计算细节。
6. 当前 18 链路读取 23E 结果不崩溃。
```

本阶段不做 18 深度聚合增强。

---

## 16. 与 Hermes 通知的关系

本阶段不做 Hermes 推送。

但需要记录后续硬规则：

```text
所有最终策略通知必须包含 strategy_evidence_summary。
不得只发送最终操作结论。
每个参与本轮判断的策略至少输出一条中文摘要。
若某策略关闭、缺数据、失败或不适用，也必须在通知中简要说明。
```

原因：用户必须知道最终决定是由哪些策略证据推导出来的，不能只收到“等 60000 支撑做多”这种无证据链结论。

该规则应在 23F 或最终 advice / Hermes 通知层实现，不在 23E 中实现。

---

## 17. 数据库与迁移

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

## 18. 本阶段不做

23E 明确不做：

```text
不做最终 advice
不做 trade_setup
不做正式 entry
不做正式 stop_loss
不做正式 take_profit
不做仓位计算
不做杠杆建议
不做保证金建议
不读账户
不读持仓
不发送 Hermes
不调用大模型
不请求 Binance
不做自动交易
不做人工执行反馈
不做完整复盘系统
不做 18 深度聚合重构
不做 23F 角色化聚合
```

---

## 19. 测试要求

至少新增或更新测试：

```text
1. VolatilityRiskControlStrategy 输出 risk_control 角色 StrategyResult。
2. 配置声明 strategy_role = risk_control。
3. 配置声明 provides / requires / consumes。
4. runner 能在 23B / 23C / 23D 后运行 23E。
5. 23E 只读取前序 common_result，不读取 strategy_payload_json。
6. 23B 缺失或未知时，23E 使用 default_conservative，不默认 allow。
7. 23C key_levels 缺失时，23E 输出 insufficient_context，不默认 allow。
8. 23D trigger_state 缺失时，23E 输出 insufficient_context 或 wait，不抛异常。
9. 正常波动 + 空间足够 + pullback_confirmed → allow / allow_with_caution。
10. extreme_volatility → wait / block_all_candidates。
11. breakout_confirmed 但 chase_risk high → wait / block_current_candidate。
12. false_breakout → block_current_candidate。
13. 上方空间不足 → long_feasibility poor / invalid。
14. 下方空间不足 → short_feasibility poor / invalid。
15. fee_buffer / slippage_buffer 导致净空间不足 → feasibility poor / invalid。
16. long_feasibility 与 short_feasibility 分开计算。
17. global_market_risk 与 candidate_risk 分开输出。
18. risk_scope 正确表达 long_only / short_only / current_candidate / all_candidates。
19. common_result 包含 risk_gate_decision / volatility_state / chase_risk 等公开摘要。
20. strategy_payload_json 包含 ATR、距离、粗略盈亏比等私有细节。
21. 私有细节不进入 common_result。
22. 数据不足时不抛异常。
23. 策略关闭后 runner 不报错。
24. 单个策略失败不影响其他策略运行。
25. run_strategy_signals 能正常落库 23E 结果。
26. 18 链路读取 23E 结果不崩溃。
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

## 20. 验收标准

23E 完成后，系统应能回答：

```text
1. 当前市场整体风险是什么？
2. 当前候选风险是什么？
3. 当前波动率是否正常？
4. 当前是否存在追单风险？
5. 多头方向空间是否足够？
6. 空头方向空间是否足够？
7. 23D 的触发是否被 23E 降级或否决？
8. 23E 的风控结论作用范围是什么？
9. 当前风险策略是否根据 23B 市场状态动态选择？
10. 这是否只是风控证据，不是最终交易建议？
```

验收通过条件：

```text
1. VolatilityRiskControlStrategy 已实现。
2. strategy_role = risk_control。
3. provides 正确。
4. requires / consumes 正确。
5. runner 支持 23E 消费 23B / 23C / 23D 公开 common_result。
6. 23E 不读取任何前序策略 strategy_payload_json。
7. common_result / strategy_payload_json 边界清楚。
8. risk_gate_decision / risk_scope 表达清楚。
9. global_market_risk / candidate_risk 分开输出。
10. long_feasibility / short_feasibility 分开输出。
11. 不生成最终 advice。
12. 不生成 trade_setup。
13. 不输出正式 entry / stop_loss / take_profit。
14. 不发送 Hermes。
15. 不调用大模型。
16. 不请求 Binance。
17. 不读取账户或持仓。
18. 不新增自动交易语义。
19. 原则上不新增数据库迁移。
20. tests/strategy 通过。
21. tests/strategy_aggregation 通过或说明当前无对应测试范围。
```

---

## 21. 后续阶段建议

23E 完成后，建议进入：

```text
23F：角色化聚合层增强
```

23F 应重点处理：

```text
1. 汇总 23B / 23C / 23D / 23E 的角色化证据。
2. 输出 strategy_evidence_summary。
3. 标记每个策略对最终结论的支持、反对、降级或否决作用。
4. 处理证据缺失、策略关闭、策略失败。
5. 为最终 advice / Hermes 通知提供证据链。
```

23E 的核心不是告诉用户“该不该交易”，而是判断：

```text
当前候选是否值得继续推进。
```
