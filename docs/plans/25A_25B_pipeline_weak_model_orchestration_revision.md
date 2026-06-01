# 25A / 25B 修订：Strategy Pipeline 接入 27A / 27B 弱模型编排计划

## 1. 阶段定位

本计划不是新增 27 功能，而是把已经完成的 27A / 27B / 27C 正式接入 25 阶段的自动策略链路。

当前状态：

```text
27A：弱模型运行，生成 WMR / WMA
27B：弱模型输出质量检查，生成 WMQC
27C：18 material pack 已能读取 weak_model_summary
```

当前缺口：

```text
25A 主 pipeline 尚未保证 18 前先运行 27A / 27B
25B scheduler runner 尚未明确验证会走新的 pipeline 顺序
```

因此本计划拆成两部分：

```text
25A-revision：主 pipeline 编排接入 27A / 27B
25B-revision：scheduler runner 兼容并验证新 pipeline
```

---

## 2. 总目标链路

最终自动链路必须变成：

```text
4h K线已收盘并入库
→ 16 / 23 策略信号 SSR
→ 27A 弱模型运行 WMR / WMA
→ 27B 弱模型质量检查 WMQC
→ 18 材料包 AMP，读取 weak_model_summary
→ 20 判断调用 / 复用 19
→ 19 大模型审查
→ 21 最终 advice / Hermes
```

禁止继续出现：

```text
16 SSR
→ 18 AMP
→ 后补 27A / 27B
```

否则会继续生成：

```text
weak_model_summary.status = missing / unchecked
```

且 18 的幂等机制不会自动覆盖旧 AMP。

---

# 25A-revision：主 Pipeline 编排接入 27A / 27B

## 3. 25A 修改范围

25A 负责修改 `run_strategy_pipeline` 或其底层 pipeline service。

目标：

```text
在 18 material pack 生成之前，自动运行或复用 27A / 27B。
```

25A 不做：

```text
不新增弱模型
不调整弱模型参数
不修改 27C material schema
不新增 scoring_contract
不修改大模型 prompt
不改 21 Hermes 展示
不请求 Binance REST
不读取账户 / 仓位
不自动交易
不绕过 19 / 20 / 21 原有边界
```

---

## 4. 25A 新编排顺序

主流程应改为：

```text
1. 获取 / 生成 SSR
2. SSR 可用后，进入 27A
3. 27A 成功后，进入 27B
4. 27B passed / warning 后，进入 18
5. 18 读取 weak_model_summary
6. 后续继续 20 / 19 / 21
```

---

## 5. 27A 编排规则

当 SSR 生成后：

```text
1. 查询该 SSR 是否已有最新 success WMR / WMA
2. 如果已有可复用 WMR / WMA，优先复用
3. 如果没有，则调用 27A service 生成 WMR / WMA
4. 27A 必须在 18 之前完成
5. 18 不允许隐式运行 27A
```

27A 进入 18 前必须满足：

```text
weak_model_run.run_status = success
weak_model_aggregation 存在
snapshot_id 与 SSR 匹配
symbol / base_interval / higher_interval / kline_slot 匹配
```

如不满足：

```text
pipeline_status = blocked
current_step = 27a_weak_model_run
error_code = weak_model_run_failed
```

---

## 6. 27B 编排规则

27A 成功后：

```text
1. 查询该 WMR 是否已有最新 WMQC
2. 如果已有可复用 WMQC，优先复用
3. 如果没有，则调用 27B service 生成 WMQC
4. 27B 必须在 18 之前完成
5. 18 不允许隐式运行 27B
```

自动 pipeline 不允许在没有 WMQC 的情况下继续生成 AMP。

---

## 7. 27B 质量状态处理

自动 pipeline 中，质量状态处理如下：

```text
27B passed
→ 继续进入 18
→ weak_model_summary.status = available

27B warning
→ 继续进入 18
→ weak_model_summary.status = warning
→ material pack 必须保留 warning 信息

27B critical
→ 阻断自动 pipeline
→ 不进入 18
→ 不生成 AMP
→ error_code = weak_model_quality_critical

27B 执行失败
→ 阻断自动 pipeline
→ 不进入 18
→ error_code = weak_model_quality_check_failed

没有 WMR / WMA / WMQC
→ 阻断自动 pipeline
→ 不生成 AMP
```

说明：

```text
27C 仍保留 missing / unchecked / excluded_by_quality_check 的兼容能力；
但 25A 自动链路应更严格，避免自动生成低质量 AMP。
```

---

## 8. 25A 幂等规则

重复运行同一个 slot / SSR 时：

```text
1. 已有 success WMR / WMA，则复用
2. 已有 WMQC，则复用
3. 已有 AMP，则复用或 skipped，不重复生成
4. 不重复刷出无意义 WMR / WMQC
5. 不因 WMR / WMA / WMQC 流水 ID 变化误触发 20 模型重审
```

如果手动 retry 需要生成新 WMR / WMQC，必须保留：

```text
trigger_source
trace_id
pipeline_run_id
retry 标记或可追踪事件
```

---

## 9. 25A 配置开关

建议新增或复用：

```text
STRATEGY_PIPELINE_WEAK_MODELS_ENABLED=true
STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED=true
```

语义：

```text
STRATEGY_PIPELINE_WEAK_MODELS_ENABLED=false
→ pipeline 不运行 27A
→ 自动链路不应默认继续进入 18，除非显式测试降级模式允许

STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED=false
→ pipeline 不运行 27B
→ 自动链路不应默认继续进入 18，除非显式测试降级模式允许
```

