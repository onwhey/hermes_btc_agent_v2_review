
git # 24C_model_review_uses_strategy_evidence.md

## 1. 阶段名称

第 24C 阶段：`model_review_uses_strategy_evidence`

中文名称：大模型策略证据审查与反驳层。

---

## 2. 阶段背景

24A 已完成：

```text
16 runner 跑完 23B/C/D/E 后，可配置自动触发 23F。
```

24B 已完成：

```text
18 material pack 已正式消费 23F strategy_evidence_aggregation_result。
```

当前链路已经形成：

```text
23B/C/D/E 策略结果
↓
23F 策略证据聚合
↓
18 material pack
```

但大模型审查层还没有被明确约束为“审查 23F 策略证据链”。如果只让大模型自由总结材料，它很容易变成：

```text
复述 23F
顺着已有结论锦上添花
输出大量无约束废话
无法提出有效反驳
```

所以 24C 的核心目标不是“让大模型看见 23F”，而是让大模型成为独立审查官。

---

## 3. 阶段目标

24C 目标：

```text
让 19/20 大模型审查正式基于 18 material pack 中的 strategy_evidence，
对 23F 策略证据链进行结构化审查、反驳、场景推演和交易纪律检查。
```

大模型定位：

```text
独立审查官
不是交易策略
不是最终交易员
不是文案润色器
不是 23F 的复读机
```

24C 输出的是：

```text
模型审查结果
给 21 advice 层的约束建议
模型输出质量标记
```

24C 不输出：

```text
最终交易建议
正式 entry / stop_loss / take_profit
trade_setup
Hermes 最终通知
```

---

## 4. 当前阶段模型形态

24C 当前只实现：

```text
单主审查模型
chain_mode = single
stage_role = primary_review
```

后续预留：

```text
chain_mode = relay
stage_role = primary_review / adversarial_review / synthesis_review
```

24C 不要求一次实现多模型接力，但数据库、配置和结果字段应尽量避免写死“永远只有一个模型”。

---

## 5. 大模型角色定义

### 5.1 当前主审查模型

当前主审查模型负责：

```text
1. 理解 23F 策略证据链
2. 判断是否同意 23F candidate_bias
3. 提出最强反方观点
4. 找出被高估 / 被低估的策略证据
5. 做主场景、反向场景、风险场景、空仓场景推演
6. 检查交易纪律
7. 指出缺失证据
8. 给 21 advice 层提出约束建议
```

### 5.2 后续模型接力预留

后续可扩展为：

```text
模型 A：primary_review 主审查
模型 B：adversarial_review 反驳 / 风控审查
模型 C：synthesis_review 综合裁决
```

后续接力输入规则：

```text
模型 B 输入 = 原始 material pack + 23F + 模型 A 输出
模型 C 输入 = 原始 material pack + 23F + 模型 A 输出 + 模型 B 输出
```

后续模型不得只看上一棒摘要，必须能回看原始证据，防止错误传递。

---

## 6. 模型选择原则

24C 不写死 DeepSeek、OpenAI、Claude 或任何具体供应商。

应基于现有 model profile / provider adapter 设计：

```text
provider enabled
profile enabled
model_key
model_name
model_version
profile_version
profile_hash
request_params
capabilities
cost_policy
```

早期建议只启用一个主审查模型，例如：

```text
deepseek_xxx_review
或
openai_xxx_review
```

但代码结构必须支持后续添加：

```text
primary_review
adversarial_review
synthesis_review
parallel_review
```

模型选择不是只看“聪明”，而是看角色匹配：

```text
结构推理能力
反驳能力
风险审查能力
稳定 JSON 输出能力
中文表达能力
成本与延迟
```

---

## 7. 输入材料要求

24C 的模型输入必须来自 18 material pack，重点包括：

