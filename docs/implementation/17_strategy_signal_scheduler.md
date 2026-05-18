# 17 Strategy Signal Scheduler 实现说明

## 1. 功能定位

第 17 阶段实现的是 scheduler 编排层：4h 增量采集成功后，scheduler 触发第 16 阶段 `StrategySignalService` 生成独立策略信号，并把调度过程记录到 `strategy_signal_scheduler_event_log`。

本阶段不实现最终交易建议，不做策略聚合，不调用 DeepSeek/GPT/Claude 或任何大模型，不读取账户、订单、持仓或私钥，不自动交易，不修改 `market_kline_4h` / `market_kline_1d`，不请求 Binance REST / WebSocket。

## 2. 发起入口

入口文件：

`app/scheduler/runner.py`

入口方法：

`SchedulerRunner._run_strategy_signal_post_collect_if_needed()`

触发条件：

1. 上游 job 是 `kline_4h_incremental` 或 `kline_1d_incremental`。
2. 上游 collector 返回 `status=success`。
3. `STRATEGY_SIGNAL_SCHEDULER_ENABLED=true` 时，runner 调用第 17 后置 job。

第 17 不新增独立固定时间策略任务，不通过 `scripts/run_strategy_signals.py` 调用，也不把策略逻辑写入 collector service。

## 3. 核心调用链

```text
app/scheduler/runner.py::SchedulerRunner.run_once
    -> app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/scheduler/strategy_signal_scheduler_service.py::run_strategy_signal_scheduler_after_collect
    -> app/scheduler/strategy_signal_scheduler_service.py::StrategySignalSchedulerService.run_after_collector_success
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/snapshot_resolver.py::SnapshotResolver.ensure_latest_snapshot
    -> app/strategy/input_builder.py::StrategyInputBuilder.build_input_from_snapshot
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_run_with_results
```

UTC 00:00 close boundary:

```text
4h collector success
    -> 写入 strategy_signal_scheduler_event_log status=waiting_upstream
1d collector success
    -> 将同一事件更新为 running
    -> 调用 StrategySignalService
```

## 4. 第 17 如何调用第 16

`StrategySignalSchedulerService` 只构造并传入 `StrategySignalRunRequest`：

```text
trigger_source="scheduler"
ensure_latest_snapshot=True
dry_run=False
confirm_write=True
created_by="strategy_signal_scheduler"
```

第 17 不直接调用第 15 阶段 MarketContextSnapshot service。快照复用、懒生成、blocked / failed 门禁全部由第 16 阶段 `SnapshotResolver` 负责。

## 5. 幂等与目标 K线

第 17 只处理上游 collector 所属调度 slot 对应的 4h K线。目标不再由第 17 运行时的当前 UTC 时间推算，而是优先使用上游 collector result 中明确的 latest written/closed 4h `open_time_ms`；如果 result 没有该字段，则使用 `upstream_slot_time_utc` 计算：

```text
4h collector:
target_base_close_time_utc = upstream_slot_time_utc - KLINE_4H_INCREMENTAL_COLLECT_UTC_MINUTES_AFTER_CLOSE
target_base_open_time_utc = target_base_close_time_utc - 4 hours

1d collector:
target_base_close_time_utc = upstream_slot_time_utc 所属 UTC 日期的 00:00
target_base_open_time_utc = target_base_close_time_utc - 4 hours

target_higher_open_time_utc = target_base_close_time_utc - 1 day
```

`current_time_utc` 仅表示第 17 本次服务实际运行时间，用于审计和传给第 16 阶段的运行上下文，不再用于绑定 `target_base_open_time_ms`。因此 scheduler 延迟、重启补跑或 catch-up 时，事件仍绑定到上游 collector 的调度 slot / 实际目标 K线，不会漂移到运行当刻推算出的最新 K线。

唯一键：

```text
uk_strategy_signal_scheduler_target
(symbol, base_interval, higher_interval, target_base_open_time_ms)
```

已有 `running` / `success` / `partial_success` / `blocked` / `failed` / `skipped` 记录时，不创建第二条调度记录，只在原记录上更新 `skip_count`、`last_skipped_at_utc`、`last_skip_reason`。

第 17 不做历史多根补跑，不补发历史 Hermes。

## 6. 新增表

迁移文件：

`migrations/versions/20260518_17_create_strategy_signal_scheduler_event_log.py`

ORM：

`app/storage/mysql/models/strategy_signal_scheduler_event.py`

新增表：

`strategy_signal_scheduler_event_log`

主要字段：

