# 22A 人工执行反馈与人工仓位记录计划

> 阶段：22A  
> 主题：Manual Execution Feedback（人工执行反馈）  
> 状态：根据最新讨论修订  
> 边界：只记录用户主动反馈的人工执行事实，不自动交易，不读取交易所账户，不同步真实仓位。

---

## 1. 阶段目标

22A 用于补齐系统对“用户是否执行建议”的认知缺口。

现有链路已经能生成策略建议：

```text
15 快照
→ 16 策略信号
→ 17 策略调度
→ 18 分析材料
→ 19 大模型审查能力
→ 20 大模型审查编排/复用
→ 21 最终建议与生命周期通知
```

但系统还不知道用户是否真的执行了建议。因此 22A 只做一件事：

```text
结构化记录用户主动反馈的人工开仓、加仓、减仓、平仓、止盈、止损动作，
并基于这些流水计算人工仓位的手续费、账面已实现盈亏、实际净已实现盈亏和按保证金收益率。
```

如果用户没有反馈某条建议，业务上默认该建议未执行 / 放弃。系统不得假设用户已经开仓。

如果用户曾经反馈过开仓，系统中存在 `open` 状态的人工仓位，那么后续某一轮未反馈，只表示“本轮没有新增人工操作”，不代表系统自动关闭仓位。仓位只能由用户反馈 `close_position` / `take_profit` / `stop_loss` 后关闭。

---

## 2. 核心边界

22A 必须禁止：

```text
1. 不自动交易；
2. 不调用交易所下单接口；
3. 不读取 Binance 账户；
4. 不同步 Binance 真实持仓；
5. 不调用大模型；
6. 不做 Hermes 自然语言写库；
7. 不做资金费率计算；
8. 不做滑点字段；
9. 不做浮盈浮亏；
10. 不做移动止损；
11. 不做取消计划；
12. 不做主动不执行记录；
13. 不做 update / delete / void / correction 纠错流程；
14. 不修改 strategy_advice 的生命周期状态；
15. 不把 manual_position 当成交易所真实持仓；
16. 不把 execution_record 当成交易所真实订单。
```

22A 只相信用户主动反馈。实际交易了但没反馈，系统按未执行处理。

---

## 3. 两张核心表

22A 使用两张表。

### 3.1 `strategy_advice_manual_position`

人工仓位汇总表 / 状态表。

一行代表一笔人工仓位，例如：

```text
MP-001：BTCUSDT 多单，从开仓到最终关闭的一整条人工交易链。
```

它负责保存：

```text
当前状态；
当前剩余数量；
平均成本；
累计手续费；
账面已实现盈亏；
实际净已实现盈亏；
保证金基准；
有效杠杆；
开仓想法和后续复盘预留字段。
```

### 3.2 `strategy_advice_execution_record`

人工执行流水表。

一行代表一次人工操作，例如：

```text
EXE-001：开仓 300U；
EXE-002：加仓 500U；
EXE-003：部分减仓 500U；
EXE-004：止盈全平。
```

它负责保存每次具体执行动作的价格、成交金额、手续费、单笔盈亏、参考建议等。

### 3.3 主线关系

```text
manual_position_id
  ├── open_position
  ├── add_position
  ├── reduce_position
  └── close_position / take_profit / stop_loss
```

核心主键是 `manual_position_id`，不是 `advice_id`。

原因：同一笔人工仓位可能跨多个建议版本，例如：

```text
A1 开仓
A3 加仓
B2 平仓
```

每次操作可分别记录参考的 `advice_id`。

---

## 4. 支持的执行动作

`strategy_advice_execution_record.execution_action` 必须表示本次人工执行动作。

22A 支持：

```text
open_position      开仓
add_position       加仓
reduce_position    部分减仓 / 部分止盈
close_position     普通全平
take_profit        止盈全平
stop_loss          止损全平
```

22A 不支持：

```text
move_stop          移动止损
cancel_plan        取消计划
not_executed       主动不执行
reduce_ratio       百分比减仓
partial_take_profit 独立动作
partial_stop_loss   独立动作
```

如果只是部分止盈，使用：

```text
reduce_position
```

并在 `reason` / `note` 中说明“部分止盈”。

---

## 5. advice_id 规则

`advice_id` 表示：

```text
本次人工操作参考了哪条系统建议。
```

22A 规则：

