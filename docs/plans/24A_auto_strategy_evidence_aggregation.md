# 24A_auto_strategy_evidence_aggregation.md

## 1. 阶段名称

第 24A 阶段：`auto_strategy_evidence_aggregation`

中文名称：策略运行后自动触发 23F 策略证据聚合。

---

## 2. 阶段目标

24A 目标：

```text
16 runner 跑完全部策略并成功落库后，可以根据配置自动调用 23F。
```

当前状态：

```text
16 runner 可以运行 23B/C/D/E 等策略
23F 可以通过 CLI 手动聚合
但 16 跑完后不会自动触发 23F
```

24A 完成后：

```text
16 runner
↓
策略结果落库
↓
自动触发 23F aggregation
↓
生成 strategy_evidence_aggregation_result
```

---

## 3. 核心边界

24A 只做自动接线，不改变策略本身。

允许做：

```text
1. 在策略运行成功后触发 23F service
2. 增加自动触发开关
3. 增加失败日志和 Hermes 告警
4. 保持 23F 幂等
5. 支持手动补跑
```

禁止做：

```text
1. 不改 23B/C/D/E 核心算法
2. 不改 23F 聚合语义，除非修接入 bug
3. 不调用大模型
4. 不改 advice
5. 不生成 trade_setup
6. 不发送最终策略建议
7. 不读取账户或持仓
8. 不自动交易
```

---

## 4. 配置开关

新增 env / config 开关：

```text
STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false
```

语义：

```text
false：16 只运行策略，不自动触发 23F
true：16 跑完策略后自动触发 23F
```

建议默认：

```text
false
```

原因：

```text
先保持生产安全，由用户确认后再开启自动聚合。
```

可选新增告警开关：

```text
STRATEGY_EVIDENCE_AGGREGATION_FAILURE_ALERT_ENABLED=true
```

如果不新增独立开关，也可以沿用系统告警总开关，但必须在代码和文档中说明。

---

## 5. 触发时机

建议触发时机：

```text
run_strategy_signals 生成 strategy_signal_run
↓
各策略 StrategyResult 写入 strategy_signal_result
↓
本轮策略运行完成
↓
如果 STRATEGY_EVIDENCE_AGGREGATION_ENABLED=true
↓
调用 StrategyEvidenceAggregationService
```

注意：

```text
23F 不作为普通 strategy 插入 runner 策略列表。
23F 是所有策略运行完成后的后置聚合步骤。
```

---

## 6. 输入参数

自动调用 23F 时必须传入：

```text
strategy_signal_run_id
trigger_source
trace_id
dry_run / confirm_write 状态
```

trigger_source 建议：

```text
cli：用户手动 run_strategy_signals 触发
scheduler：调度任务触发
systemd：系统服务触发
```

如果 run_strategy_signals 是 dry-run：

```text
不得写入 23F aggregation。
可以在输出中说明“dry-run 未触发写库聚合”。
```

如果 run_strategy_signals 是 confirm-write：

```text
允许自动写入 23F aggregation。
```

---

## 7. 幂等要求

23F 已有 `strategy_signal_run_id` 级幂等要求，24A 必须继续遵守。

规则：

```text
同一 strategy_signal_run_id 自动触发多次，不得插入多条有效 23F 结果。
```

可接受行为：

```text
1. 返回已有 aggregation_id
2. 更新同一条结果
3. 明确 blocked 并提示已有结果
```

但行为必须稳定，并有测试覆盖。

---

## 8. 失败处理

这是 24A 的硬规则。

如果 23F 自动聚合失败：

```text
1. 不回滚 23B/C/D/E 已落库策略结果
2. 必须记录错误日志
3. 必须记录可追踪事件或 alert_message
4. 必须通过 Hermes 明确通知用户
5. 不允许静默失败
```

### 8.1 Hermes 告警要求

告警类型建议：

```text
alert_type = strategy_evidence_aggregation_failed
severity = warning 或 error
source = strategy_evidence_aggregation
```

告警必须包含：

```text
strategy_signal_run_id
symbol
base_interval
higher_interval
trigger_source
error_code
error_message
trace_id
是否可手动补跑
建议补跑命令
not_trading_advice = true
```

### 8.2 Hermes 告警模板

固定模板示例：

```text
【BTC 策略证据聚合失败】

本轮策略结果已生成并落库，但 23F 策略证据聚合失败。

影响：
- 本轮策略证据链未完成
- 18 材料包可能无法使用最新 23F 聚合结果
- 可手动补跑 23F

strategy_signal_run_id: {strategy_signal_run_id}
symbol: {symbol}
interval: {base_interval}/{higher_interval}
trigger_source: {trigger_source}
error_code: {error_code}
trace_id: {trace_id}

建议：
请检查日志后手动执行 23F 聚合命令。
```

注意：

```text
该告警属于系统固定模板告警，不得调用大模型。
```

---

## 9. 手动补跑能力

24A 不取消现有 CLI。

失败后用户应能执行：

```bash
python -m scripts.run_strategy_evidence_aggregation \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

如果已经存在成功结果：

```text
CLI 应返回已有 aggregation_id 或幂等结果。
```

---

## 10. 代码位置建议

可能涉及：

```text
app/strategy/runner.py
app/strategy/aggregation/evidence_service.py
app/strategy/aggregation/evidence_repository.py
scripts/run_strategy_signals.py
app/core/config.py 或对应 settings
app/alerting/ 或 Hermes alert 相关模块
```

要求：

```text
scripts 只做参数解析和调用。
自动聚合核心逻辑放 app 层。
Hermes 告警使用固定模板。
```

---

## 11. dry-run / confirm-write 规则

### 11.1 dry-run

```text
不写 strategy_signal_result
不写 strategy_evidence_aggregation_result
不发送 Hermes 失败告警
可以打印将会触发 23F 的说明
```

### 11.2 confirm-write

```text
策略结果写库
如果开关打开，自动触发 23F 写库
23F 失败时发送 Hermes 告警
```

---

## 12. 测试要求

至少测试：

```text
1. STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false 时，runner 不自动调用 23F。
2. STRATEGY_EVIDENCE_AGGREGATION_ENABLED=true 时，runner 跑完策略后自动调用 23F。
3. dry-run 不写入 23F。
4. confirm-write 写入 23F。
5. 同一 strategy_signal_run_id 重复自动触发幂等。
6. 23F 失败时，不回滚 strategy_signal_result。
7. 23F 失败时，生成 Hermes 告警或 alert_message。
8. Hermes 告警使用固定模板，不调用大模型。
9. 23F 失败后 CLI 可手动补跑。
10. 23F 不作为普通 strategy 出现在 strategy registry 执行列表中。
```

建议运行：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```

---

## 13. 验收标准

24A 验收通过条件：

```text
1. 自动聚合开关存在且默认关闭。
2. 开关关闭时不影响原策略运行。
3. 开关开启时，策略运行完成后自动生成 23F aggregation。
4. 23F 自动失败不影响策略结果落库。
5. 23F 自动失败必须 Hermes 告警。
6. 告警内容包含 strategy_signal_run_id / error_code / trace_id / 补跑提示。
7. 手动补跑命令仍可用。
8. 重复触发幂等。
9. 不调用大模型。
10. 不生成最终 advice。
```
