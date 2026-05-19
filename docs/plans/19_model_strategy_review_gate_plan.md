# 第 19 阶段：大模型策略审查门控层（完整版 v2）

---

## 1. 阶段定位

第 19 阶段不是“大模型行情预测层”，也不是“大模型最终交易建议层”。

本阶段的定位是：

> 让大模型对第 18 阶段生成的策略聚合材料包进行审查，判断策略假设、数学材料、风险条件和多策略冲突是否足够支持继续推进。

大模型在本阶段的角色是“审查员”，不是“策略本体”，更不是“交易员”。

第 19 阶段的核心目标：

1. 审查已有策略结果是否逻辑自洽；
2. 审查数学材料是否支持当前分析假设；
3. 动态审查 N 个策略之间的一致、分歧和冲突；
4. 识别证据缺口；
5. 做风险反驳和过度交易过滤；
6. 生成条件化验证计划和人工审核问题；
7. 保存模型审查过程、版本、输入摘要、输出结构化结果；
8. 通过 Hermes 发送中文摘要，但必须明确这不是最终交易建议。

---

## 2. 阶段名称

建议文件名：

```text
19_model_strategy_review_gate.md
```

中文名称：

```text
第 19 阶段：大模型策略审查门控层
```

英文辅助名：

```text
Model Strategy Review Gate
```

---

## 3. 核心原则

第 19 阶段回答的问题不是：

```text
现在应该做多还是做空？
```

而是：

```text
当前策略假设是否值得继续推进？
证据是否足够？
风险是否可接受？
多策略冲突是否过大？
是否需要等待或人工审核？
```

大模型不得绕过策略层直接裸判行情。大模型只能审查材料、策略结果、冲突、风险和证据质量。

---

## 4. 与前置阶段关系

### 4.1 上游输入

第 19 阶段只消费第 18 阶段输出：

1. `strategy_aggregation_run`
2. `analysis_material_pack`

第 19 阶段不直接读取原始 K线后自行判断方向。

如需追溯市场上下文，只能通过第 18 阶段材料包中的：

1. `snapshot_id`
2. `material_pack_id`
3. `aggregation_run_id`
4. 时间窗口
5. 恢复契约
6. 摘要指标
7. 策略结果摘要

### 4.2 下游输出

第 19 阶段输出的是：

```text
模型审查结果
```

不是：

```text
最终交易建议
```

后续如果需要生成最终建议，应由独立阶段完成，例如：

```text
第 20+ 阶段：最终建议聚合层 / 人工执行辅助层
```

---

## 5. 本阶段允许做

第 19 阶段允许：

1. 读取成功状态的 `analysis_material_pack`；
2. 构造大模型审查输入；
3. 支持 mock model / dry-run；
4. 在显式配置和显式参数下调用真实模型；
5. 审查已有策略结果；
6. 审查数学材料；
7. 动态审查 N 个策略之间的冲突；
8. 识别证据缺口；
9. 进行风险反驳和过度交易过滤；
10. 生成条件化验证计划；
11. 生成人工审核问题；
12. 保存模型审查运行记录；
13. 保存结构化模型审查结果；
14. 发送 Hermes 中文摘要；
15. 记录模型 provider、模型名、模型版本、prompt 版本、schema 版本、trace_id。

---

## 6. 本阶段禁止做

第 19 阶段禁止：

1. 不允许自动交易；
2. 不允许调用交易接口；
3. 不允许生成订单；
4. 不允许生成杠杆建议；
5. 不允许生成仓位建议；
6. 不允许生成最终入场价；
7. 不允许生成最终止损价；
8. 不允许生成最终止盈价；
9. 不允许把模型输出当成最终交易建议；
10. 不允许绕过第 18 阶段材料包直接让模型裸判行情；
11. 不允许在本阶段实现真实策略；
12. 不允许新增或实现 `GannStrategy`、`TrendStrategy`、`RiskControlStrategy` 等策略类；
13. 不允许修改正式 K线表；
14. 不允许将完整 prompt、完整 response、完整 debug dump、大段上下文无限制写入数据库；
15. 不允许写死策略数量；
16. 不允许写死策略名称。

