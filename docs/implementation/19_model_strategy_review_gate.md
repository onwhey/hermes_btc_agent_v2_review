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

---

## 4. 19B DeepSeek Provider 与模型版本档案

### 4.1 发起方式

用户仍然只能手动执行 CLI：

```bash
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --use-real-model --model-key deepseek_v4_pro_review --confirm-real-model-cost --dry-run
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --use-real-model --model-key deepseek_v4_pro_review --confirm-real-model-cost --confirm-write
```

本功能不接 scheduler。scheduler 不会触发真实模型调用。

### 4.2 核心调用链路

```text
scripts/run_model_analysis.py::main
    -> app/model_analysis/service.py::run_model_analysis
    -> app/model_analysis/service.py::ModelAnalysisService.run_model_analysis
    -> app/model_analysis/provider_resolution.py::resolve_provider_for_request
    -> app/model_analysis/model_registry.py::resolve_model_review_profile
    -> app/model_analysis/prompt_builder.py::build_model_review_prompt
    -> app/model_analysis/service.py::_enrich_provider_resolution_before_call
    -> app/model_analysis/repository.py::create_model_analysis_run
       (confirm-write 真实模型调用前先写 status=running)
    -> app/model_analysis/service.py::_write_request_artifact_or_return_failure
       (仅在 --capture-raw-request 或配置开启时执行)
    -> app/model_analysis/providers/deepseek.py::DeepSeekReviewProvider.call_review_model
    -> app/model_analysis/provider_response_parser.py::parse_openai_style_response
    -> app/model_analysis/schema_validator.py::validate_model_review_output
    -> app/model_analysis/repository.py::update_model_analysis_run
    -> app/model_analysis/repository.py::create_model_analysis_result
```

超长 raw response 需要隔离时追加：

```text
app/model_analysis/service.py::ModelAnalysisService._write_response_artifact_if_allowed
    -> app/model_analysis/artifact_store.py::write_model_provider_artifact
    -> app/model_analysis/repository.py::create_model_provider_call_artifact
```

artifact 写入失败时追加：

```text
app/model_analysis/service.py::ModelAnalysisService._return_artifact_write_failed
    -> app/model_analysis/repository.py::update_model_analysis_run
    -> app/model_analysis/hermes_formatter.py::build_model_analysis_artifact_write_failed_visible_body
    -> app/alerting/service.py::send_alert
```

真实 provider 请求失败时追加：

```text
app/model_analysis/service.py::ModelAnalysisService._return_or_persist_failed
    -> app/model_analysis/repository.py::create_model_analysis_run 或 update_model_analysis_run
    -> app/model_analysis/service.py::ModelAnalysisService._record_provider_failed_hermes_and_return
    -> app/model_analysis/service.py::ModelAnalysisService._send_or_skip_provider_failed_hermes
    -> app/model_analysis/hermes_formatter.py::build_model_analysis_provider_call_failed_visible_body
    -> app/alerting/service.py::send_alert
```

该路径是 provider 通用路径，不是 DeepSeek 专用路径。任何真实 provider adapter 抛出
`ProviderCallError`，都会由 service 统一转成 `error_code=provider_call_failed`，
confirm-write 时写入 run，且不写 `model_analysis_result`。

dry-run 路径不写数据库、不写 artifact、不发送 Hermes。对于超长 response、
artifact 写入失败模拟路径、provider_call_failed 等可告警场景，dry-run 只在返回结果中记录
`hermes_status=skipped_dry_run` 或等价跳过状态，不产生外部副作用。

### 4.3 配置与模型档案

读取配置：

- `MODEL_REVIEW_REAL_MODEL_ENABLED`
- `MODEL_REVIEW_ARTIFACT_DIR`
- `MODEL_REVIEW_CAPTURE_RAW_REQUEST`
- `MODEL_REVIEW_CAPTURE_RAW_RESPONSE`
- `MODEL_REVIEW_HERMES_ON_OVERSIZED_OUTPUT`
- `MODEL_REVIEW_HERMES_ENABLED`
- `MODEL_REVIEW_RAW_ARTIFACT_MAX_BYTES`
- `DEEPSEEK_API_KEY`

