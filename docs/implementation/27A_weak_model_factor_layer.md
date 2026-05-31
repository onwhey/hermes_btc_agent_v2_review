# 27A 弱模型 / 因子层基础设施实现说明

## 1. 功能：基于 SSR 绑定快照运行弱模型

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx --dry-run
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx --confirm-write
```

27A 第一版只支持 `strategy_signal_run_id` 输入。`--kline-slot-utc` 仅用于校验 SSR 绑定 snapshot 的 4h slot，不会触发 15 ensure snapshot，也不会自行选择新快照。

### 1.2 入口文件

`scripts/run_weak_models.py`

入口方法：

`main()`

### 1.3 核心 service

`app/weak_models/service.py`

核心方法：

`WeakModelService.run_weak_models_for_strategy_signal()`

### 1.4 调用链路

```text
scripts/run_weak_models.py::main
    ↓
app/weak_models/service.py::WeakModelService.run_weak_models_for_strategy_signal
    ↓
app/weak_models/repository.py::WeakModelRepository.get_strategy_signal_run
    ↓
app/weak_models/repository.py::WeakModelRepository.get_snapshot_by_snapshot_id
    ↓
app/weak_models/repository.py::WeakModelRepository.restore_snapshot_kline_windows
    ↓
app/weak_models/registry.py::WeakModelRegistry.load_enabled_models
    ↓
app/weak_models/models.py::<weak model>.evaluate
    ↓
app/weak_models/aggregation.py::WeakModelAggregator.aggregate
    ↓
app/weak_models/repository.py::upsert_run / upsert_result / upsert_aggregation
```

## 2. 配置

配置目录：

`configs/weak_models/`

第一版配置文件：

1. `registry.yaml`
2. `trend_strength_directional.yaml`
3. `volatility_risk_gate.yaml`
4. `support_distance_confirmation.yaml`
5. `market_regime_context.yaml`

配置读取：

`app/weak_models/config.py::load_weak_model_profiles()`

配置字段包含 `enabled`、`maturity_stage`、`model_role`、`static_weight`、`input_intervals`、`input_window`、`params` 等。`enabled=false`、`maturity_stage=disabled/deprecated` 不运行；`observe_only` 运行并落库但不进入正式聚合；`active` 才参与聚合。

本功能未新增环境变量。

## 3. 数据读取

读取数据库表：

1. `strategy_signal_run`：按 `strategy_signal_run_id` 读取 SSR 和 `snapshot_id`。
2. `market_context_snapshot`：按 SSR 绑定的 `snapshot_id` 读取 15 快照。
3. `market_kline_4h` / `market_kline_1d`：通过 15 snapshot repository 的 restore 契约，只读还原快照记录的窗口。

不读取 Redis。
不请求外部接口。
不请求 Binance REST。
不调用 DeepSeek/GPT/Claude 或其他大模型。
不发送 Hermes。
不读取账户、仓位或交易私有状态。

## 4. Snapshot 校验

校验发生在：

`app/weak_models/service.py::_validate_strategy_signal_run()`

`app/weak_models/service.py::_validate_snapshot()`

校验内容：

1. SSR 必须存在。
2. SSR 必须有 `snapshot_id`。
3. SSR 的 `symbol/base_interval_value/higher_interval_value` 必须匹配请求。
4. snapshot 必须存在。
5. snapshot `status` 必须为 `created`。
6. snapshot 的 symbol/base/higher 必须匹配 SSR。
7. 如果用户传入 `--kline-slot-utc`，必须与 snapshot `latest_4h_open_time_utc` 一致。
8. snapshot 必须能通过 15 restore 契约还原 K线窗口。

校验失败：

```text
status=blocked
error_code=invalid_or_missing_snapshot
```

27A 不会静默换快照，不会调用 15 ensure snapshot，不会修复 K线。

## 5. 四个弱模型计算逻辑

### 5.1 trend_strength_directional

文件：

`app/weak_models/models.py::TrendStrengthDirectionalModel`

逻辑：

基于 4h close 的快慢均线关系、慢均线斜率和最新价相对慢均线的位置输出方向因子。

输出：

`signal_score`、`confidence`、`direction_bias`、`effective_score`、`evidence_json`

### 5.2 volatility_risk_gate

文件：

`app/weak_models/models.py::VolatilityRiskGateModel`

逻辑：

基于 4h ATR 百分比、最近 range 扩张和最新 range 占比输出风险因子。风险极端且 `can_veto=true` 时输出 veto。

输出：

`risk_score`、`risk_level`、`can_veto`、`veto_triggered`、`trade_permission`

### 5.3 support_distance_confirmation

文件：

`app/weak_models/models.py::SupportDistanceConfirmationModel`

逻辑：

基于最近 4h 窗口的高低点估算窗口支撑/压力，判断最新价更接近支撑、压力或区间中部。

输出：

`confirmation_score`、`supports_direction`、`confidence`、`evidence_json`

### 5.4 market_regime_context

文件：

`app/weak_models/models.py::MarketRegimeContextModel`

逻辑：

基于 4h 与 1d 窗口涨跌幅、4h 窗口宽度输出背景状态。默认配置为 `observe_only`，只落库观察，不影响正式聚合。

输出：

`context_regime`、`context_score`、`confidence`、`evidence_json`

## 6. 聚合规则

聚合文件：

`app/weak_models/aggregation.py`

聚合方法：

`WeakModelAggregator.aggregate()`

只聚合 `enabled=true` 且 `maturity_stage=active` 且 `static_weight > 0` 的模型输出。`observe_only` 输出会写入 `weak_model_result`，但不进入 `weak_model_aggregation` 的正式方向和风险汇总。

方向聚合公式：

```text
directional_score =
    sum(signal_score * confidence * static_weight)
    /
    sum(confidence * static_weight)
