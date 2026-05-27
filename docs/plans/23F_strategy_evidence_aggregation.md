# 23F_strategy_evidence_aggregation.md

## 1. 阶段名称

第 23F 阶段：`strategy_evidence_aggregation`

中文名称：策略证据聚合层 / 策略组长层。

---

## 2. 阶段定位

23F 是 23 阶段的收口层。

它不是普通策略，不是最终 advice，不是大模型审查，也不是 Hermes 通知层。

23F 的身份是：

```text
策略组长
```

它负责读取本轮所有已运行策略的公开结果，判断这些策略之间的关系，形成一条可解释、可复盘、可交给 18 / 19 / 20 / 21 使用的策略证据链。

一句话：

```text
23B/C/D/E 和后续新增策略负责各自表达观点；
23F 负责判断这些观点之间如何组合、谁参与决策、谁只观察、谁支持、谁反对、谁要求等待、谁触发否决。
```

---

## 3. 阶段目标

23F 要回答：

```text
1. 本轮有哪些策略运行了？
2. 哪些策略成功、失败、跳过、关闭、缺数据？
3. 哪些策略正式参与决策？
4. 哪些策略只是观察期，只记录和通知，不参与最终判断？
5. 每个策略对候选方向的作用是什么？
6. 哪些策略支持 long / short / wait？
7. 哪些策略反对当前候选？
8. 哪些策略触发风控降级或否决？
9. 当前证据是否完整？
10. 当前证据之间是否冲突？
11. 当前策略域候选结论是什么？
12. 后续 18 / 大模型 / advice 应重点审查什么？
```

23F 输出的是策略证据收口结果，不是最终交易建议。

---

## 4. 与现有公共层的区别

现有策略公共层负责：

```text
加载策略
运行策略
传递 EvidenceContext
校验 StrategyResult
落库 strategy_signal_result
保证单个策略失败不影响其他策略
```

23F 不重复这些职责。

23F 负责：

```text
读取本轮所有策略结果
按角色和能力分组
判断策略参与权限
解释策略结果之间的关系
生成证据链和候选结论
```

边界必须写死：

```text
公共层 = 执行、读取、传递、校验、落库
23F = 语义聚合、冲突解释、证据链生成、候选结论收口
```

23F 不得重新运行 23B / 23C / 23D / 23E，也不得重新加载策略进行二次计算。

---

## 5. 与 18 的关系

23F 和 18 必须分层。

```text
23F = 策略域聚合
18 = 模型材料聚合
```

推荐链路：

```text
全部策略结果
↓
23F 生成 strategy_evidence_summary / decision_source_chain
↓
18 将 23F 输出作为模型材料的一部分
↓
19 / 20 大模型审查
↓
21 advice 生成最终人工建议
```

23F 不替代 18。

18 也不应该重新理解所有策略关系。

23F 只负责策略域证据收口；18 负责把策略证据、市场材料、数学材料、模型输入材料整理成大模型可审查的 material pack。

---

## 6. 23F 不是最终 advice

23F 可以输出：

```text
当前策略证据偏 long
当前策略证据偏 short
当前策略证据要求 wait
当前证据冲突
当前风控阻断当前候选
当前缺少触发确认
```

23F 不得输出：

```text
正式开多
正式开空
正式入场价
正式止损价
正式止盈价
正式 trade_setup
建议链生命周期动作
Hermes 最终通知
```

这些属于 21 advice / 后续通知层。

---

## 7. 输入范围

23F 必须读取本轮全部策略结果，而不是固定读取 23B / 23C / 23D / 23E。

正确方式：

```text
按 strategy_signal_run_id 读取本轮所有 StrategyResult
↓
只读取每条结果的 common_result / common_payload_json
↓
读取策略配置中的 governance metadata
↓
按 strategy_role / provides / participation_mode / maturity_stage 分组
```

23F 允许读取：

```text
strategy_signal_run
strategy_signal_result 基础元数据
strategy_name
strategy_version
strategy_role
provides
common_payload_json
validation_status
validation_errors_json
策略配置中的 governance metadata
```

23F 禁止读取：

