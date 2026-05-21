# 20_model_review_aggregation_and_orchestration.md

# 阶段 20：模型审查结果聚合、复用判断与接力编排计划

## 1. 阶段定位

阶段 20 是阶段 18 与阶段 19、阶段 21 之间的模型审查控制层。

阶段 20 不直接请求 DeepSeek、GPT、Claude 或任何大模型供应商接口。

阶段 20 负责：

1. 读取阶段 18 生成的 `analysis_material_pack`；
2. 读取阶段 19 已经落库的 `model_analysis_run` / `model_analysis_result`；
3. 判断本轮是否需要重新触发大模型审查；
4. 判断是否可以复用上一轮模型审查结果；
5. 管理未来模型接力链路的状态与恢复；
6. 聚合模型审查结果，生成阶段 21 可以安全使用的输入；
7. 明确输出本轮大模型参与状态，防止用户误以为每次通知都经过了最新大模型审查。

阶段 20 不是最终建议层。

阶段 20 不生成最终交易建议，不生成交易信号，不生成入场价、止损价、止盈价、仓位、杠杆，也不触发自动交易。

阶段 20 的核心目标是：

> 把阶段 19 的模型审查结果整理成可追溯、可复用、可比较、可恢复、可交给阶段 21 使用的受约束输入。

---

## 2. 当前项目状态

当前阶段 19 已经完成基础验收：

- 支持从阶段 18 的 `analysis_material_pack` 读取模型审查材料；
- 支持真实 DeepSeek 调用；
- 支持 profile / provider / schema / prompt 版本记录；
- 支持 `model_analysis_run` / `model_analysis_result` 落库；
- 支持 token、成本、profile_hash、review_version_key 记录；
- 支持 raw response 超长隔离保存；
- 支持模型分析 Hermes 告警进入统一审计；
- 当前真实模型总闸已可通过 `MODEL_REVIEW_REAL_MODEL_ENABLED=false` 阻断真实调用。

当前阶段 19 只允许人工 CLI 触发真实模型审查。

当前自动链路停在阶段 18：

```text
4h K线采集
  ↓
17 策略信号调度
  ↓
16 策略信号生成
  ↓
18 聚合材料包生成
  ↓
停止
```

当前 scheduler 不应直接触发阶段 19。

阶段 20 完成后，未来自动链路应变为：

```text
4h K线采集
  ↓
17 触发 16
  ↓
16 生成策略信号
  ↓
18 生成分析材料包
  ↓
20 判断是否需要模型审查
  ↓
如需审查：20 创建/调度 19 的模型调用任务
如不需审查：20 复用旧结果或标记跳过原因
  ↓
19 执行单次模型调用
  ↓
20 聚合模型审查结果
  ↓
21 生成最终人工建议或策略通知
```

---

## 3. 阶段边界

### 3.1 阶段 19 的职责

阶段 19 是单次模型调用执行层。

它负责：

- 根据 `material_pack_id`、`model_key`、`model_role`、profile 和 prompt 生成一次模型审查；
- 调用 mock provider 或真实 provider；
- 校验模型返回 schema；
- 写入 `model_analysis_run` / `model_analysis_result`；
- 记录成本、token、hash、artifact、Hermes 状态。

阶段 19 不负责：

- 决定是否需要重新调用模型；
- 判断是否复用上一轮模型结果；
- 管理多模型接力链；
- 生成最终交易建议；
- 直接接入 scheduler 自动运行。

### 3.2 阶段 20 的职责

阶段 20 是模型审查控制层。

它负责：

- 判断本轮材料包是否需要模型审查；
- 判断旧模型审查结果是否仍可复用；
- 判断是否需要创建模型接力链；
- 管理接力链 step 状态；
- 监控 failed / timeout / retry_waiting 步骤；
- 跳过已成功步骤，避免重复烧成本；
- 聚合阶段 19 的一个或多个模型审查结果；
- 给阶段 21 输出大模型参与状态和建议生成边界。

