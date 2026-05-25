# 23A Strategy Common Contract Plan：策略公共协议与结果适配层

## 1. 阶段定位

23A 不是新的策略编号，也不是独立运行阶段。

23A 的定位是：在现有第 16 阶段策略框架基础上，补齐“真实策略开发前必须统一的公共协议、上下文视图、结果校验、结果适配和落库字段”。

当前实际链路必须按仓库现状理解：

```text
第 17 scheduler / CLI
  -> 调用第 16 StrategySignalService
  -> 第 16 内部 SnapshotResolver 确保可用 MarketContextSnapshot
       -> 必要时懒调用第 15 MarketContextSnapshotService
  -> 第 16 构造 StrategyEvaluationInput / StrategyContextView
  -> 第 16 加载独立策略文件并运行
  -> 独立策略 return StrategyResult
  -> 23A 公共协议层校验 / 适配 / 落库
  -> 第 18 消费已落库 strategy_signal_run / strategy_signal_result
  -> 第 19 / 20 / 21 后续模型审查与最终建议
```

禁止写成：

```text
15 -> 16 -> 23A -> strategy
```

原因：第 15 快照不是主动上游流水线任务；第 16 才是运行入口，第 16 内部通过懒生成机制使用第 15。

---

## 2. 本阶段目标

23A 只做公共层，不做任何具体真实策略。

本阶段目标：

1. 定义统一的 `StrategyContextView`，让所有真实策略使用同一份只读市场上下文。
2. 定义统一的 `StrategyResult` 公共输出协议。
3. 明确哪些字段属于公共字段，哪些字段属于策略私有扩展字段。
4. 实现策略结果校验器，防止真实策略输出脏数据、伪交易指令、缺失关键字段。
5. 实现策略结果适配器，把新协议落到现有 `strategy_signal_result` 或新增字段中。
6. 最小调整第 18 的读取适配，使其能优先读取公共协议字段。
7. 保持第 15、第 17 调度链路不变。
8. 不开发支撑压力策略、江恩策略、趋势策略等真实策略算法。

---

## 3. 现有阶段关系

### 3.1 第 15 阶段

第 15 已负责 `MarketContextSnapshot`。

23A 不修改第 15：

1. 不改快照表结构。
2. 不改变快照懒生成机制。
3. 不请求 Binance。
4. 不写 K线表。
5. 不把策略指标塞进 snapshot。

### 3.2 第 16 阶段

第 16 已有：

```text
app/strategy/types.py
app/strategy/base.py
app/strategy/runner.py
app/strategy/input_builder.py
app/strategy/snapshot_resolver.py
app/strategy/signal_service.py
app/strategy/result_repository.py
app/strategy/strategies/
```

23A 是对第 16 策略框架的升级，不是另起炉灶。

23A 允许修改第 16 的公共类型、runner、repository 和占位策略，使其支持新协议。

### 3.3 第 17 阶段

第 17 只负责编排触发第 16。

23A 不修改第 17 的调度规则：

1. 不新增独立 23A scheduler。
2. 不让第 17 直接调用第 15。
3. 不让第 17 直接跑具体策略。
4. 不改变 UTC 00:00 等待 1d 采集的规则。
5. 不改变幂等规则。

如现有第 17 通知需要读取新字段，只做兼容展示，不扩大通知功能。

### 3.4 第 18 阶段

第 18 消费第 16 落库结果。

23A 需要最小调整第 18 的输入适配：

1. 优先读取新公共协议字段。
2. 保留旧 `metrics/debug_info/reason_codes` 兼容。
3. 不重写第 18 聚合算法。
4. 不重新跑第 16。
5. 不调用大模型。
6. 不生成最终建议。

---

## 4. 强制边界

### 4.1 允许做

1. 新增 `StrategyContextView`。
2. 新增 `StrategyResult` / `StrategyCommonPayload` / `StrategyExtensionPayload`。
3. 新增策略角色枚举。
4. 新增关键价位公共结构。
5. 新增候选观察场景公共结构。
6. 新增风险标记、证据项公共结构。
7. 新增结果校验器。
8. 新增结果适配器。
9. 更新 `BaseStrategy` 接口或提供兼容适配。
10. 更新现有占位策略以返回新协议。
11. 更新 `StrategyRunner`，在落库前校验策略结果。
12. 更新 `StrategySignalResult` 持久化字段或 JSON 字段。
13. 最小调整第 18 输入读取。
14. 补测试。
15. 补 implementation 文档。

### 4.2 禁止做

