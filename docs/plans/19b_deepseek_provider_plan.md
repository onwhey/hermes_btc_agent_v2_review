# 第 19B 阶段计划：真实大模型 Provider 接入与模型版本档案架构


> Codex 开工前必须同时阅读：
>
> - `docs/plans/19_model_strategy_review_gate_plan.md`
> - `docs/plans/19_model_strategy_review_gate_addendum.md`
> - `docs/plans/19b_deepseek_provider_plan.md`
>
> 三者共同构成第 19 阶段完整上下文。任何一个缺失，都不应继续实现。

---

## 1. 阶段定位

第 19A 已完成：

- `mock provider`
- 模型配置注册表雏形
- `model_analysis_run` / `model_analysis_result`
- `human_review_required`
- `partial_success` 材料包准入
- CLI 手动触发
- 不生成交易建议

第 19B 的目标是：

```text
在不改变 19A 安全边界的前提下，接入真实大模型 Provider。
第一轮只实现 DeepSeek Provider 的真实调用能力，并同时补强“Provider Adapter + Model Profile”架构。
```

第 19B 不是策略开发阶段。它不负责判断多空，也不负责生成最终交易建议。

---

## 2. 阶段核心目标

19B 要完成：

1. 建立正式的 `Provider Adapter + Model Profile` 架构；
2. 支持 provider 级开关和 profile 级开关；
3. 支持 DeepSeek 真实 API 调用；
4. 默认主模型 profile 使用更高质量模型，例如 `deepseek_v4_pro_review`；
5. 保留 `deepseek_v4_flash_review` 作为可选低成本备用 profile，但默认关闭；
6. 所有真实模型调用必须经过多重门控；
7. 真实模型输出必须经过 schema 校验；
8. 不允许模型输出交易字段；
9. 记录 token、成本、模型版本、profile hash、请求 hash、响应 hash；
10. 默认不把完整 raw request / raw response 写入主表；
11. 模型返回过长时，不允许静默丢弃，必须隔离保存并 Hermes 提醒；
12. CLI 仍然只支持手动触发；
13. 不接 scheduler；
14. 不做横向对比；
15. 不做分析接力；
16. 不做微信人工回复入口。

---

## 3. 禁止事项

本阶段禁止：

1. 不要新增真实交易策略；
2. 不要实现 `GannStrategy`、`TrendStrategy`、`RiskControlStrategy` 等真实策略；
3. 不要生成最终交易建议；
4. 不要输出入场价、止损价、止盈价、仓位、杠杆；
5. 不要调用交易接口；
6. 不要自动交易；
7. 不要修改正式 K 线表；
8. 不要新增 scheduler 自动触发；
9. 不要实现横向对比执行逻辑；
10. 不要实现分析接力执行逻辑；
11. 不要实现微信自然语言人工补充入口；
12. 不要把完整 prompt、完整 request、完整 raw response、完整 reasoning 内容塞进主业务表；
13. 不要假设切换模型版本只需要修改 `model_name`；
14. 不要把 DeepSeek 所有版本塞进一个大 YAML 文件；
15. 不要执行 `git checkout`、`git switch`、创建分支、合并分支等 Git 分支操作。

---

## 4. 为什么 19B 必须补强模型版本架构

大模型供应商和模型版本是一对多关系。

例如：

```text
DeepSeek
  - deepseek_v4_pro_review
  - deepseek_v4_flash_review
  - deepseek_reasoner_review，如未来仍需兼容

OpenAI / GPT
  - gpt_53_review
  - gpt_54_review
  - gpt_55_review
```

不同版本之间可能存在差异：

- 请求 API 风格不同；
- 参数名称不同；
- 支持的推理参数不同；
- 是否支持 JSON 输出不同；
- 是否返回 reasoning 内容不同；
- token usage 路径不同；
- 价格不同；
- 最大输入 / 输出限制不同；
- 是否支持工具调用不同；
- response 字段路径不同。

