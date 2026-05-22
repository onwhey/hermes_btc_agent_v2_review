# 20 模型审查聚合与编排实现说明

本文档说明阶段 20 当前已经实现的内容。20A 的聚合与复用判断另见
`docs/implementation/20A_model_review_aggregation.md`；本文件补充 20B 的
模型接力 chain / step 状态机与断点续跑框架。

## 1. 功能：20B mock 模型接力 chain / step 状态机

### 1.1 发起方式

用户手动执行：

    python -m scripts.run_model_review_chain \
      --material-pack-id AMP-xxx \
      --chain-key mock_deepseek_then_gpt_risk_review \
      --trigger-source cli \
      --dry-run

或确认写库：

    python -m scripts.run_model_review_chain \
      --material-pack-id AMP-xxx \
      --chain-key mock_deepseek_then_gpt_risk_review \
      --trigger-source cli \
      --confirm-write

恢复未完成链：

    python -m scripts.run_model_review_chain \
      --chain-id CHAIN-xxx \
      --trigger-source cli \
      --resume \
      --confirm-write

`--simulate-step-failure 2` 只用于 CLI/测试模拟失败，不是生产默认逻辑。

### 1.2 入口文件

`scripts/run_model_review_chain.py`

入口方法：

`main()`

脚本只解析参数、创建 `ModelReviewChainRequest`、打开 MySQL session、调用
service 并打印紧凑结果。脚本不包含状态机业务逻辑，不直接写表，不请求任何
模型 provider，不发送 Hermes，不修改 K线表，不允许 scheduler 调用。

### 1.3 核心 service

`app/model_review_chain/service.py`

核心方法：

`ModelReviewChainService.run_model_review_chain()`

### 1.4 核心调用链路

创建新链：

    scripts/run_model_review_chain.py::main
        ↓
    app/model_review_chain/service.py::run_model_review_chain
        ↓
    app/model_review_chain/service.py::ModelReviewChainService.run_model_review_chain
        ↓
    app/model_review_chain/chain_profile.py::resolve_chain_profile
        ↓
    app/model_review_chain/repository.py::get_material_pack_by_id
        ↓
    app/model_review_chain/repository.py::create_model_review_chain_run
        ↓
    app/model_review_chain/repository.py::create_model_review_chain_step
        ↓
    app/model_review_chain/repository.py::create_mock_model_analysis_run
        ↓
    app/model_analysis/repository.py::create_model_analysis_run
        ↓
    app/model_review_chain/repository.py::update_model_review_chain_step
        ↓
    app/model_review_chain/repository.py::update_model_review_chain_run

恢复未完成链：

    scripts/run_model_review_chain.py::main
        ↓
    app/model_review_chain/service.py::run_model_review_chain
        ↓
    app/model_review_chain/repository.py::get_chain_run_by_chain_id
        ↓
    app/model_review_chain/repository.py::list_chain_steps
        ↓
    app/model_review_chain/state_machine.py::step_is_resumable
        ↓
    app/model_review_chain/repository.py::create_mock_model_analysis_run
        ↓
    app/model_review_chain/repository.py::update_model_review_chain_step
        ↓
    app/model_review_chain/repository.py::update_model_review_chain_run

### 1.5 读取配置

本功能不新增环境变量配置。

读取的固定边界：

- `trigger_source` 当前只允许 `cli`
- 默认 `chain_key = mock_deepseek_then_gpt_risk_review`
- 默认 `max_retry_count = 1`

### 1.6 请求外部接口

本功能不请求外部接口。

本功能不调用 DeepSeek、GPT、Claude 或任何真实模型 provider。

本功能不请求 Binance。

### 1.7 读取数据库

创建新链时读取：

- `analysis_material_pack`
  - 通过 `material_pack_id` 查找阶段 18 材料包

恢复链时读取：

- `model_review_chain_run`
  - 通过 `chain_id` 查找链状态
- `model_review_chain_step`
  - 通过 `chain_id` 按 `step_no` 顺序读取步骤

### 1.8 写入数据库

确认写库模式写入：

- `model_review_chain_run`
  - 记录链 ID、材料包 ID、聚合 run ID、策略信号 run ID、snapshot ID、symbol、interval、chain profile、链状态、step 统计、错误信息和边界字段。
