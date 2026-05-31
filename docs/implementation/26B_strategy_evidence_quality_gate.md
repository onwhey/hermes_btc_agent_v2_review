# 26B 策略证据质量闸门实现说明

## 1. 功能：25 pipeline 中的策略证据质量闸门

### 1.1 发起方式

26B 主体由 25 pipeline 自动调用，不是独立调度任务：

```text
scripts/run_strategy_pipeline.py::main
    ↓
app/strategy_pipeline/service.py::StrategyPipelineService.run_strategy_pipeline
    ↓
app/strategy_pipeline/service.py::StrategyPipelineService._run_confirmed_pipeline
    ↓
23F/24 证据聚合成功后
    ↓
app/strategy_pipeline/service.py::StrategyPipelineService._run_stage26b
    ↓
app/strategy/evidence_quality/service.py::StrategyEvidenceQualityGateService.run_strategy_evidence_quality_gate
```

接入位置固定为：

```text
16/17 策略信号
    ↓
23F/24 策略证据聚合
    ↓
26B 策略证据质量闸门
    ↓
18 材料包
    ↓
20 模型审查
    ↓
21 建议生命周期
```

26B failed 时，25 pipeline 不会调用 18/20/21。

### 1.2 入口文件

核心入口：

`app/strategy_pipeline/service.py`

入口方法：

`StrategyPipelineService._run_stage26b()`

核心 service：

`app/strategy/evidence_quality/service.py`

核心方法：

`StrategyEvidenceQualityGateService.run_strategy_evidence_quality_gate()`

辅助模块：

- `app/strategy/evidence_quality/config.py`：读取 registry/YAML 并识别正常运行策略。
- `app/strategy/evidence_quality/evaluator.py`：执行纯证据质量判断，不访问数据库和外部服务。
- `app/strategy/evidence_quality/repository.py`：读取 SSR/SEA/strategy result，写入 26B 质量结果。
- `app/strategy/evidence_quality/alerting.py`：固定模板 Hermes 系统告警。

### 1.3 读取配置

读取统一配置：

- `STRATEGY_EVIDENCE_QUALITY_GATE_ENABLED`
- `STRATEGY_EVIDENCE_QUALITY_GATE_ALERT_ENABLED`

读取本地策略配置：

- `configs/strategies/strategy_registry.yaml`
- `configs/strategies/*.yaml`
- `configs/strategy_aggregation/evidence_aggregation.yaml`

正常运行策略第一版判定：

```text
enabled=true
maturity_stage=active
且 participation_mode=decision_participant 或 can_veto=true
```

`gann_placeholder` 属于 experimental / observe_only / decision_weight=0，占位策略缺失不阻断。

### 1.4 数据库读取

26B 通过 repository 只读取：

- `strategy_signal_run`
- `strategy_signal_result`
- `strategy_evidence_aggregation_result`
- `strategy_evidence_quality_check_result` 现有结果，用于幂等更新

读取字段以公开证据为主：

- `strategy_name`
- `strategy_role`
- `strategy_status`
- `validation_status`
- `common_payload_json`
- `role_coverage_matrix_json`
- SSR / SEA / symbol / interval / trace 相关字段

26B 不读取策略私有 payload，不读取模型 prompt/response，不读取账户或仓位。

### 1.5 数据库写入

26B 写入或更新：

`strategy_evidence_quality_check_result`

核心字段：

- `quality_check_id`
- `pipeline_run_id`
- `strategy_signal_run_id`
- `evidence_aggregation_id`
- `symbol`
- `base_interval`
- `higher_interval`
- `kline_slot_utc`
- `status`
- `severity`
- `should_block_pipeline`
- `error_code`
- `error_message`
- `failed_checks_json`
- `warning_checks_json`
- `strategy_quality_json`
- `role_quality_json`
- `config_snapshot_json`
- `alert_required`
- `alert_status`
- `alert_message_id`
- `not_trading_advice`
- `trigger_source`
- `trace_id`

唯一键：

- `quality_check_id`
- `(evidence_aggregation_id, trigger_source)`

幂等规则：

同一个 SEA 在 `trigger_source=pipeline` 下重复运行时更新已有 26B 行，不重复创建质量结果。

JSON 字段只保存紧凑摘要、失败字段和配置快照，不保存完整 K线窗口、完整策略上下文、完整模型输入或输出。

### 1.6 blocked 时写入 25 pipeline event

26B failed 后，25 pipeline final event 写入：

```text
status=blocked
current_step=26b_strategy_evidence_quality_gate
error_code=strategy_evidence_quality_failed
error_message=中文失败摘要
details_json.stage26b_result.quality_check_id
details_json.stage26b_result.failed_strategies
details_json.stage26b_result.failed_roles
details_json.stage26b_result.missing_fields
details_json.stage26b_result.alert_status
details_json.stage26b_result.alert_error_message
details_json.stage26b_result.trace_id
```

blocked 后不会调用：