因此不能把模型接入设计成：

```text
provider = deepseek
model_name = xxx
```

然后以为以后只改 `model_name` 就能切换版本。

第 19B 必须改成：

```text
Provider Adapter 处理供应商接口差异。
Model Profile 描述每个具体模型/版本的能力、参数、返回映射和成本策略。
业务代码只通过 model_key 调用统一模型审查服务。
```

---

## 5. 推荐配置目录结构

新增或调整：

```text
configs/model_review/
  model_registry.yaml

  providers/
    deepseek.yaml
    openai.yaml
    claude.yaml

  profiles/
    deepseek/
      deepseek_v4_pro_review.yaml
      deepseek_v4_flash_review.yaml
      deepseek_reasoner_review.yaml

    openai/
      gpt_53_review.yaml
      gpt_54_review.yaml
      gpt_55_review.yaml

    claude/
      claude_xxx_review.yaml

  prompts/
    review_gate_v1.md
```

本阶段只需要真实实现 DeepSeek provider。

OpenAI / Claude 目录可以预留，但不要实现真实调用。

---

## 6. Provider 级开关

每个供应商有自己的配置文件。

示例：`configs/model_review/providers/deepseek.yaml`

```yaml
provider: deepseek
enabled: true
api_base_url: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
timeout_seconds: 60
max_retries: 1
retry_backoff_seconds: 2
```

示例：`configs/model_review/providers/openai.yaml`

```yaml
provider: openai
enabled: false
api_base_url: https://api.openai.com
api_key_env: OPENAI_API_KEY
timeout_seconds: 60
max_retries: 1
retry_backoff_seconds: 2
```

规则：

```text
provider.enabled=false 时，该 provider 下所有 profile 都不可用。
```

这允许用户一键关闭整个供应商。

例如：

```text
关闭 OpenAI/GPT 整体：openai.yaml enabled=false
保留 DeepSeek：deepseek.yaml enabled=true
```

---

## 7. Profile 级开关

每个具体模型版本一个 profile 文件。

不要把一个供应商的所有版本塞进同一个大 YAML。

示例：`configs/model_review/profiles/deepseek/deepseek_v4_pro_review.yaml`

```yaml
model_key: deepseek_v4_pro_review
provider: deepseek
enabled: true

api_style: openai_chat_completion
model_name: deepseek-v4-pro
model_version: v4_pro
profile_version: profile_v1

model_role: mathematical_structure_review
analysis_mode: single

prompt_template_version: review_gate_v1
review_schema_version: review_schema_v1

capabilities:
  json_output: true
  thinking: true
  reasoning_content: true
  function_calling: false
  streaming: false

request_params:
  temperature: 0.2
  top_p: 1
  max_tokens: 4096
  response_format:
    type: json_object
  reasoning_effort: high

response_mapping:
  final_content_path: choices.0.message.content
  reasoning_content_path: choices.0.message.reasoning_content
  usage_path: usage
  finish_reason_path: choices.0.finish_reason
  provider_request_id_path: id

unsupported_params:
  - tools
  - function_call

cost_policy:
  track_token_usage: true
  require_cost_confirmation: true
  currency: USD
```

示例：`configs/model_review/profiles/deepseek/deepseek_v4_flash_review.yaml`

```yaml
model_key: deepseek_v4_flash_review
provider: deepseek
enabled: false

api_style: openai_chat_completion
model_name: deepseek-v4-flash
model_version: v4_flash
profile_version: profile_v1

model_role: fast_low_cost_review
analysis_mode: single

prompt_template_version: review_gate_v1
review_schema_version: review_schema_v1

capabilities:
  json_output: true
  thinking: true
  reasoning_content: true
  function_calling: false
  streaming: false

request_params:
  temperature: 0.2
  top_p: 1
  max_tokens: 4096
  response_format:
    type: json_object
  reasoning_effort: high

response_mapping:
  final_content_path: choices.0.message.content
  reasoning_content_path: choices.0.message.reasoning_content
  usage_path: usage
  finish_reason_path: choices.0.finish_reason
  provider_request_id_path: id

unsupported_params:
  - tools
  - function_call

cost_policy:
  track_token_usage: true
  require_cost_confirmation: true
  currency: USD
```