生产建议：

```text
两个开关都必须为 true。
```

若配置关闭，CLI / scheduler 日志必须明确输出关闭原因。

---

## 10. 25A CLI 输出要求

`run_strategy_pipeline` 输出中应增加：

```text
weak_model_run_id
weak_model_aggregation_id
weak_model_quality_check_id
weak_model_status
weak_model_quality_status
weak_model_directional_score
weak_model_risk_level
weak_model_trade_permission
weak_model_pipeline_action = created / reused / skipped / blocked
weak_model_quality_pipeline_action = created / reused / skipped / blocked
```

输出必须能回答：

```text
本轮是否运行 27A
本轮是否复用 27A
本轮是否运行 27B
本轮是否复用 27B
是否允许 18 使用弱模型摘要
如阻断，阻断在哪一步
```

---

# 25B-revision：Scheduler Runner 兼容新 Pipeline

## 11. 25B 修改范围

25B 不应该重新实现 27A / 27B 逻辑。

正确结构：

```text
scheduler runner
→ 调用新的 run_strategy_pipeline / pipeline service
→ 自动继承 27A / 27B / 18 顺序
```

25B 需要确认：

```text
1. scheduler 没有绕过 run_strategy_pipeline
2. scheduler 没有自己拼旧链路：16 → 18 → 20 → 21
3. scheduler 自动触发时同样会先 27A / 27B，再 18
4. scheduler 日志能记录弱模型阶段状态
5. scheduler 对 weak_model critical / failure 的 blocked 状态能正确记录
```

---

## 12. 25B 兼容要求

如果 scheduler 当前只是调用 `run_strategy_pipeline`：

```text
不需要重复实现弱模型逻辑
只需要补测试、日志字段、状态展示
```

如果 scheduler 当前绕过 pipeline service，直接编排：

```text
16 → 18 → 20 → 21
```

必须修改为调用统一 pipeline service。

---

## 13. 25B 日志与状态要求

scheduler runner / scheduler event log 应能看到：

```text
pipeline_run_id
kline_slot_utc
trigger_source = scheduler
current_step
pipeline_status
weak_model_run_id
weak_model_aggregation_id
weak_model_quality_check_id
weak_model_quality_status
blocked_reason
error_code
error_message
```

对于 27 相关阻断：

```text
weak_model_run_failed
weak_model_quality_check_failed
weak_model_quality_critical
weak_model_disabled_by_config
weak_model_quality_gate_disabled_by_config
```

必须能在日志里定位。

---

## 14. 25B 开关继承

scheduler runner 必须继承 25A 的配置开关：

```text
STRATEGY_PIPELINE_WEAK_MODELS_ENABLED
STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED
```

不能出现：

```text
CLI pipeline 开了 27A / 27B
scheduler 自动任务却绕过 27A / 27B
```

如果 scheduler 因配置关闭跳过弱模型，必须记录：

```text
trigger_source=scheduler
status=blocked 或 skipped_by_config
reason=weak_model_disabled_by_config
```

是否允许降级进入 18，必须显式配置，不允许默认静默降级。

---

# 统一测试要求

## 15. 25A 测试

至少覆盖：

```text
1. run_strategy_pipeline 在 18 前自动运行 27A
2. 27A 后自动运行 27B
3. 27B passed 后生成 AMP，weak_model_summary.status=available
4. 27B warning 后生成 AMP，weak_model_summary.status=warning
5. 27B critical 时 pipeline blocked，不生成 AMP
6. 27A 失败时 pipeline blocked，不进入 18
7. 27B 失败时 pipeline blocked，不进入 18
8. 已有 WMR / WMA 时复用，不重复生成
9. 已有 WMQC 时复用，不重复生成
10. 自动链路不生成 unchecked AMP
```

## 16. 25B 测试

至少覆盖：

```text
1. scheduler runner 调用统一 pipeline service
2. scheduler 触发时自动走 27A → 27B → 18
3. scheduler 不重复实现 27A / 27B
4. scheduler 记录 weak_model_run_id / weak_model_quality_check_id
5. scheduler 遇到 27B critical 时记录 blocked
6. scheduler 遇到 27A / 27B failure 时记录 failed / blocked
7. scheduler 不生成 missing / unchecked AMP
```

## 17. 回归测试

```bash
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_pipeline_scheduler -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/weak_models -q
python -m pytest tests/model_review_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest -q
```

如果仓库没有 `tests/strategy_pipeline_scheduler`，则以现有 scheduler / pipeline runner 测试目录为准。

---

# 验收标准

## 18. 25A 验收

```text
1. 手动运行 run_strategy_pipeline 后，自动产生或复用 WMR / WMA
2. 自动产生或复用 WMQC
3. 18 AMP 中 weak_model_summary.status=available 或 warning
4. 自动链路不再产生 unchecked / missing 的弱模型材料包
5. 27B critical / 27A failure / 27B failure 会阻断 18
6. 重复运行不会刷出重复 WMR / WMQC / AMP
```

## 19. 25B 验收

```text
1. scheduler runner 触发时走同一套 pipeline service
2. scheduler 自动链路也包含 27A / 27B
3. scheduler 日志能看到弱模型阶段 ID 和状态
4. scheduler 不会绕过 27A / 27B 直接生成 18 AMP
5. scheduler 对 weak_model critical / failure 的 blocked 状态可追踪
```

---

## 20. 完成后的下一步

25A / 25B 修订完成后，再做：

```text
27D：21 / Hermes 展示弱模型摘要
28：弱模型、策略、大模型复盘
```
