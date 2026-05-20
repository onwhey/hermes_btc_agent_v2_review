# 第 19B 阶段计划：真实大模型 Provider 接入与模型版本档案架构

> 文件路径：`docs/plans/19b_deepseek_provider_plan.md`
>
> 本文件是第 19B 阶段当前正式计划，替代此前所有 19B 草稿。
>
> Codex 开工前必须同时阅读：
>
> - `AGENTS.md`
> - `docs/plans/19_model_strategy_review_gate_plan.md`
> - `docs/plans/19_model_strategy_review_gate_addendum.md`
> - `docs/plans/19b_deepseek_provider_plan.md`
>
> 以上文件共同构成第 19 阶段完整上下文。任何一个缺失，都不应继续实现。
>
> 本计划必须在已经完成并验证通过的 19A 基础上实现。19B 不得重做 19A。

---

## 1. 阶段定位

第 19B 阶段的目标是：在 19A 已完成的 mock 大模型审查门控骨架基础上，接入真实 DeepSeek Provider，并建立可长期扩展的模型供应商与模型版本档案架构。

19B 仍然不是策略开发阶段，也不是最终交易建议阶段。

19B 只做：

- 真实大模型 Provider Adapter 架构；
- Model Profile 模型版本档案架构；
- DeepSeek 单模型真实审查；
- provider 级开关和 profile 级开关；
- 真实模型调用多重门控；
- 请求、响应、token、成本、hash、profile 信息记录；
- raw request / raw response 默认不进主业务表；
- 超长 response 不丢弃，走隔离存储或 artifact 引用，并必须 Hermes 告警；
- CLI 手动触发真实模型审查。

19B 不做：

- 不开发真实交易策略；
- 不接 scheduler 自动调用；
- 不做横向对比执行逻辑；
- 不做分析接力执行逻辑；
- 不做微信人工补充材料入口；
- 不生成最终交易建议；
- 不输出入场价、止损价、止盈价、仓位、杠杆；
- 不调用交易接口；
- 不自动交易。

---

## 2. 核心边界

19B 必须继续继承 19A 的边界：

1. 大模型审查结果不是最终交易建议；
2. 大模型不得输出可执行交易指令；
3. 系统不得自动交易；
4. 系统不得调用交易接口；
5. 系统不得修改正式 K 线表；
6. 真实模型调用必须由人工 CLI 显式触发；
7. 默认不调用真实模型；
8. 默认使用 mock provider；
9. dry-run 不写库；
10. confirm-write 才允许写入业务表；
11. `MODEL_REVIEW_ENABLED=false` 时，`confirm-write` 必须 blocked；
12. blocked / failed 不得锁死后续重跑；
13. run 表是 attempt 表；
14. result 表是成功结果表；
15. `review_version_key` 在 run 表非唯一，在 result 表唯一。

---

## 3. 19B 的真实价值

19B 的重点不是“让大模型说几句话”，而是验证以下链路是否可控：

```text
18 生成 analysis_material_pack
        ↓
19B 根据 model_key 加载 model profile
        ↓
Provider Adapter 构造真实请求
        ↓
DeepSeek 返回结构化审查结果
        ↓
schema_validator 校验
        ↓
长度 / 禁止字段 / token / 成本 / hash 校验
        ↓
写入 model_analysis_run / model_analysis_result
        ↓
必要时 Hermes 提醒
```

大模型负责审查材料；程序负责强制格式、校验边界、拒绝越界输出、记录追溯信息。

程序不得去“理解”大模型的长篇自然语言推理内容。程序只解析最终按 schema 输出的结构化 JSON。

---

## 4. 配置目录结构

19B 需要将 19A 的 `configs/model_review/` 扩展为 provider + profile 结构。

目标结构：

```text
configs/model_review/
  model_registry.yaml

  providers/
    deepseek.yaml

  profiles/
    deepseek/
      deepseek_v4_pro_review.yaml
      deepseek_v4_flash_review.yaml
```

说明：

- `model_registry.yaml` 只负责声明哪些 `model_key` 允许参与审查；
- `providers/deepseek.yaml` 负责 DeepSeek 供应商级配置和 enabled 总开关；
- `profiles/deepseek/*.yaml` 每个文件只描述一个具体模型版本；
- 不允许把 DeepSeek 所有版本塞进一个大 YAML；
- 后续 OpenAI/GPT、Claude 等供应商应按同样结构扩展；
- 19B 只实现 DeepSeek Provider，不实现 OpenAI/Claude Provider。