```text
1. advice_id 必填；
2. advice_id 不做唯一约束；
3. 同一个 advice_id 可以被开仓、加仓、减仓、平仓等多条 execution_record 复用；
4. 如果 advice_id 不存在，必须 blocked，不写库；
5. review_id / setup_id 不是用户必填项，系统能根据 advice_id 唯一推导则自动填，推导不了允许为空；
6. 如果同一 advice_id 下存在多个 trade_setup，只有用户明确指定 setup_id 时才绑定 setup_id。
```

---

## 6. manual_position_id 规则

### 6.1 开仓

开仓时用户不传 `manual_position_id`。

系统必须：

```text
1. 新建 strategy_advice_manual_position；
2. 自动生成 manual_position_id；
3. 写入 open_position 执行流水；
4. CLI 输出中返回 manual_position_id。
```

### 6.2 后续操作

以下动作需要绑定已有 `manual_position_id`：

```text
add_position
reduce_position
close_position
take_profit
stop_loss
```

如果用户未传 `manual_position_id`，系统只能在同一 `symbol + side` 下存在唯一一笔 `open` 人工仓位时自动推断。

如果存在多笔匹配的 open 仓位，必须 blocked，不能猜。

### 6.3 传错 manual_position_id

如果用户传入的 `manual_position_id`：

```text
不存在；
已 closed；
symbol 不匹配；
side 不匹配；
action 不允许作用在该仓位上；
```

系统必须：

```text
1. 拒绝写入 execution_record；
2. 不更新 manual_position；
3. 不计算盈亏；
4. 不发送结算回执；
5. 通过 Hermes 发送中文错误提醒；
6. CLI 明确输出 blocked 原因。
```

该 Hermes 提醒属于“人工执行录入失败”，不是交易建议。

---

## 7. 用户输入字段口径

22A 不再要求用户输入杠杆。

用户输入的核心字段：

```text
advice_id        必填，参考建议 ID；
symbol           交易对，例如 BTCUSDT；
side             long / short；
price            实际成交价；
notional_usdt    实际成交名义金额；
margin_usdt      本次操作投入 / 新增保证金，仅 open_position / add_position 使用；
reason           可选，操作原因；
note             可选，备注；
trigger_source   cli / scheduler / systemd 等来源。
```

### 7.1 notional_usdt

`notional_usdt` 表示实际成交名义金额。

例如用户说：

```text
成交金额 300U
```

则：

```text
notional_usdt = 300
```

它不是保证金，不是本金。

程序计算：

```text
quantity_base_asset = notional_usdt / price
fee_usdt = notional_usdt * fee_rate
```

### 7.2 margin_usdt

用户输入层和 `execution_record` 只保留一个保证金字段：

```text
margin_usdt
```

它表示：

```text
本次操作投入 / 新增的保证金。
```

系统根据 `execution_action` 判断语义：

```text
open_position：
  margin_usdt = 本次开仓投入保证金；
  必填；
  必须 > 1U；
  用于初始化 manual_position.margin_basis_usdt。

add_position：
  margin_usdt = 本次加仓新增保证金；
  必填；
  允许 = 0；
  累加到 manual_position.margin_basis_usdt。

reduce_position / close_position / take_profit / stop_loss：
  不需要 margin_usdt；
  不调整 manual_position.margin_basis_usdt。
```

示例：

```text
开仓 300U，保证金 100U：
execution_record.margin_usdt = 100
manual_position.margin_basis_usdt = 100

加仓 500U，保证金 0U：
execution_record.margin_usdt = 0
manual_position.margin_basis_usdt 仍为 100

加仓 200U，保证金 50U：
execution_record.margin_usdt = 50
manual_position.margin_basis_usdt = 原值 + 50
```

---

## 8. 保证金与有效杠杆口径

必须废弃错误方案：

```text
300U / 3 + 200U / 5 = 140U
```

22A 不允许使用“每笔成交保证金 = notional_usdt / leverage 后累加”的方案。

最终口径：

```text
execution_record 记录每次成交事实；
manual_position 维护整笔人工仓位的累计保证金基准 margin_basis_usdt；
effective_leverage 由程序反算。
```

### 8.1 margin_basis_usdt

`manual_position.margin_basis_usdt` 是当前整笔人工仓位的保证金基准。

它由程序维护：

```text
open_position：margin_basis_usdt = 本次 margin_usdt
add_position：margin_basis_usdt = 原 margin_basis_usdt + 本次 margin_usdt
reduce_position：不变
close_position / take_profit / stop_loss：不变，用关闭前最后值计算收益率
```

### 8.2 current_cost_basis_usdt

使用 `current_cost_basis_usdt` 表示：

