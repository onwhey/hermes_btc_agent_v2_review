# 25A Strategy Pipeline Orchestration Implementation

## 1. 功能：手动统一策略 pipeline

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_strategy_pipeline \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --confirm-write
```

本阶段不接管 scheduler runner，也不把 25A 注册为自动任务。

### 1.2 入口文件

`scripts/run_strategy_pipeline.py`

入口方法：

`main()`

脚本只解析 CLI 参数、读取配置、打开 MySQL session、调用 app service。
脚本不请求 Binance，不写 Redis，不直接发送 Hermes，不调用大模型，不修改 K 线表，不执行交易。

### 1.3 核心 service

`app/strategy_pipeline/service.py`

核心方法：

`StrategyPipelineService.run_strategy_pipeline()`

### 1.4 调用链路

```text
scripts/run_strategy_pipeline.py::main
    ↓
app/strategy_pipeline/service.py::run_strategy_pipeline
    ↓
app/scheduler/strategy_signal_scheduler_service.py::run_after_collector_success
    ↓
app/strategy/signal_service.py::run_strategy_signals
    ↓
app/strategy/aggregation/repository.py::get_latest_strategy_evidence_aggregation
    ↓
app/strategy/aggregation/service.py::run_strategy_aggregation
    ↓
app/model_review_chain/worker.py::run_model_review_chain_worker
    ↓
app/model_review_aggregation/service.py::run_model_review_aggregation
    ↓