```text
任何 strategy_payload_json
任何策略内部函数
任何策略私有算法
任何交易所接口
任何账户或持仓
大模型
Hermes
```

---

## 8. 策略治理字段

23F 必须支持策略治理字段。

这些字段建议放在各策略自身 YAML 配置中。

重点规则：

```text
enabled = true 只代表策略会运行和落库；
是否参与最终策略聚合，由 participation_mode / maturity_stage / decision_weight / can_veto 决定。
```

### 8.1 字段定义

建议每个策略配置增加：

```yaml
maturity_stage: experimental
participation_mode: observe_only
decision_weight: "0"
can_veto: false
veto_scope: none
notification_required: true
```

字段含义：

```text
maturity_stage：策略成熟度。
participation_mode：参与决策的程度。
decision_weight：决策权重。
can_veto：是否具备否决权。
veto_scope：否决范围。
notification_required：是否进入最终策略摘要通知。
```

### 8.2 maturity_stage

建议枚举：

```text
experimental
active
deprecated
disabled
```

### 8.3 participation_mode

采用四档，权限递增：

```text
observe_only
< evidence_only
< advisory
< decision_participant
```

含义：

```text
observe_only：只运行、落库、可展示，不参与结论。
evidence_only：可进入证据展示，但不改变候选方向。
advisory：可影响解释、置信度、提醒优先级，但权重较低。
decision_participant：正式参与聚合判断，可影响候选方向。
```

注意：

```text
participation_mode 不包含否决权。
否决权由 can_veto / veto_scope 单独控制。
```

### 8.4 can_veto / veto_scope

示例：

```yaml
can_veto: true
veto_scope: current_candidate
```

`veto_scope` 建议枚举：

```text
none
long_candidate
short_candidate
current_candidate
all_candidates
```

说明：

```text
can_veto = true 表示该策略在满足条件时可以产生否决效果。
veto_scope 表示否决作用范围。
具备否决权的策略通常也应该是 decision_participant，但字段上仍保持独立，避免语义混乱。
```

---

## 9. 默认治理配置建议

23B / 23C / 23D / 23E 当前可作为正式参与策略。

### 9.1 23B 市场状态策略

```yaml
maturity_stage: active
participation_mode: decision_participant
decision_weight: "1.0"
can_veto: false
veto_scope: none
notification_required: true
```

### 9.2 23C 支撑压力策略

```yaml
maturity_stage: active
participation_mode: decision_participant
decision_weight: "1.0"
can_veto: false
veto_scope: none
notification_required: true
```

### 9.3 23D 突破回踩确认策略

```yaml
maturity_stage: active
participation_mode: decision_participant
decision_weight: "1.0"
can_veto: false
veto_scope: none
notification_required: true
```

### 9.4 23E 风控闸门策略

```yaml
maturity_stage: active
participation_mode: decision_participant
decision_weight: "1.0"
can_veto: true
veto_scope: current_candidate
notification_required: true
```

### 9.5 未来观察期策略示例：江恩

```yaml
maturity_stage: experimental
participation_mode: observe_only
decision_weight: "0"
can_veto: false
veto_scope: none
notification_required: true
```

---

## 10. 聚合维度

23F 不按固定策略名聚合，而按以下维度聚合：

```text
strategy_role
provides
maturity_stage
participation_mode
decision_weight
can_veto
veto_scope
validation_status
common_result
```

这样后续新增策略时，不需要修改 23F 主逻辑。

23F 应支持未来新增：

```text
江恩策略
完整海龟策略
威科夫策略
斐波那契策略
流动性清理策略
成交量分布策略
模型辅助评分策略
```

新增策略只要遵守 StrategyResult 协议并声明治理字段，就能被 23F 读取和展示。

---

## 11. 策略作用分类

23F 需要为每个策略判断其对当前候选的作用。

建议枚举：

```text
support_long
support_short
support_wait
oppose_long
oppose_short
neutral
uncertain
block_long
block_short
block_current_candidate
block_all
observe_only
not_applicable
failed
disabled
missing
```

说明：