1. 不开发支撑压力策略算法。
2. 不开发江恩策略算法。
3. 不开发趋势真实策略升级。
4. 不开发波动率真实风控策略升级。
5. 不新增自动交易。
6. 不读取账户。
7. 不读取持仓。
8. 不请求 Binance REST。
9. 不请求 Binance WebSocket。
10. 不写 K线表。
11. 不修改第 15 快照表结构。
12. 不改变第 17 调度幂等。
13. 不调用 DeepSeek / GPT / Claude。
14. 不生成最终交易建议。
15. 不创建建议生命周期。
16. 不做回测系统。
17. 不做 Admin。
18. 不做 Hermes 自然语言交互。
19. 不把策略私有字段硬塞进公共 DTO。
20. 不把公共层做成具体策略逻辑集合。

---

## 5. 核心设计原则

### 5.1 公共字段和私有字段分离

每个策略返回结果分两块：

```text
common_payload      公共字段，供 18 / 19 / 20 / 21 通用消费
extension_payload   策略私有字段，只由该策略解释
```

公共层只理解 `common_payload`。

公共层不理解江恩扇形角度、时间窗口、特殊数学结构等策略私有细节。

例如江恩策略未来可以在 `extension_payload` 中放：

```json
{
  "gann": {
    "fan_origin": "...",
    "angle_lines": [],
    "time_windows": [],
    "price_time_square": {}
  }
}
```

支撑压力策略未来可以在 `extension_payload` 中放：

```json
{
  "support_resistance": {
    "swing_points": [],
    "level_clusters": [],
    "touch_statistics": {}
  }
}
```

公共层不能为这些私有字段新增专属属性。

### 5.2 策略文件自治

每个真实策略仍然是独立文件或独立类：

```text
app/strategy/strategies/support_resistance_strategy.py
app/strategy/strategies/gann_strategy.py
app/strategy/strategies/trend_strategy.py
```

策略内部可以有自己的计算细节。

但 return 时必须符合统一 `StrategyResult` 协议。

### 5.3 结果先校验，再落库

策略返回结果后，必须经过：

```text
StrategyResultValidator
  -> StrategyResultAdapter
  -> StrategySignalResultRepository
```

禁止策略直接写数据库。

禁止策略绕过校验。

### 5.4 方向倾向不是交易指令

公共协议允许表达：

```text
bullish_bias
bearish_bias
neutral
mixed
wait
not_applicable
```

禁止策略输出：

```text
buy
sell
open_position
close_position
add_position
reduce_position
must_trade
```

如果需要表达候选场景，只能用“观察场景 / 候选结构”，不能写成操作指令。

---

## 6. 新增核心类型

建议新增：

```text
app/strategy/context_view.py
app/strategy/result_contract.py
app/strategy/result_validator.py
app/strategy/result_adapter.py
app/strategy/common_indicators.py
```

也可以按现有代码风格合并到 `types.py`，但不允许把文件做成大杂烩。

---

## 7. StrategyContextView

`StrategyEvaluationInput` 是第 16 已有的原始输入对象。

23A 新增 `StrategyContextView`，作为真实策略使用的只读视图。

建议字段：

```text
snapshot_id
symbol
base_interval_value
higher_interval_value

base_klines
higher_klines

latest_base_open_time_ms
latest_higher_open_time_ms
latest_base_close_price
latest_higher_close_price

base_start_open_time_ms
base_end_open_time_ms
higher_start_open_time_ms
higher_end_open_time_ms

base_window_count
higher_window_count

trace_id
evaluated_at_utc
```

允许提供只读辅助方法：

```text
latest_base_close()
recent_base_high(window)
recent_base_low(window)
recent_base_range(window)
```

禁止在 `StrategyContextView` 中：

1. 查询数据库。
2. 请求 Binance。
3. 写 Redis。
4. 发送 Hermes。
5. 调用大模型。
6. 生成交易建议。
7. 放入某个具体策略的专属计算结果。

---

## 8. StrategyResult 公共协议

建议定义：

```text
StrategyResult
  contract_version
  strategy_name
  strategy_version
  strategy_role
  strategy_status
  common_payload
  extension_payload
  trace_id
```

### 8.1 strategy_role

建议枚举：

```text
directional          方向型策略
support_resistance   支撑压力策略
risk_control         风控策略
filter               过滤器策略
context              背景信息策略
placeholder          占位策略
```

### 8.2 strategy_status

沿用现有枚举：

```text
success
no_signal
invalid
not_implemented
failed
```

### 8.3 common_payload

公共字段建议：

```text
market_bias
risk_level
signal_strength
confidence_score
reason_codes
reason_text

key_levels
scenario_candidates
risk_flags
evidence_items
observation_window

not_trading_advice
```

