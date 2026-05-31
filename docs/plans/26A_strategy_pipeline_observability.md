# 26A 策略链路运行观测 Plan

## 1. 阶段定位

26A 是策略系统进入安全试运行后的运行观测层。

它不生成交易建议，不优化策略算法，不调用大模型做分析，不修改 25 调度链路。它只回答一个问题：

> 最近 N 根 4h K线后，策略链路有没有按预期执行；如果没有，停在哪里，原因是否合理。

26A 是 26 阶段的第一部分，后续可继续扩展：

- 26B：策略证据质量检查
- 26C：运行日报 / 观察报告

本阶段只做 26A。

---

## 2. 背景

当前 25 阶段已经实现：

```text
09 4h K线采集成功
→ scheduler runner 自动触发 25 pipeline
→ 25 pipeline 编排 17/16
→ 15 懒加载快照
→ 23B/23C/23D/23E 策略模块
→ 24A/23F 策略证据聚合
→ 18 材料包
→ 20C/19/20A 模型审查与聚合
→ 21A/21B 建议与通知记录
```

但系统开始自动运行后，不能只靠人工翻日志判断是否正常。26A 需要提供一个结构化 CLI，用于快速检查最近若干根 4h K线对应的策略链路状态。

---

## 3. 核心目标

实现一个 CLI：

```bash
python -m scripts.check_strategy_pipeline_status \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 5
```

该脚本应输出最近 N 根 4h 已收盘 K线对应的策略链路状态，至少包含：

- K线 slot
- 09 K线是否存在 / 是否采集成功
- 25 pipeline 是否存在
- pipeline 当前状态
- 当前步骤 current_step
- SSR 是否生成
- SEA 是否生成
- AMP 是否生成
- MRAG 是否生成
- ADVR 是否生成
- 是否真实调用模型
- 是否真实发送 Hermes
- 阻断 / 失败原因
- 该阻断在当前配置下是否合理

---

## 4. 非目标

26A 不做以下事情：

- 不新增策略
- 不修改 23B/23C/23D/23E/23F 算法
- 不修改 18 材料包生成逻辑
- 不修改 19 模型 prompt
- 不修改 20 模型复用规则
- 不修改 21 建议生成逻辑
- 不修改 25 调度逻辑
- 不自动调用大模型
- 不真实发送 Hermes
- 不自动交易
- 不读取交易所账户
- 不读取真实仓位
- 不做收益复盘
- 不做大模型自我进化

---

## 5. 观测范围

26A 第一版只观测 BTCUSDT / 4h / 1d 主链路，但实现上应保留参数化能力：

- symbol
- base_interval
- higher_interval
- limit

默认值建议：

```text
symbol=BTCUSDT
base_interval=4h
higher_interval=1d
limit=5
```

---

## 6. 状态判断规则

### 6.1 slot 维度

26A 必须按 4h K线 slot 判断，而不是只查最近 pipeline。

原因：只查最近 pipeline 无法发现漏跑。

每个 slot 应至少判断：

```text
该 slot 是否有已收盘 K线
该 slot 是否有对应 pipeline_run
该 slot 是否重复 pipeline_run
该 slot 是否缺 pipeline_run
```

### 6.2 pipeline 状态

状态分类建议：

```text
healthy              正常完成
expected_blocked     当前配置下合理阻断
failed               异常失败
missing              应该运行但缺失
duplicate            同一 slot 有重复 pipeline
unknown              无法判断
```

注意：不要简单把 `blocked` 当失败。

例如：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED=false
且 pipeline 阻断在 20C / 19 / 20A
且 error_code=no_model_review_result
```

在安全模式下属于 expected_blocked。

但如果真实模型开关已开启，仍然出现 `no_model_review_result`，则应判定为 failed 或 abnormal_blocked。

### 6.3 链路完整度

每轮至少检查：

```text
SP → SSR → SEA → AMP → MRAG → ADVR
```

说明：

- SSR：策略信号运行结果
- SEA：23F 策略证据聚合结果
- AMP：18 材料包
- MRAG：20A 模型审查聚合结果
- ADVR：21A/21B 建议生命周期 review

安全模式下，链路可能合理停在 MRAG 或 20C 之前，但必须解释清楚原因。

---

## 7. 输出要求

CLI 输出应简洁、中文为主、字段清楚。

建议输出分两层：

### 7.1 汇总

示例：

```text
策略链路运行观测
symbol=BTCUSDT base_interval=4h higher_interval=1d limit=5

