# 24B material pack consumes 23F 实现说明

## 1. 功能：18 material pack 消费 23F 策略证据链

### 1.1 发起方式

用户手动执行现有 18 聚合入口：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

也可以 dry-run：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli
```

本阶段没有新增 CLI，没有新增 scheduler job。脚本仍只负责参数解析、打开 MySQL session、调用 app service，不承载 23F 或 18 的核心业务逻辑。

### 1.2 入口文件

`scripts/run_strategy_aggregation.py`

入口方法：

`main()`

核心 service：

`app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation`

### 1.3 核心调用链路

```text
scripts/run_strategy_aggregation.py::main
    ↓
app/strategy/aggregation/service.py::run_strategy_aggregation
    ↓
app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation
    ↓
app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_strategy_signal_run
    ↓
app/strategy/aggregation/repository.py::StrategyAggregationRepository.list_strategy_signal_results
    ↓
app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_latest_strategy_evidence_aggregation
    ↓
app/strategy/aggregation/material_builder.py::build_material_pack
    ↓
app/strategy/aggregation/repository.py::StrategyAggregationRepository.create_material_pack
```

## 2. 23F 读取规则

18 在构造 material pack 前，通过：

`app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_latest_strategy_evidence_aggregation`

按 `strategy_signal_run_id` 查询：

`strategy_evidence_aggregation_result`

读取字段包括：

- `aggregation_id`
- `strategy_signal_run_id`
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

18 只搬运 23F 已产出的公开聚合结果，不重新计算 `candidate_bias`、`role_coverage_matrix`、`strategy_conflict_summary` 或 `risk_gate_summary`。

## 3. material pack 新增结构

当存在 23F aggregation 时，`material_json` 中写入：

```json
{
  "strategy_evidence": {
    "source": "strategy_evidence_aggregation_result",
    "aggregation_id": "SEA-xxx",
    "strategy_signal_run_id": "SSR-xxx",
    "status": "success",
    "candidate_bias": "wait",
    "candidate_confidence": "0.7200",
    "decision_readiness": "wait_for_confirmation",
    "strategy_evidence_summary": {},
    "decision_source_chain": [],
    "role_coverage_matrix": {},
    "evidence_missing": [],
    "strategy_conflict_summary": {},
    "participation_summary": {},
    "observe_only_summary": {},
    "risk_gate_summary": {},
    "model_review_focus": {},
    "not_trading_advice": true
  }
}
```

当不存在 23F aggregation 时，`material_json` 中仍写入 `strategy_evidence`，但明确标记旧兼容来源：

```json
{
  "strategy_evidence": {
    "source": "legacy_strategy_results",
    "aggregation_id": null,
    "strategy_signal_run_id": "SSR-xxx",
    "warning": "23F aggregation not found; material pack used legacy compatible strategy evidence.",
    "not_trading_advice": true
  }
}
```

这样后续模型审查不会误以为 material pack 已经使用 23F 聚合结果。

## 4. 数据库读写

本功能读取：

- `strategy_signal_run`
- `strategy_signal_result`
- `strategy_evidence_aggregation_result`
- `market_context_snapshot` 及 snapshot 引用的 K 线窗口，仍由现有 snapshot repository 只读恢复

本功能写入：

- `strategy_aggregation_run`
- `analysis_material_pack`

本功能不写入：

- `strategy_evidence_aggregation_result`
- `market_kline_4h`
- `market_kline_1d`
- 账户、订单、持仓、杠杆、保证金相关表

本阶段未新增 migration。

## 5. 私有 payload 边界

24B 明确禁止 18 读取任何策略私有 payload。

实现上：

- `StrategyAggregationRepository.list_strategy_signal_results()` 使用 `load_only()` 只加载 18 需要的公开/旧兼容字段和 `common_payload_json`。
- `app/strategy/aggregation/rules.py` 不再读取策略私有 payload，也不再把私有 payload summary 放入 18 evidence。
- `app/strategy/aggregation/material_builder.py` 只读取 23F 聚合表中已经生成的公开 JSON 字段。

如果未来确实需要解析某个策略私有结构，应在独立 adapter 中做可选解析，不应放入 18 material pack builder。

## 6. 异常处理

### 6.1 找不到 23F aggregation

发生位置：

`app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation`

处理方式：

- 不阻断 18 material pack 生成。
- `material_json.strategy_evidence.source` 写为 `legacy_strategy_results`。
- 写入 warning：`23F aggregation not found; material pack used legacy compatible strategy evidence.`
- 不发送 Hermes。
- 不调用大模型。

### 6.2 23F aggregation 查询异常

发生位置：

`app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation`

处理方式：

- 捕获查询异常，将 23F aggregation 视为缺失。
- 保持 18 旧逻辑兼容，不因 23F 缺失而失败。
- 不发送 Hermes。24A 已负责 23F 自动聚合失败告警。

### 6.3 material pack 写库异常

沿用 18 已有处理：

- 异常由 repository 抛出。
- service 捕获后 rollback。
- 返回 `failed` 或按已有唯一键冲突逻辑恢复为 `skipped`。
- 不修改正式 K 线表。
- 不自动修复数据。

## 7. 外部接口、Redis、Hermes、大模型

本功能不请求外部接口。

本功能不读取 Redis。

本功能不写入 Redis。

本功能不新增 Hermes 发送逻辑。

本功能不调用 DeepSeek、OpenAI、Claude 或其他大模型。

本功能不请求 Binance。

本功能不读取账户或持仓。

本功能不生成 advice。

本功能不生成 trade_setup。

本功能不涉及自动交易。

## 8. 对应测试

测试文件：

- `tests/strategy_aggregation/test_23f_strategy_evidence_aggregation.py`
- `tests/strategy_aggregation/test_strategy_aggregation_service.py`

覆盖内容：

- 有 23F aggregation 时，material pack 使用 `strategy_evidence.source = strategy_evidence_aggregation_result`。
- material pack 携带 `aggregation_id`、`strategy_signal_run_id`、`candidate_bias`、`decision_readiness`。
- material pack 携带 `strategy_evidence_summary`、`decision_source_chain`、`role_coverage_matrix`、`risk_gate_summary`、`model_review_focus`。
- 没有 23F aggregation 时，material pack 不崩溃并写入 legacy warning。
- 18 保留旧数学材料逻辑，例如 swing、volatility、support_resistance。
- 18 不读取私有 strategy payload。
- 24B 不调用大模型，不请求 Binance，不新增 Hermes。

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

## 9. 人工检查命令

运行 18 material pack：

```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