要求：

```text
not_trading_advice = true
```

### 8.4 extension_payload

类型：

```text
Mapping[str, Any]
```

规则：

1. 可以为空。
2. 必须 JSON 可序列化。
3. 必须有大小限制。
4. 不允许放 K线全量数组。
5. 不允许放密钥、账户、订单、持仓。
6. 不允许放最终交易指令。
7. 策略私有字段必须放这里。

---

## 9. 公共结构定义

### 9.1 KeyLevel

关键价位公共结构：

```text
level_type
price
zone_low
zone_high
strength
source
timeframe
reason
```

`level_type` 建议：

```text
support
resistance
trigger
invalidation
target_observation
reference
```

注意：`target_observation` 是观察目标，不是止盈指令。

### 9.2 ScenarioCandidate

候选观察场景：

```text
scenario_type
direction_bias
activation_condition
invalidation_condition
target_observation_zone
risk_boundary
observation_period_bars
preliminary_reward_risk_ratio
supporting_evidence
opposing_evidence
```

`scenario_type` 建议：

```text
long_candidate
short_candidate
wait
risk_block
observation_only
```

禁止使用：

```text
open_long
open_short
buy
sell
```

### 9.3 RiskFlag

风险标记：

```text
risk_type
risk_level
triggered
reason
source
```

### 9.4 EvidenceItem

证据项：

```text
evidence_type
direction
strength
description
source
```

---

## 10. 按策略角色的校验规则

### 10.1 directional

方向型策略如果输出 `success`，必须至少包含：

```text
market_bias != not_applicable
reason_codes 非空
reason_text 非空
scenario_candidates 至少 1 条
```

每条候选场景必须包含：

```text
activation_condition
invalidation_condition
risk_boundary
observation_period_bars
```

如果策略不能提供这些字段，就不应该标记为 `directional success`，应返回 `invalid` 或 `no_signal`。

### 10.2 support_resistance

支撑压力策略如果输出 `success`，必须至少包含：

```text
key_levels 至少 1 条
reason_text 非空
```

它不强制输出方向，也不强制输出目标价。

### 10.3 risk_control

风控策略如果输出 `success`，必须至少包含：

```text
risk_level
risk_flags 至少 1 条
```

它可以输出 `risk_block` 场景，但不能直接输出“禁止交易指令”。

### 10.4 filter

过滤器策略必须输出：

```text
filter_status = pass / reject / unknown
reason_text
```

可放在 `common_payload` 中。

### 10.5 context

背景策略必须输出：

```text
reason_text
evidence_items 或 context_summary
```

不强制输出方向、关键价位或候选场景。

### 10.6 placeholder

占位策略只能输出：

```text
strategy_status = not_implemented
not_trading_advice = true
```

禁止伪造真实策略判断。

---

## 11. 数据库与落库

Codex 必须先检查当前 `strategy_signal_result` ORM 和 migration。

如果当前表已有足够 JSON 字段，可复用，但必须保证字段语义清晰。

推荐新增最小字段：

```text
contract_version
strategy_role
common_payload_json
extension_payload_json
validation_status
validation_errors_json
```

要求：

1. 不删除旧字段。
2. 不破坏旧数据。
3. `metrics_json` 不再承载新业务主协议。
4. `debug_info_json` 只用于调试，不用于第 18 主消费。
5. 超长 payload 必须 blocked 或截断前记录错误，不能静默丢弃。
6. migration 必须可回滚。
7. ORM、repository、测试同步更新。

---

## 12. 第 18 适配要求

23A 只允许对第 18 做输入适配，不允许重写第 18 聚合逻辑。

第 18 读取策略结果时：

```text
优先读取 common_payload_json
若不存在，则兼容旧字段 metrics_json / reason_codes_json / reason_text
extension_payload_json 只进入材料包的 strategy_private_payload 摘要，不参与通用聚合硬编码
```

禁止：

1. 第 18 直接理解江恩私有字段。
2. 第 18 直接理解支撑压力私有字段。
3. 第 18 重新运行策略。
4. 第 18 重新生成 snapshot。
5. 第 18 调用大模型。

---

## 13. 现有占位策略迁移

23A 需要更新现有三个策略：

```text
trend_structure_strategy.py
volatility_risk_strategy.py
gann_placeholder_strategy.py
```

目的不是提高策略质量，只是让它们符合新协议。

要求：

1. trend 仍是草稿策略，不升级成正式趋势策略。
2. volatility 仍是简单风控信号，不升级成完整风控系统。
3. gann 仍必须是 `not_implemented`，不能伪造江恩分析。
4. 三个策略都必须通过新 validator。