---

## 5. model_registry.yaml 规则

`model_registry.yaml` 示例：

```yaml
enabled_models:
  - mock_review
  - deepseek_v4_pro_review
  - deepseek_v4_flash_review

default_mode: single
default_real_model_key: deepseek_v4_pro_review
```

规则：

1. registry 是“选择谁参与”的列表；
2. registry 启用不代表模型一定可用；
3. 实际可用还必须通过 provider 开关、profile 开关、环境变量、CLI 成本确认和 API key 检查；
4. 19B 只执行 `single` 模式；
5. `relay_chain` 和 `parallel_comparison` 只预留，不实现执行逻辑；
6. 如果 registry 启用了未知 `model_key`，应 blocked，并输出明确错误；
7. 如果 registry 启用了 disabled profile，真实调用必须 blocked。

---

## 6. Provider 级开关

`configs/model_review/providers/deepseek.yaml` 示例：

```yaml
provider: deepseek
enabled: true
api_style: openai_chat_completion
base_url: https://api.deepseek.com
timeout_seconds: 60
max_retry: 1
api_key_env: DEEPSEEK_API_KEY

provider_version: provider_profile_v1
docs_checked_at: "2026-05-20"
docs_source:
  - "DeepSeek official API documentation"
```

规则：

1. provider 级开关控制整个供应商；
2. `enabled=false` 时，该 provider 下所有 profile 都不可用；
3. provider 配置不得存储 API key 明文；
4. API key 只能从环境变量读取；
5. 真实调用前必须检查 API key 是否存在；
6. provider 配置必须记录 `docs_checked_at` 和 `docs_source`；
7. provider 配置变更后必须重新计算相关 profile / provider hash 或记录配置版本变化。

---

## 7. Profile 级开关

每个模型版本必须有独立 profile 文件。

`configs/model_review/profiles/deepseek/deepseek_v4_pro_review.yaml` 示例：

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
  reasoning_content: true
  thinking: true
  function_calling: false
  streaming: false

request_params:
  max_tokens: 4096
  reasoning_effort: high
  response_format:
    type: json_object
  extra_body:
    thinking:
      type: enabled

ignored_params_in_thinking_mode:
  - temperature
  - top_p
  - presence_penalty
  - frequency_penalty

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
  unit: per_1m_tokens
  pricing_version: deepseek_v4_profile_v1
  pricing_is_estimate: true
  fallback_when_cache_breakdown_missing: conservative_cache_miss
```

`configs/model_review/profiles/deepseek/deepseek_v4_flash_review.yaml` 示例：

```yaml
model_key: deepseek_v4_flash_review
provider: deepseek
enabled: false

api_style: openai_chat_completion
model_name: deepseek-v4-flash
model_version: v4_flash
profile_version: profile_v1

model_role: low_cost_review
analysis_mode: single
prompt_template_version: review_gate_v1
review_schema_version: review_schema_v1

capabilities:
  json_output: true
  reasoning_content: true
  thinking: true
  function_calling: false
  streaming: false

request_params:
  max_tokens: 4096
  reasoning_effort: high
  response_format:
    type: json_object
  extra_body:
    thinking:
      type: enabled

ignored_params_in_thinking_mode:
  - temperature
  - top_p
  - presence_penalty
  - frequency_penalty

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
  unit: per_1m_tokens
  pricing_version: deepseek_v4_profile_v1
  pricing_is_estimate: true
  fallback_when_cache_breakdown_missing: conservative_cache_miss
