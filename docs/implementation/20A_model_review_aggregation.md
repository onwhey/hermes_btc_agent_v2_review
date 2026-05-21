# 20A 模型审查结果聚合与复用判断实现说明

## 1. 功能：手动聚合阶段 19 模型审查结果

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_model_review_aggregation \
  --material-pack-id AMP-xxx \
  --trigger-source cli \
  --dry-run
```

确认写入时执行：

```bash
python -m scripts.run_model_review_aggregation \
  --material-pack-id AMP-xxx \
  --trigger-source cli \
  --confirm-write
```

本功能只允许 `trigger_source=cli`。20A 第一版不接 scheduler。

### 1.2 入口文件

入口文件：

`scripts/run_model_review_aggregation.py`

入口方法：

`main()`

### 1.3 核心 service

核心 service 文件：

`app/model_review_aggregation/service.py`

核心 service 方法：

`ModelReviewAggregationService.run_model_review_aggregation()`

便捷调用方法：

`app/model_review_aggregation/service.py::run_model_review_aggregation`

### 1.4 核心调用链路

```text
用户 CLI
    ↓
scripts/run_model_review_aggregation.py::main
    ↓
app/model_review_aggregation/service.py::run_model_review_aggregation
    ↓
app/model_review_aggregation/service.py::ModelReviewAggregationService.run_model_review_aggregation
    ↓
app/model_review_aggregation/repository.py::get_material_pack_by_id
    ↓
app/model_review_aggregation/repository.py::list_model_analysis_runs_for_material_pack
    ↓
app/model_review_aggregation/repository.py::list_success_model_review_candidates
    ↓
app/model_review_aggregation/fingerprint.py::build_material_fingerprint
    ↓
app/model_review_aggregation/fingerprint.py::calculate_reuse_base_bars
    ↓
app/model_review_aggregation/candidate_rules.py::candidate_metadata_is_compatible
    ↓
app/model_review_aggregation/summarizer.py::summarize_accepted_model_results
    ↓
app/model_review_aggregation/result_builder.py::build_persistence_payload
    ↓
app/model_review_aggregation/repository.py::create_model_review_aggregation_run
        仅 --confirm-write 执行