阶段 20 不负责：

- 直接请求大模型供应商；
- 自己实现 DeepSeek/GPT/Claude HTTP 调用；
- 生成最终交易建议；
- 输出可执行交易结构。

### 3.3 阶段 21 的职责

阶段 21 是最终人工建议表达层。

它负责：

- 读取阶段 20 输出；
- 用中文生成用户可读的策略通知或人工建议；
- 明确展示本轮是否调用大模型、是否复用旧结果、是否跳过模型调用以及原因。

阶段 21 不应该直接拼接多个阶段 19 原始结果，也不应该绕过阶段 20 判断模型参与状态。

---

## 4. 分阶段实现建议

阶段 20 不应一次性实现完整自动模型接力。

建议拆成 20A、20B、20C。

### 4.1 阶段 20A：模型审查聚合与复用判断

第一版只做确定性控制，不调用任何大模型。

20A 实现：

1. 读取指定 `analysis_material_pack`；
2. 读取该材料包对应的阶段 19 成功结果；
3. 判断是否存在可复用的旧模型结果；
4. 判断旧模型结果是否超过复用有效期；
5. 生成模型审查聚合摘要；
6. 输出大模型参与状态；
7. dry-run 不写库；
8. confirm-write 写入聚合结果。

20A 不实现：

- 自动调用真实模型；
- 多模型真实接力；
- scheduler 自动模型调用；
- 最终交易建议。

### 4.2 阶段 20B：模型接力链状态机

20B 实现模型接力任务链，但先使用 mock 或已存在的阶段 19 mock provider 验证。

20B 实现：

1. `chain_run` / `chain_step` 状态机；
2. `chain_id`、`chain_step`、`parent_model_analysis_run_id`；
3. step 级 pending / running / success / failed / timeout / retry_waiting；
4. 成功 step 不重复执行；
5. 失败 step 可断点续跑；
6. partial_success 聚合；
7. watchdog / worker 扫描未完成 step。

20B 不应直接上真实 DeepSeek + GPT 接力。

### 4.3 阶段 20C：scheduler 自动模型调用

20C 才允许 scheduler / worker 自动触发阶段 19。

20C 必须具备：

1. 真实模型总闸；
2. 自动运行总闸；
3. scheduler 模型调用总闸；
4. provider/profile/YAML 启用控制；
5. scheduler 自动调用模型白名单；
6. 每日预算；
7. 每 4h 周期最大模型调用次数；
8. 分布式锁；
9. 幂等键；
10. 失败重试；
11. 超时处理；
12. 断点续跑；
13. Hermes 告警和审计。

---

## 5. scheduler 与阶段 19/20 的关系

硬规则：

> scheduler 不直接调用阶段 19。scheduler 只能触发阶段 20，由阶段 20 判断是否创建或推进阶段 19 模型调用任务。

原因：

如果 scheduler 直接调用阶段 19，会绕过复用判断、预算控制、模型接力状态机和透明通知规则，容易造成重复模型调用、重复成本、数据污染和审计混乱。

正确关系：

```text
scheduler
  ↓
20 model review controller
  ↓
19 single model execution
  ↓
20 aggregation / chain state update
```

阶段 19 可以继续保留 CLI 手动入口。

CLI 手动调用真实模型时必须带人工成本确认参数，例如：

```bash
python -m scripts.run_model_analysis \
  --material-pack-id AMP-xxx \
  --trigger-source cli \
  --use-real-model \
  --model-key deepseek_v4_pro_review \
  --confirm-real-model-cost \
  --confirm-write
```

scheduler / worker 自动调用时不使用 `--confirm-real-model-cost`，而是完全受配置、预算、白名单、频率、状态机控制。

---

## 6. 模型审查复用规则

### 6.1 复用目的

如果本轮材料包与上一轮模型审查输入没有实质变化，系统不应每 4 小时重复请求大模型。

例如：