汇总：
- 检查 slot 数：5
- healthy：0
- expected_blocked：5
- failed：0
- missing：0
- duplicate：0
- 当前真实模型：关闭
- 当前真实 Hermes：关闭
```

### 7.2 明细

示例：

```text
[2026-05-31T04:00:00Z]
状态：expected_blocked
说明：安全模式下模型关闭，pipeline 合理停在 20C。
- pipeline_run_id：SP-xxx
- pipeline_status：blocked
- current_step：20c_19_20a_model_review
- SSR：存在 SSR-xxx
- SEA：存在 SEA-xxx
- AMP：存在 AMP-xxx
- MRAG：存在 MRAG-xxx
- ADVR：缺失
- real_model_called：false
- hermes_real_sent：false
- error_code：no_model_review_result
- error_message：MODEL_REVIEW_REAL_MODEL_ENABLED=false
```

### 7.3 退出码建议

```text
0：全部 healthy 或 expected_blocked
1：存在 missing / failed / duplicate / unknown
2：脚本参数错误或数据库查询失败
```

---

## 8. 配置读取

26A 应读取当前配置，用于判断 blocked 是否合理。

至少读取：

- STRATEGY_PIPELINE_ENABLED
- STRATEGY_PIPELINE_SCHEDULER_ENABLED
- STRATEGY_EVIDENCE_AGGREGATION_ENABLED
- STRATEGY_PIPELINE_REAL_MODEL_ENABLED
- STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST
- MODEL_REVIEW_REAL_MODEL_ENABLED
- STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED
- STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED

输出中要显示关键开关，避免用户误判。

---

## 9. 数据来源建议

优先读取以下表：

- market_kline_4h 或当前 4h K线表
- strategy_pipeline_event_log
- strategy_signal_run
- strategy_evidence_aggregation_result
- analysis_material_pack
- model_review_aggregation_run
- strategy_advice_lifecycle_review
- alert_message（可选，仅用于通知状态核验）

第一版不要求复杂 join，可以通过 repository/service 分层查询。

---

## 10. 缺失与重复判断

### 10.1 缺失

如果某个已收盘 slot 有 4h K线，但没有 pipeline_run，应输出：

```text
状态：missing
说明：该 4h K线已存在，但未找到对应 25 pipeline。
```

### 10.2 重复

如果同一 symbol/base_interval/higher_interval/kline_slot 存在多个 pipeline_run，应输出：

```text
状态：duplicate
说明：同一 slot 存在多个 pipeline_run，请检查幂等或手动重复触发。
```

但重复不一定都是错误。若存在人工 retry，应在说明中提示需要人工确认。

第一版可以先标记 duplicate，不做复杂归因。

---

## 11. 合理阻断判断

第一版至少支持以下 expected_blocked 场景：

### 11.1 安全模式关闭真实模型

条件：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED=false
或 STRATEGY_PIPELINE_REAL_MODEL_ENABLED=false
```

且 pipeline 停在：

```text
20c_19_20a_model_review
```

且 error_code 类似：

```text
no_model_review_result
real_model_disabled
```

则判定为 expected_blocked。

### 11.2 Hermes 真实发送关闭

如果 21 已生成 review，但 Hermes 未真实发送，且：

```text
STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=false
或 STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=false
```

则不应判定为失败。

---

## 12. 测试要求

新增或修改测试，至少覆盖：

1. 最近 N 根 slot 全部有 pipeline，且安全模式下停在 20C，判定 expected_blocked。
2. 已有 4h K线但缺 pipeline，判定 missing。
3. 同一 slot 有多个 pipeline，判定 duplicate。
4. pipeline failed，判定 failed。
5. MODEL_REVIEW_REAL_MODEL_ENABLED=false 时，no_model_review_result 判定 expected_blocked。
6. MODEL_REVIEW_REAL_MODEL_ENABLED=true 时，no_model_review_result 不应判定 expected_blocked。
7. 输出中包含关键 ID：SP / SSR / SEA / AMP / MRAG / ADVR。
8. 输出中包含 real_model_called / hermes_real_sent。
9. CLI 参数错误时返回 exit_code=2。
10. 不调用真实模型，不发送 Hermes。

必须运行：

```bash
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/scheduler -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_advice -q
```

如新增独立测试目录，例如 `tests/strategy_observability`，也必须运行。

---

## 13. 文件建议

建议新增：

```text
app/strategy_observability/__init__.py
app/strategy_observability/types.py
app/strategy_observability/repository.py
app/strategy_observability/service.py
scripts/check_strategy_pipeline_status.py
tests/strategy_observability/test_strategy_pipeline_status.py
```

如果项目已有更合适的 monitoring 模块，也可以放入现有结构，但不要把大量查询逻辑塞进 script。

---

## 14. 边界声明

26A 输出必须明确：

```text
本检查只用于策略链路运行观测，不是交易建议。
不自动交易，不读取账户，不生成订单。
```

---

## 15. 验收标准

26A 完成后，用户应能通过一条命令看到最近 N 根 4h K线的策略链路运行状态。

验收通过条件：

- CLI 可运行
- 能按 slot 展示最近 N 根 4h K线
- 能识别 missing / duplicate / failed / expected_blocked
- 能显示 SP / SSR / SEA / AMP / MRAG / ADVR 关键链路 ID
- 能解释 blocked 是否合理
- 安全模式下不会误报模型关闭为系统失败
- 不调用真实模型
- 不发送 Hermes
- 不影响 25 调度链路
