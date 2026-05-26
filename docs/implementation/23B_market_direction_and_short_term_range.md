# 23B 市场大方向与短期行情区间实现说明

## 1. 功能：市场大方向识别

### 1.1 发起入口

本功能不新增 CLI，不新增 scheduler。沿用第 16 阶段入口：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/market_direction_regime_strategy.py::MarketDirectionRegimeStrategy.evaluate
```

### 1.2 核心职责

`MarketDirectionRegimeStrategy` 只负责根据 `StrategyEvaluationInput` 中已经恢复的 4h / 1d K线窗口，识别：

1. `primary_regime`
2. `regime_phase`
3. 市场环境背景摘要

它不生成最终 advice，不生成 trade_setup，不输出入场、止损、止盈、仓位或杠杆字段。

### 1.3 配置

读取：

```text
configs/strategies/market_direction_regime_strategy.yaml
```

关键配置：

```text
strategy_role = context
provides = [primary_regime, regime_phase, market_environment_context]
lookback_bars
minimum_required_bars
thresholds
```

### 1.4 数据流

```text
MarketContextSnapshot restore rows
    -> StrategyEvaluationInput
    -> MarketDirectionRegimeStrategy.evaluate
    -> StrategyResult
    -> app/strategy/common/result_adapter.py::adapt_strategy_result_to_signal
    -> strategy_signal_result
```

`common_result` 只写入角色化公共证据：`risk_level`、`signal_strength`、`confidence_score`、`reason_codes`、`reason_text`、`evidence_items`、`context_summary`、`not_trading_advice`。

`strategy_payload_json` 写入私有字段：`primary_regime`、`regime_phase`、`trend_strength`、`regime_confidence`、`phase_confidence`、`decision_implication`。

### 1.5 异常与不足数据处理

K线数量不足时返回 `strategy_status=invalid`，并在 `strategy_payload_json.primary_regime` 写入 `insufficient_data`。该路径不抛出业务异常，不中断其他策略。

本功能不请求外部接口，不读取数据库，不写入数据库。写库只发生在第 16 阶段 repository：

```text
app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_results
```

本功能不读取 Redis，不写入 Redis，不发送 Hermes，不调用 DeepSeek 或其他大模型，不涉及自动交易。

## 2. 功能：短期行情区间识别

### 2.1 发起入口

沿用第 16 阶段入口：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/short_term_range_strategy.py::ShortTermRangeStrategy.evaluate
```

### 2.2 核心职责

`ShortTermRangeStrategy` 只负责识别近期基础周期运行区间：

1. `recent_range_high`
2. `recent_range_low`
3. `range_mid`
4. `range_position`
5. `range_quality`

它不做正式支撑压力策略，不输出正式触发位、失效位、目标位或最终交易结构。

### 2.3 配置

读取：

```text
configs/strategies/short_term_range_strategy.yaml
```

关键配置：

```text
strategy_role = context
provides = [short_term_range, range_position, range_quality]
lookback_bars
minimum_required_bars
thresholds
```

### 2.4 数据流

```text
MarketContextSnapshot restore rows
    -> StrategyEvaluationInput
    -> ShortTermRangeStrategy.evaluate
    -> StrategyResult
    -> app/strategy/common/result_adapter.py::adapt_strategy_result_to_signal
    -> strategy_signal_result
```

`common_result` 只写入角色化公共证据。

`strategy_payload_json` 写入私有字段：`recent_range_high`、`recent_range_low`、`range_mid`、`range_width_pct`、`range_position`、`range_quality`、`range_basis`。

### 2.5 异常与不足数据处理

K线数量不足时返回 `strategy_status=invalid`，并在 `strategy_payload_json.range_quality` 写入 `insufficient_data`。该路径不抛出业务异常，不影响其他策略运行。

本功能不请求外部接口，不读取数据库，不写入数据库，不读取 Redis，不写入 Redis，不发送 Hermes，不调用 DeepSeek 或其他大模型，不涉及自动交易。

## 3. 配置与 registry

修改：

```text
app/strategy/registry.py
configs/strategies/strategy_registry.yaml
```

registry 新增两个策略类映射，并让简单 YAML 读取器支持一层 nested mapping，用于读取：

```text
lookback_bars
minimum_required_bars
thresholds
```

同一个 `strategy_role=context` 下允许 `market_direction_regime` 和 `short_term_range` 并存。`provides` 只表示具体能力，不改变公共 schema。

## 4. 第 18 阶段兼容

修改：

```text
app/strategy/aggregation/rules.py
```

最小兼容规则：

1. 18 阶段仍优先读取 `common_payload_json`。
2. 只有 `strategy_role=directional` 的 `market_bias` 才进入方向分类。
3. `context` 策略不会因为带有 `market_bias` 而被当作方向票。
4. `strategy_payload_json` 仍只做私有 payload 摘要，不参与公共聚合。

本阶段不做 18 聚合层大规模重构。

## 5. 数据库与迁移

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

本阶段不修改 K线表，不写入业务初始化数据。

## 6. 测试

新增：

```text
tests/strategy/test_23b_market_direction_and_range.py
```

覆盖：

1. `MarketDirectionRegimeStrategy` 输出 `context` 角色 `StrategyResult`。
2. `ShortTermRangeStrategy` 输出 `context` 角色 `StrategyResult`。
3. 两个策略配置声明 `provides`。
4. 同一个 `strategy_role` 下多个策略可并存。
5. 私有字段不进入 `common_result`。
6. 数据不足时输出 `insufficient_data` 且不抛异常。
7. 第 16 阶段 service 可落库 23B 字段。
8. 第 18 阶段读取 23B context 结果不崩溃。

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

人工检查命令：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m scripts.run_strategy_signals --symbol BTCUSDT --base-interval 4h --higher-interval 1d --ensure-latest-snapshot --trigger-source cli --dry-run
```

## 7. 本阶段明确不负责

1. 不做完整支撑压力策略。
2. 不做突破 / 回踩触发策略。
3. 不做江恩策略。
4. 不做斐波那契策略。
5. 不做流动性清理策略。
6. 不做资金费率 / OI 策略。
7. 不生成最终 advice。
8. 不生成 trade_setup。
9. 不发送 Hermes。
10. 不调用 DeepSeek、OpenAI、Claude 或其他大模型。
11. 不读取账户。
12. 不读取持仓。
13. 不请求 Binance。
14. 不自动交易。
15. 不做人为执行反馈。
16. 不做完整复盘系统。