- `model_review_chain_step`
  - 记录 step ID、step_no、mock model_key、model_role、父 step / 父 model_analysis_run、当前 step 的 model_analysis_run、状态、尝试次数、hash 和错误信息。
- `model_analysis_run`
  - 每个真实执行的 mock step 写一条 compact 记录。
  - 该写入通过 `app/model_analysis/repository.py::create_model_analysis_run` 完成。
  - 不写 `model_analysis_result`，因为 20B 不是最终模型审查结果生成阶段。

唯一键：

- `model_review_chain_run.chain_id`
- `model_review_chain_step.chain_step_id`
- `model_review_chain_step(chain_id, step_no)`
- `model_analysis_run.model_analysis_run_id`

幂等与断点规则：

- 新建链会生成新的 `chain_id`。
- resume 只读取已有 `chain_id`。
- 已经 `success` 的 step 在 resume 中只作为父上下文，不会再次 mock 执行，也不会再次创建 `model_analysis_run`。
- `failed`、`retry_waiting`、`timeout` step 可被 resume 继续处理。
- 已 `success`、`skipped`、`blocked`、或仍为 `pending` 的 step 在 20B 第一版 resume 中不会被重复执行。
- `max_retry_count` 表示首次尝试后的最大重试次数；达到后不再继续执行该 step。

### 1.9 新增表结构概要

新增 migration：

`migrations/versions/20260525_20b_create_model_review_chain_tables.py`

新增表：

- `model_review_chain_run`
  - `chain_id`
  - `material_pack_id`
  - `aggregation_run_id`
  - `strategy_signal_run_id`
  - `snapshot_id`
  - `symbol`
  - `base_interval`
  - `higher_interval`
  - `chain_key`
  - `chain_profile_version`
  - `status`
  - `trigger_source`
  - `trace_id`
  - step 计数字段
  - `summary_text`
  - `error_code`
  - `error_message`
  - 四个边界字段

- `model_review_chain_step`
  - `chain_step_id`
  - `chain_id`
  - `step_no`
  - `model_key`
  - `model_role`
  - `parent_step_id`
  - `parent_model_analysis_run_id`
  - `model_analysis_run_id`
  - `status`
  - `attempt_no`
  - `max_retry_count`
  - `started_at_utc`
  - `finished_at_utc`
  - `error_code`
  - `error_message`
  - `retry_after_utc`
  - `step_input_hash`
  - `step_output_hash`

### 1.10 状态机规则

step 状态：

- `pending`
- `running`
- `success`
- `failed`
- `timeout`
- `retry_waiting`
- `skipped`
- `blocked`

chain 状态：

- `pending`
- `running`
- `partial_success`
- `success`
- `failed`
- `blocked`

链状态由 `app/model_review_chain/state_machine.py::calculate_chain_state()` 根据
step 状态计算：

- 所有 step 为 `success` 时，chain 为 `success`
- 至少一个 step 成功且后续存在失败/超时/阻断/等待重试时，chain 为 `partial_success`
- 没有成功 step 且存在失败/超时/阻断/等待重试时，chain 为 `failed`
- 有 running step 时，chain 为 `running`
- 否则为 `pending`

`partial_success` 不会被伪装成完整模型审查，也不会写出最终交易建议。

### 1.11 Redis

本功能不读取 Redis。

本功能不写入 Redis。

### 1.12 Hermes

本功能不发送 Hermes。

本功能不写入 `alert_message`。

异常只返回 CLI/service 结构化结果，不触发报警。

### 1.13 scheduler

本功能不接 scheduler。

`scripts/run_model_review_chain.py` 只接受 `--trigger-source cli`。

scheduler 不会自动创建 chain、不会 resume chain、不会触发阶段 19。

20C 才会讨论真实调度编排边界。

### 1.14 trigger_source 与 data_source

本功能涉及 `trigger_source`：

- 允许值仅为 `cli`

本功能不涉及 K线写入，因此不涉及 `data_source`。

### 1.15 边界字段

以下字段固定为 false：

- `is_final_trading_advice`
- `is_trading_signal`
- `is_executable`
- `auto_trading_allowed`

