# 24_strategy_evidence_chain_integration_overview.md

## 1. 阶段名称

第 24 阶段：`strategy_evidence_chain_integration`

中文名称：策略证据链接入下游主链路。

---

## 2. 阶段背景

23 阶段已经完成策略接力主链路：

```text
23B：市场方向与阶段
23C：支撑压力
23D：突破 / 跌破 / 回踩触发
23E：波动率与风控闸门
23F：策略证据聚合 / 策略组长层
```

但当前主链路仍存在断点：

```text
16 可以运行 23B/C/D/E
23F 可以单独聚合策略结果
但 16 跑完策略后，还没有自动触发 23F
18 也还没有正式以 23F 作为策略证据来源
```

所以 24 的目标不是继续新增策略，而是让 23F 正式进入下游链路。

---

## 3. 24 阶段总目标

24 阶段目标：

```text
把 23F 策略证据聚合结果接入 18 模型材料包主链路。
```

当前先做：

```text
24A：16 runner 跑完策略后，可配置自动触发 23F
24B：18 正式消费 23F aggregation
```

暂不做：

```text
24C：大模型审查输入深度改造
24D：advice / Hermes 最终证据链通知改造
25：市场数学与弱模型证据层
```

---

## 4. 设计原则

### 4.1 闭环优先

当前最重要的是先让：

```text
策略结果
↓
23F 策略证据链
↓
18 模型材料包
```

跑通。

不要在 24 同时新增数学模型层、弱模型层或新的交易策略。

### 4.2 23F 与 18 分层

```text
23F = 策略域聚合层
18 = 模型材料包组装层
```

23F 负责：

```text
解释策略之间的关系
说明谁支持、谁反对、谁等待、谁阻断
生成 strategy_evidence_summary / decision_source_chain / model_review_focus
```

18 负责：

```text
把策略证据、行情摘要、数学材料、后续模型材料整理成大模型可读 material pack
```

18 不应该重新实现 23F 的策略语义聚合逻辑。

### 4.3 18 在 24 开始职责降级

24 中可以开始明确：

```text
18 是材料包组装器
```

但 24 不做大拆分。

24 中：

```text
18 优先读取 23F
18 保留旧数学材料逻辑
18 没有 23F 时不崩溃
```

25 再把 market_math / 弱模型证据层从 18 中抽象出来。

---

## 5. 阶段拆分

### 5.1 24A：自动策略证据聚合

目标：

```text
16 runner 跑完 23B/C/D/E 等全部策略后，可以根据配置自动调用 23F。
```

重点：

```text
STRATEGY_EVIDENCE_AGGREGATION_ENABLED
23F 自动触发
失败不回滚策略结果
失败必须 Hermes 告警
幂等
可手动补跑
```

### 5.2 24B：18 消费 23F

目标：

```text
18 生成材料包时，优先读取 23F strategy_evidence_aggregation_result。
```

重点：

```text
strategy_evidence_summary
decision_source_chain
role_coverage_matrix
strategy_conflict_summary
risk_gate_summary
model_review_focus
```

如果 23F 不存在：

```text
18 保持旧逻辑，不崩溃。
```

---

## 6. 本阶段不做

24A/24B 不做：

```text
不新增交易策略
不改 23B/C/D/E 核心算法
不改 23F 聚合核心算法，除非为接入修补明显 bug
不做数学/弱模型证据层
不调用大模型
不改最终 advice 生成逻辑
不生成 trade_setup
不输出正式 entry / stop_loss / take_profit
不做 Hermes 最终策略通知重构
不读取账户或持仓
不自动交易
```

---

## 7. 失败处理总原则

如果 23F 自动聚合失败：

```text
1. 不回滚 23B/C/D/E 已落库策略结果
2. 必须记录失败日志 / 事件
3. 必须通过 Hermes 明确通知用户
4. 通知必须包含 strategy_signal_run_id、失败原因、trace_id、是否可手动补跑
5. 不允许静默失败
6. 告警内容必须由固定模板生成，不调用大模型
```

---

## 8. 24 完成后的链路

24A/24B 完成后，主链路应变为：

```text
16 runner 运行全部策略
↓
自动生成 23F strategy_evidence_aggregation_result
↓
18 material pack 优先读取 23F 策略证据链
↓
后续 19/20/21 使用更清晰的策略证据材料
```

---

## 9. 后续阶段建议

24A/24B 完成后，再推进：

```text
24C：模型审查输入使用 23F 证据链
24D：advice / Hermes 通知展示完整证据链
25：市场数学与弱模型证据层
```

不要在 24A/24B 中提前做这些内容。

---

## 10. 验收标准

24 总体验收标准：

```text
1. 16 跑完策略后，可以自动生成 23F 聚合结果。
2. 自动触发有独立开关。
3. 23F 自动失败不影响策略结果落库。
4. 23F 自动失败必须 Hermes 告警。
5. 18 优先消费 23F 聚合结果。
6. 没有 23F 时 18 保持兼容不崩溃。
7. 24 不引入新策略、不调用大模型、不生成最终 advice。
```
