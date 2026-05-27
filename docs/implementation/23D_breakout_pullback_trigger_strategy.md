# 23D 突破回踩触发确认策略实现说明

## 1. 功能：BreakoutPullbackTriggerStrategy

### 1.1 发起入口

本阶段不新增 CLI，不新增 scheduler，沿用第 16 阶段策略信号入口：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/support_resistance_strategy.py::SupportResistanceStrategy.evaluate
    -> app/strategy/evidence_context.py::EvidenceContext.with_signal
    -> app/strategy/strategies/breakout_pullback_trigger_strategy.py::BreakoutPullbackTriggerStrategy.evaluate_with_evidence
```

### 1.2 核心职责

`BreakoutPullbackTriggerStrategy` 只负责识别关键位附近的公开触发过滤证据：

1. 突破尝试与突破确认。
2. 跌破尝试与跌破确认。
3. 突破或跌破后的回踩测试、回踩确认、回踩失败。
4. 假突破与假跌破。
5. 成交量确认、降权或拒绝信号。

本策略不生成最终 advice，不生成 trade_setup，不输出入场价、止损价、止盈价、仓位、杠杆或盈亏比。

## 2. 输入与依赖

### 2.1 统一输入

策略只读取第 16 阶段传入的：

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

快照最新性、合格性和懒生成仍由 15 / 16 主框架负责。本策略不查询数据库，不自行查询 MarketContextSnapshot。

### 2.2 同轮公开证据

23D 采用方案 B，由 runner 在同一轮中传递公开 EvidenceContext：

```text
SupportResistanceStrategy StrategyResult
    -> adapt_strategy_result_to_signal
    -> StrategySignal.common_payload_json["key_levels"]
    -> EvidenceContext.public_role_outputs["support_resistance"]
    -> BreakoutPullbackTriggerStrategy.evaluate_with_evidence
```

`EvidenceContext` 只保存 `common_payload_json`。它不保存、不传递、不解析：

```text
strategy_payload_json
strategy_model_material_json
23C raw_swing_points
23C merged_level_clusters
23C cluster_scoring_details
23C 内部函数
```

如果 23C 关闭、失败、未输出 `key_levels` 或 `key_levels` 为空，23D 输出：

```text
trigger_state = insufficient_key_levels
filter_decision = not_applicable
reason_code = missing_support_resistance_key_levels
```

并且不抛异常。

## 3. 配置

新增配置文件：

```text
configs/strategies/breakout_pullback_trigger_strategy.yaml
```

核心配置：

```text
strategy_name = breakout_pullback_trigger_strategy
strategy_role = filter
provides = [
  breakout_confirmation,
  breakdown_confirmation,
  pullback_confirmation,
  false_breakout_filter,
  trigger_state,
  filter_decision,
  tested_level_summary,
  volume_confirmation,
]
requires = [{ role = support_resistance, provides = key_levels }]
consumes = [common_result.key_levels]
lookback_bars
minimum_required_bars
thresholds
volume
output_limits
```

配置由以下方法读取：

```text
app/strategy/registry.py::StrategyRegistry.load_enabled_strategies
```

`configs/strategies/strategy_registry.yaml` 已注册本策略，runner 会根据 `requires` 和前序策略 `provides` 做最小依赖排序。

## 4. Runner / EvidenceContext 最小改造

### 4.1 Runner

修改文件：

```text
app/strategy/runner.py
```

新增行为：

1. `StrategyRunner.run_strategies()` 先读取 enabled strategies。
2. `_order_strategies_for_public_dependencies()` 根据 `requires` / `provides` 做本轮最小排序。
3. 每个策略运行完成后，将成功或 no_signal 的公开 `common_payload_json` 写入 `EvidenceContext`。
4. 如果策略实现 `evaluate_with_evidence(input_data, evidence_context)`，runner 调用该方法；否则继续调用原有 `evaluate(input_data)`。

这不改变第 16 阶段主链路：

```text
signal_service -> input_builder -> runner -> result_repository
```

### 4.2 EvidenceContext

新增文件：

```text
app/strategy/evidence_context.py
```

职责：

1. 保存同轮前序策略的公开 `common_payload_json`。
2. 按 role 返回公开 `key_levels`。
3. 不保存私有 payload。
4. 不做 23F 深度聚合，不做 role_coverage_matrix，不做 evidence_missing 总表，不做 key_level_conflict 融合。

## 5. StrategyResult 三段边界

### 5.1 common_result

23D 的 `common_result` 只保存公开过滤摘要：

```text
trigger_state
filter_decision
tested_level_summary
volume_state
volume_confirmation
filter_status
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
not_trading_advice = true
```

### 5.2 strategy_payload_json

私有计算细节只写入：

```text
strategy_payload_json
```

包括：

```text
breakout_distance_pct
breakdown_distance_pct
close_relation_to_zone
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