```

规则：

1. `deepseek_v4_pro_review` 默认启用；
2. `deepseek_v4_flash_review` 默认关闭，只作为备用低成本 profile；
3. profile 级开关控制具体模型版本；
4. 同一个 provider 下可以关闭某个版本，保留另一个版本；
5. 业务代码只面向 `model_key`，不得直接写死 `model_name`；
6. 不允许假设更换模型版本只需要修改 `model_name`；
7. 不同模型版本的参数、返回结构、能力、成本、schema 支持情况，都必须在独立 profile 中声明。

---

## 8. 模型 Profile 参数治理与文档核验规则

真实模型版本不得依赖供应商默认参数。每个 model profile 必须显式声明关键请求参数、参数来源、文档核验信息和 profile 版本信息。

### 8.1 不依赖供应商默认值

禁止假设 DeepSeek、OpenAI、Claude 等供应商的默认参数长期稳定。

每个真实模型 profile 必须显式声明：

- `api_style`
- `model_name`
- `model_version`
- `request_params`
- `response_mapping`
- `capabilities`
- `unsupported_params`
- `cost_policy`
- `profile_version`
- `docs_checked_at`
- `docs_source`

如果某个参数确实依赖供应商默认值，必须在 profile 中明确标注原因，不得隐式依赖。

### 8.2 DeepSeek thinking mode 显式配置

DeepSeek v4 pro profile 必须显式声明 thinking mode 配置，不得只依赖供应商默认开启。

示例：

```yaml
request_params:
  max_tokens: 4096
  reasoning_effort: high
  extra_body:
    thinking:
      type: enabled

ignored_params_in_thinking_mode:
  - temperature
  - top_p
  - presence_penalty
  - frequency_penalty
```

如果某些参数在 thinking mode 下不生效，必须在 profile 中显式列入 `ignored_params_in_thinking_mode`，避免误导。

### 8.3 profile 版本与 hash

每个 model profile 必须生成并记录：

- `profile_version`
- `profile_hash`
- `docs_checked_at`
- `docs_source`

`profile_hash` 必须参与 `review_version_key` 计算。

同一个 material pack 使用不同 profile 审查时，必须生成不同的 `review_version_key`。

### 8.4 新增或升级模型的核验流程

新增模型版本或升级模型 profile 前，必须完成 smoke test。

smoke test 至少验证：

1. API 能正常请求；
2. 模型能返回结果；
3. `response_mapping` 能正确提取最终 content；
4. `schema_validator` 能通过；
5. 不输出禁止交易字段；
6. token usage / cost 信息能被记录；
7. raw response 长度控制符合规则；
8. Hermes 告警链路在异常时可触发。

### 8.5 不要求人工每天检查供应商默认值

系统不要求用户每天检查 DeepSeek、OpenAI、Claude 的默认参数是否变化。

正确处理方式是：

1. 关键参数显式写入 profile；
2. 新增或升级 profile 时核验官方文档；
3. 真实调用前通过 smoke test 验证；
4. 运行中通过 schema 失败、返回结构异常、reasoning_content 缺失、超长输出等异常触发 Hermes 告警；
5. 如发现供应商行为变化，通过更新 `profile_version` / `profile_hash` 修正，而不是临时改业务代码。

---

## 9. 真实模型多重门控

真实模型调用必须同时满足：

1. `MODEL_REVIEW_REAL_MODEL_ENABLED=true`；
2. `MODEL_REVIEW_ENABLED=true`；
3. provider `enabled=true`；
4. profile `enabled=true`；
5. `model_registry.yaml` 启用了该 `model_key`；
6. CLI 显式传入 `--use-real-model`；
7. CLI 显式传入 `--model-key`；
8. CLI 显式传入 `--confirm-real-model-cost`；
9. API key 存在；
10. material pack 可审查；
11. schema 校验通过。

任一条件不满足，不得调用真实模型。

特别规则：

- `dry-run` 可以在显式确认成本后调用真实模型，但不得写库；
- `confirm-write` 才允许写入 `model_analysis_run` / `model_analysis_result`；
- `MODEL_REVIEW_ENABLED=false` 时，`confirm-write` 必须 blocked；
- scheduler 不得调用真实模型；
- 19B 不实现自动周期调用真实模型。

---

## 10. Provider Adapter 架构

建议新增或扩展：

```text
app/model_analysis/providers/base.py
app/model_analysis/providers/deepseek.py
app/model_analysis/model_registry.py
```

职责：

### 10.1 base.py

定义统一 Provider 接口，例如：

```text
ModelProvider
ProviderRequest
ProviderResponse
ProviderUsage
ProviderError
```

要求：

1. 所有 provider 返回统一结构；
2. service 层不关心具体 provider 原始返回格式；
3. provider 错误必须转成统一错误码；
4. provider 调用必须支持 timeout；
5. provider 调用必须记录 trace_id。

### 10.2 deepseek.py

DeepSeek Provider 只负责：

1. 根据 profile 构造请求；
2. 发送请求；
3. 解析原始响应；
4. 根据 `response_mapping` 提取最终 content；
5. 提取 reasoning_content；
6. 提取 usage；
7. 提取 finish_reason；
8. 计算 raw response hash / 长度；
9. 返回统一 `ProviderResponse`。

`deepseek.py` 不负责：

- 不做业务审查决策；
- 不写数据库；
- 不发 Hermes；
- 不生成交易建议；
- 不处理 scheduler。

### 10.3 service.py

`service.py` 只负责：

1. 根据 `model_key` 加载 profile；
2. 根据 provider 选择 adapter；
3. 构造审查 prompt；
4. 调用 provider；
5. 调用 schema validator；
6. 写 run/result；
7. 处理 blocked/failed/success；
8. 必要时调用 Hermes formatter。

`service.py` 不得直接拼 DeepSeek 请求。

---

## 11. 真实调用记录顺序

真实模型调用属于外部高成本、不可完全重放动作。

19B 必须保证调用可审计：

```text
创建 model_analysis_run(status=running)
        ↓
