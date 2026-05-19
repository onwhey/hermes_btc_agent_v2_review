# 19 大模型策略审查门控层实现说明

## 1. 功能：19A mock 审查门控

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --dry-run
```

确认写库必须显式执行：

```bash
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --confirm-write
```

本阶段不接自动 scheduler。

### 1.2 入口文件

`scripts/run_model_analysis.py`

入口方法：

`main()`

脚本只解析参数、打开 MySQL session、调用 service、打印紧凑结果。

### 1.3 核心调用链路

```text
scripts/run_model_analysis.py::main
    ↓
app/model_analysis/service.py::run_model_analysis
    ↓
app/model_analysis/service.py::ModelAnalysisService.run_model_analysis
    ↓
app/model_analysis/repository.py::get_material_pack_by_id
    ↓
app/model_analysis/prompt_builder.py::build_model_review_prompt
    ↓
app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material
    ↓
app/model_analysis/schema_validator.py::validate_model_review_output
    ↓
app/model_analysis/repository.py::create_model_analysis_run
    ↓
app/model_analysis/repository.py::create_model_analysis_result
```

Hermes 开启时追加：

```text
app/model_analysis/service.py::ModelAnalysisService._send_or_skip_hermes
    ↓
app/model_analysis/hermes_formatter.py::build_model_analysis_visible_body
    ↓
