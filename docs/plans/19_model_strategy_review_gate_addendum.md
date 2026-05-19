# 第 19 阶段补充说明：模型配置、审查模式与人工确认边界

本文件是 `docs/plans/19_model_strategy_review_gate_plan.md` 的补充文件。两者共同构成第 19 阶段的完整计划。实现第 19 阶段时，必须同时阅读两个文件；如果两者存在冲突，以本补充文件中的更严格边界为准。

---

## 1. 补充文件定位

第 19 阶段已经确定为：

```text
大模型策略审查门控层
```

当前实现范围仍然限定为 19A：

```text
工程骨架 + mock provider + 数据库存储 + CLI + Hermes 中文摘要 + 测试
```

19A 暂不接入真实 DeepSeek、GPT、Claude 或其他真实大模型；暂不接入自动 scheduler；暂不实现微信人工补充材料入口。

但为了避免后续重构，19A 必须在设计上预留：

1. 模型配置注册表；
2. 多模型启停能力；
3. 横向对比模式；
4. 分析接力模式；
5. 人工审核标记；
6. 人工补充材料闭环的后续边界。

---

## 2. 19 主计划和 addendum 的关系

`19_model_strategy_review_gate_plan.md` 和本补充文件不是两个独立阶段。

它们是一体的：

```text
19 主计划：定义第 19 阶段主体目标、模块、流程、数据库、CLI、测试。
19 addendum：补充本轮讨论后确认的模型配置、多模型模式和人工审核边界。
```

Codex 实现时必须同时遵守两个文件。

如果 Codex 只能处理一个入口文件，可以在主计划文件末尾加入：

```markdown
## 补充说明

第 19 阶段还必须遵守：

docs/plans/19_model_strategy_review_gate_addendum.md

该补充文件与本计划共同构成第 19 阶段完整需求。若两者存在冲突，以补充文件中更严格的边界为准。
```

Codex 工作指令中也必须明确要求先阅读：

```text
docs/plans/19_model_strategy_review_gate_plan.md
docs/plans/19_model_strategy_review_gate_addendum.md
```

如果任一文件不存在，Codex 应停止实现并提示用户补充文件，不得自行创建或重写 plans。

---

## 3. 模型配置注册表

### 3.1 背景

大模型审查能力不应写死为单一 provider。

后续可能出现以下情况：

1. 今天使用 DeepSeek 分析；
2. 明天切换成 GPT 分析；
3. DeepSeek 和 GPT 分别独立分析；
4. DeepSeek 先分析数学结构，GPT 再做风险反驳；
5. 后续增加 Claude 或其他模型；
6. 某个模型使用效果不好，可以通过配置禁用。

因此，大模型应像策略一样配置化，不能只靠 `.env` 中的单一 `MODEL_REVIEW_PROVIDER` 控制。

### 3.2 配置文件目录

建议新增：

```text
configs/model_review/
```

建议文件：

```text
configs/model_review/model_registry.yaml
configs/model_review/mock_review.yaml
```

19A 暂时只需要 mock provider，但配置结构必须为未来 DeepSeek、GPT、Claude 预留。

后续可扩展：

```text
configs/model_review/deepseek_math_review.yaml
configs/model_review/gpt_risk_rebuttal_review.yaml
configs/model_review/claude_context_review.yaml
```

### 3.3 model_registry.yaml 建议结构

```yaml
enabled_models:
  - mock_review

default_mode: single

supported_modes:
  - single
  - relay_chain
  - parallel_comparison
```

说明：

1. `enabled_models` 控制当前启用哪些模型配置；
2. 19A 只实现 `single + mock`；
3. `relay_chain` 和 `parallel_comparison` 仅预留，不在 19A 完整实现；
4. 后续开启真实模型时，通过配置文件控制启停，不应改代码。

### 3.4 mock_review.yaml 建议结构