```text
support_long：支持多头候选。
support_short：支持空头候选。
support_wait：支持等待。
oppose_long：反对多头候选。
oppose_short：反对空头候选。
neutral：中性。
uncertain：证据不足。
block_*：产生风控阻断。
observe_only：观察期策略，不参与结论。
not_applicable：本轮不适用。
failed：策略失败。
disabled：策略关闭。
missing：预期存在但本轮缺失。
```

---

## 12. 候选方向聚合

23F 可以生成策略域候选方向，但不能生成最终 advice。

建议输出：

```text
candidate_bias:
  long
  short
  wait
  neutral
  conflict
  blocked
  insufficient_evidence
```

说明：

```text
long：策略域证据偏多。
short：策略域证据偏空。
wait：策略域证据要求等待。
neutral：中性。
conflict：多空证据冲突。
blocked：被风控或关键策略阻断。
insufficient_evidence：证据不足。
```

注意：

```text
candidate_bias 不是最终交易建议。
后续仍需 18 / 19 / 20 / 21 处理。
```

---

## 13. 证据完整性检查

23F 需要输出 role_coverage_matrix。

至少覆盖：

```text
context
support_resistance
filter
risk_control
```

后续新增角色也应动态加入。

缺失时必须说明：

```text
缺哪个角色
缺哪个 provides
是否影响候选结论
是否降级为 insufficient_evidence / wait
```

---

## 14. 冲突识别

23F 需要识别策略冲突。

至少包括：

```text
direction_conflict
trigger_vs_risk_conflict
context_vs_trigger_conflict
support_resistance_missing
risk_veto_conflict
observe_only_disagreement
```

示例：

```text
23B 偏多，但 23D 未确认触发 → wait
23D 突破确认，但 23E 追单风险高 → wait / blocked
23C 关键位缺失，但 23D 有触发 → insufficient_evidence
观察期江恩看空，但正式策略偏多 → observe_only_disagreement，不影响候选方向
```

---

## 15. 输出对象

建议新增策略域聚合对象：

```text
StrategyEvidenceAggregation
```

建议字段：

```text
aggregation_id
strategy_signal_run_id
symbol
base_interval
higher_interval
status
candidate_bias
candidate_confidence
decision_readiness
strategy_evidence_summary
decision_source_chain
role_coverage_matrix
evidence_missing
strategy_conflict_summary
participation_summary
observe_only_summary
risk_gate_summary
model_review_focus
not_trading_advice
created_at_utc
```

### 15.1 status

```text
success
partial_success
insufficient_evidence
failed
```

### 15.2 decision_readiness

```text
ready_for_model_review
needs_more_evidence
wait_for_confirmation
blocked_by_risk
conflict_requires_review
not_ready
```

说明：

```text
decision_readiness 表示策略域是否适合进入后续模型审查 / advice 流程。
不是交易许可。
```

---

## 16. 落库要求

23F 的输出必须可追踪、可复盘。

优先方案：

```text
新增轻量表 strategy_evidence_aggregation_result
```

原因：

```text
23F 不是普通策略，不应伪装成 strategy_signal_result。
23F 也不是 18 model material pack，不应塞进 18 表里。
23F 是策略域证据收口结果，应独立保存。
```

建议字段：

```text
id
aggregation_id
strategy_signal_run_id
symbol
base_interval
higher_interval
status
candidate_bias
candidate_confidence
decision_readiness
strategy_evidence_summary_json
decision_source_chain_json
role_coverage_matrix_json
evidence_missing_json
strategy_conflict_summary_json
participation_summary_json
observe_only_summary_json
risk_gate_summary_json
model_review_focus_json
not_trading_advice
trace_id
created_at_utc
updated_at_utc
```

约束建议：

```text
strategy_signal_run_id 唯一
aggregation_id 唯一
```

幂等规则：

```text
同一 strategy_signal_run_id 重复执行 23F，不得重复插入多条有效聚合结果。
可选择 update 原记录，或 blocked 并返回已有 aggregation_id。
```

如果仓库已有可复用的策略域聚合表或 artifact 表，Codex 可以复用，但必须在报告中说明原因。

---

## 17. 与 18 的最小衔接

23F 完成后，18 不需要大改。

最小衔接原则：

