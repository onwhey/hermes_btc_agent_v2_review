# 27D：最终 Advice / Hermes 展示弱模型摘要计划

## 1. 阶段定位

27D 负责把 27A / 27B / 27C 已经进入主链路的弱模型结果，展示到最终 advice 与 Hermes 通知中。

当前已完成：

```text
27A：弱模型运行，生成 WMR / WMA
27B：弱模型输出质量检查，生成 WMQC
27C：18 material pack 已能读取 weak_model_summary
25A/25B 修订：pipeline 在 18 前运行 / 复用 27A、27B
```

27D 要解决的问题：

```text
用户在最终策略通知里能看到弱模型给出的方向系数、风险状态、质量状态。
```

27D 不负责重新计算弱模型，不负责改变最终建议结论。

---

## 2. 核心目标

在 21 最终 advice / Hermes 通知中增加弱模型摘要区块：

```text
【弱模型摘要】
方向：偏空 / 偏多 / 中性
方向系数：-0.60
方向强度：0.60
置信度：0.079
风险等级：low
交易权限：allow
质量检查：passed
是否触发否决：false
说明：弱模型只是辅助证据，不是最终交易建议。
```

要求用户能清楚看到：

```text
弱模型怎么看
弱模型强度多大
弱模型质量是否通过
弱模型有没有触发风控否决
弱模型是否只是辅助证据
```

---

## 3. 数据来源

27D 不直接读取 27A / 27B 表作为主入口。

优先来源：

```text
18 analysis_material_pack.material_json.weak_model_summary
```

21 生成 advice / notification payload 时，从 18 / 20 / MRAG 关联到 material pack，再读取：

```text
weak_model_summary.status
weak_model_summary.quality_status
weak_model_summary.weak_model_run_id
weak_model_summary.weak_model_aggregation_id
weak_model_summary.quality_check_id
weak_model_summary.directional_bias
weak_model_summary.directional_score
weak_model_summary.directional_confidence
weak_model_summary.risk_level
weak_model_summary.trade_permission
weak_model_summary.veto_triggered
weak_model_summary.veto_factors
weak_model_summary.context_summary
legacy_math_context.status
```

不要在 21 内重新运行 27A / 27B。

---

## 4. 展示字段

建议最终展示字段：

```text
weak_model_direction_label：偏多 / 偏空 / 中性
weak_model_directional_score：方向系数
weak_model_signal_strength：abs(directional_score)
weak_model_directional_confidence：弱模型聚合置信度
weak_model_risk_level：风险等级
weak_model_trade_permission：弱模型交易权限
weak_model_quality_status：质量检查状态
weak_model_veto_triggered：是否触发否决
weak_model_veto_factors：否决原因
weak_model_context_regime：市场背景
```

中文映射建议：

```text
bullish → 偏多
bearish → 偏空
neutral → 中性
allow → 允许继续分析
deny / block / stop → 弱模型否决
passed → 通过
warning → 警告
critical → 严重异常
missing → 缺失
unchecked → 未检查
```

---

## 5. 展示原则

必须明确写出：

```text
弱模型只是辅助证据，不是最终交易建议。
```

不能写成：

```text
弱模型建议做多
弱模型建议做空
弱模型给出交易信号
```

正确表达：

```text
弱模型方向倾向：偏空
弱模型方向系数：-0.60
弱模型交易权限：allow，只表示未被弱模型风控否决，不等于建议开仓
```

---

## 6. 状态处理规则

### 6.1 passed

```text
展示完整弱模型摘要
允许进入最终通知
```

### 6.2 warning

```text
展示弱模型摘要
同时显示：弱模型质量检查存在警告，需谨慎参考
```

### 6.3 critical / excluded_by_quality_check

```text
不展示为正常弱模型依据
展示为：弱模型质量严重异常，本轮不作为有效辅助证据
```

### 6.4 missing

```text
展示为：本轮未找到弱模型结果
不得伪装为中性
```

### 6.5 unchecked