---

## 7. 大模型在本阶段的核心能力

### 7.1 策略结果审查

大模型应审查已有策略输出是否自洽，例如：

1. 策略结论是否和理由匹配；
2. 策略证据是否足够；
3. 失效条件是否明确；
4. 目标空间和风险是否合理；
5. 是否存在单一策略过度乐观；
6. 是否应允许进入下一层建议流程。

### 7.2 数学材料分析

大模型可以分析第 18 阶段整理出的数学材料，例如：

1. 支撑压力摘要；
2. 波动率摘要；
3. 多周期结构摘要；
4. 近期高低点；
5. 区间位置；
6. 风险条件；
7. 验证计划。

但大模型不负责重新计算指标，只负责解释这些材料是否支持当前假设。

### 7.3 动态多策略冲突审查

第 19 阶段不得假设策略数量固定，也不得假设策略名称固定。

无论未来是 3 个策略、10 个策略还是 30 个策略，第 19 阶段都必须消费动态列表：

```json
{
  "strategy_results": [
    {
      "strategy_name": "gann_placeholder",
      "strategy_version": "placeholder_v1",
      "strategy_role": "directional",
      "enabled": true,
      "status": "success",
      "hypothesis_direction": "long_hypothesis",
      "evidence_quality": "medium",
      "risk_level": "medium",
      "summary": "...",
      "reason_codes": ["..."],
      "missing_evidence": []
    }
  ]
}
```

大模型审查逻辑必须基于通用字段进行动态分组：

1. `strategy_name`
2. `strategy_version`
3. `strategy_role`
4. `enabled`
5. `status`
6. `hypothesis_direction`
7. `risk_level`
8. `evidence_quality`
9. `summary`
10. `reason_codes`
11. `missing_evidence`

禁止在代码里写死：

```text
如果 gann 看多、trend 看空，则……
```

正确做法是：

```text
遍历 strategy_results，按方向、角色、风险、证据质量动态分组后审查。
```

动态输入不等于无限输入。第 19 阶段必须先做策略摘要压缩：

1. 只接收 enabled=true 且已经产生有效结果的策略摘要；
2. 不接收每个策略的完整 debug dump；
3. 不接收完整指标序列；
4. 不接收完整中间计算过程；
5. 每个策略最多保留有限数量的 reason codes、missing evidence 和风险说明；
6. 当策略数量较多时，优先保留高风险、强冲突、高证据质量、人工审核相关的策略摘要；
7. 低权重、低证据、重复性强的策略结果应聚合为分组摘要；
8. 超过输入长度限制时，必须 blocked 或要求人工审核，不允许自动扩大 prompt。

建议配置：

```env
MODEL_REVIEW_MAX_STRATEGY_ITEMS=30
MODEL_REVIEW_MAX_REASON_ITEMS_PER_STRATEGY=5
MODEL_REVIEW_MAX_MISSING_EVIDENCE_ITEMS_PER_STRATEGY=5
MODEL_REVIEW_MAX_RISK_WARNING_ITEMS=10
MODEL_REVIEW_MAX_TOTAL_INPUT_CHARS=10000
```

### 7.4 证据缺口检查

大模型应能明确输出：

```text
当前材料不足，不能推进。
```

典型缺失证据包括：

1. 缺少失效条件；
2. 缺少目标空间；
3. 缺少关键价位；
4. 缺少多周期确认；
5. 缺少风控边界；
6. 缺少策略版本；
7. 缺少策略理由；
8. 缺少数据质量说明。

### 7.5 风险反驳与过度交易过滤

大模型必须能输出：

