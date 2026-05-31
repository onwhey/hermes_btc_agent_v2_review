# 25：策略全链路统一编排计划

## 1. 阶段定位

25 不是新增一套并行调度器，也不是正式生产部署阶段。

25 的目标是把当前分散在 scheduler runner、17、16、24A、18、20C、21C 等位置的后置触发关系，收口成唯一的策略主链路编排层，避免重复跑策略、重复生成材料包、重复调用大模型、重复生成建议和重复发送通知。

一句话定义：

```text
25 = 策略全链路统一 pipeline 编排层
```

25 启用后，外层 scheduler / systemd / cron 只负责唤醒 pipeline；业务判断、执行顺序、锁、幂等、失败恢复和告警，必须由项目内 pipeline 代码负责。

---

## 2. 当前代码中的分散触发关系

本节是现状盘点，不是 25 的目标结构。

当前代码里已经存在若干自动或半自动触发关系，但这些关系分散在 scheduler runner、17、16、24A、18、20C、21C 中。它们不是一个统一 pipeline，也不能在 25 中被原样复制成第二套调度链路。

当前分散触发关系如下：

```text
09：4h K线增量采集成功
→ scheduler runner 后置触发 17
→ 17 调用 16
→ 16 在 ensure_latest_snapshot=true 时懒加载 / 复用 / 创建 15 市场快照
→ 16 基于快照运行 23B / 23C / 23D / 23E 等独立策略
→ 24A 在 16 写库成功后自动触发 23F 策略证据聚合
→ scheduler runner 在 17 success / partial_success 后，按开关触发 18
→ 18 生成 analysis material pack，并消费 23F 结果
→ scheduler runner 在 18 success / partial_success 后，按开关触发 20C
→ 20C 判断是否已有可用 19 模型审查结果，必要时调用 19，并推进 / 准备 20A 聚合
→ 20A 生成 model review aggregation run，即 MRAG
→ 21C 或现有 advice 自动链路消费 MRAG，推进 21A / 21B
→ 21A 生成 strategy_advice / lifecycle_review / trade_setup
→ 21B 生成或发送 Hermes 通知
```

注意：

```text
1. 上述顺序只是当前代码中分散存在的触发关系。
2. 它不是一个已经完成的统一 pipeline。
3. 25 不能在这套关系之外再新增一条并行调度链路。
4. 25 要做的是收口这套分散触发关系，让主链路只有一个入口和一个编排者。
```

---

## 3. 25 完成后的唯一目标顺序

25 完成后，主链路应收口为：

```text
09：4h K线采集成功
→ scheduler runner 只触发 25 pipeline
→ 25 pipeline 接收或解析本轮目标上下文：
   symbol / base_interval / higher_interval / kline_slot
→ 25 pipeline 加锁并创建 pipeline run
→ 25 pipeline 调用 17
→ 17 调用 16
→ 16 懒加载 / 复用 / 创建 15 市场快照
→ 16 运行 23B / 23C / 23D / 23E 等独立策略
→ 24A 自动生成 23F 策略证据聚合结果
→ 25 pipeline 校验 23F 是否存在且状态可用
→ 25 pipeline 调用 18 生成 analysis material pack
→ 25 pipeline 调用 20C / 19 / 20A 链路
→ 20C 判断是否复用旧模型结果或调用 19
→ 19 如被允许则执行模型审查
→ 20A 聚合可用模型审查结果，生成 MRAG
→ 25 pipeline 调用 21A / 21B 链路
→ 21A 生成最终人工建议及生命周期 review
→ 21B 生成 Hermes 通知；是否真实发送由开关控制
→ 25 pipeline 记录总结果和每一步结果
```

25 启用后，scheduler runner 不得绕过 25 直接分别触发 18、20C、21C。

目标结构必须满足：

```text
1. runner 只负责触发 25 pipeline。
2. 25 pipeline 是主链路唯一编排者。
3. 17 / 18 / 20C / 21C 可以保留，但必须作为 25 内部步骤或恢复入口，不能与 25 并行抢任务。
4. 24A 保留为 16 后置 hook，不由 25 重复调用 23F。
```

说明：
- symbol、base_interval、higher_interval 来自 09/17 上游任务参数。
- kline_slot 表示本轮刚完成且已成功落库的 base_interval K线 open_time_utc。
- 25 不得凭空猜测 kline_slot。
- 若上游事件没有显式传入 kline_slot，25 只能从已成功采集并通过质量检查的最新收盘 K线推导。
- 若无法唯一确定目标 K线，pipeline 必须 blocked，不得继续生成策略、模型审查或 advice。

---

## 4. 模块边界

### 4.1 09：数据入口

09 继续负责：

