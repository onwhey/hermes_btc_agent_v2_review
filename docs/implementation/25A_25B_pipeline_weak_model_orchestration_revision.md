# 25A / 25B Pipeline 接入 27A / 27B 弱模型编排实现说明

## 1. 功能：25A 主 pipeline 在 18 前编排 27A / 27B

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_strategy_pipeline --symbol BTCUSDT --base-interval 4h --higher-interval 1d --confirm-write
```

scheduler 自动触发：

```text
app/scheduler/runner.py::SchedulerRunner._run_strategy_pipeline_post_collect_if_needed
    -> app/scheduler/jobs/strategy_pipeline_job.py::run_strategy_pipeline_after_collect_job
```

### 1.2 入口文件

手动入口：

`scripts/run_strategy_pipeline.py`

入口方法：

`main()`

scheduler 入口：

`app/scheduler/jobs/strategy_pipeline_job.py`

入口方法：

`run_strategy_pipeline_after_collect_job()`

### 1.3 核心调用链路

```text
scripts/run_strategy_pipeline.py::main
    -> app/strategy_pipeline/service.py::run_strategy_pipeline
    -> app/strategy_pipeline/service.py::StrategyPipelineService.run_strategy_pipeline
    -> app/strategy_pipeline/service.py::StrategyPipelineService._run_confirmed_pipeline
    -> app/strategy_pipeline/stage17_reuse.py::resolve_stage17_result_or_reusable_duplicate
    -> app/strategy_pipeline/evidence_stage.py::run_or_reuse_stage23f_for_pipeline
    -> app/strategy_pipeline/service.py::StrategyPipelineService._run_stage26b
    -> app/strategy_pipeline/weak_model_stage.py::run_or_reuse_weak_model_stages_for_pipeline
    -> app/weak_models/service.py::WeakModelService.run_weak_models_for_strategy_signal
    -> app/weak_models/output_quality_service.py::WeakModelOutputQualityService.check_weak_model_output_quality
    -> app/strategy_pipeline/service.py::StrategyPipelineService._run_stage18
    -> app/strategy_pipeline/service.py::StrategyPipelineService._run_stage20
    -> app/strategy_pipeline/service.py::StrategyPipelineService._run_stage21
```

本次修订保留既有 26B 策略证据质量闸门，实际顺序为：

```text
16/17 SSR
    -> 23F/24A SEA
    -> 26B strategy evidence quality gate
    -> 27A WMR/WMA
    -> 27B WMQC
    -> 18 AMP
    -> 20C/20A
    -> 21
```

### 1.4 读取配置

新增配置：

```text
STRATEGY_PIPELINE_WEAK_MODELS_ENABLED=true
STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED=true
```

读取位置：

`app/core/config.py::load_settings`

默认值：

`app/core/constants.py`

如果 `STRATEGY_PIPELINE_WEAK_MODELS_ENABLED=false`，pipeline 在 `27a_weak_model_run` 阻断，`error_code=weak_model_disabled_by_config`，不进入 18。

如果 `STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED=false`，pipeline 在 `27b_weak_model_quality_check` 阻断，`error_code=weak_model_quality_gate_disabled_by_config`，不进入 18。

### 1.5 27A 复用与创建规则

复用查询：

`app/strategy_pipeline/repository.py::get_latest_success_weak_model_package_for_strategy_run`

复用条件：

- `weak_model_run.run_status = success`
- WMR 与 WMA 都匹配同一个 `strategy_signal_run_id`
- `snapshot_id`、`symbol`、`base_interval`、`higher_interval`、`kline_slot_utc` 匹配当前 SSR/slot
- WMA 存在

没有可复用 WMR/WMA 时调用：

`app/weak_models/service.py::WeakModelService.run_weak_models_for_strategy_signal`

请求使用当前 SSR 的 `strategy_signal_run_id`，并传入当前 pipeline 的 `pipeline_run_id`、`trigger_source`、`trace_id`。

27A 失败、blocked、partial_success 或无 WMA 时，pipeline 阻断：

```text
current_step=27a_weak_model_run
error_code=weak_model_run_failed 或 27A 返回的 error_code
```

### 1.6 27B 复用与创建规则

复用查询：

`app/strategy_pipeline/repository.py::get_latest_weak_model_quality_check_by_run_id`

没有可复用 WMQC 时调用：

`app/weak_models/output_quality_service.py::WeakModelOutputQualityService.check_weak_model_output_quality`

请求使用 27A 输出的 `weak_model_run_id`，并设置 `confirm_write=True`。

27B 状态处理：

- `passed`：继续进入 18。
- `warning`：继续进入 18，18 读取 WMQC 后会在 `weak_model_summary.quality_status` 中显式保留 warning。
- `critical`：阻断，不进入 18，`error_code=weak_model_quality_critical`。
- 无结果、异常或未知状态：阻断，不进入 18，`error_code=weak_model_quality_check_failed`。

## 2. 功能：25B scheduler runner 继承统一 pipeline

### 2.1 scheduler 调用链

```text
app/scheduler/runner.py::SchedulerRunner.run_once
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_pipeline_post_collect_if_needed
    -> app/scheduler/jobs/strategy_pipeline_job.py::run_strategy_pipeline_after_collect_job
    -> app/strategy_pipeline/service.py::run_strategy_pipeline