规则：

```text
profile.enabled=false 时，该模型版本不可用。
```

这允许用户只关闭某个版本，而保留同 provider 下其他版本。

例如：

```text
关闭 gpt_54_review
保留 gpt_55_review
```

---

## 8. Registry 选择规则

`configs/model_review/model_registry.yaml` 负责声明哪些 `model_key` 参与当前审查体系。

示例：

```yaml
default_mode: single

enabled_models:
  - mock_review
  - deepseek_v4_pro_review

manual_only_models:
  - deepseek_v4_pro_review

future_modes:
  relay_chain: false
  parallel_comparison: false
```

注意：

```text
registry 中列出 model_key，不代表一定可用。
```

最终可用必须同时满足：

1. `MODEL_REVIEW_REAL_MODEL_ENABLED=true`；
2. provider 配置存在；
3. `provider.enabled=true`；
4. profile 配置存在；
5. `profile.enabled=true`；
6. `model_key` 在 registry 中启用；
7. CLI 显式传入 `--use-real-model`；
8. CLI 显式传入 `--model-key <model_key>`；
9. CLI 显式传入 `--confirm-real-model-cost`；
10. API key 存在；
11. 当前 provider adapter 已实现；
12. profile 的 `api_style` 被当前 adapter 支持；
13. 材料包通过 19A 准入规则。

缺一个都不能调用真实模型。

---

## 9. 默认主模型策略

第 19B 默认真实模型 profile 应选择高质量模型，而不是低成本模型。

建议：

```text
默认主模型：deepseek_v4_pro_review
备用模型：deepseek_v4_flash_review，默认 enabled=false
```

理由：

```text
本项目的成本核心不是 token，而是错误审查导致策略判断偏差。
策略审查错误的代价远高于少量 token 成本。
```

但代码不得写死 `deepseek-v4-pro`。

实际模型名称必须从 profile 读取：

```text
model_key -> profile -> model_name
```

如果 DeepSeek 后续新增更高版本，只新增新的 profile 文件，不改核心业务代码。

---

## 10. 代码结构建议

新增或调整：

```text
app/model_analysis/
  model_registry.py
  model_profile.py
  artifact_store.py

  providers/
    base.py
    deepseek.py
    mock.py

  provider_response_parser.py
  cost_estimator.py
  prompt_builder.py
  schema_validator.py
  service.py
  repository.py
```

职责：

### 10.1 `model_registry.py`

负责：

- 读取 `model_registry.yaml`；
- 加载 provider 配置；
- 加载 profile 配置；
- 应用 provider 级开关；
- 应用 profile 级开关；
- 根据 `model_key` 返回可用 profile；
- 生成 `profile_hash`；
- 校验必要字段。

### 10.2 `model_profile.py`

负责定义模型 profile 数据结构。

字段至少包括：

```text
model_key
provider
enabled
api_style
model_name
model_version
profile_version
profile_hash
model_role
analysis_mode
prompt_template_version
review_schema_version
capabilities
request_params
response_mapping
unsupported_params
cost_policy
```

### 10.3 `providers/base.py`

定义 Provider Adapter 接口。

建议方法：

```python
class ModelProviderAdapter:
    def call_review_model(self, request: ProviderRequest) -> ProviderResponse:
        ...
```

### 10.4 `providers/deepseek.py`

负责 DeepSeek 真实调用。

它不负责业务判断，只负责：