`event_id`、`symbol`、`base_interval`、`higher_interval`、`target_base_open_time_ms`、`target_base_open_time_utc`、`target_base_close_time_ms`、`target_base_close_time_utc`、`target_higher_open_time_ms`、`status`、`trigger_source`、`trigger_reason`、`run_id`、`snapshot_id`、`upstream_4h_collector_event_id`、`upstream_1d_collector_event_id`、`strategy_count`、`success_count`、`failed_count`、`invalid_count`、`not_implemented_count`、`message`、`error_code`、`error_message`、`trace_id`、`hermes_enabled`、`hermes_status`、`hermes_message`、`hermes_error`、`skip_count`。

## 7. 状态说明

`waiting_upstream`：UTC 00:00 close boundary 已完成 4h 采集，等待 1d 采集成功。

`running`：调度事件已写入，准备或正在调用第 16 阶段。

`success`：第 16 返回 success。

`partial_success`：第 16 返回 partial_success，视为正常可接受结果，不当成系统失败。

`blocked`：第 15/16 阶段快照、数据质量或输入条件阻断。

`failed`：第 17 编排或第 16 调用出现异常。

`skipped`：配置关闭、重复触发或不满足调度时机。

## 8. Hermes 行为

配置项：

```text
STRATEGY_SIGNAL_HERMES_ENABLED
STRATEGY_SIGNAL_HERMES_NOTIFY_SUCCESS
STRATEGY_SIGNAL_HERMES_NOTIFY_PARTIAL_SUCCESS
STRATEGY_SIGNAL_HERMES_NOTIFY_BLOCKED
STRATEGY_SIGNAL_HERMES_NOTIFY_FAILED
STRATEGY_SIGNAL_HERMES_NOTIFY_SKIPPED
```

`skipped` 默认不发送。`waiting_upstream` 不发送。每个 4h 周期最多发送一条第 17 摘要。

Hermes 通过 `app/alerting` 固定模板发送，发送结果写回 `strategy_signal_scheduler_event_log.hermes_status`：

`disabled`、`not_required`、`sent`、`failed`

通知正文明确包含：

1. 这是独立策略信号。
2. 不是最终交易建议。
3. 未调用大模型。
4. 系统未自动交易。

Hermes 失败只更新 `hermes_status=failed` 和 `hermes_error`，不把策略运行结果改成 failed。

## 9. 配置

新增配置读取在：

`app/core/constants.py`

`app/core/config.py`

`app/scheduler/config.py`

`.env.example` 已新增：

```text
STRATEGY_SIGNAL_SCHEDULER_ENABLED=false
STRATEGY_SIGNAL_SYMBOLS=BTCUSDT
STRATEGY_SIGNAL_BASE_INTERVAL=4h
STRATEGY_SIGNAL_HIGHER_INTERVAL=1d
STRATEGY_SIGNAL_SCHEDULER_RUNNING_TIMEOUT_SECONDS=900
STRATEGY_SIGNAL_HERMES_ENABLED=false
STRATEGY_SIGNAL_HERMES_NOTIFY_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_BLOCKED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_FAILED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_SKIPPED=false
```

默认关闭，避免部署后自动写策略信号或自动发送 Hermes。

## 10. 异常处理

1. 上游不是 4h / 1d collector success：返回 skipped，不调用第 16。
2. UTC 00:00 时 4h 成功：写 `waiting_upstream`，不调用第 16。
3. UTC 00:00 时 1d 成功且已有 waiting 事件：更新为 running，再调用第 16。
4. 第 16 返回 blocked / failed：写入同名状态和原因。
5. 第 16 抛异常：写入 `failed`，`error_code=strategy_signal_service_exception`。
6. Hermes 抛异常或返回失败：只记录 Hermes 失败，不修改策略状态。

## 11. 测试

新增测试：

`tests/scheduler/test_strategy_signal_scheduler.py`

覆盖：

1. 4h 采集成功后触发 StrategySignalService。
2. UTC 00:00 等待 1d 采集完成。
3. scheduler 不调用策略 CLI，不直接调用第 15。
4. 同一 target 4h K线不重复创建事件。
5. running / success / partial_success / blocked / failed / skipped 状态。
6. partial_success 不视为失败。
7. Hermes 开关关闭不发送，开启发送并记录结果。
8. skipped 默认不发送 Hermes。
9. 第 17 只处理最近一根理论已收盘 4h K线。

运行：

```bash
python -m pytest tests/scheduler tests/strategy
python -m scripts.check_project_invariants
```

可选：

```bash
python -m pytest
python -m alembic upgrade head
```