```text
1. 4h / 1d K线增量采集。
2. 写入数据库。
3. K线连续性和质量检查。
4. collector event 记录。
```

09 不负责完整策略链路，不直接调用 18、19、20、21。

### 4.2 17：策略信号调度步骤

17 保留，但在 25 启用后降级为 pipeline 内部步骤。

17 负责：

```text
1. 根据 collector 成功事件判断是否应该运行策略信号。
2. 处理 UTC 00:00 等需要等待 1d K线的边界。
3. 调用 16 StrategySignalService。
4. 写入 strategy_signal_scheduler_event_log。
```

17 不再作为绕过 25 的主链路调度器。

### 4.3 16：策略信号执行层

16 负责：

```text
1. 在 ensure_latest_snapshot=true 时懒加载 / 复用 / 创建 15 市场快照。
2. 运行已启用的独立策略，例如 23B / 23C / 23D / 23E。
3. 持久化 strategy_signal_run 和 strategy_signal_result。
4. 触发 24A 自动证据聚合 hook。
```

注意：

```text
16 不直接承担完整 pipeline 编排职责。
16 不应该主动调用 18 / 20 / 21。
```

### 4.4 15：懒加载市场快照

15 不是独立定时任务。

15 是由 16 在策略运行前通过 ensure_latest_snapshot 触发的快照能力，负责复用或创建最新合格市场快照。

### 4.5 24A / 23F：策略证据聚合

24A 保留为 16 写库成功后的自动 hook。

23F 是策略证据聚合结果，不是独立策略，也不是最终 advice。

25 不直接重复调用 23F。25 只校验 16 之后是否已经生成可用 23F：

```text
1. 如果 23F success：继续 18。
2. 如果 23F insufficient_evidence：是否继续由 25 规则决定，但必须透明记录。
3. 如果 23F failed：pipeline 应停止或降级，并 Hermes 告警。
```

### 4.6 18：材料包生成

18 负责生成 analysis material pack。

18 必须消费 23F 结果，并输出 material_schema_v2。

25 调用 18 时，必须使用当前 pipeline 内的 strategy_signal_run_id，禁止扫描旧 SSR 随机生成材料包。

### 4.7 20C / 19 / 20A：模型审查链路

该链路必须拆清：

```text
20C：模型审查调用判断 / 复用 / 成本闸门 / 真实模型调用控制。
19：实际模型审查能力层，即 model_analysis。
20A：模型审查结果聚合，生成 MRAG。
```

25 不直接绕过 20C 强行调用 19。

25 必须遵守：

```text
1. 如果已有当前 material_pack 可用 19 审查结果，可复用。
2. 如果旧审查结果超过复用有效期，不得继续复用。
3. 如果真实模型开关关闭，不得调用真实模型。
4. 如果调用真实模型，必须满足成本确认 / env 开关 / model profile 启用等条件。
5. 20A 只聚合兼容 schema 的模型审查结果。
```

### 4.8 21A / 21B / 21C：建议与通知

21A 负责生成 strategy_advice、lifecycle_review、trade_setup。

21B 负责生成 / 发送 Hermes 通知。

21C 在 25 启用后应降级为：

```text
1. 25 内部可调用的 advice 自动链路能力；或
2. 失败恢复 / 补通知 worker；或
3. 兼容手动入口。
```

21C 不得与 25 并行消费同一个 MRAG。

---

## 5. 禁止重复调度规则

25 必须写死以下规则：

```text
1. 同一 symbol + base_interval + higher_interval + kline_slot 只能有一个主 pipeline run。
2. 同一 collector 成功事件不得触发多个并行 pipeline。
3. 同一 strategy_signal_run_id 不得重复生成多个主 23F 结果。
4. 同一 strategy_signal_run_id 不得重复生成多个主 material pack，除非显式 force 并有审计记录。
5. 同一 material_pack_id 不得重复真实调用大模型，除非显式 force 并有成本确认。
6. 同一 MRAG 不得重复生成 strategy_advice。
7. 同一 review_id 不得重复发送 Hermes。
8. runner 不得在 25 启用后继续直接触发 18 / 20C / 21C 主链路。
9. 17 / 18 / 20C / 21C 的旧入口可以保留，但必须受 pipeline 锁和数据库幂等约束。
```

禁止出现：

```text
17 跑一次 16，25 又跑一次 16。
18 自动跑一次，25 又跑一次 18。
20C 自动调用一次模型，25 又触发一次模型。
21C 扫描 MRAG 生成 advice，25 又对同一个 MRAG 生成 advice。
21B 对同一个 review_id 重复发送通知。
```

---

## 6. pipeline 身份与锁

25 pipeline run 建议以以下字段定义唯一身份：

