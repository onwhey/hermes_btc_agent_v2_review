# 21_strategy_advice_lifecycle_plan.md

# 阶段 21：最终人工建议生成与建议生命周期

## 0. 本文件定位

本文件是阶段 21 的正式开发计划草案，用于指导后续 Codex 开发。

阶段 21 的目标不是重新做策略、不是重新调度大模型、也不是自动交易，而是把阶段 20 的受控模型审查结果，转换成可追踪、可复核、可通知、可生命周期管理的 `strategy_advice` 最终人工建议记录。

阶段 21 可以拆分为 21A / 21B / 21C 实施，但阶段 21 完整完成后，必须能完整验证：

```text
20 聚合结果
  -> strategy_advice
  -> strategy_advice_lifecycle_review
  -> strategy_advice_event
  -> strategy_advice_trade_setup
  -> 通知 payload / Hermes 发送
  -> scheduler 自动链路
```

---

## 1. 阶段定位

阶段关系：

```text
15：市场快照层
16：策略信号层，例如江恩、趋势、支撑压力、风控等
17：策略调度层，负责调用 16
18：分析材料层，整理策略结果和数学材料，生成 analysis_material_pack
19：单次大模型审查层，拿 18 材料请求大模型
20：模型审查控制层，决定是否调度 19、是否复用、是否过期、是否接力、是否 partial_success
21：最终人工建议生成与生命周期管理层
```

21 只消费 20 的模型审查控制结果，不重新实现 20 的模型调度、复用判断、chain 管理和模型有效性判断。

21 的核心职责：

```text
1. 读取阶段 20 的聚合结果；
2. 生成或维护 strategy_advice；
3. 判断本轮与上一条 active 建议的生命周期关系；
4. 保存每轮生命周期复核记录；
5. 保存生命周期事件流水；
6. 保存可选的条件交易方案 trade setup；
7. 准备通知所需字段和通知 payload；
8. 后续接入 Hermes 推送；
9. 保证每条建议都能追溯到 20 / 18 / 16 / 15。
```

---

## 2. 严格边界

21 必须遵守以下边界：

```text
1. 不自动交易；
2. 不读取账户；
3. 不自动下单；
4. 不生成系统自动执行指令；
5. 不直接调用 19；
6. 不直接请求 DeepSeek / GPT / Claude；
7. 不绕过 20 直接使用 19 原始结果生成最终建议；
8. 不把大模型输出直接当最终建议；
9. 不在证据不足时强行给方向性操作建议；
10. 不在 21A 实现复杂复盘；
11. 不把 trade setup 当订单、交易信号或必须执行指令。
```

所有 21 产出的建议记录必须保留边界字段：

```text
is_trading_signal = false
is_executable = false
auto_trading_allowed = false
```

不再使用 `is_final_strategy_advice` 字段。推送/通知判断不依赖该字段。

---

## 3. 21 输入来源

21 主要读取以下上游结果：

```text
model_review_aggregation_run        阶段 20A 聚合结果
model_review_chain_run              阶段 20B/20C 模型接力链状态
model_review_chain_step             阶段 20B/20C 模型接力 step 状态
analysis_material_pack              阶段 18 材料包
strategy_signal_run                 阶段 16 策略信号运行
market_context_snapshot             阶段 15 市场快照
```

21 应该优先读取 20 的受控输出，例如：

```text
model_review_invoked
model_review_invocation_mode
model_review_reused
reused_model_analysis_run_id
model_review_skip_reason
model_review_block_reason
invoked_model_keys_json
invoked_model_roles_json
model_review_chain_status
latest_model_review_at_utc
model_review_basis
model_review_expired
review_decision_summary
evidence_quality_summary
risk_acceptability_summary
strategy_conflict_summary
```

21 不应该重新判断：

```text
是否应该调用大模型；
是否应该复用旧模型结果；
旧模型结果是否超过 3 根 4h K线；
模型接力是否完整。
```

这些判断属于 20。

---

## 4. 建议内容结构

21 不应该靠大量枚举穷举所有建议类型。建议拆成三层：

```text
advice_action      当前总体建议动作
directional_bias   当前方向倾向
trade_permission   当前是否允许人工考虑交易
```

### 4.1 advice_action

阶段 21 先支持以下稳定英文 key，后续 Hermes 中文文案通过映射层转换：

```text
wait                 等待
avoid_trade          不交易
stop_trading         暂停交易
conditional_trade    条件满足后才允许人工考虑交易
manage_position      管理已有人工仓位，后续预留
```

不要单独设计以下容易重复的动作：

