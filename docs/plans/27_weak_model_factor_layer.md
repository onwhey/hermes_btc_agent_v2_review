# 27 弱模型 / 因子层计划

## 1. 阶段定位

27 阶段用于把原来 18 中偏“数学材料 / 因子判断”的部分，上游化为独立的弱模型层。

27 不是替代 18。

正确边界是：

```text
15 市场上下文快照
→ 16/23 策略层
→ 27 弱模型 / 因子层
→ 18 材料包组装层
→ 19/20 大模型审查
→ 21 最终建议
```

其中：

```text
27 负责计算、落库、聚合弱模型结果；
18 负责读取策略证据、26B 质量结果、27 弱模型摘要，并组装 material pack；
19/20 负责审查；
21 负责最终建议展示。
```

一句话：

```text
27 是 18 的上游增强，不是 18 的替代品。
```

---

## 2. 为什么要做 27

原来的 18 数学材料适合早期快速把结构信息喂给大模型，但它存在问题：

```text
1. 数学判断和材料组装混在一起
2. 每个数学依据是否有效无法独立复盘
3. 后续无法清楚判断哪个因子有用、哪个因子是噪音
4. 权重、可信度、启用状态难以治理
5. 大模型容易看到一堆未经结构化的材料
```

27 的目标是把这些松散数学材料变成：

```text
可运行
可配置
可落库
可追踪
可复盘
可降权
可禁用
```

的弱模型 / 因子证据单元。

---

## 3. 核心原则

### 3.1 第一版是规则型弱模型，不是机器学习模型

27 第一版不做训练、拟合、参数优化。

第一版只做：

```text
规则型弱模型 / 因子模型
```

例如：

```text
趋势强弱
波动率风险
成交量确认
动量延续
支撑压力距离
多周期一致性
假突破风险
盈亏比可行性
```

### 3.2 前期不追求权重绝对正确

27 前期核心不是找到最优权重，而是建立完整数据底座。

必须记录：

```text
初始输入
弱模型输出
聚合过程
后续市场结果
复盘判断
权重调整历史
```

后期大模型复盘可以基于这些数据，给出更客观的权重、可信度、降权或禁用建议。

但大模型不得静默修改配置。  
任何权重或启用状态调整，必须经过人工确认，并记录调整原因。

### 3.3 不直接生成最终交易建议

27 不能输出：

```text
开多
开空
止损
止盈
仓位
杠杆
```

27 只能输出：

```text
方向支持
方向反对
风险升高
确认不足
背景状态
因子冲突
```

最终建议仍归 21。

### 3.4 不调用大模型

27 不调用 DeepSeek / GPT / Claude。

### 3.5 不请求 Binance

27 不直接请求 Binance REST。  
27 不自己扫描最新 K线。  
27 不读取交易所账户或真实仓位。

---

## 4. 输入来源：必须使用 15 快照

27 弱模型必须基于 15 的 market_context_snapshot。

主链路规则：

```text
25 pipeline
→ 16 调用 15，确保生成或复用最新合格 snapshot
→ 16 生成 SSR，并记录 snapshot_id
→ 27 读取 SSR 绑定的 snapshot_id
→ 27 基于同一份 snapshot 计算弱模型
```

27 不自己判断“最新快照”。

### 4.1 主链路取快照规则

27 输入必须包含：

```text
pipeline_run_id
strategy_signal_run_id
```

27 根据 `strategy_signal_run_id` 查询 SSR，再取得：

```text
market_context_snapshot_id / snapshot_id
```

然后校验：

```text
symbol 匹配
base_interval 匹配
higher_interval 匹配
kline_slot_utc 匹配
snapshot status 正常
snapshot 使用已收盘 K线
```

校验失败时：

```text
27 blocked
error_code=invalid_or_missing_snapshot
不允许静默换快照
不允许自行重新生成快照
```

### 4.2 独立 CLI 模式

27 后续可提供独立 CLI。

CLI 允许两种模式：

```text
模式一：传 strategy_signal_run_id
→ 使用 SSR 绑定的 snapshot

模式二：传 kline_slot_utc
→ 可调用 15 ensure snapshot
→ 但必须记录 snapshot_id 和生成/复用动作
```

主链路只能使用模式一。

---

## 5. 弱模型角色分类

