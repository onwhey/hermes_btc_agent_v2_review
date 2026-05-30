# 24C model_review_uses_strategy_evidence 实现说明

## 1. 功能：模型审查消费 23F 策略证据链

### 1.1 发起方式

用户手动执行：

```text
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --dry-run
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --use-real-model --model-key <配置中的 model_key> --confirm-real-model-cost --confirm-write
```

本阶段不新增 scheduler，不自动运行模型审查。

### 1.2 入口文件

`scripts/run_model_analysis.py`

入口方法：

`main()`

脚本只解析 CLI 参数、初始化 session、调用 app service。脚本不构造 prompt，不调用 provider，不写数据库，不发送 Hermes，不生成 advice，不执行交易。

### 1.3 核心调用链路

```text
scripts/run_model_analysis.py::main
    ↓
app/model_analysis/service.py::ModelAnalysisService.run_model_analysis
    ↓
app/model_analysis/repository.py::ModelAnalysisRepository.get_material_pack_by_id
    ↓
app/model_analysis/material_pack_reviewability.py::validate_material_pack_reviewability
    ↓
app/model_analysis/material_input.py::extract_strategy_evidence
    ↓
app/model_analysis/material_input.py::build_time_anchor_summary
    ↓
app/model_analysis/provider_resolution.py::resolve_provider_for_request
    ↓
app/model_analysis/prompt_builder.py::build_model_review_prompt
    ↓
app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material
    或 app/model_analysis/providers/deepseek.py::DeepSeekReviewProvider.call_review_model
    ↓
app/model_analysis/schema_validator.py::validate_model_review_output
    ↓
app/model_analysis/repository.py::create_model_analysis_run
    ↓
app/model_analysis/repository.py::create_model_analysis_result
```

## 2. 数据读取

### 2.1 读取数据库表

读取：

```text
analysis_material_pack
```

读取字段包括：

```text
material_pack_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
material_json
summary_json
question_json
validation_plan_json
data_window_json
future_leakage_guard_json
created_at_utc
updated_at_utc
```

24C 只从 `material_json.strategy_evidence` 读取 23F 公开证据链，不读取任何 `strategy_payload_json`，不读取策略私有 payload，不重新运行策略，不复制 23F 聚合逻辑。

### 2.2 strategy_evidence 输入

`app/model_analysis/material_input.py::extract_strategy_evidence()` 从 `material_json` 中提取：

```text
strategy_evidence.source
strategy_evidence.aggregation_id
strategy_evidence.strategy_signal_run_id
candidate_bias
decision_readiness
strategy_evidence_summary
decision_source_chain
role_coverage_matrix
evidence_missing
strategy_conflict_summary
risk_gate_summary
model_review_focus
```

如果 `strategy_evidence` 缺失、为空，或 `source != strategy_evidence_aggregation_result`，service 在调用模型前 blocked。

### 2.3 时间锚点输入

`app/model_analysis/material_input.py::build_time_anchor_summary()` 从 material pack 和 K 线摘要中提取：

```text
analysis_time_utc
analysis_time_prc
latest_base_kline_open_time_utc
latest_base_kline_close_time_utc
latest_higher_kline_open_time_utc
latest_higher_kline_close_time_utc
data_freshness_status
```

UTC 到 PRC 展示转换统一使用 `app/core/time_utils.py`，不手写 `+8`。

缺少必要时间字段、检测到未来 K 线或时间过旧时，service 在调用模型前 blocked，错误码为 `material_pack_time_anchor_missing` 或 `stale_data`。

## 3. Prompt 语义

`app/model_analysis/prompt_builder.py::build_model_review_prompt()` 将模型定位为独立风险审查官：

```text
You are an independent risk review officer.
You must rebut 23F first.
Do not use material-external news, macro, on-chain, account, position, or old BTC market-memory information.
Do not treat 23F candidate_bias as fact.
Do not output formal entry / stop_loss / take_profit.
```

Prompt 只要求输出结构化 JSON，不要求也不允许生成最终交易建议。

## 4. 输出 JSON schema

24C 要求模型输出至少包含：

```text
agreement_with_23f
review_decision
main_objection
strongest_counterargument
missing_evidence
disputed_strategy_points
overestimated_evidence
underestimated_evidence
scenario_review
discipline_check
recommendation_to_advice_layer
evidence_refs
time_freshness_assessment
boundary_flags
quality_flags
confidence
summary
not_trading_advice
human_review_required
is_final_trading_advice
is_trading_signal
is_executable
auto_trading_allowed
```

其中 `scenario_review` 包含：

```text
main_scenario
opposite_scenario
risk_scenario
no_trade_scenario
```

`discipline_check` 包含：

```text
chasing_risk
risk_reward_quality
stop_condition_clarity
overtrading_risk
```

## 5. 输出校验与归一化

`app/model_analysis/schema_validator.py::validate_model_review_output()` 检查：