这些字段同时出现在：

- `model_review_chain_run`
- mock step 对应的 `model_analysis_run`
- CLI/service result

### 1.16 异常处理

参数异常：

- 发生在 `app/model_review_chain/result_builder.py::validate_chain_request`
- 返回 `status=failed`
- 不写数据库
- 不发送 Hermes
- 不调用模型

材料包查询异常：

- 发生在 `app/model_review_chain/service.py::_create_new_chain`
- 捕获后 rollback
- 返回 `error_code=material_pack_lookup_failed`
- 不允许 partial write 继续提交

链查询异常：

- 发生在 `app/model_review_chain/service.py::_resume_existing_chain`
- 捕获后 rollback
- 返回 `error_code=chain_lookup_failed`

创建或执行异常：

- 发生在 `app/model_review_chain/service.py::_persist_and_execute_new_chain`
  或 `_execute_chain_steps`
- 捕获后 rollback
- 返回 `status=failed`
- 不发送 Hermes
- 不自动修复数据
- 不修改 K线数据

### 1.17 本功能不负责

- 不调用真实 DeepSeek
- 不调用真实 GPT
- 不调用真实 Claude
- 不接 scheduler
- 不实现 20C 的真实调度编排
- 不实现模型 provider 接力 worker
- 不生成最终交易建议
- 不生成交易信号
- 不生成入场价、止损价、止盈价、仓位、杠杆
- 不修改 16/17/18/19 的业务语义
- 不修改正式 K线、快照、策略信号或材料包
- 不进入阶段 21 通知层

### 1.18 测试

对应测试文件：

- `tests/model_review_chain/test_model_review_chain_service.py`

覆盖内容：

- material_pack 不存在时 blocked
- dry-run 不写库
- confirm-write 写入 chain/step 和 mock `model_analysis_run`
- 两步 mock chain 全成功时 chain 为 `success`
- step 1 成功、step 2 失败时 chain 为 `partial_success`
- resume 不重复执行已成功 step
- resume 只继续处理失败、等待重试、超时等可恢复 step
- 超过 `max_retry_count` 后不再继续重试
- 边界字段全部为 false
- 不调用真实模型
- 不接 scheduler
- 数据可追溯到 `material_pack_id`、`aggregation_run_id`、`strategy_signal_run_id`、`snapshot_id`

默认 pytest 不请求外部服务、不连接真实 MySQL、不连接真实 Redis、不发送 Hermes、
不调用真实模型。

人工检查命令：

    python -m pytest tests/model_review_chain -q
    python -m pytest tests/model_review_aggregation -q
    python -m pytest tests/model_analysis -q
    python -m pytest tests -q
    python -m alembic current -v

## 2. 功能：20C scheduler/worker 自动模型审查调用与恢复机制

### 2.1 发起方式

scheduler 自动链路：

    4h K线采集完成
        -> app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
        -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
        -> app/scheduler/jobs/strategy_aggregation_job.py::run_strategy_aggregation_after_signal_job
        -> app/scheduler/runner.py::_run_model_review_chain_worker_after_aggregation_if_needed
        -> app/scheduler/jobs/model_review_chain_worker_job.py::run_model_review_chain_worker_after_aggregation_job
        -> app/model_review_chain/worker.py::run_model_review_chain_worker

手动 worker tick：

    python -m scripts.run_model_review_chain_worker \
      --trigger-source cli \
      --dry-run

或确认推进：

    python -m scripts.run_model_review_chain_worker \
      --material-pack-id AMP-xxx \
      --trigger-source cli \
      --confirm-write

手动恢复某条 chain：

    python -m scripts.run_model_review_chain_worker \
      --chain-id CHAIN-xxx \
      --trigger-source cli \
      --confirm-write

20C CLI 现在提供 `--confirm-real-model-cost`，只用于 `trigger_source=cli` 的手动 worker tick 成本确认。
scheduler / worker 自动调用真实模型时不依赖 CLI 成本确认，而是必须同时通过配置总闸、预算、白名单、频率、锁和 step 状态机检查。

### 2.2 入口文件

`scripts/run_model_review_chain_worker.py`

入口方法：

`main()`