```text
observe
prepare_long
prepare_short
conditional_long
conditional_short
```

这些由 `advice_action + directional_bias + strategy_advice_trade_setup` 组合表达。

### 4.2 directional_bias

```text
bullish      偏多
bearish      偏空
neutral      中性
mixed        分歧
unknown      不明确
```

方向倾向不等于交易许可。

例如：

```text
advice_action = wait
directional_bias = bearish
trade_permission = not_allowed
```

含义是：结构偏空，但当前仍然等待，不建议开仓。

### 4.3 trade_permission

```text
not_allowed                    不允许交易
conditionally_allowed          条件满足后可人工考虑
position_management_only       只允许管理已有人工仓位
```

---

## 5. strategy_advice_trade_setup

`strategy_advice_trade_setup` 必须实现，不能仅作为远期预留。

它用于记录某条 `strategy_advice` 下的一个或多个条件交易方案/交易结构，例如：

```text
低多
高空
突破确认
回踩确认
区间上下沿观察
已有人工仓位的后续管理，后期再扩展
```

它不是订单，不是自动交易信号，不是必须执行指令。

一条 `strategy_advice` 可以有：

```text
0 个 setup：例如 wait / stop_trading / avoid_trade；
1 个 setup：例如只给出一个低多观察方案；
多个 setup：例如低多方案 + 突破确认方案。
```

示例：当前价格 70000，不追多，等待 65000 支撑低多，63000 失效。

```text
strategy_advice:
  advice_action = conditional_trade
  directional_bias = bullish 或 neutral
  trade_permission = conditionally_allowed

strategy_advice_trade_setup:
  setup_type = pullback_to_support
  side = long
  entry_zone_json = {"lower": 64500, "upper": 65500, "description": "65000 附近支撑区"}
  trigger_condition_json = {"text": "价格回撤至支撑区后，4h 结构不继续破位"}
  invalid_condition_json = {"text": "4h 收盘有效跌破 63000，低多假设失效"}
  stop_loss_json = {"price": 63000}
  target_zones_json = []
  expiry_base_bars = 3
  permission = conditionally_allowed
```

### 5.1 建议字段

```text
id
setup_id
advice_id
setup_rank
setup_type
side
entry_zone_json
trigger_condition_json
invalid_condition_json
stop_loss_json
target_zones_json
expiry_base_bars
permission
source_strategy_names_json
source_model_keys_json
status
created_at_utc
updated_at_utc
```

### 5.2 第一版要求

21A 可以只实现基础版：

```text
1. 表结构必须具备；
2. 落库能力必须具备；
3. 允许没有 setup；
4. 允许简单 setup；
5. 不要求第一版把低多、高空、突破、回踩等所有文案做完善；
6. 不允许把 setup 标记为可自动执行。
```

---

## 6. 建议生命周期总原则

21 必须维护建议生命周期，不能每 4h 生成孤立建议。

典型例子：

```text
04:00 创建 A1
08:00 继续维持 A1
12:00 A1 调整/升级为 A2
16:00 关闭 A2
```

核心规则：

```text
创建新建议：新增 strategy_advice；
延续建议：不新增 strategy_advice，只新增 lifecycle_review 和 event；
调整/升级建议：旧建议 superseded，新增新版本；
关闭/完成/失效/过期：不新增新版本，更新当前 active 建议状态；
每轮 4h 正常运行后必须生成 lifecycle_review；
每轮 4h 正常运行后必须具备通知依据，不能完全静默。
```

---

## 7. 生命周期三张核心表

阶段 21 生命周期使用三张核心表：

```text
strategy_advice
strategy_advice_lifecycle_review
strategy_advice_event
```

### 7.1 strategy_advice

记录建议版本本身，例如 A1、A2、A3。

建议字段：

```text
id
advice_id
advice_code
symbol
base_interval
higher_interval
parent_advice_id
root_advice_id
previous_advice_id
advice_path
version_no
advice_status
advice_action
directional_bias
trade_permission
source_review_aggregation_run_id
source_material_pack_id
source_strategy_signal_run_id
source_snapshot_id
source_model_chain_id
model_review_invoked
model_review_reused
model_review_basis
model_review_expired
model_review_status_summary_json
summary_text
risk_summary_json
strategy_summary_json
model_summary_json
is_trading_signal
is_executable
auto_trading_allowed
created_at_utc
updated_at_utc
closed_at_utc
```

字段说明：

