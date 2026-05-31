# 25B：scheduler runner 接入策略 pipeline 自动编排

## 1. 本章定位

25B 的目标是把 25A 已经跑通的手动 `strategy_pipeline`（策略流水线）接入自动调度链路。

25A 已完成的是：

```text
手动 CLI → 25 pipeline → 17/16 → 23F → 18 → 20C/19/20A → 21A/21B
```

25B 要完成的是：

```text
09：4h K线采集成功
→ scheduler runner（调度运行器）
→ 只触发 25 pipeline
→ 25 pipeline 内部继续编排完整策略链路
```

25B 不是新增策略，不是修改模型审查，不是修改建议逻辑。它只解决一个工程问题：**自动调度入口统一收口到 25 pipeline，避免旧链路和新链路多头触发。**

---

## 2. 当前问题

25A 已能作为手动入口跑完整链路，但当前自动链路仍存在历史阶段遗留：

```text
09 采集成功后，scheduler runner 可能直接触发 17；
25 pipeline 内部也会调用 17/16；
如果后续再让 runner 触发 25，就可能出现多头调度。
```

必须避免以下错误结构：

```text
09 成功
→ runner 触发 17
→ runner 又触发 25
→ 25 内部再次触发 17/16
```

这会导致重复记录、重复策略运行、重复模型审查、重复建议通知，后续排查会非常困难。

---

## 3. 25B 完成后的目标顺序

25B 完成后，自动主链路应为：

```text
09：4h K线采集成功
→ scheduler runner 检测到本轮 09 成功
→ runner 只触发 25 pipeline
→ 25 pipeline 接收 symbol / base_interval / higher_interval / kline_slot_utc
→ 25 pipeline 内部编排：
   17/16：策略信号生成，16 懒加载 15 快照并运行 23B/23C/23D/23E
   → 24A/23F：策略证据聚合
   → 18：模型材料包生成
   → 20C/19/20A：模型审查复用 / 调用 / 聚合
   → 21A/21B：建议生命周期与通知记录
```

重点：

```text
scheduler runner 只负责触发 25；
25 负责内部编排；
17、18、20、21 的 service 和 CLI 保留，但不再由 runner 多头直接触发。
```

---

## 4. 范围

### 4.1 本章要做

1. 新增或完善自动调度开关，例如：

```text
STRATEGY_PIPELINE_SCHEDULER_ENABLED=false
```

该开关只控制 scheduler runner 是否自动触发 25 pipeline。

2. 修改 scheduler runner：

```text
当 09 的 4h K线采集成功后，如果 STRATEGY_PIPELINE_SCHEDULER_ENABLED=true，runner 触发 25 pipeline。
```

3. runner 触发 25 时，必须把明确的上下文传给 25：

```text
symbol
base_interval
higher_interval
kline_slot_utc
trigger_source=scheduler
```

`kline_slot_utc` 必须来自本轮 09 成功采集的已收盘 K线，不允许在 runner 中凭空使用“当前最新时间”猜测。

4. 当 pipeline 自动入口启用时，runner 不得再直接触发旧 17 自动链路。

5. 25B 必须复用 25A 已有锁、幂等、stage 编排和 event log，不新增第二套业务链路。

6. pipeline 自动执行失败时，必须记录可追踪日志，并通过固定模板产生系统级失败提醒，不允许静默失败。

### 4.2 本章不做

1. 不修改 23B/23C/23D/23E/23F 策略算法。
2. 不修改 18 材料包结构，除非发现 25B 接入必须补字段，但原则上不应改。
3. 不修改 19 prompt（提示词）。
4. 不修改 20 的模型复用规则。
5. 不修改 21 建议生命周期规则。
6. 不新增自动交易、账户读取、仓位读取、下单、调杠杆能力。
7. 不删除旧 17/18/20/21 CLI；它们仍用于人工排查和恢复。

---

## 5. 自动开关规则

### 5.1 新增 25B 自动入口开关

建议新增：

```text
STRATEGY_PIPELINE_SCHEDULER_ENABLED=false
```

默认必须为 `false`，防止部署后自动接管。

含义：