模型配置文件：

```text
configs/model_review/model_registry.yaml
configs/model_review/providers/deepseek.yaml
configs/model_review/profiles/deepseek/deepseek_v4_pro_review.yaml
configs/model_review/profiles/deepseek/deepseek_v4_flash_review.yaml
```

`model_registry.yaml` 只声明允许参与审查的 `model_key`。`providers/deepseek.yaml`
控制 provider 级开关和请求基础参数。每个 `profiles/deepseek/*.yaml`
只描述一个具体模型版本，并计算 `profile_hash` 入库。

每个真实模型 profile 必须包含：

- `profile_version`
- `docs_checked_at`
- `docs_source`
- `request_params.max_tokens`
- `request_params.response_format`

`profile_hash` 覆盖上述配置内容，并参与 `review_version_key`。新增或修改 profile 后，
`review_version_key` 会变化。通用 registry 只校验 provider 通用 profile 字段，
包括 `model_key`、`provider`、`enabled`、`api_style`、`model_name`、
`model_version`、`profile_version`、`model_role`、`analysis_mode`、
`prompt_template_version`、`review_schema_version`、`docs_checked_at`、
`docs_source`、`request_params`、`response_mapping` 和 `capabilities`。

DeepSeek thinking mode 的专属校验不写死在通用 registry 规则中，而是由
`app/model_analysis/providers/deepseek.py::validate_deepseek_model_profile` 执行。
当 `provider=deepseek` 且 `capabilities.thinking=true` 时，该校验要求：

- `request_params.reasoning_effort`
- `request_params.extra_body.thinking.type=enabled`
- `ignored_params_in_thinking_mode` 包含 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty`

这样后续 GPT、Claude 或其他 provider 不会被 DeepSeek 风格 thinking 参数误杀。

`model_key` 是系统内部唯一键，profile 文件名只是配置组织方式；`model_name` 是供应商
API 真实模型字符串。DeepSeek adapter 只使用 profile 中的 `model_name` 和
`request_params` 构造请求，不把 `model_key` 或 profile 文件名当作模型名传给供应商；
也不在 adapter 中写死业务模型名，不补业务关键默认参数。如果关键字段缺失，registry
或 DeepSeek profile 校验阶段会返回 blocked，不会默默依赖供应商默认值。

真实模型调用必须同时满足：`MODEL_REVIEW_REAL_MODEL_ENABLED=true`、provider
启用、profile 启用、registry 启用该 `model_key`、CLI 传入 `--use-real-model`、
CLI 传入 `--model-key`、CLI 传入 `--confirm-real-model-cost`、API key 存在、
material pack 可审查、schema 校验通过。任一条件不满足，不调用真实模型。

`deepseek_v4_pro_review` 默认启用。`deepseek_v4_flash_review` 默认关闭。

### 4.4 数据库写入

迁移文件：

```text
migrations/versions/20260523_19b_deepseek_provider_fields.py
```

`model_analysis_run` 新增 provider / profile / token / 成本 / hash / artifact 引用字段，
包括 `model_key`、`provider`、`model_name`、`model_version`、`profile_version`、
`profile_hash`、`api_style`、`input_token_count`、`output_token_count`、
`total_token_count`、`estimated_cost`、`cost_currency`、`raw_response_hash`、
`raw_response_char_count`、`raw_response_byte_count`、`raw_response_storage_ref`、
`raw_request_hash`、`raw_request_storage_ref`。

新增表：

```text
model_provider_call_artifact
```

该表只保存 artifact 引用、hash、长度、capture_reason 和模型档案摘要。完整 raw
request / raw response 不进入主业务表。默认也不保存完整 raw request、完整 raw
response 或 reasoning 内容到数据库主表。

真实模型 confirm-write 的审计顺序是：

```text
校验 material_pack / provider / profile / registry / env / CLI 成本确认
    -> 构造 review_version_key
    -> create_model_analysis_run(status=running)
    -> 可选写 raw_request artifact
    -> 调用 DeepSeek
    -> update_model_analysis_run(status=success / blocked / failed)
    -> success 且 schema 合格时 create_model_analysis_result