```text
当前剩余持仓的成本基准金额。
```

它不是实时市值，也不是累计开仓金额。

开仓 / 加仓时增加；减仓 / 平仓时按被减掉仓位的原始成本减少。

示例：

```text
60000 开 300U 多单：
quantity = 300 / 60000 = 0.005 BTC
current_cost_basis_usdt = 300

65000 加仓 200U：
quantity = 200 / 65000 = 0.00307692 BTC
current_cost_basis_usdt = 500

如果 avg_entry_price ≈ 61904.76，在 68000 减仓 100U：
reduce_quantity = 100 / 68000 = 0.00147059 BTC
本次减掉的成本基准 ≈ 61904.76 * 0.00147059 = 91.04U
减仓后 current_cost_basis_usdt ≈ 500 - 91.04 = 408.96U
```

不能简单用：

```text
500 - 100 = 400U
```

因为 `100U` 是退出成交金额，不是这部分仓位的原始成本。

### 8.3 effective_leverage

有效杠杆由程序计算：

```text
effective_leverage = current_cost_basis_usdt / margin_basis_usdt
```

例如：

```text
开仓 300U，保证金 100U：
effective_leverage = 300 / 100 = 3x

后续加仓 500U，新增保证金 0U：
current_cost_basis_usdt = 800
margin_basis_usdt = 100
effective_leverage = 800 / 100 = 8x
```

如果超过用户人工纪律边界，例如 5x，应在 CLI / Hermes 回执中明确提示风险，但不因此自动阻止写库，除非后续另设硬规则。

---

## 9. 平均成本算法

采用加权平均成本法。

### 9.1 open_position

```text
quantity_base_asset = notional_usdt / price
avg_entry_price = price
initial_entry_price = price
current_quantity_base_asset = quantity_base_asset
current_cost_basis_usdt = notional_usdt
```

### 9.2 add_position

```text
new_quantity = notional_usdt / price
new_avg_entry_price =
  (old_quantity * old_avg_entry_price + notional_usdt)
  / (old_quantity + new_quantity)

current_quantity_base_asset = old_quantity + new_quantity
current_cost_basis_usdt = old_current_cost_basis_usdt + notional_usdt
```

### 9.3 reduce_position

部分减仓不改变 `avg_entry_price`。

```text
reduce_quantity = notional_usdt / price
cost_basis_reduced = avg_entry_price * reduce_quantity
current_quantity_base_asset = old_quantity - reduce_quantity
current_cost_basis_usdt = old_current_cost_basis_usdt - cost_basis_reduced
```

多单本次账面盈亏：

```text
gross_pnl_usdt = (price - avg_entry_price) * reduce_quantity
```

空单本次账面盈亏：

```text
gross_pnl_usdt = (avg_entry_price - price) * reduce_quantity
```

### 9.4 close_position / take_profit / stop_loss

全平不需要用户传 `notional_usdt`。

程序使用当前剩余数量：

```text
exit_quantity = current_quantity_base_asset
close_notional_usdt = exit_quantity * price
fee_usdt = close_notional_usdt * fee_rate
```

多单账面盈亏：

```text
gross_pnl_usdt = (price - avg_entry_price) * exit_quantity
```

空单账面盈亏：

```text
gross_pnl_usdt = (avg_entry_price - price) * exit_quantity
```

关闭后：

```text
current_quantity_base_asset = 0
current_cost_basis_usdt = 0
status = closed
close_price = price
closed_at_utc = now 或用户提供的 executed_at_utc
```

---

## 10. 手续费

手续费率放 env / config，不能写死在业务代码中。

默认：

```env
MANUAL_EXECUTION_FEE_RATE=0.0002
```

每一次成交都必须计算手续费：

```text
fee_usdt = 本次成交名义金额 * fee_rate
```

包括：

```text
open_position
add_position
reduce_position
close_position
take_profit
stop_loss
```

不能只扣最后一次平仓手续费。

---

## 11. 盈亏口径

22A 区分两种盈亏：

```text
gross_realized_pnl_usdt
账面已实现盈亏，不扣手续费。

net_realized_pnl_usdt
实际净已实现盈亏，扣全部手续费。
```

仓位汇总净盈亏按：

```text
manual_position.net_realized_pnl_usdt
= manual_position.gross_realized_pnl_usdt - manual_position.total_fee_usdt
```

必须避免重复扣手续费。

允许 `execution_record` 保存单笔：

```text
fee_usdt
gross_pnl_usdt
net_pnl_usdt
```