```text
advice_status
= 当前这条建议的状态。

advice_action
= 当前总体建议动作。

directional_bias
= 当前方向倾向。

trade_permission
= 当前是否允许人工考虑交易。

advice_path
= 业务 ID 链路，例如 ADV-xxx-v1/ADV-xxx-v2/ADV-xxx-v3。
```

不再使用数字 `path = 1/2/3` 字段。

不再使用 `is_final_strategy_advice` 字段。

### 7.2 advice_status

建议状态使用稳定英文 key：

```text
candidate      候选，默认第一版可以少用
active         当前有效建议
superseded     被新版本替代
completed      建议完成
invalidated    建议失效
expired        建议过期
closed         建议关闭
cancelled      建议取消
```

---

### 7.3 strategy_advice_lifecycle_review

记录每轮 4h 对 active 建议的生命周期复核结果。

它不是收益复盘表。它回答：

```text
本轮对上一条 active 建议做了什么？
为什么继续？
为什么调整？
为什么关闭？
本轮是否需要通知？
```

建议字段：

```text
id
review_id
symbol
base_interval
higher_interval
reviewed_advice_id
result_advice_id
previous_advice_id
lifecycle_action
lifecycle_reason
source_review_aggregation_run_id
source_material_pack_id
source_strategy_signal_run_id
source_snapshot_id
model_review_invoked
model_review_reused
model_review_basis
model_review_expired
notification_required
notification_level
notification_reason
notification_payload_json
created_at_utc
```

### 7.4 lifecycle_action

`lifecycle_action` 使用稳定英文 key，后续 Hermes 中文通知通过映射层转换。

建议第一版取值：

```text
create_new_advice
continue_active_advice
update_active_advice
complete_active_advice
invalidate_active_advice
expire_active_advice
close_active_advice
cancel_active_advice
wait_without_active_advice
stop_trading
```

中文映射后续由通知层处理，例如：

```text
create_new_advice          => 新建建议
continue_active_advice     => 延续上一条建议
update_active_advice       => 调整上一条建议
complete_active_advice     => 建议完成
invalidate_active_advice   => 建议失效
expire_active_advice       => 建议过期
close_active_advice        => 关闭建议
wait_without_active_advice => 无 active 建议，继续等待
stop_trading               => 暂停交易
```

key 一旦入库，不应频繁修改。中文文案可以后续统一整改。

---

### 7.5 strategy_advice_event

记录生命周期事件流水。

它回答：

```text
这条建议从创建到关闭，发生过哪些事件？
```

建议字段：

```text
id
event_id
advice_id
related_review_id
event_type
event_reason
event_payload_json
created_at_utc
```

事件类型示例：

```text
created
continued
updated
superseded
activated
completed
invalidated
expired
closed
cancelled
notification_created
notification_sent
notification_failed
```

第一版可以做薄一点，但表结构和基本写入能力应具备。

---

## 8. advice_path 链路规则

阶段 21 只保留业务 ID 链路字段：

```text
advice_path = ADV-xxx-v1/ADV-xxx-v2/ADV-xxx-v3
```

不使用数字 ID 链路字段 `path = 1/2/3`。

规则：

```text
新建根建议：
advice_path = 当前 advice_id

创建新版本：
advice_path = parent.advice_path + "/" + 当前 advice_id

延续建议：
不创建新 strategy_advice
advice_path 不变

关闭/完成/失效/过期：
不创建新版本
更新当前 active 建议状态
advice_path 不变
```

---

## 9. A1/A2 生命周期例子

### 9.1 04:00 创建 A1

```text
strategy_advice 新增 A1
A1.status = active
A1.version_no = 1
A1.advice_path = A1
```

新增 lifecycle_review：

```text
lifecycle_action = create_new_advice
result_advice_id = A1
```

新增 event：

```text
event_type = created
event_type = activated
```

通知应表达：

```text
本轮生命周期：新建建议链
当前建议：A1
```

---

### 9.2 08:00 维持 A1

不新增 strategy_advice。

```text
A1 仍然 active
A1.version_no 不变
A1.advice_path 不变
```

新增 lifecycle_review：

```text
reviewed_advice_id = A1
result_advice_id = A1
lifecycle_action = continue_active_advice
lifecycle_reason = 本轮无实质变化，A1 继续有效
```

新增 event：

```text
event_type = continued
```

通知必须发送，可以是简短续期通知：

```text
本轮生命周期：延续上一条建议
当前建议：A1 继续有效
```

---

### 9.3 12:00 A1 调整为 A2

```text
A1.status = superseded
A2.status = active
A2.parent_advice_id = A1
A2.root_advice_id = A1
A2.version_no = 2
A2.advice_path = A1/A2
```