- 根据 profile 构造请求；
- 调用 DeepSeek API；
- 处理 timeout；
- 处理 retry；
- 返回统一 ProviderResponse；
- 不泄露 API key；
- 不把完整 response 写主表。

### 10.5 `provider_response_parser.py`

负责根据 profile.response_mapping 解析 provider 原始返回。

它要提取：

- final content；
- reasoning 内容是否存在；
- reasoning 字符数 / 字节数；
- usage；
- finish_reason；
- provider_request_id；
- metadata 摘要。

### 10.6 `schema_validator.py`

负责统一结果校验。

不论哪个模型返回，都必须变成同一个 schema。

禁止字段：

```text
entry_price
stop_loss
take_profit
position_size
leverage
order_type
final_advice
buy_now
sell_now
```

发现禁止字段必须 blocked 或 schema_invalid。

---

## 11. 数据库设计原则

不同模型版本返回不同，数据库不为每个版本单独新增固定字段。

主表只存：

```text
统一审查结果
模型调用元信息
摘要
hash
长度
token
成本
artifact 引用
```

不要存：

```text
完整 raw request
完整 raw response
完整 reasoning_content
完整 prompt
```

---

## 12. 建议数据库迁移

新增 Alembic migration，例如：

```text
202605xx_19b_add_real_model_provider_fields.py
```

### 12.1 `model_analysis_run` 建议新增字段

```text
api_style
profile_version
profile_hash
provider_request_id
finish_reason

prompt_template_hash
rendered_prompt_hash
request_payload_hash
raw_request_hash
raw_response_hash

request_char_count
request_byte_count
raw_response_char_count
raw_response_byte_count
parsed_content_char_count
parsed_content_byte_count

input_token_count
output_token_count
total_token_count
estimated_cost
cost_currency

request_params_summary_json
capabilities_json
response_metadata_summary_json
provider_usage_json
provider_extra_summary_json

raw_request_storage_ref
raw_response_storage_ref
artifact_status
```

如果字段过多，可拆分出 `model_provider_call_log`，但不要把不同模型版本的特殊字段硬塞成大量固定列。

### 12.2 `model_analysis_result` 保持统一结果字段

`model_analysis_result` 继续只保存统一审查结果，例如：

```text
review_decision
evidence_quality
logic_consistency
risk_acceptability
strategy_conflict_level
human_review_required
missing_evidence_json
risk_warnings_json
human_review_questions_json
validation_focus_json
summary_text
not_trading_advice_text
```

不为 DeepSeek / GPT / Claude 单独加专属字段。

### 12.3 可选新增 artifact 表

为了满足“超长内容不能丢弃”，建议 19B 新增：

```text
model_analysis_artifact
```

字段建议：

```text
id
artifact_id
model_analysis_run_id
artifact_type
provider
model_key
model_name
model_version
profile_hash
storage_ref
sha256_hash
char_count
byte_count
capture_reason
created_at_utc
```

`artifact_type` 可选：

```text
raw_request
raw_response
reasoning_content
oversized_response
oversized_content
```

前期可以使用本地文件存储：

```text
runtime/model_review_artifacts/YYYYMMDD/<artifact_id>.json
```

该目录必须加入 `.gitignore`。

---

## 13. raw request 存储规则

默认不保存完整 raw request。

默认保存：

```text
request_payload_hash
rendered_prompt_hash
prompt_template_hash
request_char_count
request_byte_count
request_params_summary_json
```

原因：

```text
raw request 可由 material_pack + prompt_template + profile + request_params 确定性重建。
不应重复塞入主表。
```

如果未来需要完整请求审计，应通过 artifact 机制保存，不进主表。

---

## 14. raw response 存储规则

默认不保存完整 raw response 到主表。

默认保存：

```text
raw_response_hash
raw_response_char_count
raw_response_byte_count
response_metadata_summary_json
provider_usage_json
provider_extra_summary_json
```

如果 raw response 很长但可成功提取合格结构化结果：

