# 22B 人工执行 Hermes 入口计划

## 1. 阶段定位

22B 是 **人工执行反馈的 Hermes/微信入口层**。

22A 已经负责人工执行记录、仓位汇总、手续费、平均成本、已实现盈亏、净盈亏、保证金收益率、关闭仓位回执等核心计算与落库。22B 不重写这些算法，只负责把用户从 Hermes/微信发来的自然语言反馈，安全转换成 22A 可以执行的结构化请求。

核心流程：

```text
用户通过 Hermes/微信发送自然语言执行反馈
  -> 22B 规则解析
  -> 生成待确认 intent
  -> Hermes 返回中文确认消息
  -> 用户确认 MEI-xxx
  -> 22B 调用 22A service 写库
  -> 返回最终成功/失败回执
```

22B 的第一原则：**自然语言不能直接写库，必须先生成草稿并二次确认。**

---

## 2. 本阶段目标

22B 最小交付目标：

1. 接收 Hermes/微信入站人工执行反馈消息。
2. 使用规则解析器解析自然语言，不调用大模型。
3. 将解析结果写入 pending confirmation intent。
4. 通过 Hermes 返回中文待确认消息。
5. 用户回复确认后，调用 22A service 完成真实写库。
6. 支持取消、过期、重复确认幂等。
7. 对缺字段、解析失败、错误 manual_position_id、advice_id 不存在等情况返回中文提醒。
8. 保持 22A 的计算逻辑唯一，不在 22B 重算仓位、手续费、盈亏。

---

## 3. 明确不做

22B 不做以下内容：

```text
不自动交易
不读取 Binance 账户
不同步真实持仓
不调用 DeepSeek / OpenAI / Claude 等大模型
不让自然语言直接写 strategy_advice_execution_record
不绕过确认直接调用 22A 写库
不重写 22A 盈亏算法
不改 strategy_advice 生命周期状态
不做 Admin 后台
不做纠错 / 作废 / 修改 / 删除
不做复盘判断
不做资金费率
不做滑点字段
不做浮盈浮亏
不做 reduce_ratio
```

22B 只是一个安全录入入口，不是交易机器人。

---

## 4. 和 22A 的关系

22A 是事实写入和计算层。

22B 是入口、解析、确认、调度层。

分工如下：

```text
22B:
- 接收 Hermes 入站文本
- 解析文本
- 保存 intent
- 发送确认消息
- 处理确认/取消/过期
- 调用 22A service

22A:
- 校验 advice_id / manual_position_id
- 创建/更新 manual_position
- 写 execution_record
- 计算数量、均价、手续费、盈亏、收益率
- 关闭仓位时生成结算回执
```

22B 调用 22A 时，必须使用 22A 已有 service，不允许复制 22A 的计算逻辑。

---

## 5. 入站消息类型

22B 需要识别三类 Hermes 入站消息。

### 5.1 新的人工执行反馈

示例：

```text
advice_id=ADV-xxx，BTCUSDT 多单，成交价 60000，开仓 300U，保证金 100U
```

```text
MP-xxx 加仓，BTCUSDT 多单，成交价 62000，成交金额 200U，保证金 0U，advice_id=ADV-xxx
```

```text
MP-xxx 减仓，BTCUSDT 多单，成交价 65000，成交金额 100U，advice_id=ADV-xxx
```

```text
MP-xxx 平仓，成交价 66000，advice_id=ADV-xxx
```

### 5.2 确认 intent

示例：

```text
确认 MEI-xxxx
确认MEI-xxxx
```

### 5.3 取消 intent

示例：

```text
取消 MEI-xxxx
取消MEI-xxxx
```

如果消息既不是执行反馈，也不是确认/取消命令，应返回无法识别的中文提醒，不写库。

---

## 6. 规则解析要求

22B 先只做规则解析，不接大模型。

需要识别字段：

```text
action
symbol
side
manual_position_id
advice_id
price
notional_usdt
margin_usdt
reason
note
```

### 6.1 action 识别

```text
开仓 / 开多 / 开空              -> open_position
加仓 / 补仓                    -> add_position
减仓 / 部分减仓 / 部分止盈      -> reduce_position
平仓 / 全平                    -> close_position
止盈                           -> take_profit
止损                           -> stop_loss
```

注意：

- “部分止盈”在 22A 仍按 `reduce_position` 处理。
- “止盈全平”才按 `take_profit` 处理。
- “止损全平”才按 `stop_loss` 处理。
- 不支持百分比减仓，例如“减仓 50%”。

### 6.2 side 识别

```text
多单 / 做多 / long  -> long
空单 / 做空 / short -> short
```

专业英文可以识别，但回执使用中文展示。

### 6.3 symbol 识别

第一阶段至少支持：

```text
BTCUSDT
BTC
比特币
btc
```

统一归一为：