调用 DeepSeek
        ↓
解析 / 校验 / 长度检查
        ↓
更新 run 为 success / blocked / failed
        ↓
必要时写 result
```

不得在真实模型调用完成后才首次创建 run 记录。

如果进程在真实调用期间崩溃，数据库至少应能看到：

- run 已创建；
- status=running；
- model_key；
- provider；
- material_pack_id；
- review_version_key；
- trace_id；
- created_at_utc。

后续可通过超时巡检或人工检查识别卡住的 running run。

---

## 12. CLI 行为

继续使用：

```text
scripts/run_model_analysis.py
```

新增或确认参数：

```text
--use-real-model
--model-key
--confirm-real-model-cost
```

规则：

1. 默认不调用真实模型；
2. 默认仍使用 mock；
3. `--use-real-model` 但无 `--confirm-real-model-cost`，必须 blocked；
4. `--use-real-model` 但未指定 `--model-key`，必须 blocked；
5. `--model-key` 指向 disabled profile，必须 blocked；
6. `--model-key` 所属 provider disabled，必须 blocked；
7. `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 时，必须 blocked；
8. API key 缺失时，必须 blocked；
9. dry-run 可以在显式确认成本后调用真实模型，但不得写库；
10. confirm-write 才能写库；
11. `MODEL_REVIEW_ENABLED=false` 时 confirm-write 仍然 blocked；
12. 不允许 scheduler 调用真实模型。

真实调用 dry-run 示例：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --dry-run
```

真实调用写库示例：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --confirm-write
```

---

## 13. 数据库迁移

新增 19B migration，不要破坏已存在的 19A revision 链。

建议补充字段。

`model_analysis_run` 建议固定列：

- `provider`
- `model_key`
- `model_name`
- `model_version`
- `profile_version`
- `profile_hash`
- `api_style`
- `provider_request_id`
- `request_payload_hash`
- `rendered_prompt_hash`
- `prompt_template_hash`
- `request_params_summary_json`
- `capabilities_json`
- `response_metadata_summary_json`
- `provider_usage_json`
- `raw_request_hash`
- `raw_response_hash`
- `raw_response_storage_ref`
- `raw_response_char_count`
- `raw_response_byte_count`
- `input_token_count`
- `output_token_count`
- `total_token_count`
- `estimated_cost`
- `cost_currency`
- `cost_estimation_basis`

如字段过多，允许将 provider 差异放入 JSON 摘要字段，但以下字段建议保留固定列：

- `provider`
- `model_key`
- `model_name`
- `model_version`
- `profile_version`
- `profile_hash`
- `api_style`
- `input_token_count`
- `output_token_count`
- `total_token_count`
- `estimated_cost`
- `cost_currency`
- `raw_response_hash`
- `raw_response_char_count`
- `raw_response_byte_count`

`model_analysis_result` 继续只存统一审查结果，不按模型版本增加特殊列。

---

## 14. raw request / raw response 存储规则

默认规则：