```text
有方向，但不值得交易。
证据不足，应等待。
风险过高，不应推进。
策略冲突过大，需要人工审核。
```

这不是保守，而是本系统的关键价值。

### 7.6 条件化验证计划

大模型应输出后续观察条件，例如：

1. 哪些条件满足后，当前假设增强；
2. 哪些条件发生后，当前假设失效；
3. 未来几根 4h K线应重点验证什么；
4. 哪些风险信号需要人工确认。

### 7.7 人工审核问题生成

大模型应能生成给用户或后续 Admin 的审核问题，例如：

1. 当前是否允许推进候选？
2. 哪些冲突需要人工判断？
3. 是否需要等待下一根 4h K线？
4. 是否需要暂时停止交易？
5. 是否需要补充策略证据？

---

## 8. 动态策略输入设计

### 8.1 不写死策略数量

第 19 阶段必须支持 N 个策略输入。

禁止在任何代码、prompt、schema 中假设：

```text
系统只有江恩、趋势、风控三个策略。
```

### 8.2 不写死策略名称

第 19 阶段不得依赖固定策略名判断逻辑。

允许把策略名作为展示信息和审计信息，但审查逻辑应基于策略角色和通用字段。

### 8.3 策略角色

建议策略角色使用通用枚举：

```text
directional          方向策略
risk_control         风控策略
support_resistance   支撑压力策略
volatility           波动率策略
volume               成交量策略
cycle                周期策略
sentiment            情绪策略
filter               过滤器策略
meta                 元审查策略
placeholder          占位策略
```

### 8.4 策略启停

策略启停由 `configs/strategies/*.yaml` 控制，例如：

```yaml
strategy_name: gann_placeholder
strategy_version: placeholder_v1
enabled: true
base_interval_value: 4h
higher_interval_value: 1d
```

第 19 阶段只消费第 18 阶段已经聚合的策略结果，不直接决定某个策略是否运行。

如果某策略配置为 `enabled: false`，上游策略阶段不应运行该策略，第 18 阶段也不应把它作为有效结果交给第 19 阶段。

---

## 9. 输入材料压缩规则

如果未来策略数量达到 30 个以上，不能把所有策略完整 debug 都传给大模型。

第 18 阶段应先聚合和压缩：

1. 所有策略结果入库；
2. 第 18 阶段按方向、角色、风险、证据质量分组；
3. 只把关键冲突、代表性理由、风险否决项、缺失证据、摘要列表传给第 19 阶段；
4. 第 19 阶段审查摘要，不消费无限长原始 debug。

第 19 阶段输入必须遵守大字段人工审核规则。

---

## 10. 数据库大字段人工审核规则

第 19 阶段必须遵守数据库字段长度和大字段人工审核规则。

凡是字段内容预计或实际满足任一条件，必须进入人工审核：

1. 超过 32KB；
2. 超过 10,000 个字符；
3. 后续可能持续追加增长；
4. 单字段内容无法明确说明查询价值；
5. 字段只是为了方便保存完整上下文；
6. 字段内容可以通过其他表、ID、时间范围、版本号重新恢复；
7. 字段可能被 Codex 或后续自动化流程不断扩大。

未经人工审核，不允许将以下内容直接写入单个 Text/JSON 字段：

1. 完整 prompt；
2. 完整 response；
3. 完整模型接力上下文；
4. 完整策略 debug dump；
5. 完整指标序列；
6. 完整 K线窗口；
7. 大段日志；
8. 大段历史上下文。

数据库优先保存：

1. 摘要；
2. hash；
3. 版本号；
4. ID；
5. 时间范围；
6. 恢复契约；
7. 关键证据；
8. 结构化审查结果。

---

## 11. 模型配置设计

### 11.1 `.env` 总开关

建议新增：