- A/B/C 三个策略上一轮全看空；
- 本轮仍然全看空；
- 风险状态没变；
- 结构状态没变；
- 波动状态没变；
- 关键价位没触发；
- 模型审查结果仍在有效期内；

则阶段 20 可以复用上一轮模型审查结果。

### 6.2 复用判断依据

不得只比较 `material_pack_id`。

每 4 小时都会生成新的 `material_pack_id`，但这不代表模型审查输入语义发生了实质变化。

复用判断应基于模型审查输入指纹：

```text
review_input_fingerprint
```

指纹建议纳入：

- `symbol`
- `base_interval`
- `higher_interval`
- `analysis_hypothesis_direction`
- `risk_gate_status`
- `risk_level`
- `conflict_level`
- `structure_state`
- `volatility_state`
- 关键支撑/压力区域状态
- 是否触发关键价位
- `material_summary_hash`
- `model_key`
- `model_role`
- `profile_hash`
- `prompt_template_hash`
- `review_schema_version`

### 6.3 必须重新审查的情况

以下情况必须重新触发模型审查，不能复用旧结果：

1. 策略方向发生变化；
2. 风险状态发生变化；
3. 市场结构状态发生变化；
4. 波动状态发生变化；
5. 关键价位被触发，例如突破、跌破、接近止损/目标/失效区；
6. 上一次模型审查失败、超时、schema_invalid 或 partial_success 不满足当前要求；
7. profile / prompt / schema 版本变化；
8. 旧模型审查结果超过复用有效期；
9. 人工强制要求重新审查。

### 6.4 复用有效期

旧模型审查结果必须设置有效期。

默认：

```text
MODEL_REVIEW_REUSE_MAX_BASE_BARS=3
```

当前 base interval 为 `4h`，因此默认最多复用：

```text
3 根 4h K线，约 12 小时
```

超过 3 根 base interval K线后，即使模型输入指纹仍然相似，也必须重新触发模型审查。

如果超过有效期但真实模型总闸关闭，例如：

```env
MODEL_REVIEW_REAL_MODEL_ENABLED=false
```

阶段 20 必须明确标记：

```text
model_review_expired_but_real_model_disabled
```

阶段 21 通知必须明确展示：

```text
本轮未调用最新大模型。
原因：旧模型审查结果已超过 3 根 4h K线复用期限，但真实模型调用被配置关闭。
当前结果仅基于代码策略和聚合材料，不包含最新大模型审查。
```

不得伪装成最新模型审查。

---

## 7. 大模型参与状态透明度要求

这是阶段 20 和阶段 21 的硬规则。

任何策略通知、模型审查通知、最终建议通知，都必须明确说明本轮大模型参与状态。

不得使用模糊措辞让用户误以为每次通知都经过了最新大模型审查。

阶段 20 输出必须包含：

```text
model_review_invoked
model_review_invocation_mode
model_review_reused
reused_model_analysis_run_id
reused_model_review_created_at_utc
model_review_skip_reason
model_review_block_reason
invoked_model_keys
invoked_model_roles
model_review_chain_status
model_review_partial_failure_reason
latest_model_review_at_utc
model_review_basis
model_review_staleness_base_bars
model_review_reuse_max_base_bars
```

字段含义：

- `model_review_invoked`：本轮是否真实调用大模型；
- `model_review_invocation_mode`：none / single / relay / comparison / reused；
- `model_review_reused`：是否复用旧模型结果；
- `reused_model_analysis_run_id`：复用哪条阶段 19 结果；
- `model_review_skip_reason`：为什么跳过本轮模型调用；
- `model_review_block_reason`：为什么被配置或预算阻断；
- `invoked_model_keys`：本轮调用了哪些模型；
- `invoked_model_roles`：每个模型承担什么角色；
- `model_review_chain_status`：接力链状态；
- `model_review_partial_failure_reason`：部分失败原因；
- `model_review_basis`：当前结论依据是什么；
- `model_review_staleness_base_bars`：旧结果距离当前已经过几根 base interval K线；
- `model_review_reuse_max_base_bars`：最大可复用 K线数。

