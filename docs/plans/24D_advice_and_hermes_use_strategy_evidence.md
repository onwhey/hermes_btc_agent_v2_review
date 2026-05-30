# 24D_advice_and_hermes_use_strategy_evidence.md

## 1. 阶段名称

第 24D 阶段：`advice_and_hermes_use_strategy_evidence`

中文名称：最终建议与 Hermes 通知展示策略证据链。

---

## 2. 阶段背景

24A 已完成：

```text
16 跑完 23B/C/D/E 后，可配置自动触发 23F 策略证据聚合。
```

24B 已完成：

```text
18 material pack 已消费 23F，并生成 material_schema_v2。
```

24C 已完成：

```text
19/20 大模型审查已基于 18 material pack 中的 strategy_evidence，
对 23F 策略证据链进行结构化审查、反驳、场景推演和交易纪律检查。
```

当前链路已经具备：

```text
23B/C/D/E 策略结果
↓
23F 策略证据聚合
↓
18 material pack
↓
24C 大模型审查
```

但 21 advice / Hermes 最终通知还没有明确消费并展示这些证据链。用户最终收到的通知不能只给一个结论，例如：

```text
等待
```

必须能看清楚：

```text
为什么等待
哪些策略支持
哪些策略反对
风控为什么保守
大模型是否同意 23F
大模型提出了什么反驳
本轮结论与上一条 active advice 的关系
```

---

## 3. 阶段目标

24D 目标：

```text
让 21 advice 和 Hermes 最终通知正式消费 23F 策略证据链与 24C 大模型审查结果，
并以简短中文形式展示最终建议背后的关键证据。
```

24D 只做展示与消费，不做重新分析。

24D 应做到：

```text
1. 21 能读取 23F strategy_evidence_aggregation_result。
2. 21 能读取 24C model_analysis_result。
3. 21 生成的 advice / lifecycle_review / notification_payload 能包含证据链摘要。
4. Hermes 通知能用中文短句展示关键策略证据和模型审查结论。
5. 缺 23F、缺 24C、模型低质量、模型越界时，能明确降级并告诉用户。
6. 不重新调用大模型。
7. 不重新跑 23F。
8. 不改变自动交易边界。
```

---

## 4. 核心定位

24D 的角色：

```text
证据链展示层
```

不是：

```text
新策略层
新模型层
新聚合层
新复盘层
```

职责划分：

```text
23F：策略组长，聚合各策略证据。
24C：大模型审查官，审查并反驳 23F。
21：最终 advice 层，结合生命周期生成最终人工建议。
24D：让 21 和 Hermes 清楚展示 23F + 24C 的关键证据链。
```

---

## 5. 21 应消费的数据

### 5.1 23F 策略证据聚合结果

21 应读取与当前 MRAG / material pack / strategy_signal_run_id 相关的最新有效 23F 结果。

重点字段：

```text
aggregation_id
strategy_signal_run_id
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
trace_id
```

21 不应重新计算：

```text
candidate_bias
candidate_confidence
decision_readiness
role_coverage_matrix
strategy_conflict_summary
risk_gate_summary
```

这些属于 23F 职责。

### 5.2 24C 大模型审查结果

21 应读取与当前 material_pack_id / strategy_evidence_aggregation_id 相关的最新有效模型审查结果。

重点字段或 JSON 内容：

```text
model_analysis_run_id
model_analysis_result_id
material_pack_id
strategy_evidence_aggregation_id
strategy_signal_run_id
provider
model_key
model_name
model_version
profile_hash
model_role
analysis_mode
review_decision
evidence_quality
risk_acceptability
strategy_conflict_level
human_review_required
agreement_with_23f
main_objection
strongest_counterargument
missing_evidence
disputed_strategy_points
scenario_review
discipline_check
recommendation_to_advice_layer
evidence_refs
quality_flags
boundary_flags
schema_error_code
status
error_code
```

21 不应直接采用低质量或越界模型结果。

---

## 6. 24C 结果采用规则

### 6.1 可采用结果

模型结果满足以下条件时，可作为正常审查意见进入 21：

```text
status = success
schema_error_code 为空
boundary_flags 为空或不包含严重越界
quality_flags 不包含 low_quality / summary_only / missing_evidence_refs 等严重质量问题
is_final_trading_advice = false
is_trading_signal = false
is_executable = false
auto_trading_allowed = false
```

### 6.2 低权重采用

以下情况可展示，但不得高权重影响最终建议：

```text
quality_flags 包含 low_quality
缺少 strongest_counterargument
缺少 evidence_refs
模型输出过于复述 23F
模型 confidence 过低
```

通知中应明确：

```text
大模型审查质量不足，仅作低权重参考。
```

### 6.3 拒绝采用

以下情况不得采用，只能记录和提示：

```text
boundary_violation
模型输出正式 entry / stop_loss / take_profit
模型引用材料外新闻、价格、账户或旧行情
schema_invalid
parse_failed
model_call_skipped
real_model_disabled
stale_data
input_char_limit_exceeded
```