```text
1. strategy_evidence
2. candidate_bias
3. decision_readiness
4. strategy_evidence_summary
5. decision_source_chain
6. role_coverage_matrix
7. evidence_missing
8. strategy_conflict_summary
9. risk_gate_summary
10. model_review_focus
11. 行情摘要
12. 现有数学材料
13. 数据时间字段
```

模型不得自由访问：

```text
外部新闻
交易所账户
用户仓位
链上数据
未提供的宏观数据
模型训练记忆中的旧行情
```

---

## 8. 时间锚定与材料时效约束

为了防止大模型使用几年前的训练记忆或旧行情，模型输入必须明确包含：

```text
analysis_time_utc
analysis_time_prc
latest_base_kline_open_time_utc
latest_base_kline_close_time_utc
latest_higher_kline_open_time_utc
latest_higher_kline_close_time_utc
data_freshness_status
```

prompt 必须明确告知模型：

```text
你只能基于本次 material pack 中提供的数据做审查。
当前分析时间是 {analysis_time_utc} / {analysis_time_prc}。
最新确认的 base interval K线是 {latest_base_kline_close_time_utc}。
最新确认的 higher interval K线是 {latest_higher_kline_close_time_utc}。
不得使用模型训练记忆中的 BTC 历史价格、旧新闻、旧市场状态或未提供的外部信息。
如果材料时间过旧、缺少最新 K线、或 data_freshness_status 异常，必须输出 stale_data 或 need_more_evidence。
```

程序层硬规则：

```text
1. material pack 缺少必要时间字段时，不调用模型，直接 blocked。
2. latest kline 距 analysis_time 超过允许阈值时，标记 stale_data。
3. 模型输出引用材料外时间、新闻、价格或外部事件时，标记 boundary_violation。
```

---

## 9. Prompt 设计要求

24C prompt 不能写成：

```text
请根据以下策略结果给出交易建议。
```

必须写成审查官任务：

```text
你是独立风险审查官。
你的任务不是生成交易建议，而是审查 23F 策略证据链是否可靠。
你必须先提出反方观点，再判断是否接受 23F。
所有判断必须引用输入证据。
证据不足时必须输出 need_more_evidence 或 wait。
不得强行给方向。
```

prompt 必须包含禁止项：

```text
不得编造未提供的价格。
不得引入材料包外的新闻、宏观、链上或账户信息。
不得假设用户已有仓位。
不得把 23F candidate_bias 当成事实。
不得为了完整性强行给方向。
不得输出正式 entry / stop_loss / take_profit。
不得使用“稳健、谨慎、关注”等空泛词代替证据。
不得只复述 23F。
```

---

## 10. 强制审查任务

模型必须完成以下审查任务。

### 10.1 反驳审查

必须回答：

```text
23F 哪些结论可能是错的？
哪些证据可能被高估？
有没有相反解释？
有没有看起来合理但实际不可交易的地方？
```

必须输出：

```text
main_objection
strongest_counterargument
```

### 10.2 场景推演

必须输出：

```text
main_scenario
opposite_scenario
risk_scenario
no_trade_scenario
```

### 10.3 交易纪律审查

必须检查：

```text
是否追单
盈亏比是否不足
止损/失效条件是否清晰
目标空间是否太小
是否处于震荡区间中部
是否存在过度交易风险
```

### 10.4 策略冲突解释

必须解释：

```text
23B / 23C / 23D / 23E / 23F 之间是否存在逻辑冲突
冲突是方向冲突、触发冲突、时间层级冲突，还是风险条件不满足
```

### 10.5 缺失证据审查

必须指出：

```text
当前还缺什么证据
缺失证据是否足以阻止进入 21 advice
```

### 10.6 给 21 的约束建议

只能输出：

```text
allow_conditional
wait
reject
need_more_evidence
downgrade
risk_reject
```

不得直接输出最终交易动作。

---

## 11. 输出结构要求

模型输出必须是结构化 JSON。

建议 schema：