- `app/strategy_pipeline/service.py::StrategyPipelineService._run_stage18`
- `app/strategy_pipeline/service.py::StrategyPipelineService._run_stage20`
- `app/strategy_pipeline/service.py::StrategyPipelineService._run_stage21`

### 1.7 Hermes 告警

26B blocking failure 会通过固定模板发送系统重大告警：

入口：

`app/strategy/evidence_quality/alerting.py::send_strategy_evidence_quality_failure_alert`

使用：

- `app/alerting/types.py::AlertType.STRATEGY_EVIDENCE_QUALITY_FAILURE`
- `severity=critical`
- `app/alerting/templates.py::render_alert_message`
- `app/alerting/hermes_client.py::HermesClient.send_alert_message`
- `app/storage/mysql/repositories/alert_message_repository.py::AlertMessageRepository`

告警模板中文内容包含：

```text
标题：策略证据质量重大异常
symbol / base_interval / higher_interval
kline_slot_utc
pipeline_run_id
strategy_signal_run_id
strategy_evidence_aggregation_id
失败策略列表
失败角色列表
缺失字段 / 失败原因
处理结果：
- 已阻断 18 材料包
- 未调用大模型
- 未生成策略建议
- 未自动交易
not_trading_advice=true
```

Hermes 发送失败不会回滚 `strategy_evidence_quality_check_result`。service 会把 `alert_status=submit_failed` 和失败摘要写回 26B 结果，并在 pipeline details 中体现。

### 1.8 异常处理

数据库或配置读取异常：

```text
app/strategy/evidence_quality/service.py::run_strategy_evidence_quality_gate
    ↓ 抛出
app/strategy_pipeline/service.py::run_strategy_pipeline
    ↓ 捕获
strategy_pipeline_event_log status=failed
```

Hermes 异常：

```text
app/strategy/evidence_quality/alerting.py::send_strategy_evidence_quality_failure_alert
    ↓ 抛出
app/strategy/evidence_quality/service.py::_send_alert_for_blocking_result
    ↓ 捕获
strategy_evidence_quality_check_result.alert_status=submit_failed
pipeline_event_log.details_json.stage26b_result.alert_status=submit_failed
```

Hermes 异常不会让 pipeline 继续进入 18，也不会回滚已写入的 26B 质量结果。

### 1.9 辅助只读 CLI

新增只读查询脚本：

```text
python -m scripts.check_strategy_evidence_quality --symbol BTCUSDT --base-interval 4h --higher-interval 1d --limit 20
```

入口：

`scripts/check_strategy_evidence_quality.py::main`

调用：

`app/strategy/evidence_quality/service.py::StrategyEvidenceQualityGateService.query_strategy_evidence_quality_results`

该 CLI 只读 `strategy_evidence_quality_check_result`，不运行 26B，不写库，不发送 Hermes。

退出码：

- `0`：查询结果均非 blocking，或没有结果
- `1`：存在 failed/blocking 质量结果
- `2`：参数错误或数据库查询失败

### 1.10 不负责边界

本功能不请求 Binance。
本功能不读取或修改正式 K线表。
本功能不做 K线漏采/连续性检查。
本功能不修改策略算法。
本功能不修改 18 材料包核心生成逻辑。
本功能不修改 20 模型审查逻辑。
本功能不修改 21 建议生命周期逻辑。
本功能不调用 DeepSeek / GPT / Claude。
本功能不读取账户或仓位。
本功能不生成订单。
本功能不自动交易。

当前 SSR/SEA 表没有直接 `kline_slot_utc` 字段，26B 会记录 pipeline 传入的 slot，并校验 SSR/SEA id、symbol、base_interval、higher_interval。完整的“不同 slot 混用”只能在后续表结构提供 slot 字段后进一步严格化。

### 1.11 对应测试

新增：

`tests/strategy_evidence_quality/test_strategy_evidence_quality_gate.py`

覆盖：

- active decision_participant 策略缺失 => blocked
- active can_veto 风控策略缺失 => blocked
- strategy_status=failed / invalid => blocked
- common_payload_json 解析失败 => blocked
- support_resistance 缺 key_levels => blocked
- filter 缺 trigger_state => blocked
- risk_control 缺 risk_gate_decision => blocked
- gann_placeholder observe_only / decision_weight=0 不阻断
- experimental / internship 等非 active required 策略缺失不阻断
- required role 缺失阻断
- blocked 后触发 Hermes 告警
- Hermes 告警失败不回滚 26B 质量结果
- gate disabled 时记录 skipped，不阻断
- CLI 只读查询不写库、不发 Hermes

更新：

`tests/strategy_pipeline/test_strategy_pipeline_service.py`

覆盖：

- 26B 接入顺序在 23F/24 后、18 前
- 26B blocked 后不调用 18/20/21
- 26B Hermes 失败信息进入 pipeline details

默认 pytest 不请求真实 Binance、不连接真实 Redis、不调用真实模型、不读取账户、不自动交易。Hermes 发送使用 mock/fake。