27 不采用“所有模型共用一个输出字段”的设计。

采用：

```text
公共字段 + 按 model_role 区分配置和输出契约
```

模型角色第一版分为：

```text
directional      方向型
risk             风险型
confirmation     确认型
context          背景型
```

---

## 6. 公共配置字段

每个 weak model profile 必须包含：

```text
model_key
model_name
enabled
maturity_stage
model_role
model_version
config_version
config_hash
input_intervals
input_window
static_weight
description
params
```

### 6.1 enabled

```text
enabled=true  启用
enabled=false 禁用
```

关闭模型不参与运行，不参与聚合。

### 6.2 maturity_stage

建议取值：

```text
experimental
observe_only
active
deprecated
disabled
```

规则：

```text
experimental / observe_only 默认只落库观察，不参与正式聚合
active 可参与正式聚合
disabled 不运行
deprecated 保留历史，不再运行
```

### 6.3 static_weight

`static_weight` 是长期静态权重，范围：

```text
0.0 ~ 1.0
```

第一版规则：

```text
单个弱模型 static_weight 不超过 0.30
新模型默认 static_weight <= 0.10
observe_only 模型 static_weight = 0
```

---

## 7. 角色配置和输出契约

### 7.1 directional 方向型

用于表达偏多 / 偏空 / 中性。

输出字段：

```text
signal_score: -1.0 ~ 1.0
confidence: 0.0 ~ 1.0
direction_bias: bullish / bearish / neutral / mixed
effective_score
evidence_json
```

建议离散分值：

```text
+0.75 强偏多
+0.50 偏多
+0.25 弱偏多
 0.00 中性
-0.25 弱偏空
-0.50 偏空
-0.75 强偏空
```

第一版不轻易输出 `+1.0` 或 `-1.0`。

计算：

```text
effective_score = signal_score × confidence × static_weight
```

### 7.2 risk 风险型

风险型不强行转换成多空方向。

输出字段：

```text
risk_score: 0.0 ~ 1.0
risk_level: low / medium / high / extreme
can_veto: true / false
veto_triggered: true / false
trade_permission: allow / caution / block
confidence: 0.0 ~ 1.0
evidence_json
```

风险分级建议：

```text
risk_score < 0.35        low
0.35 <= risk_score < 0.60 medium
0.60 <= risk_score < 0.80 high
risk_score >= 0.80       extreme
```

如果：

```text
can_veto=true
且 risk_score >= 0.80
```

则：

```text
trade_permission=block
```

注意：

```text
风险高不是看空；
风险高表示不适合交易或需要降级处理。
```

### 7.3 confirmation 确认型

用于确认某个方向是否被支持。

输出字段：

```text
supports_direction: long / short / neutral / none
confirmation_score: 0.0 ~ 1.0
confidence: 0.0 ~ 1.0
evidence_json
```

确认型不直接改变方向分数。  
它只标记：

```text
supporting_confirmations
opposing_confirmations
missing_confirmations
```

### 7.4 context 背景型

用于描述市场环境，不直接投票方向。

输出字段：

```text
regime: trend / range / high_volatility / low_volatility / transition / unknown
context_score: 0.0 ~ 1.0
confidence: 0.0 ~ 1.0
evidence_json
```

---

## 8. 初始可信度规则

`confidence` 范围：

```text
0.0 ~ 1.0
```

第一版建议：

```text
0.30 数据不足 / 信号很弱
0.50 普通有效
0.70 较强
0.80 很强
```

第一版不输出 `0.95 / 1.0`。

可信度可由以下因素决定：

```text
数据是否充足
信号是否清晰
是否和高周期冲突
是否靠近关键支撑压力
当前波动率是否异常
最近是否频繁假突破
```

---

## 9. 聚合规则

27 聚合不能把所有模型简单加权平均。

必须按角色分开：

```text
方向模型决定偏多 / 偏空 / 中性
风险模型决定 allow / caution / block
确认模型判断方向是否被支持
背景模型描述当前市场环境
```

### 9.1 方向聚合

方向型模型聚合：

```text
directional_score =
sum(signal_score × confidence × static_weight)
/
sum(confidence × static_weight)
```

若分母为 0：

```text
directional_score = 0
directional_bias = neutral
```

阈值建议：

