# 27C 18 材料包接入弱模型摘要实现说明

## 1. 功能：18 material pack 接入 weak_model_summary

### 1.1 发起方式

沿用阶段 18 入口：

```bash
python -m scripts.run_strategy_aggregation --strategy-signal-run-id SSR-xxx --confirm-write
```

本阶段没有新增 CLI，没有新增 scheduler 任务。

### 1.2 入口文件

`scripts/run_strategy_aggregation.py`

入口方法：

`main()`

### 1.3 核心 service

`app/strategy/aggregation/service.py`

核心方法：

`StrategyAggregationService.run_strategy_aggregation()`

### 1.4 调用链路

```text
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::get_strategy_signal_run
    -> app/strategy/aggregation/repository.py::list_strategy_signal_results
    -> app/strategy/aggregation/repository.py::restore_snapshot_kline_windows
    -> app/strategy/aggregation/repository.py::get_latest_strategy_evidence_aggregation
    -> app/strategy/aggregation/repository.py::get_latest_weak_model_material
    -> app/strategy/aggregation/weak_model_material.py::build_weak_model_summary
    -> app/strategy/aggregation/material_builder.py::build_material_pack
    -> app/strategy/aggregation/repository.py::create_aggregation_run
    -> app/strategy/aggregation/repository.py::create_material_pack
```

## 2. 读取与写入

读取数据库表：

1. `strategy_signal_run`
2. `strategy_signal_result`
3. `market_context_snapshot` 及其 snapshot restore 契约读取的 `market_kline_4h` / `market_kline_1d`
4. `strategy_evidence_aggregation_result`
5. `weak_model_run`
6. `weak_model_aggregation`
7. `weak_model_quality_check`
8. `weak_model_result` 的 `model_key/config_hash` 摘要字段

写入数据库表：

1. `strategy_aggregation_run`
2. `analysis_material_pack`

本阶段不新增数据库表，不新增 migration。

不读取 Redis，不写入 Redis。
不请求外部接口。
不请求 Binance REST。
不发送 Hermes 新类型告警；阶段 18 原有 Hermes 行为不变。
不调用 DeepSeek/GPT/Claude 或其他大模型。
不读取账户、仓位或交易私有状态。
不生成订单，不自动交易。

## 3. WMA / WMQC 选择规则

选择逻辑落在：

`app/strategy/aggregation/repository.py::get_latest_weak_model_material`

规则：

1. 只选择 `weak_model_run.run_status=success`。
2. 必须匹配当前 `strategy_signal_run_id`。
3. 必须匹配当前 SSR 绑定的 `snapshot_id`。
4. 必须匹配 `symbol/base_interval/higher_interval/kline_slot_utc`。
5. 同一 SSR 多条 WMR 时按 `weak_model_run.created_at_utc desc, id desc` 选择最新一条。
6. 读取同一 `weak_model_run_id` 最新 `weak_model_quality_check`。
7. 只读取 `weak_model_result` 的配置 hash 摘要，不读取 `raw_output_json`。

如果没有符合条件的 WMR/WMA，18 不失败，`weak_model_summary.status=missing`。

## 4. weak_model_summary schema

核心字段：

```text
status
weak_model_run_id
weak_model_aggregation_id
quality_check_id
quality_status
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
kline_slot_utc
directional_bias
directional_score
directional_confidence
risk_level
trade_permission
veto_triggered
supporting_factors
opposing_factors
conflict_factors
low_confidence_factors
veto_factors
context_summary
quality_issues
source_config_hashes
summary_text
excluded_values
not_trading_advice
```

状态映射：

1. `quality_check.status=passed` -> `weak_model_summary.status=available`
2. `quality_check.status=warning` -> `weak_model_summary.status=warning`
3. `quality_check.status=critical` -> `weak_model_summary.status=excluded_by_quality_check`
4. 无 `quality_check` -> `weak_model_summary.status=unchecked`
5. 无 WMR/WMA -> `weak_model_summary.status=missing`

`critical` 时，`directional_score/directional_confidence` 写为 `null`，`trade_permission/risk_level` 写为 `excluded_by_quality_check`，原始摘要值只保存在 `excluded_values` 中，避免下游把它当正常弱模型结论。