```text
model_analysis_result 正常写入。
raw response 不进主表。
可根据配置决定是否 artifact 保存。
```

如果 raw response 或 parsed content 过长，导致无法安全写入结果表：

```text
不能丢弃。
不能硬塞主表。
必须隔离保存 artifact。
必须 Hermes 提醒。
run.status = blocked。
error_code = model_output_too_large。
不写 model_analysis_result。
```

---

## 15. 超长返回处理规则

延续当前数据库安全规则：

```text
任一待入库文本字段超过 10000 字符或 32KB，不能写入主业务表。
```

但真实模型返回超长时，不能静默丢弃。

处理流程：

```text
收到模型返回
  ↓
统计 raw response char / byte
  ↓
尝试提取 final content
  ↓
统计 final content char / byte
  ↓
如果超长：写 artifact，记录 hash/ref/长度
  ↓
更新 model_analysis_run.status=blocked
  ↓
error_code=model_output_too_large
  ↓
Hermes 必须提醒
  ↓
不写 model_analysis_result
```

Hermes 提醒必须包含：

```text
标题：BTC 大模型审查返回过长
model_key
provider
model_name
material_pack_id
model_analysis_run_id
输出字符数
输出字节数
artifact_id 或 storage_ref
处理结果：已阻断 / 已隔离保存 / 未生成正式审查结果
trace_id
```

如果 Hermes 发送失败：

```text
不能静默吞掉。
必须记录 hermes_status=failed。
CLI 必须输出告警信息。
日志必须包含 trace_id。
```

如果未来频繁触发超长：

```text
说明 prompt、schema、摘要策略、字段长度、拆表策略或 artifact 机制可能需要维护调整。
这属于后期维护项，不在 19B 初始阶段直接放宽所有字段长度。
```

---

## 16. 真实调用门控

真实模型调用必须同时满足：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED=true
MODEL_REVIEW_ENABLED=true
provider.enabled=true
profile.enabled=true
model_registry.yaml 中启用该 model_key
CLI 带 --use-real-model
CLI 带 --model-key <model_key>
CLI 带 --confirm-real-model-cost
API key 存在
provider adapter 已实现
api_style 被支持
material pack 可审查
```

否则必须 blocked，不得调用真实模型。

CLI 示例：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --model-key deepseek_v4_pro_review \
  --use-real-model \
  --confirm-real-model-cost \
  --confirm-write
```

不允许只靠 `.env` 一开就自动花钱。

---

## 17. `.env` 配置建议

新增：

```env
MODEL_REVIEW_REAL_MODEL_ENABLED=false
MODEL_REVIEW_CONFIG_DIR=configs/model_review
MODEL_REVIEW_ARTIFACT_DIR=runtime/model_review_artifacts
MODEL_REVIEW_CAPTURE_RAW_REQUEST=false
MODEL_REVIEW_CAPTURE_RAW_RESPONSE=false
MODEL_REVIEW_HERMES_ON_OVERSIZED_OUTPUT=true
MODEL_REVIEW_RAW_ARTIFACT_MAX_BYTES=1048576
DEEPSEEK_API_KEY=
```

保留：

```env
MODEL_REVIEW_ENABLED=false
MODEL_REVIEW_DRY_RUN=true
MODEL_REVIEW_PROVIDER=mock
MODEL_REVIEW_MAX_INPUT_CHARS=10000
MODEL_REVIEW_MAX_OUTPUT_CHARS=10000
MODEL_REVIEW_MAX_INPUT_BYTES=32768
MODEL_REVIEW_MAX_OUTPUT_BYTES=32768
MODEL_REVIEW_HERMES_ENABLED=false
```

注意：

```text
MODEL_REVIEW_ENABLED=false 时，dry-run 仍可运行。
confirm-write 真实写库必须要求 MODEL_REVIEW_ENABLED=true。
真实模型调用还要额外要求 MODEL_REVIEW_REAL_MODEL_ENABLED=true。
```

