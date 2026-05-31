# 26C-A 策略链路观察索引实现说明

## 1. 功能：构建 strategy_pipeline_observation 观察索引

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10 \
  --confirm-write
```

默认不写库；未传 `--confirm-write` 时为 dry-run。

本功能不接入 scheduler，不允许 `--trigger-source scheduler`。

### 1.2 入口文件

`scripts/build_strategy_pipeline_observations.py`

入口方法：

`main()`

脚本职责：

- 解析 CLI 参数。
- 校验 `symbol`、`base_interval`、`higher_interval`、`limit`、`kline_slot_utc`、`trigger_source`。
- 打开 MySQL session。
- 调用 service。
- 输出中文观察结果。

脚本不负责 SQL 查询、不做 canonical 选择、不发送 Hermes、不调用模型、不请求 Binance REST、不自动交易。

### 1.3 核心调用链路

```text
scripts/build_strategy_pipeline_observations.py::main
    ↓
app/strategy_pipeline_observation/service.py::build_strategy_pipeline_observations
    ↓
app/strategy_pipeline_observation/service.py::StrategyPipelineObservationService.build_strategy_pipeline_observations
    ↓
app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.list_kline_slots
    ↓
app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.list_pipeline_runs_for_slots
    ↓
app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.load_evidence_quality_by_pipeline_run
    ↓
app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.load_advice_links_by_pipeline_run
    ↓
app/strategy_pipeline_observation/service.py::StrategyPipelineObservationService._build_payload_for_slot
    ↓
