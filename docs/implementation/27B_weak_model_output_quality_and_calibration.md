# 27B 弱模型输出质量审查与参数校准实现说明

## 1. 功能：弱模型输出质量检查

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.check_weak_model_output_quality --weak-model-run-id WMR-xxx
python -m scripts.check_weak_model_output_quality --symbol BTCUSDT --base-interval 4h --higher-interval 1d --limit 10
python -m scripts.check_weak_model_output_quality --weak-model-run-id WMR-xxx --confirm-write
```

27B 第一版只检查已经落库的 27A `weak_model_run`、`weak_model_result`、`weak_model_aggregation`。
它不会重新运行弱模型，不会重新聚合，不会修改 27A 原始输出。

### 1.2 入口文件

`scripts/check_weak_model_output_quality.py`

入口方法：

`main()`

### 1.3 核心 service

`app/weak_models/output_quality_service.py`

核心方法：

`WeakModelOutputQualityService.check_weak_model_output_quality()`

### 1.4 核心调用链路

```text
scripts/check_weak_model_output_quality.py::main
    ↓
app/weak_models/output_quality_service.py::WeakModelOutputQualityService.check_weak_model_output_quality
    ↓
app/weak_models/output_quality_repository.py::get_quality_target_by_run_id
    或
app/weak_models/output_quality_repository.py::list_recent_quality_targets
    ↓
app/weak_models/output_quality_rules.py::evaluate_quality_issues
    ↓