阶段 21 通知中必须展示类似内容：

```text
【大模型状态】
- 本轮是否调用大模型：否
- 是否复用旧模型结果：是
- 复用模型结果：MAR-xxx
- 原模型审查时间：2026-xx-xx xx:xx
- 距离当前已过 K线：2 根 4h
- 复用上限：3 根 4h
- 跳过原因：策略方向、风险状态、结构状态与上一轮无实质变化
```

如果本轮被配置阻断：

```text
【大模型状态】
- 本轮是否调用大模型：否
- 原因：MODEL_REVIEW_REAL_MODEL_ENABLED=false
- 是否复用旧结果：否
- 当前依据：仅基于代码策略和阶段 18 聚合材料，不包含最新大模型审查
```

如果模型接力部分失败：

```text
【大模型状态】
- 本轮是否调用大模型：部分完成
- DeepSeek 结构审查：成功
- GPT 风险复核：失败 / 超时
- 当前接力状态：partial_success
- 是否允许生成方向建议：否
```

---

## 8. 模型接力编排规范

### 8.1 分工原则

模型接力的编排逻辑属于阶段 20。

真实模型调用仍属于阶段 19。

```text
20 负责流程
19 负责调用
21 负责表达
```

阶段 20 不应绕过阶段 19 直接请求供应商接口。

### 8.2 接力示例

未来可能存在：

```text
DeepSeek：数学/江恩/结构推演
  ↓
GPT：风险反驳、过度交易过滤
  ↓
20：整理接力结果
  ↓
21：生成中文人工建议
```

每一步真实模型调用都必须生成独立 `model_analysis_run`。

示例：

#### 第 1 步：DeepSeek 结构审查

```text
analysis_mode = relay
chain_id = CHAIN-xxx
chain_step = 1
model_role = mathematical_structure_review
model_key = deepseek_v4_pro_review
parent_model_analysis_run_id = null
```

#### 第 2 步：GPT 风险复核

```text
analysis_mode = relay
chain_id = CHAIN-xxx
chain_step = 2
model_role = risk_challenge_review
model_key = gpt55_risk_review
parent_model_analysis_run_id = MAR-DeepSeek-xxx
```

### 8.3 接力状态

`chain_run` 状态建议：

```text
pending
running
partial_success
success
failed
blocked
```

`chain_step` 状态建议：

```text
pending
running
success
failed
timeout
blocked
skipped
retry_waiting
```

### 8.4 失败恢复规则

如果 step 1 成功，step 2 失败：

```text
chain_status = partial_success
step 1 = success
step 2 = failed
```

后续恢复时，只能重新执行 step 2，不得重复调用 step 1。

硬规则：

```text
同一 chain_id + chain_step + review_version_key 已 success 时，不允许 scheduler/worker 再次真实调用模型。
```

如果确实需要重跑，必须使用人工 CLI 的 `force_rerun` 类参数，并且 scheduler 禁止使用强制重跑能力。

---

## 9. worker / watchdog 规范

未来 20B/20C 应新增模型审查链 worker 或 watchdog。

短期可以由现有 scheduler 定期 tick，例如每 1～5 分钟扫描数据库。

worker / watchdog 负责：

1. 扫描 `pending` / `retry_waiting` / `timeout` 可恢复 step；
2. 获取 Redis 锁，防止并发执行同一 step；
3. 检查依赖 step 是否成功；
4. 跳过已成功 step；
5. 执行当前未完成 step；
6. 记录 attempt；
7. 失败后进入 retry_waiting 或 failed；
8. 超过最大重试次数后标记 chain 为 partial_success 或 failed；
9. 触发阶段 20 聚合更新。

不得依赖人工发现 step 失败。

不得依赖一个长脚本串完 DeepSeek + GPT + 聚合。

---

## 10. 幂等与版本规则

### 10.1 单次模型调用幂等

每一次模型调用必须有稳定 `review_version_key`。