1. 不保存完整 raw request 到主业务表；
2. 不保存完整 raw response 到主业务表；
3. 不保存完整 reasoning_content 到主业务表；
4. 主表保存 hash、长度、token、成本、metadata 摘要；
5. 完整原文如需保存，必须走 artifact 隔离机制；
6. artifact 也必须受长度、hash、引用、告警规则管控。

可以新增表：

```text
model_provider_call_artifact
```

建议字段：

- `id`
- `artifact_id`
- `model_analysis_run_id`
- `artifact_type`
- `provider`
- `model_key`
- `model_name`
- `model_version`
- `profile_hash`
- `storage_ref`
- `sha256_hash`
- `char_count`
- `byte_count`
- `capture_reason`
- `created_at_utc`

`artifact_type` 可选：

- `raw_request`
- `raw_response`
- `reasoning_content`
- `oversized_response`

如果暂时不实现真实文件存储，也必须预留 `storage_ref`、hash、长度和 `capture_reason`，不得静默丢弃。

---

## 15. raw request 规则

默认不保存完整发送给 DeepSeek 的请求体。

但必须记录足够追溯信息：

- `material_pack_id`
- `input_material_hash`
- `prompt_template_version`
- `prompt_template_hash`
- `model_key`
- `provider`
- `model_name`
- `model_version`
- `profile_version`
- `profile_hash`
- `api_style`
- `request_params_summary_json`
- `request_payload_hash`
- `rendered_prompt_hash`
- `input_char_count`
- `input_byte_count`
- `estimated_input_tokens`

完整 raw request 只有在显式调试或审计模式下才允许保存到 artifact，不得进入主业务表。

如果实现 `--capture-raw-request`，必须真正写 artifact 表或隔离存储。不能暴露半成品 CLI 参数。

如果暂不实现 raw request 捕获，应不要暴露 `--capture-raw-request` 参数。

---

## 16. raw response 规则

默认不保存完整 raw response 到主业务表。

必须记录：

- `raw_response_hash`
- `raw_response_char_count`
- `raw_response_byte_count`
- `finish_reason`
- `provider_usage_json`
- `response_metadata_summary_json`

如果 raw response 超长，但可以提取合规结构化 JSON：

- `model_analysis_result` 正常写入；
- raw response 不进主表；
- 记录 hash、长度、artifact 引用；
- run 可以为 success。

如果 raw response 超长，且无法安全提取结构化结果：

- `model_analysis_run.status=blocked`；
- `error_code=model_output_too_large`；
- 不写 `model_analysis_result`；
- 必须记录 hash、长度、trace_id、artifact 引用；
- 必须 Hermes 告警。

---

## 17. 超长 response 处理

严格执行：

1. raw response 超过 10000 字符或 32KB，不允许写入主业务表；
2. 结构化 content 超过 10000 字符或 32KB，不允许写入 `model_analysis_result`；
3. 任一待入库字段超过限制，必须 blocked；
4. 超长内容不能静默丢弃；
5. 必须记录 hash、长度、trace_id、artifact 引用；
6. 必须 Hermes 提醒用户；
7. 如果 Hermes 发送失败，不能吞掉，必须记录 `hermes_status=failed`。

超长返回频繁发生时，不应简单视为模型错误，也可能说明：

- prompt 过长；
- schema 设计过宽；
- 摘要策略不合理；
- 数据库字段长度太小；
- 需要拆表；
- 需要正式 artifact 存储；
- 需要调整 response schema。

这属于后期维护和数据库设计评估项。前期仍按 32KB / 10000 字符规则处理。

---

## 18. artifact 写入失败处理

artifact 写入失败不能导致用户无感知。

如果 raw response 过长，且 artifact 写入失败，必须：

1. 不静默丢弃；
2. 更新 `model_analysis_run.status=failed` 或 `blocked`；
3. 写入明确 `error_code`，例如 `artifact_write_failed`；
4. 记录 raw response hash、长度、trace_id；
5. Hermes 启用时必须告警；
6. CLI 返回非 0；
7. 不写 `model_analysis_result`。

不能让异常直接抛出后中断进程而没有 run 记录。

---

## 19. Hermes 告警

超长 response 必须 Hermes 提醒。

提醒内容必须中文，至少包含：

