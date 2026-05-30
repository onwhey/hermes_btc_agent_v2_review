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