建议组成：

```text
material_pack_hash
+ model_key
+ model_role
+ profile_hash
+ prompt_template_hash
+ review_schema_version
+ parent_model_analysis_result_hash
+ chain_id / chain_step
```

同一版本 key 已经有成功结果时，scheduler/worker 不得重复调用真实模型。

### 10.2 复用不能跨版本

如果以下任何内容变化，旧模型结果不得作为同版本结果复用：

- `profile_hash`
- `prompt_template_hash`
- `review_schema_version`
- `model_key`
- `model_role`
- 关键 material summary hash
- 父级模型结果 hash

否则会出现：prompt 已升级但系统仍复用旧 prompt 结果的问题。

---

## 11. 预算与频率控制

未来 scheduler 自动调用真实模型时，必须有预算和频率控制。

建议配置：

```env
MODEL_REVIEW_REAL_MODEL_ENABLED=false
MODEL_REVIEW_AUTO_RUN_ENABLED=false
MODEL_REVIEW_SCHEDULER_ENABLED=false
MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS=deepseek_v4_pro_review,gpt55_risk_review
MODEL_REVIEW_DAILY_BUDGET_USD=5
MODEL_REVIEW_MAX_RUNS_PER_4H=2
MODEL_REVIEW_REUSE_MAX_BASE_BARS=3
```

含义：

- `MODEL_REVIEW_REAL_MODEL_ENABLED`：真实模型总闸；
- `MODEL_REVIEW_AUTO_RUN_ENABLED`：是否允许自动模型审查流程；
- `MODEL_REVIEW_SCHEDULER_ENABLED`：是否允许 scheduler 触发模型审查；
- `MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS`：允许被 scheduler 自动调用的模型白名单；
- `MODEL_REVIEW_DAILY_BUDGET_USD`：每日真实模型预算；
- `MODEL_REVIEW_MAX_RUNS_PER_4H`：每个 4h 周期最多真实模型调用次数；
- `MODEL_REVIEW_REUSE_MAX_BASE_BARS`：旧模型审查结果最多复用几根 base interval K线。

预算控制必须分调用前和调用后：

1. 调用前估算成本；
2. 判断今日已花费 + 本次预估成本是否超过预算；
3. 调用后记录实际成本；
4. 超预算时阻断调用并记录原因。

不得等调用完成后才发现超预算。

---

## 12. 超时、重试与长耗时处理

大模型不是毫秒级接口。模型调用可能持续十几秒甚至更久。

阶段 20/19 后续自动化必须设计为长耗时任务。

建议区分：

```text
connect_timeout
read_timeout
total_timeout
```

超时后应记录：

- `status = timeout`
- `error_code = provider_timeout`
- `trace_id`
- `attempt_no`
- `request_payload_hash`
- `provider_request_id`，如果供应商返回过

如果出现“请求可能已经到供应商但本地没收到响应”的情况，不得覆盖原 attempt，应记录为新的 attempt。

后续 retry 必须保留历史 attempt 记录，便于审计成本和排查。

---

## 13. raw request / raw response 存储规则

大模型返回内容可能很长，尤其是 reasoning / thinking 内容。

主业务表只保存：

- hash；
- 长度；
-摘要；
-结构化结果；
- artifact 引用；
- token / cost；
- 状态。

原始 request / response / reasoning 不应硬塞进主业务表。

超长内容必须隔离保存到 artifact，并记录：

```text
raw_request_storage_ref
raw_response_storage_ref
raw_response_hash
raw_response_char_count
raw_response_byte_count
```

阶段 20 聚合时应优先使用结构化结果摘要，不应把完整 reasoning 原文继续传给下一个模型，避免 token、成本和内存失控。

---

## 14. 建议新增模块

建议新增：

```text
app/model_review_aggregation/
```

可能包含：