检查 material pack：

```text
material_json.strategy_evidence.source
material_json.strategy_evidence.aggregation_id
material_json.strategy_evidence.candidate_bias
material_json.strategy_evidence.decision_source_chain
```

没有 23F 时应看到：

```text
material_json.strategy_evidence.source = legacy_strategy_results
material_json.strategy_evidence.warning = 23F aggregation not found; material pack used legacy compatible strategy evidence.
```

## 10. 本功能不负责

- 不做 24C / 24D / 25。
- 不触发 23F 自动聚合。
- 不重写 23F 聚合算法。
- 不复制 23F 的 candidate bias / conflict / coverage 计算逻辑。
- 不拆分现有 market math 材料。
- 不开发新策略。
- 不修改 23B / 23C / 23D / 23E / 23F 核心算法。
- 不生成最终 advice。
- 不生成 trade_setup。
- 不发送 Hermes。
- 不调用大模型。
- 不请求 Binance。
- 不读取账户或持仓。
- 不实现自动交易。

## 11. material schema version

24B 将 `MATERIAL_SCHEMA_VERSION` 升级为：

```text
material_schema_v2
```

原因：

```text
24B 在 material_json 中新增 strategy_evidence 结构。
旧 material_schema_v1 material pack 不包含该结构。
如果继续使用 v1，18 的幂等检查会把旧包误判为当前版本已存在，从而跳过 v2 material pack 生成。
```

幂等键仍然包含：

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

因此已有 `material_schema_v1` 成功记录不会阻止生成 `material_schema_v2` material pack；同一个 run 已有 `material_schema_v2` 成功记录时，仍会保持 skipped / already_exists 幂等行为。
