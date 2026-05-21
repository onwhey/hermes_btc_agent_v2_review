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