```text
false：scheduler runner 不自动触发 25 pipeline。
true：scheduler runner 在 09 成功后触发 25 pipeline。
```

### 5.2 与旧 17 自动开关的关系

如果当前仍存在旧的 17 自动开关，例如：

```text
STRATEGY_SIGNAL_SCHEDULER_ENABLED=true/false
```

则 25B 必须明确优先级：

```text
当 STRATEGY_PIPELINE_SCHEDULER_ENABLED=true 时，runner 以 25 pipeline 为唯一自动主链路；
不得同时直接触发旧 17 自动链路。
```

如果两个开关都为 true：

```text
25 pipeline 优先；
旧 17 直接触发应被跳过；
必须写日志说明 old_stage17_auto_trigger_skipped_due_to_pipeline_enabled。
```

### 5.3 真实模型调用开关

25B 不得因为自动调度而绕过真实模型成本控制。

自动 pipeline 是否真实调用模型，必须继续受现有模型开关控制。如果当前 25A 已有 pipeline 级真实模型开关，则复用现有开关；如果 scheduler 场景缺少明确同意开关，则新增 scheduler 专用确认开关，例如：

```text
STRATEGY_PIPELINE_SCHEDULER_USE_REAL_MODEL=false
STRATEGY_PIPELINE_SCHEDULER_CONFIRM_REAL_MODEL_COST=false
```

默认必须为 `false`。

