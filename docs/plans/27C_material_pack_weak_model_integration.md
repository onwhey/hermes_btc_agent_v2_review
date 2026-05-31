# 27C：18 材料包接入弱模型摘要与模型审查兼容计划

## 1. 阶段定位

27C 的目标不是继续开发弱模型，而是把 27A 产生、27B 审查、27B-1 校准后的弱模型摘要接入阶段 18 的 material pack，并确保后续 19/20 大模型审查不会误读、漏读或复用过期结论。

完整链路：

```text
15 市场上下文快照
→ 16/23 策略层
→ 27A 弱模型运行
→ 27B 弱模型质量审查
→ 27B-1 参数保守化
→ 27C 18 材料包接入 weak_model_summary
→ 20 判断是否需要调用/复用 19
→ 19 大模型审查完整 material pack
→ 21 最终建议
```

一句话：

```text
27C 不是简单把弱模型结果塞给 18，而是让弱模型摘要成为大模型可审查、可质疑、可追踪的正式材料。
```

---

## 2. 背景与关键风险

27A/27B 已经完成：

```text
weak_model_run
weak_model_result
weak_model_aggregation
weak_model_quality_check
```

并已验证：

```text
directional_score 从 -0.75 保守化到 -0.60
27B quality_check 从 warning 变为 passed
```

但现在进入 27C 后，会出现三个新风险：

### 2.1 原 18 数学材料与 27 弱模型重复

原 18 中已有一部分“数学材料 / 因子判断”。  
27A 已经把这类内容上游化为弱模型。

如果 27C 直接把 weak_model_summary 加进 18，但不处理旧数学材料，会产生重复喂料：

```text
旧数学材料说偏空
弱模型也说偏空
大模型误以为这是两组独立证据
```

实际它们可能来自同一套趋势、均线、波动、支撑压力逻辑。

这会放大偏见。

### 2.2 20 可能错误复用旧大模型审查

原 20 的复用逻辑可能主要关注策略结果、风险状态、material pack 或 K线有效期。

现在弱模型摘要进入 material pack 后，如果弱模型发生变化，例如：

```text
上一轮：weak_model_summary = bearish -0.75 warning
这一轮：weak_model_summary = bearish -0.60 passed
```

虽然方向没变，但弱模型强度、质量状态、配置 hash 都变了。  
20 不应静默复用旧大模型审查。

### 2.3 19 大模型不能盲信弱模型

大模型不能把 weak_model_summary 当成结论。  
它必须审查、质疑、反驳弱模型，包括：

```text
directional_score 是否过强
confidence 是否虚高
risk_level 是否过松
trade_permission 是否合理
quality_status 是否 warning / unchecked / critical
弱模型和策略是否同源重复计票
```

---

## 3. 核心目标

27C 需要完成：

```text
1. 18 根据当前 SSR / pipeline 找到对应 weak_model_aggregation
2. 18 读取对应 weak_model_quality_check
3. material pack 增加 weak_model_summary
4. 原 18 数学材料与 weak_model_summary 去重 / 降级 / 标记 legacy
5. material pack hash 必须包含 weak_model_summary
6. 20 复用判断必须考虑 weak_model_summary 变化
7. 19 prompt / material instruction 必须要求大模型质疑弱模型
8. 27B critical 时，weak_model_summary 不作为正常材料
9. 没有 WMA 时 18 不失败，但明确标记 missing
10. 不重新运行 27A / 27B
```

---

## 4. 严格边界

27C 不做：

```text
不新增弱模型
不调整弱模型参数
不重新跑 27A
不重新跑 27B
不自动调用 19
不直接发送 Hermes
不请求 Binance REST
不读取账户
不读取仓位
不自动交易
不改 21 最终建议展示
不接 scheduler 新逻辑
不把 weak_model_result 全量塞进 material pack
不让旧数学材料与弱模型重复表达同源证据
```

允许改动范围：

```text
18 material pack 构建逻辑
18 material schema
18 material hash / input hash
20 模型复用判断所依赖的 material change metadata
19 material prompt / review instruction 中对 weak_model_summary 的审查要求
相关测试
```

---

## 5. 读取规则

### 5.1 主关联键

27C 应优先通过当前 18 所使用的：

```text
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
kline_slot_utc
```

查询：

```text
weak_model_run
→ weak_model_aggregation
→ weak_model_quality_check
```

### 5.2 选择哪一条 WMA

同一个 SSR 可能有多次弱模型运行。

选择规则：

```text
1. 只选择 run_status=success 的 weak_model_run
2. 优先选择 created_at_utc 最新的一条
3. 必须 snapshot_id 与当前 SSR snapshot_id 一致
4. 必须 symbol/base_interval/higher_interval/kline_slot_utc 一致
5. 如果没有符合条件的 WMA，weak_model_summary.status=missing
```

### 5.3 27B quality_check 规则

读取同一个 `weak_model_run_id` 对应的最新 `weak_model_quality_check`。