```yaml
model_key: mock_review
provider: mock
enabled: true
model_name: mock-reviewer
model_version: mock_v1
model_role: review_gate_mock
analysis_mode: single
prompt_template_version: review_gate_v1
review_schema_version: review_schema_v1
max_input_chars: 10000
max_output_chars: 10000
max_input_bytes: 32768
max_output_bytes: 32768
```

说明：

1. `enabled=false` 时，该模型配置不得参与审查；
2. `provider=mock` 时，不允许发起任何真实网络请求；
3. `model_key` 是模型配置的稳定标识，不等同于 provider 名称；
4. 后续多个模型可以共用同一 provider，但有不同 `model_key` 和 `model_role`。

### 3.5 `.env` 的职责

`.env` 只负责总开关、密钥、配置目录等环境级设置。

建议保留或新增：

```env
MODEL_REVIEW_ENABLED=false
MODEL_REVIEW_DRY_RUN=true
MODEL_REVIEW_CONFIG_DIR=configs/model_review
MODEL_REVIEW_HERMES_ENABLED=false
```

未来真实模型密钥也应放 `.env`，例如：

```env
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

但每个模型是否启用、模型角色、prompt 版本、schema 版本、分析模式，不应全部塞进 `.env`，应由 `configs/model_review/*.yaml` 管理。

---

## 4. 模型审查模式预留

第 19 阶段后续需要同时支持两种模型协作模式：

1. 横向对比；
2. 分析接力。

19A 不完整实现这两种模式，但数据库字段、payload、配置结构需要预留。

### 4.1 single 模式

`single` 是 19A 当前实现模式。

流程：

```text
analysis_material_pack
        ↓
一个 mock model 独立审查
        ↓
保存 model_analysis_run
        ↓
保存 model_analysis_result
```

19A 只要求跑通此模式。

### 4.2 parallel_comparison 横向对比模式

横向对比是指多个模型分别对同一个材料包进行独立审查。

流程：

```text
同一个 analysis_material_pack
        ↓
DeepSeek 独立审查
GPT 独立审查
Claude 独立审查
        ↓
系统比较模型分歧与一致性
```

用途：

1. 新策略上线后的模型理解稳定性评估；
2. 多模型表现横向对比；
3. 策略严重冲突时的多模型复核；
4. 定期抽样复盘；
5. 发现模型偏差和模型分歧。

横向对比不建议作为每次 4h 的默认流程，否则成本高、输出冗余、后续处理复杂。

### 4.3 relay_chain 分析接力模式

分析接力是指多个模型按角色串联处理。

示例：

```text
DeepSeek：先做数学结构、江恩结构、价格结构审查
        ↓
GPT：基于 DeepSeek 结论做风险反驳、过度交易过滤、证据缺口审查
        ↓
系统保存接力链路和最终审查结论
```

用途：

1. 日常生产决策链路；
2. 充分利用不同模型长处；
3. 让模型分工，而不是重复输出类似结论。

建议后续日常主流程优先使用分析接力，横向对比用于评估、抽检、分歧复核。

### 4.4 日常模式分配原则

后续完整实现时，建议：

```text
普通材料包：
不调用真实模型，或只做轻量 mock / skipped。

有方向型策略候选，但风险不高：
可使用单模型审查。

有方向型策略候选，且可能进入后续建议层：
使用分析接力。

策略严重分歧 / 风控高风险 / 模型审查不确定：
使用横向对比。

新策略上线初期：
提高横向对比比例，用于评估模型审查质量。

用户手动请求：
允许手动触发横向对比或分析接力。
```

---

## 5. 数据库字段预留要求

为避免后续重构，19A 的数据库表和 payload 建议预留以下字段。

### 5.1 model_analysis_run 建议补充字段

```text
model_key
model_role
analysis_mode
chain_id
chain_step
parent_model_analysis_run_id
comparison_group_id
```

字段说明：

1. `model_key`：来自 `configs/model_review/*.yaml` 的稳定模型配置标识；
2. `model_role`：模型在审查链路中的角色，例如 `math_structure_review`、`risk_rebuttal_review`、`review_gate_mock`；
3. `analysis_mode`：`single`、`relay_chain`、`parallel_comparison`；
4. `chain_id`：分析接力链路 ID；
5. `chain_step`：接力步骤序号；
6. `parent_model_analysis_run_id`：接力模式中上一轮模型审查 run id；
7. `comparison_group_id`：横向对比中同一组模型审查的组 ID。

19A 只使用：

```text
model_key = mock_review
model_role = review_gate_mock
analysis_mode = single
```

其他字段可以为空。

### 5.2 model_analysis_result 建议补充字段

```text
human_review_required
```

字段说明：

1. `human_review_required` 是人工介入标记；
2. 它不是失败标记；
3. 它可以出现在成功审查结果中；
4. 后续用于快速筛选“哪些审查结果需要用户处理”。

---

## 6. human_review_required 规则

### 6.1 基本定义

`human_review_required` 表示：

```text
模型审查已经完成，但审查结论需要人工进一步判断、确认或补充材料。
```

它不等于：

```text
blocked
failed
系统错误
```

### 6.2 和 blocked 的区别

`blocked` 表示系统无法完成审查，例如：

1. material pack 不存在；
2. material pack 非 success；
3. 输入超长；
4. 输出超长；
5. schema 不合法；
6. 输出包含禁止交易字段；
7. 配置不允许 confirm-write；
8. 数据库写入失败。

`human_review_required` 表示审查可以完成，但结论需要人工介入，例如：

1. 多策略严重分歧；
2. 多模型严重分歧；
3. 外部事件未接入系统；
4. 系统不知道用户是否已有仓位；
5. 用户是否执行过上一条建议影响判断；
6. 需要人工选择是否继续观察；
7. 风险边界涉及人工纪律判断。

### 6.3 和 require_more_evidence 的区别

`require_more_evidence` 表示：

```text
证据不足，不能继续推进。
```

但它不一定需要人工补材料。

系统可自动获得的数据缺失时：

```text
review_decision = require_more_evidence
human_review_required = false
status = blocked 或 success，视具体场景而定
```

需要人工判断的信息缺失时：

```text
review_decision = require_more_evidence 或 human_review_required
human_review_required = true
status = success
```

原则：

```text
人只补系统无法自动获取、无法由策略重新计算、无法由数据库恢复的信息。
系统自己能拿到的数据，缺了就是系统链路问题，不能转嫁给人工。
```

### 6.4 需要人工补充材料的典型场景

可以触发 `human_review_required=true` 的场景：

1. 系统不知道用户当前是否已有仓位；
2. 系统不知道用户是否已经按上一条建议执行；
3. 系统不知道用户是否已经减仓、平仓或止损；
4. 当前判断依赖用户风险偏好或纪律选择；
5. 当前存在外部重大事件，而系统尚未接入新闻源或宏观事件源；
6. 多策略分歧严重，系统不应自动推进；
7. 多模型分歧严重，需要人工判断是否继续观察；
8. 模型认为风险高，但并非系统错误，需要用户决定是否继续跟踪。

不应触发人工补充的场景：

1. K线缺失；
2. 快照不完整；
3. 策略结果字段缺失；
4. material pack schema 错误；
5. 模型输出格式错误；
6. 配置文件缺失；
7. enabled=true 的策略运行失败；
8. 数据库缺表或迁移错误。

这些是系统问题，应 blocked / failed / alert，不应要求用户补充。

---

## 7. 人工补充材料闭环

### 7.1 19A 不实现

19A 不实现微信自然语言回复入口，不实现 Skill 解析，不实现人工补充材料入库。

19A 只需要：

1. 在模型审查结果中保存 `human_review_required`；
2. 在 Hermes 中文消息中说明是否需要人工审核；
3. 在输出中保留 `human_review_questions`；
4. 为后续人工补充材料闭环预留边界。

### 7.2 后续推荐链路

后续人工补充材料入口建议采用：

```text
微信自然语言回复
        ↓
Skill 解析成结构化草稿
        ↓
微信把解析结果发给用户确认
        ↓
用户确认 / 修改 / 取消
        ↓
系统业务校验
        ↓
写入 human_review_input
        ↓
绑定 review_id / material_pack_id / model_analysis_run_id
```

关键规则：

```text
Skill 输出只是结构化草稿，不是最终事实。
未经用户确认，不得写入核心业务表。
```

### 7.3 建议状态流

后续可设计：

```text
pending_parse
parsed_pending_confirm
confirmed
validation_failed
accepted
rejected
expired
```

含义：

1. `pending_parse`：收到自然语言，等待解析；
2. `parsed_pending_confirm`：已解析，等待用户确认；
3. `confirmed`：用户已确认；
4. `validation_failed`：系统业务校验失败；
5. `accepted`：校验通过，正式入库；
6. `rejected`：用户否认或取消；
7. `expired`：超时未确认。

### 7.4 后续 human_review_input 表建议

后续单独阶段可新增：

```text
human_review_input
```

建议字段：

```text
id
human_review_input_id
review_id
material_pack_id
model_analysis_run_id
raw_message
parsed_json
confirmation_message
confirmed_json
parse_status
confirm_status
validation_status
source
confidence
confirmed_at_utc
created_at_utc
updated_at_utc
```

正式业务只读：

```text
confirmed_json
```

不直接读取未经确认的 `parsed_json`。

---

## 8. Hermes 文案补充

第 19 阶段 Hermes 中文消息中，如果 `human_review_required=true`，必须明确提示：

```text
本次审查需要人工确认。
```

同时列出：

1. 需要人工确认的问题；
2. material_pack_id；
3. model_analysis_run_id；
4. trace_id；
5. 这不是最终交易建议；
6. 本阶段未自动交易；
7. 本阶段未生成订单；
8. 本阶段未给出仓位或杠杆。

19A 不需要支持用户直接回复处理，但应让消息内容为后续微信确认链路预留字段。

---

## 9. 测试补充要求

在 `tests/model_analysis/` 中补充或确认以下测试：

1. 可以读取 `configs/model_review/model_registry.yaml`；
2. 可以读取 `configs/model_review/mock_review.yaml`；
3. `enabled=false` 的模型不会被加载；
4. 19A 默认只使用 `single + mock`；
5. `analysis_mode` 支持 `single`；
6. `relay_chain` 和 `parallel_comparison` 在 19A 不完整执行；
7. `model_analysis_run` 不对 `review_version_key` 建唯一约束；
8. `model_analysis_result` 对 `review_version_key` 建唯一约束；
9. `model_analysis_run` 包含或可表达 `model_key`、`model_role`、`analysis_mode`；
10. 接力和横向对比字段可以为空；
11. `model_analysis_result.human_review_required` 可以落表；
12. `human_review_required=true` 不会被当作 blocked；
13. `review_decision=human_review_required` 可以作为 success 结果保存；
14. Hermes 中文文案在需要人工审核时包含“需要人工确认”；
15. 19A 不实现微信回复入口；
16. 19A 不调用真实大模型；
17. 19A 不执行自动 scheduler。

---

## 10. 当前实现范围确认

本补充文件不会扩大 19A 的实现范围到真实模型或微信入口。

19A 仍然只实现：

```text
single + mock
```

但必须避免把系统写死为：

```text
单一 provider
单一模型
单一审查模式
无法配置启停
无法横向对比
无法分析接力
无法筛选人工审核结果
```

---

## 11. 最终原则

第 19 阶段的大模型能力应像策略一样可配置、可启停、可扩展、可复盘。

当前不急于接入真实 DeepSeek 或 GPT，但不能把代码写成后续只能支持一个模型。

日常生产长期建议：

```text
分析接力作为主流程。
横向对比作为评估、抽检、分歧复核工具。
```

人工补充材料长期建议：

```text
微信自然语言输入 + Skill 结构化解析 + 微信确认 + 系统校验 + 正式入库。
```

但这些都不是 19A 的完整实现范围，只是 19A 必须预留的设计方向。