但汇总时不得：

```text
SUM(execution_record.net_pnl_usdt) 后再次减 total_fee_usdt
```

---

## 12. 收益率

22A 只保留一种收益率：

```text
net_pnl_ratio_on_margin
```

计算：

```text
net_pnl_ratio_on_margin = net_realized_pnl_usdt / margin_basis_usdt
```

不保存按名义金额口径收益率。

如果 `margin_basis_usdt <= 0`，收益率必须 blocked 或置空，并写明原因。

---

## 13. 多空支持

22A 必须同时支持：

```text
long   多单
short  空单
```

多单已实现账面盈亏：

```text
(exit_price - avg_entry_price) * exit_quantity_base_asset
```

空单已实现账面盈亏：

```text
(avg_entry_price - exit_price) * exit_quantity_base_asset
```

手续费独立扣除。

---

## 14. 建议表字段方向

### 14.1 `strategy_advice_manual_position`

建议字段至少包含：

```text
id
manual_position_id

symbol
side
status                       open / closed

opened_at_utc
closed_at_utc

opened_by_advice_id
latest_related_advice_id
closed_by_advice_id

initial_entry_price
avg_entry_price
close_price

current_quantity_base_asset
current_cost_basis_usdt
margin_basis_usdt
effective_leverage

total_open_notional_usdt
total_close_notional_usdt
total_fee_usdt

gross_realized_pnl_usdt
net_realized_pnl_usdt
net_pnl_ratio_on_margin

open_reason
open_decision_context
review_status
review_summary
review_correctness

trigger_source
created_by
trace_id
created_at_utc
updated_at_utc

is_manual
auto_trading_allowed
```

`status` 只需要：

```text
open
closed
```

不做 `partially_closed`。

### 14.2 `strategy_advice_execution_record`

建议字段至少包含：

```text
id
execution_id
manual_position_id

execution_action
symbol
side

price
notional_usdt
quantity_base_asset

margin_usdt
fee_rate
fee_usdt
gross_pnl_usdt
net_pnl_usdt

advice_id
review_id
setup_id
advice_resolution_method
setup_resolution_method

reason
note
executed_at_utc
trigger_source
created_by
trace_id
created_at_utc

is_manual
auto_trading_allowed
```

`margin_usdt` 只在：

```text
open_position
add_position
```

中使用。

---

## 15. total_open_notional_usdt / total_close_notional_usdt 用途

它们属于同一行 `manual_position` 汇总数据。

示例：

```text
开仓 300U
加仓 500U
减仓 500U
```

则：

```text
total_open_notional_usdt = 800
total_close_notional_usdt = 500
```

用途：

```text
1. 展示仓位累计开仓规模；
2. 展示累计退出规模；
3. 校验手续费；
4. 审计 execution_record 汇总是否一致；
5. 后续复盘判断是否加仓过重；
6. 平仓回执展示操作链。
```

它们不是利润。不能用：

```text
total_close_notional_usdt - total_open_notional_usdt
```

直接算盈亏。

盈亏必须按：

```text
价格差 * 数量
```

计算。

---

## 16. Hermes 回执

### 16.1 关闭仓位结算回执

当以下动作导致 `manual_position.status = closed` 时：

```text
close_position
take_profit
stop_loss
```

脚本必须立即：

```text
1. 写入 execution_record；
2. 更新 manual_position；
3. 计算总手续费、账面已实现盈亏、实际净已实现盈亏、收益率；
4. 整理操作链和对应 advice_id；
5. 通过 Hermes 发送中文结算回执。
```

Hermes 回执使用独立 env 开关：

```env
MANUAL_EXECUTION_RECEIPT_SEND_ENABLED=false
```

回执内容先做基础占位版，包含：

```text
manual_position_id
symbol / side
开仓价 / 平均成本 / 平仓价
总开仓金额
总退出金额
总手续费
账面已实现盈亏
实际净已实现盈亏
按保证金收益率
操作链概要
关联 advice_id 列表
```

后期会统一调整 Hermes 模板。

### 16.2 回执失败处理

如果 execution_record 和 manual_position 已经写入成功，但 Hermes 回执发送失败：

```text
1. 不回滚数据库；
2. 写 alert_message / event / log；
3. CLI 输出必须明确提示：数据库已写入，但 Hermes 回执失败。
```

### 16.3 manual_position_id 错误提醒

如果用户传错 `manual_position_id`，必须通过 Hermes 发送中文错误提醒。该提醒不属于交易建议。