---

## 18. Prompt 构造规则

19B 不直接把完整 `analysis_material_pack.material_json` 全量塞给模型。

必须沿用摘要压缩原则：

```text
只发送模型审查需要的材料摘要。
保留关键证据、风险、冲突、问题列表、边界说明。
不发送完整 K 线数组。
不发送大段 debug。
```

prompt 必须明确：

1. 你不是交易员；
2. 你不能给最终交易建议；
3. 你不能给入场价、止损价、止盈价、仓位、杠杆；
4. 你只能审查材料完整性、证据充分性、逻辑自洽性、风险可接受度、冲突程度、是否需要人工审核；
5. 输出必须是 JSON；
6. `not_trading_advice` 必须为 `true`；
7. 如果证据不足，输出 `require_more_evidence` 或 `wait`，不得编造结论。

---

## 19. 模型输出统一 Schema

真实模型输出必须满足 19A 已有 schema，并至少包含：

```text
review_decision
evidence_quality
logic_consistency
risk_acceptability
strategy_conflict_level
missing_evidence
risk_warnings
human_review_questions
validation_focus
human_review_required
not_trading_advice
```

`not_trading_advice` 必须为 `true`。

`human_review_required` 必须为 boolean。

禁止出现：

```text
entry_price
stop_loss
take_profit
position_size
leverage
order_type
final_advice
buy_now
sell_now
```

出现禁止字段：

```text
status=blocked
error_code=model_output_contains_forbidden_trading_fields
不写 model_analysis_result
Hermes 可选提醒
```

---

## 20. 成本和 token 记录

无论用户是否关心 token 成本，都必须记录调用消耗。

原因不是省钱，而是用于复盘、监控和异常识别。

必须记录：

```text
input_token_count
output_token_count
total_token_count
estimated_cost
cost_currency
provider_usage_json
```

如果 provider 没返回 usage：

```text
usage_source = unavailable
estimated_cost = null
provider_usage_json 记录原因
```

不能因为拿不到 usage 就让调用失败，除非 cost_policy 明确要求必须有 usage。

---

## 21. 状态规则

### success

满足：

```text
真实模型调用成功
输出可解析
schema 合法
无禁止交易字段
长度合格
model_analysis_result 写入成功
```

### blocked

包括：

```text
真实模型门控条件不满足
provider disabled
profile disabled
model_key 未启用
API key 缺失
material pack 不可审查
模型输出超长
schema 不合法
输出包含禁止交易字段
artifact 必需但保存失败
```

### failed

包括：

```text
数据库写入失败
Provider 网络异常且重试后失败
未预期异常
Hermes 发送异常导致关键告警无法记录
```

注意：

```text
human_review_required 不是 failed。
human_review_required 是成功审查结果中的人工介入标记。
```

---

## 22. Hermes 规则

普通成功审查是否发 Hermes，遵循：

```text
MODEL_REVIEW_HERMES_ENABLED
```

但模型返回过长必须触发 Hermes 告警流程：

```text
MODEL_REVIEW_HERMES_ON_OVERSIZED_OUTPUT=true
```

如果该配置为 true，但 Hermes 发送失败：

```text
run.hermes_status = failed
日志必须记录 trace_id
CLI 必须打印告警
```

Hermes 中文文案必须明确：

```text
这不是最终交易建议。
本阶段未自动交易。
本阶段未生成订单。
本阶段未给出仓位或杠杆。
```

---

## 23. 19B CLI 行为

继续使用：

```text
scripts/run_model_analysis.py
```

新增参数：

```text
--model-key
--use-real-model
--confirm-real-model-cost
--capture-raw-response
--capture-raw-request
```

规则：

