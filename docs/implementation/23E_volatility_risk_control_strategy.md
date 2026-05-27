# 23E 波动率与风控闸门策略实现说明

## 1. 功能：VolatilityRiskControlStrategy

### 1.1 发起入口

本阶段不新增 CLI，不新增 scheduler，沿用第 16 阶段策略信号入口：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/market_direction_regime_strategy.py::MarketDirectionRegimeStrategy.evaluate
    -> app/strategy/evidence_context.py::EvidenceContext.with_signal
    -> app/strategy/strategies/support_resistance_strategy.py::SupportResistanceStrategy.evaluate
    -> app/strategy/evidence_context.py::EvidenceContext.with_signal
    -> app/strategy/strategies/breakout_pullback_trigger_strategy.py::BreakoutPullbackTriggerStrategy.evaluate_with_evidence
    -> app/strategy/evidence_context.py::EvidenceContext.with_signal
    -> app/strategy/strategies/volatility_risk_control_strategy.py::VolatilityRiskControlStrategy.evaluate_with_evidence
```

`VolatilityRiskControlStrategy` 只输出风控证据，不生成最终 advice，不生成 trade_setup，不输出正式 entry / stop_loss / take_profit，不涉及仓位、杠杆或保证金建议。

### 1.2 核心职责

本策略负责在 23B / 23C / 23D 的公开证据之后，判断当前候选是否值得继续推进：

1. 计算 ATR、近期波动扩张、最新 K 线振幅和影线风险。
2. 根据 23B 公开市场状态动态选择 `risk_policy_profile`。
3. 根据 23C 公开 `key_levels` 计算多空方向空间可行性。
4. 根据 23D 公开触发状态判断追单、假突破、缩量确认等候选风险。
5. 输出 `risk_gate_decision`、`risk_scope`、`global_market_risk`、`candidate_risk`、`long_feasibility`、`short_feasibility` 等公开风控摘要。

## 2. 输入与依赖

### 2.1 统一策略输入

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

快照最新性、合格性和懒生成仍由 15 / 16 主框架负责。本策略不查询数据库，不自行查询 MarketContextSnapshot。

### 2.2 同轮公开 EvidenceContext

23E 复用 23D 引入的 `EvidenceContext`，只读取前序策略的公开 `common_payload_json`：

```text
23B context role common_payload_json
    -> primary_regime / regime_phase / trend_strength / decision_implication / market_environment_context 公开字段；旧结果缺少结构化字段时才从公开 reason_codes 兼容解析

23C support_resistance role common_payload_json
    -> key_levels

23D filter role common_payload_json
    -> trigger_state / filter_decision / tested_level_summary / volume_state / volume_confirmation
```

`EvidenceContext` 不保存、不传递、不解析：

```text
前序 strategy_payload_json
前序 strategy_model_material_json
23B / 23C / 23D 内部函数
23B / 23C / 23D 私有算法
```

任一关键上下文缺失时，23E 返回 `insufficient_context` / `wait` / `unknown` 类保守结果，不默认放行。

## 3. 配置

新增配置文件：

```text
configs/strategies/volatility_risk_control_strategy.yaml
```

核心配置：

```text
strategy_name = volatility_risk_control_strategy
strategy_version = 23E-1
strategy_role = risk_control
provides = [
  volatility_risk,
  trade_permission_filter,
  risk_gate_decision,
  reward_risk_feasibility,
  chase_risk,
  stop_distance_reference,
  market_state_aware_risk_policy,
]
requires = [
  role=context, provides=primary_regime,
  role=support_resistance, provides=key_levels,
  role=filter, provides=trigger_state,
]
consumes = [
  common_result.primary_regime,
  common_result.regime_phase,
  common_result.market_environment_context,
  common_result.key_levels,
  common_result.trigger_state,
  common_result.filter_decision,
  common_result.tested_level_summary,
  common_result.volume_state,
  common_result.volume_confirmation,
]
```

`risk_policy_mapping` 和 `risk_policy_profiles` 由配置驱动。未知或缺失市场状态会进入 `default_conservative`，不会默认 `allow`。若当前候选方向与 23B 公开市场背景相反，且当前 profile 配置了 `countertrend_action`，23E 会执行该动作；未配置时至少降级为 `wait`。

## 4. StrategyResult 三段边界

### 4.1 common_result

`common_result` 只保存公开风控摘要：

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
risk_level
risk_flags
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
not_trading_advice = true
```