```env
MODEL_REVIEW_ENABLED=false
MODEL_REVIEW_DRY_RUN=true
MODEL_REVIEW_PROVIDER=mock
MODEL_REVIEW_CONFIG_DIR=configs/models
MODEL_REVIEW_MAX_INPUT_CHARS=10000
MODEL_REVIEW_MAX_OUTPUT_CHARS=10000
MODEL_REVIEW_HERMES_ENABLED=false
MODEL_REVIEW_TIMEOUT_SECONDS=60
MODEL_REVIEW_MAX_STRATEGY_ITEMS=30
MODEL_REVIEW_MAX_REASON_ITEMS_PER_STRATEGY=5
MODEL_REVIEW_MAX_MISSING_EVIDENCE_ITEMS_PER_STRATEGY=5
MODEL_REVIEW_MAX_RISK_WARNING_ITEMS=10
MODEL_REVIEW_MAX_TOTAL_INPUT_CHARS=10000
```

配置语义必须明确：

1. `MODEL_REVIEW_ENABLED=false` 表示禁止真实写库、真实模型调用和真实 Hermes 发送；
2. `MODEL_REVIEW_ENABLED=false` 不应阻止 dry-run 和 mock 验证；
3. dry-run 可以构造输入、执行 mock provider、校验 schema、打印结果，但不得写库；
4. confirm-write 必须显式指定，并且需要 `MODEL_REVIEW_ENABLED=true` 才能真实写库；
5. 真实模型 provider 本轮不实现，即使配置为非 mock，也必须 blocked 或报未实现。

说明：

1. 默认不启用真实模型；
2. 默认 dry-run；
3. 默认 provider 为 mock；
4. 真实 provider 必须显式配置；
5. 超过输入输出长度限制必须 blocked；
6. Hermes 默认关闭，验证后再开启。

### 11.2 模型配置文件

建议新增：

```text
configs/models/mock.yaml
configs/models/deepseek.yaml
```

示例：

```yaml
provider: deepseek
enabled: false
model_name: deepseek-reasoner
model_version: ""
timeout_seconds: 60
max_input_chars: 10000
max_output_chars: 10000
prompt_template_version: review_gate_v1
analysis_schema_version: model_review_schema_v1
```

`.env` 放密钥和总开关，模型参数放 `configs/models/*.yaml`。

---

## 12. 数据库设计草案

### 12.1 `model_review_run`

用于记录一次模型审查运行。

建议字段：

```text
id
model_review_run_id
aggregation_run_id
material_pack_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
review_version_key
review_schema_version
prompt_template_version
model_provider
model_name
model_version
model_role
review_mode
status
input_material_hash
input_summary_json
input_char_count
output_char_count
is_final_trading_advice
is_trading_signal
is_executable
auto_trading_allowed
trace_id
trigger_source
created_by
error_code
error_message
hermes_enabled
hermes_status
hermes_message
hermes_error
hermes_sent_at_utc
created_at_utc
updated_at_utc
```

硬约束：

```text
is_final_trading_advice = false
is_trading_signal = false
is_executable = false
auto_trading_allowed = false
```

唯一约束：

```text
model_review_run_id 唯一
```

`model_review_run` 是 attempt 表，用于记录每一次审查尝试。它允许同一个 `review_version_key` 出现多次，用于记录 blocked、failed、success 等不同尝试。

禁止在 `model_review_run` 上对 `review_version_key` 建唯一约束。否则第一次 blocked / failed 会占用版本键，导致后续修复后 success 重跑被数据库锁死。

禁止使用多个 VARCHAR 字段组成大复合唯一索引。

普通索引建议：

```text
material_pack_id
aggregation_run_id
strategy_signal_run_id
status, created_at_utc
trace_id
```

### 12.2 `model_review_result`

用于保存结构化审查结果。

建议字段：