app/strategy_advice/scheduler_service.py::run_strategy_advice_scheduler
```

实际调度顺序：

```text
25A manual pipeline
→ 17
→ 16
→ 15 snapshot resolver/lazy snapshot path inside 16
→ 23B/C/D/E strategy registry execution inside 16
→ 24A/23F evidence aggregation verification
→ 18 material pack
→ 20C/19/20A model review chain and aggregation
→ 21A/21B advice lifecycle and notification
```

## 2. 配置

新增配置：

- `STRATEGY_PIPELINE_ENABLED=false`
- `STRATEGY_PIPELINE_AUTO_RUN_ENABLED=false`
- `STRATEGY_PIPELINE_REAL_MODEL_ENABLED=false`
- `STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=false`
- `STRATEGY_PIPELINE_LOCK_TTL_SECONDS=1800`

25A 只使用手动入口。`STRATEGY_PIPELINE_AUTO_RUN_ENABLED` 预留给后续阶段，本阶段不接入 scheduler。

真实模型调用必须同时满足：

- 下游模型配置允许；
- `STRATEGY_PIPELINE_REAL_MODEL_ENABLED=true`；
- CLI 传入 `--use-real-model --confirm-real-model-cost`。

真实 Hermes 发送必须同时满足：

- 下游 21B 通知配置允许；
- `STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=true`；
- CLI 传入 `--send-real-hermes`。

## 3. Kline Slot 解析

25A 接收或推导 `kline_slot_utc`：

- CLI 传入 `--kline-slot-utc` 时，按 UTC 解释，作为 base 4h K 线 open time。
- 未传入时，`app/strategy_pipeline/repository.py::resolve_latest_base_kline_slot_utc()` 只从 `market_kline_4h` 查询当前 symbol / interval 最新正式 K 线 open time。
- 无法推导时返回 blocked，要求用户显式传入 `--kline-slot-utc`。

本功能不请求 Binance，不生成 K 线，不修改正式 K 线表。

## 4. Redis 锁

确认写入模式会获取 pipeline 级 Redis 锁：

```text
strategy_pipeline:{symbol}:{base_interval}:{higher_interval}:{kline_slot_utc}
```

示例：

```text
strategy_pipeline:BTCUSDT:4h:1d:2026-05-30T04:00:00Z
```

实现文件：

`app/strategy_pipeline/locks.py`

Redis 锁只防止同一根 K 线并发跑多个 pipeline，不参与交易执行。

## 5. MySQL 写入

新增表：

`strategy_pipeline_event_log`

迁移文件：

`migrations/versions/20260603_25a_strategy_pipeline_event_log.py`

ORM：

`app/storage/mysql/models/strategy_pipeline.py`

写入内容是 pipeline 运行摘要和阶段 ID，包括：

- `pipeline_run_id`
- `strategy_signal_run_id`
- `strategy_evidence_aggregation_id`
- `material_pack_id`
- `model_analysis_run_id`
- `review_aggregation_run_id`
- `advice_id`
- `review_id`
- `notification_status`
- `model_review_invoked`
- `model_review_reused`
- `real_model_called`
- `hermes_real_sent`
- `error_code`
- `error_message`

表中不保存完整 material_json、完整 prompt、完整模型输出、完整策略 debug 或 K 线窗口。

## 6. 异常处理

- 参数非法或 pipeline 开关关闭：blocked，不调用下游服务。
- Kline slot 无法确定：blocked，不猜测目标 K 线。
- Redis 锁冲突：skipped，不运行下游阶段。
- 17/16 未成功产生 `strategy_signal_run_id`：blocked。
- 24A/23F 无聚合结果：blocked，不让 18 伪装证据完整。
- 18 无 `material_pack_id`：blocked。
- 20C/20A 无可用模型审查聚合：blocked。
- 21A/21B 失败：failed，但不回滚前序已提交的策略、材料包或模型审查结果。

## 7. Hermes / 大模型 / 交易边界

本功能不直接发送 Hermes。

21B 是否真实发送由下游配置、pipeline 配置和 CLI 三重确认决定。

本功能不直接调用 DeepSeek/OpenAI/Claude。

20C/19 是否真实调用模型由下游配置、pipeline 配置、CLI 成本确认三重确认决定。

本功能不读取账户，不读取持仓，不封装 Binance 私有接口，不下单，不平仓，不撤单，不自动交易。

## 8. 测试

对应测试：

`tests/strategy_pipeline/test_strategy_pipeline_service.py`

覆盖：

- dry-run 不写库、不加锁；
- confirm-write 按 17 → 23F → 18 → 20C → 20A → 21C 顺序调用；
- kline slot 无法确定时 blocked；
- Redis 锁冲突时 skipped；
- pipeline 开关未允许时不会绕过真实模型 / Hermes 门禁；
- pipeline 结果保留非交易边界字段。

默认测试不请求 Binance，不连接真实 Redis，不发送 Hermes，不调用真实大模型，不访问交易接口。

## 9. 2026-05-31 补充修复：显式编排 24A/23F 与真实模型调用标记

本次修复后，25A 在 17/16 成功返回 `strategy_signal_run_id` 后，不再只检查 23F 是否存在，而是调用
`app/strategy_pipeline/evidence_stage.py::run_or_reuse_stage23f_for_pipeline`。

该阶段的实际规则：

- 如果已存在可用 `strategy_evidence_aggregation_result`，25A 复用其 `aggregation_id`，不重复创建。
- 如果不存在，25A 显式调用 `app/strategy/aggregation/evidence_service.py::run_strategy_evidence_aggregation` 创建 23F 聚合结果。
- 如果 `STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false`，25A 返回 blocked，且不会继续跑 18 material pack。
- 如果 23F 返回 failed / blocked 或没有持久化可用结果，25A 停止在 24A/23F 阶段并写入 pipeline event log。

修复后的调度顺序：

```text
25A CLI
→ 17
→ 16
→ 15 snapshot resolver / lazy snapshot path inside 16
→ 23B/C/D/E strategy execution inside 16
→ 25A explicit 24A/23F lookup-or-create
→ 18 material pack
→ 20C/19/20A model review chain and aggregation
→ 21A/21B advice lifecycle and notification
```

`model_review_invoked` 与 `real_model_called` 的语义也已拆开：

- `model_review_invoked=true` 只表示 20C 模型审查链路被触发。
- `real_model_called=true` 只表示本轮 pipeline 新调用了 DeepSeek / OpenAI / Claude 等真实外部 provider。
- mock_review、dry-run、复用已有模型审查结果，都必须保持 `real_model_called=false`。

本次仍未修改 scheduler runner 自动接管逻辑；25A 仍然只是手动统一入口。

## 10. 2026-05-31 补充修复：Stage-17 duplicate skipped 后复用已有 SSR

本次修复后，25A 在调用 `app/scheduler/strategy_signal_scheduler_service.py::run_after_collector_success`
返回 `skipped` 时，不再直接终止。25A 会通过
`app/strategy_pipeline/stage17_reuse.py::resolve_stage17_result_or_reusable_duplicate`
读取 `strategy_signal_scheduler_event_log` 中同一
`symbol / base_interval / higher_interval / target_base_open_time_utc`
下最新的 `success` 或 `partial_success` 记录。

复用规则：

- 只复用 `status in (success, partial_success)` 且 `run_id` 非空的旧 Stage-17 事件。
- `blocked / failed / skipped / running / waiting_upstream` 不能贡献可复用 SSR。
- `run_id` 为空的旧成功事件不能复用。
- 找到可复用事件时，25A 设置 `state.strategy_signal_run_id`，并继续显式调用或复用 24A/23F。
- 复用时不删除旧 event，不重跑 16，不重复创建 `strategy_signal_run`。
- pipeline event log 的 `details_json` 会记录：
  - `reused_stage17_duplicate=true`
  - `reused_strategy_signal_run_id`
  - `reused_stage17_event_id`

相关查询由 `app/strategy_pipeline/repository.py::get_latest_reusable_stage17_scheduler_event`
完成。该查询只读 MySQL，不写库、不提交事务、不请求外部接口、不发送 Hermes、不调用大模型、不涉及交易执行。

## 11. 2026-05-31 补充修复：显式重试 failed/blocked Stage-17 事件

本次新增手动 CLI 参数：

```bash
python -m scripts.run_strategy_pipeline \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --confirm-write \
  --retry-failed-stage17
