# 26B 策略证据质量闸门 Plan

## 1. 阶段定位

26B 是策略主链路中的**策略证据质量闸门**，不是普通只读诊断工具。

它插入在：

```text
16/17 策略信号运行
→ 23F/24 策略证据聚合
→ 26B 策略证据质量闸门
→ 18 材料包
→ 20 模型审查聚合
→ 21 建议生命周期
```

26B 只判断一件事：

> 本轮正常运行策略输出的公开证据是否完整、有效、可继续作为后续 18/20/21 的可靠输入。

如果正常运行策略缺失或出错，26B 必须阻断主链路，并立即发送 Hermes 系统告警。

---

## 2. 关键原则

### 2.1 重大事故规则

任何正常运行策略，只要发生缺失或出错，都视为重大事故：

```text
不允许继续进入 18 材料包
不允许继续进入 20 模型审查
不允许继续生成 21 建议
必须立即 Hermes 告警
```

这里的告警是系统告警，不是交易建议，不调用大模型。

### 2.2 不属于 26B 的事情

26B 不做：

- 不检查原始 K线是否漏采或连续，原始 K线质量仍由 07/11 负责。
- 不判断策略未来是否赚钱。
- 不判断行情涨跌是否正确。
- 不调用 DeepSeek / GPT / Claude 等大模型。
- 不生成交易建议。
- 不读取交易所账户或仓位。
- 不自动交易。
- 不修改策略算法。

---

## 3. 正常运行策略定义

26B 第一版按配置识别“正常运行策略”。

策略同时满足以下条件时，视为正常运行策略：

```text
1. 出现在 configs/strategies/strategy_registry.yaml 的 enabled_strategies 中
2. 策略 YAML 中 enabled=true
3. maturity_stage=active
4. participation_mode=decision_participant 或 can_veto=true
```

以下策略不纳入重大事故判断：

```text
enabled=false 的主动关闭策略
maturity_stage=experimental / internship / observe_only 的实习期或观察期策略
participation_mode=observe_only 且 decision_weight=0 且 can_veto=false 的占位策略
```

例如当前 `gann_placeholder` 属于占位/观察策略，不应被当成有效 Gann 策略，也不应因为它不提供真实 Gann 证据而阻断主链路。

---

## 4. 质量状态

26B 输出三类状态：

```text
passed   正常运行策略全部存在，公共证据完整，可继续后续链路
warning  非阻断问题，例如观察期策略缺失、辅助字段较弱、非核心证据质量偏低
failed   正常运行策略缺失或出错，必须阻断后续链路并 Hermes 告警
```

在主链路中，`failed` 必须阻断。

---

## 5. failed 判定规则

任一正常运行策略出现以下情况，26B 必须判定 `failed`。

### 5.1 策略结果缺失

正常运行策略在本轮 `strategy_signal_run_id` 下没有对应 `strategy_signal_result`。

示例：

```text
support_resistance_strategy 在 registry 和 YAML 中均启用且 active，
但本轮 SSR 没有该策略结果。
```

处理：

```text
failed
error_code=active_strategy_result_missing
阻断 18/20/21
发送 Hermes critical 告警
```

### 5.2 策略运行失败或无效

正常运行策略结果出现：

```text
strategy_status=failed
strategy_status=invalid
validation_status=failed
validation_status=invalid
```

处理：

```text
failed
error_code=active_strategy_result_failed
```

说明：

- 如果整个 16/17 阶段失败，没有 SSR/SEA，则属于 25/26A 暴露的 pipeline failed，26B 不负责让主链路继续。
- 26B 的前提是至少已有 SSR/SEA，可检查本轮策略证据。

### 5.3 公共证据不可解析

正常运行策略的 `common_payload_json` 解析失败，或解析后不是合法对象。

处理：

```text
failed
error_code=common_payload_parse_failed
```

### 5.4 必需 provides 缺失

正常运行策略声明了 `provides`，但公共输出缺少对应关键证据。

第一版至少检查以下核心 provides：

```text
context              -> primary_regime / market_environment_context
support_resistance   -> key_levels
filter               -> trigger_state
risk_control         -> risk_gate_decision
```

这些规则优先从 `configs/strategy_aggregation/evidence_aggregation.yaml` 和策略 YAML 读取。

### 5.5 支撑压力关键证据缺失

`support_resistance_strategy` 为正常运行策略时，以下任一情况必须 failed：

```text
key_levels 缺失或为空
nearest_support 和 nearest_resistance 均缺失
current_price 缺失
key_levels 无法关联当前价格
```

说明：

支撑压力是后续 filter、risk_control、模型审查的重要基础。该策略失败后，后续方向建议不能继续假装证据完整。

### 5.6 filter 空转

`breakout_pullback_trigger_strategy` 为正常运行策略时，以下任一情况必须 failed：