```text
id
model_review_run_id
material_pack_id
aggregation_run_id
review_version_key
review_decision
reviewed_hypotheses_json
evidence_quality_level
logic_consistency_level
risk_acceptability_level
strategy_conflict_level
overtrading_risk_level
human_review_required
supporting_reasons_json
opposing_reasons_json
risk_warnings_json
missing_evidence_json
rejection_reasons_json
conditions_to_reconsider_json
validation_focus_json
human_review_questions_json
summary_text
not_trading_advice_text
created_at_utc
updated_at_utc
```

唯一约束：

```text
review_version_key 唯一
```

`model_review_result` 只保存 success / partial_success 的最终审查结果。`review_version_key` 只允许在 result 表唯一，不允许在 run 表唯一。

业务层查询已有结果时，只查询 success / partial_success 的 result。若存在同一个 `review_version_key` 的成功结果，应返回 skipped / already_exists。

`review_decision` 建议枚举：

```text
accept_candidate
reject_candidate
require_more_evidence
wait
stop_trading_review
human_review_required
insufficient_material
schema_invalid
```

注意：

1. `accept_candidate` 只表示允许进入下一层候选流程；
2. `accept_candidate` 不是最终交易建议；
3. `stop_trading_review` 不是最终交易命令；
4. `human_review_required` 表示需要人工审核，不表示系统失败。

---

## 13. 模块设计

建议新增目录：

```text
app/model_review/
```

建议文件：

```text
app/model_review/types.py
app/model_review/payloads.py
app/model_review/prompt_builder.py
app/model_review/schema_validator.py
app/model_review/model_client.py
app/model_review/providers/base.py
app/model_review/providers/mock.py
app/model_review/providers/deepseek.py
app/model_review/repository.py
app/model_review/service.py
app/model_review/hermes_formatter.py
```

职责：

1. `types.py`：枚举、常量、状态；
2. `payloads.py`：输入输出 DTO；
3. `prompt_builder.py`：构造审查输入，不调用模型；
4. `schema_validator.py`：校验模型输出结构；
5. `model_client.py`：provider 调用抽象；
6. `providers/mock.py`：mock provider；
7. `providers/deepseek.py`：DeepSeek provider 封装，不写业务判断；
8. `repository.py`：数据库读写；
9. `service.py`：流程编排；
10. `hermes_formatter.py`：中文 Hermes 消息格式化。

---

## 14. 核心流程

```text
读取 analysis_material_pack
        ↓
校验 material pack 状态、版本、长度
        ↓
读取聚合策略摘要
        ↓
构造动态 strategy_results 列表
        ↓
构造模型审查输入
        ↓
选择 provider：mock / deepseek
        ↓
dry-run / confirm-write / real-model
        ↓
解析模型输出
        ↓
校验输出 schema
        ↓
保存 model_review_run
        ↓
保存 model_review_result
        ↓
可选发送 Hermes 中文摘要
```

---

## 15. CLI 设计

新增脚本：

```text
scripts/run_model_review.py
```

默认 dry-run：

```bash
python -m scripts.run_model_review \
  --material-pack-id "..." \
  --trigger-source cli \
  --dry-run
```

真实写库：

```bash
python -m scripts.run_model_review \
  --material-pack-id "..." \
  --trigger-source cli \
  --confirm-write
```

真实调用模型必须显式开启：

```bash
python -m scripts.run_model_review \
  --material-pack-id "..." \
  --trigger-source cli \
  --confirm-write \
  --use-real-model
```

默认行为：

1. 默认 dry-run；
2. 默认不写库；
3. 默认不调用真实模型；
4. 默认不发 Hermes；
5. 必须显式参数才写库或调用真实 provider。

---

## 16. 调度策略

第 19 阶段初期只支持 CLI 手动触发。

不建议一开始接入自动 scheduler。

原因：

1. 真实模型调用有成本；
2. 真实策略尚未完整接入；
3. 需要先观察审查质量；
4. 避免模型输出被误读为最终建议。

后续如果接入自动触发，必须满足：