```text
symbol
base_interval
higher_interval
base_kline_open_time_utc 或 base_kline_close_time_utc
upstream_collector_event_id 或 upstream_job_run_id
trigger_source
```

Redis 锁建议包含：

```text
strategy_pipeline:{symbol}:{base_interval}:{higher_interval}:{base_kline_close_time_utc}
```

数据库侧建议新增或复用 pipeline 执行日志，至少记录：

```text
pipeline_run_id
symbol
base_interval
higher_interval
base_kline_open_time_utc
base_kline_close_time_utc
upstream_collector_event_id
trigger_source
status
current_step
strategy_signal_run_id
strategy_evidence_aggregation_id
material_pack_id
model_analysis_run_id
model_review_aggregation_run_id
advice_id
review_id
notification_status
real_model_invoked
real_notification_sent
error_code
error_message
trace_id
started_at_utc
finished_at_utc
details_json
```

如果现有 scheduler 日志能完整表达这些字段，可以复用；否则新增轻量表，例如：

```text
strategy_pipeline_run
strategy_pipeline_step_event
```

---

## 7. pipeline 步骤状态

每一步都必须有明确状态：

```text
pending
running
success
partial_success
skipped
blocked
failed
```

建议步骤名固定为：

```text
preflight
acquire_lock
run_strategy_signal_17_16
verify_strategy_evidence_23f
run_material_pack_18
run_model_review_chain_20c_19_20a
run_advice_21a
run_notification_21b
finalize
```

失败时必须记录：

```text
失败步骤
是否已写库
是否已调用真实模型
是否已生成 MRAG
是否已生成 advice
是否已生成 Hermes 通知
是否已真实发送 Hermes
下一次是否可恢复
```

---

## 8. 开关规则

25 可以新增 pipeline 级开关，但只能进一步限制，不能绕过下游开关。

建议新增：

```env
STRATEGY_PIPELINE_ENABLED=false
STRATEGY_PIPELINE_SCHEDULER_ENABLED=false
STRATEGY_PIPELINE_REAL_MODEL_ENABLED=false
STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST=false
STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=false
```

含义：

```text
STRATEGY_PIPELINE_ENABLED：是否允许手动运行 pipeline。
STRATEGY_PIPELINE_SCHEDULER_ENABLED：是否允许 scheduler 自动触发 pipeline。
STRATEGY_PIPELINE_REAL_MODEL_ENABLED：pipeline 是否允许进入真实模型调用路径。
STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST：pipeline 是否确认真实模型成本。
STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED：pipeline 是否允许真实发送最终策略通知。
```

但必须同时遵守已有下游开关：

```env
STRATEGY_SIGNAL_SCHEDULER_ENABLED
STRATEGY_EVIDENCE_AGGREGATION_ENABLED
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED
MODEL_REVIEW_REAL_MODEL_ENABLED
STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED
```

任何 pipeline 级开关都不能绕过下游开关。

示例：

```text
如果 STRATEGY_PIPELINE_REAL_MODEL_ENABLED=true，
但 MODEL_REVIEW_REAL_MODEL_ENABLED=false，
则仍不得调用真实模型。
```

---

## 9. 25A 交付范围

25A 先做手动 pipeline CLI，不接 scheduler 自动触发。

新增入口建议：

```bash
python -m scripts.run_strategy_pipeline \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --confirm-write
```

25A 必须完成：

```text
1. pipeline service。
2. pipeline CLI。
3. 手动触发完整链路。
4. 调用 17，继而由 17 调用 16。
5. 校验 24A / 23F 结果。
6. 调用 18。
7. 调用 20C / 19 / 20A 链路。
8. 调用 21A / 21B 链路。
9. 写入 pipeline run / step 日志。
10. 输出每一步产物 ID。
11. 不真实发送 Hermes，除非显式开关允许。
12. 不自动交易。
```

25A 不做：

```text
1. scheduler 自动接入。
2. systemd 生产部署。
3. 新策略。
4. 弱模型。
5. 复盘。
6. Admin。
7. 自动交易。
```

---

## 10. 25B 交付范围

25B 做锁、幂等、失败恢复。

必须覆盖：

```text
1. Redis pipeline 锁。
2. 数据库幂等兜底。
3. 重复运行同一 kline_slot 不重复生成 advice。
4. 18 已成功时不重复生成 material pack，除非 force。
5. 19 已成功且可复用时不重复调用真实模型。
6. 21A 已成功时不重复生成 advice。
7. 21B 已生成通知时不重复生成 / 发送通知。
8. 上一次失败后可以从安全步骤恢复。
9. 所有恢复动作必须记录 step event。
```

---