1. 标题：`BTC 大模型审查返回过长`；
2. `model_key`；
3. `provider`；
4. `model_name`；
5. `material_pack_id`；
6. `model_analysis_run_id`；
7. `raw_response_char_count`；
8. `raw_response_byte_count`；
9. 处理结果：已阻断 / 已隔离保存 / 未生成正式审查结果；
10. `trace_id`；
11. 明确说明：不是最终交易建议，未自动交易。

Hermes 发送失败时：

- 不得吞掉；
- 必须记录 `hermes_status=failed` 或等价字段；
- CLI / 日志要能看到。

---

## 20. Prompt 与 Schema

继续沿用 19A 的审查边界。

真实模型 prompt 必须明确：

1. 你不是交易员；
2. 你不能给最终交易建议；
3. 你不能给入场价、止损价、止盈价、仓位、杠杆；
4. 你只能审查材料完整性、证据质量、逻辑一致性、风险接受度、策略冲突、是否需要人工审核；
5. 必须输出 JSON；
6. 必须包含 `human_review_required`；
7. 必须包含 `not_trading_advice=true`。

`schema_validator` 必须继续禁止：

- `entry_price`
- `stop_loss`
- `take_profit`
- `position_size`
- `leverage`
- `order_type`
- `final_advice`
- `buy_now`
- `sell_now`

如果真实模型输出这些字段，必须 blocked 或 schema_invalid，不得写入 result。

---

## 21. token 与成本记录

真实模型调用必须记录：

- `input_token_count`
- `output_token_count`
- `total_token_count`
- `estimated_cost`
- `cost_currency`
- `cost_estimation_basis`
- `provider_usage_json`

如果 provider 返回 cache hit / cache miss token，应记录到 `provider_usage_json`。

如果 provider 没有返回 token usage：

1. 不得报 success 后假装有精确成本；
2. 可以估算并标记 estimated；
3. `provider_usage_json` 里记录 `usage_missing=true`；
4. `estimated_cost` 可以为空或估算值，但必须明确。

成本记录不是为了节省 token，而是为了复盘、监控和后续模型效果评估。

---

## 22. review_version_key 规则

保持 19A 幂等规则：

1. `model_analysis_run` 是 attempt 表；
2. `model_analysis_run.review_version_key` 非唯一；
3. `model_analysis_result.review_version_key` 唯一；
4. blocked / failed 不锁死重跑；
5. success / partial_success 结果存在时，同 `review_version_key` 再跑应 skipped / already_exists。

19B 中 `review_version_key` 必须纳入：

- `material_pack_id`
- `model_key`
- `provider`
- `model_name`
- `model_version`
- `profile_hash`
- `prompt_template_hash`
- `review_schema_version`

不同模型 profile 审查同一 material pack，必须生成不同 `review_version_key`。

---

## 23. partial_success 材料包准入

保持 19A 最新规则：

1. `success` 允许；
2. `partial_success` 有条件允许；
3. `partial_success` 必须通过核心字段完整性校验；
4. placeholder / not_implemented 不应自动阻断；
5. `failed_strategy_count > 0` 或 `invalid_strategy_count > 0` 必须阻断；
6. `blocked` / `failed` / `skipped` / `running` / `pending` 不允许。

不要回退到“只允许 success”。

---

## 24. smoke test 规则

19B 必须提供 smoke test 能力，至少用于：

1. 检查 provider 配置可读；
2. 检查 profile 配置可读；
3. 检查 API key 存在；
4. 可选发起最小真实请求；
5. 验证 `response_mapping` 能提取 content；
6. 验证 schema 能通过；
7. 验证禁止交易字段能被拦截；
8. 验证 token / usage 信息能记录；
9. 验证超长返回时 Hermes 告警 formatter 可用。

真实 smoke test 必须由人工显式触发，不得由 scheduler 自动触发。

---

## 25. 测试要求

新增或修改 `tests/model_analysis`，覆盖：