app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.upsert_observation
```

`upsert_observation` 仅在 `--confirm-write` 且非 `--dry-run` 时执行。

### 1.4 读取配置

读取非敏感配置：

- `STRATEGY_PIPELINE_REAL_MODEL_ENABLED`
- `STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST`
- `MODEL_REVIEW_REAL_MODEL_ENABLED`
- `STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED`
- `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED`

这些配置只用于判断 20C blocked 是否属于真实模型关闭导致的 expected blocked。

### 1.5 请求外部接口

本功能不请求外部接口。

不请求 Binance REST。

不请求 Binance WebSocket。

不调用 DeepSeek、GPT、Claude 或其他大模型。

不发送 Hermes。

### 1.6 读取数据库表

读取：

- `market_kline_4h`
- `strategy_pipeline_event_log`
- `strategy_evidence_quality_check_result`
- `strategy_advice_lifecycle_review`
- `strategy_advice`
- `alert_message`

说明：

- `market_kline_4h` 只用于读取已入库 4h K线 slot。
- 26C-A 不识别“理论应收盘但 market_kline_4h 缺失”的 slot。
- K线本身是否漏采、是否连续，仍由 07/11 K线质量检查负责。

### 1.7 写入数据库表

写入：

- `strategy_pipeline_observation`

仅在用户传入 `--confirm-write` 且未传入 `--dry-run` 时写入。

不写入：

- `market_kline_4h`
- `strategy_pipeline_event_log`
- `strategy_signal_run`
- `strategy_signal_result`
- `strategy_evidence_aggregation_result`
- `strategy_evidence_quality_check_result`
- `analysis_material_pack`
- `model_analysis_run`
- `model_review_aggregation_run`
- `strategy_advice`
- `strategy_advice_lifecycle_review`
- `alert_message`

### 1.8 observation 表结构

新增表：

`strategy_pipeline_observation`

核心字段：

- `observation_id`
- `symbol`
- `base_interval`
- `higher_interval`
- `kline_slot_utc`
- `canonical_pipeline_run_id`
- `canonical_trigger_source`
- `canonical_reason`
- `duplicate_pipeline_count`
- `excluded_pipeline_run_ids_json`
- `observation_status`
- `eligible_for_review`
- `eligible_for_advice_performance_review`
- `pipeline_status`
- `pipeline_current_step`
- `pipeline_error_code`
- `pipeline_error_message`
- `strategy_signal_run_id`
- `strategy_evidence_aggregation_id`
- `evidence_quality_check_id`
- `material_pack_id`
- `model_analysis_run_id`
- `review_aggregation_run_id`
- `advice_id`
- `review_id`
- `alert_message_id`
- `evidence_quality_status`
- `evidence_quality_should_block`
- `evidence_quality_failed_roles_json`
- `evidence_quality_failed_strategies_json`
- `model_review_invoked`
- `model_review_reused`
- `real_model_called`
- `real_model_blocked_by_config`
- `hermes_real_sent`
- `notification_status`
- `created_at_utc`
- `updated_at_utc`
- `details_json`

唯一约束：

```text
UNIQUE(symbol, base_interval, higher_interval, kline_slot_utc)
```

业务 ID：

```text
SPO-<symbol>-<BASE>-<HIGHER>-<YYYYMMDDTHHMMSSZ>
```

例如：

```text
SPO-BTCUSDT-4H-1D-20260531T040000Z
```

### 1.9 canonical pipeline 选择规则

规则落在：

`app/strategy_pipeline_observation/service.py::_select_canonical_pipeline`

第一版规则：

- 没有 pipeline：`observation_status=missing_pipeline`。
- 只有 CLI pipeline：`observation_status=only_cli_runs`，不选 canonical。
- scheduler + CLI 同时存在：优先选择 scheduler。
- 多条 scheduler：按观察状态优先级，再按 `created_at_utc`，再按 `id` 选择。
- CLI pipeline 默认进入 `excluded_pipeline_run_ids_json`，原因是 `cli_excluded_from_formal_sample`。
- 非 canonical scheduler 进入 `excluded_pipeline_run_ids_json`，原因是 `superseded_by_canonical_scheduler_pipeline`。

状态优先级：

```text
notification_sent
notification_prepared
advice_generated
model_review_completed
expected_blocked_by_model_config
quality_blocked
pipeline_failed
unknown
```

### 1.10 observation 状态规则

规则落在：

`app/strategy_pipeline_observation/service.py::_classify_canonical_pipeline`

支持状态：

- `missing_pipeline`
- `only_cli_runs`
- `pipeline_failed`
- `quality_blocked`
- `expected_blocked_by_model_config`
- `model_review_completed`
- `advice_generated`
- `notification_prepared`
- `notification_sent`
- `unknown`

关键映射：

- 26B failed 或 `strategy_evidence_quality_failed`：`quality_blocked`。
- 20C 停在真实模型关闭配置：`expected_blocked_by_model_config`。
- pipeline failed 或非 expected 的 blocked：`pipeline_failed`。
- 已有 `advice_id` 或 `review_id`：`advice_generated`。
- advice 已有通知状态：`notification_prepared`。
- pipeline 标记 `hermes_real_sent=true`：`notification_sent`。

eligibility：

- canonical scheduler pipeline 存在时：`eligible_for_review=true`。
- 没有 pipeline 或只有 CLI：`eligible_for_review=false`。
- 26B blocked：`eligible_for_advice_performance_review=false`。
- 真实模型关闭导致 blocked：`eligible_for_advice_performance_review=false`。
- 已有 advice：`eligible_for_advice_performance_review=true`。

### 1.11 幂等规则

幂等范围：

```text
symbol + base_interval + higher_interval + kline_slot_utc
```

规则：

- observation 不存在则创建。
- observation 已存在则更新。
- 重复执行不创建重复 observation。
- duplicate pipeline 只记录在 `duplicate_pipeline_count` 和 `excluded_pipeline_run_ids_json`。

实现位置：

`app/strategy_pipeline_observation/repository.py::StrategyPipelineObservationRepository.upsert_observation`

### 1.12 Redis

本功能不读取 Redis。

本功能不写入 Redis。

### 1.13 Hermes

本功能不发送 Hermes。

本功能不调用 `app/alerting` 的发送逻辑。

`hermes_real_sent` 字段只读取既有 pipeline 审计事实，不代表 26C 发送过 Hermes。

### 1.14 DeepSeek / 大模型

本功能不调用 DeepSeek。

本功能不调用任何大模型。

`real_model_called` 和 `model_review_invoked` 只来自既有 pipeline 审计记录。

### 1.15 scheduler

本功能不接 scheduler。

`scripts/build_strategy_pipeline_observations.py` 不允许 `--trigger-source scheduler`。

26C-A 不新增 scheduler job，不修改 `app/scheduler`。

### 1.16 trigger_source / data_source

CLI 参数 `--trigger-source` 第一版只允许：

```text
cli
```

该字段仅用于记录 26C 构建动作来源，不用于正式 K线写入。

本功能不写正式 K线表，因此不产生新的 K线 `data_source`。

### 1.17 异常处理

参数错误：

- 发生位置：`scripts/build_strategy_pipeline_observations.py::main`
- 返回：exit code `2`
- 行为：不打开写库流程，不发送 Hermes，不调用模型。

数据库查询或写入失败：

- 发生位置：repository 查询或 `upsert_observation`
- 捕获位置：`scripts/build_strategy_pipeline_observations.py::main`
- 返回：exit code `2`
- session_scope 会 rollback 并关闭 session。
- 不发送 Hermes。
- 不重试。
- 不修改正式 K线。
- 不自动修复。

canonical 状态无法明确判断：

- 发生位置：`service.py::_classify_canonical_pipeline`
- 行为：写入或输出 `observation_status=unknown`。
- 不阻断任何生产 pipeline，因为 26C-A 不参与主链路。

### 1.18 测试

测试文件：

`tests/strategy_pipeline_observation/test_strategy_pipeline_observation.py`

覆盖：

- scheduler pipeline 生成 canonical observation。
- CLI pipeline 默认不进入 canonical。
- 多条 CLI 为 `only_cli_runs`。
- scheduler + CLI 选择 scheduler。
- 多条 scheduler 按状态优先级和时间选择 canonical。
- 没有 pipeline 为 `missing_pipeline`。
- 26B passed 写入 observation。
- 26B failed 为 `quality_blocked`，且 advice performance 不 eligible。
- 模型关闭导致 20C blocked 标记 `expected_blocked_by_model_config`。
- pipeline failed 标记 `pipeline_failed`。
- advice 存在时 `eligible_for_advice_performance_review=true`。
- 重复执行不重复创建 observation。
- 26C 不调用模型。
- 26C 不发送 Hermes。
- 26C 不重新跑 16/23F/26B/18/20/21。
- CLI dry-run 不写库。
- CLI confirm-write 才写库。
- CLI 参数错误返回 exit code `2`。

默认 pytest 不请求 Binance、不连接真实 MySQL、不连接 Redis、不发送 Hermes、不调用模型。

### 1.19 人工检查脚本

dry-run：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10
```

确认写入：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10 \
  --confirm-write
```

精确 slot：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --kline-slot-utc 2026-05-31T04:00:00Z \
  --confirm-write
```

### 1.20 本功能不负责

- 不实现 26C-B。
- 不采集 advice 后市场表现。
- 不做胜率统计。
- 不做盈亏比统计。
- 不做策略评分。
- 不做模型评分。
- 不做复盘结论。
- 不做策略降权或禁用建议。
- 不做后台报表。
- 不接 scheduler 自动任务。
- 不请求 Binance REST。
- 不调用模型。
- 不发送 Hermes。
- 不重新跑 16/23F/26B/18/20/21。
- 不自动交易。
- 不读取账户。
- 不读取仓位。
- 不生成订单。