```text
app/model_review_aggregation/service.py
app/model_review_aggregation/repository.py
app/model_review_aggregation/schema.py
app/model_review_aggregation/models.py
app/model_review_aggregation/review_necessity.py
app/model_review_aggregation/fingerprint.py
app/model_review_aggregation/chain_worker.py
app/model_review_aggregation/formatter.py
```

20A 可以先实现：

```text
service.py
repository.py
schema.py
review_necessity.py
fingerprint.py
```

20B/20C 再实现：

```text
chain_worker.py
chain_step 状态机
scheduler job
```

---

## 15. 建议新增 CLI

20A 建议新增：

```bash
python -m scripts.run_model_review_aggregation \
  --material-pack-id AMP-xxx \
  --trigger-source cli \
  --dry-run
```

正式写入：

```bash
python -m scripts.run_model_review_aggregation \
  --material-pack-id AMP-xxx \
  --trigger-source cli \
  --confirm-write
```

20A 第一版只允许 `trigger_source=cli`。

20A 不允许自动调用真实模型。

20B/20C 后续可新增 worker / scheduler 入口，但必须单独 plan 或明确追加本文件实现范围。

---

## 16. 建议新增数据表

20A 是否新增表由实现时检查现有结构后决定。

如果新增，建议表名：

```text
model_review_aggregation_run
```

核心字段建议：

```text
id
review_aggregation_run_id
material_pack_id
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval

status
trigger_source
created_by
trace_id

input_model_run_count
input_model_result_count
accepted_model_result_count
blocked_model_result_count
failed_model_result_count
skipped_model_result_count

aggregation_mode
model_review_invoked
model_review_invocation_mode
model_review_reused
reused_model_analysis_run_id
reused_model_review_created_at_utc
model_review_skip_reason
model_review_block_reason
invoked_model_keys_json
invoked_model_roles_json
model_review_chain_status
model_review_partial_failure_reason
latest_model_review_at_utc
model_review_basis
model_review_staleness_base_bars
model_review_reuse_max_base_bars

review_input_fingerprint
review_input_fingerprint_version

review_decision_summary
evidence_quality_summary
risk_acceptability_summary
strategy_conflict_summary
model_consensus_level
allowed_advice_mode
directional_trade_allowed

model_results_summary_json
model_disagreement_json
risk_warnings_json
missing_evidence_json
human_review_questions_json
summary_text

is_final_trading_advice
is_trading_signal
is_executable
auto_trading_allowed

created_at_utc
updated_at_utc
```

边界字段必须固定为：

```text
is_final_trading_advice = false
is_trading_signal = false
is_executable = false
auto_trading_allowed = false
```

### 16.1 后续接力链表

20B 可考虑新增：

```text
model_review_chain_run
model_review_chain_step
model_review_chain_attempt
```

但 20A 不强制实现。

---

## 17. 处理规则

### 17.1 没有阶段 19 结果

如果某材料包没有成功的 `model_analysis_result`：

```text
status = blocked
error_code = no_model_analysis_result
model_review_invoked = false
model_review_reused = false
model_review_basis = material_only
```

不得自动调用真实模型补齐。

20A 只输出：当前缺少模型审查结果。

### 17.2 有一个成功模型结果

生成单模型审查摘要。

不得伪装成多模型共识。

### 17.3 有多个成功模型结果

可以计算：

- 结论是否一致；
- 风险是否一致；
- 证据质量是否一致；
- 是否存在重大分歧；
- 是否需要人工复核。

### 17.4 存在 failed / blocked / skipped

默认不把 failed 结果作为有效审查结论。

但应记录：

- 成功数量；
- 失败数量；
- 阻断数量；
- 跳过数量；
- 是否影响最终聚合状态。

### 17.5 风险阻断

如果任一有效模型结果显示：

```text
risk_acceptability = unacceptable
```

阶段 20 应默认输出：

```text
directional_trade_allowed = false
allowed_advice_mode = wait_only
```

不得让阶段 21 生成方向性交易建议。

### 17.6 证据不足

如果有效模型结果显示：

```text
evidence_quality = insufficient
```