1. provider.enabled=false 时真实模型 blocked；
2. profile.enabled=false 时真实模型 blocked；
3. model_registry 未启用 model_key 时 blocked；
4. `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 时 blocked；
5. `--use-real-model` 缺少 `--confirm-real-model-cost` 时 blocked；
6. `--use-real-model` 缺少 `--model-key` 时 blocked；
7. API key 缺失时 blocked；
8. enabled 的 `deepseek_v4_pro_review` 可进入 fake DeepSeek client 流程；
9. `deepseek_v4_flash_review` 默认 disabled；
10. profile_hash 正确生成并入库；
11. `review_version_key` 包含 profile_hash；
12. 不同 model_key 审查同一 material_pack 生成不同 `review_version_key`；
13. 真实 provider 测试必须使用 fake client，不允许测试直接访问外网；
14. raw response 不写入主表；
15. raw response hash / char_count / byte_count 被记录；
16. token usage 被记录；
17. estimated_cost 被记录或明确为空且标记 usage_missing；
18. raw response 超过限制时 blocked；
19. 超长 response 不丢弃，生成 artifact 记录或 storage_ref；
20. 超长 response 触发 Hermes formatter；
21. Hermes 超长告警文案为中文；
22. artifact 写入失败时 run 状态和 Hermes 告警正确；
23. schema 中出现 entry_price / stop_loss / take_profit / leverage / position_size 时 blocked；
24. `not_trading_advice` 必须为 true；
25. `human_review_required` 必须是 boolean；
26. dry-run 不写库；
27. confirm-write 才写库；
28. `MODEL_REVIEW_ENABLED=false` 时 confirm-write blocked；
29. 不修改 K线表；
30. 不生成最终交易建议；
31. 不调用交易接口；
32. DeepSeek thinking mode 参数来自 profile；
33. profile 中存在 `docs_checked_at` 和 `docs_source`；
34. profile 不依赖供应商默认 thinking 配置；
35. thinking mode 下无效参数被列入 `ignored_params_in_thinking_mode`；
36. provider 调用前先创建 `model_analysis_run(status=running)`；
37. provider 调用失败时 run 能更新为 failed 或 blocked。

---

## 26. 验收命令

完成后运行：

```bash
python -m py_compile app/model_analysis/model_registry.py
python -m py_compile app/model_analysis/service.py
python -m py_compile app/model_analysis/repository.py
python -m py_compile app/model_analysis/schema_validator.py
python -m py_compile app/model_analysis/providers/base.py
python -m py_compile app/model_analysis/providers/mock.py
python -m py_compile app/model_analysis/providers/deepseek.py
python -m py_compile app/model_analysis/hermes_formatter.py
python -m py_compile scripts/run_model_analysis.py

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

## 27. 人工验证命令示例

真实模型 dry-run：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --dry-run
```

真实模型 confirm-write：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --confirm-write
```

禁用真实模型时预期 blocked：

```bash
MODEL_REVIEW_REAL_MODEL_ENABLED=false python -m scripts.run_model_analysis \
  --material-pack-id "AMP-xxx" \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --dry-run
```

---

## 28. 交付说明

完成后请输出：

1. 新增了哪些文件；
2. 修改了哪些文件；
3. 新增了哪些配置项；
4. 新增了哪些数据库字段或表；
5. Provider Adapter 架构如何实现；
6. Model Profile 如何加载；
7. provider enabled 和 profile enabled 如何共同生效；
8. DeepSeek v4 pro profile 是否默认启用；
9. DeepSeek v4 flash profile 是否默认关闭；
10. 是否确认没有真实 scheduler 调用；
11. 是否确认没有交易建议字段；
12. 是否确认 raw request / raw response 不进主业务表；
13. 超长 response 如何处理；
14. artifact 写入失败如何处理；
15. Hermes 超长提醒是否实现；
16. token / 成本如何记录；
17. review_version_key 是否包含 profile_hash；
18. thinking mode 是否由 profile 显式配置；
19. profile 是否包含 `docs_checked_at` / `docs_source`；
20. pytest 结果。

---

## 29. 最终阶段结论

19B 完成后，系统应达到：

```text
可通过 CLI 手动触发 DeepSeek v4 pro 对 analysis_material_pack 做真实模型审查；
模型调用受 provider/profile/registry/env/CLI 多重门控；
结果必须通过 schema 校验；
不生成交易建议；
不自动交易；
不接 scheduler；
请求和响应默认不进主业务表；
超长内容不丢弃，走 artifact / hash / Hermes 告警；
不同模型版本通过独立 profile 管理；
后续可扩展 GPT、Claude、横向对比和分析接力，但 19B 不实现这些扩展。
```