```text
directional_score >= 0.35  bullish
directional_score <= -0.35 bearish
其他                         neutral / mixed
```

### 9.2 风险聚合

风险型模型不进入方向分数。

规则：

```text
任一 can_veto=true 且 veto_triggered=true
→ final_weak_model_permission=block

无 veto，但最高 risk_level=high
→ final_weak_model_permission=caution

否则
→ final_weak_model_permission=allow
```

### 9.3 确认聚合

确认型模型输出：

```text
supporting_confirmations
opposing_confirmations
missing_confirmations
```

第一版不把确认型模型强行拉进方向分数。

### 9.4 最终弱模型摘要

输出：

```text
directional_bias
directional_score
directional_confidence
risk_level
trade_permission
supporting_factors
opposing_factors
conflict_factors
low_confidence_factors
veto_factors
context_regime
summary_text
```

---

## 10. 建议首批弱模型

第一版最多 5~8 个，不做太多。

建议首批：

```text
trend_strength_directional
momentum_continuation_directional
multi_timeframe_alignment_directional
volatility_risk_gate
support_distance_confirmation
volume_confirmation
fake_breakout_risk_gate
market_regime_context
```

如果开发量过大，27A 只做前 4 个：

```text
trend_strength_directional
volatility_risk_gate
support_distance_confirmation
market_regime_context
```

剩余放 27B。

---

## 11. 数据库设计建议

### 11.1 weak_model_run

记录一次弱模型运行批次。

字段建议：

```text
id
weak_model_run_id
pipeline_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
kline_slot_utc
run_status
trigger_source
model_count_total
model_count_enabled
model_count_executed
model_count_failed
created_at_utc
updated_at_utc
details_json
```

### 11.2 weak_model_result

记录每个弱模型的单次结果。

字段建议：

```text
id
weak_model_result_id
weak_model_run_id
model_key
model_role
model_version
config_version
config_hash
maturity_stage
enabled
participation_mode

symbol
base_interval
higher_interval
kline_slot_utc
snapshot_id

status
error_code
error_message

signal_score
direction_bias
risk_score
risk_level
trade_permission
veto_triggered
confirmation_score
supports_direction
context_regime
context_score
confidence
static_weight
effective_score

input_summary_json
evidence_json
raw_output_json

created_at_utc
```

### 11.3 weak_model_aggregation

记录弱模型聚合摘要。

字段建议：

```text
id
weak_model_aggregation_id
weak_model_run_id
pipeline_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
kline_slot_utc

directional_score
directional_bias
directional_confidence
risk_level
trade_permission
veto_triggered
supporting_factors_json
opposing_factors_json
conflict_factors_json
low_confidence_factors_json
context_summary_json
summary_text

created_at_utc
details_json
```

### 11.4 weak_model_adjustment_log 后续预留

用于后期记录权重和配置调整。

字段建议：

```text
adjustment_id
model_key
old_config_hash
new_config_hash
old_value_json
new_value_json
reason
review_id
suggested_by
approved_by
applied_by
applied_at_utc
```

27A 可暂不实现完整 adjustment_log，但 plan 中必须预留。

---

## 12. 配置文件设计

建议配置目录：

```text
configs/weak_models/
```

建议文件：

```text
configs/weak_models/registry.yaml
configs/weak_models/trend_strength_directional.yaml
configs/weak_models/volatility_risk_gate.yaml
configs/weak_models/support_distance_confirmation.yaml
configs/weak_models/market_regime_context.yaml
```

示例：

```yaml
model_key: trend_strength_directional
model_name: 趋势强弱方向弱模型
enabled: true
maturity_stage: active
model_role: directional
model_version: v1
config_version: 2026-06-01
static_weight: 0.25
input_intervals: ["4h", "1d"]
input_window:
  base_interval_limit: 180
  higher_interval_limit: 365
params:
  ma_fast: 20
  ma_slow: 60
  slope_window: 10
```

---

## 13. 与 18 的关系和后续改造

27A 第一版可以先独立运行、落库、生成 weak_model_aggregation。

27B / 27C 再让 18 读取 weak_model_aggregation。

18 后续材料包结构应变成：

```text
23F 策略证据
+ 26B 质量结果
+ 27 weak_model_aggregation
+ 市场上下文摘要
→ material pack
```