```text
trigger_state 缺失
filter_decision 缺失
它输出突破/回踩确认，但本轮没有可用 key_levels
它消费的 key_levels 与本轮 SSR/SEA 不一致
```

### 5.7 风控关键结论缺失

`volatility_risk_control_strategy` 为正常运行策略时，以下任一情况必须 failed：

```text
risk_gate_decision 缺失
trade_permission_filter 缺失
reward_risk_feasibility 缺失
can_veto=true 但没有输出可解释的 veto / allow / wait 决策
```

### 5.8 slot 或 run 混用

同一轮 26B 检查中发现证据来自不同 `strategy_signal_run_id`、不同 slot，或 material/evidence 关联不一致，必须 failed。

处理：

```text
failed
error_code=strategy_evidence_run_mismatch
```

---

## 6. warning 判定规则

以下情况可以 warning，不阻断：

```text
观察期策略缺失或 invalid
占位策略未输出真实证据
非核心辅助字段为空，但核心 provides 完整
支撑压力数量较少，但 key_levels 仍可用
置信度偏低，但策略状态成功且字段完整
策略之间存在可解释分歧，但没有关键证据缺失
```

warning 后续可以进入 18/20/21，但必须在质量结果中记录，后续材料包可选择展示。

26B 第一版如果尚未接入 18 材料包，不强制修改 18。

---

## 7. 主链路行为

### 7.1 passed

```text
25 pipeline 继续进入 18
不发送单独 Hermes 告警
质量结果落库
```

### 7.2 warning

```text
25 pipeline 继续进入 18
质量结果落库
不单独 Hermes 告警
后续可在策略通知中展示 warning 摘要
```

### 7.3 failed

```text
25 pipeline 停止进入 18
不生成新的 18 材料包
不触发 20 模型审查
不生成 21 建议
25 pipeline event 标记 blocked 或 failed
current_step=26b_strategy_evidence_quality_gate
error_code=strategy_evidence_quality_failed
立即发送 Hermes critical 系统告警
```

建议 25 pipeline 状态使用：

```text
status=blocked
error_code=strategy_evidence_quality_failed
```

说明：

这里的 blocked 表示质量闸门主动阻断，不是安全模式 expected_blocked。26A 应将其暴露为异常，不应归类为 expected_blocked。

---

## 8. Hermes 告警规则

### 8.1 告警触发

只要 26B failed，必须发送 Hermes 系统告警。

告警不调用大模型，使用固定中文模板。

### 8.2 告警性质

```text
alert_type=strategy_evidence_quality_failure
severity=critical
source=hermes_btc_agent.strategy_evidence_quality_gate
not_trading_advice=true
```

### 8.3 告警内容必须包含

```text
symbol
base_interval / higher_interval
kline_slot_utc
strategy_signal_run_id
strategy_evidence_aggregation_id
pipeline_run_id
失败策略列表
缺失/错误字段
阻断结果
是否调用大模型：否
是否生成建议：否
是否自动交易：否
trace_id
```

### 8.4 告警示例

```text
【策略证据质量重大异常】

BTCUSDT 4h / 1d 本轮策略链路已阻断。

原因：
- 正常运行策略 support_resistance_strategy 输出失败
- 必需证据 key_levels 缺失
- 后续 filter / risk_control / 模型审查不允许继续使用残缺证据

处理结果：
- 已阻断 18 材料包生成
- 未调用大模型
- 未生成交易建议
- 未自动交易

本告警不是交易建议。
```

### 8.5 幂等

同一个 `strategy_signal_run_id` / `evidence_aggregation_id` 的 failed 告警不得重复刷屏。

建议幂等键：

```text
strategy_evidence_quality_failure:{evidence_aggregation_id}
```

或数据库侧使用：

```text
alert_message.related_type='strategy_evidence_quality_check'
alert_message.related_id=quality_check_id
```

同一个 `quality_check_id` 只允许一条成功/已准备的告警记录。

---

## 9. 数据库设计建议

新增表：`strategy_evidence_quality_check_result`

核心字段：

```text
id
quality_check_id                  EQC-xxx
pipeline_run_id                   可空，CLI 手动检查时可空
strategy_signal_run_id             必填
evidence_aggregation_id            必填
symbol
base_interval
higher_interval
kline_slot_utc
status                             passed / warning / failed
severity                           info / warning / critical
should_block_pipeline               bool
error_code
error_message
failed_checks_json
warning_checks_json
strategy_quality_json
role_quality_json
config_snapshot_json
alert_required                     bool
alert_status                       pending / sent / failed / skipped
alert_message_id                   可空
not_trading_advice                 bool=true
trigger_source                     cli / pipeline
trace_id
created_at_utc
updated_at_utc
```