脚本只解析参数、创建 `ModelReviewChainWorkerRequest`、打开 MySQL session、调用 worker service 并打印紧凑结果。脚本不直接调用 stage 19、不直接请求模型 provider、不写 K线、不发送 Hermes、不生成最终建议。

### 2.3 核心 service

`app/model_review_chain/worker.py`

核心方法：

`ModelReviewChainWorker.run_model_review_chain_worker()`

### 2.4 核心调用链路

material_pack 入口：

    app/model_review_chain/worker.py::run_model_review_chain_worker
        -> app/model_review_aggregation/service.py::run_model_review_aggregation
        -> app/model_review_chain/repository.py::get_latest_chain_run_for_material_pack
        -> app/model_review_chain/repository.py::create_model_review_chain_run
        -> app/model_review_chain/repository.py::create_model_review_chain_step
        -> app/model_review_chain/worker.py::_advance_chain_steps
        -> app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy
        -> app/model_analysis/service.py::run_model_analysis

chain 恢复入口：

    app/model_review_chain/worker.py::run_model_review_chain_worker
        -> app/model_review_chain/repository.py::get_chain_run_by_chain_id
        -> app/model_review_chain/repository.py::list_chain_steps
        -> app/model_review_chain/state_machine.py::step_is_resumable
        -> app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy
        -> app/model_analysis/service.py::run_model_analysis

scheduler 只触发 20C worker，不直接调用 `app/model_analysis/service.py`。

### 2.5 读取配置

新增或使用以下配置：

- `MODEL_REVIEW_REAL_MODEL_ENABLED`：真实模型总闸，默认 `false`。
- `MODEL_REVIEW_AUTO_RUN_ENABLED`：自动模型审查总开关，默认 `false`。
- `MODEL_REVIEW_SCHEDULER_ENABLED`：是否允许 scheduler/worker 自动推进，默认 `false`。
- `MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS`：scheduler 自动调用模型 key 白名单，默认空。
- `MODEL_REVIEW_DAILY_BUDGET_USD`：每日自动模型调用预算上限，默认 `0`。
- `MODEL_REVIEW_MAX_RUNS_PER_4H`：每个 4h 周期最多自动模型调用次数，默认 `2`。
- `MODEL_REVIEW_REUSE_MAX_BASE_BARS`：旧模型审查复用最多 base K线根数，默认 `3`。

这些配置在 `app/core/config.py` 读取，并在 `.env.example` 中给出默认关闭示例。

### 2.6 复用与过期规则

20C 先调用 20A 做 dry-run 聚合判断：

- 当前 `material_pack` 已有成功 stage-19 结果时，直接使用 `current_model_review`，本轮不调用大模型。
- 没有当前结果但 20A 判断旧结果仍在 `MODEL_REVIEW_REUSE_MAX_BASE_BARS` 内时，复用旧结果，结果中记录 `reused_model_analysis_run_id`。
- 旧结果超过复用期限时，不把它伪装成最新审查。
- 如果旧结果过期且 `MODEL_REVIEW_REAL_MODEL_ENABLED=false`，返回 `model_review_expired_but_real_model_disabled`，并明确 `本轮未调用大模型`。

### 2.7 自动调用前置检查

每个 step 调用 stage 19 前，由 `app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy()` 检查：

- `MODEL_REVIEW_REAL_MODEL_ENABLED=true`
- `MODEL_REVIEW_AUTO_RUN_ENABLED=true`
- `MODEL_REVIEW_SCHEDULER_ENABLED=true`
- `model_key` 在 `MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS` 中
- profile 和 provider config 都是 `enabled=true`
- `MODEL_REVIEW_DAILY_BUDGET_USD` 未超限
- 本次 `estimated_cost_usd` 不会导致预算超限
- 当前 UTC 4h bucket 的 worker 真实模型调用次数未超过 `MODEL_REVIEW_MAX_RUNS_PER_4H`
- 当前 step 不是 `success`
- 当前 step retry 次数未超过 `max_retry_count`

预算检查先于 stage 19 调用；预算不足时 step 记录为 `blocked`，不会先调用模型再发现超预算。

### 2.8 锁和并发

20C 使用 `app/core/task_lock.py::RedisTaskLock`：