阶段 20 应默认限制阶段 21 只能输出等待、观察、证据不足类通知。

---

## 18. Hermes 告警

20A 第一版默认不主动发送 Hermes 告警。

以下异常可考虑 Hermes warning：

- 追溯链断裂；
- 阶段 19 结果边界字段异常，例如 `is_executable=true`；
- 模型接力 partial_success 长时间未恢复；
- 旧模型结果超过 3 根 base interval，但真实模型调用被配置阻断；
- 预算不足导致应审查但无法调用模型。

如果发送 Hermes，必须写入统一 `alert_message` 审计表。

---

## 19. 禁止事项

阶段 20 严禁：

- 直接调用真实 DeepSeek；
- 直接调用真实 GPT；
- 直接调用真实 Claude；
- 绕过阶段 19 调用模型供应商；
- 修改 K线数据；
- 修改市场快照；
- 修改阶段 16/17/18 的业务语义；
- 生成最终交易建议；
- 生成交易信号；
- 生成入场价、止损价、止盈价；
- 生成仓位或杠杆建议；
- 接入自动交易；
- 默认让 scheduler 自动真实调用模型。

---

## 20. 测试要求

20A 至少覆盖：

1. 没有模型审查结果时返回 blocked；
2. 单模型审查结果时生成单模型摘要；
3. 多模型审查结果时能识别一致和分歧；
4. failed / blocked / skipped 结果不会被误当成有效模型结论；
5. 旧结果未过期且指纹相似时允许复用；
6. 旧结果超过 3 根 base interval 时不允许继续复用；
7. 真实模型关闭且旧结果过期时，输出 `model_review_expired_but_real_model_disabled`；
8. profile_hash / prompt_template_hash / review_schema_version 变化时不复用旧结果；
9. 输出大模型参与状态字段完整；
10. 输出边界字段全部为 false；
11. dry-run 不写库；
12. confirm-write 正常写库；
13. 不调用任何真实大模型。

20B 后续至少覆盖：

1. step 1 success + step 2 failed；
2. resume 后只重跑 step 2；
3. 已成功 step 不重复真实调用；
4. 超过 retry 次数后 chain 为 partial_success 或 failed；
5. timeout attempt 可审计。

---

## 21. 验收标准

20A 验收标准：

1. 可以对已有阶段 19 结果生成模型审查聚合摘要；
2. 不触发真实模型调用；
3. 可以判断是否复用旧模型结果；
4. 默认复用上限为 3 根 base interval K线；
5. 超过 3 根后不继续复用；
6. 如果真实模型关闭，明确输出模型调用被配置阻断；
7. 输出大模型参与状态字段完整；
8. 不生成最终交易建议；
9. 不生成交易信号；
10. 不生成可执行交易结构；
11. 数据可追溯到 18、16、15；
12. 测试通过；
13. 文档说明清楚阶段边界。

20B/20C 验收标准另行细化。

---

## 22. 推荐验证命令

```bash
python -m pytest tests/model_review_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests -q
python -m alembic current -v
```

如果新增迁移：

```bash
python -m alembic upgrade head
python -m alembic current -v
```

20A 验证期间不得打开真实模型调用。

`.env` 应保持：

```env
MODEL_REVIEW_REAL_MODEL_ENABLED=false
```

---

## 23. 当前阶段结论

阶段 20 不是最终建议层。

阶段 20 是模型审查控制层，负责：

1. 判断是否需要模型审查；
2. 判断旧模型结果是否可以复用；
3. 管理未来模型接力链；
4. 聚合阶段 19 模型审查结果；
5. 给阶段 21 输出可安全使用的受约束输入；
6. 强制披露本轮大模型参与状态。

阶段 20A 应先做聚合与复用判断，不调用真实模型。

阶段 20B 再做接力链状态机。

阶段 20C 才允许 scheduler 在配置、预算、白名单、频率和状态机控制下自动触发真实模型审查。

最终交易建议应留到阶段 21。
