# 23C_support_resistance_strategy.md

## 1. 阶段名称

第 23C 阶段：`support_resistance_strategy`

中文名称：支撑压力与关键价格区域识别。

---

## 2. 阶段目标

23C 是 23B 之后的关键价格位置层。

本阶段目标不是生成最终交易建议，而是让系统稳定回答：

```text
1. 当前价格下方有哪些重要支撑区域？
2. 当前价格上方有哪些重要压力区域？
3. 哪些价格区域是近期有效的？
4. 哪些价格区域只是历史参考？
5. 哪些区域可能发生支撑压力转换？
```

23C 输出的是价格地图，不是交易指令。

---

## 3. 核心定位

23C 只负责：

```text
识别支撑
识别压力
识别区间边界
识别历史参考位
识别可能的支撑压力转换区域
```

23C 为后续模块提供价格基础：

```text
23D：突破 / 回踩 / 跌破触发确认
23E：波动率与风控否决
23F：角色化聚合增强
最终建议层：组合 trade_setup
```

23C 不负责判断是否突破成立，不负责入场，不负责止损止盈。

---

## 4. 方法来源与采用范围

23C 应借鉴成熟交易方法，但只采用与“关键价格区域识别”相关的部分。

### 4.1 method_basis

```text
1. Price Action / 价格行为
2. Swing High / Swing Low / 摆动高低点
3. Previous High / Previous Low / 前高前低
4. Range Boundary / 箱体上下沿
5. Donchian Channel / 唐奇安 N 周期高低点思想
6. Multi-timeframe Confluence / 多周期共振
7. ATR 或百分比区间宽度思想
```

### 4.2 used_parts

23C 本阶段实际采用：

```text
1. 从 4h / 1d K线中识别 swing high / swing low。
2. 提取前高、前低、近期区间高低点。
3. 将相近价格点聚类成 zone，而不是输出单点。
4. 按触碰次数、反应幅度、时间新旧、多周期共振、距离当前价格、区间宽度评分。
5. 输出 nearest / major / range_boundary / historical_reference 等分层结果。
6. 标记可能的 role flip 区域。
```

### 4.3 excluded_parts

23C 本阶段明确不采用：

```text
1. 完整价格行为交易系统。
2. 主观 K 线形态交易，例如 pin bar、engulfing 等。
3. 完整威科夫分析。
4. 成交量分布 / Volume Profile。
5. 盘口订单流。
6. 流动性清理 / 扫损策略。
7. 江恩角度。
8. 斐波那契回撤与扩展。
9. 突破确认。
10. 回踩确认。
11. 入场、止损、止盈、盈亏比。
```

---

## 5. 策略角色与能力声明

23C 必须沿用 0005 决策文档中的“策略角色协作 / 策略接力”架构。

### 5.1 strategy_role

```text
strategy_role = support_resistance
```

### 5.2 provides

配置中必须声明：

```yaml
provides:
  - key_levels
  - support_zones
  - resistance_zones
  - range_boundaries
  - invalidation_reference_zones
  - target_observation_zones
  - role_flip_candidates
```

注意：

```text
invalidation_reference_zones 不是最终止损价。
target_observation_zones 不是最终止盈价。
```

它们只是后续模块的价格参考。

---

## 6. 新增策略模块

建议新增：

```text
app/strategy/strategies/support_resistance_strategy.py
configs/strategies/support_resistance_strategy.yaml
```

中文名称：支撑压力策略。

职责：

```text
从 4h / 1d K线窗口中识别当前仍有解释力的关键价格区域。
```

---

## 7. 输入数据边界

23C 只允许使用主框架传入的统一输入。

允许：

```text
MarketContextSnapshot
StrategyEvaluationInput
4h K线窗口
1d K线窗口
snapshot_id
symbol
base_interval
higher_interval
```

禁止：

```text
策略内部自行查询数据库
策略内部自行查询 MarketContextSnapshot
请求 Binance REST
请求 WebSocket
读取账户
读取持仓
调用 Hermes
调用大模型
调用交易接口
```

快照最新性、合格性、懒生成逻辑由 15 / 16 主框架负责，不得下放到 23C 策略内部。

---

## 8. 核心算法流程

23C 初版采用简单、可解释、可复盘的规则。

### 8.1 识别候选价格点

从 4h / 1d K线窗口中识别：

```text
swing_high
swing_low
recent_high
recent_low
previous_high
previous_low
range_high
range_low
```

Swing 点识别可采用：

```text
当前高点高于左侧 N 根和右侧 N 根 → swing_high
当前低点低于左侧 N 根和右侧 N 根 → swing_low
```

同时需要最小波动过滤：

```text
过小波动不形成有效 swing 点。
```

### 8.2 历史点不直接丢弃

500 根 4h K线中的早期 swing 点仍可进入候选池。

但它们不得与近期结构同权重。

历史点需要按以下因素降权或保留：

```text
1. 是否后续被再次测试。
2. 是否触碰后出现明显反应。
3. 是否与后续多个 swing 点接近。
4. 是否与 1d 级别关键点共振。
5. 距离当前价格是否仍有意义。
6. 时间是否过于久远。
```