这些字段不进入 `common_result`。

### 5.3 strategy_model_material_json

只写入后续模型层可读摘要，例如触发状态、过滤结果、测试关键位、成交量状态、主要证据和不确定性。本阶段不调用大模型。

## 6. 数据流与入库

23D 策略自身不读数据库、不写数据库。

当用户通过第 16 阶段非 dry-run 且 `confirm_write=True` 运行策略信号时，仍由既有 repository 写入：

```text
strategy_signal_run
strategy_signal_result
```

写入链路：

```text
app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/common/result_adapter.py::adapt_strategy_result_to_signal
    -> app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_run_with_results
```

23D 写入 `strategy_signal_result` 的相关字段：

```text
strategy_name = breakout_pullback_trigger_strategy
strategy_role = filter
common_payload_json = 公开触发过滤摘要
strategy_model_material_json = 模型材料摘要
strategy_payload_json = 私有计算细节
validation_status
validation_errors_json
```

不新增数据库表，不新增 migration，不修改 K 线表。

## 7. 异常处理

1. 23C key_levels 缺失：`BreakoutPullbackTriggerStrategy.evaluate_with_evidence()` 返回 `no_signal`，不抛异常。
2. K 线数量不足：返回 contract-valid `invalid` 结果，`trigger_state=insufficient_data`。
3. 单个策略异常：由 `app/strategy/runner.py::StrategyRunner._evaluate_strategy()` 隔离为 failed signal，其他策略继续运行。
4. 非 dry-run 入库异常：沿用第 16 阶段 `StrategySignalService.run_strategy_signals()` 和 repository 异常处理，失败时 rollback。
5. 本阶段不允许 partial write 到 K 线表，不允许自动修复行情数据。

## 8. 外部服务与边界

本功能不请求外部接口。
本功能不请求 Binance REST。
本功能不请求 WebSocket。
本功能不读取账户。
本功能不读取持仓。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不新增 scripts。
本功能不修改正式 K 线表。
本功能不自动交易。

## 9. 测试

对应测试文件：

```text
tests/strategy/test_23d_breakout_pullback_trigger_strategy.py
```

覆盖内容：

1. 23D 输出 `filter` 角色 StrategyResult。
2. 配置声明 `provides` / `requires` / `consumes`。
3. runner 将 support_resistance 公开 key_levels 排在 23D 前传递。
4. 23D 只读取公开 `common_result.key_levels`，不读取私有 payload。
5. 缺少 key_levels 时输出 `insufficient_key_levels / not_applicable`。
6. 突破确认、突破尝试、假突破、跌破确认、假跌破、回踩测试、回踩确认。
7. 成交量只作为确认或降权因子。
8. 私有计算细节不进入 `common_result`。
9. 数据不足时返回 invalid，不抛异常。
10. 策略关闭后 registry 不报错。
11. 单策略失败不影响 23D 运行。
12. 第 16 阶段落库适配。
13. 第 18 阶段读取 23D 结果不崩溃。

已运行：

```text
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```
