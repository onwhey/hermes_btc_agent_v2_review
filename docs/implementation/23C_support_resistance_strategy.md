# 23C 支撑压力与关键价格区域实现说明

## 1. 功能：SupportResistanceStrategy

### 1.1 发起入口

本阶段不新增 CLI，不新增 scheduler，沿用第 16 阶段策略信号入口：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/support_resistance_strategy.py::SupportResistanceStrategy.evaluate
```

### 1.2 核心职责

`SupportResistanceStrategy` 只负责输出价格地图：

1. 支撑区域。
2. 压力区域。
3. 区间边界。
4. 历史参考位。
5. 潜在支撑压力转换区域。

它不生成最终 advice，不生成 trade_setup，不输出入场价、最终失效价、最终目标价、仓位、杠杆或盈亏比。

### 1.3 输入来源

本策略只读取第 16 阶段传入的：

```text
app/strategy/types.py::StrategyEvaluationInput
```

使用字段：

```text
base_klines
higher_klines
snapshot_id
symbol
base_interval_value
higher_interval_value
trace_id
```

快照最新性、合格性和懒生成仍由第 15 / 16 主框架负责。

本策略不请求外部接口，不查询数据库，不读取 Redis，不发送 Hermes，不调用 DeepSeek 或其他大模型。

## 2. 配置

新增配置：

```text
configs/strategies/support_resistance_strategy.yaml
```

关键配置：

```text
strategy_name = support_resistance_strategy
strategy_role = support_resistance
provides = [
  key_levels,
  support_zones,
  resistance_zones,
  range_boundaries,
  invalidation_reference_zones,
  target_observation_zones,
  role_flip_candidates,
]
lookback_bars
minimum_required_bars
thresholds
output_limits
```

配置由：

```text
app/strategy/registry.py::StrategyRegistry.load_enabled_strategies
```

读取。registry 支持 `support_resistance_strategy.yaml` 精确文件名，并兼容既有 `<strategy_name>_strategy.yaml` 命名。

## 3. 核心算法

### 3.1 候选点识别

策略从 4h / 1d K线窗口中提取：

```text
swing_high
swing_low
recent_high
recent_low
previous_high
previous_low
range_high
range_low
```

Swing 点使用左右窗口比较，并通过 `min_swing_move_pct` 做最小波动过滤。

### 3.2 聚类与评分

相近价格点按 `cluster_width_pct` 聚类为 zone。每个 zone 计算：

```text
strength_score
confidence_score
current_relevance_score
touch_count
reaction_strength
timeframe_weight
recency_score
cluster_density
distance_from_current_price_pct
zone_width_pct
zone_quality
```

孤立点会被标记为 `outlier` 并降低强度。过宽 zone 会标记为 `wide` 并降权。距离当前价格较远或时间较旧的区域可以保留为 `historical_reference`，但 `current_relevance_score` 会降低。

### 3.3 分层输出

输出层级包括：

```text
nearest_support
nearest_resistance
major_support
major_resistance
range_upper_boundary
range_lower_boundary
historical_reference
role_flip_candidate
```

潜在支撑压力转换只标记为候选，不确认突破、跌破或回踩。

## 4. StrategyResult 三段边界

### 4.1 common_result

`common_result` 只保存公共可复用摘要：

```text
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
key_levels
not_trading_advice = true
```

`key_levels` 单项包含：

```text
level_id
level_type
level_group
zone_low
zone_high
zone_mid
timeframe
strength_score
confidence_score
current_relevance_score
touch_count
distance_from_current_price_pct
role_flip_status
zone_quality
reason
```

示例：

```json
{
  "level_id": "SR-001",
  "level_type": "invalidation_reference",
  "level_group": "nearest_support",
  "zone_low": "59880.00",
  "zone_high": "60200.00",
  "zone_mid": "60040.00",
  "timeframe": "4h",
  "strength_score": "0.7200",
  "confidence_score": "0.6800",
  "current_relevance_score": "0.8100",
  "touch_count": 4,
  "distance_from_current_price_pct": "1.2000",
  "role_flip_status": "none",
  "zone_quality": "clear",
  "reason": "当前价格下方最近的支撑参考区域；该区域不是最终失效价。"
}
```

### 4.2 strategy_payload_json

私有计算细节只写入：

```text
strategy_payload_json
```

包括：

```text
raw_swing_points
merged_level_clusters
cluster_scoring_details
reaction_strength_details
recency_score_details
role_flip_detection_details
zone_width_config
calculation_params
excluded_outliers
selected_level_groups
```

这些字段不会进入 `common_result`。

### 4.3 strategy_model_material_json

写入后续模型层可读摘要，例如最近支撑、最近压力、主要证据、不确定性和后续审查问题。

本阶段不调用大模型。

## 5. 第 16 / 18 链路

第 16 阶段可独立运行并落库：

```text
strategy_signal_result.strategy_role = support_resistance
strategy_signal_result.common_payload_json = key_levels 摘要
strategy_signal_result.strategy_payload_json = 私有计算细节
```

第 18 阶段不做深度聚合增强。本阶段只确保：

```text
app/strategy/aggregation/rules.py::classify_strategy_results
```

读取 23C 结果不崩溃，且不会把 `support_resistance` 结果当作方向票。

## 6. 数据库与迁移

未新增 migration。

未新增数据库表。

未修改 23A migration。

复用已有字段：

```text
strategy_role
common_payload_json
strategy_model_material_json
strategy_payload_json
validation_status
validation_errors_json
```

本阶段不修改 K线表，不插入业务数据。

## 7. 测试

新增：

```text
tests/strategy/test_23c_support_resistance_strategy.py
```

覆盖：

1. `SupportResistanceStrategy` 输出 `support_resistance` 角色 `StrategyResult`。
2. 配置声明 `strategy_role` 和 `provides`。
3. `common_result.key_levels` 存在支撑压力摘要。
4. 私有 swing / cluster / scoring 明细只进入 `strategy_payload_json`。
5. 能识别 nearest / major / range boundary / historical / role flip 分组。
6. 过宽 zone 降低质量。
7. 孤立点不会成为高强度关键位。
8. 历史远点保留为 historical reference 且当前相关性降低。
9. 数据不足返回 invalid，不抛异常。
10. 策略关闭后 registry 不报错。
11. 单策略失败不影响其他策略。
12. 第 16 阶段能落库 23C 结果。
13. 第 18 阶段读取 23C 结果不崩溃。

人工检查命令：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m scripts.run_strategy_signals --symbol BTCUSDT --base-interval 4h --higher-interval 1d --ensure-latest-snapshot --trigger-source cli --dry-run
```

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

## 8. 本阶段明确不负责

1. 不做最终 advice。
2. 不做 trade_setup。
3. 不输出入场价、最终失效价、最终目标价。
4. 不做盈亏比。
5. 不做突破确认。
6. 不做跌破确认。
7. 不做回踩确认。
8. 不做风控否决。
9. 不做江恩。
10. 不做斐波那契。
11. 不做流动性清理。
12. 不做订单流。
13. 不调用大模型。
14. 不发送 Hermes。
15. 不请求 Binance。
16. 不读取账户。
17. 不读取持仓。
18. 不自动交易。
19. 不做人工执行反馈。
20. 不做完整复盘系统。
21. 不做第 18 阶段深度聚合重构。