### 8.3 聚类成价格区间

支撑压力必须输出 zone，不应只输出单点。

例如：

```text
59950
60120
60200
59880
```

应合并为：

```text
support_zone = 59880 - 60200
```

聚类宽度可基于：

```text
固定百分比
ATR
当前价格百分比
配置阈值
```

本阶段优先使用配置化百分比或简化 ATR，避免过度复杂。

### 8.4 区域评分

每个 zone 至少计算：

```text
strength_score
confidence_score
current_relevance_score
touch_count
reaction_strength
timeframe_weight
recency_score
cluster_density
distance_from_current_price_pct
zone_width_pct
zone_quality
```

评分依据：

```text
1. 触碰次数越多，重要性越高。
2. 触碰后反应越强，重要性越高。
3. 越近期有效，当前相关性越高。
4. 1d 与 4h 共振，重要性更高。
5. 离当前价格过远，只保留为 historical_reference。
6. zone 过宽，质量下降。
7. 孤立极端插针不得直接成为强支撑或强压力。
```

### 8.5 分层输出

不要把所有 level 混在一个无序数组里。

至少区分：

```text
nearest_support
nearest_resistance
major_support
major_resistance
range_boundaries
historical_reference
role_flip_candidates
```

### 8.6 role flip 识别

23C 可以标记潜在支撑压力转换，但不确认交易触发。

枚举：

```text
none
resistance_to_support
support_to_resistance
unconfirmed
```

示例：

```text
原压力区被价格上穿后，后续价格回踩该区域。
23C 可以标记 resistance_to_support_candidate。
```

但确认是否有效回踩属于 23D。

---

## 9. 输出字段设计

23C 必须遵守 23A 三段结构：

```text
common_result
strategy_model_material_json
strategy_payload_json
```

### 9.1 common_result

`common_result` 可以放角色化公共证据。

建议包含：

```text
risk_level
signal_strength
confidence_score
reason_codes
reason_text
evidence_items
key_levels
not_trading_advice = true
```

其中 `key_levels` 可以包含公开可复用的关键价格区域摘要。

建议结构：

```json
{
  "key_levels": [
    {
      "level_id": "SR-4H-SUPPORT-001",
      "level_type": "support",
      "level_group": "nearest_support",
      "zone_low": "59880",
      "zone_high": "60200",
      "zone_mid": "60040",
      "timeframe": "4h",
      "strength_score": "0.72",
      "confidence_score": "0.68",
      "current_relevance_score": "0.81",
      "touch_count": 4,
      "distance_from_current_price_pct": "1.2",
      "role_flip_status": "none",
      "zone_quality": "clear",
      "reason": "多个 4h swing low 聚集，最近触碰后出现明显反弹。"
    }
  ]
}
```

### 9.2 strategy_payload_json

私有计算细节必须放入 `strategy_payload_json`。

例如：

```text
raw_swing_points
merged_level_clusters
cluster_scoring_details
reaction_strength_details
recency_score_details
role_flip_detection_details
zone_width_config
calculation_params
excluded_outliers
```

禁止把这些私有细节塞进 `common_result`。

### 9.3 strategy_model_material_json

放后续模型层可读摘要。

例如：

```text
最近支撑压力摘要
关键价格区域解释
主要证据
反方证据
不确定性
需要模型重点审查的问题
```

---

## 10. key_levels 类型定义

### 10.1 level_type

```text
support
resistance
range_boundary
invalidation_reference
target_observation
historical_reference
```

说明：

```text
support：支撑区域
resistance：压力区域
range_boundary：当前区间边界
invalidation_reference：失效参考区域，不是最终止损
target_observation：目标观察区域，不是最终止盈
historical_reference：历史参考区域
```

### 10.2 level_group

```text
nearest_support
nearest_resistance
major_support
major_resistance
range_upper_boundary
range_lower_boundary
historical_reference
role_flip_candidate
```

### 10.3 zone_quality

```text
clear
weak
wide
narrow
noisy
outlier
insufficient_data
unknown
```

### 10.4 role_flip_status

```text
none
resistance_to_support
support_to_resistance
unconfirmed
```

---

## 11. 配置要求

配置文件建议：

```text
configs/strategies/support_resistance_strategy.yaml
```

至少包含：

```yaml
enabled: true
strategy_name: support_resistance_strategy
strategy_version: "23C-1"
strategy_role: support_resistance
provides:
  - key_levels
  - support_zones
  - resistance_zones
  - range_boundaries
  - invalidation_reference_zones
  - target_observation_zones
  - role_flip_candidates
base_interval: 4h
higher_interval: 1d
lookback_bars:
  base: 180
  higher: 365
minimum_required_bars:
  base: 80
  higher: 120
thresholds:
  swing_left_bars: 2
  swing_right_bars: 2
  min_swing_move_pct: "0.004"
  cluster_width_pct: "0.006"
  max_zone_width_pct: "0.025"
  nearest_distance_pct: "0.08"
  major_level_min_strength: "0.60"
  outlier_reaction_min_pct: "0.01"
output_limits:
  nearest_support: 3
  nearest_resistance: 3
  major_support: 5
  major_resistance: 5
  historical_reference: 5
```