```

风险聚合：

1. 有 active risk veto 时，`trade_permission=block`。
2. 无 veto 时取最高风险等级。
3. high 风险输出 `caution`，low/medium 输出 `allow`。

27A 聚合结果不是交易建议，不包含开仓、平仓、止损、止盈、仓位、杠杆或执行字段。

## 7. 数据写入

只有 `--confirm-write` 且非 dry-run 时写库。

### 7.1 weak_model_run

用途：记录一次 27A 批次运行。

主要字段：

`weak_model_run_id`、`pipeline_run_id`、`strategy_signal_run_id`、`snapshot_id`、`symbol`、`base_interval`、`higher_interval`、`kline_slot_utc`、`run_status`、`trigger_source`、`model_count_total`、`model_count_enabled`、`model_count_executed`、`model_count_failed`、`trace_id`、`details_json`、`created_at_utc`、`updated_at_utc`

唯一键：

`UNIQUE(weak_model_run_id)`

### 7.2 weak_model_result

用途：记录每个弱模型的单独输出。

主要字段：

`weak_model_result_id`、`weak_model_run_id`、`model_key`、`model_role`、`model_version`、`config_version`、`config_hash`、`maturity_stage`、`enabled`、`participation_mode`、`symbol`、`base_interval`、`higher_interval`、`kline_slot_utc`、`snapshot_id`、`status`、`error_code`、`error_message`、角色输出字段、`confidence`、`static_weight`、`effective_score`、`input_summary_json`、`evidence_json`、`raw_output_json`、`created_at_utc`

唯一键：

`UNIQUE(weak_model_result_id)`

`UNIQUE(weak_model_run_id, model_key)`

### 7.3 weak_model_aggregation

用途：记录 active 弱模型正式聚合摘要。

主要字段：

`weak_model_aggregation_id`、`weak_model_run_id`、`pipeline_run_id`、`strategy_signal_run_id`、`snapshot_id`、`symbol`、`base_interval`、`higher_interval`、`kline_slot_utc`、`directional_score`、`directional_bias`、`directional_confidence`、`risk_level`、`trade_permission`、`veto_triggered`、`supporting_factors_json`、`opposing_factors_json`、`conflict_factors_json`、`low_confidence_factors_json`、`context_summary_json`、`summary_text`、`details_json`、`created_at_utc`

唯一键：

`UNIQUE(weak_model_aggregation_id)`

`UNIQUE(weak_model_run_id)`

### 7.4 幂等与冲突

Repository 使用 `weak_model_run_id`、`weak_model_result_id`、`weak_model_aggregation_id` upsert。重复相同 run id 会更新同一批 27A 行；不同 trace 生成不同 run id。

## 8. CLI 行为

默认 dry-run：

```bash
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx
```

显式 dry-run：

```bash
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx --dry-run
```

写库：

```bash
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx --confirm-write
```

带 slot 校验：

```bash
python -m scripts.run_weak_models --strategy-signal-run-id SSR-xxx --kline-slot-utc 2026-05-31T04:00:00Z --dry-run
```

dry-run 不写 MySQL。
confirm-write 写入三张 27A 表。
脚本只允许 `--trigger-source cli`，不允许 scheduler 调用。

## 9. 异常处理

1. 参数错误在 `scripts/run_weak_models.py::main()` 捕获，返回 `EXIT_PARAMETER_ERROR`。
2. 配置加载错误在 `WeakModelService.run_weak_models_for_strategy_signal()` 中转为 `status=failed`。
3. SSR 或 snapshot 缺失、字段不匹配、slot 不匹配、snapshot restore 失败，返回 `status=blocked`、`error_code=invalid_or_missing_snapshot`。
4. 单个弱模型抛异常时，转换为该模型 `weak_model_result.status=failed`；其他模型继续运行，批次可为 `partial_success`。
5. 数据库写入异常由调用方 session 上下文回滚并向上抛出，CLI 显示失败。
6. Hermes 不参与本功能，因此不存在 Hermes 失败回滚问题。

## 10. 本功能不负责

1. 不实现 27B/27C/27D。
2. 不接入 25 pipeline。
3. 不修改 18 材料包逻辑。
4. 不修改 19/20 模型审查逻辑。
5. 不修改 21 建议生命周期逻辑。
6. 不接 scheduler 自动任务。
7. 不请求 Binance REST。
8. 不调用 DeepSeek/GPT/Claude。
9. 不发送 Hermes。
10. 不读取账户、仓位或交易私有状态。
11. 不生成订单。
12. 不自动交易。
13. 不做复盘胜率、盈亏比、策略评分或模型评分。

## 11. 测试

新增测试目录：

`tests/weak_models/`

覆盖内容：

1. 配置开关与 observe_only 权重校验。
2. 四个弱模型角色输出契约。
3. active 加权聚合公式。
4. observe_only 运行和落库但不参与聚合。
5. SSR snapshot_id 缺失 blocked。
6. snapshot slot 不匹配 blocked。
7. dry-run 不写库。
8. confirm-write 写入 run/result/aggregation。
9. 单个模型失败转 partial_success。
10. CLI 默认 dry-run。
11. CLI confirm-write。
12. CLI 拒绝 scheduler trigger_source。
13. migration 仅创建 27A 三张弱模型表。

默认 pytest 不访问真实 Binance、真实 MySQL、真实 Redis、真实 Hermes，也不调用大模型。

运行：

```bash
python -m pytest tests/weak_models -q
```

## 12. 27A 审查修复：veto、SSR 状态与 observe_only context

### 12.1 veto_factors 独立落库

修复入口：

`migrations/versions/20260608_27a_weak_model_veto_factors.py`

新增字段：

`weak_model_aggregation.veto_factors_json`

写入位置：

`app/weak_models/repository.py::WeakModelRepository.upsert_aggregation()`

写入规则：

`WeakModelAggregationSummary.veto_factors` 会序列化为紧凑 JSON 数组，写入 `veto_factors_json`。该字段是独立列，不依赖 `details_json`。

迁移行为：

1. `upgrade()` 给 `weak_model_aggregation` 增加 nullable 的 `veto_factors_json`。
2. 将历史空值回填为 `[]`。
3. 将字段改为 `nullable=False`。
4. `downgrade()` 只删除该字段。

本迁移不修改 K线表，不修改策略算法，不修改 18/19/20/21，不写业务数据，不发送 Hermes，不请求 Binance REST。

### 12.2 strategy_signal_run 状态校验

校验位置：

`app/weak_models/service.py::WeakModelService._validate_strategy_signal_run()`

规则：

只有 `strategy_signal_run.status == "success"` 才允许进入弱模型执行。其他状态会在 snapshot 查询前直接 blocked。

失败结果：

```text
status=blocked
error_code=invalid_strategy_signal_run_status
```

27A 不会因为 SSR 状态异常而自行换 snapshot，也不会继续运行弱模型。

### 12.3 observe_only context 汇总

处理位置：

`app/weak_models/aggregation.py::_context_summary()`

明确选择：

`observe_only` 不参与方向、风险、确认的正式聚合，不影响 `directional_score`、`risk_level`、`trade_permission` 或确认因子列表。

但 `model_role=context` 的 `observe_only` 输出允许进入 `context_summary`，用于提供市场背景摘要，并在摘要中记录：

```text
source_model_key
source_maturity_stage=observe_only
source_participation_mode=observe_only
```

这样保留默认 `market_regime_context=observe_only` 的审计观察属性，同时避免“配置存在背景模型但摘要一直 unknown”的模糊状态。

### 12.4 新增覆盖测试

测试文件：

1. `tests/weak_models/test_models_and_aggregation.py`
2. `tests/weak_models/test_weak_model_service.py`
3. `tests/weak_models/test_weak_model_cli_and_config.py`

新增覆盖：

1. `veto_factors_json` 独立字段落库。
2. 新增 27A follow-up migration 只增加 `weak_model_aggregation.veto_factors_json`。
3. SSR 非 `success` 状态直接 blocked，且不查询 snapshot、不运行弱模型。
4. observe_only context 写入 `context_summary`，但不影响方向分数或风险权限。