### 4.2 strategy_payload_json

私有计算细节只写入 `strategy_payload_json`：

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

这些字段不进入 `common_result`。

### 4.3 strategy_model_material_json

`strategy_model_material_json` 只保存后续模型层可读摘要，包括风控闸门结论、波动率风险、追单风险、空间可行性、反方证据和不确定性。本阶段不调用大模型。

## 5. 数据流与入库

23E 策略自身不读数据库、不写数据库。非 dry-run 且 `confirm_write=True` 时，仍由第 16 阶段统一 repository 写入：

```text
app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/common/result_adapter.py::adapt_strategy_result_to_signal
    -> app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_run_with_results
```

写入表：

```text
strategy_signal_run
strategy_signal_result
```

23E 写入 `strategy_signal_result` 的相关字段：

```text
strategy_name = volatility_risk_control_strategy
strategy_role = risk_control
common_payload_json = 公开风控摘要
strategy_model_material_json = 模型材料摘要
strategy_payload_json = 私有计算细节
validation_status
validation_errors_json
```

本阶段不新增数据库表，不新增 migration，不修改 K 线表。

## 6. 异常处理

1. K 线数量不足：`VolatilityRiskControlStrategy._insufficient_data_result()` 返回 contract-valid invalid 结果，不抛异常。
2. 23B / 23C / 23D 公开上下文缺失：`VolatilityRiskControlStrategy.evaluate_with_evidence()` 返回 `risk_gate_decision=insufficient_context`，不默认放行。
3. 单个策略异常：沿用 `app/strategy/runner.py::StrategyRunner._evaluate_strategy()` 隔离为 failed signal，其他策略继续运行。
4. 非 dry-run 入库异常：沿用第 16 阶段 `StrategySignalService.run_strategy_signals()` 与 repository 异常处理，失败时 rollback。
5. 本阶段不允许 partial write 到 K 线表，不允许自动修复行情数据。

## 7. 外部服务与边界

本功能不请求外部接口。  
本功能不请求 Binance REST。  
本功能不请求 WebSocket。  
本功能不读取账户。  
本功能不读取持仓。  
本功能不读取 Redis。  
本功能不写入 Redis。  
本功能不发送 Hermes。  
本功能不调用 DeepSeek 或其他大模型。  
本功能不新增 scheduler。  
本功能不新增 scripts。  
本功能不修改正式 K 线表。  
本功能不自动交易。  
本功能不生成人工执行反馈。

## 8. 测试

对应测试文件：

```text
tests/strategy/test_23e_volatility_risk_control_strategy.py
```

覆盖内容：

1. 23E 输出 `risk_control` 角色 StrategyResult。
2. 配置声明 `strategy_role` / `provides` / `requires` / `consumes`。
3. runner 在公开依赖满足后运行 23E。
4. 23E 只读取前序 `common_result`，不读取前序 `strategy_payload_json`。
5. 23B / 23C / 23D 上下文缺失时保守输出。
6. 极端波动、追单风险、假突破、空间不足、手续费和滑点缓冲。
7. `global_market_risk` 与 `candidate_risk` 分开输出。
8. `long_feasibility` 与 `short_feasibility` 分开计算。
9. 私有计算细节不进入 `common_result`。
10. 数据不足、策略关闭、单策略失败、16 落库适配、18 链路读取兼容。

已运行：

```text
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```