规则：

```text
quality_check.status=passed
→ weak_model_summary.status=available

quality_check.status=warning
→ weak_model_summary.status=warning
→ 可以纳入 material pack，但必须带 quality_issues

quality_check.status=critical
→ weak_model_summary.status=excluded_by_quality_check
→ 不把 directional_score / trade_permission 当成有效弱模型结论

没有 quality_check
→ weak_model_summary.status=unchecked
→ 可以纳入，但必须明确标记 unchecked
```

27C 第一版不阻断 18 主链路，但不能把 critical 当正常材料。

---

## 6. material pack schema 调整

在 18 material pack 中新增：

```json
{
  "weak_model_summary": {
    "status": "available | missing | unchecked | warning | excluded_by_quality_check",
    "weak_model_run_id": "",
    "weak_model_aggregation_id": "",
    "quality_check_id": "",
    "quality_status": "",
    "directional_bias": "",
    "directional_score": null,
    "directional_confidence": null,
    "risk_level": "",
    "trade_permission": "",
    "veto_triggered": false,
    "supporting_factors": [],
    "opposing_factors": [],
    "conflict_factors": [],
    "low_confidence_factors": [],
    "veto_factors": [],
    "context_summary": {},
    "quality_issues": [],
    "source_config_hashes": [],
    "summary_text": "",
    "not_trading_advice": true
  }
}
```

### 6.1 不允许塞入全量原始结果

禁止把所有 `weak_model_result.raw_output_json` 全量塞进 material pack。

允许放：

```text
聚合摘要
主要支持因子
主要反对因子
主要冲突因子
风险否决因子
低置信度因子
背景摘要
质量检查摘要
配置 hash 摘要
```

---

## 7. 原 18 数学材料迁移规则

这是 27C 必须补的核心规则。

### 7.1 不允许重复计票

18 接入 weak_model_summary 后，原有数学材料不得和弱模型重复表达同一类判断。

重复来源包括：

```text
趋势强弱
均线方向
波动风险
成交量确认
支撑压力距离
多周期一致性
假突破风险
```

如果这些已经由 27 弱模型表达，应以 weak_model_summary 为主。

### 7.2 旧数学材料处理方式

27C 第一版不强行删除旧字段，但必须降级或标记：

```text
legacy_math_context
deprecated_math_material
```

规则：

```text
1. 弱模型已覆盖的数学判断，不再作为独立强证据输出
2. 旧数学材料只保留未被弱模型覆盖的背景信息
3. 如果保留旧数学材料，必须明确 source=legacy_math_context
4. material pack 中必须提示大模型：legacy_math_context 与 weak_model_summary 可能同源，不得重复计票
```

### 7.3 后续清理

27C 通过后，可以再做小任务：

```text
27C-1：清理或降级旧数学材料
```

但 27C 先完成接入和去重标记。

---

## 8. 20 模型复用兼容规则

27C 后，20 不能只看策略是否变化。  
20 判断是否复用旧 19 审查结果时，必须把 weak_model_summary 纳入材料变化判断。

### 8.1 material hash 必须包含弱模型摘要

18 生成 material pack hash 时必须包含：

```text
weak_model_run_id
weak_model_aggregation_id
quality_check_id
quality_status
directional_bias
directional_score
directional_confidence
risk_level
trade_permission
veto_triggered
veto_factors
context_summary
source_config_hashes
```

否则 20 可能误判材料未变化。

### 8.2 触发重新审查的弱模型变化

以下变化应视为 material pack 实质变化：

```text
1. weak_model_aggregation_id 变化
2. quality_check_id 变化
3. quality_status 从 passed 变 warning / critical / unchecked
4. directional_bias 变化
5. directional_score 跨阈值变化，例如 neutral ↔ bullish/bearish
6. directional_score 强度明显变化，例如 -0.75 → -0.60
7. risk_level 变化
8. trade_permission 变化
9. veto_triggered 变化
10. weak_model config_hash 变化
```

### 8.3 不允许错误复用

如果策略结论没变，但弱模型摘要发生实质变化：

```text
不能静默复用旧模型审查。
```

可以选择：

```text
重新调用 19
或明确记录：模型调用被配置关闭，因此无法基于新 weak_model_summary 重新审查
```

不能伪装成“已由最新大模型审查”。

---

## 9. 19 大模型审查要求

27C 后，19 看到 weak_model_summary 时，必须被明确要求：

```text
审查弱模型，而不是盲信弱模型。
```

### 9.1 大模型必须质疑的问题

19 prompt / material instruction 中应加入：

```text
1. weak_model directional_score 是否过强
2. confidence 是否虚高
3. risk_level 与当前波动是否一致
4. trade_permission=allow 是否过松
5. confirmation 是否真的支持方向
6. quality_status 是否 warning / unchecked / critical
7. weak_model_summary 与策略证据是否冲突
8. weak_model_summary 与策略证据是否同源重复
9. 是否存在双重计票风险
10. 若弱模型和策略冲突，优先指出冲突，而不是强行综合
```