app/alerting/service.py::send_alert
```

### 1.4 读取配置

读取配置项：

- `MODEL_REVIEW_ENABLED`
- `MODEL_REVIEW_DRY_RUN`
- `MODEL_REVIEW_PROVIDER`
- `MODEL_REVIEW_MAX_INPUT_CHARS`
- `MODEL_REVIEW_MAX_OUTPUT_CHARS`
- `MODEL_REVIEW_MAX_INPUT_BYTES`
- `MODEL_REVIEW_MAX_OUTPUT_BYTES`
- `MODEL_REVIEW_MAX_STRATEGY_ITEMS`
- `MODEL_REVIEW_MAX_REASON_ITEMS_PER_STRATEGY`
- `MODEL_REVIEW_HERMES_ENABLED`
- `MODEL_REVIEW_PROMPT_TEMPLATE_VERSION`
- `MODEL_REVIEW_SCHEMA_VERSION`

默认 provider 是 `mock`，默认 dry-run，默认不发送 Hermes。`MODEL_REVIEW_ENABLED=false` 时仍允许 dry-run，但阻断 `--confirm-write`。

### 1.5 数据来源

本功能只读取：

`analysis_material_pack`

读取规则：

- 只允许 `status=success` 的第 18 阶段材料包进入审查。
- `partial_success`、`blocked`、`failed` 或不存在的材料包都会返回 `blocked`。
- 不绕过 `analysis_material_pack` 直接读取 K 线。
- 不请求 Binance。
- 不读取 Redis。

### 1.6 Prompt 摘要构造

`app/model_analysis/prompt_builder.py::build_model_review_prompt` 只构造受限摘要，不调用模型。

输入摘要包含：

- material pack 基础 ID 和版本信息。
- 动态策略摘要列表。
- 证据质量、风险等级、缺失证据、reason codes 等短字段。
- 审查问题和验证重点的短摘要。

限制规则：

- 最多 `MODEL_REVIEW_MAX_STRATEGY_ITEMS` 条策略摘要。
- 每个策略最多 `MODEL_REVIEW_MAX_REASON_ITEMS_PER_STRATEGY` 条 reason/missing evidence。
- 输入超过 `MODEL_REVIEW_MAX_INPUT_CHARS` 或 `MODEL_REVIEW_MAX_INPUT_BYTES` 时返回 `blocked`。
- 不保存完整 prompt。
- 不保存完整材料包 debug 或完整指标序列。

策略名称和策略数量完全动态处理，不写死 gann、trend、risk_control 等策略名。

### 1.7 Mock provider

`app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material`

本阶段只实现 mock provider：

- 不连接真实大模型。
- 不调用 DeepSeek。
- 不调用 OpenAI、Claude 或其他真实 provider。
- 不生成 long/short 操作建议。
- 不生成入场价、止损价、止盈价、仓位、杠杆。
- 默认返回 `wait` 或 `require_more_evidence` 等审查结论。

mock 输出超过 `MODEL_REVIEW_MAX_OUTPUT_CHARS` 或 `MODEL_REVIEW_MAX_OUTPUT_BYTES` 时返回 `blocked`。

### 1.8 Schema 校验

`app/model_analysis/schema_validator.py::validate_model_review_output`

必填字段：

- `review_decision`
- `evidence_quality`
- `logic_consistency`
- `risk_acceptability`
- `strategy_conflict_level`
- `missing_evidence`
- `risk_warnings`
- `human_review_questions`
- `validation_focus`
- `not_trading_advice`

`not_trading_advice` 必须为 `true`。

禁止字段：

- `entry_price`
- `stop_loss`
- `take_profit`
- `position_size`
- `leverage`
- `order_type`
- `final_advice`
- `buy_now`
- `sell_now`

出现禁止字段时返回 `blocked`，不写 `model_analysis_result`。

### 1.9 入库流程

新增迁移：

`migrations/versions/20260520_19_create_model_analysis_tables.py`

新增表：

- `model_analysis_run`
- `model_analysis_result`

`model_analysis_run` 是 attempt 表：

- 可以记录多次 `blocked`、`failed`、`success` 尝试。
- 只对 `model_analysis_run_id` 建唯一约束。
- 不对 `review_version_key` 建唯一约束。
- 普通索引包括 `material_pack_id`、`aggregation_run_id`、`strategy_signal_run_id`、`review_version_key`、`status, created_at_utc`、`trace_id`。

`model_analysis_result` 是最终结果表：

- 只在审查成功后写入。
- `model_analysis_result_id` 唯一。
- `review_version_key` 单字段唯一。
- `review_version_key` 来源为 `material_pack_id + model_provider + model_name + model_version + prompt_template_version + review_schema_version + review_mode` 的 SHA-256。

没有使用多个 VARCHAR 字段组成的大复合唯一索引。

### 1.10 幂等与并发

幂等规则：

- 如果同一 `review_version_key` 的 `model_analysis_result` 已存在，service 返回 `skipped / already_exists`。
- `model_analysis_run` 中的 `blocked` 或 `failed` attempt 不会锁死后续重跑。
- 并发写入最终结果时，如果 `model_analysis_result.review_version_key` 唯一约束冲突，service 会重新查询已有结果并返回 `skipped / already_exists`，不会把并发重复运行报成 `failed`。

### 1.11 Hermes

Hermes formatter：

`app/model_analysis/hermes_formatter.py::build_model_analysis_visible_body`

中文正文明确包含：

- 这是大模型审查结果，不是最终交易建议。
- 本阶段未自动交易。
- 本阶段未生成订单。
- 本阶段未给出仓位或杠杆。
- 当前 `review_decision`。
- 证据质量。
- 风险接受度。
- 是否需要人工审核。
- `trace_id`。

Hermes 发送失败不会把主审查结果改成 `failed`，只在 `model_analysis_run` 中记录 `hermes_status=failed` 和错误摘要。

### 1.12 异常处理

阻断场景：

- material pack 不存在。
- material pack 非 `success`。
- `MODEL_REVIEW_ENABLED=false` 时执行 `--confirm-write`。
- 输入超过字符或字节限制。
- 输出超过字符或字节限制。
- schema 缺字段、枚举非法、`not_trading_advice` 不为 true。
- 输出包含禁止交易字段。
- provider 配置不是 `mock`。

失败场景：

- 数据库读取异常。
- 数据库写入异常。
- Hermes 状态回写异常。
- 未预期异常由 service 捕获后转为结构化失败。

`human_review_required` 是审查成功后的结论，不等于系统失败，也不等于 `blocked`。

### 1.13 本功能不负责

- 不新增真实策略。
- 不实现 GannStrategy、TrendStrategy、RiskControlStrategy。
- 不修改 `configs/strategies`。
- 不根据 K 线、支撑压力或指标自行判断 long/short。
- 不生成入场价、止损价、止盈价、仓位、杠杆。
- 不生成最终交易建议。
- 不调用真实大模型。
- 不调用交易接口。
- 不自动交易。
- 不修改正式 K 线表。
- 不接自动 scheduler。

### 1.14 测试

对应测试：

`tests/model_analysis/test_model_analysis_service.py`

覆盖内容：

- dry-run 不写库。
- `MODEL_REVIEW_ENABLED=false` 时 dry-run 可运行。
- `MODEL_REVIEW_ENABLED=false` 时 confirm-write 被 blocked。
- confirm-write 才写 `model_analysis_run` 和 `model_analysis_result`。
- 默认使用 mock provider。
- material pack 缺失和非 success 被 blocked。
- 输入和输出字符/字节超限被 blocked。
- mock 输出 schema 合法时 success。
- schema 非法或包含交易字段时 blocked。
- success 后重复运行 skipped / already_exists。
- blocked / failed attempt 不锁死后续重跑。
- 并发唯一冲突恢复为 skipped / already_exists。
- `human_review_required` 是 success 结论。
- Hermes 中文文案边界。
- run 表无 `review_version_key` 唯一约束。
- result 表有 `review_version_key` 唯一约束。
- 没有大复合 VARCHAR 唯一索引。
- N 个动态策略摘要会压缩。

默认 pytest 不请求真实外部服务，不连接真实模型，不发送真实 Hermes，不访问交易接口。

### 1.15 人工检查命令

```bash
python -m py_compile app/model_analysis/types.py
python -m py_compile app/model_analysis/service.py
python -m py_compile app/model_analysis/repository.py
python -m py_compile app/model_analysis/prompt_builder.py
python -m py_compile app/model_analysis/schema_validator.py
python -m py_compile app/model_analysis/providers/mock.py
python -m py_compile app/model_analysis/hermes_formatter.py
python -m py_compile scripts/run_model_analysis.py