```json
{
  "agreement_with_23f": "agree | partial | disagree | insufficient_evidence",
  "review_decision": "accept | require_wait | downgrade | risk_reject | need_more_evidence",
  "main_objection": "",
  "strongest_counterargument": "",
  "missing_evidence": [],
  "disputed_strategy_points": [],
  "overestimated_evidence": [],
  "underestimated_evidence": [],
  "scenario_review": {
    "main_scenario": "",
    "opposite_scenario": "",
    "risk_scenario": "",
    "no_trade_scenario": ""
  },
  "discipline_check": {
    "chasing_risk": "low | medium | high | unknown",
    "risk_reward_quality": "good | weak | bad | unknown",
    "stop_condition_clarity": "clear | unclear | unknown",
    "overtrading_risk": "low | medium | high | unknown"
  },
  "recommendation_to_advice_layer": "allow_conditional | wait | reject | need_more_evidence | downgrade | risk_reject",
  "evidence_refs": [],
  "time_freshness_assessment": {
    "uses_provided_time_only": true,
    "stale_data": false,
    "comment": ""
  },
  "boundary_flags": [],
  "quality_flags": [],
  "confidence": 0.0,
  "summary": ""
}
```

自由文本只能放在 `summary`，不得作为主结果。

---

## 12. 证据引用要求

模型每个关键判断必须绑定 `evidence_refs`。

例如：

```json
{
  "claim": "当前不适合追多",
  "evidence_refs": [
    "strategy_evidence.risk_gate_summary",
    "strategy_evidence.decision_source_chain.23D",
    "strategy_evidence.strategy_evidence_summary.23C"
  ]
}
```

如果模型输出没有有效证据引用：

```text
标记 low_quality
不得高权重进入 21 advice
```

---

## 13. 程序校验要求

不能模型说什么就信。

程序必须校验：

```text
1. 是否是合法 JSON
2. 必填字段是否存在
3. enum 是否合法
4. strongest_counterargument 是否为空
5. evidence_refs 是否为空
6. 是否越权输出正式 entry / stop_loss / take_profit
7. 是否引用材料外新闻、时间、价格或外部信息
8. 是否明显只复述 23F
9. recommendation_to_advice_layer 是否合法
10. time_freshness_assessment 是否存在
```

异常状态建议：

```text
parse_failed
schema_invalid
low_quality
boundary_violation
stale_data
model_timeout
model_error
```

低质量或越界结果：

```text
可以入库
但不得作为高权重审查结果被 21 使用
```

---

## 14. 入库要求

24C 应复用现有 19/20 模型审查表结构；如现有字段不足，可新增最小必要字段或 JSON 字段，不做大范围重构。

至少需要记录：

```text
model_review_id
model_review_chain_id
chain_mode
stage_role
stage_order
parent_review_id
material_pack_id
strategy_evidence_aggregation_id
strategy_signal_run_id
model_key
provider
model_name
model_version
profile_version
profile_hash
agreement_with_23f
review_decision
recommendation_to_advice_layer
main_objection
strongest_counterargument
missing_evidence_json
disputed_strategy_points_json
scenario_review_json
discipline_check_json
evidence_refs_json
time_freshness_assessment_json
boundary_flags_json
quality_flags_json
raw_response_hash
raw_response_storage_ref
status
error_code
error_message
trace_id
created_at_utc
```

如果现有表已经有类似字段，可以优先落在统一 JSON 结果字段中，不强制拆出所有列。

---

## 15. 24C 与 20 的关系

24C 可落在现有 19/20 模型审查链路中。

当前阶段允许：

```text
单模型审查结果
chain_mode = single
stage_role = primary_review
```

20 后续可继续负责：

```text
多模型聚合
横向对比
接力综合
模型审查复用
```

24C 不要求完整实现多模型接力，但必须避免把结构写死成永远单模型。

---

## 16. 24C 与 21 的关系

24C 不生成最终 advice。

24C 只给 21 提供：

