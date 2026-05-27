# 24B_material_pack_consumes_23f.md

## 1. 阶段名称

第 24B 阶段：`material_pack_consumes_23f`

中文名称：18 模型材料包正式消费 23F 策略证据聚合结果。

---

## 2. 阶段目标

24B 目标：

```text
18 生成模型材料包时，优先读取 23F strategy_evidence_aggregation_result。
```

当前状态：

```text
18 已有材料包能力
23F 已能生成策略证据聚合结果
但 18 尚未正式以 23F 作为策略证据主来源
```

24B 完成后：

```text
23F 策略证据聚合
↓
18 material pack
↓
19 / 20 大模型审查
```

---

## 3. 18 的职责边界

24B 开始明确 18 的角色：

```text
18 = Material Pack Builder / 材料包组装器
```

18 负责：

```text
把策略证据、行情摘要、现有数学材料、后续模型材料组织成大模型可审查输入
```

18 不负责：

```text
重新解释策略之间的关系
重新判断谁支持谁反对
重新实现 23F 聚合逻辑
```

注意：

```text
24B 不拆现有数学材料。
25 再把 market_math / 弱模型证据层独立出去。
```

---

## 4. 消费 23F 的规则

18 生成 material pack 时：

```text
如果存在对应 strategy_signal_run_id 的 23F aggregation：
  优先读取 23F 聚合摘要
否则：
  保持旧逻辑，不崩溃
```

读取字段建议：

```text
strategy_evidence_summary_json
decision_source_chain_json
role_coverage_matrix_json
evidence_missing_json
strategy_conflict_summary_json
participation_summary_json
observe_only_summary_json
risk_gate_summary_json
model_review_focus_json
candidate_bias
candidate_confidence
decision_readiness
status
```

---

## 5. 23F 来源标记

18 material pack 中必须明确标记：

```text
strategy_evidence_source = strategy_evidence_aggregation_result
strategy_evidence_aggregation_id = AGG-xxx
strategy_signal_run_id = SSR-xxx
```

如果没有 23F：

```text
strategy_evidence_source = legacy_strategy_results
strategy_evidence_aggregation_id = null
```

不要让后续大模型误以为已经经过 23F 聚合。

---

## 6. material pack 建议结构

18 输出中建议增加或规范化：

```json
{
  "strategy_evidence": {
    "source": "strategy_evidence_aggregation_result",
    "aggregation_id": "SEA-xxx",
    "strategy_signal_run_id": "SSR-xxx",
    "candidate_bias": "wait",
    "decision_readiness": "wait_for_confirmation",
    "strategy_evidence_summary": {},
    "decision_source_chain": [],
    "role_coverage_matrix": {},
    "strategy_conflict_summary": [],
    "risk_gate_summary": {},
    "model_review_focus": []
  }
}
```

如果没有 23F：

```json
{
  "strategy_evidence": {
    "source": "legacy_strategy_results",
    "aggregation_id": null,
    "strategy_signal_run_id": "SSR-xxx",
    "warning": "23F aggregation not found; material pack used legacy compatible strategy evidence."
  }
}
```

---

## 7. 没有 23F 时的兼容

24B 必须保持兼容。

没有 23F 时：

```text
18 不得失败
18 不得阻断整个材料包生成
18 应写入 warning / evidence_source 标记
18 可以继续使用旧逻辑
```

但如果 23F 自动聚合失败已经由 24A 告警，则 18 不需要重复发送 Hermes 告警。

---

## 8. 与 24A 的关系

24A 负责：

```text
16 跑完策略后自动生成 23F
23F 失败时 Hermes 告警
```

24B 负责：

```text
18 读取 23F
没有 23F 时兼容旧逻辑
```

24B 不负责：

```text
自动触发 23F
23F 失败告警
23F 手动补跑
```

---

## 9. 与 19 / 20 的关系

24B 不重构大模型审查。

但 24B 需要确保 material pack 中已有：

```text
strategy_evidence_summary
decision_source_chain
model_review_focus
```

后续 24C 再让 19 / 20 明确基于这些字段审查：

```text
是否同意 23F
哪个策略证据可能有问题
是否建议等待 / 降级 / 否决
```

---

## 10. 与 25 的关系

24B 不做数学/弱模型层。

但 material pack 结构应预留多证据来源：

```text
evidence_sources:
  strategy_evidence_aggregation
  existing_market_math_material
  future_math_model_evidence
```

25 后续可以新增：

```text
market_math_feature_model
weak_model_evidence
```

18 再消费这些独立证据来源。

---

## 11. 代码位置建议

可能涉及：

```text
app/strategy_aggregation/material_pack_builder.py
app/strategy/aggregation/evidence_repository.py
app/strategy/aggregation/material_builder.py
scripts/build_model_review_material_pack.py 或相关 18 CLI
tests/strategy_aggregation/
```

要求：

```text
不要把 23F 聚合逻辑复制到 18。
18 只读取 23F 已产出的聚合结果。
```

---

## 12. 数据库要求

优先读取已有：

```text
strategy_evidence_aggregation_result
```

不建议新增表。

如果现有 repository 缺少按 `strategy_signal_run_id` 查询 23F aggregation 的方法，可以补最小查询方法。

不新增 migration，除非确实发现 23F 表缺少必要字段。

---

## 13. 测试要求

至少测试：

```text
1. 存在 23F aggregation 时，18 material pack 优先读取 23F。
2. material pack 包含 strategy_evidence.source = strategy_evidence_aggregation_result。
3. material pack 包含 aggregation_id / strategy_signal_run_id。
4. material pack 包含 strategy_evidence_summary / decision_source_chain / model_review_focus。
5. 不存在 23F aggregation 时，18 不崩溃。
6. 不存在 23F aggregation 时，strategy_evidence.source = legacy_strategy_results。
7. 18 不读取 strategy_payload_json。
8. 18 不重新计算 23F 聚合结论。
9. 18 保留旧数学材料逻辑。
10. 24B 不发送 Hermes 告警。
```

建议运行：

```bash
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/strategy -q
```

如有 18 专用测试目录，也必须运行对应测试。

---

## 14. 验收标准

24B 验收通过条件：

```text
1. 18 能读取 23F aggregation。
2. 18 material pack 中明确标记策略证据来源。
3. 23F 存在时优先使用 23F。
4. 23F 不存在时保持旧逻辑不崩。
5. 18 不复制 23F 聚合逻辑。
6. 18 不读取 strategy_payload_json。
7. 18 保留现有数学材料逻辑。
8. 24B 不调用大模型。
9. 24B 不生成 advice。
10. 24B 不发送 Hermes。
```

---

## 15. 后续

24B 完成后再推进：

```text
24C：大模型审查输入使用 23F 证据链
24D：advice / Hermes 最终通知展示证据链
25：市场数学与弱模型证据层
```
