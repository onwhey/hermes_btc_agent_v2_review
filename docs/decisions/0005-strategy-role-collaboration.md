# 0005 Strategy Role Collaboration

## 状态

Accepted

## 1. 背景

早期多策略设计容易被理解为“每个策略都输出完整交易结论”，例如每个策略都给出方向、入场、止损、目标和建议动作。这会导致策略职责混乱：专业策略被迫伪造自己并不负责的字段，聚合层也可能误把缺失的私有字段当成主链路失败。

Hermes BTC 在 23A 已经完成 `StrategyResult` 三段结构，并支持 `strategy_role`。因此策略架构语义正式从“多策略横向完整结论对比”调整为“策略角色协作 / 策略接力”。

## 2. 决策

系统正式采用“策略角色协作 / 策略接力”：

1. `directional` 提供方向候选，例如 `bullish_bias`、`bearish_bias`、`neutral`、`mixed`、`wait`、`scenario_candidates`、`activation_condition`、`invalidation_condition`、`risk_boundary`、`observation_period_bars`。
2. `support_resistance` 提供关键价格位置，例如 `support`、`resistance`、`trigger`、`invalidation`、`target_observation`、`reference`。
3. `risk_control` 提供风险等级、风险标记和否决原因，例如 `risk_level`、`risk_flags`、`risk_type`、`triggered`、`reason`。
4. `filter` 提供过滤结果，例如 `pass`、`reject`、`unknown`。
5. `context` 提供背景证据，例如宏观背景、资金费率背景、OI 背景、市场环境说明、`evidence_items`、`context_summary`。
6. `placeholder` 只表示未实现，不得伪造分析内容。

每个策略是一个专业分析模块，不再被要求变成完整交易员。

## 3. 边界

1. 策略层只输出角色化证据，不生成最终交易建议。
2. 聚合层按 `strategy_role` 收集证据，识别一致性、冲突、缺失和降级条件。
3. 聚合层默认只消费 `common_result` 中的角色化公共字段，不直接依赖具体策略私有字段。
4. 建议层负责把方向候选、关键价位、风控否决和背景证据组合成最终 `advice`、`trade_setup`、`wait` 或 `stop_trading`。
5. 横向对比仍可保留，但主要用于后续复盘评估、抽检和质量对照，不作为实时主决策逻辑。

## 4. 字段原则

1. `common_result` 只保存跨策略、跨角色可复用的公共字段。
2. `strategy_payload_json` 只保存具体策略私有扩展字段。
3. `strategy_model_material_json` 只保存后续模型层材料，不作为公共聚合字段。
4. 公共字段只能按 `strategy_role` 定义，不得按 `strategy_name` 定义。
5. 新增策略不应要求修改公共 schema。
6. `common_result` 不得包含 `gann_angle`、`gann_time_window`、`fibonacci_618`、`support_detection_method`、`liquidity_sweep_detail` 等具体策略私有字段。
7. 未来确需读取某个策略的 `strategy_payload_json` 时，必须通过独立 adapter 做可选解析。

## 5. 缺失策略处理

1. 某个具体策略关闭时，不应导致主链路失败。
2. 聚合层应记录 `evidence_missing`，而不是猜测缺失证据。
3. adapter 缺失、解析失败或策略关闭时，只能降级为 `evidence_missing` / `wait`，不得导致主链路失败。
4. 若关键证据不足，最终建议应降级为 `wait` / `no_valid_setup`，而不是报错。

## 6. 影响范围

1. 本决策不推翻 23A。
2. 本决策不修改 23A 数据库迁移。
3. 本决策不开发真实策略。
4. 本决策不修改 scheduler。
5. 本决策不新增 Hermes。
6. 本决策不调用大模型。
7. 本决策不涉及自动交易。