app/weak_models/output_quality_repository.py::upsert_quality_check
```

`upsert_quality_check` 只在 `--confirm-write` 且非 dry-run 时执行。

## 2. 数据库表

新增 migration：

`migrations/versions/20260609_27b_weak_model_quality_check.py`

新增表：

`weak_model_quality_check`

字段：

1. `id`
2. `quality_check_id`
3. `weak_model_run_id`
4. `weak_model_aggregation_id`
5. `strategy_signal_run_id`
6. `snapshot_id`
7. `symbol`
8. `base_interval`
9. `higher_interval`
10. `kline_slot_utc`
11. `status`
12. `severity`
13. `issue_count`
14. `warning_count`
15. `critical_count`
16. `should_block_pipeline`
17. `issues_json`
18. `checked_models_json`
19. `summary_text`
20. `trace_id`
21. `created_at_utc`
22. `updated_at_utc`
23. `details_json`

唯一键：

```text
UNIQUE(quality_check_id)
UNIQUE(weak_model_run_id)
```

索引：

```text
idx_weak_model_quality_check_aggregation(weak_model_aggregation_id)
idx_weak_model_quality_check_scope_slot(symbol, base_interval, higher_interval, kline_slot_utc)
idx_weak_model_quality_check_status(status, severity, created_at_utc)
```

幂等规则：

同一个 `weak_model_run_id` 重复 confirm-write 会更新同一条质量检查记录。
不同 `weak_model_run_id` 会生成不同 `quality_check_id` 和不同质量检查记录。

## 3. 读取与写入

读取数据库表：

1. `weak_model_run`
2. `weak_model_result`
3. `weak_model_aggregation`

写入数据库表：

1. `weak_model_quality_check`，仅 `--confirm-write` 时写入。

本功能不读取 Redis。
本功能不写入 Redis。
本功能不请求外部接口。
本功能不请求 Binance REST。
本功能不发送 Hermes。
本功能不调用 DeepSeek/GPT/Claude。
本功能不读取账户、仓位或交易私有状态。
本功能不生成订单。
本功能不自动交易。

## 4. 检查规则

规则文件：

`app/weak_models/output_quality_rules.py`

### 4.1 directional_score 过强

聚合 `directional_score`：

```text
abs(directional_score) >= 0.75 => warning
abs(directional_score) >= 0.90 => critical
```

单模型 `signal_score`：

```text
abs(signal_score) > 0.75 => warning
abs(signal_score) >= 0.90 => critical
```

如果强方向输出缺少 `evidence_json`，额外记录 warning。

### 4.2 confidence 过高

```text
confidence >= 0.80 => warning
confidence >= 0.95 => critical
```

27B 只提示校准建议，不自动修改 confidence。

### 4.3 risk_score 与 risk_level 不匹配

期望分层：

```text
risk_score < 0.35 => low
0.35 <= risk_score < 0.60 => medium
0.60 <= risk_score < 0.80 => high
risk_score >= 0.80 => extreme
```

不匹配时记录 warning。

如果 `risk_score >= 0.80` 但没有 `trade_permission=block` 或 `veto_triggered=true`，记录 warning。

### 4.4 veto_factors 缺失

如果聚合结果：

```text
veto_triggered=true
```

但：

```text
veto_factors_json=[]
```

则记录 warning。

### 4.5 context_summary 缺失

如果 `context_summary_json` 缺失、解析失败或没有 `regime`，记录 warning。

如果存在 context 弱模型结果，但 `context_summary_json` 缺少 `source_model_key`，记录 warning。

### 4.6 observe_only context 污染检查

`maturity_stage=observe_only` 或 `participation_mode=observe_only` 的 context 结果只允许提供背景摘要。

如果 observe_only context 出现以下可能污染正式聚合的字段，记录 warning：

1. `static_weight != 0`
2. `effective_score != 0`
3. `signal_score` 非空
4. `risk_score` 非空
5. `trade_permission` 非空

27B 不重新计算 directional_score，也不修改 trade_permission；它只检查已落库结果是否可疑。

## 5. CLI 输出

输出包含：

1. `quality_check_id`
2. `weak_model_run_id`
3. `weak_model_aggregation_id`
4. `status`
5. `severity`
6. `issue_count`
7. `warning_count`
8. `critical_count`
9. `should_block_pipeline`
10. `database_written`
11. `summary_text`
12. `issues_json`

固定声明：

```text
本检查只用于弱模型输出质量观测，不是交易建议；不自动交易，不读取账户，不生成订单。
```

## 6. dry-run 与 confirm-write

默认行为：

```text
dry-run
只读 weak_model_run/result/aggregation
不写 weak_model_quality_check
```

显式写入：

```bash
python -m scripts.check_weak_model_output_quality --weak-model-run-id WMR-xxx --confirm-write
```

写入行为：

```text
写入或更新 weak_model_quality_check
不修改 weak_model_run
不修改 weak_model_result
不修改 weak_model_aggregation
不修改 configs/weak_models/*.yaml
```

## 7. 不做的事情

27B 本次明确不做：

1. 不接入 18 材料包。
2. 不接入 19/20 模型审查。
3. 不接入 21 建议生命周期。
4. 不接 scheduler。
5. 不重新跑弱模型。
6. 不修改原始 27A 输出。
7. 不自动改配置。
8. 不静默调整权重、confidence、enabled 或 static_weight。
9. 不做胜率、盈亏比、策略评分或模型评分。
10. 不发送 Hermes。
11. 不调用大模型。
12. 不请求 Binance REST。
13. 不读取账户或仓位。
14. 不自动交易。

参数校准在本阶段只以 `issues_json.calibration_suggestion` 和 `summary_text` 的形式提出建议。
如后续确实要改 `configs/weak_models/*.yaml`，必须人工确认并更新 `config_version`，使 `config_hash` 变化。

## 8. 异常处理

参数错误：

`scripts/check_weak_model_output_quality.py::main()` 返回 `EXIT_PARAMETER_OR_DATABASE_ERROR=2`。

数据库查询或写入异常：

CLI 捕获异常，打印中文错误摘要并返回 2。

质量问题：

返回 `status=warning` 或 `status=critical`，但：

```text
should_block_pipeline=false
```

27B 第一版不阻断主链路。

## 9. 测试

测试文件：

`tests/weak_models/test_weak_model_output_quality.py`

覆盖：

1. directional_score 过强产生 warning。
2. confidence 过高产生 warning。
3. risk_score 与 risk_level 不匹配产生 warning。
4. veto_triggered=true 但 veto_factors 缺失产生 warning。
5. context_summary 缺失产生 warning。
6. observe_only context 不影响 directional_score / trade_permission。
7. 默认 dry-run 不写库。
8. confirm-write 写入 weak_model_quality_check。
9. 不调用弱模型。
10. 不发送 Hermes。
11. 不请求 Binance REST。
12. CLI 默认 dry-run。
13. CLI confirm-write。
14. CLI 参数错误 exit code=2。
15. 27B migration 只新增质量检查表，不修改 K线表。

默认 pytest 不访问真实 Binance、真实 MySQL、真实 Redis、真实 Hermes，也不调用大模型。

运行：

```bash
python -m pytest tests/weak_models -q
```

## 10. 当前 27A 输出校准观察

本实现未连接真实数据库，也未读取生产数据。

根据 27B plan 中给出的示例：

```text
directional_score=-0.750000
```

27B 会将其标记为：

```text
status=warning
error_code=directional_score_too_strong
```

这说明如果当前 27A 输出频繁达到 `±0.75`，在 27C 接入 18 之前需要人工复核是否应降低方向分数上限、降低 confidence 或增加冲突场景降置信度。

## 11. 27B-1 参数保守化校准

本次 27B-1 只调整 `trend_strength_directional` 的配置化输出上限，不修改 27A 架构，不修改 27B 质量检查规则，不接入 18/19/20/21，不接 scheduler。

### 11.1 调整项

配置文件：

`configs/weak_models/trend_strength_directional.yaml`

调整内容：

```text
config_version: 27a_v1 -> 27b_1_conservative_v1
strong_signal_score: 隐式默认 0.75 -> 显式配置 0.60
weak_signal_score: 显式配置 0.25
trend_signal_score: 显式配置 0.50
```

`app/weak_models/models.py::TrendStrengthDirectionalModel.evaluate()` 现在从 `params` 读取 `weak_signal_score`、`trend_signal_score`、`strong_signal_score`。如果旧配置没有这些字段，仍使用旧默认值，不改变旧配置的解析兼容性。

### 11.2 调整原因

27B 质量检查规则中：

```text
abs(directional_score) >= 0.75
```

会产生：

```text
error_code=directional_score_too_strong
severity=warning
```

当前观察到的 `weak_model_aggregation.directional_score=-0.75` 属于接入 18 前需要保守化的初始输出。27B-1 将常规强偏多/强偏空从 `±0.75` 下调到 `±0.60`，避免单个趋势弱模型在证据尚未进入 27C 校准前过早给出过强方向分数。

### 11.3 不负责的边界

本次不自动修改任何其他 `configs/weak_models/*.yaml`。
本次不静默调整 `enabled`、`static_weight`、`confidence` 或聚合权重。
本次不写数据库，不新增 migration，不请求 Binance REST，不发送 Hermes，不调用大模型，不读取账户或仓位，不自动交易。

### 11.4 测试

新增/调整测试：

1. `tests/weak_models/test_models_and_aggregation.py::test_conservative_trend_strength_config_avoids_directional_too_strong_warning`
2. `tests/weak_models/test_weak_model_cli_and_config.py::test_27b_1_trend_config_lowers_strong_score_and_changes_hash`

第一项验证同一类强趋势输入在新配置下输出 `0.60`，不会触发 `directional_score_too_strong`。
第二项验证 `config_version` 已更新，且当前配置与旧 `27a_v1` 配置的 `config_hash` 不同。