```text
18 生成模型材料时，如果存在 23F aggregation，则优先读取 23F 的 strategy_evidence_summary / decision_source_chain / model_review_focus。
如果不存在 23F aggregation，则保持原有逻辑，不崩溃。
```

本阶段不做 18 深度重构。

---

## 18. Hermes 通知关系

23F 本阶段不直接发送 Hermes。

但 23F 必须为后续通知准备证据链。

后续 Hermes 最终通知必须满足：

```text
1. 不得只发送最终操作结论。
2. 必须展示每个正式参与策略的简短结论。
3. 必须展示观察期策略的摘要，但明确说明“不参与最终决策”。
4. 必须说明哪个策略导致 wait / block / 降级。
5. 必须说明哪些策略缺数据、关闭、失败或不适用。
```

23F 负责产出这些通知所需的结构化材料，但不负责发送。

---

## 19. 模块位置建议

23F 不放在：

```text
app/strategy/strategies/
```

建议放在：

```text
app/strategy/aggregation/
```

推荐新增：

```text
app/strategy/aggregation/evidence_aggregator.py
app/strategy/aggregation/evidence_models.py
app/strategy/aggregation/evidence_repository.py
app/strategy/aggregation/evidence_service.py
configs/strategy_aggregation/evidence_aggregation.yaml
scripts/run_strategy_evidence_aggregation.py
```

说明：

```text
scripts 只做 CLI 参数解析和调用 service。
核心逻辑必须在 app/strategy/aggregation/。
```

---

## 20. CLI 要求

建议新增 CLI：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --dry-run
```

写库模式：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

要求：

```text
dry-run 不写库，只打印聚合摘要。
confirm-write 写入 strategy_evidence_aggregation_result。
```

后续 scheduler 或 16 runner 可自动触发，但本阶段先以 CLI 和 service 能力为主。

---

## 21. 与 16 runner 的关系

23F 可以有两种接入方式。

### 21.1 第一阶段接入方式

```text
16 runner 跑完所有策略并落库
↓
手动或脚本调用 23F CLI
↓
23F 按 strategy_signal_run_id 聚合
```

### 21.2 后续自动接入方式

```text
16 runner 跑完所有策略
↓
自动调用 23F service
↓
生成 aggregation
↓
18 使用 aggregation
```

本阶段建议优先实现第一阶段方式，避免一次性修改 runner 过多。

如果 Codex 认为可以低风险接入 runner，可实现为可配置开关，默认关闭。

---

## 22. 配置要求

新增配置：

```text
configs/strategy_aggregation/evidence_aggregation.yaml
```

建议内容：

```yaml
enabled: true
required_roles:
  - context
  - support_resistance
  - filter
  - risk_control
minimum_decision_participants: 3
default_missing_role_decision: insufficient_evidence
default_unknown_strategy_mode: evidence_only
include_observe_only_in_summary: true
candidate_confidence:
  high_threshold: "0.75"
  medium_threshold: "0.55"
  low_threshold: "0.35"
decision_readiness_rules:
  risk_blocked: blocked_by_risk
  missing_required_role: needs_more_evidence
  conflict: conflict_requires_review
  wait_bias: wait_for_confirmation