### 9.2 大模型输出应包含

后续 19/20 审查结果中应能表达：

```text
weak_model_assessment
weak_model_supports_strategy
weak_model_conflicts_with_strategy
weak_model_quality_concerns
duplicate_evidence_risk
model_reviewer_note
```

27C 不一定完整改 19 结果表，但 material prompt 至少要让大模型输出这类判断。

---

## 10. 缺失与异常处理

### 10.1 没有 WMR / WMA

```text
weak_model_summary.status=missing
summary_text=未找到对应弱模型摘要，本轮材料包不包含弱模型证据。
```

18 不失败。

### 10.2 没有 27B quality_check

```text
weak_model_summary.status=unchecked
quality_status=unchecked
```

18 可以继续，但必须标记。

### 10.3 27B warning

```text
weak_model_summary.status=warning
quality_status=warning
quality_issues=issues_json
```

可以纳入，但必须提示大模型谨慎使用。

### 10.4 27B critical

```text
weak_model_summary.status=excluded_by_quality_check
quality_status=critical
quality_issues=issues_json
```

不把 directional_score / trade_permission 当正常材料使用。

---

## 11. 数据库是否新增

27C 第一版不建议新增表。

理由：

```text
27A 已有 weak_model_run/result/aggregation
27B 已有 weak_model_quality_check
18 已有 material_pack
```

27C 只需要把 weak_model_summary 写进 material pack 内容和 material hash。

如果现有 material pack 字段长度不足，必须按项目超长内容规则处理，不得静默截断。

---

## 12. 建议服务边界

建议新增：

```text
app/strategy_aggregation/weak_model_material.py
```

职责：

```text
根据 strategy_signal_run_id / snapshot_id 查询 WMA
读取 27B quality_check
构造 weak_model_summary
处理 missing / unchecked / warning / critical
提供 material hash 参与字段
```

不应把查询逻辑散落在 CLI、19 或 20 里。

---

## 13. CLI / 验证入口

27C 不一定新增 CLI。

复用现有 18 material pack 生成入口，以仓库实际脚本为准。

dry-run 输出中应能看到：

```text
weak_model_summary.status
weak_model_run_id
weak_model_aggregation_id
quality_check_id
quality_status
directional_bias
directional_score
risk_level
trade_permission
```

---

## 14. 测试要求

新增或扩展 18 / strategy_aggregation / model_review 相关测试。

至少覆盖：

```text
1. 有 WMA 且 27B passed，material pack 包含 weak_model_summary
2. 有 WMA 且 27B warning，material pack 包含 weak_model_summary，并标记 warning
3. 有 WMA 且 27B critical，material pack 标记 excluded_by_quality_check
4. 有 WMA 但无 quality_check，material pack 标记 unchecked
5. 没有 WMA，material pack 标记 missing，18 不失败
6. 多条 WMR 时选择同 SSR 最新 success WMR
7. snapshot_id 不匹配的 WMR 不可被选中
8. 不把 weak_model_result raw_output_json 全量塞入 material pack
9. legacy_math_context 不与 weak_model_summary 重复计票
10. material hash 包含 weak_model_summary
11. weak_model_summary 变化会导致 material hash 变化
12. 20 复用判断能识别 weak_model_summary 变化
13. 19 prompt / material instruction 包含“质疑弱模型”要求
14. 不重新运行 27A
15. 不重新运行 27B
16. 不自动调用大模型
17. 不发送 Hermes
18. 不请求 Binance REST
```

回归：

```bash
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/weak_models -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_pipeline -q
python -m pytest -q
```

---

## 15. 验收标准

27C 验收通过条件：

```text
1. 18 material pack 中出现 weak_model_summary
2. weak_model_summary 能正确引用 WMR / WMA / WMQC
3. passed / warning / critical / unchecked / missing 五种状态处理清楚
4. critical 不作为正常弱模型结论进入大模型材料
5. warning 明确标记，不静默当正常
6. 没有 WMA 时 18 不失败
7. 不包含 weak_model_result raw_output_json 全量
8. 原 18 数学材料被降级或标记 legacy，避免同源重复计票
9. material hash 包含 weak_model_summary
10. 20 复用判断能识别 weak_model_summary 变化
11. 19 prompt 明确要求质疑弱模型
12. 不重新运行 27A / 27B
13. 不自动调用大模型 / Hermes / Binance REST
14. pytest 全通过
```

---

## 16. 完成后的下一步

27C 完成后，下一步才考虑：

```text
27D：21 最终 advice / Hermes 展示弱模型摘要
```

但 27D 前必须确认：

```text
1. 18 material pack 中 weak_model_summary 简洁可信
2. 原数学材料没有和弱模型重复放大
3. 20 不会错误复用旧模型审查
4. 19 能明确审查和质疑 weak_model_summary
```