大模型后续不应该直接看到大量弱模型原始结果。  
它应该看到：

```text
弱模型总体结论
主要支持因子
主要反对因子
主要冲突因子
风险否决因子
低置信度因子
```

---

## 14. 分阶段实现建议

### 27A：弱模型基础设施

做：

```text
BaseWeakModel
profile 加载
角色配置
结果类型
weak_model_run
weak_model_result
weak_model_aggregation
CLI 手动运行
基于 SSR snapshot_id 输入
首批 4 个弱模型
单元测试
```

不接 18。  
不接 pipeline 自动链路。  
不调用大模型。

### 27B：弱模型聚合增强

做：

```text
更多弱模型
聚合摘要优化
方向 / 风险 / 确认 / 背景分层输出
observe_only / active 规则
```

### 27C：18 材料包接入

做：

```text
18 读取 weak_model_aggregation
material_schema 增加 weak_model_summary
19/20 输入结构跟随调整
```

### 27D：21 展示接入

做：

```text
最终 advice / Hermes 展示弱模型摘要
展示支持因子、反对因子、风险因子
```

---

## 15. CLI 建议

27A 新增：

```bash
python -m scripts.run_weak_models \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --dry-run
```

写库：

```bash
python -m scripts.run_weak_models \
  --strategy-signal-run-id SSR-xxx \
  --trigger-source cli \
  --confirm-write
```

可选：

```bash
python -m scripts.run_weak_models \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --kline-slot-utc "2026-05-31T08:00:00Z" \
  --trigger-source cli \
  --confirm-write
```

但如果通过 slot 模式运行，必须明确使用 15 ensure snapshot，并记录 snapshot_id。

---

## 16. 测试要求

新增：

```text
tests/weak_models/
```

至少覆盖：

1. profile enabled=false 不运行。
2. observe_only 运行但不参与正式聚合。
3. active 模型参与聚合。
4. directional 输出 signal_score / confidence / effective_score。
5. risk 输出 risk_score / risk_level / trade_permission。
6. risk veto 触发 block。
7. confirmation 输出 supports_direction / confirmation_score。
8. context 输出 regime / context_score。
9. 方向聚合公式正确。
10. 风险模型不进入方向分数。
11. 确认模型不直接拉方向分数。
12. snapshot_id 缺失时 blocked。
13. snapshot 与 SSR slot 不匹配时 blocked。
14. 主链路模式不自行生成快照。
15. CLI dry-run 不写库。
16. confirm-write 才写库。
17. 结果记录 config_hash。
18. 每个 weak_model_result 记录 input_summary_json / evidence_json。
19. 不调用大模型。
20. 不发送 Hermes。
21. 不请求 Binance REST。
22. 重复运行按 run_id 幂等或生成新 run 的规则清晰。

回归测试：

```bash
python -m pytest tests/weak_models -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest -q
```

---

## 17. 验收标准

27A 验收通过条件：

```text
1. 配置可加载。
2. enabled=false 模型不运行。
3. observe_only 模型落库但不参与正式聚合。
4. active 模型参与聚合。
5. 方向 / 风险 / 确认 / 背景四类输出契约清晰。
6. 弱模型结果落库。
7. 弱模型聚合摘要落库。
8. 使用 16 SSR 绑定的 snapshot_id。
9. snapshot 校验失败时 blocked。
10. 不调用大模型。
11. 不发送 Hermes。
12. 不请求 Binance REST。
13. CLI dry-run / confirm-write 正常。
14. pytest 全通过。
```

---

## 18. 明确禁止

27 禁止：

```text
直接生成最终交易建议
自动交易
读取账户
读取真实仓位
请求 Binance REST
调用 DeepSeek / GPT / Claude
把风险模型硬转成看空
把所有模型强制输出同一个 signal_score
静默修改权重
静默调整 enabled
不记录 config_hash
不记录 snapshot_id
```

---

## 19. 后续复盘原则

后续 28 复盘可以由大模型参与，但必须基于 27 记录的数据：

```text
输入数据
模型输出
聚合过程
后续市场表现
复盘分析
人工确认的调整记录
```

大模型可以建议：

```text
调高权重
调低权重
禁用模型
转入 observe_only
修改参数
```

但不能自动落地。

最终规则：

```text
大模型给建议，人工确认，系统记录调整。
```