## 11. 25C 交付范围

25C 才接入 scheduler 自动触发。

25C 修改 runner 时必须满足：

```text
1. 09 采集成功后，runner 只触发 25 pipeline。
2. runner 不再直接触发 17 → 18 → 20C → 21C 分散后置链路。
3. 如果 STRATEGY_PIPELINE_SCHEDULER_ENABLED=false，保留旧链路或直接跳过，具体由 plan 明确。
4. 如果启用 25 pipeline，旧链路必须关闭或降级为 pipeline 内部步骤。
5. scheduler 自动触发时 trigger_source=scheduler。
6. 手动 CLI 触发时 trigger_source=cli。
```

25C 是最容易搞乱调度的阶段，不能提前做。

---

## 12. 25D 交付范围

25D 做失败告警和最终运行报告。

失败告警必须说明：

```text
pipeline_run_id
失败步骤
失败原因
是否已写库
是否调用过真实模型
是否生成 MRAG
是否生成 advice
是否生成 Hermes 通知
是否真实发送 Hermes
建议人工动作
trace_id
```

告警不是交易建议，不得包含自动交易动作。

---

## 13. 测试要求

必须新增或修改测试覆盖：

```text
1. 25A 手动 pipeline 成功路径。
2. 16 成功但 23F 失败时 pipeline 停止并记录失败。
3. 18 成功但 20C 被模型开关阻断时 pipeline 正确 blocked。
4. 19 已有可复用结果时不重复调用真实模型。
5. 20A schema 不兼容时 pipeline 给出清晰错误。
6. 21A 已处理同一 MRAG 时不重复生成 advice。
7. 21B 已处理同一 review_id 时不重复通知。
8. pipeline 级真实模型开关不能绕过 MODEL_REVIEW_REAL_MODEL_ENABLED。
9. pipeline 级通知开关不能绕过 STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED。
10. scheduler 自动接入后 runner 不再绕过 25 直接触发 18 / 20C / 21C。
11. 所有边界字段保持 false：is_final_trading_advice / is_trading_signal / is_executable / auto_trading_allowed。
```

常规测试命令：

```bash
python -m pytest tests/scheduler -q
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/model_review_aggregation -q
python -m pytest tests/strategy_advice -q
```

如新增 pipeline 专用测试目录：

```bash
python -m pytest tests/strategy_pipeline -q
```

---

## 14. 验收标准

25A 验收：

```text
1. 手动一条命令可以跑完整链路。
2. 输出 SSR / SEA / AMP / MAR / MRAG / ADV / ADVR 等关键 ID。
3. 每一步状态清晰。
4. 不重复调用真实模型。
5. 不重复生成 advice。
6. 不真实发送 Hermes，除非明确开启。
7. 不改变自动交易边界。
```

25B 验收：

```text
1. 重复运行同一 slot 不重复生成主产物。
2. 失败后可安全恢复。
3. 锁与数据库幂等均生效。
```

25C 验收：

```text
1. runner 只触发 25 pipeline。
2. 旧的 17 / 18 / 20C / 21C 分散后置链路不再与 25 并行竞争。
3. scheduler 触发顺序清楚可追踪。
```

25D 验收：

```text
1. 每类失败都有中文 Hermes 告警。
2. 告警说明失败阶段和当前已完成产物。
3. 告警不是交易建议。
```

---

## 15. Codex 完成后必须输出的内容

Codex 完成每个子阶段后，必须在回复中输出：

```text
1. 修改文件列表。
2. 新增 / 修改的 env 开关。
3. 新增 / 修改的数据表或字段。
4. 实际实现的调度顺序。
5. 旧入口如何降级或保留。
6. 哪些地方防止重复运行。
7. 哪些地方防止重复真实模型调用。
8. 哪些地方防止重复通知。
9. 每一步失败时的处理规则。
10. 测试结果。
11. 仍未覆盖的风险。
```

其中第 4 点必须用明确顺序写出，例如：

```text
runner → 25 pipeline → 17 → 16 → 15 snapshot resolver → 23B/C/D/E → 24A/23F → 18 → 20C/19/20A → 21A/21B
```

如果 Codex 不能明确输出这个顺序，说明 25 没有真正收口调度，不能验收。

---

## 16. 最终原则

25 最大风险不是代码写不出来，而是调度链路被写乱。

所以本阶段最重要的不是“多跑几个功能”，而是确保：

```text
1. 主链路只有一个编排者。
2. 旧入口不与新 pipeline 抢任务。
3. 每一步都有幂等。
4. 每一步失败都可追踪。
5. 大模型和 Hermes 真实发送都受明确开关控制。
6. 系统仍然只提供人工建议，不自动交易。
```
