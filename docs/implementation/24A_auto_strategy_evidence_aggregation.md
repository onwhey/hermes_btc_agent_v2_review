# 24A 自动策略证据聚合实现说明

## 1. 功能：16 策略运行后自动触发 23F

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

调度侧若调用 16 app service，可传 `trigger_source=scheduler`。本阶段没有新增 scheduler job。

### 1.2 入口文件

`scripts/run_strategy_signals.py`

入口方法：

`main()`

脚本只解析 CLI 参数、创建 `StrategySignalRunRequest`、打开 MySQL session 并调用 app service。脚本不实现 23F 聚合逻辑，不直接发送 Hermes，不读写 K 线表，不请求 Binance，不调用大模型，不涉及自动交易。

### 1.3 核心调用链路

```text
scripts/run_strategy_signals.py::main
    ↓
app/strategy/signal_service.py::run_strategy_signals
    ↓
app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    ↓
app/strategy/runner.py::StrategyRunner.run_strategies
    ↓
app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_run_with_results
    ↓
MySQL commit strategy_signal_run / strategy_signal_result
    ↓
app/strategy/auto_evidence_aggregation.py::StrategyEvidenceAggregationAutoHook.maybe_run_after_strategy_signal_persistence
    ↓
app/strategy/aggregation/evidence_service.py::StrategyEvidenceAggregationService.run_strategy_evidence_aggregation
    ↓
app/strategy/aggregation/evidence_repository.py::StrategyEvidenceAggregationRepository.upsert_aggregation_result
```

23F 仍位于 `app/strategy/aggregation/`，不是普通 strategy，不注册到 `configs/strategies/strategy_registry.yaml`，也不放入 `app/strategy/strategies/`。

## 2. 配置

新增配置：

```text
STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false
```

实现位置：

- `app/core/constants.py`
- `app/core/config.py`
- `.env.example`

默认值为 `false`。关闭时，16 只运行并写入策略信号，不自动触发 23F。开启时，仅在 `confirm_write=True` 且非 dry-run，并且策略运行状态为 `success` 或 `partial_success` 后触发 23F。

## 3. dry-run / confirm-write 行为

### 3.1 dry-run

dry-run 不写 `strategy_signal_result`，不写 `strategy_evidence_aggregation_result`，不发送 23F 失败 Hermes 告警。

CLI 输出会包含：

```text
strategy_evidence_aggregation_status=skipped
strategy_evidence_aggregation_database_written=false
```

### 3.2 confirm-write

confirm-write 先写入并提交：

- `strategy_signal_run`
- `strategy_signal_result`

提交成功后，如果 `STRATEGY_EVIDENCE_AGGREGATION_ENABLED=true`，再调用 23F service 写入或更新：

- `strategy_evidence_aggregation_result`

23F 的幂等能力仍由 `StrategyEvidenceAggregationRepository.upsert_aggregation_result()` 通过 `strategy_signal_run_id` 级别唯一结果维护；重复触发会更新同一条聚合结果，不插入多条有效结果。

## 4. 数据库读写

本功能读取：

- `market_context_snapshot` 及快照引用的 K 线窗口：由 16 既有 input builder 读取。
- `strategy_signal_run` / `strategy_signal_result`：由 23F repository 读取本轮公开策略结果。

本功能写入：

- `strategy_signal_run`
- `strategy_signal_result`
- `strategy_evidence_aggregation_result`
- `alert_message`：仅在自动 23F 失败时记录固定模板告警。

本功能不写入：

- `market_kline_4h`
- `market_kline_1d`
- 订单、账户、持仓、杠杆、保证金相关表。

本阶段未新增 migration。

## 5. Hermes 告警

自动 23F 失败时由：

`app/strategy/auto_evidence_aggregation.py::StrategyEvidenceAggregationAutoHook._send_failure_alert_and_commit`

调用：

`app/alerting/service.py::send_alert`

使用固定模板：

`AlertType.STRATEGY_EVIDENCE_AGGREGATION_FAILED`

告警包含：

- `strategy_signal_run_id`
- `symbol`
- `base_interval`
- `higher_interval`
- `trigger_source`
- `error_code`
- `error_message`
- `trace_id`
- `manual_rerun_available`
- `manual_rerun_command`
- `not_trading_advice=true`

该告警不调用 DeepSeek / OpenAI / Claude 等大模型，不生成最终交易建议，不触发自动交易。真实发送仍受全局 Hermes 配置控制；无论发送是否成功，strategy signal 已提交结果不会被回滚。

## 6. 异常处理

### 6.1 16 写库失败

异常发生在：

`app/strategy/signal_service.py::StrategySignalService._persist_strategy_run_result_if_requested`

处理方式：

- rollback 当前事务。
- 返回 `StrategyRunStatus.FAILED`。
- 不触发 23F。
- 不发送 24A 自动 23F 失败告警，因为 strategy signal 本身没有成功落库。

### 6.2 23F 自动聚合失败

异常发生在：

`app/strategy/auto_evidence_aggregation.py::StrategyEvidenceAggregationAutoHook.maybe_run_after_strategy_signal_persistence`

处理方式：

- 不回滚已提交的 `strategy_signal_run` / `strategy_signal_result`。
- 记录 logger error。
- 通过固定模板创建 `alert_message` 并尝试提交 Hermes。
- 在 `StrategySignalRunResult.details["strategy_evidence_aggregation"]` 中记录失败状态、错误码、错误信息和手动补跑命令。

### 6.3 Hermes 告警失败

异常发生在：

`app/strategy/auto_evidence_aggregation.py::StrategyEvidenceAggregationAutoHook._send_failure_alert_and_commit`

处理方式：

- 捕获异常并记录 logger error。
- 不改写已落库的策略信号结果。
- 在返回 details 中记录 `alert_status=failed` 和 `alert_error_message`。

## 7. 手动补跑

24A 保留 23F 手动补跑入口：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

脚本入口：

`scripts/run_strategy_evidence_aggregation.py::main`

核心 service：

`app/strategy/aggregation/evidence_service.py::StrategyEvidenceAggregationService.run_strategy_evidence_aggregation`

## 8. 本功能不负责

- 不做 24B。
- 不修改 18 material pack 主逻辑。
- 不开发新策略。
- 不修改 23B / 23C / 23D / 23E 核心算法。
- 不把 23F 注册成普通 strategy。
- 不调用大模型。
- 不生成最终 advice。
- 不生成 trade_setup。
- 不发送最终策略建议。
- 不请求 Binance。
- 不读取账户或持仓。
- 不实现自动交易。

## 9. 测试

对应测试：

- `tests/strategy/test_strategy_signal_framework.py`
- `tests/strategy_aggregation/test_23f_strategy_evidence_aggregation.py`
- `tests/test_alerting.py`

覆盖范围：

- 开关默认关闭。
- 开关关闭时不自动调用 23F。
- 开关开启时 confirm-write 后调用 23F。
- dry-run 不写 23F。
- 23F 失败不回滚 strategy signal 已写入结果。
- 23F 失败生成固定模板告警。
- 23F 不进入普通 strategy registry。
- 23F 仍只读取公开 common payload，不读取私有 payload。
- alerting 固定模板可渲染，不调用大模型。

默认 pytest 不请求 Binance，不连接真实 MySQL，不连接真实 Redis，不发送真实 Hermes，不调用大模型，不访问交易接口。