```text
展示为：弱模型结果未经过 27B 质量检查
自动 pipeline 正常情况下不应出现；若出现，应作为链路异常提示
```

---

## 7. Advice 结构落库

如果当前 `strategy_advice` / `strategy_advice_lifecycle_review` 已有 JSON 字段保存通知 payload，则在 payload 中新增：

```json
{
  "weak_model_display": {
    "status": "available",
    "quality_status": "passed",
    "direction_label": "偏空",
    "directional_score": -0.6,
    "signal_strength": 0.6,
    "directional_confidence": 0.079,
    "risk_level": "low",
    "trade_permission": "allow",
    "veto_triggered": false,
    "veto_factors": [],
    "context_regime": "trend",
    "not_trading_advice": true,
    "source": {
      "weak_model_run_id": "WMR-xxx",
      "weak_model_aggregation_id": "WMA-xxx",
      "quality_check_id": "WMQC-xxx",
      "material_pack_id": "AMP-xxx"
    }
  }
}
```

不建议新增主表固定字段，优先放在现有通知 payload / details JSON 中，避免数据库字段膨胀。

---

## 8. Hermes 通知模板

建议在最终策略通知中增加一个短区块：

```text
【弱模型摘要】
方向：偏空
方向系数：-0.60
方向强度：0.60
置信度：0.079
风险等级：low
权限：allow
质量：passed
说明：弱模型仅作辅助证据，不是最终交易建议。
```

如果 warning：

```text
【弱模型摘要】
方向：偏空
方向系数：-0.60
质量：warning
提示：弱模型质量检查存在警告，本轮仅作低权重参考。
```

如果 critical：

```text
【弱模型摘要】
状态：严重异常
说明：本轮弱模型结果未作为有效辅助证据。
```

---

## 9. 长度控制

Hermes 通知不能把弱模型全部展开。

限制：

```text
veto_factors 最多展示 3 条
quality_issues 最多展示 3 条
context_summary 只展示 regime / source_model_key / confidence 等关键字段
不展示 raw_output_json
不展示每个弱模型完整计算过程
```

完整追溯依赖：

```text
weak_model_run_id
weak_model_aggregation_id
quality_check_id
material_pack_id
```

---

## 10. 不做事项

27D 不做：

```text
不重新运行 27A
不重新运行 27B
不修改 27C material schema
不新增 scoring_contract
不修改 19 prompt
不调整弱模型权重
不调整弱模型参数
不改变 20 复用判断
不调用大模型
不请求 Binance REST
不读取账户 / 仓位
不自动交易
```

---

## 11. 测试要求

至少覆盖：

```text
1. 21 生成通知 payload 时能读取 weak_model_summary
2. passed 状态下 Hermes payload 包含弱模型方向、系数、风险、质量
3. warning 状态下明确展示 warning
4. critical / excluded_by_quality_check 状态下不作为有效辅助证据
5. missing 状态下显示缺失，不伪装为中性
6. unchecked 状态下显示未检查
7. Hermes 文案包含“不是最终交易建议”
8. 不展示 raw_output_json
9. 长数组会被限制数量
10. 不调用 27A / 27B / 大模型 / Binance REST
```

回归：

```bash
python -m pytest tests/strategy_advice -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/weak_models -q
python -m pytest -q
```

如果仓库测试目录命名不同，以现有 21 / advice / notification 测试目录为准。

---

## 12. 验收标准

27D 通过标准：

```text
1. 最终 advice notification payload 包含 weak_model_display
2. Hermes 通知能看到弱模型方向系数、强度、风险、质量状态
3. warning / critical / missing / unchecked 展示符合规则
4. 不把弱模型表达成最终交易建议
5. 不污染 19 prompt / 20 复用判断 / 27C material schema
6. 不产生超长通知
7. pytest 通过
```

---

## 13. 完成后下一步

27D 完成后，27 阶段可以收尾。

后续进入：

```text
28：策略 / 弱模型 / 大模型复盘基础
```