```text
BTCUSDT
```

后续可扩展其他交易对，但 22B 首版不需要主动扩展。

### 6.4 金额识别

以下表达都应尽量识别：

```text
成交金额 300U
成交 300U
开仓 300U
加仓 200U
减仓 100U
300 USDT
300u
```

统一为：

```text
notional_usdt
```

### 6.5 保证金识别

22A 最新保证金口径：

```text
用户输入 / execution_record 只有一个 margin_usdt
```

规则：

```text
open_position:
  “保证金 100U” = 本次开仓保证金 margin_usdt=100，必须 > 1

add_position:
  “保证金 100U” = 本次新增保证金 margin_usdt=100
  “保证金 0U” = 本次加仓没有新增保证金，允许

reduce_position / close_position / take_profit / stop_loss:
  不需要 margin_usdt
  即使用户误写保证金，22B 应提示该动作不需要保证金字段，或者在确认草稿中明确“保证金字段将被忽略”
```

为了避免误解，待确认消息必须写成：

```text
本次新增保证金：0U
```

而不是“当前保证金 0U”。

---

## 7. 各动作必填字段

### 7.1 open_position

必填：

```text
action
symbol
side
price
notional_usdt
margin_usdt
advice_id
```

不需要：

```text
manual_position_id
```

开仓会由 22A 生成新的 manual_position_id。

### 7.2 add_position

必填：

```text
action
symbol
side
price
notional_usdt
margin_usdt
advice_id
```

`manual_position_id` 建议提供。

如果不提供，22B 可以让 22A 在只有唯一匹配 open manual_position 时自动推断；如果存在多笔匹配仓位，必须 blocked，不允许猜。

### 7.3 reduce_position

必填：

```text
action
symbol
side
price
notional_usdt
advice_id
```

`manual_position_id` 建议提供。规则同 add_position。

不需要：

```text
margin_usdt
```

### 7.4 close_position / take_profit / stop_loss

必填：

```text
action
symbol
side
price
advice_id
```

`manual_position_id` 建议提供。规则同 add_position。

不需要：

```text
notional_usdt
margin_usdt
```

全平金额由 22A 根据 current_quantity_base_asset 和 price 自动计算。

---

## 8. intent 表设计

新增表建议命名：

```text
strategy_advice_manual_execution_intent
```

用途：保存 Hermes 自然语言解析后的待确认草稿和执行状态。

核心字段建议：

```text
id
intent_id
status
source_channel
source_message_id
source_user_id
raw_text
normalized_text
parsed_action
parsed_symbol
parsed_side
parsed_manual_position_id
parsed_advice_id
parsed_price
parsed_notional_usdt
parsed_margin_usdt
parsed_reason
parsed_note
parsed_payload_json
validation_status
validation_error_code
validation_error_message
missing_fields_json
dry_run_snapshot_json
executed_manual_position_id
executed_execution_id
expires_at_utc
confirmed_at_utc
cancelled_at_utc
executed_at_utc
failed_at_utc
trace_id
created_at_utc
updated_at_utc
```

### 8.1 intent_id

格式建议：

```text
MEI-xxxxxxxxxxxxxxxx
```

必须唯一。

### 8.2 status 枚举

至少支持：

```text
pending_confirmation
confirmed
executed
cancelled
expired
parse_failed
validation_failed
execution_failed
```

建议状态流转：

```text
新消息解析失败:
  parse_failed

新消息解析成功但字段缺失/校验失败:
  validation_failed

新消息解析成功并可确认:
  pending_confirmation

用户取消:
  pending_confirmation -> cancelled

用户确认但已过期:
  pending_confirmation -> expired

用户确认并调用 22A:
  pending_confirmation -> confirmed -> executed

用户确认后 22A 写库失败:
  pending_confirmation -> confirmed -> execution_failed
```

---

## 9. 过期规则

默认过期时间：

```text
10 分钟
```

配置项：

```text
MANUAL_EXECUTION_INTENT_EXPIRE_MINUTES=10
```

过期处理：

1. 用户超过过期时间再确认，不能写库。
2. 系统应将 intent 标记为 expired。
3. 返回中文提醒：

```text
【人工执行确认已过期】
intent_id: MEI-xxx
处理结果：未写入数据库
请重新发送执行反馈。
```

首版可采用懒过期：确认时发现过期再标记 expired。后续如有需要再加定时清理任务。

---

## 10. 二次确认流程

### 10.1 解析成功后

系统先生成 `pending_confirmation` intent，然后发送确认消息。

确认消息必须包含：

```text
intent_id
动作
symbol / side
manual_position_id，如果有
advice_id
price
notional_usdt，如果适用
margin_usdt，如果适用
预计手续费
dry-run 后的关键结果，如果可用
过期时间
确认/取消指令
```

示例：