配置不得写死在业务代码中。

---

## 12. 与 23B 的关系

23B 输出：

```text
市场大方向
短期运行区间
当前阶段
```

23C 输出：

```text
正式关键价格区域
支撑
压力
区间边界
历史参考
```

23C 不应硬依赖 23B 的私有字段。

23C 应与 23B 一样，从主框架传入的同一份 `StrategyEvaluationInput / ContextView` 中读取 K线窗口并独立计算。

如果未来聚合层需要组合 23B 与 23C，应在 23F 处理，不在 23C 内部处理。

---

## 13. 与 18 链路关系

23C 策略必须能通过 16 阶段独立运行并落库。

必须保证：

```text
1. strategy_role = support_resistance 能正确落库。
2. provides 能正确读取。
3. common_payload_json 能保存 key_levels 摘要。
4. strategy_payload_json 能保存私有计算细节。
5. 当前 18 链路读取 23C 结果不崩溃。
```

本阶段不做 18 的深度聚合增强。

23F 再处理：

```text
support_resistance_summary
role_coverage_matrix
evidence_missing
key_level_conflict
多策略支撑压力融合
```

---

## 14. 本阶段不做

23C 明确不做：

```text
不做最终 advice
不做 trade_setup
不做入场价
不做止损价
不做止盈价
不做盈亏比
不做突破确认
不做跌破确认
不做回踩确认
不做风控否决
不做江恩
不做斐波那契
不做流动性清理
不做订单流
不调用大模型
不发送 Hermes
不请求 Binance
不读取账户
不读取持仓
不做自动交易
不做人工执行反馈
不做完整复盘系统
不做 18 深度聚合重构
```

---

## 15. 数据库与迁移

原则上不新增数据库表。

优先复用 23A 已有字段：

```text
strategy_role
common_payload_json
strategy_model_material_json
strategy_payload_json
validation_status
validation_errors_json
```

如 Codex 认为必须新增字段或 migration，必须先汇报，不得擅自新增。

---

## 16. 测试要求

至少新增或更新测试：

```text
1. SupportResistanceStrategy 输出 support_resistance 角色 StrategyResult。
2. 配置声明 strategy_role = support_resistance。
3. 配置声明 provides。
4. common_result 中存在 key_levels 摘要。
5. strategy_payload_json 中存在 raw_swing_points / merged_level_clusters 等私有细节。
6. 私有细节不进入 common_result。
7. 能识别 nearest_support / nearest_resistance。
8. 能识别 major_support / major_resistance。
9. 能识别 range_boundaries。
10. 能标记 role_flip_candidates。
11. zone_width_pct 过宽时降低 zone_quality 或降权。
12. 孤立插针不应直接成为高强度关键位。
13. 历史远点可以保留为 historical_reference，但 current_relevance_score 应降低。
14. 数据不足时输出 insufficient_data，不得抛异常。
15. 策略关闭后 runner 不报错。
16. 单个策略失败不影响其他策略运行。
17. run_strategy_signals 能正常落库 23C 结果。
18. 18 链路读取 23C 结果不崩溃。
```

建议运行：

```bash
python -m pytest tests/strategy -q
python -m pytest tests/strategy_aggregation -q
```

如时间允许，再运行：

```bash
python -m pytest tests -q
```

---

## 17. 验收标准

23C 完成后，系统应能回答：

```text
1. 当前价格下方最近支撑在哪里？
2. 当前价格上方最近压力在哪里？
3. 哪些是大级别主要支撑？
4. 哪些是大级别主要压力？
5. 哪些是当前区间边界？
6. 哪些只是历史参考位？
7. 哪些区域可能发生支撑压力转换？
8. 每个价格区域为什么重要？
9. 每个价格区域现在还有多大相关性？
10. 这是否只是价格地图，而不是交易建议？
```

验收通过条件：

```text
1. SupportResistanceStrategy 已实现。
2. strategy_role = support_resistance。
3. provides 正确。
4. common_result / strategy_payload_json 边界清楚。
5. common_result 只保存 key_levels 摘要，不保存私有计算细节。
6. strategy_payload_json 保存 swing / cluster / scoring 私有细节。
7. 不生成最终 advice。
8. 不生成 trade_setup。
9. 不输出 entry / stop_loss / take_profit。
10. 不发送 Hermes。
11. 不调用大模型。
12. 不请求 Binance。
13. 不读取账户或持仓。
14. 不新增自动交易语义。
15. 原则上不新增数据库迁移。
16. tests/strategy 通过。
17. tests/strategy_aggregation 通过或说明当前无对应测试范围。
```

---

## 18. 后续阶段建议

23C 完成后，建议进入：

```text
23D：突破 / 跌破 / 回踩确认策略
23E：波动率与风控否决策略
23F：角色化聚合层增强
```

23C 的核心不是找出所有历史价格线，而是筛出：

```text
当前仍有解释力的关键价格区域
```