python -m alembic upgrade head
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/scheduler -q
python -m pytest tests -q
```

## 2. 19 addendum 补充实现说明

### 2.1 模型配置注册表

新增配置目录：

```text
configs/model_review/
```

新增配置文件：

```text
configs/model_review/model_registry.yaml
configs/model_review/mock_review.yaml
```

加载链路：

```text
app/model_analysis/service.py::ModelAnalysisService._resolve_provider
    ↓
app/model_analysis/model_registry.py::load_enabled_model_review_configs
    ↓
app/model_analysis/model_registry.py::select_stage19a_mock_model_config
```

`MODEL_REVIEW_CONFIG_DIR` 只指定配置目录；具体模型启停由
`configs/model_review/*.yaml` 中的 `enabled` 控制。19A 只执行第一个
`enabled=true`、`provider=mock`、`analysis_mode=single` 的模型配置。
非 mock provider 可被配置文件识别为未来 provider，但 19A 不会执行真实调用。
找不到可执行 mock 配置时，service 返回 `blocked`，不会调用真实模型。

本功能不请求外部接口，不读取 Redis，不写正式 K 线表，不调用真实大模型，不自动交易。

### 2.2 横向对比与分析接力预留字段

新增安全修正迁移：

```text
migrations/versions/20260521_19a_model_review_registry_fields.py
```

`model_analysis_run` 追加字段：

```text
model_key
model_role
analysis_mode
chain_id
chain_step
parent_model_analysis_run_id
comparison_group_id
```

`analysis_mode` 当前只执行 `single`。`relay_chain` 和
`parallel_comparison` 只作为未来字段预留，19A 没有实现接力执行逻辑，
也没有实现横向对比执行逻辑。

新增索引都是单字段小索引：

```text
idx_model_analysis_run_model_key
idx_model_analysis_run_analysis_mode
idx_model_analysis_run_chain_id
idx_model_analysis_run_comparison_group_id
```

没有新增大复合 VARCHAR 唯一索引。`model_analysis_run.review_version_key`
仍然不是唯一约束。

### 2.3 human_review_required 落表

`model_analysis_result` 新增字段：

```text
human_review_required Boolean NOT NULL DEFAULT false
```

写入链路：

```text
app/model_analysis/schema_validator.py::validate_model_review_output
    ↓
app/model_analysis/payloads.py::build_result_payload
    ↓
app/model_analysis/repository.py::create_model_analysis_result
```

`human_review_required` 必须来自 schema 合法输出中的布尔字段。它表示审查成功后
需要人工进一步判断，不等于 `blocked`，也不等于 `failed`。
`blocked` / `failed` 仍只写 `model_analysis_run`，不写 `model_analysis_result`。
`success` / `partial_success` 才写最终结果表。

### 2.4 人工补充材料链路规则

19A 只记录 `human_review_required` 和 `human_review_questions`，不实现微信回复入口，
不实现自然语言解析 Skill，不实现确认流程，不新增 `human_review_input` 表。

未来人工补充材料链路约束如下：

```text
微信自然语言回复
    ↓
Skill 解析成结构化草稿
    ↓
微信发给用户确认
    ↓
用户确认 / 修改 / 取消
    ↓
系统业务校验
    ↓
正式入库
    ↓
绑定 review_id / material_pack_id / model_analysis_run_id
```

Skill 解析结果只是草稿，不是事实。未经用户确认，不得写入核心业务事实表。