1. `MODEL_REVIEW_ENABLED=true`；
2. `MODEL_REVIEW_AUTO_RUN_ENABLED=true`；
3. 第 18 阶段 material pack 状态为 success；
4. 输入长度未超限；
5. provider 配置有效；
6. 幂等检查通过；
7. 不重复调用同一 `material_pack + model + prompt_version + schema_version`。

---

## 17. 幂等规则

第 19 阶段必须幂等。

建议使用：

```text
review_version_key
```

计算来源：

```text
material_pack_id
model_provider
model_name
model_version
prompt_template_version
review_schema_version
review_mode
```

同一个 `review_version_key` 已经在 `model_review_result` 中存在 success / partial_success 结果时：

```text
返回 skipped / already_exists
```

`model_review_run` 不得因为同一个 `review_version_key` 而拒绝插入 attempt 记录。

blocked / failed 不应永久锁死后续重跑。

可以保留 blocked / failed 尝试记录，但不能阻止后续 success。

幂等规则必须遵守：

1. run 表记录尝试，不对 `review_version_key` 唯一；
2. result 表记录最终成功结果，只在 result 表对 `review_version_key` 唯一；
3. service 查询已有结果时，只查 result 表中的 success / partial_success；
4. 如果并发写入 result 触发唯一冲突，应重新查询已有 success / partial_success，并返回 skipped / already_exists；
5. 并发唯一冲突不应被记录为 failed。

---

## 18. 状态设计

建议状态：

```text
pending
running
success
partial_success
failed
blocked
skipped
```

`blocked` 和 `human_review_required` 必须严格区分：

```text
blocked：
系统无法完成审查，例如材料包不存在、输入超长、schema 非法、配置缺失。

human_review_required：
系统已经完成审查，审查结论要求人工介入。它是 review_decision，不是运行失败。
```

如果模型审查成功并得出 `human_review_required`，运行状态应是 success 或 partial_success，不能简单记为 blocked。

blocked 场景：

1. material pack 不存在；
2. material pack 不是 success；
3. 输入超过 10,000 字符；
4. 输入超过 32KB；
5. prompt 构造失败；
6. 模型输出 schema 不合法；
7. 配置禁止真实模型调用；
8. 缺少 provider 配置；
9. 输入或输出触发大字段人工审核且无法自动继续；
10. strategy_results 为空且当前模式要求必须有策略结果。

failed 场景：

1. provider 调用异常；
2. 网络异常；
3. API 返回错误；
4. 数据库写入失败；
5. Hermes 发送失败。

Hermes 失败不应把模型审查主结果改为 failed，但必须记录 `hermes_status=failed`。

---

## 19. 模型输出 Schema

模型输出必须结构化。

建议 JSON schema：

```json
{
  "review_decision": "accept_candidate | reject_candidate | require_more_evidence | wait | stop_trading_review | human_review_required | insufficient_material",
  "evidence_quality_level": "strong | moderate | weak | insufficient",
  "logic_consistency_level": "strong | partial | weak | inconsistent",
  "risk_acceptability_level": "acceptable | caution | high_risk | unacceptable",
  "strategy_conflict_level": "none | low | medium | high | severe",
  "overtrading_risk_level": "low | medium | high",
  "human_review_required": true,
  "supporting_reasons": [],
  "opposing_reasons": [],
  "risk_warnings": [],
  "missing_evidence": [],
  "rejection_reasons": [],
  "conditions_to_reconsider": [],
  "validation_focus": [],
  "human_review_questions": [],
  "not_trading_advice": true
}
```

模型输出不得包含：

1. 具体下单指令；
2. 杠杆；
3. 仓位比例；
4. 最终入场价；
5. 最终止损价；
6. 最终止盈价；
7. “立即买入 / 立即卖出”等表达；
8. 自动执行建议。

---

## 20. Hermes 通知规则

第 19 阶段 Hermes 消息必须使用中文。

标题示例：

```text
BTC 大模型策略审查结果
```

正文必须包含：