- material lock：`model_review_chain:material:{material_pack_id}`
- chain lock：`model_review_chain:chain:{chain_id}`
- step lock：`model_review_chain:step:{chain_id}:{step_no}`

拿不到锁时返回 `skipped`，写明 `worker_lock_already_held`，不异常崩溃。Redis 失败时返回 `blocked`，写明 `worker_lock_failed`。

### 2.9 读取数据库

20C 读取：

- `analysis_material_pack`：确认材料包存在并构造 chain 元信息。
- `model_analysis_run` / `model_analysis_result`：通过 20A 判断当前结果、旧结果复用、过期和聚合摘要。
- `model_review_chain_run`：查找待恢复 chain 或同 material_pack 最新 chain。
- `model_review_chain_step`：按 `step_no` 恢复 step。
- `model_analysis_run`：读取 worker 真实模型调用历史，用于预算与 4h 频率控制。

### 2.10 写入数据库

20C confirm-write 可能写入：

- `model_review_chain_run`
  - 新建自动 chain，或更新 chain status / step 统计 / error 信息。
- `model_review_chain_step`
  - 新建 step，或更新 step status / attempt_no / model_analysis_run_id / hash / error 信息。
- `model_analysis_run` 和 `model_analysis_result`
  - 只通过 `app/model_analysis/service.py::run_model_analysis` 写入。
  - 20C 不绕过 stage 19 的模型调用记录体系。

20C 本阶段不新增数据库迁移；理由是 20B 已有 `model_review_chain_run`、`model_review_chain_step`，stage 19 已有 `model_analysis_run` / `model_analysis_result`，足以保存 chain/step 状态、attempt 追踪、预算依据和 provider 结果。

### 2.11 Redis、Hermes 与外部接口

Redis：

- 20C 只读写 worker 锁 key。
- 不读写 `bitcoin_price`，不缓存行情，不修改 K线相关 Redis 状态。

Hermes：

- 20C worker 和 scheduler job 自身不发送 Hermes。
- 如果 stage 19 真实调用成功且其既有配置启用了模型审查 Hermes，Hermes 行为仍由 stage 19 service 负责。

外部接口：

- dry-run 和默认配置不请求任何外部模型接口。
- scheduler 不直接请求外部模型接口。
- 只有 20C worker 通过全部 gate 后，才会委托 stage 19 调用其已实现的 provider。

### 2.12 trigger_source 与 data_source

20C worker 接受：

- `trigger_source=cli`
- `trigger_source=scheduler`
- `trigger_source=worker`

20C 调用 stage 19 时固定传入：

- `trigger_source=worker`

本功能不写正式 K线，因此不涉及 `data_source`。

### 2.13 透明度输出

20C worker result 和 scheduler details 保留：

- `model_review_invoked`
- `model_review_invocation_mode`
- `model_review_reused`
- `reused_model_analysis_run_id`
- `model_review_skip_reason`
- `model_review_block_reason`
- `invoked_model_keys_json`
- `invoked_model_roles_json`
- `model_review_chain_status`
- `latest_model_review_at_utc`
- `model_review_basis`
- `model_review_expired`

如果未调用大模型，输出必须包含 `本轮未调用大模型`。如果复用旧结果，输出复用的 `model_analysis_run_id`。如果因配置关闭而跳过，输出具体配置名。

### 2.14 状态机与断点续跑

20C 复用 20B step 状态：

- `pending`
- `running`
- `success`
- `failed`
- `timeout`
- `retry_waiting`
- `skipped`
- `blocked`

恢复规则：

- `success` step 只作为 parent context，不重复调用 stage 19。
- `pending` / `failed` / `retry_waiting` / `timeout` 可由 worker 恢复。
- 超过 `max_retry_count` 后不再重试。
- step 1 成功、step 2 失败后，worker resume 只推进 step 2。
- chain 未完整成功时只会返回 `partial_success` / `failed` / `blocked`，不会伪装成完整模型审查。

### 2.15 边界字段

以下字段固定为 false：

- `is_final_trading_advice`
- `is_trading_signal`
- `is_executable`
- `auto_trading_allowed`