```

默认行为不变：未传 `--retry-failed-stage17` 时，Stage-17 返回 duplicate skipped 且没有可复用
`success / partial_success + run_id` 的 SSR，25A 仍返回 blocked，不继续 23F、18、20、21。

显式重试规则：

- 25A 先查询同一 `symbol / base_interval / higher_interval / kline_slot_utc` 下是否已有可复用
  `success / partial_success + run_id`。如果存在，必须复用旧 SSR，不会重跑 16。
- 如果没有可复用 SSR，25A 仍先调用
  `app/scheduler/strategy_signal_scheduler_service.py::run_after_collector_success`，让 Stage-17 返回
  duplicate skipped，并记录本轮 stage17_result。
- 只有用户显式传入 `--retry-failed-stage17`，且历史最新 Stage-17 事件为 `failed` 或 `blocked`，
  且该事件没有 `run_id`，25A 才调用现有
  `app/strategy/signal_service.py::StrategySignalService.run_strategy_signals` 重新生成新的
  `strategy_signal_run_id`。
- 旧 Stage-17 event 不会被删除、修改或覆盖；新 SSR 只通过 Stage-16 正常落库生成。
- 重试成功后，25A 继续显式调用或复用 24A/23F，然后再进入 18、20C/19/20A、21A/21B。
- 重试失败时，25A 停止在 `17_16_strategy_signals` 阶段并写 pipeline event log，不会绕过 23F 进入 18。

禁止重试的情况：

- 已存在 `success / partial_success + run_id`：必须复用旧 SSR。
- 历史 Stage-17 事件仍为 `running` 或 `waiting_upstream`。
- 历史 Stage-17 事件不是 `failed / blocked`，或仍带有 `run_id`。
- pipeline Redis 锁未获取。
- `kline_slot_utc` 无法唯一确定。

pipeline event log 的 `details_json` 会记录：

- `retry_failed_stage17=true`
- `retry_reason`
- `previous_stage17_event_id`
- `previous_stage17_status`
- `previous_stage17_error_code`
- `new_strategy_signal_run_id`

本次没有修改 scheduler runner 自动链路；25A 仍是手动统一入口。该重试能力不请求 Binance、不读取账户或持仓、
不调用大模型、不发送 Hermes、不生成 advice、不涉及自动交易。
