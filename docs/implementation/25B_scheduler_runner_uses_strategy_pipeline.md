# 25B Scheduler Runner 接入策略 Pipeline 实现说明

## 1. 功能：09 成功后自动触发 25 Pipeline

### 1.1 发起方式

自动触发入口：

```text
09 4h K线增量采集成功
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_pipeline_post_collect_if_needed
```

手动 pipeline CLI 仍保留：

```bash
python -m scripts.run_strategy_pipeline --symbol BTCUSDT --base-interval 4h --higher-interval 1d --trigger-source cli --confirm-write
```

### 1.2 配置开关

新增配置：

```text
STRATEGY_PIPELINE_SCHEDULER_ENABLED=false
```

默认值为 `false`。关闭时，scheduler runner 不自动触发 25 pipeline，继续保持原有后置链路行为。开启时，09 的 4h K线采集成功后，runner 只触发 25 pipeline，不再直接触发旧 17/18/20/21 分散链路。

自动模式不会默认真实调用大模型，也不会默认真实发送 Hermes：

- `app/scheduler/jobs/strategy_pipeline_job.py::run_strategy_pipeline_after_collect_job` 构造请求时固定 `use_real_model=false`。
- 固定 `confirm_real_model_cost=false`。
- 固定 `send_real_hermes=false`。
- 下游仍受 20/21/25 既有开关约束。

### 1.3 核心调用链路

```text
app/scheduler/runner.py::SchedulerRunner._acquire_slot_and_run_job
    -> app/scheduler/runner.py::SchedulerRunner._run_post_collect_chain_if_needed
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_pipeline_post_collect_if_needed
    -> app/scheduler/jobs/strategy_pipeline_job.py::run_strategy_pipeline_after_collect_job
    -> app/strategy_pipeline/service.py::run_strategy_pipeline
    -> app/strategy_pipeline/service.py::StrategyPipelineService.run_strategy_pipeline
    -> 17/16
    -> 24A/23F
    -> 18
    -> 20C/19/20A
    -> 21A/21B
```

## 2. kline_slot_utc 来源

25B 不允许 pipeline 自己猜 slot。

runner 只从 09 collector 成功结果中读取明确字段：

```text
latest_written_open_time_ms
latest_closed_open_time_ms
latest_base_open_time_ms
actual_end_open_time_ms
end_open_time_ms
```

当前 4h collector result 会在 `details` 中带出：

```text
actual_start_open_time_ms
actual_end_open_time_ms
```

其中 `actual_end_open_time_ms` 被转换为 UTC aware datetime 后传给 25 pipeline。若无法取得明确 slot，runner 记录 `strategy_pipeline.status=blocked` 和 `error_code=pipeline_kline_slot_missing`，不触发 pipeline，也不回退触发旧 17。

## 3. 旧 17 自动链路旁路规则

当 `STRATEGY_PIPELINE_SCHEDULER_ENABLED=true`：

```text
09 success -> runner -> 25 pipeline
```

runner 不再直接调用：

```text
stage-17 strategy signal scheduler
stage-18 strategy aggregation
20C model review worker
21C advice scheduler
```

如果旧 `STRATEGY_SIGNAL_SCHEDULER_ENABLED=true` 同时开启，runner 会在本轮 details 中记录：

```text
old_stage17_auto_trigger_skipped_due_to_pipeline_enabled=true
```

旧 CLI 和 service 不删除；它们仍作为 25 内部复用能力或人工排查入口。

## 4. 数据库与 Redis

本阶段不新增数据库 migration。

runner 仍写 Redis scheduler slot marker。25 pipeline 内部继续使用已有 pipeline Redis 锁：

```text
strategy_pipeline:{symbol}:{base_interval}:{higher_interval}:{kline_slot_utc}
```

runner 不重写 pipeline 幂等逻辑，不新建第二套锁。

25 pipeline 由既有 `strategy_pipeline_event_log` 记录每个 pipeline run。runner 在自己的 `SchedulerRunRecord.details["strategy_pipeline"]` 中保存本次触发关系和 pipeline 结果摘要。

## 5. 失败处理与告警

若 09 已成功但 25 pipeline 返回 `failed` / `blocked`，或 `partial_success` 且缺少关键 23F 聚合 ID，runner 会通过固定模板生成系统级 alert：

```text
alert_type=system_error
title=Strategy pipeline scheduler failure
```

告警 details 至少包含：

- upstream_09_success=true
- pipeline_run_id
- pipeline_status
- current_step
- error_code
- error_message
- symbol/base_interval/higher_interval
- kline_slot_utc
- trace_id
- not_trading_advice=true
- is_final_trading_advice=false
- is_trading_signal=false
- is_executable=false
- auto_trading_allowed=false

告警不调用大模型，不是交易建议，不包含自动交易动作。Hermes 是否真实发送仍由统一 alerting 配置控制；告警发送失败不回滚 09 或 pipeline 结果。

## 6. 本功能不负责

- 不修改 23B/23C/23D/23E/23F 策略算法。
- 不修改 24C prompt。
- 不修改 21 advice 生成逻辑。
- 不请求 Binance。
- 不写正式 K线表。
- 不读取账户或持仓。
- 不下单，不生成订单。
- 不自动交易。
- 不绕过模型成本确认。
- 不绕过 Hermes 发送开关。

## 7. 测试

对应测试：

```text
tests/scheduler/test_strategy_pipeline_scheduler_hook.py
```

覆盖：

- `STRATEGY_PIPELINE_SCHEDULER_ENABLED=false` 时不触发 25。
- 开启后 09 成功触发 25。
- 开启后旧 17 自动链路被旁路。
- `kline_slot_utc` 来自 09 `actual_end_open_time_ms`。
- 09 结果缺 slot 时 blocked，不猜测。
- 25 pipeline failed/blocked 时 runner 记录并生成系统告警。
- 自动模式不真实调用模型、不真实发送 Hermes。
- 非交易边界字段保持 false。

默认 pytest 不访问真实 Binance、MySQL、Redis、Hermes 或大模型。