```text
【人工执行待确认】

intent_id: MEI-xxx
动作：BTCUSDT 多单开仓
参考建议：ADV-xxx
成交价：60000
成交金额：300U
本次保证金：100U
预计手续费：0.06U

请在 10 分钟内回复：
确认 MEI-xxx
或：
取消 MEI-xxx
```

### 10.2 用户确认

用户回复：

```text
确认 MEI-xxx
```

系统检查：

1. intent 是否存在。
2. status 是否仍为 pending_confirmation。
3. 是否过期。
4. 是否已经 executed。
5. 调用 22A service confirm-write。

执行成功后：

```text
status = executed
executed_manual_position_id = 22A 返回值
executed_execution_id = 22A 返回值
```

返回中文最终回执。

### 10.3 用户取消

用户回复：

```text
取消 MEI-xxx
```

系统检查 intent 存在且未执行，然后：

```text
status = cancelled
```

返回中文提醒：

```text
【人工执行已取消】
intent_id: MEI-xxx
处理结果：未写入数据库。
```

---

## 11. dry-run / validation 设计

解析出结构化 payload 后，22B 应优先调用 22A 的 dry-run 或 validation 能力，不写库，用于：

1. 校验 advice_id 是否存在。
2. 校验 manual_position_id 是否存在、是否 open、是否匹配 symbol/side/action。
3. 校验同 symbol+side 多笔 open 仓位时是否需要 manual_position_id。
4. 计算预计手续费、数量、有效杠杆等确认展示信息。

dry-run 成功后再生成 pending_confirmation。

如果 dry-run blocked，则保存 validation_failed 或直接返回错误提醒，不能生成可确认写库的 intent。

---

## 12. 幂等规则

### 12.1 重复确认

如果一个 intent 已经 executed，再次收到：

```text
确认 MEI-xxx
```

系统不得再次调用 22A，不得重复写 execution_record。

返回：

```text
【人工执行已处理】
intent_id: MEI-xxx
该记录已执行，不会重复写入。
manual_position_id: xxx
execution_id: xxx
```

### 12.2 重复取消

如果 intent 已 cancelled，再次取消应返回已取消，不报系统错误。

### 12.3 已取消后确认

已取消 intent 再确认，必须拒绝写库。

### 12.4 已过期后确认

已过期 intent 再确认，必须拒绝写库。

---

## 13. Hermes 中文提醒模板

22B 通知要尽量中文、简短、清楚。

### 13.1 解析失败

```text
【人工执行解析失败】
原因：无法识别执行动作或关键字段。
处理结果：未写入数据库。

请按示例重新发送：
advice_id=ADV-xxx，BTCUSDT 多单，成交价 60000，开仓 300U，保证金 100U
```

### 13.2 缺字段

```text
【人工执行解析失败】
原因：缺少实际成交价 price / advice_id / 成交金额等字段。
处理结果：未写入数据库。
```

### 13.3 待确认

```text
【人工执行待确认】
intent_id: MEI-xxx
动作：BTCUSDT 多单开仓
参考建议：ADV-xxx
成交价：60000
成交金额：300U
本次保证金：100U
预计手续费：0.06U

请在 10 分钟内回复：
确认 MEI-xxx
或：
取消 MEI-xxx
```

### 13.4 执行成功

```text
【人工执行记录成功】
intent_id: MEI-xxx
manual_position_id: MP-xxx
execution_id: MEX-xxx
动作：BTCUSDT 多单开仓
处理结果：已写入数据库。
```

### 13.5 执行失败

```text
【人工执行录入失败】
intent_id: MEI-xxx
原因：manual_position_id 不存在或不匹配。
处理结果：未写入数据库。
```

### 13.6 过期

```text
【人工执行确认已过期】
intent_id: MEI-xxx
处理结果：未写入数据库。
请重新发送执行反馈。
```

---

## 14. 配置项

新增 env/config：

```text
MANUAL_EXECUTION_HERMES_ENTRY_ENABLED=false
MANUAL_EXECUTION_HERMES_REPLY_SEND_ENABLED=false
MANUAL_EXECUTION_INTENT_EXPIRE_MINUTES=10
```

说明：

- `MANUAL_EXECUTION_HERMES_ENTRY_ENABLED` 控制是否启用 Hermes 人工执行入口。
- `MANUAL_EXECUTION_HERMES_REPLY_SEND_ENABLED` 控制是否真实发送 Hermes 中文回复。
- `MANUAL_EXECUTION_INTENT_EXPIRE_MINUTES` 控制确认过期时间。

22A 已有：

```text
MANUAL_EXECUTION_FEE_RATE=0.0002
MANUAL_EXECUTION_RECEIPT_SEND_ENABLED=false
```

22B 不应改坏这些配置。

---

## 15. 模块边界建议

建议新增模块：

```text
app/manual_execution/hermes_entry/
```