1. 默认仍然 dry-run；
2. 默认仍然 mock；
3. 不传 `--use-real-model` 不调用真实模型；
4. 不传 `--confirm-real-model-cost` 不调用真实模型；
5. 不传 `--model-key` 不调用真实模型；
6. `--capture-raw-response` 默认不允许超过大小限制；
7. 超长异常捕获不依赖 `--capture-raw-response`，必须隔离保存；
8. 不接 scheduler。

---

## 24. 冒烟测试脚本

建议新增：

```text
scripts/check_model_provider.py
```

用途：

```text
只检查 provider/profile/API key/网络/最小调用是否可用。
不读取 material_pack。
不写 model_analysis_result。
不生成交易建议。
```

示例：

```bash
python -m scripts.check_model_provider \
  --model-key deepseek_v4_pro_review \
  --use-real-model \
  --confirm-real-model-cost
```

冒烟测试也必须经过门控，不允许误调用。

---

## 25. 测试要求

新增或修改 `tests/model_analysis/`。

必须覆盖：

### 25.1 配置与 profile

1. provider.enabled=false 时，profile 不可用；
2. profile.enabled=false 时，该版本不可用；
3. registry 未启用 model_key 时不可用；
4. provider enabled + profile enabled + registry enabled 时才可用；
5. 缺少 required profile 字段时 blocked；
6. profile_hash 稳定生成；
7. 修改 profile 后 profile_hash 变化；
8. `deepseek_v4_pro_review` 可作为默认真实 profile；
9. `deepseek_v4_flash_review` 默认 disabled。

### 25.2 真实调用门控

1. 未传 `--use-real-model` 不调用真实模型；
2. 未传 `--confirm-real-model-cost` 不调用真实模型；
3. 未传 `--model-key` 不调用真实模型；
4. `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 不调用真实模型；
5. API key 缺失时 blocked；
6. provider adapter 未实现时 blocked；
7. api_style 不支持时 blocked。

### 25.3 DeepSeek adapter

1. 能根据 profile 构造请求；
2. 不把 API key 写入日志；
3. timeout 可处理；
4. retry 可处理；
5. mock HTTP response 可解析 content；
6. mock HTTP response 可解析 usage；
7. mock HTTP response 可解析 finish_reason；
8. mock HTTP response 可解析 provider_request_id；
9. reasoning_content 只存摘要，不进主表。

### 25.4 schema 与安全

1. 合法 JSON 输出可写入 result；
2. 非 JSON 输出 blocked；
3. 缺少必填字段 blocked；
4. `human_review_required` 非 boolean blocked；
5. `not_trading_advice=false` blocked；
6. 输出包含 `entry_price` blocked；
7. 输出包含 `stop_loss` blocked；
8. 输出包含 `take_profit` blocked；
9. 输出包含 `position_size` blocked；
10. 输出包含 `leverage` blocked；
11. 不生成最终交易建议；
12. 不修改 K 线表。

### 25.5 长度与 artifact

1. raw response 超过限制时 blocked；
2. parsed content 超过限制时 blocked；
3. summary_text 超过限制时 blocked；
4. 超长 response 会写 artifact；
5. artifact 写入后 run 记录 hash/ref/长度；
6. 超长 response 必须触发 Hermes 告警流程；
7. Hermes 发送失败时记录 hermes_status=failed；
8. 不允许静默丢弃超长 response；
9. 不允许强行写入 model_analysis_result。

### 25.6 数据库

1. model_analysis_run 记录 profile_version/profile_hash/api_style；
2. model_analysis_run 记录 token usage；
3. model_analysis_run 记录 cost；
4. model_analysis_run 记录 request/response hash；
5. model_analysis_result 只存统一审查结果；
6. 不为 DeepSeek 专门增加 result 字段；
7. 不出现大复合 VARCHAR 唯一索引；
8. run 表 review_version_key 仍非唯一；
9. result 表 review_version_key 仍唯一；
10. blocked / failed 不锁死后续 success 重跑。

---

## 26. 验收命令

完成后运行：

```bash
python -m py_compile app/model_analysis/model_profile.py
python -m py_compile app/model_analysis/model_registry.py
python -m py_compile app/model_analysis/artifact_store.py
python -m py_compile app/model_analysis/providers/base.py
python -m py_compile app/model_analysis/providers/deepseek.py
python -m py_compile app/model_analysis/provider_response_parser.py
python -m py_compile app/model_analysis/cost_estimator.py
python -m py_compile app/model_analysis/service.py
python -m py_compile app/model_analysis/repository.py
python -m py_compile app/model_analysis/schema_validator.py
python -m py_compile scripts/run_model_analysis.py
python -m py_compile scripts/check_model_provider.py