---

## 17. CLI 范围

22A 只做 CLI，不接 Hermes 自然语言输入。

建议入口：

```text
scripts.record_manual_execution
scripts.check_manual_positions
```

### 17.1 记录人工执行

示例：

```bash
python -m scripts.record_manual_execution \
  --action open_position \
  --advice-id ADV-111 \
  --symbol BTCUSDT \
  --side long \
  --price 60000 \
  --notional-usdt 300 \
  --margin-usdt 100 \
  --trigger-source cli \
  --confirm-write
```

加仓示例：

```bash
python -m scripts.record_manual_execution \
  --action add_position \
  --manual-position-id MP-xxx \
  --advice-id ADV-111 \
  --symbol BTCUSDT \
  --side long \
  --price 60000 \
  --notional-usdt 500 \
  --margin-usdt 0 \
  --trigger-source cli \
  --confirm-write
```

减仓示例：

```bash
python -m scripts.record_manual_execution \
  --action reduce_position \
  --manual-position-id MP-xxx \
  --advice-id ADV-111 \
  --symbol BTCUSDT \
  --side long \
  --price 60000 \
  --notional-usdt 500 \
  --trigger-source cli \
  --confirm-write
```

全平示例：

```bash
python -m scripts.record_manual_execution \
  --action close_position \
  --manual-position-id MP-xxx \
  --advice-id ADV-111 \
  --symbol BTCUSDT \
  --side long \
  --price 62000 \
  --trigger-source cli \
  --confirm-write
```

### 17.2 查询 open 人工仓位

示例：

```bash
python -m scripts.check_manual_positions \
  --symbol BTCUSDT \
  --status open \
  --trigger-source cli
```

最小输出字段：

```text
manual_position_id
symbol
side
status
avg_entry_price
current_quantity_base_asset
current_cost_basis_usdt
margin_basis_usdt
effective_leverage
opened_at_utc
opened_by_advice_id
```

Hermes 自然语言查询“当前还有几笔 manual_position”放后续阶段，不属于 22A。

---

## 18. 参数校验规则

### 18.1 通用校验

```text
price > 0
notional_usdt > 0，除 close_position / take_profit / stop_loss 不需要 notional_usdt
fee_rate >= 0
symbol 非空
side in long / short
advice_id 必填且存在
trigger_source 必填
confirm-write 未传时不得写库
所有金额、价格、数量计算必须使用 Decimal，不得使用 float
```

### 18.2 open_position

必须：

```text
advice_id
symbol
side
price
notional_usdt
margin_usdt
```

校验：

```text
margin_usdt > 1
```

系统创建新的 `manual_position_id`。

### 18.3 add_position

必须：

```text
manual_position_id 或可唯一推断的 open 仓位
advice_id
symbol
side
price
notional_usdt
margin_usdt
```

校验：

```text
margin_usdt >= 0
manual_position.status = open
symbol / side 匹配
```

### 18.4 reduce_position

必须：

```text
manual_position_id 或可唯一推断的 open 仓位
advice_id
symbol
side
price
notional_usdt
```

校验：

```text
notional_usdt > 0
reduce_quantity = notional_usdt / price
reduce_quantity <= current_quantity_base_asset
不接受 margin_usdt
不调整 margin_basis_usdt
```

### 18.5 close_position / take_profit / stop_loss

必须：

```text
manual_position_id 或可唯一推断的 open 仓位
advice_id
symbol
side
price
```

校验：

```text
manual_position.status = open
current_quantity_base_asset > 0
不需要 notional_usdt
不接受 margin_usdt
```

---

## 19. 示例链路

### 19.1 开仓

输入：

```text
advice_id=ADV-111，BTCUSDT 多单，成交价 60000，成交金额 300U，保证金 100U
```

系统计算：

```text
quantity_base_asset = 300 / 60000 = 0.005 BTC
fee_usdt = 300 * 0.0002 = 0.06U
avg_entry_price = 60000
current_cost_basis_usdt = 300
margin_basis_usdt = 100
effective_leverage = 300 / 100 = 3x
```

### 19.2 加仓

输入：

```text
加仓 advice_id=ADV-111，BTCUSDT 多单，成交价 60000，成交金额 500U，保证金 0U
```

假设原仓位：

```text
current_quantity_base_asset = 0.005 BTC
avg_entry_price = 60000
current_cost_basis_usdt = 300
margin_basis_usdt = 100
```

系统计算：