20C 不生成入场价、止损价、止盈价、仓位、杠杆，不生成最终交易建议，不进入 21。

### 2.16 异常处理

参数错误：

- 发生在 `app/model_review_chain/worker.py::_validate_worker_request`
- 返回 `status=failed`
- 不写数据库，不读写 Redis，不调用 stage 19。

20A 聚合读取失败或材料包不存在：

- 由 20A service 返回结构化结果。
- 20C 转换为 blocked/failed worker result。
- 不调用真实模型。

锁失败：

- 发生在 `app/model_review_chain/worker.py::_acquire_lock_or_skipped`
- Redis 异常返回 `worker_lock_failed`
- 锁被占用返回 `worker_lock_already_held`
- 不调用 stage 19。

预算、白名单、频率或 provider/profile gate 失败：

- 发生在 `app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy`
- step 写为 `blocked`
- chain 根据 step 状态更新为 `blocked` 或 `partial_success`
- 不调用 stage 19。

stage 19 调用失败：

- 发生在 `app/model_review_chain/worker.py::_call_stage19_for_step`
- stage 19 返回 `failed` / `blocked` / `timeout` 后，20C 更新对应 step 状态
- 已成功 step 不会被回滚或重复执行
- chain 不完整时不标记为 `success`

### 2.17 本功能不负责

- 不让 scheduler 直接调用 stage 19。
- 不接 21。
- 不发送最终交易建议。
- 不生成交易信号。
- 不生成入场价、止损价、止盈价、仓位、杠杆。
- 不自动交易。
- 不修改 K线数据。
- 不修改 16/17/18/19 的既有业务语义。
- 不提交真实密钥，不修改 `.env` 中真实密钥。

### 2.18 对应测试

对应测试文件：

- `tests/model_review_chain/test_model_review_chain_worker.py`
- `tests/scheduler/test_model_review_chain_worker_hook.py`
- 回归：`tests/model_review_chain/test_model_review_chain_service.py`
- 回归：`tests/model_review_aggregation/test_model_review_aggregation_service.py`
- 回归：`tests/model_analysis`

覆盖内容：