---

## 14. 配置文件

检查并更新：

```text
configs/strategies/strategy_registry.yaml
configs/strategies/*.yaml
```

新增字段建议：

```yaml
contract_version: strategy_result_contract_v1
strategy_role: support_resistance
enabled: true
```

规则：

1. `strategy_name` 必须唯一。
2. `strategy_version` 必须明确。
3. `strategy_role` 必须与策略输出一致。
4. 配置中不能放密钥。
5. 配置中不能写交易指令。

---

## 15. Hermes 通知边界

23A 不新增 Hermes 通知。

如果第 17 或第 18 已有配置化通知，可做兼容展示：

```text
strategy_role
market_bias
risk_level
key_levels 摘要
reason_text
```

但必须保持：

```text
本阶段仅为独立策略信号，不是最终交易建议。
```

不允许为了 23A 新增微信交互、确认、纠错、人工反馈。

---

## 16. 测试要求

必须新增或更新测试：

```text
tests/strategy/test_strategy_context_view.py
tests/strategy/test_strategy_result_contract.py
tests/strategy/test_strategy_result_validator.py
tests/strategy/test_strategy_result_adapter.py
tests/strategy/test_strategy_runner.py
tests/strategy/test_signal_service.py
tests/aggregation/test_strategy_result_contract_adapter.py
```

至少覆盖：

1. 公共字段完整输出。
2. extension_payload 存在但公共层不理解其内部结构。
3. directional 缺少失效条件时被 validator 拒绝。
4. support_resistance 无 key_levels 时被 validator 拒绝。
5. risk_control 无 risk_flags 时被 validator 拒绝。
6. placeholder 不能输出伪真实分析。
7. 策略异常仍被 runner 隔离。
8. dry-run 不写数据库。
9. confirm-write 才写数据库。
10. 第 18 能读取新公共协议字段。
11. 旧字段兼容不破坏。

---

## 17. 人工验收命令

Codex 完成后，至少给出并通过以下命令。

```bash
python -m pytest tests/strategy
```

如果修改了第 18 适配：

```bash
python -m pytest tests/aggregation
```

如果新增 migration：

```bash
python -m alembic upgrade head
python -m alembic current -v
```

检查 CLI help：

```bash
python -m scripts.run_strategy_signals --help
```

dry-run 验证：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

正式写入验证只能在用户确认后执行：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

---

## 18. implementation 文档

完成后必须新增：

```text
docs/implementation/23a_strategy_common_contract.md
```

必须写清：

1. 入口文件。
2. 调用链。
3. `StrategyContextView` 如何构造。
4. `StrategyResult` 如何校验。
5. 新字段如何落库。
6. 第 18 如何适配。
7. dry-run 是否写库。
8. 是否发送 Hermes。
9. 哪些事情明确不做。
10. 测试命令。
11. 人工验收命令。

---

## 19. Codex 执行前强制阅读

Codex 修改前必须阅读：

```text
AGENTS.md
docs/rules/project_invariants.md
docs/architecture/module_boundaries.md

docs/plans/15_market_context_snapshot.md
docs/plans/16_strategy_signal_framework.md
docs/plans/17_strategy_signal_scheduler_plan.md
docs/plans/18_strategy_aggregation_material_pack.md

app/strategy/types.py
app/strategy/base.py
app/strategy/runner.py
app/strategy/input_builder.py
app/strategy/snapshot_resolver.py
app/strategy/signal_service.py
app/strategy/result_repository.py
app/strategy/strategies/

第 18 当前实际代码文件
当前 strategy_signal_run / strategy_signal_result ORM
当前 Alembic migration 链
```

如果实际文件名与本文不同，以仓库实际文件为准，但必须在总结中说明。

---

## 20. 验收标准

23A 完成后，应该达到：

1. 真实策略可以独立新增文件。
2. 真实策略只需要关心自己的算法和私有 payload。
3. 公共层能统一校验策略输出。
4. 第 18 能消费统一公共字段。
5. 江恩、支撑压力、趋势、风控可以共享同一套结果协议。
6. 策略私有字段不会污染公共代码。
7. 第 15 懒生成机制不被破坏。
8. 第 17 调度机制不被破坏。
9. 不产生任何自动交易能力。
10. 不新增交互型外围功能。

---

## 21. 完成后的下一步

23A 完成后，不继续扩展交互。

下一步应单独开真实策略计划，优先建议：

```text
STR-SR-01 support_resistance_strategy
```

即：支撑压力策略独立 plan。

不要命名为 23B / 23C。