## 5. legacy_math_context 处理

实现位置：

`app/strategy/aggregation/weak_model_material.py::build_legacy_math_context_summary`

阶段 18 保留原有 `swing/volatility/support_resistance` 字段以兼容旧测试和下游读取，但新增：

```text
legacy_math_context.source=legacy_math_context
legacy_math_context.status=deprecated_math_material
legacy_math_context.independent_evidence_weight=background_only
```

材料包同时写入提示：

```text
legacy_math_context 与 weak_model_summary 可能同源，模型审查不得把它们当作两组独立证据重复计票。
```

## 6. material hash 与 20 复用判断

阶段 18 material schema 从 `material_schema_v2` 升级为 `material_schema_v3`。

20 复用判断修改位置：

`app/model_review_aggregation/fingerprint.py::build_material_fingerprint`

新增纳入 fingerprint 的弱模型关键字段：

```text
status
weak_model_run_id
weak_model_aggregation_id
quality_check_id
quality_status
directional_bias
directional_score
directional_confidence
risk_level
trade_permission
veto_triggered
veto_factors
context_summary
quality_issues
source_config_hashes
```

因此弱模型强度、质量状态、配置 hash 或 WMA/WMQC ID 变化时，20 不会静默复用旧 19 审查。

## 7. 19 prompt 质疑弱模型

修改位置：

1. `app/model_analysis/input_compactor.py::build_compacted_model_review_input_summary`
2. `app/model_analysis/prompt_builder.py::build_model_review_prompt`
3. `app/model_analysis/schema_validator.py::validate_model_review_output`

19 prompt 输入新增：

```text
weak_model_summary
weak_model_review_focus
legacy_math_context
```

prompt 指令明确要求：

```text
审查弱模型，而不是盲信弱模型；若弱模型和策略冲突，优先指出冲突。
```

输出骨架支持表达：

```text
weak_model_assessment
weak_model_supports_strategy
weak_model_conflicts_with_strategy
weak_model_quality_concerns
duplicate_evidence_risk
model_reviewer_note
```

本阶段不自动调用 19，只修改材料和 prompt 构建规则。

## 8. 异常处理

`get_latest_weak_model_material` 查询失败时：

1. `StrategyAggregationService.run_strategy_aggregation()` 捕获异常。
2. 不阻断阶段 18 主链路。
3. 按 `weak_model_summary.status=missing` 构建材料包。
4. 不发送 Hermes。
5. 不重新运行 27A / 27B。

`build_material_pack` 仍沿用阶段 18 原有异常处理：K线窗口不足或 future-leakage guard 失败会 blocked，材料构建内部异常会 failed。

## 9. 对应测试

新增或调整：

1. `tests/strategy_aggregation/test_strategy_aggregation_service.py`
   - available / warning / unchecked / excluded / missing 状态
   - 多 WMR 选择最新 success
   - snapshot 不匹配 WMR 不可选
   - 不包含 `raw_output_json`
   - `legacy_math_context` 去重提示
2. `tests/strategy_aggregation/test_23f_strategy_evidence_aggregation.py`
   - `material_schema_v3`
   - 缺 WMA 时 `weak_model_summary.status=missing`
3. `tests/model_analysis/test_model_analysis_service.py`
   - prompt 包含弱模型摘要和质疑弱模型要求
4. `tests/model_review_aggregation/test_model_review_aggregation_service.py`
   - material fingerprint 包含 weak_model_summary
   - weak_model_summary 变化阻止旧 19 复用

默认 pytest 不请求真实 Binance，不连接真实 MySQL/Redis，不发送真实 Hermes，不调用真实大模型，不访问交易接口。

## 10. 本阶段不负责

1. 不重新运行 27A。
2. 不重新运行 27B。
3. 不新增弱模型。
4. 不调整弱模型参数。
5. 不自动调用 19。
6. 不修改 21 展示。
7. 不接 scheduler 新逻辑。
8. 不自动交易。
9. 不读取账户或仓位。
10. 不请求 Binance REST。