python -m alembic upgrade head
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/scheduler -q
```

如果时间允许：

```bash
python -m pytest tests -q
```

---

## 27. 手动真实调用验证

在测试环境中，确认 `.env`：

```env
MODEL_REVIEW_ENABLED=true
MODEL_REVIEW_REAL_MODEL_ENABLED=true
DEEPSEEK_API_KEY=真实 key
```

先跑冒烟测试：

```bash
python -m scripts.check_model_provider \
  --model-key deepseek_v4_pro_review \
  --use-real-model \
  --confirm-real-model-cost
```

再跑真实审查 dry-run：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --model-key deepseek_v4_pro_review \
  --use-real-model \
  --confirm-real-model-cost \
  --dry-run
```

再跑 confirm-write：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --model-key deepseek_v4_pro_review \
  --use-real-model \
  --confirm-real-model-cost \
  --confirm-write
```

预期：

```text
status=success 或 blocked
如果 success：model_analysis_result 写入统一审查结果
如果 blocked：必须有明确 error_code/message
不得生成交易建议
不得生成入场/止损/止盈/仓位/杠杆
```

---

## 28. 交付说明要求

Codex 完成后必须说明：

1. 新增了哪些文件；
2. 修改了哪些文件；
3. 是否实现 Provider Adapter + Model Profile；
4. provider 级开关如何生效；
5. profile 级开关如何生效；
6. registry 如何筛选可用模型；
7. 是否默认主 profile 为 `deepseek_v4_pro_review`；
8. `deepseek_v4_flash_review` 是否默认关闭；
9. 是否只实现 DeepSeek 真实 provider；
10. 是否确认未实现 OpenAI / Claude 真实调用；
11. 是否确认未实现横向对比；
12. 是否确认未实现分析接力；
13. 是否确认未接 scheduler；
14. 是否确认未生成交易建议；
15. 是否确认未修改 K 线表；
16. 是否记录 token / 成本；
17. 是否默认不保存完整 raw request / raw response；
18. 超长 response 是否写 artifact；
19. 超长 response 是否 Hermes 提醒；
20. pytest 结果。

---

## 29. 阶段完成标准

第 19B 完成必须满足：

```text
1. mock provider 仍然可用；
2. DeepSeek provider 可通过手动 CLI 真实调用；
3. provider/profile/registry 三层开关生效；
4. 不同模型版本由独立 profile 管理；
5. 业务代码只通过 model_key 调用；
6. 真实模型输出可解析为统一审查结果；
7. schema validator 能阻止交易字段；
8. token 和成本可追踪；
9. raw request/response 不进主表；
10. 超长响应不丢弃，有 artifact 和 Hermes 告警；
11. 不自动交易；
12. 不产生最终交易建议；
13. 不接 scheduler。
```

---

## 30. 后续阶段预留

19B 完成后，后续可以继续拆分：

```text
19C：GPT / OpenAI Provider 接入
19D：多模型横向对比 parallel_comparison
19E：DeepSeek → GPT 分析接力 relay_chain
19F：人工审核输入与微信确认闭环
```

当前 19B 只做 DeepSeek 单模型真实审查。

不要在 19B 把后续阶段提前实现。