```

scheduler 不重复实现 27A / 27B，只调用统一 pipeline service。

### 2.2 scheduler 记录字段

`app/scheduler/runner.py::_strategy_pipeline_result_details` 现在会记录：

- `weak_model_run_id`
- `weak_model_aggregation_id`
- `weak_model_quality_check_id`
- `weak_model_status`
- `weak_model_quality_status`
- `weak_model_directional_score`
- `weak_model_risk_level`
- `weak_model_trade_permission`
- `weak_model_pipeline_action`
- `weak_model_quality_pipeline_action`

这些字段来自 `StrategyPipelineResult`，不会由 scheduler 自行计算。

## 3. 数据库读写

### 3.1 读取表

pipeline 编排层新增读取：

- `weak_model_run`
- `weak_model_aggregation`
- `weak_model_quality_check`

仍会读取既有：

- `market_kline_4h`
- `strategy_signal_run`
- `strategy_signal_scheduler_event_log`
- `strategy_evidence_aggregation_result`
- `analysis_material_pack`

### 3.2 写入表

pipeline 编排层自身仍只写：

- `strategy_pipeline_event_log`

27A service 在需要创建时写：

- `weak_model_run`
- `weak_model_result`
- `weak_model_aggregation`

27B service 在需要创建时写：

- `weak_model_quality_check`

18/20/21 的写入仍由原服务负责，本次没有修改其核心逻辑。

### 3.3 幂等规则

- 同 SSR 已有 success WMR/WMA 时复用，不重复创建。
- 同 WMR 已有 WMQC 时复用，不重复创建。
- 已有可复用 AMP 时仍沿用原 stage18 already_exists 处理。
- WMR/WMA/WMQC 流水 ID 不进入 20 material fingerprint 的逻辑本次未改动。

## 4. 外部服务与边界

本次功能不请求 Binance REST。

本次功能不读取 Redis，除既有 pipeline lock 仍由 25A lock manager 使用 Redis。

本次功能不发送 Hermes。26B/21 的 Hermes 行为仍受原有配置和服务边界控制，本次未新增发送路径。

本次功能不调用 DeepSeek、GPT、Claude 或其他大模型。20/19 是否调用真实模型仍由既有 20C 配置和 pipeline request gate 控制，本次未绕过。

本次功能不读取账户，不读取仓位，不生成订单，不自动交易。

本次功能不请求 Binance REST，不重新跑 15 snapshot，不重新跑 18 隐式弱模型，不修改 27A/27B 算法，不修改 27C material schema，不修改 19 prompt，不修改 21 展示。

## 5. 异常处理

27A 查询复用失败或 service 异常：

```text
app/strategy_pipeline/weak_model_stage.py::_run_or_reuse_stage27a
    -> 捕获异常
    -> pipeline status=blocked
    -> current_step=27a_weak_model_run
    -> error_code=weak_model_run_failed
```

27A 返回非 success 或没有 aggregation：

```text
status=blocked
current_step=27a_weak_model_run
error_code=27A 返回的 error_code，或 weak_model_run_failed
```

27B 查询复用失败或 service 异常：

```text
app/strategy_pipeline/weak_model_stage.py::_run_or_reuse_stage27b
    -> 捕获异常
    -> pipeline status=blocked
    -> current_step=27b_weak_model_quality_check
    -> error_code=weak_model_quality_check_failed
```

27B critical：

```text
status=blocked
current_step=27b_weak_model_quality_check
error_code=weak_model_quality_critical
```

配置关闭：

```text
STRATEGY_PIPELINE_WEAK_MODELS_ENABLED=false
    -> current_step=27a_weak_model_run
    -> error_code=weak_model_disabled_by_config

STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED=false
    -> current_step=27b_weak_model_quality_check
    -> error_code=weak_model_quality_gate_disabled_by_config
```

所有阻断都发生在 18 前，因此自动链路不会生成 `weak_model_summary.status=missing` 或 `unchecked` 的新 AMP。

## 6. 对应测试

新增和调整：

- `tests/strategy_pipeline/test_strategy_pipeline_service.py`
- `tests/scheduler/test_strategy_pipeline_scheduler_hook.py`

覆盖范围：

- pipeline 在 18 前运行 27A / 27B。
- 27A / 27B 已有结果时复用，不重复生成。
- 27B warning 允许进入 18。
- 27B critical 阻断 18。
- 27A failure 阻断 18。
- 27B execution failure 阻断 18。
- 配置关闭时显式阻断并记录原因。
- scheduler runner 仍调用统一 pipeline service，并记录 weak-model 字段。

默认 pytest 不请求真实 Binance，不连接真实 MySQL/Redis，不发送真实 Hermes，不调用真实大模型。

## 7. 人工检查命令

```bash
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/scheduler -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/weak_models -q
python -m pytest tests/model_review_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest -q
```

人工运行 pipeline：

```bash
python -m scripts.run_strategy_pipeline --symbol BTCUSDT --base-interval 4h --higher-interval 1d --confirm-write
```

输出会包含：

- `weak_model_run_id`
- `weak_model_aggregation_id`
- `weak_model_quality_check_id`
- `weak_model_status`
- `weak_model_quality_status`
- `weak_model_directional_score`
- `weak_model_risk_level`
- `weak_model_trade_permission`
- `weak_model_pipeline_action`
- `weak_model_quality_pipeline_action`

## 8. 本次明确没有实现

- 不新增弱模型。
- 不调整弱模型参数。
- 不修改 27C material schema。
- 不新增 scoring_contract。
- 不修改大模型 prompt。
- 不修改 21 Hermes 展示。
- 不接新 scheduler job。
- 不请求 Binance REST。
- 不读取账户或仓位。
- 不生成订单。
- 不自动交易。