通知中应明确：

```text
本轮模型审查未被采用，原因：xxx。
```

### 6.4 多个模型审查结果的选择优先级

当同一 material_pack_id / strategy_evidence_aggregation_id 下存在多个 24C 模型审查结果时，21 应按以下顺序选择可采用结果：

1. 只考虑 status=success 且 schema_error_code 为空的结果。
2. 排除 boundary_violation / parse_failed / schema_invalid。
3. 真实模型结果优先于 mock_review。
4. 同一 model_key 多次成功时，取 created_at_utc 最新的一条。
5. 如果多个真实模型同时可用，优先采用配置中 priority 更高的模型；若暂无 priority 配置，则取最新成功结果，并在 payload 中记录 model_key。
6. low_quality 结果只能低权重展示，不得作为强依据。
7. mock_review 只能用于测试或 dry-run，不应在真实 advice / Hermes 正式通知中被描述为真实大模型审查。


---

## 7. advice 中新增或扩展的证据链摘要

优先复用现有 21 表结构和 JSON 字段，不强制新增大量列。

建议在 `strategy_advice_lifecycle_review` 或对应 notification payload 中增加结构化摘要：

```json
{
  "strategy_evidence_chain": {
    "source": "strategy_evidence_aggregation_result",
    "aggregation_id": "SEA-xxx",
    "strategy_signal_run_id": "SSR-xxx",
    "candidate_bias": "wait",
    "candidate_confidence": 0.62,
    "decision_readiness": "wait_for_confirmation",
    "key_strategy_points": [],
    "strategy_conflicts": [],
    "risk_gate_summary": {},
    "evidence_missing": []
  },
  "model_review_summary": {
    "source": "model_analysis_result",
    "model_analysis_run_id": "MAR-xxx",
    "model_analysis_result_id": "MARES-xxx",
    "model_key": "deepseek_v4_pro_review",
    "review_decision": "require_more_evidence",
    "evidence_quality": "weak",
    "risk_acceptability": "unknown",
    "agreement_with_23f": "partial",
    "main_objection": "",
    "strongest_counterargument": "",
    "recommendation_to_advice_layer": "need_more_evidence",
    "human_review_required": true,
    "quality_flags": [],
    "boundary_flags": []
  }
}
```

字段命名可按现有 21 payload 结构做最小适配，但语义必须保留。

---

## 8. Hermes 通知展示要求

24D 通知必须中文化、短句化、可读。

通知不能把 24C 几千字原文直接发给用户。

### 8.1 通知长度控制

建议：

```text
Hermes 主消息控制在 800-1500 中文字以内。
更长的模型审查原文只留数据库或 artifact 引用。
```

### 8.2 通知最小内容

通知必须包含：

```text
1. 本轮 advice 生命周期关系：new / continue / adjust / close / invalidate / complete / no_change。
2. 23F 策略组长候选结论。
3. 24C 大模型审查结论。
4. 关键策略证据摘要。
5. 关键反驳或风险点。
6. 最终 advice 动作。
7. 是否调用了大模型。
8. 调用了哪个模型 / 是否复用 / 是否跳过。
9. 不自动交易声明。
```

### 8.3 推荐通知模板

示例：

```text
【BTC 策略建议：等待确认】

生命周期：continue，本轮结论延续上一条 active advice。
策略组长结论：wait，置信度 0.62，等待突破/跌破确认。
大模型审查：require_more_evidence，证据质量 weak，建议人工复核。

关键证据：
- 23B：大方向未形成强趋势，偏震荡/等待。
- 23C：关键支撑压力存在，但当前价格不在高质量入场位置。
- 23D：突破/跌破触发条件未确认。
- 23E：风控不支持追单。

模型反驳：
- 当前证据不足以支持直接开仓。
- 需要更多成交量或突破确认。

最终动作：不建议直接开仓，等待下一根确认 K线或关键位触发。
边界：本通知不是交易指令，系统不会自动下单。
```

实际内容以 21 当前 advice_action / directional_bias / trade_permission 为准。

---

## 9. 缺失数据降级规则

### 9.1 缺 23F

如果没有可用 23F：

```text
21 不得伪装成策略证据完整。
Hermes 必须说明：本轮缺少 23F 策略证据聚合，最终建议仅基于旧链路/有限材料生成。
```

建议：

```text
advice 降级为 wait 或 need_more_evidence。
```

### 9.2 缺 24C

如果没有可用 24C：

```text
21 不得伪装成大模型已审查。
Hermes 必须说明：本轮未使用最新大模型审查。
```

是否允许继续生成 advice，按现有 21 规则处理；但通知必须透明。

### 9.3 24C 被拒绝采用

如果 24C 存在但不可采用：

```text
Hermes 必须说明模型审查未被采用及原因。
```

例如：

```text
大模型审查未被采用：输出越界，包含正式交易动作。
```

### 9.4 23F 与 24C 冲突

如果 23F 与 24C 冲突：

```text
21 必须展示冲突。
```