```text
model review decision
risk objections
missing evidence
scenario review
discipline check
recommendation_to_advice_layer
quality flags
boundary flags
```

21 后续使用规则建议：

```text
模型 agree：可增强 23F 置信度
模型 partial：保守推进
模型 require_wait：最终建议倾向 wait
模型 risk_reject：最终建议必须降级或阻断
模型 low_quality：记录但低权重采用
模型 boundary_violation：拒绝采用
```

---

## 17. 失败处理

模型调用失败时：

```text
1. 不回滚 material pack
2. 不回滚 23F aggregation
3. 记录 run 状态和 error_code
4. 不生成最终 advice
5. 是否 Hermes 告警沿用现有模型调用失败规则
```

如果真实模型总开关关闭：

```text
应明确标记 model_call_skipped
不得伪装成已审查
```

如果输出过长：

```text
不得静默截断
必须记录长度、hash、error_code 或隔离存储引用
```

---

## 18. 配置要求

必须使用现有模型开关体系：

```text
provider enabled
profile enabled
真实模型总闸
CLI 成本确认
model profile
```

不得把模型名称写死在业务代码中。

24C 需要支持：

```text
--model-key
--use-real-model
--confirm-real-model-cost
--dry-run
--confirm-write
```

具体 CLI 可复用现有 19/20 脚本，不要求新增脚本，除非现有入口无法满足 24C。

---

## 19. 本阶段不做

24C 不做：

```text
不做 24D
不生成最终 advice
不生成 trade_setup
不发送最终 Hermes 策略通知
不开发 25 数学/弱模型证据层
不新增交易策略
不改 23B/C/D/E/F 核心算法
不读取账户或持仓
不自动交易
不引入材料包外数据
不实现完整多模型接力
```

---

## 20. 测试要求

至少覆盖：

```text
1. 24C 模型输入包含 strategy_evidence。
2. 24C 模型输入包含 analysis_time_utc / analysis_time_prc / latest kline time。
3. material pack 缺少必要时间字段时，不调用模型并 blocked。
4. prompt 明确要求反驳 23F。
5. 模型输出合法 JSON 时，能正确解析并入库。
6. 模型输出缺 strongest_counterargument 时，标记 low_quality。
7. 模型输出缺 evidence_refs 时，标记 low_quality。
8. 模型输出正式 entry / stop_loss / take_profit 时，标记 boundary_violation。
9. 模型输出引用材料外新闻/价格/时间时，标记 boundary_violation。
10. model_call_skipped 不得伪装成已审查。
11. chain_mode=single / stage_role=primary_review 正确保存。
12. 预留 parent_review_id / model_review_chain_id 不破坏现有逻辑。
13. 不调用大模型时 dry-run 不写库。
14. confirm-write 才写库。
15. 不生成 advice。
16. 不发送最终 Hermes 策略通知。
```

建议运行：

```bash
python -m pytest tests/model_review -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/strategy -q
```

如仓库测试目录名称不同，以实际目录为准。

---

## 21. 验收标准

24C 验收通过条件：

```text
1. 大模型输入正式包含 23F strategy_evidence。
2. 输入包含明确时间锚点。
3. prompt 明确模型是独立审查官，不是交易建议生成器。
4. 模型输出必须结构化。
5. 程序能校验模型输出质量和越界。
6. 低质量 / 越界结果不会被高权重采用。
7. 结果入库可追溯 model_key / profile / material_pack / strategy_evidence_aggregation。
8. 当前支持单模型 primary_review。
9. 后续可扩展 relay / parallel，不写死单模型。
10. 不生成最终 advice，不发送最终策略通知。
```

---

## 22. 后续阶段

24C 完成后，再做：

```text
24D：21 advice / Hermes 通知展示策略证据链与模型审查结论
25：市场数学与弱模型证据层
后续：多模型接力 / 横向对比 / 模型审查复盘
```