新增 lifecycle_review：

```text
reviewed_advice_id = A1
result_advice_id = A2
lifecycle_action = update_active_advice
lifecycle_reason = 关键条件变化，A1 被 A2 替代
```

新增 event：

```text
A1: superseded
A2: created
A2: activated
```

通知应表达：

```text
本轮生命周期：调整上一条建议
上一条建议：A1
新建议版本：A2
```

---

### 9.4 16:00 关闭 A2

不新增 A3。

```text
A2.status = closed / completed / invalidated / expired
A2.advice_path 不变
```

新增 lifecycle_review：

```text
reviewed_advice_id = A2
result_advice_id = A2
lifecycle_action = close_active_advice / complete_active_advice / invalidate_active_advice / expire_active_advice
lifecycle_reason = 关闭、完成、失效或过期原因
```

新增 event：

```text
event_type = closed / completed / invalidated / expired
```

通知应表达：

```text
本轮生命周期：关闭 / 完成 / 失效 / 过期上一条建议
当前结束建议：A2
原因：xxx
```

---

## 10. 通知规则

21 最终必须通知，不能完全静默。

原因：用户需要区分：

```text
系统正常运行但结论未变
```

和：

```text
系统崩溃 / scheduler 没跑 / Hermes 没发
```

通知分两类：

### 10.1 完整通知

用于：

```text
新建建议
建议调整
建议升级或降级
建议关闭
建议完成
建议失效
建议过期
风险状态变化
大模型状态变化
模型结果过期
模型接力 partial_success
配置阻断
```

### 10.2 简短续期通知

用于结论无实质变化：

```text
本轮已运行
上一条建议继续有效
本轮生命周期：continue_active_advice
大模型状态：未调用 / 复用 / 当前结果仍有效
当前建议未变
```

### 10.3 通知判断字段

通知判断不使用 `is_final_strategy_advice`。

应由 `strategy_advice_lifecycle_review` 控制：

```text
notification_required
notification_level
notification_reason
notification_payload_json
```

后续 Hermes/alert_message 应使用：

```text
alert_message.related_type = strategy_advice
alert_message.related_id = advice_id
```

21A 可以先生成通知 payload 或通知准备记录；21B 再正式接 Hermes 发送；21 完整完成后必须能跑通实际通知链路。

---

## 11. 大模型状态透明要求

21 通知和建议记录必须明确展示：

```text
本轮是否调用大模型
是否复用旧模型结果
复用的是哪条 model_analysis_run
旧结果是否超过 3 根 4h K线
是否被配置阻断
模型接力是否完整
模型审查是否 partial_success
当前建议是否基于最新模型审查
```

必须从 20 读取并保留：

```text
model_review_invoked
model_review_invocation_mode
model_review_reused
reused_model_analysis_run_id
model_review_skip_reason
model_review_block_reason
invoked_model_keys_json
invoked_model_roles_json
model_review_chain_status
latest_model_review_at_utc
model_review_basis
model_review_expired
```

如果本轮没有调用大模型，必须明确说明原因。

如果复用旧模型结果，必须明确说明复用哪条 run，以及是否仍在 3 根 base interval K线有效期内。

如果模型审查过期且真实模型关闭，不能伪装成最新大模型审查。

---

## 12. 风控否决规则

21 必须保留风控否决能力。

如果 20 或模型审查显示：

```text
risk_acceptability = unacceptable
strategy_conflict_level = high
model_review_expired = true 且没有新模型审查
model_review_chain_status = partial_success
数据质量异常
关键上游缺失
```

21 应倾向输出：

```text
wait
avoid_trade
stop_trading
```

不能输出积极的条件交易方案。

如果生成 `strategy_advice_trade_setup`，必须满足最低要求：

```text
有明确触发条件
有明确失效条件
有风险边界
有观察周期或有效期
没有被风控否决
```

---

## 13. 复盘后置

21A 不实现完整复盘。

21A 只保留后续复盘需要的来源链路：

```text
source_review_aggregation_run_id
source_material_pack_id
source_strategy_signal_run_id
source_snapshot_id
advice_id
created_at_utc
base_interval
```

完整复盘后续单独开阶段处理，例如：

```text
最大浮盈
最大浮亏
是否触发止损
是否触发目标
策略胜率
大模型审查质量
人工是否执行
```

后续真正复盘表不要命名为 `strategy_advice_review`，避免和 `strategy_advice_lifecycle_review` 混淆。

---

## 14. 分阶段实现建议