```text
new_quantity = 500 / 60000 = 0.00833333 BTC
fee_usdt = 500 * 0.0002 = 0.10U
current_quantity_base_asset = 0.01333333 BTC
avg_entry_price = 60000
current_cost_basis_usdt = 800
margin_basis_usdt = 100 + 0 = 100
effective_leverage = 800 / 100 = 8x
```

如果人工纪律边界是 5x，CLI / Hermes 回执应提示：

```text
当前有效杠杆 8x，超过 5x 风险纪律边界。
```

### 19.3 减仓

输入：

```text
减仓 advice_id=ADV-111，BTCUSDT 多单，成交价 60000，成交金额 500U
```

系统计算：

```text
reduce_quantity = 500 / 60000 = 0.00833333 BTC
cost_basis_reduced = avg_entry_price * reduce_quantity = 60000 * 0.00833333 = 500U
gross_pnl_usdt = 0U
fee_usdt = 500 * 0.0002 = 0.10U
net_pnl_usdt = -0.10U
current_quantity_base_asset = 0.005 BTC
current_cost_basis_usdt = 300U
margin_basis_usdt = 100U，不变
effective_leverage = 300 / 100 = 3x
```

---

## 20. 测试要求

至少覆盖：

```text
1. open_position 成功写入；
2. open_position 缺 advice_id blocked；
3. open_position margin_usdt <= 1 blocked；
4. add_position margin_usdt = 0 成功；
5. add_position margin_usdt > 0 成功；
6. add_position manual_position_id 错误 blocked，并生成 Hermes 错误提醒；
7. reduce_position 成功，且不改变 avg_entry_price；
8. reduce_position 盈利场景；
9. reduce_position 亏损场景；
10. reduce_position 数量超过当前持仓 blocked；
11. close_position 成功关闭仓位；
12. take_profit 成功关闭仓位；
13. stop_loss 成功关闭仓位；
14. long 盈亏公式；
15. short 盈亏公式；
16. 多次开仓/加仓/减仓手续费全部计入 total_fee_usdt；
17. net_realized_pnl_usdt = gross_realized_pnl_usdt - total_fee_usdt；
18. 关闭仓位后 Hermes 回执开关关闭时不真实发送；
19. Hermes 回执失败不回滚数据库；
20. check_manual_positions 能查询 open 仓位；
21. 同一 symbol + side 多笔 open 仓位时不允许自动猜 manual_position_id；
22. 所有金额、数量、手续费、盈亏计算使用 Decimal。
```

---

## 21. 验收标准

22A 验收通过需要满足：

```text
1. Alembic migration 可执行；
2. 两张表结构符合计划；
3. CLI 能记录 open/add/reduce/close/take_profit/stop_loss；
4. 开仓成功后返回 manual_position_id；
5. 加仓使用同一个 margin_usdt 字段，margin_usdt=0 可正确提高有效杠杆；
6. 不存在 margin_delta_usdt / 用户输入层 margin_basis_usdt 两套保证金字段；
7. manual_position.margin_basis_usdt 由程序维护；
8. reduce_position 不调整保证金基准；
9. close_position / take_profit / stop_loss 自动按剩余数量全平；
10. 手续费按每笔成交独立计算并汇总；
11. 平均成本计算正确；
12. 部分减仓不改变平均成本；
13. 账面盈亏和实际净盈亏区分正确；
14. 多空公式正确；
15. 关闭仓位后生成基础 Hermes 结算回执；
16. Hermes 回执失败不回滚数据库；
17. 查询 CLI 可列出 open manual_position；
18. 不实现 update/delete/void/correction；
19. 不接 Hermes 自然语言输入；
20. 不自动交易、不读账户、不调用大模型。
```

---

## 22. 给 Codex 的特别提醒

1. 不要使用“每笔 notional_usdt / leverage 后累加保证金”的旧方案。
2. 不要让用户同时输入 `margin_basis_usdt` 和 `margin_delta_usdt`。
3. 用户输入和 execution_record 中只保留一个 `margin_usdt`。
4. `execution_action` 决定 `margin_usdt` 的业务含义。
5. `manual_position.margin_basis_usdt` 是程序维护的汇总字段。
6. `current_cost_basis_usdt` 是剩余仓位成本基准，不是实时市值。
7. 减仓时减少的是成本基准，不是直接用退出成交金额抵扣成本。
8. 关闭仓位前必须确保所有手续费已经汇总进 `total_fee_usdt`。
9. 金额计算必须用 Decimal，禁止 float。
10. 22A 是人工执行反馈，不是订单系统。