```

如果 DeepSeek 调用期间进程崩溃，数据库至少保留 `status=running` 的 attempt 记录。
dry-run 不写数据库，但 CLI 输出会包含 `provider`、`model_key`、`model_name`、
`profile_hash` 和 `review_version_key`，方便人工确认即将使用的模型档案。

`model_analysis_run.review_version_key` 仍然不是唯一约束。`model_analysis_result.review_version_key`
仍然是单字段唯一约束。未新增多个 VARCHAR 组成的大复合唯一索引。

### 4.5 超长 response 与 Hermes

raw response 超过 `MODEL_REVIEW_MAX_OUTPUT_CHARS` 或
`MODEL_REVIEW_MAX_OUTPUT_BYTES` 时，不写入主业务表。如果结构化 JSON 仍可提取且 schema
合规，审查结果可以正常写入，raw response 只记录 hash、长度和 artifact 引用。如果无法安全
生成统一审查结果，则 `model_analysis_run.status=blocked`，不写
`model_analysis_result`。

超长 response 会调用：

```text
app/model_analysis/hermes_formatter.py::build_model_analysis_oversized_response_visible_body
```

Hermes 文案为中文，明确说明这不是最终交易建议，未自动交易，且包含 `model_key`、
provider、`model_name`、`material_pack_id`、`model_analysis_run_id`、raw response
长度和 `trace_id`。Hermes 失败只记录 `hermes_status=failed`，不把主审查结果改为
failed。

如果 raw request / raw response artifact 本地写入或 artifact 元数据落库失败，service
会返回：

```text
status = failed
error_code = artifact_write_failed
```

已存在的 running run 会被更新为 failed，并记录 `trace_id`、`error_code`、
`error_message`。raw response 场景会同时记录 `raw_response_hash`、
`raw_response_char_count`、`raw_response_byte_count` 和已知的 artifact 引用信息。
失败路径不写 `model_analysis_result`，也不会把完整 raw response、完整 raw request
或完整 reasoning_content 塞进主业务表。

artifact 失败 Hermes 调用：

```text
app/model_analysis/hermes_formatter.py::build_model_analysis_artifact_write_failed_visible_body
```

中文告警明确说明“BTC 大模型审查 artifact 写入失败”“模型返回未能完整隔离保存”、
是否生成正式审查结果、`model_key`、`material_pack_id`、`model_analysis_run_id`、
`trace_id`，并声明这不是最终交易建议、未自动交易。

真实 provider 请求失败时，service 捕获 `ProviderCallError` 并统一记录：

```text
status = failed
error_code = provider_call_failed
```

run 会记录 `trace_id`、provider、`model_key`、`model_name`、`material_pack_id`、
`review_version_key` 和错误摘要。confirm-write 且 `MODEL_REVIEW_HERMES_ENABLED=true`
时会发送中文 Hermes 告警，标题为“BTC 大模型请求失败”，正文包含 provider、
`model_key`、`model_name`、`material_pack_id`、`model_analysis_run_id`、
`error_code`、错误摘要、`trace_id`，并明确“未生成正式审查结果”“不是最终交易建议，
未自动交易”。该路径不写 `model_analysis_result`。

Hermes 发送本身失败时，不把 provider 失败改写为成功；service 会记录
`hermes_status=failed` 和错误摘要。dry-run 下不会发送 Hermes，只返回
`hermes_status=skipped_dry_run`。

### 4.6 本功能不负责

- 不实现真实交易策略。
- 不生成最终交易建议。
- 不生成入场价、止损价、止盈价、仓位、杠杆。
- 不调用交易接口。
- 不自动交易。
- 不接 scheduler。
- 不修改正式 K 线表。
- 不实现横向对比执行逻辑。
- 不实现分析接力执行逻辑。
- 不实现微信人工补充材料入口。

### 4.7 测试

对应测试：

```text
tests/model_analysis/test_model_analysis_19b.py
tests/model_analysis/test_model_analysis_service.py
```

测试覆盖 provider/profile/registry 门控、成本确认、API key 缺失、fake DeepSeek
client、profile_hash 入库、review_version_key 纳入 profile_hash、token 与成本记录、
raw response hash 和长度记录、超长 response artifact、Hermes 超长中文提醒、schema
禁止交易字段、dry-run 不写库、confirm-write 才写库、真实模型调用前 running run、
provider 异常后 failed run、artifact 写入失败 error_code、artifact 失败中文 Hermes、
raw request artifact、DeepSeek thinking mode profile 字段和 ignored params、通用 registry
不会用 DeepSeek thinking 规则误杀非 DeepSeek provider、DeepSeek provider/profile
校验仍要求显式 thinking mode、dry-run 下 provider_call_failed / oversized response /
artifact_write_failed 不发送 Hermes、confirm-write 下 provider_call_failed 发送通用中文
Hermes 告警、provider_call_failed 不写 result、DeepSeek 请求 payload 使用
profile.model_name 而不是 model_key。默认 pytest
不访问外网，不请求真实 DeepSeek，不发送真实 Hermes，不访问交易接口，不修改正式 K 线表。

## 3. 19A partial_success 准入修正

### 3.1 发起方式

仍由用户手动执行：

```bash
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --dry-run
```

本修正不新增 scheduler 入口，不新增真实模型调用，不修改正式 K 线表。

### 3.2 核心调用链路

```text
scripts/run_model_analysis.py::main
    ↓