只有同时满足以下条件，scheduler 自动触发的 25 pipeline 才允许真实调用 DeepSeek / OpenAI / Claude 等外部模型：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED=true
STRATEGY_PIPELINE_REAL_MODEL_ENABLED=true（如已存在）
STRATEGY_PIPELINE_SCHEDULER_USE_REAL_MODEL=true
STRATEGY_PIPELINE_SCHEDULER_CONFIRM_REAL_MODEL_COST=true
```

如果这些条件不满足，25 pipeline 可以运行到模型阶段，但不得真实调用模型。

### 5.4 Hermes 真实发送开关

25B 不得因为自动调度而绕过 Hermes 发送控制。

建议规则：

```text
策略建议通知是否真实发送，继续受 21/通知层原有发送开关控制。
如果需要 pipeline scheduler 专用开关，则新增：
STRATEGY_PIPELINE_SCHEDULER_SEND_REAL_ALERT=false
```

默认必须为 `false`。

即使 pipeline 自动执行，系统仍要区分：

```text
通知记录生成成功
真实 Hermes 发送成功
真实 Hermes 被配置阻断
真实 Hermes 发送失败
```

---

## 6. 触发条件

25B 只允许在明确的 09 成功事件后触发 pipeline。

### 6.1 必要条件

runner 触发 25 前，必须确认：

```text
1. 本轮 09 任务成功完成。
2. 目标 K线为已收盘 K线。
3. symbol 明确，例如 BTCUSDT。
4. base_interval 明确，例如 4h。
5. higher_interval 明确，例如 1d。
6. kline_slot_utc 明确，且对应本轮已收盘 4h K线 open_time_utc。
```

如果无法确定 `kline_slot_utc`，必须 blocked，不允许用当前时间猜。

### 6.2 不允许触发的情况

以下情况不得触发 25 pipeline：

```text
1. 09 采集失败。
2. 09 被跳过且没有新收盘 K线。
3. K线质量检查失败，且现有规则要求阻断策略。
4. 无法确定 symbol / interval / kline_slot。
5. pipeline scheduler 开关关闭。
6. 已存在同一 slot 的 pipeline 正在运行，无法取得锁。
```

---

## 7. 25B 调用 25 pipeline 的请求参数

scheduler runner 调用 25 pipeline 时，应构造类似请求：

```text
symbol=BTCUSDT
base_interval=4h
higher_interval=1d
kline_slot_utc=<来自 09 成功事件的 target_base_open_time_utc>
trigger_source=scheduler
confirm_write=true
retry_failed_stage17=false（默认）
use_real_model=<由 scheduler 模型开关决定>
confirm_real_model_cost=<由 scheduler 成本确认开关决定>
send_real_alert=<由 scheduler / 21 通知开关共同决定>
```

默认自动调度不应启用 `retry_failed_stage17`。

`retry_failed_stage17` 是人工恢复参数，不应被 scheduler 自动使用。否则某个失败 slot 可能被自动反复重试。

---

## 8. 幂等与锁

25B 必须复用 25A 的 pipeline 锁：

```text
strategy_pipeline:{symbol}:{base_interval}:{higher_interval}:{kline_slot_utc}
```

要求：

```text
1. 同一根 K线不能并发跑多个 pipeline。
2. runner 不得在 pipeline 锁外自行重试完整链路。
3. 如果 pipeline 返回 lock_conflict，runner 记录 skipped/blocked，不再触发旧 17。
4. 如果 pipeline 已经为同一 slot 生成过后续结果，应依赖 25 内部幂等复用，不在 runner 重写一套幂等。
```

25B 不能把 runner 层锁当成唯一防重复机制。真正的业务幂等仍由各阶段已有规则负责：

```text
17/16：同一 slot 的策略信号幂等
23F：同一 SSR 的证据聚合幂等
18：同一 SSR/SEA 的材料包幂等
19/20：同一 AMP 的模型审查与聚合复用
21：同一 MRAG 的建议生命周期幂等
21B：同一 review_id 的通知幂等
```

---

## 9. 日志与告警

### 9.1 必须记录 runner → pipeline 触发结果

runner 触发 25 pipeline 后，必须记录：

```text
trigger_source=scheduler
pipeline_run_id
symbol
base_interval
higher_interval
kline_slot_utc
status
current_step
strategy_signal_run_id
strategy_evidence_aggregation_id
material_pack_id
model_analysis_run_id
review_aggregation_run_id
advice_id
review_id
model_review_invoked
model_review_reused
real_model_called
hermes_real_sent
error_code
error_message
trace_id
```

如果现有 `strategy_pipeline_event_log` 已能表达，则复用。runner 自身日志只保存触发关系和简要状态。

### 9.2 失败提醒

如果 scheduler 自动触发 25 pipeline 后出现：

```text
failed
blocked
partial_success 且关键阶段缺失
```

必须产生系统级失败提醒。

提醒要求：

```text
1. 固定模板生成，不调用大模型。
2. 必须说明失败阶段 current_step。
3. 必须说明 error_code / error_message。
4. 必须说明 pipeline_run_id / trace_id。
5. 必须说明这不是交易建议。
6. 真实发送仍受 Hermes 配置和系统告警发送配置控制，但不得静默。
```

如果 Hermes 真实发送失败，不回滚 pipeline 结果，只记录 alert_message / event / log。

---

## 10. 兼容旧链路

25B 不删除旧链路。

旧能力仍保留：

```text
scripts.run_strategy_signals
scripts.run_strategy_evidence_aggregation
scripts.run_strategy_aggregation_material_pack
scripts.run_model_analysis
scripts.run_model_review_aggregation
scripts.run_strategy_advice_scheduler
```

但自动主链路必须收口：

```text
当 STRATEGY_PIPELINE_SCHEDULER_ENABLED=true 时，runner 自动入口只触发 25 pipeline。
```

旧 CLI 仍用于人工排查、补跑和恢复。

---

## 11. 不变量

25B 完成后，必须继续满足：

```text
is_final_trading_advice=false
is_trading_signal=false
is_executable=false
auto_trading_allowed=false
```

自动调度不改变交易边界。

系统仍然：

```text
不自动交易
不读取账户
不读取真实仓位
不下单
不调杠杆
不生成订单
```

---

## 12. 测试要求

新增或修改测试，建议放在：

```text
tests/strategy_pipeline/
tests/scheduler/
```

至少覆盖以下场景：

### 12.1 开关关闭

```text
STRATEGY_PIPELINE_SCHEDULER_ENABLED=false
```

预期：runner 不触发 25 pipeline。

### 12.2 开关开启后触发 25

```text
09 采集成功
STRATEGY_PIPELINE_SCHEDULER_ENABLED=true
```

预期：runner 调用 25 pipeline，并传入正确的 `symbol/base_interval/higher_interval/kline_slot_utc`。

### 12.3 不再直接触发旧 17

当 pipeline scheduler enabled=true 时，即使旧 17 自动开关也为 true，runner 不得直接调用旧 17 自动链路。

预期：只触发 25 pipeline。

### 12.4 kline_slot 不能猜

如果 09 事件无法提供明确 slot，runner 不触发 pipeline，记录 blocked。

### 12.5 pipeline 锁冲突

如果同一 slot 已有 pipeline 锁，runner 不重复触发旧 17，记录 skipped/blocked。

### 12.6 自动模式默认不真实调用模型

默认 scheduler 自动 pipeline 不应真实调用外部模型。

预期：

```text
real_model_called=false
```

### 12.7 自动模型调用必须显式开关

只有 scheduler 真实模型开关、pipeline 真实模型开关、模型真实调用总开关全部满足时，才允许真实模型调用。

### 12.8 自动模式默认不真实发送 Hermes

默认 scheduler 自动 pipeline 不应真实发送 Hermes。

预期：

```text
hermes_real_sent=false
```

### 12.9 失败必须记录

pipeline 返回 failed/blocked 时，runner 必须记录 pipeline_run_id、current_step、error_code、trace_id。

### 12.10 不破坏非交易边界

所有自动 pipeline 输出仍必须满足：

```text
is_final_trading_advice=false
is_trading_signal=false
is_executable=false
auto_trading_allowed=false
```

---

## 13. 验收命令建议

### 13.1 测试

```bash
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/scheduler -q
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_advice -q
```

### 13.2 手动模拟 runner 触发

如果已有 scheduler 检查脚本，可增加类似 dry-run 验证：

```bash
STRATEGY_PIPELINE_SCHEDULER_ENABLED=true \
STRATEGY_PIPELINE_ENABLED=true \
STRATEGY_EVIDENCE_AGGREGATION_ENABLED=true \
python -m scripts.check_strategy_pipeline_scheduler \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --kline-slot-utc "2026-05-30T20:00:00Z" \
  --trigger-source cli \
  --dry-run