```text
合法 JSON 对象
必填字段
枚举值
strongest_counterargument 是否为空
evidence_refs 是否为空
是否出现 entry_price / stop_loss / take_profit 等越界交易字段
是否引用材料外新闻、链上、账户、用户仓位或未提供价格
recommendation_to_advice_layer 是否合法
time_freshness_assessment 是否存在
是否明显只复述 23F
```

低质量输出会写入 `quality_flags`，例如：

```text
missing_strongest_counterargument
missing_evidence_refs
possible_23f_restatement
```

越界输出会写入 `boundary_flags`，例如：

```text
boundary_violation
forbidden_trading_field_present
external_information_reference
```

低质量或越界输出可以保留为审计结果，但会被归一化为低可信、需要人工复核，不会被标记为高可信可采纳结果。

## 6. 入库与追溯

本阶段未新增 migration，复用现有表：

```text
model_analysis_run
model_analysis_result
```

`model_analysis_run.input_summary_json` 保存：

```text
strategy_evidence
time_anchors
strategy_evidence_aggregation_id
candidate_bias
decision_readiness
strategy_evidence_summary
decision_source_chain
role_coverage_matrix
evidence_missing
strategy_conflict_summary
risk_gate_summary
model_review_focus
```

`model_analysis_run.response_metadata_summary_json` 保存真实 provider 返回的 schema normalization、`boundary_flags`、`quality_flags` 等摘要。

`model_analysis_result` 继续使用既有字段保存归一化审查结论，并在 `validation_focus_json` 中附带 `review_payload_24c`，用于追溯完整 24C 审查结构。

## 7. 单模型主审查预留

当前阶段：

```text
chain_mode = single
stage_role = primary_review
stage_order = 1
parent_review_id = null
```

对应现有字段：

```text
analysis_mode
model_role
chain_step
parent_model_analysis_run_id
chain_id
```

本阶段不实现 relay、parallel、adversarial_review 或 synthesis_review，只保留现有链式字段兼容。

## 8. 配置

继续使用现有模型配置体系：

```text
configs/model_review/model_registry.yaml
configs/model_review/mock_review.yaml
configs/model_review/profiles/deepseek/*.yaml
```

关键配置：

```text
model_key
provider
enabled
model_name
model_version
model_role
analysis_mode
prompt_template_version
review_schema_version
profile_hash
cost_policy
```

本阶段不在业务代码中写死具体真实模型名称。真实模型调用仍受：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED
--use-real-model
--model-key
--confirm-real-model-cost
--confirm-write
```

共同控制。

## 9. Hermes 与外部服务

dry-run 默认使用 mock provider，不请求外部接口。

真实模型调用仅在用户显式开启真实模型总闸、指定 `model_key`、确认成本并 confirm-write 时发生。

本阶段不发送最终 Hermes 策略通知，不生成 advice，不生成 trade_setup，不读取账户或持仓，不自动交易。

Hermes 只沿用原有模型审查异常告警路径，例如 provider 调用失败、持久化失败或 oversized response，不新增最终策略通知。

## 10. 异常处理

缺少 23F `strategy_evidence`：

```text
material_pack_reviewability.py::validate_material_pack_reviewability
    -> blocked
    -> error_code = strategy_evidence_missing
    -> 不调用模型
```

`strategy_evidence.source != strategy_evidence_aggregation_result`：

```text
error_code = strategy_evidence_source_not_23f
不调用模型
```

缺少时间锚点：

```text
error_code = material_pack_time_anchor_missing
不调用模型
```

数据过旧或未来 K 线：

```text
error_code = stale_data
不调用模型
```

模型输出 schema 缺字段或枚举非法：

```text
error_code = schema_missing_required_field 或 schema_invalid_enum_value
不写 final result
```

模型输出越界交易字段：

```text
写入 boundary_flags
human_review_required = true
evidence_quality = weak
risk_acceptability = unacceptable
```

## 11. 不负责边界

本功能不生成最终 advice。
本功能不生成 trade_setup。
本功能不发送最终 Hermes 策略通知。
本功能不读取 Binance。
本功能不读取账户。
本功能不读取持仓。
本功能不自动交易。
本功能不读取 strategy_payload_json。
本功能不复制 23F 聚合逻辑。
本功能不修改 23B / 23C / 23D / 23E / 23F 策略算法。

## 12. 测试

对应测试：

```text
tests/model_analysis/test_model_analysis_service.py
tests/model_analysis/test_model_analysis_19b.py
```

覆盖：

```text
模型输入包含 strategy_evidence
模型输入包含 analysis_time_utc / analysis_time_prc / latest kline time
缺少 strategy_evidence 时 blocked 且不调用模型
缺少时间锚点时 blocked 且不调用模型
prompt 要求反驳 23F
prompt 禁止材料外信息和旧行情记忆
合法 JSON 能解析并入库
缺 strongest_counterargument 标记 low_quality
缺 evidence_refs 标记 low_quality
出现 entry / stop_loss / take_profit 标记 boundary_violation
引用材料外信息标记 boundary_violation
dry-run 不写库
confirm-write 才写库
chain_mode=single / stage_role=primary_review
不读取 strategy_payload_json
不生成 advice
不发送最终 Hermes 策略通知
```

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用真实大模型。