app/model_analysis/service.py::run_model_analysis
    ↓
app/model_analysis/service.py::ModelAnalysisService.run_model_analysis
    ↓
app/model_analysis/service.py::_validate_material_pack_reviewability
    ↓
app/model_analysis/service.py::_validate_partial_success_material_pack
```

### 3.3 material pack 准入规则

`analysis_material_pack.status=success` 直接允许进入 19A mock review。

`analysis_material_pack.status=partial_success` 只有在核心材料完整时允许进入：

- `material_json` 不为空；
- `summary_json` 不为空；
- `validation_plan_json` 不为空；
- `data_window_json` 不为空；
- `future_leakage_guard_json` 不为空；
- `question_json` / `question_list_json` / `stage19_question_json` 或 `material_json.question_list_for_stage19` 不为空；
- `snapshot_id` 不为空；
- `strategy_signal_run_id` 不为空；
- `failed_strategy_count = 0`；
- `invalid_strategy_count = 0`；
- `effective_strategy_count >= 1`。

因 placeholder / not_implemented 策略导致的 `partial_success` 不会自动阻止 19A；
只要核心材料完整且 failed / invalid 数量为 0，就可以进入审查。

### 3.4 blocked 路径

状态为 `failed`、`blocked`、`skipped`、`running`、`pending` 或未知状态时返回：

```text
error_code = material_pack_status_not_reviewable
message = analysis_material_pack status is not reviewable.
```

`partial_success` 核心字段不完整时返回：

```text
error_code = material_pack_partial_core_incomplete
message = analysis_material_pack partial_success is not reviewable because core material is incomplete.
```

`partial_success` 中存在 failed / invalid 策略材料时返回：

```text
error_code = material_pack_partial_failed_or_invalid_strategy
message = analysis_material_pack partial_success is not reviewable because strategy material contains failed or invalid results.
```

### 3.5 数据库默认值修正

新增迁移：

```text
migrations/versions/20260522_19a_model_analysis_run_human_review_default.py
```

该迁移只把 `model_analysis_run.human_review_required` 的数据库默认值修正为 false。
`model_analysis_run` 是 attempt 表，blocked / failed / skipped / success 都可能记录在这里，
所以默认不应表示需要人工审核。

`model_analysis_result.human_review_required` 保持默认 false，成功审查结果仍可写入 true。