```

若不新增脚本，也可以通过现有 scheduler runner 测试入口模拟 09 成功事件，但必须清楚输出是否触发 25，而不是触发旧 17。

### 13.3 自动真实运行前检查

正式打开自动 25 前，必须确认：

```text
STRATEGY_PIPELINE_SCHEDULER_ENABLED=true
STRATEGY_SIGNAL_SCHEDULER_ENABLED 不会导致 runner 直接触发旧 17
STRATEGY_PIPELINE_REAL_MODEL_ENABLED 按预期设置
MODEL_REVIEW_REAL_MODEL_ENABLED 按预期设置
STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED 按预期设置
Hermes 发送开关按预期设置
```

---

## 14. Codex 完成后必须报告

Codex 完成 25B 后，必须报告：

```text
1. 修改文件。
2. 是否新增 env 配置。
3. 25B 自动触发顺序。
4. 09 成功后 runner 如何取得 symbol/base_interval/higher_interval/kline_slot_utc。
5. 当 STRATEGY_PIPELINE_SCHEDULER_ENABLED=true 时，旧 17 自动链路是否被跳过。
6. 如果旧 17 开关和 25 pipeline 开关同时为 true，实际谁生效。
7. 自动模式是否默认真实调用模型；正确答案应为不会。
8. 自动模式是否默认真实发送 Hermes；正确答案应为不会。
9. pipeline 失败如何记录和提醒。
10. 如何避免重复调度。
11. 测试结果。
12. 遗留问题。
```

---

## 15. 验收标准

25B 通过标准：

```text
1. scheduler runner 可以在 09 采集成功后自动触发 25 pipeline。
2. runner 不再直接触发旧 17 主链路。
3. 同一根 K线不会并发跑多个 pipeline。
4. 自动 pipeline 能拿到明确 kline_slot_utc，不靠当前时间猜。
5. 25 内部仍按 25A 已验证顺序执行。
6. 默认不真实调用大模型。
7. 默认不真实发送 Hermes。
8. 失败可追踪、可告警、不静默。
9. 不破坏非交易边界。
10. 不新增自动交易能力。
```