- `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 时不调用 stage 19。
- `MODEL_REVIEW_AUTO_RUN_ENABLED=false` 时不自动推进。
- `MODEL_REVIEW_SCHEDULER_ENABLED=false` 时 scheduler/worker 不执行。
- model_key 不在白名单时不调用模型。
- 每日预算不足时不调用模型。
- 4h 频率超限时不调用模型。
- 旧模型结果在 TTL 内复用。
- 旧模型结果过期时不伪装成最新审查。
- step1 success、step2 failed 后 resume 只跑 step2。
- success step 不重复执行。
- partial_success 不伪装成 success。
- dry-run 不写库。
- confirm-write 写 chain/step 状态。
- 默认测试不调用真实模型、不连接真实 Redis、不发送 Hermes。
- 边界字段全部为 false。

人工检查命令：

    python -m pytest tests/model_review_chain -q
    python -m pytest tests/model_review_aggregation -q
    python -m pytest tests/model_analysis -q
    python -m pytest tests/scheduler -q
    python -m pytest tests -q
    python -m alembic current -v

### 2.19 20C 安全闭环修复：CLI 成本确认、临时等待与 RUNNING 超时恢复

本次修复只调整 20C worker 安全边界，不进入 21，不修改 16/17/18/19 既有业务语义。

#### 2.19.1 CLI 与 scheduler 的成本确认差异

手动 CLI 入口：

    scripts/run_model_review_chain_worker.py::main
        -> app/model_review_chain/worker.py::run_model_review_chain_worker
        -> app/model_review_chain/worker.py::_advance_one_step
        -> app/model_review_chain/worker_safety.py::cli_real_model_cost_confirmation_missing

CLI 使用 `trigger_source=cli` 且 `--confirm-write` 推进 worker 时，如果本轮会触发真实模型调用，必须额外传入：

    --confirm-real-model-cost

否则 worker 将 step 标记为 `retry_waiting`，返回 `error_code=cli_real_model_cost_not_confirmed`，并明确写出“本轮未调用大模型”。此路径不会调用 `app/model_analysis/service.py::run_model_analysis`，不会产生 stage-19 成本。

scheduler / worker 自动路径：

    app/scheduler/jobs/model_review_chain_worker_job.py::run_model_review_chain_worker_after_aggregation_job
        -> app/model_review_chain/worker.py::run_model_review_chain_worker

scheduler 不使用 CLI 成本确认参数。自动真实调用是否允许，只由以下配置与状态机 gate 决定：

- `MODEL_REVIEW_REAL_MODEL_ENABLED`
- `MODEL_REVIEW_AUTO_RUN_ENABLED`
- `MODEL_REVIEW_SCHEDULER_ENABLED`
- `MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS`
- `MODEL_REVIEW_DAILY_BUDGET_USD`
- `MODEL_REVIEW_MAX_RUNS_PER_4H`
- Redis worker lock
- step 是否已 success
- step retry 次数是否耗尽

20C 仍然不让 scheduler 直接调用 stage 19；stage 19 仍只由 worker 在所有 gate 通过后调用。

#### 2.19.2 永久 BLOCKED 与临时 RETRY_WAITING

永久阻断继续使用 `blocked`，用于配置或模型 profile 明确不允许的场景：

- `MODEL_REVIEW_REAL_MODEL_ENABLED=false`
- `MODEL_REVIEW_AUTO_RUN_ENABLED=false`
- `MODEL_REVIEW_SCHEDULER_ENABLED=false`
- `model_key_not_in_scheduler_whitelist`
- `provider_config_missing`
- `model_profile_disabled`
- `model_provider_disabled`

临时阻断使用 `retry_waiting`，并写入 `retry_after_utc`，避免 worker 后续扫描时永久卡死：

- `daily_budget_exceeded`
- `max_runs_per_4h_exceeded`
- `worker_lock_already_held`
- stage-19 返回的可恢复 timeout / rate limit / unavailable 类错误
- CLI worker 缺少 `--confirm-real-model-cost`，需要人工重新带确认参数触发

相关实现：

    app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy
        -> 返回 AutomationPolicyDecision.is_temporary / retry_after_utc
    app/model_review_chain/worker.py::_advance_one_step
        -> 临时阻断写 step.status=retry_waiting
        -> 永久阻断写 step.status=blocked

worker 扫描未完成任务时仍会读取 `pending` / `running` / `partial_success` / `failed` chain；step 级 `retry_waiting` 已在 `app/model_review_chain/state_machine.py::RESUMABLE_STEP_STATUSES` 中，因此后续 tick 可以恢复。

#### 2.19.3 RUNNING step 超时恢复

新增配置：

    MODEL_REVIEW_STEP_RUNNING_TIMEOUT_SECONDS=300

读取位置：

    app/core/config.py::load_settings
    app/core/config.py::AppSettings.model_review_step_running_timeout_seconds

恢复规则：

    app/model_review_chain/worker.py::_advance_chain_steps
        -> app/model_review_chain/worker.py::_normalize_stale_running_steps
        -> app/model_review_chain/worker_safety.py::running_step_timed_out

当 worker 发现 step 满足：

- `status=running`
- `started_at_utc` 距当前 UTC 时间超过 `MODEL_REVIEW_STEP_RUNNING_TIMEOUT_SECONDS`

则先归一化旧状态，不立即继续调用模型：

- retry 次数未耗尽：写为 `timeout`，设置 `retry_after_utc`，后续 tick 可 resume；
- retry 次数已耗尽：写为 `failed`，chain 根据已有成功 step 数变为 `partial_success` 或 `failed`。

这样进程崩溃、系统重启或网络卡死都不会让 step 永久停在 `running`。

#### 2.19.4 本阶段仍不是最终建议层

20C worker 仅负责自动模型审查链的安全推进与恢复：

- 不生成最终交易建议；
- 不生成交易信号；
- 不生成入场价、止损价、止盈价、仓位、杠杆；
- 不进入阶段 21；
- 不自动交易；
- 不修改 K线数据；
- 不绕过 stage 19 的 `model_analysis_run` / `model_analysis_result` 记录体系。

对应测试：

- `tests/model_review_chain/test_model_review_chain_worker.py`
- `tests/scheduler/test_model_review_chain_worker_hook.py`