建议唯一约束：

```text
UNIQUE(evidence_aggregation_id, trigger_source)
```

如果 pipeline 重试同一 `evidence_aggregation_id`，应复用已有 failed 结果或幂等返回，不重复写入多条质量结果。

---

## 10. 模块建议

新增模块：

```text
app/strategy_evidence_quality/__init__.py
app/strategy_evidence_quality/types.py
app/strategy_evidence_quality/config.py
app/strategy_evidence_quality/repository.py
app/strategy_evidence_quality/service.py
app/strategy_evidence_quality/alerting.py
scripts/check_strategy_evidence_quality.py
tests/strategy_evidence_quality/test_strategy_evidence_quality_gate.py
```

迁移：

```text
migrations/versions/xxxx_add_strategy_evidence_quality_check_result.py
```

25 pipeline 需要新增一个 stage：

```text
PIPELINE_STEP_STAGE26B = "26b_strategy_evidence_quality_gate"
```

插入位置：23F 成功之后，18 之前。

---

## 11. CLI 设计

CLI 第一版用于人工查看质量闸门结果，也可以 dry-run 检查指定 SSR/SEA。

建议命令：

```bash
python -m scripts.check_strategy_evidence_quality \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 5
```

指定检查：

```bash
python -m scripts.check_strategy_evidence_quality \
  --evidence-aggregation-id SEA-xxx
```

CLI 默认只读：

```text
不写数据库
不发送 Hermes
不阻断 pipeline
不调用大模型
```

如果后续需要人工补跑质量检查并写库，必须单独设计 `--confirm-write`，本阶段不默认开放。

---

## 12. 退出码

CLI 退出码：

```text
0：全部 passed 或仅 warning
1：存在 failed
2：参数错误或数据库查询失败
```

pipeline 内部不使用 CLI 退出码，而使用 service 返回对象控制是否阻断。

---

## 13. 与 26A 的关系

26A 检查链路是否跑到位：

```text
K线 → SP → SSR → SEA → AMP → MRAG → ADVR
```

26B 检查 SEA 之后的策略证据是否合格。

如果 26B failed：

```text
25 pipeline 停在 26B
26A 后续应看到 current_step=26b_strategy_evidence_quality_gate
26A 应将其归类为 failed / abnormal blocked，而不是 expected_blocked
```

26A 不需要理解所有 26B 细节，只需要能显示当前步骤、error_code、error_message。

---

## 14. 测试要求

必须新增测试覆盖：

```text
1. active decision_participant 策略缺失 -> failed + should_block_pipeline=true
2. active can_veto 风控策略缺失 -> failed
3. active 策略 strategy_status=failed -> failed
4. active 策略 strategy_status=invalid -> failed
5. common_payload_json 解析失败 -> failed
6. support_resistance 缺 key_levels -> failed
7. filter 输出 trigger 但无本轮 key_levels -> failed
8. risk_control 缺 risk_gate_decision -> failed
9. gann_placeholder observe_only 缺失 -> 不 failed，最多 warning 或忽略
10. enabled=false 策略缺失 -> 不 failed
11. maturity_stage=experimental/internship 策略缺失 -> 不 failed
12. 全部 active 策略证据完整 -> passed
13. warning 不阻断 pipeline
14. failed 阻断 18/20/21
15. failed 触发 Hermes 告警准备/发送逻辑
16. Hermes 告警幂等，同一 quality_check_id 不重复发送
17. CLI 默认只读，不写库、不发 Hermes
18. CLI 参数错误 exit_code=2
```

回归测试建议：

```bash
python -m pytest tests/strategy_evidence_quality -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_observability -q
python -m pytest tests/strategy_aggregation -q
python -m pytest tests/model_analysis -q
python -m pytest tests/strategy_advice -q
```

---

## 15. 验收标准

26B 通过条件：

```text
1. 25 pipeline 在 23F/24 之后、18 之前执行 26B
2. active 策略缺失或出错时，pipeline 不继续进入 18/20/21
3. active 策略缺失或出错时，Hermes 发送 critical 系统告警
4. observe_only / experimental / disabled 策略缺失不会阻断
5. gann_placeholder 不会被误判为有效 Gann 策略
6. 质量结果落库，可追踪 quality_check_id
7. 告警幂等，不重复刷屏
8. CLI 可查看最近质量检查结果
9. 26B 不调用大模型、不交易、不读取账户
10. 26A 能看到 26B 阻断后的 pipeline 状态
```

---

## 16. 当前阶段不做

本阶段不做：

```text
不做策略收益复盘
不做历史回放
不做模型 prompt 优化
不做 Admin 后台
不做 UI 面板
不做自然语言修正
不做 Gann 正式策略实现
不做资金费率/滑点复盘
```

这些放到后续阶段。