内部可拆：

```text
parser.py              规则解析
intent_schema.py       intent 请求/响应结构
intent_repository.py   intent 表读写
intent_service.py      解析、确认、取消、执行编排
templates.py           中文消息模板
```

也可以按现有项目风格调整，但必须保持：

```text
scripts/*.py 只做入口，不放核心业务逻辑
核心逻辑放 app/manual_execution/hermes_entry/
```

---

## 16. CLI / 测试入口

为了不依赖真实微信调试，22B 应提供最小 CLI 验证入口。

建议：

```text
scripts.parse_manual_execution_intent
scripts.confirm_manual_execution_intent
```

### 16.1 parse CLI

用途：模拟 Hermes 入站自然语言，生成 intent，不直接写 execution_record。

示例：

```bash
python -m scripts.parse_manual_execution_intent \
  --text "advice_id=ADV-xxx，BTCUSDT 多单，成交价 60000，开仓 300U，保证金 100U" \
  --trigger-source cli \
  --confirm-write
```

### 16.2 confirm CLI

用途：模拟用户确认。

示例：

```bash
python -m scripts.confirm_manual_execution_intent \
  --intent-id MEI-xxx \
  --action confirm \
  --trigger-source cli \
  --confirm-write
```

取消：

```bash
python -m scripts.confirm_manual_execution_intent \
  --intent-id MEI-xxx \
  --action cancel \
  --trigger-source cli \
  --confirm-write
```

这些 CLI 只是测试和运维入口，不替代 Hermes 正式入口。

---

## 17. Hermes 入站集成

22B 需要接入 Hermes 入站消息机制。

要求：

1. 必须复用项目现有 Hermes 安全机制，例如 webhook secret / route secret。
2. 不允许开放无鉴权写库入口。
3. 入站 payload 需要记录 trace_id。
4. 入站消息处理失败时必须有日志和中文错误提醒。
5. Hermes 入站只触发 22B intent 流程，不直接调用 22A 写库。

如果当前项目没有可复用的 Hermes 入站路由，应在 plan 范围内新增最小入站 handler，但仍必须满足鉴权和确认机制。

---

## 18. 数据一致性要求

1. intent 写入和 22A 执行写入必须有清晰事务边界。
2. 22A 写库成功但 Hermes 最终回复失败时，不得回滚 22A 数据。
3. 22A 写库成功后，intent 必须记录 executed_manual_position_id / executed_execution_id。
4. 22A 写库失败时，intent 标记 execution_failed，并记录 error_code / error_message。
5. 确认重复到达时，必须根据 intent status 幂等返回，不能重复写入 execution_record。

---

## 19. 测试要求

至少新增 tests/manual_execution_hermes_entry 或类似测试目录。

覆盖：

1. 解析 open_position 成功。
2. 解析 add_position 且 margin_usdt=0 成功。
3. 解析 reduce_position 成功。
4. 解析 close_position 成功。
5. 缺 advice_id 时失败。
6. 缺 price 时失败。
7. 缺 notional_usdt 时失败。
8. open_position 缺 margin_usdt 时失败。
9. add_position 缺 margin_usdt 时失败。
10. reduce_position 带 margin_usdt 时按规则提示或忽略，行为要固定。
11. 解析成功后只生成 intent，不写 execution_record。
12. pending intent 确认后调用 22A 写库。
13. 重复确认不重复写库。
14. 取消 intent 后不能确认写库。
15. 过期 intent 不能确认写库。
16. manual_position_id 错误时不写库，并返回中文错误。
17. 同 symbol+side 多笔 open 且未传 manual_position_id 时 blocked。
18. 不调用大模型。
19. Hermes reply send disabled 时不真实发送，但状态可追踪。
20. 全量 pytest 通过。

---

## 20. 验收标准

22B 通过标准：

1. 用户自然语言执行反馈不会直接写库。
2. 解析成功后会生成 `MEI-xxx` 待确认 intent。
3. 待确认消息中文清晰，能看出动作、价格、金额、保证金、advice_id。
4. 用户确认后才调用 22A 写库。
5. 用户取消后不会写库。
6. intent 过期后不会写库。
7. 重复确认不会重复写 execution_record。
8. 缺字段和错误 manual_position_id 会中文提醒。
9. 22B 不重写 22A 算法。
10. 22B 不调用大模型。
11. 22B 不自动交易、不读取账户。
12. 所有新增测试和全量测试通过。

---

## 21. 后续阶段预留

22B 不做但后续可扩展：

```text
Hermes 自然语言查询 open manual_position
人工执行记录纠错 / 作废 / 修正
Admin 后台
执行反馈复盘
多交易对扩展
大模型辅助解析
更复杂的语义纠错
```

其中“大模型辅助解析”即使以后做，也必须保持确认机制，不能让大模型输出直接写库。