例如：

```text
23F 倾向 wait，但大模型要求 require_more_evidence，说明策略证据仍不足，最终建议保持等待。
```

若模型为低质量结果，不得因模型冲突强行推翻 23F。

---

## 10. 生命周期关系要求

21 原有生命周期关系必须保留：

```text
new
continue
adjust
close
invalidate
complete
no_change
```

24D 不改变生命周期算法，只增加证据链说明。

通知必须说明：

```text
本轮建议是新开建议链，还是上一条建议的延续、调整、关闭、失效、完成或无实质变化。
```

即使最终结论未变，也要说明：

```text
系统已运行，本轮为 continue / no_change。
```

---

## 11. 幂等与重复通知

24D 必须保持 21B / 21C 既有幂等规则：

```text
同一 review_id 不重复创建成功通知。
重复运行不刷屏。
补发只补 21B 通知链路，不重跑 21A。
```

如果只是证据链摘要新增导致 payload 结构变化，不得破坏已有 notification 幂等。

---

## 12. 配置要求

可复用现有 21/24 开关。

如需新增开关，建议仅新增轻量显示开关：

```text
STRATEGY_ADVICE_INCLUDE_EVIDENCE_CHAIN=true
STRATEGY_ADVICE_INCLUDE_MODEL_REVIEW_SUMMARY=true
```

默认建议开启。

不应新增会改变策略判断的大开关。

---

## 13. 本阶段不做

24D 不做：

```text
不重新跑 23F
不重新生成 18 material pack
不调用大模型
不修改 24C prompt
不做弱模型 / 因子层
不新增策略
不修改 23B/C/D/E/F 算法
不生成真实订单
不读取账户或持仓
不自动交易
不实现后台查询页面
不做完整复盘
```

---

## 14. 测试要求

至少覆盖：

```text
1. 21 可读取 23F strategy_evidence_aggregation_result。
2. 21 可读取 24C model_analysis_result。
3. advice / lifecycle_review / notification_payload 包含 strategy_evidence_chain。
4. notification_payload 包含 model_review_summary。
5. Hermes 通知中展示 23F candidate_bias / decision_readiness。
6. Hermes 通知中展示 24C review_decision / evidence_quality。
7. 模型 low_quality 时，只低权重展示，不高权重采用。
8. 模型 boundary_violation 时，不采用并说明原因。
9. 缺 24C 时，不伪装成已模型审查。
10. 缺 23F 时，不伪装成策略证据完整。
11. 23F 与 24C 冲突时，通知展示冲突。
12. 通知长度受控，不直接发送 24C 原文。
13. 不重新调用大模型。
14. 不重新跑 23F。
15. 不生成 trade_setup 以外的新交易执行结构。
16. 不改变 is_trading_signal=false / is_executable=false / auto_trading_allowed=false。
17. 21B / 21C 通知幂等不被破坏。
```

建议运行：

```bash
python -m pytest tests/strategy_advice -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy -q
```

如测试目录不同，以仓库实际目录为准。

---

## 15. 服务器验证建议

### 15.1 准备数据

确保已经有：

```text
23F strategy_evidence_aggregation_result.status = success
18 material_schema_v2
24C model_analysis_result.status = success
```

### 15.2 跑 21 advice 生成

使用现有 21A / 21B / 21C 入口。

预期：

```text
1. strategy_advice_lifecycle_review 写入成功。
2. notification_payload_json 包含 strategy_evidence_chain。
3. notification_payload_json 包含 model_review_summary。
4. Hermes 通知包含简短证据链。
5. 不重新调用大模型。
6. 不自动交易。
```

### 15.3 查询验证

建议查询：

```sql
SELECT
  review_id,
  advice_id,
  notification_required,
  notification_level,
  JSON_EXTRACT(notification_payload_json, '$.strategy_evidence_chain') AS strategy_evidence_chain,
  JSON_EXTRACT(notification_payload_json, '$.model_review_summary') AS model_review_summary
FROM strategy_advice_lifecycle_review
ORDER BY created_at_utc DESC
LIMIT 3;
```

具体表字段以当前 21 实现为准。

---

## 16. 验收标准

24D 验收通过条件：

```text
1. 21 advice 消费 23F 证据链。
2. 21 advice 消费 24C 模型审查结果。
3. notification_payload 保存证据链摘要。
4. Hermes 中文通知展示关键证据和模型反驳。
5. 缺 23F / 缺 24C / 模型不可采用时，通知透明说明。
6. 低质量模型结果不会被高权重采用。
7. 不重新调用大模型。
8. 不重新跑 23F。
9. 不破坏 21B / 21C 幂等。
10. 不改变不自动交易边界。
```

---

## 17. 24D 完成后的下一步

24D 完成后，进入：

```text
24E：24 阶段全链路验收
```

24E 重点验证：

```text
16 → 23F → 18 → 24C → 21 → Hermes
```

完整链路能自动运行、能追溯、能通知、能失败告警、不会静默、不重复刷屏。
