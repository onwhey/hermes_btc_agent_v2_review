# 27B 弱模型输出质量审查与参数校准计划

## 1. 阶段定位

27A 已完成弱模型基础设施：

```text
配置加载
BaseWeakModel
四类 model_role
四个首批弱模型
weak_model_run/result/aggregation 落库
CLI dry-run / confirm-write
```

27B 的目标不是继续扩展大量新弱模型，而是先审查和校准 27A 已有输出。

一句话：

```text
27A 解决“能不能跑”；27B 解决“跑出来的数值是否可信、是否过猛、是否适合进入 18 材料包”。
```

---

## 2. 为什么需要 27B

当前 27A 已验证能写库，例如出现过：

```text
directional_bias=bearish
directional_score=-0.750000
risk_level=low
trade_permission=allow
```

这说明系统已经能产生弱模型结论，但也暴露出一个风险：

```text
初始弱模型的方向分数可能过强。
```

如果不先校准就接入 18，大模型可能被未经验证的弱模型摘要影响，导致后续 20/21 的判断被放大或偏置。

所以 27B 必须先做保守化校准。

---

## 3. 核心目标

27B 需要完成：

```text
1. 审查首批 4 个弱模型输出是否合理
2. 审查 directional_score 是否过度极端
3. 审查 confidence 是否过高
4. 审查 risk_gate 是否过松或过严
5. 审查 evidence_json 是否足够解释结果
6. 审查 context observe_only 是否只提供背景、不污染聚合
7. 建立第一版保守参数规则
8. 建立弱模型输出质量检查 CLI
```

---

## 4. 严格边界

27B 不做：

```text
不接入 18
不接入 19/20
不接入 21
不接入 scheduler
不调用大模型
不发送 Hermes
不请求 Binance REST
不读取账户
不读取仓位
不自动交易
不新增大量弱模型
不做完整复盘
不做胜率统计
不做权重自动优化
```

27B 只审查和校准 27A 弱模型输出。

---

## 5. 审查对象

首批审查对象：

```text
trend_strength_directional
volatility_risk_gate
support_distance_confirmation
market_regime_context
```

分别检查：

```text
directional 是否过强
risk 是否过松/过严
confirmation 是否解释清楚
context 是否只做背景
```

---

## 6. 输出质量检查规则

建议新增一个只读检查能力：

```text
weak_model_output_quality_check
```

它不改变原始 `weak_model_result`，只判断输出质量。

### 6.1 方向分数检查

方向型模型检查：

```text
signal_score 是否超过 ±0.75
directional_score 是否超过 ±0.75
是否频繁输出 strong bullish / strong bearish
是否 evidence_json 足以支撑强方向
```

第一版建议：

```text
若 directional_score 绝对值 >= 0.75，标记 warning
若 directional_score 绝对值 >= 0.90，标记 critical
```

但 27B 第一版只检查，不阻断主链路。

### 6.2 confidence 检查

检查：

```text
confidence 是否经常 >= 0.80
数据不足时是否仍给高 confidence
高周期冲突时是否降低 confidence
靠近支撑压力时是否降低 confidence
波动异常时是否降低 confidence
```

第一版建议：

```text
单模型 confidence 默认不超过 0.70
只有证据非常清晰时允许 0.80
不允许 0.95 / 1.00
```

### 6.3 risk_gate 检查

检查：

```text
volatility_risk_gate 是否识别高波动
risk_score 是否过低
risk_level 是否过松
veto_triggered 是否几乎不会触发
```

第一版建议：

```text
risk_score >= 0.60 应至少 high
risk_score >= 0.80 且 can_veto=true 应 block
```

如果市场波动明显异常但 risk_level=low，需要标记为可疑。

### 6.4 confirmation 检查

检查：

```text
confirmation 是否强行支持方向
support_distance_confirmation 是否区分靠近支撑/压力
confirmation_score 是否过高
supports_direction 是否有证据
```

确认型不应直接改变方向分数。

### 6.5 context 检查

检查：

```text
market_regime_context 是否写入 context_summary
source_maturity_stage 是否为 observe_only
是否没有污染 directional_score
是否没有影响 trade_permission
```

---

## 7. 参数保守化原则

27B 可以调整配置参数，但必须遵守：

```text
保守优先
不要追求漂亮分数
不要追求看起来聪明
不要输出过多强方向
```

建议第一版参数方向：