```text
这是大模型对策略材料包的审查结果，不是最终交易建议。
本阶段未自动交易。
本阶段未生成订单。
本阶段未给出仓位或杠杆。
```

建议包含：

1. symbol；
2. base interval；
3. material_pack_id；
4. model provider；
5. model name；
6. review_decision；
7. evidence_quality_level；
8. risk_acceptability_level；
9. strategy_conflict_level；
10. human_review_required；
11. missing_evidence；
12. trace_id。

---

## 21. 测试要求

新增测试目录：

```text
tests/model_review/
```

必须覆盖：

1. dry-run 不写库；
2. confirm-write 才写库；
3. 默认不调用真实模型；
4. mock model 返回可解析结构；
5. DeepSeek provider 未开启时不会调用；
6. material pack 不存在时 blocked；
7. material pack 非 success 时 blocked；
8. 输入超过 10,000 字符时 blocked；
9. 输入超过 32KB 时 blocked；
10. 输出超过 10,000 字符时 blocked 或需要人工审核；
11. 模型输出 schema 不合法时 blocked；
12. success 后重复运行返回 skipped；
13. failed / blocked 后允许重跑；
14. Hermes 中文消息包含“不是最终交易建议”；
15. 不生成入场、止损、止盈、仓位、杠杆字段；
16. 不调用交易接口；
17. 不修改 K线表；
18. 不写死策略数量；
19. 不写死策略名称；
20. N 个 strategy_results 可以动态输入。

---

## 22. 验收标准

第 19 阶段完成时，必须满足：

1. 可以通过 CLI 对一个 material pack 执行 dry-run；
2. 可以通过 mock model 生成结构化审查结果；
3. 可以在显式配置和显式参数下调用真实 DeepSeek；
4. 可以保存 `model_review_run`；
5. 可以保存 `model_review_result`；
6. Hermes 中文摘要格式正确；
7. 所有输出明确不是最终交易建议；
8. 不生成交易执行字段；
9. 不自动交易；
10. 不修改 K线表；
11. 不保存超过 10,000 字符或 32KB 的字段内容，除非人工审核；
12. 支持 N 个策略结果动态审查；
13. 不写死策略数量和策略名称；
14. 重复运行具备幂等性；
15. failed / blocked 不会锁死后续重跑；
16. human_review_required 与 blocked 语义分离；
17. MODEL_REVIEW_ENABLED=false 时 dry-run 可用；
18. 30 个策略输入时不会超过长度限制，不会全量塞入 prompt；
19. 测试通过。

---

## 23. 本阶段不解决的问题

第 19 阶段不解决：

1. 真实江恩策略开发；
2. 真实趋势策略开发；
3. 真实风控策略开发；
4. 最终交易建议生成；
5. 仓位管理；
6. 杠杆建议；
7. 自动交易；
8. 复盘评分；
9. 多模型横向评估完整实现；
10. 模型接力完整实现。

模型接力和多模型横向对比可以预留字段和边界，但不在本阶段完整实现。

---

## 24. 风险提示

第 19 阶段最大的风险不是代码难度，而是语义越界。

必须防止：

1. 模型审查结果被误读为最终建议；
2. 模型在没有足够策略证据时裸判行情；
3. prompt / response 变成数据库大字段垃圾桶；
4. Hermes 消息暗示用户马上操作；
5. 后续真实策略开发反过来被模型输出污染；
6. 代码写死三五个策略，导致未来新增策略无法接入审查。

本阶段只验证：

```text
大模型如何审查策略材料、如何识别风险和证据缺口、如何动态处理 N 个策略结果、如何留痕、如何发送中文审查摘要。
```

不验证：

```text
策略是否有效。
```

---

## 25. 一句话总结

第 19 阶段不是让大模型替策略做判断，而是让大模型审查策略判断是否合格。

第 19 阶段不是三策略分析器，而是 N 策略动态审查门控层。