```

策略自身配置需要补治理字段：

```yaml
maturity_stage: active
participation_mode: decision_participant
decision_weight: "1.0"
can_veto: false
veto_scope: none
notification_required: true
```

---

## 23. 本阶段不做

23F 明确不做：

```text
不新增具体交易策略
不改 23B / 23C / 23D / 23E 核心算法
不生成最终 advice
不生成 trade_setup
不输出正式 entry
不输出正式 stop_loss
不输出正式 take_profit
不调用大模型
不发送 Hermes
不请求 Binance
不读取账户
不读取持仓
不自动交易
不做人工执行反馈
不做完整复盘统计
不做策略自我进化
不做 18 深度重构
```

---

## 24. 测试要求

至少新增或更新测试：

```text
1. 23F 能读取同一 strategy_signal_run_id 下全部策略结果。
2. 23F 不固定读取 23B / 23C / 23D / 23E 名称。
3. 23F 按 strategy_role 分组。
4. 23F 按 provides 识别能力。
5. 23F 能读取策略配置中的 maturity_stage / participation_mode / decision_weight / can_veto / veto_scope。
6. enabled=true 但 participation_mode=observe_only 时，不参与 candidate_bias。
7. observe_only 策略进入 observe_only_summary。
8. evidence_only 策略进入证据展示，但不改变候选方向。
9. advisory 策略可影响 confidence 或解释，但不得越权否决。
10. decision_participant 策略参与候选方向判断。
11. can_veto=true 且满足条件时，能输出 block_current_candidate / block_all 等。
12. can_veto=false 时，即使 common_result 有风险提示，也不得产生正式否决。
13. 23E risk_control 的 block_current_candidate 能被 23F 正确解释为风控阻断。
14. 缺 context 角色时，decision_readiness = needs_more_evidence 或 insufficient_evidence。
15. 缺 risk_control 角色时，必须标记 evidence_missing。
16. 23B 偏多、23C 有支撑、23D 未确认、23E wait 时，candidate_bias = wait。
17. 23D 突破确认但 23E 追单风险高时，decision_readiness = blocked_by_risk 或 wait_for_confirmation。
18. 正式策略偏 long，观察期策略偏 short 时，candidate_bias 不被观察期策略改变，但 observe_only_summary 记录分歧。
19. 23F 不读取任何 strategy_payload_json。
20. 23F 输出 strategy_evidence_summary / decision_source_chain / role_coverage_matrix。
21. dry-run 不写库。
22. confirm-write 写库。
23. 同一 strategy_signal_run_id 重复 confirm-write 幂等。
24. 18 在存在 23F aggregation 时可读取摘要，不存在时不崩溃。
```

建议运行：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```

如果新增 repository / migration，补对应测试。

---

## 25. 验收标准

23F 完成后，应能回答：

```text
1. 本轮所有策略分别说了什么？
2. 哪些策略正式参与判断？
3. 哪些策略只是观察期？
4. 每个策略对候选方向的作用是什么？
5. 哪些策略导致等待、阻断或降级？
6. 当前证据是否完整？
7. 当前证据是否冲突？
8. 当前策略域候选方向是什么？
9. 当前是否适合进入模型审查？
10. 后续 Hermes / advice 应展示哪些证据链？
```

验收通过条件：

```text
1. 23F 不作为普通策略实现。
2. 23F 位于 app/strategy/aggregation/ 或等价策略聚合目录。
3. 23F 读取本轮全部策略结果，不固定读取 23B-E。
4. 23F 只读取 common_result，不读取 strategy_payload_json。
5. 策略治理字段已支持。
6. observe_only / evidence_only / advisory / decision_participant 权限清晰。
7. can_veto / veto_scope 与 participation_mode 分离。
8. role_coverage_matrix 正常输出。
9. strategy_evidence_summary 正常输出。
10. decision_source_chain 正常输出。
11. evidence_missing 正常输出。
12. strategy_conflict_summary 正常输出。
13. observe_only_summary 正常输出。
14. risk_gate_summary 正常输出。
15. 23F 输出 not_trading_advice = true。
16. 不生成 advice。
17. 不生成 trade_setup。
18. 不发送 Hermes。
19. 不调用大模型。
20. 不请求 Binance。
21. 不读取账户或持仓。
22. dry-run / confirm-write 行为正确。
23. 重复执行幂等。
24. tests/strategy 通过。
25. tests/strategy_aggregation 通过。
```

---

## 26. 后续阶段建议

23F 完成后，23 阶段可视为策略接力主链路闭环。

后续重点应转向：

```text
1. 18 消费 23F 策略证据聚合结果，形成更好的模型材料包。
2. 19 / 20 大模型审查策略证据链。
3. 21 advice 结合策略证据、大模型审查、建议生命周期生成最终人工建议。
4. Hermes 通知展示完整证据链，而不是只展示最终结论。
5. 后续复盘阶段统计每个策略的贡献、误导、否决正确率、观察期策略表现。
```

最终目标：

```text
让系统不仅能给结论，还能解释结论从哪些策略证据推导而来，并能在后期复盘中定位到底是哪一环出了问题。
```