```text
directional signal_score 上限先压到 ±0.50 或 ±0.60
directional_score 超过 ±0.75 需要非常强证据
confidence 默认 0.50~0.70
observe_only 权重为 0
新模型 static_weight <= 0.10
单 active 模型 static_weight <= 0.30
```

如果 `trend_strength_directional` 当前容易输出 `-0.75 / +0.75`，27B 应优先考虑：

```text
1. 调低强方向阈值触发频率
2. 把强偏多/强偏空从 ±0.75 改为更少触发
3. 增加高周期冲突降置信度
4. 增加靠近关键支撑压力时降置信度
```

---

## 8. 建议新增表或记录方式

27B 第一版可以不新增复杂表。优先复用：

```text
weak_model_aggregation.details_json
weak_model_result.evidence_json
```

但建议新增轻量表：

```text
weak_model_quality_check
```

字段建议：

```text
id
quality_check_id
weak_model_run_id
weak_model_aggregation_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
kline_slot_utc

status
severity
issue_count
warning_count
critical_count
should_block_pipeline

issues_json
checked_models_json
summary_text

created_at_utc
details_json
```

第一版：

```text
should_block_pipeline=false
```

27B 不阻断主链路，只用于审查和校准。

---

## 9. 建议新增 CLI

新增只读检查：

```bash
python -m scripts.check_weak_model_output_quality \
  --weak-model-run-id WMR-xxx
```

也支持最近 N 条：

```bash
python -m scripts.check_weak_model_output_quality \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10
```

默认：

```text
只读
不写库
不发送 Hermes
```

如需要记录质量检查结果：

```bash
python -m scripts.check_weak_model_output_quality \
  --weak-model-run-id WMR-xxx \
  --confirm-write
```

---

## 10. 27B 是否要阻断

第一版不阻断。

原因：

```text
27A 弱模型尚未接入 18/20/21
27B 只是校准阶段
现在阻断意义不大
```

27B 只输出：

```text
passed
warning
critical
```

其中：

```text
critical 表示弱模型输出存在明显质量问题
但不影响主策略链路
```

后续如果 27C 接入 18，再讨论是否需要质量闸门。

---

## 11. 需要保留的数据

27B 必须保留：

```text
检查时的 weak_model_run_id
检查时的 aggregation_id
每个模型的原始输出
触发 warning/critical 的原因
建议调整方向
是否已人工确认调整
```

但 27B 不自动改配置。

---

## 12. 参数调整规则

如果 27B 后需要调整配置：

```text
必须改 configs/weak_models/*.yaml
必须更新 config_version
必须导致 config_hash 变化
必须在实现说明中记录 old_value / new_value / reason
```

不允许：

```text
代码里静默改权重
代码里硬编码临时阈值
不更新 config_version
不记录调整原因
```

---

## 13. 测试要求

新增或扩展：

```text
tests/weak_models/
```

至少覆盖：

```text
1. directional_score 过强时产生 warning
2. confidence 过高时产生 warning
3. risk_score 与 risk_level 不匹配时产生 warning
4. veto 条件存在但 veto_factors 缺失时产生 warning
5. context_summary 缺失时产生 warning
6. observe_only context 不影响 directional_score
7. quality check 默认不写库
8. confirm-write 才写入 quality check
9. quality check 不调用大模型
10. quality check 不发送 Hermes
11. quality check 不请求 Binance REST
12. 参数调整后 config_hash 变化
```

回归：

```bash
python -m pytest tests/weak_models -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_pipeline_observation -q
python -m pytest -q
```

---

## 14. 验收标准

27B 验收通过条件：

```text
1. 能检查最近 N 条 weak_model_run
2. 能识别 directional_score 过强
3. 能识别 confidence 过高
4. 能识别 risk_gate 异常
5. 能识别 context_summary 缺失
6. 能输出清晰 issues_json
7. 默认只读不写库
8. confirm-write 才写质量检查结果
9. 不调用大模型
10. 不发送 Hermes
11. 不请求 Binance REST
12. pytest 全通过
```

---

## 15. 27B 完成后的下一步

27B 完成后再决定：

```text
27C：18 材料包接入 weak_model_aggregation
```

27C 前必须确认：

```text
弱模型输出不过猛
confidence 没有虚高
risk_gate 没有明显失真
context observe_only 行为清楚
evidence_json 足以解释结果
```

否则不要急着接入 18。