```

### 1.5 读取配置

本功能读取：

- `MODEL_REVIEW_REUSE_MAX_BASE_BARS`，默认 `3`，表示旧阶段 19 审查最多复用 3 根 base interval K 线。
- `MODEL_REVIEW_REAL_MODEL_ENABLED`，用于在旧审查过期或缺少审查结果时说明真实模型调用被哪项配置阻断。
- `MODEL_REVIEW_SCHEMA_VERSION`，用于判断已有阶段 19 审查结果是否与当前 schema 版本兼容。

本功能不读取 Binance 密钥，不读取 Hermes webhook，不读取任何交易账户配置。

### 1.6 请求外部接口

本功能不请求外部接口。

本功能不请求 Binance。

本功能不请求 DeepSeek、GPT、Claude 或其他大模型。

本功能不发送 Hermes。

### 1.7 读取数据库

本功能读取：

- `analysis_material_pack`：按 `material_pack_id` 读取阶段 18 材料包。
- `model_analysis_run`：读取当前材料包已有的阶段 19 attempt，用于统计 failed / blocked / skipped。
- `model_analysis_result`：读取同 symbol / base_interval / higher_interval 的最新成功或 partial_success 阶段 19 结果，用于当前材料包聚合或旧结果复用判断。

本功能不读取正式 K 线表。

本功能不读取账户、持仓、订单、杠杆或保证金相关数据。

### 1.8 写入数据库

`--dry-run` 不写数据库。

`--confirm-write` 写入：

- `model_review_aggregation_run`

写入字段包括：

- `review_aggregation_run_id`
- `material_pack_id`
- `aggregation_run_id`
- `strategy_signal_run_id`
- `snapshot_id`
- `symbol`
- `base_interval`
- `higher_interval`
- `status`
- `trigger_source`
- `created_by`
- `trace_id`
- `input_model_run_count`
- `input_model_result_count`
- `accepted_model_result_count`
- `failed_model_result_count`
- `blocked_model_result_count`
- `skipped_model_result_count`
- `model_review_invoked`
- `model_review_invocation_mode`
- `model_review_reused`
- `reused_model_analysis_run_id`
- `reused_model_review_created_at_utc`
- `model_review_skip_reason`
- `model_review_block_reason`
- `latest_model_review_at_utc`
- `model_review_basis`
- `model_review_reuse_status`
- `model_review_reuse_base_bars`
- `model_review_reuse_max_base_bars`
- `model_review_expired`
- `review_input_fingerprint`
- `review_decision_summary`
- `evidence_quality_summary`
- `risk_acceptability_summary`
- `strategy_conflict_summary`
- `summary_text`
- `is_final_trading_advice`
- `is_trading_signal`
- `is_executable`
- `auto_trading_allowed`

唯一键：

- `review_aggregation_run_id`

外键：

- `material_pack_id -> analysis_material_pack.material_pack_id`
- `aggregation_run_id -> strategy_aggregation_run.aggregation_run_id`
- `strategy_signal_run_id -> strategy_signal_run.run_id`

幂等规则：

- 20A 第一版每次 CLI 执行生成一个新的 `review_aggregation_run_id`。
- 阶段 19 的真实模型调用幂等仍由阶段 19 的 `review_version_key` 负责。
- 20A 不重跑阶段 19，也不覆盖阶段 19 结果。

冲突处理：

- 如果写入 `model_review_aggregation_run` 失败，service 回滚 session，返回 `status=failed` 和 `error_code=model_review_aggregation_persistence_failed`。
- 失败时不修改阶段 18、阶段 19 或 K 线数据。

### 1.9 Redis

本功能不读取 Redis。

本功能不写入 Redis。

### 1.10 Hermes

本功能不发送 Hermes。

20A 第一版只在 CLI 输出和 `model_review_aggregation_run` 中记录聚合状态。Hermes 告警留给后续单独 plan。

### 1.11 DeepSeek 与其他大模型

本功能不调用 DeepSeek。

本功能不调用 GPT。

本功能不调用 Claude。

本功能不调用任何真实大模型。

本功能只读取已经落库的阶段 19 结果，不触发阶段 19。

### 1.12 scheduler

本功能不涉及 scheduler。

本次没有新增 scheduler job。

本次没有修改现有 scheduler 调用链。

scheduler 当前仍不直接触发阶段 19。

20A 第一版也不允许 scheduler 调用 `scripts/run_model_review_aggregation.py`。

### 1.13 scripts

涉及 scripts：

- `scripts/run_model_review_aggregation.py`

脚本只负责：

- 解析 `--material-pack-id`
- 解析 `--trigger-source cli`
- 解析 `--dry-run` / `--confirm-write`
- 创建 `ModelReviewAggregationRequest`
- 打开 MySQL session
- 调用 `app/model_review_aggregation/service.py::run_model_review_aggregation`
- 打印 compact 输出

脚本不负责：

- 直接请求 Binance
- 直接请求大模型
- 直接写业务表
- 发送 Hermes
- 修改正式 K 线
- 自动修复数据
- 自动交易

### 1.14 trigger_source 与 data_source

本功能涉及 `trigger_source`：

- 允许值：`cli`
- 非 `cli` 会返回参数错误，不继续执行。

本功能不涉及正式 K 线写入，因此不生成 K 线 `data_source`。

本功能不会写入 `market_kline_4h`，也不会混用 `binance_rest_by_cli` 或 `binance_rest_by_scheduler`。

## 2. 功能：复用旧阶段 19 审查结果

### 2.1 复用判断入口

入口方法：

`app/model_review_aggregation/service.py::ModelReviewAggregationService._select_reuse_or_expired_review`

### 2.2 复用判断依据

第一版保守复用，必须同时满足：

- 当前材料包存在。
- 旧阶段 19 结果为 `success`。
- 旧结果来自同一 `symbol`。
- 旧结果来自同一 `base_interval`。
- 旧结果来自同一 `higher_interval`。
- 阶段 19 边界字段全部为 false。
- `review_schema_version` 与当前配置兼容。
- `prompt_template_hash` 与当前 prompt 模板兼容。
- 非 mock provider 必须有 `profile_hash`。
- 材料内容指纹一致。
- 旧结果距离当前材料包不超过 `MODEL_REVIEW_REUSE_MAX_BASE_BARS` 根 base interval K 线。

材料内容指纹由 `app/model_review_aggregation/fingerprint.py::build_material_fingerprint` 生成，至少参考：

- `symbol`
- `base_interval`
- `higher_interval`
- `analysis_hypothesis_direction`
- `risk_gate_status`
- `risk_level`
- `conflict_level`
- `structure_state`
- `volatility_state`
- 支撑候选数量
- 压力候选数量
- 假设失效检查摘要
- 目标观察区摘要

`base_open_time_end_ms` 不参与材料内容指纹等价比较，只用于计算相隔多少根 base interval K 线。

### 2.3 过期判断

过期判断方法：

`app/model_review_aggregation/fingerprint.py::calculate_reuse_base_bars`

当前 base interval 为 `4h` 时：

```text
MODEL_REVIEW_REUSE_MAX_BASE_BARS=3
```

表示最多复用约 12 小时。

如果旧审查超过 3 根 base interval：

- 不继续当作最新模型审查。
- 返回 `model_review_expired=true`。
- 返回 `model_review_reuse_status=model_review_expired_but_real_model_disabled`，如果 `MODEL_REVIEW_REAL_MODEL_ENABLED=false`。
- `summary_text` 和 `model_review_skip_reason` 明确写入“本轮未调用大模型”和配置阻断原因。

### 2.4 大模型参与状态

20A 输出固定说明：

- `model_review_invoked=false`
- `model_review_reused=true/false`
- `reused_model_analysis_run_id`
- `model_review_skip_reason`
- `model_review_block_reason`
- `model_review_basis`
- `latest_model_review_at_utc`
- `model_review_reuse_status`
- `model_review_reuse_base_bars`
- `model_review_expired`

没有模型参与时，`summary_text` 与 `model_review_skip_reason` 均包含：

```text
本轮未调用大模型
```

### 2.5 输出边界

20A 输出边界字段固定为 false：

- `is_final_trading_advice=false`
- `is_trading_signal=false`
- `is_executable=false`
- `auto_trading_allowed=false`

另外，`directional_trade_allowed=false`，避免阶段 21 将 20A 聚合结果误读为方向性交易输出。

## 3. 功能：阶段 20A 聚合摘要

### 3.1 聚合摘要来源

聚合摘要只来自阶段 19 的结构化字段：

- `review_decision`
- `evidence_quality`
- `risk_acceptability`
- `strategy_conflict_level`
- `missing_evidence_json`
- `risk_warnings_json`
- `human_review_questions_json`

本功能不读取 raw request。

本功能不读取 raw response。

本功能不保存完整 prompt。

本功能不保存完整大模型输出。

### 3.2 聚合摘要字段

输出至少包含：

- `material_pack_id`
- `review_aggregation_run_id`
- `strategy_signal_run_id`
- `snapshot_id`
- `accepted_model_result_count`
- `failed_model_result_count`
- `blocked_model_result_count`
- `skipped_model_result_count`
- `model_review_invoked`
- `model_review_reused`
- `reused_model_analysis_run_id`
- `model_review_skip_reason`
- `model_review_basis`
- `latest_model_review_at_utc`
- `model_review_reuse_status`
- `model_review_reuse_base_bars`
- `model_review_expired`
- `review_decision_summary`
- `evidence_quality_summary`
- `risk_acceptability_summary`
- `strategy_conflict_summary`
- `summary_text`

### 3.3 本功能不负责

本功能不生成最终交易建议。

本功能不生成交易信号。

本功能不生成入场价、止损价、止盈价、仓位或杠杆。

本功能不执行自动下单、平仓、调仓、撤单、调整杠杆或调整保证金模式。

本功能不修改阶段 16/17/18 的业务语义。

本功能不修改 K 线数据。

## 4. 新增表与迁移

新增迁移：

`migrations/versions/20260524_20a_create_model_review_aggregation_run.py`

新增 ORM：

`app/storage/mysql/models/model_review_aggregation.py`

新增表：

`model_review_aggregation_run`

新增该表的理由：

- 阶段 20A 的输出不是阶段 19 的单次模型审查结果。
- 阶段 20A 需要记录复用判断、过期判断、配置阻断原因和未来阶段 21 可读取的聚合摘要。
- 如果复用旧阶段 19 结果，当前 `model_analysis_result` 不能表达“本轮未调用大模型但复用了哪一条旧结果”。
- 因此需要独立表保存阶段 20A run 级别的审计信息。

字段设计约束：

- 不保存完整行情窗口。
- 不保存完整 prompt。
- 不保存完整模型原始输出。
- 不保存持续追加的大字段。
- JSON 字段只保存小型摘要、状态数组和问题列表。

## 5. 异常处理

### 5.1 material_pack 不存在

发生位置：

`app/model_review_aggregation/repository.py::get_material_pack_by_id`

捕获层：

`app/model_review_aggregation/service.py::ModelReviewAggregationService.run_model_review_aggregation`

结果：

- `status=blocked`
- `error_code=material_pack_not_found`
- 不写 `model_review_aggregation_run`，因为缺少外键目标。
- 不发送 Hermes。
- 不调用大模型。
- 不修改正式数据。

### 5.2 没有阶段 19 成功结果

发生位置：

`app/model_review_aggregation/repository.py::list_success_model_review_candidates`

捕获层：

`app/model_review_aggregation/service.py::_build_no_result_blocked_result`

结果：

- `status=blocked`
- `error_code=no_model_review_result`
- `model_review_invoked=false`
- `model_review_reused=false`
- `summary_text` 明确“本轮未调用大模型”。
- 如果 `MODEL_REVIEW_REAL_MODEL_ENABLED=false`，写明该配置阻断真实模型调用。
- confirm-write 时写入一条 blocked 的 20A 聚合记录。
- 不触发阶段 19。

### 5.3 旧阶段 19 结果已过期

发生位置：

`app/model_review_aggregation/service.py::_select_reuse_or_expired_review`

捕获层：

`app/model_review_aggregation/service.py::_build_expired_blocked_result`

结果：

- `status=blocked`
- `model_review_expired=true`
- `model_review_reused=false`
- `model_review_basis=expired_model_review_not_used`
- `model_review_reuse_base_bars` 记录已过几根 base interval。
- 如果 `MODEL_REVIEW_REAL_MODEL_ENABLED=false`，`error_code=model_review_expired_but_real_model_disabled`。
- 不伪装成最新模型审查。
- 不触发阶段 19。
- 不修改阶段 19 结果。

### 5.4 数据库读取失败

发生位置：

- `app/model_review_aggregation/repository.py::get_material_pack_by_id`
- `app/model_review_aggregation/repository.py::list_model_analysis_runs_for_material_pack`
- `app/model_review_aggregation/repository.py::list_success_model_review_candidates`

捕获层：

`app/model_review_aggregation/service.py::ModelReviewAggregationService.run_model_review_aggregation`

结果：

- rollback 当前 session。
- 返回 `status=failed`。
- `error_code=model_review_aggregation_lookup_failed`。
- 不发送 Hermes。
- 不调用大模型。
- 不修改正式数据。

### 5.5 数据库写入失败

发生位置：

`app/model_review_aggregation/repository.py::create_model_review_aggregation_run`

捕获层：

`app/model_review_aggregation/service.py::_persist_result_or_failed`

结果：

- rollback 当前 session。
- 返回 `status=failed`。
- `error_code=model_review_aggregation_persistence_failed`。
- 不修改阶段 18、阶段 19 或 K 线数据。
- 不发送 Hermes。
- 不调用大模型。

## 6. 对应测试

测试目录：

`tests/model_review_aggregation/`

测试文件：

`tests/model_review_aggregation/test_model_review_aggregation_service.py`

覆盖内容：

- material_pack 不存在时 blocked。
- material_pack 存在但没有阶段 19 成功结果时 blocked。
- 单条成功阶段 19 结果可以生成阶段 20A 聚合摘要。
- 旧阶段 19 结果在 3 根 base K 线内可以复用。
- 超过 3 根 base K 线后标记 expired，不允许伪装成最新审查。
- `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 时不触发真实模型，并在输出中说明配置阻断。
- dry-run 不写库。
- confirm-write 正常写入 repository payload。
- 输出边界字段全部为 false。
- 大模型参与状态字段完整。

