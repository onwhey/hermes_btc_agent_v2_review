# 23F 策略证据聚合实现说明

## 1. 功能：策略证据聚合

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --dry-run
```

确认写库：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

### 1.2 入口文件

`scripts/run_strategy_evidence_aggregation.py`

入口方法：

`main()`

该脚本只做参数解析、配置初始化、session 创建和 service 调用。

### 1.3 核心调用链路

```text
scripts/run_strategy_evidence_aggregation.py::main
    ↓
app/strategy/aggregation/evidence_service.py::run_strategy_evidence_aggregation
    ↓
app/strategy/aggregation/evidence_repository.py::get_strategy_signal_run
    ↓
app/strategy/aggregation/evidence_repository.py::list_public_strategy_signal_results
    ↓
app/strategy/aggregation/evidence_config.py::StrategyGovernanceProvider.get_strategy_governance
    ↓
app/strategy/aggregation/evidence_aggregator.py::StrategyEvidenceAggregator.aggregate_strategy_evidence
    ↓
app/strategy/aggregation/evidence_repository.py::upsert_aggregation_result
```

## 2. 数据读取

读取表：

- `strategy_signal_run`
- `strategy_signal_result`

`strategy_signal_result` 只读取公开元信息和 `common_payload_json`，包括：

- `strategy_name`
- `strategy_version`
- `strategy_status`
- `strategy_role`
- `common_payload_json`
- `validation_status`
- `validation_errors_json`
- `signal_strength`

本功能不读取 `strategy_payload_json`，不读取策略私有 payload，不调用任何策略内部函数，不重新运行策略。

## 3. 配置读取

读取配置：

- `configs/strategy_aggregation/evidence_aggregation.yaml`
- `configs/strategies/*.yaml`

策略 YAML 中新增治理字段：

- `maturity_stage`
- `participation_mode`
- `decision_weight`
- `can_veto`
- `veto_scope`
- `notification_required`

`enabled=true` 只表示策略运行和落库，不代表参与候选方向聚合。实际参与权限由 `participation_mode / decision_weight / can_veto / veto_scope` 控制。

## 4. 聚合规则

23F 按以下维度聚合：

- `strategy_role`
- `provides`
- `maturity_stage`
- `participation_mode`
- `decision_weight`
- `can_veto`
- `veto_scope`
- `validation_status`
- `common_payload_json`

四档参与模式：

- `observe_only`：进入观察摘要，不影响 `candidate_bias`
- `evidence_only`：进入证据展示，不改变候选方向
- `advisory`：影响解释材料，但不能越权否决
- `decision_participant`：按权重参与候选方向判断

`can_veto / veto_scope` 与 `participation_mode` 独立。只有 `decision_participant + can_veto=true + 明确风险阻断 common_result` 才会产生正式阻断效果。

后续修复规则：

- 23F 会先计算初步 `candidate_bias`，再按 `veto_scope` 判断风控阻断是否匹配当前候选方向。
- `block_long` 不阻断 short 候选；`block_short` 不阻断 long 候选。
- `block_current_candidate` 只阻断当前已经形成的 long/short 候选，不等同于 `block_all`。
- `veto_scope=all_candidates` 或 `effect=block_all` 才允许在 wait / conflict / insufficient_evidence 等非方向候选下产生全局 blocked。
- `role_coverage_matrix` 的 `provided` 只统计成功、校验有效、governance enabled、且 `common_payload_json` 可解析的策略结果。
- `common_payload_json` 解析失败不会中断 23F，但会进入 `evidence_missing` 和 `strategy_conflict_summary`，且不得贡献 coverage。

## 5. 数据写入

新增表：

`strategy_evidence_aggregation_result`

迁移文件：

`migrations/versions/20260602_23f_strategy_evidence_aggregation.py`

写入字段包括：

- `aggregation_id`
- `strategy_signal_run_id`
- `symbol`
- `base_interval`
- `higher_interval`
- `status`
- `candidate_bias`
- `candidate_confidence`
- `decision_readiness`
- `strategy_evidence_summary_json`
- `decision_source_chain_json`
- `role_coverage_matrix_json`
- `evidence_missing_json`
- `strategy_conflict_summary_json`
- `participation_summary_json`
- `observe_only_summary_json`
- `risk_gate_summary_json`
- `model_review_focus_json`
- `not_trading_advice`
- `trace_id`
- `trigger_source`
- `created_by`
- `created_at_utc`
- `updated_at_utc`

JSON 字段只保存摘要、证据链和聚合结果，不保存完整 K 线窗口、完整策略私有计算过程、大模型 prompt/response 或可无限膨胀上下文。

## 6. 幂等规则

`strategy_signal_run_id` 唯一。

同一个 `strategy_signal_run_id` 重复 `--confirm-write` 时：

- 如果没有 23F 结果，插入新记录。
- 如果已有 23F 结果，更新原记录。
- 不插入多条有效聚合结果。
- CLI 输出 `database_action=created` 或 `database_action=updated`。

`--dry-run` 不写库。

## 7. 18 最小衔接

修改文件：

- `app/strategy/aggregation/repository.py`
- `app/strategy/aggregation/service.py`
- `app/strategy/aggregation/material_builder.py`

调用链：

```text
app/strategy/aggregation/service.py::run_strategy_aggregation
    ↓
app/strategy/aggregation/repository.py::get_latest_strategy_evidence_aggregation
    ↓
app/strategy/aggregation/material_builder.py::build_material_pack
```

如果存在 23F 聚合结果，18 的 material pack 会加入：

- `strategy_evidence_summary`
- `decision_source_chain`
- `model_review_focus`

如果不存在 23F 聚合结果，18 保持原逻辑，不崩溃。

本阶段不做 18 深度重构。

## 8. 异常处理

异常路径：

- `strategy_signal_run` 不存在：service 返回 `blocked`，不写库。
- `strategy_signal_run.status` 非 `success / partial_success`：service 返回 `blocked`，不写库。
- `strategy_signal_result` 为空：service 返回 `blocked`，不写库。
- 配置读取或聚合失败：service rollback 并返回 `failed`。
- 写库失败：service rollback 并返回 `failed`。

本功能不发送 Hermes，因此不存在 Hermes partial_success 路径。

## 9. 本功能不负责

- 不新增具体交易策略。
- 不修改 23B / 23C / 23D / 23E 核心算法。
- 不生成最终 advice。
- 不生成 trade_setup。
- 不输出正式 entry / stop_loss / take_profit。
- 不调用大模型。
- 不发送 Hermes。
- 不请求 Binance。
- 不读取账户或持仓。
- 不做自动交易。
- 不做完整复盘统计。
- 不做策略自我进化。
- 不修改正式 K 线表。
- 不读取 Redis。
- 不写入 Redis。

## 10. 测试

对应测试文件：

- `tests/strategy_aggregation/test_23f_strategy_evidence_aggregation.py`

覆盖内容：

- 读取同一 `strategy_signal_run_id` 下全部策略结果。
- 不固定读取 23B-E 策略名。
- 不读取 `strategy_payload_json`。
- `observe_only / evidence_only / advisory / decision_participant` 权限生效。
- `can_veto=false` 不能正式否决。
- `can_veto=true` 可按 `veto_scope` 产生风控阻断。
- 23E `block_current_candidate` 被解释为风控阻断。
- 缺 context / risk_control 时输出 `evidence_missing`。
- dry-run 不写库。
- confirm-write 写库。
- 重复 confirm-write 幂等更新。
- 18 存在 23F 聚合结果时可读取摘要，不存在时不崩溃。

默认 pytest 不请求真实 Binance、不连接真实 Redis、不发送 Hermes、不调用 DeepSeek、不访问交易接口。

已运行：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests -q
git diff --check
```
