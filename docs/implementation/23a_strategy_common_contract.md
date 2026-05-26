# 23A 策略公共协议与框架增强实现说明

## 1. 功能：策略公共协议

### 1.1 发起入口

23A 不新增独立运行入口。现有入口保持不变：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::run_strategy_signals
```

Scheduler 入口也保持第 17 阶段原链路：

```text
app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/scheduler/strategy_signal_scheduler_service.py::StrategySignalSchedulerService.run_after_collector_success
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
```

23A 不新增 scheduler job，不新增 Hermes 入口，不新增 CLI。

### 1.2 核心调用链

```text
app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/input_builder.py::StrategyInputBuilder.build_input_from_snapshot
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/strategies/*::evaluate
    -> app/strategy/common/result_validator.py::StrategyResultValidator.validate_strategy_result
    -> app/strategy/common/result_adapter.py::adapt_strategy_result_to_signal
    -> app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_results
```

主链路仍是：

```text
signal_service -> input_builder -> runner -> result_repository
```

SnapshotResolver 仍属于第 16 阶段，MarketContextSnapshot 懒生成职责未移动。

### 1.3 新增核心文件

`app/strategy/common/context_view.py`

定义 `StrategyContextView`，从 `StrategyEvaluationInput` 构造只读市场上下文视图，提供 `latest_base_close()`、`recent_base_high()`、`recent_base_low()`、`recent_base_range()` 等只读辅助方法。

本文件不请求外部接口，不读取数据库，不写入数据库，不读取 Redis，不写入 Redis，不发送 Hermes，不调用 DeepSeek，不涉及交易执行。

`app/strategy/common/result_contract.py`

定义 `StrategyResult` 三段结构：

```text
common_result
strategy_model_material_json
strategy_payload_json
```

同时定义公共结构：`StrategyCommonResult`、`StrategyKeyLevel`、`StrategyScenarioCandidate`、`StrategyRiskFlag`、`StrategyEvidenceItem`、`StrategyRole`。

公共层只理解 `common_result`。`strategy_model_material_json` 和 `strategy_payload_json` 只做 JSON、大小和 hash 校验，不解释具体策略私有字段。

`app/strategy/common/result_validator.py`

校验：

1. `contract_version`
2. `schema_version`
3. JSON 可序列化
4. payload 大小上限
5. payload hash
6. 公共字段枚举合法性
7. 角色约束
8. 公共 payload 中不得出现执行式词汇

校验器不写数据库，不发送 Hermes，不调用外部接口。

`app/strategy/common/result_adapter.py`

把 `StrategyResult` 适配为现有 `StrategySignal`，保持第 16/17 阶段兼容。旧 `StrategySignal` 也会被包装为 `legacy_compatible` 公共 payload，保证旧链路不被破坏。

`app/strategy/common/payload_tools.py`

提供 canonical JSON、payload size 和 SHA-256 hash 工具。

### 1.4 公共角色校验规则

`directional + success` 必须包含：

```text
market_bias != not_applicable
reason_codes
reason_text
scenario_candidates
```

每条 scenario 必须包含：

```text
activation_condition
invalidation_condition
risk_boundary
observation_period_bars
```

`support_resistance + success` 必须包含：

```text
key_levels
reason_text
```

`risk_control + success` 必须包含：

```text
risk_level
risk_flags
```

`placeholder` 只能是：

```text
strategy_status = not_implemented
not_trading_advice = true
```

且不得输出 key_levels、scenario_candidates、risk_flags、evidence_items。

## 2. 功能：第 16 阶段策略框架兼容升级

### 2.1 修改文件

`app/strategy/base.py`

`BaseStrategy.evaluate()` 支持返回 `StrategyResult`，并保留旧 `StrategySignal` 兼容。

`app/strategy/runner.py`

策略执行后统一调用：

```text
app/strategy/common/result_adapter.py::adapt_strategy_output_to_signal
```

如果策略返回的 `StrategyResult` 校验失败，runner 不会中断整批策略，而是生成 `strategy_status=invalid` 的 `StrategySignal`，并把错误写入 `validation_errors_json`。

策略异常仍按第 16 阶段原规则隔离，不影响其他策略。

`app/strategy/types.py`

在 `StrategySignal` 上新增兼容字段：

```text
contract_version
strategy_role
common_payload_json
strategy_model_material_json
strategy_payload_json
common_payload_hash
validation_status
validation_errors_json
```

旧字段 `direction_bias`、`risk_level`、`signal_strength`、`reason_codes`、`reason_text`、`metrics`、`debug_info` 保持可用。

`app/strategy/result_repository.py`

写入 `strategy_signal_result` 时同时保存旧字段和 23A 新字段。repository 不提交事务，仍由 service 控制 commit/rollback。

### 2.2 占位策略迁移

以下文件改为返回 `StrategyResult`：

```text
app/strategy/strategies/trend_structure_strategy.py
app/strategy/strategies/volatility_risk_strategy.py
app/strategy/strategies/gann_placeholder_strategy.py
```

迁移只做协议包装：

1. trend 仍是原来的简易结构观察，不升级为正式趋势策略。
2. volatility 仍是原来的简易波动风险观察，不升级为完整风控策略。
3. gann 仍是 `not_implemented`，不伪造江恩分析。

## 3. 功能：数据库字段

### 3.1 Migration

新增 migration：

```text
migrations/versions/20260601_23a_strategy_common_contract.py
```

`revision = 20260601_23a`

`down_revision = 20260531_22b`

### 3.2 修改表

只修改：

```text
strategy_signal_result
```

新增 nullable 字段：

```text
contract_version
strategy_role
common_payload_json
strategy_model_material_json
strategy_payload_json
common_payload_hash
validation_status
validation_errors_json
```

不新增业务数据，不修改 K 线表，不修改 MarketContextSnapshot 表，不修改 15-22 阶段已有表结构语义。

### 3.3 ORM

更新：

```text
app/storage/mysql/models/strategy_signal.py
```

新增字段全部 nullable，兼容历史 `strategy_signal_result` 行。

## 4. 功能：第 18 阶段读取适配

修改：

```text
app/strategy/aggregation/rules.py
```

读取策略结果时：

```text
优先 common_payload_json
    -> market_bias / risk_level / signal_strength / reason_codes / reason_text
否则回退旧字段
    -> direction_bias / risk_level / signal_strength / reason_codes_json / reason_text / metrics_json
```

`strategy_payload_json` 只进入 `strategy_private_payload_summary`，仅保留是否存在、顶层 key 和不参与公共聚合的标记，不解释具体策略私有字段。

第 18 阶段不重新运行策略，不重新生成 snapshot，不调用大模型，不生成最终建议。

## 5. 配置

更新：

```text
configs/strategies/trend_structure_strategy.yaml
configs/strategies/volatility_risk_strategy.yaml
configs/strategies/gann_placeholder_strategy.yaml
```

新增：

```yaml
contract_version: strategy_result_contract_v1
strategy_role: ...
```

配置中不包含密钥，不包含执行指令。

## 6. 数据流

```text
MarketContextSnapshot restore rows
    -> StrategyEvaluationInput
    -> StrategyContextView / strategy evaluate
    -> StrategyResult
    -> StrategyResultValidator
    -> StrategyResultAdapter
    -> StrategySignal
    -> StrategySignalResultRepository
    -> strategy_signal_result
```

唯一键规则不变。23A 不改变 `strategy_signal_run` / `strategy_signal_result` 既有幂等关系。

失败处理：

1. 单个策略抛异常：runner 隔离为 failed signal。
2. `StrategyResult` 校验失败：runner 适配为 invalid signal，记录 `validation_errors_json`。
3. repository 写库失败：第 16 service rollback，并返回 failed。
4. dry-run：不调用 repository 写入。
5. confirm-write：写入 `strategy_signal_run` 和 `strategy_signal_result`。

## 7. Hermes / 外部接口 / Redis

23A 不新增 Hermes 通知。

本功能不请求外部接口。

本功能不读取 Redis。

本功能不写入 Redis。

本功能不调用 DeepSeek、OpenAI、Claude 或其他大模型。

本功能不读取 Binance REST / WebSocket。

本功能不读取账户、订单、持仓、私钥或交易所私有接口。

本功能不自动交易。

## 8. 测试

新增与更新测试：

```text
tests/strategy/test_strategy_common_contract.py
tests/strategy/test_strategy_signal_framework.py
tests/strategy_aggregation/test_strategy_result_contract_adapter.py
```

覆盖：

1. `StrategyContextView` 只读视图。
2. `StrategyResult` 三段结构。
3. role-specific validator。
4. 私有 payload 不被公共层解释。
5. runner 将校验失败结果转为 invalid signal。
6. repository 写入 23A 字段。
7. 第 18 阶段优先读取 `common_payload_json`。
8. 第 18 阶段兼容旧字段。
9. 原第 16/17 链路测试继续通过。

默认 pytest 不请求真实 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。

## 9. 人工验收命令

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/scheduler -q
python -m pytest tests -q
```

可选迁移验收由用户在目标数据库环境人工执行：

```bash
python -m alembic upgrade head
python -m alembic current -v
```

CLI help 检查：

```bash
python -m scripts.run_strategy_signals --help
```

dry-run 检查：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

正式写入仍需用户显式确认：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

## 10. 本功能明确不负责

1. 不开发具体真实策略。
2. 不开发支撑压力策略。
3. 不开发趋势策略升级。
4. 不开发江恩策略。
5. 不开发风控策略升级。
6. 不修改第 15 快照懒生成职责。
7. 不改变第 17 scheduler 幂等规则。
8. 不发 Hermes。
9. 不调用大模型。
10. 不生成最终 advice。
11. 不修改 strategy_advice 生命周期。
12. 不自动交易。
13. 不读取交易所账户或持仓。
14. 不修改 K 线表。

## 11. 危险关键词说明

23A 代码中保留少量执行式关键词常量，仅用于
`app/strategy/common/result_validator.py` 拒绝策略公共 payload 输出执行式语言。

这些常量不实现任何交易能力，不连接交易接口，不产生订单，不读取账户，不自动执行。