默认 pytest：

- 不请求真实 Binance。
- 不连接真实 MySQL。
- 不连接真实 Redis。
- 不发送真实 Hermes。
- 不调用 DeepSeek 或其他大模型。

人工运行：

```bash
python -m pytest tests/model_review_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests -q
python -m alembic current -v
```

本地如果使用项目虚拟环境，可执行：

```bash
.\.venv\Scripts\python.exe -m pytest tests\model_review_aggregation -q
```

## 7. 本阶段明确未实现

20A 未实现：

- 20B 的模型接力链状态机。
- 20B 的 chain worker。
- 20B 的 watchdog。
- 20C 的 scheduler 自动模型调用。
- GPT / Claude 接入。
- 自动触发 DeepSeek。
- 多模型真实接力。
- 预算与频率控制。
- 阶段 21 最终中文建议层。
- 入场价、止损价、止盈价、仓位、杠杆等最终建议字段。
- 自动交易相关任何能力。

## 8. 审查重点

建议重点审查：

- `app/model_review_aggregation/service.py::ModelReviewAggregationService.run_model_review_aggregation`
- `app/model_review_aggregation/service.py::_select_reuse_or_expired_review`
- `app/model_review_aggregation/candidate_rules.py::candidate_metadata_is_compatible`
- `app/model_review_aggregation/summarizer.py::summarize_accepted_model_results`
- `app/model_review_aggregation/result_builder.py::build_persistence_payload`
- `app/model_review_aggregation/fingerprint.py::build_material_fingerprint`
- `app/model_review_aggregation/repository.py::create_model_review_aggregation_run`
- `scripts/run_model_review_aggregation.py::main`
- `migrations/versions/20260524_20a_create_model_review_aggregation_run.py`

## 9. project_invariants 自检

本实现不违反 `docs/rules/project_invariants.md`：

- 自动交易：未实现。
- K 线数据来源：未修改。
- manual_repair / human_edit / manual_input / system_repair：未引入业务路径。
- REST / WebSocket 边界：未修改。
- trigger_source / data_source：20A 只接受 `trigger_source=cli`；不涉及正式 K 线 `data_source`。
- scripts 边界：脚本只做 CLI 参数解析和调用 service。
- scheduler 边界：未新增 scheduler 调用。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：20A 未发送 Hermes。
- MySQL / Redis 边界：只通过 repository 读写 MySQL；不使用 Redis。
- 敏感信息提交：未提交密钥或真实日志。

危险关键词 grep 说明：

- `DeepSeek`、`GPT`、`Claude`、`manual_repair`、`human_edit`、`manual_input`、`system_repair` 只出现在本 implementation 的禁止说明中。
- `order` 只出现在 `app/model_review_aggregation/repository.py` 的 SQLAlchemy `order_by(...)` 查询排序方法中，不是 Binance 交易接口，也不涉及自动交易。