### 14.1 21A：最终建议基础落库 + 生命周期基础

21A 建议实现：

```text
1. 新增 strategy_advice；
2. 新增 strategy_advice_lifecycle_review；
3. 新增 strategy_advice_event；
4. 新增 strategy_advice_trade_setup；
5. 读取 20 聚合结果；
6. 生成建议记录；
7. 判断 new / continue / update / close / invalidate / complete / expire 等生命周期关系；
8. 记录大模型状态；
9. 记录非自动交易边界；
10. 生成 notification_required / notification_level / notification_reason / notification_payload_json；
11. 提供 CLI 手动验证入口。
```

21A 可以不接 Hermes 真实发送，也可以不接 scheduler；但必须为 21B / 21C 留清楚边界。

### 14.2 21B：Hermes 通知发送

21B 建议实现：

```text
1. 读取 21A 生成的 notification payload；
2. 生成 alert_message；
3. related_type = strategy_advice；
4. related_id = advice_id；
5. 支持 full / brief 通知；
6. 记录通知成功或失败；
7. 通知文案先可用即可，后续统一中文映射和排版优化。
```

### 14.3 21C：scheduler 自动链路

21C 建议实现：

```text
1. scheduler 在合适阶段触发 21；
2. 每个 4h 周期生成 lifecycle_review；
3. 结论不变也产生 brief 通知；
4. 支持从 20 聚合结果到 21 建议和通知的完整自动链路；
5. 防止重复生成同一周期建议；
6. 保留可追溯日志。
```

阶段拆分不影响最终 21 验收。21 完整完成后必须能完整测试从 20 聚合结果到 strategy_advice、lifecycle_review、event、trade_setup、notification、Hermes 推送的完整链路。

---

## 15. 21A 不做的内容

21A 暂不实现：

```text
复杂 Hermes 文案优化
完整复盘
人工执行反馈
Admin 后台
自动交易
真实仓位管理
复杂策略 setup 细节优化
复杂多 setup 排序算法
```

---

## 16. 核心验收标准

阶段 21 至少要能回答以下问题：

```text
1. 本轮有没有生成建议？
2. 本轮建议是新建、延续、调整、关闭、完成、失效、过期，还是暂停交易？
3. 如果延续，为什么延续？
4. 如果调整，旧建议是谁，新建议是谁，为什么调整？
5. 如果关闭，关闭原因是什么？
6. 当前建议是否允许人工考虑交易？
7. 当前方向倾向是什么？
8. 是否有 trade setup？
9. trade setup 是否有触发条件、失效条件和风险边界？
10. 本轮有没有调用大模型？
11. 如果没调用，为什么没调用？
12. 如果复用模型结果，复用的是哪一条？
13. 当前建议能追溯到哪条 20 聚合、18 材料、16 策略、15 快照？
14. 本轮是否生成通知准备记录？
15. 21 完整完成后是否能实际发出 Hermes 通知？
```

---

## 17. 测试要求

阶段 21 测试至少覆盖：

```text
1. 当前没有 active 建议时，生成新建议；
2. 当前有 active 建议且结论无变化时，不新增 strategy_advice，只新增 lifecycle_review 和 event；
3. 当前有 active 建议且结论变化时，旧建议 superseded，新建新版本；
4. 新版本 advice_path 正确追加；
5. 当前 active 建议触发关闭/完成/失效/过期时，不新增新版本，只更新状态；
6. lifecycle_action 正确；
7. notification_required 默认为 true；
8. no_change / continue 场景生成 brief 通知准备；
9. update / close / invalidate 场景生成 full 通知准备；
10. model_review_invoked / reused / expired 等状态从 20 正确继承；
11. 风控否决时不生成积极 trade setup；
12. 条件交易方案能写入 strategy_advice_trade_setup；
13. is_trading_signal=false；
14. is_executable=false；
15. auto_trading_allowed=false；
16. 不调用 19；
17. 不请求真实大模型；
18. dry-run 不写库；
19. confirm-write 正常写库；
20. 21 完整完成后能验证 Hermes 发送链路。
```

---

## 18. 结论

阶段 21 不是简单通知层，而是：

```text
最终人工建议生成 + 建议生命周期管理 + 通知准备/发送基础
```

第一版实现要保守，但结构必须正确。

最重要的边界：

```text
21 可以生成人工建议和条件观察方案；
21 不能生成自动交易指令；
21 不能绕过 20 直接调度或解释大模型；
21 必须每轮说明当前建议与上一条建议的生命周期关系；
21 必须明确本轮大模型参与状态。
```
