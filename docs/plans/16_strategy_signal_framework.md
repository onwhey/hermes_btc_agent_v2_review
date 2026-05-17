# Plan 16：Strategy Signal Framework 策略信号框架

## 1. 阶段定位

第 16 阶段目标是建立 **策略信号框架**。

本阶段基于第 15 阶段的 `MarketContextSnapshot` 市场上下文快照，完成以下链路：

```text
确保拿到最新合格 MarketContextSnapshot
↓
还原 base / higher K线窗口
↓
构造 StrategyEvaluationInput 策略评估输入
↓
加载多个独立策略
↓
逐个运行策略
↓
生成每个策略自己的 StrategySignal 策略信号
↓
写入 strategy_signal_run / strategy_signal_result
```

本阶段只生成 **独立策略信号**，不生成最终交易建议。

本阶段不是：

```text
最终交易建议系统
建议生命周期系统
大模型分析系统
策略复盘系统
自动交易系统
```

---

## 2. 和第 15 阶段的关系

第 15 阶段负责：

```text
生成 MarketContextSnapshot
记录某次分析使用的 K线窗口范围
```

第 16 阶段负责：

```text
使用 MarketContextSnapshot
还原 K线窗口
运行策略信号
```

第 16 阶段不得绕过第 15 阶段直接查询最新 K线。

正确关系：

```text
MarketContextSnapshot
↓
StrategyEvaluationInput
↓
StrategyRunner
↓
StrategySignal
```

错误关系：

```text
策略自己查最新 K线
策略自己请求 Binance
策略自己生成 K线窗口
策略自己修复数据
```

这些都禁止。

---

## 3. 快照懒生成策略

第 16 阶段不安排单独的 `MarketContextSnapshot` 定时任务。

快照采用 **懒生成** 策略：

```text
策略信号运行时
↓
先 ensure_latest_snapshot
↓
有合格快照则复用
↓
没有合格快照且非 dry-run、confirm-write 时才调用 MarketContextSnapshotService 生成
↓
生成成功才运行策略
↓
生成 blocked / failed 则策略运行 blocked
```

也就是说：

```text
快照服务保留
快照 CLI 保留
但不单独定时生成快照
```

后续 scheduler 不应该安排：

```text
04:05 单独快照任务
04:06 策略任务
```

更合理的未来调度链路是：

```text
采集 4h K线
↓
复核 4h / 1d
↓
运行策略信号任务
  ↓
  ensure_latest_snapshot
  ↓
  run strategies
```

---

## 4. ensure_latest_snapshot 机制

### 4.0 dry-run 与 confirm-write 边界（审查修正）

`ensure_latest_snapshot` 必须先查找是否存在可复用的最新 `created` 状态快照。

如果存在可复用快照，直接复用该 `snapshot_id`，不创建新的 `MarketContextSnapshot`。

如果不存在可复用快照：

```text
dry-run = true
    -> 返回 blocked
    -> blocked_reason = snapshot_creation_requires_confirm_write
    -> 不调用 MarketContextSnapshotService
    -> 不写 market_context_snapshot

confirm_write = false
    -> 返回 blocked
    -> blocked_reason = snapshot_creation_requires_confirm_write
    -> 不调用 MarketContextSnapshotService
    -> 不写 market_context_snapshot

dry-run = false 且 confirm_write = true
    -> 才允许调用 MarketContextSnapshotService 懒生成 MarketContextSnapshot
```

dry-run 绝不能产生数据库写入副作用：不写 `strategy_signal_run`，不写 `strategy_signal_result`，也不得通过 `ensure_latest_snapshot` 间接创建 `MarketContextSnapshot`。

第 16 阶段必须实现 `ensure_latest_snapshot` 机制。

它的职责是：

```text
确保策略运行使用的是覆盖最新已收盘 K线的 created 状态快照
```

### 4.1 基本规则

当用户没有显式指定 `snapshot_id`，而是运行实时策略信号时，系统必须：

1. 计算当前理论上应使用的最新已收盘 base 周期 K线 open_time。
2. 计算当前理论上应使用的最新已收盘 higher 周期 K线 open_time。
3. 查询是否已有可复用快照。
4. 如果存在合格快照，直接复用。
5. 如果不存在合格快照，dry-run 或未确认写入时返回 blocked；只有非 dry-run 且 `confirm_write=True` 时才调用 `MarketContextSnapshotService` 生成。
6. 如果生成结果为 `created`，继续运行策略。
7. 如果生成结果为 `blocked` 或 `failed`，策略运行直接 `blocked`。
8. 不允许回退使用旧快照。

### 4.2 可复用快照条件

可复用快照必须同时满足：

```text
status = created
symbol 相同
base_interval_value 相同
higher_interval_value 相同
lookback_base_count 相同
lookback_higher_count 相同
end_base_open_time_ms 覆盖当前理论最新已收盘 base K线 open_time_ms
end_higher_open_time_ms 覆盖当前理论最新已收盘 higher K线 open_time_ms
base / higher 数据质量状态通过
快照可成功还原 K线窗口
还原出的 K线数量等于 actual_count
```

当前默认：

```text
base_interval_value = 4h
higher_interval_value = 1d
```

但代码不得把策略框架写死为 4h。

### 4.3 不允许使用旧快照

如果 4:00 已经产生新的 4h 收盘 K线，而 4:02 策略运行时新 K线尚未采集或复核，则策略运行必须：

```text
status = blocked
blocked_reason = snapshot_not_ready
```

不得使用 00:00 前一批旧快照继续运行策略。

### 4.4 幂等复用

`ensure_latest_snapshot` 不能每次都新建快照。

必须先查找是否已有合格快照。

如果已有：

```text
直接复用 snapshot_id
不重复创建
```

如果没有：

```text
非 dry-run 且 confirm-write 时再调用 MarketContextSnapshotService
```

这样避免：

```text
04:05 已经生成快照
04:06 策略运行又生成一份完全相同快照
```

### 4.5 并发防重

虽然当前阶段主要是手动运行，但仍应预留简单防重能力。

风险来源包括：

```text
人工重复执行
scheduler 补跑
任务未结束又被再次触发
未来多实例部署
重试机制
```

本阶段至少应做到：

```text
同一个 symbol + base_interval + higher_interval + end_base_open_time_ms + end_higher_open_time_ms + lookback 配置
优先复用已有 created 快照
```

如项目已有锁机制，可在 `ensure_latest_snapshot` 中使用轻量锁。

推荐锁语义：

```text
snapshot_ensure:{symbol}:{base_interval}:{higher_interval}:{end_base_open_time_ms}:{end_higher_open_time_ms}
```

拿不到锁时，不应直接生成第二份快照。可以等待短时间后重新查询已有快照。

---

## 5. snapshot_id 模式和实时模式

第 16 阶段 CLI 应支持两类入口。

### 5.1 指定 snapshot_id 模式

用于测试、人工验证、历史复盘。

示例：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id MCS-BTCUSDT-4H-1D-xxxx \
  --trigger-source cli \
  --dry-run
```

正式写入：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id MCS-BTCUSDT-4H-1D-xxxx \
  --trigger-source cli \
  --confirm-write
```

要求：

1. 必须校验 snapshot 存在。
2. 必须校验 snapshot.status = created。
3. 必须校验 snapshot 可还原 K线窗口。
4. 必须校验还原数量等于 snapshot actual_count。
5. 该模式允许用于历史复盘。
6. 不得请求 Binance。
7. 不得修改 K线表。

### 5.2 ensure latest snapshot 模式

用于实时策略信号运行。

示例：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

正式写入：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

要求：

1. `--snapshot-id` 与 `--ensure-latest-snapshot` 二选一。
2. 有合格快照则复用。
3. 无合格快照才调用快照服务生成。
4. 快照生成 blocked / failed 时策略运行 blocked。
5. 不允许回退旧快照。

---

## 6. 多周期抽象原则

当前默认运行：

```text
base_interval_value = 4h
higher_interval_value = 1d
```

含义：

```text
4h = 当前主判断周期
1d = 高一级背景周期
```

但策略框架不得写死 4h。

核心命名应使用：

```text
base_interval_value
higher_interval_value
base_klines
higher_klines
```

不要在框架核心层使用：

```text
klines_4h
klines_1d
```

后续如果发现 4h 不适合作为主周期，可以扩展为：

```text
base_interval_value = 1d
higher_interval_value = 1w
```

或者：

```text
base_interval_value = 1d
lower_interval_value = 4h
```

第 16 阶段不实现 1w，但接口不能锁死 4h。

---

## 7. 第 16 阶段不得重构第 15 阶段表结构

第 15 阶段 `MarketContextSnapshot` 已经合并到 master，本阶段不得为了追求通用命名去重构第 15 阶段数据库字段。

当前第 15 阶段快照表仍然使用：

```text
start_4h_open_time_ms
end_4h_open_time_ms
actual_4h_count
start_1d_open_time_ms
end_1d_open_time_ms
actual_1d_count
```

第 16 阶段内部可以使用 `base_interval_value` / `higher_interval_value` / `base_klines` / `higher_klines` 抽象，但当前默认映射为：

```text
base_interval_value = 4h -> 使用第 15 阶段 4h 字段
higher_interval_value = 1d -> 使用第 15 阶段 1d 字段
```

禁止在第 16 阶段修改 `market_context_snapshot` 表，把 4h / 1d 字段强行重构成 base / higher 字段。

如果未来真的需要支持 1d + 1w 或更多周期，应单独开新阶段评估快照表结构演进。

---

## 8. ensure_latest_snapshot 不负责采集和复核

`ensure_latest_snapshot` 只负责确保拿到最新合格快照。

它允许：

1. 查询已有 created 状态快照。
2. 复用合格快照。
3. 在非 dry-run 且 confirm-write 时调用 `MarketContextSnapshotService` 生成快照。
4. 在快照 blocked / failed 时返回策略运行 blocked。

它不允许：

1. 调用 K线采集服务。
2. 调用 K线回补服务。
3. 调用 K线复核脚本。
4. 调用 Binance REST。
5. 修改正式 K线表。
6. 为了让策略能跑而自动补数据。

如果 4h / 1d 数据尚未采集或复核，`ensure_latest_snapshot` 必须返回 blocked，而不是自行补数据。

也就是说，第 16 阶段不是采集编排器。采集和复核仍然属于前置数据链路。

---

## 9. 最新已收盘 K线计算必须复用现有规则

`ensure_latest_snapshot` 需要判断快照是否覆盖当前理论最新已收盘 K线。

该计算必须遵守第 15 阶段和项目既有时间规则：

1. 业务判断以 UTC 为准。
2. PRC 时间只用于展示。
3. 不能在多个模块里手写零散时间计算。
4. 优先复用第 15 阶段已有的 snapshot readiness / latest closed Kline 计算逻辑。
5. 如果需要抽公共函数，应放在合适的 core 或 market_context 工具模块中。

禁止在策略模块里直接硬编码“当前小时减 4”之类的时间计算。

---

## 10. 并发锁是可选增强，不得扩大本阶段范围

第 16 阶段必须实现幂等复用：

```text
同一个 symbol + base_interval + higher_interval + end_base_open_time_ms + end_higher_open_time_ms + lookback
如果已有合格 created 快照，应直接复用，不重复创建。
```

如果项目已有通用锁能力，可以在 `ensure_latest_snapshot` 中使用轻量锁防重。

如果没有现成锁能力，本阶段不强制新增复杂分布式锁系统。可以先通过“先查可复用快照，再生成，再次查询确认”的方式降低重复生成概率。

不得因为并发防重，在第 16 阶段引入过重的任务队列、分布式调度或新基础设施。

---

## 11. 本阶段允许做的事情

本阶段允许：

1. 新增 `app/strategy/`。
2. 新增 `BaseStrategy` 策略基类。
3. 新增 `StrategyEvaluationInput` 策略输入对象。
4. 新增 `StrategySignal` 策略信号对象。
5. 新增 `StrategyRegistry` 策略注册器。
6. 新增 `StrategyRunner` 策略运行器。
7. 新增 `StrategyInputBuilder` 策略输入构建器。
8. 新增 `StrategySignalService` 策略信号服务。
9. 新增 `SnapshotResolver` / `ensure_latest_snapshot` 快照确保机制。
10. 新增策略配置文件。
11. 新增最小可运行策略。
12. 新增策略信号运行表。
13. 新增策略信号结果表。
14. 新增手动 CLI 入口。
15. 新增测试。
16. 新增实现文档。

---

## 12. 本阶段禁止做的事情

本阶段禁止：

1. 不生成最终交易建议。
2. 不生成开仓建议。
3. 不生成平仓建议。
4. 不生成止盈建议。
5. 不生成止损建议。
6. 不生成仓位建议。
7. 不生成杠杆建议。
8. 不调用 DeepSeek。
9. 不调用 GPT。
10. 不调用 Claude。
11. 不调用任何大模型。
12. 不发送 Hermes 策略提醒。
13. 不读取账户。
14. 不读取持仓。
15. 不自动交易。
16. 不请求 Binance REST。
17. 不请求 Binance WebSocket。
18. 不修改 `market_kline_4h`。
19. 不修改 `market_kline_1d`。
20. 不修复 K线。
21. 不自动回补 K线。
22. 不人工改数。
23. 不接入 scheduler。
24. 不让 scripts 承载核心逻辑。
25. 不创建建议生命周期表。
26. 不创建策略复盘表。
27. 不创建大模型分析表。
28. 不创建关键证据 K线表。

---

## 13. 核心模块设计

建议新增目录：

```text
app/strategy/
  __init__.py
  base.py
  types.py
  registry.py
  runner.py
  input_builder.py
  signal_service.py
  result_repository.py
  snapshot_resolver.py
  strategies/
    __init__.py
    trend_structure_strategy.py
    volatility_risk_strategy.py
    gann_placeholder_strategy.py
```

---

## 14. StrategyEvaluationInput

`StrategyEvaluationInput` 是策略运行的唯一输入对象。

建议字段：

```text
snapshot_id
symbol
base_interval_value
higher_interval_value
base_klines
higher_klines
lookback_base_count
lookback_higher_count
latest_base_open_time_ms
latest_higher_open_time_ms
base_start_open_time_ms
base_end_open_time_ms
higher_start_open_time_ms
higher_end_open_time_ms
base_quality_check_id
higher_quality_check_id
trace_id
evaluated_at_utc
```

要求：

1. `base_klines` 当前默认来自 4h。
2. `higher_klines` 当前默认来自 1d。
3. 策略只能使用该输入。
4. 策略不得自己查 K线。
5. 策略不得请求 Binance。
6. 策略不得判断是否需要回补。

---

## 15. StrategySignal

`StrategySignal` 是单个策略的独立输出。

它不是最终建议。

建议字段：

```text
strategy_name
strategy_version
strategy_status
direction_bias
risk_level
signal_strength
reason_codes
reason_text
metrics
debug_info
trace_id
```

---

## 16. strategy_status

建议枚举：

```text
success
no_signal
invalid
not_implemented
failed
```

含义：

```text
success          策略成功输出信号
no_signal        策略正常运行，但没有明确倾向
invalid          输入不满足策略条件
not_implemented  策略占位，尚未实现
failed           策略运行异常
```

---

## 17. direction_bias

方向倾向不是交易建议。

建议枚举：

```text
bullish_bias      偏多倾向
bearish_bias      偏空倾向
neutral           中性
mixed             混合
unknown           未知
not_applicable    不适用
```

禁止使用：

```text
long
short
buy
sell
open_position
close_position
```

---

## 18. risk_level

建议枚举：

```text
low              低风险
medium           中等风险
high             高风险
extreme          极端风险
unknown          未知
not_applicable   不适用
```

---

## 19. signal_strength

建议使用：

```text
0.0 ~ 1.0
```

含义：

```text
0.0 = 没有信号
1.0 = 当前策略自身认为信号很强
```

注意：这是单策略强度，不是最终交易置信度。

---

## 20. BaseStrategy

`BaseStrategy` 负责定义统一策略接口。

建议接口：

```python
class BaseStrategy:
    strategy_name: str
    strategy_version: str

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
        ...
```

要求：

1. 每个策略必须有稳定 `strategy_name`。
2. 每个策略必须有明确 `strategy_version`。
3. 每个策略只能输出自己的独立信号。
4. 策略不得写数据库。
5. 策略不得发送 Hermes。
6. 策略不得调用大模型。
7. 策略不得请求 Binance。
8. 策略不得读取账户或持仓。
9. 策略不得生成最终交易建议。

---

## 21. StrategyRegistry

`StrategyRegistry` 负责加载和注册策略。

职责：

1. 根据配置加载启用策略。
2. 校验策略名称唯一。
3. 校验策略版本存在。
4. 校验策略对象继承 `BaseStrategy`。
5. 返回策略列表给 `StrategyRunner`。

禁止：

1. 不写具体策略逻辑。
2. 不写数据库逻辑。
3. 不写策略聚合逻辑。
4. 不输出最终建议。

---

## 22. StrategyRunner

`StrategyRunner` 负责运行一批策略。

职责：

1. 接收 `StrategyEvaluationInput`。
2. 从 registry 获取策略列表。
3. 逐个运行策略。
4. 捕获单个策略异常。
5. 保证一个策略失败不影响其他策略。
6. 汇总每个策略的 `StrategySignal`。
7. 返回结构化运行结果。

运行状态建议：

```text
success
partial_success
blocked
failed
```

规则：

```text
全部策略成功 => success
部分策略成功，部分 failed / invalid / not_implemented => partial_success
全部策略失败 => failed
输入不合法或 snapshot 不可还原 => blocked
```

---

## 23. StrategyInputBuilder

`StrategyInputBuilder` 负责从 `snapshot_id` 构造 `StrategyEvaluationInput`。

职责：

1. 读取 `MarketContextSnapshot`。
2. 调用快照还原方法读取 base / higher K线窗口。
3. 校验还原数量等于 snapshot 记录的 actual_count。
4. 校验 K线按 open_time_ms 升序排列。
5. 校验 base_interval / higher_interval 与配置一致。
6. 构造 `StrategyEvaluationInput`。

禁止：

1. 不请求 Binance。
2. 不修复 K线。
3. 不回补 K线。
4. 不跳过 snapshot。
5. 不生成策略结论。

---

## 24. SnapshotResolver

建议新增：

```text
app/strategy/snapshot_resolver.py
```

职责：

1. 实现 `ensure_latest_snapshot`。
2. 先查可复用快照。
3. 没有合格快照时，dry-run / 未确认写入返回 blocked，非 dry-run 且 confirm-write 才调用 `MarketContextSnapshotService`。
4. 处理 snapshot blocked / failed。
5. 返回可用 snapshot_id 或 blocked 结果。
6. 不运行策略。
7. 不请求 Binance。
8. 不修改 K线表。

`SnapshotResolver` 是第 16 阶段连接第 15 阶段的边界模块。

---

## 25. 初始策略

本阶段只做三个最小策略，用于验证框架。

### 25.1 TrendStructureStrategy

文件：

```text
app/strategy/strategies/trend_structure_strategy.py
```

职责：

```text
基于 base_klines 判断基础趋势结构倾向
```

允许使用简单指标：

1. 最近收盘价相对均线位置。
2. 最近高低点结构。
3. 收盘价处于近期区间的位置。
4. 近期波动是否异常。

输出示例：

```text
direction_bias = bullish_bias
risk_level = medium
signal_strength = 0.62
reason_codes = ["close_above_mid_ma", "higher_low_structure"]
reason_text = "最近收盘价位于中期均线上方，且低点结构未继续下移，趋势结构偏多。"
```

禁止输出：

```text
建议做多
建议开仓
建议买入
建议加仓
止损放在某价
止盈看到某价
```

### 25.2 VolatilityRiskStrategy

文件：

```text
app/strategy/strategies/volatility_risk_strategy.py
```

职责：

```text
识别当前 base 周期波动是否过高，是否存在风险放大
```

允许输出：

```text
risk_level = high
direction_bias = not_applicable
signal_strength = 0.70
reason_codes = ["range_expansion", "recent_volatility_elevated"]
reason_text = "近期 K线波动区间明显扩大，波动风险升高。"
```

禁止输出：

```text
停止交易
必须空仓
禁止开仓
```

这些属于后续聚合层或建议层。

### 25.3 GannPlaceholderStrategy

文件：

```text
app/strategy/strategies/gann_placeholder_strategy.py
```

职责：

```text
保留江恩策略扩展位，但不伪造江恩分析
```

输出：

```text
strategy_status = not_implemented
direction_bias = not_applicable
risk_level = not_applicable
signal_strength = 0.0
reason_codes = ["gann_strategy_not_implemented"]
reason_text = "江恩策略尚未实现，本阶段仅保留策略扩展位，不输出江恩判断。"
```

禁止：

1. 不写假江恩。
2. 不用均线冒充江恩。
3. 不输出江恩买卖点。
4. 不输出江恩时间窗口。
5. 不输出江恩价格目标。

---

## 26. 配置文件

建议新增：

```text
configs/strategies/
  strategy_registry.yaml
  trend_structure_strategy.yaml
  volatility_risk_strategy.yaml
  gann_placeholder_strategy.yaml
```

### 26.1 strategy_registry.yaml

示例：

```yaml
enabled_strategies:
  - trend_structure
  - volatility_risk
  - gann_placeholder

default_base_interval: 4h
default_higher_interval: 1d
```

### 26.2 trend_structure_strategy.yaml

示例：

```yaml
strategy_name: trend_structure
strategy_version: v1
enabled: true
base_interval_value: 4h
higher_interval_value: 1d
ma_short_period: 20
ma_mid_period: 60
min_required_base_klines: 120
min_required_higher_klines: 120
```

### 26.3 volatility_risk_strategy.yaml

示例：

```yaml
strategy_name: volatility_risk
strategy_version: v1
enabled: true
base_interval_value: 4h
higher_interval_value: 1d
lookback_period: 30
high_volatility_percentile: 0.80
extreme_volatility_percentile: 0.95
```

### 26.4 gann_placeholder_strategy.yaml

示例：

```yaml
strategy_name: gann_placeholder
strategy_version: placeholder_v1
enabled: true
base_interval_value: 4h
higher_interval_value: 1d
```

---

## 27. 数据库设计

本阶段新增两张表。

必须使用 Alembic migration。

### 27.1 strategy_signal_run

记录一次策略信号运行批次。

建议字段：

```text
id
run_id
snapshot_id
symbol
base_interval_value
higher_interval_value
status
trigger_source
strategy_count
success_count
failed_count
invalid_count
not_implemented_count
blocked_reason
error_message
trace_id
started_at_utc
finished_at_utc
created_at_utc
updated_at_utc
```

建议索引：

```text
UNIQUE(run_id)
INDEX(snapshot_id)
INDEX(symbol, base_interval_value, higher_interval_value, created_at_utc)
INDEX(status, created_at_utc)
INDEX(trace_id)
```

### 27.2 strategy_signal_result

记录每个策略独立输出。

建议字段：

```text
id
run_id
snapshot_id
symbol
base_interval_value
higher_interval_value
strategy_name
strategy_version
strategy_status
direction_bias
risk_level
signal_strength
reason_codes_json
reason_text
metrics_json
debug_json
error_message
trace_id
created_at_utc
updated_at_utc
```

建议索引：

```text
INDEX(run_id)
INDEX(snapshot_id)
INDEX(strategy_name, strategy_version)
INDEX(strategy_status)
INDEX(direction_bias)
INDEX(risk_level)
INDEX(trace_id)
```

要求：

1. `reason_text` 必须中文。
2. `reason_codes_json` 用稳定英文代码。
3. `metrics_json` 保存策略计算指标。
4. `debug_json` 只能保存非敏感调试信息。
5. 不保存完整 K线数组。
6. 不保存最终建议。
7. 不保存大模型输出。

---

## 28. CLI 手动入口

建议新增：

```text
scripts/run_strategy_signals.py
```

### 28.1 使用已有 snapshot

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <snapshot_id> \
  --trigger-source cli \
  --dry-run
```

正式写入：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <snapshot_id> \
  --trigger-source cli \
  --confirm-write
```

### 28.2 确保最新 snapshot 后运行

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

正式写入：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

要求：

1. `--snapshot-id` 与 `--ensure-latest-snapshot` 二选一。
2. `--dry-run` 不写策略结果表，也不得通过 ensure-latest 间接创建 MarketContextSnapshot。
3. `--confirm-write` 才允许写入。
4. CLI 只解析参数并调用 app service。
5. CLI 不直接查表。
6. CLI 不直接写表。
7. CLI 不请求 Binance。
8. CLI 不调用大模型。
9. CLI 不发送 Hermes。
10. CLI 不生成最终交易建议。

---

## 29. StrategySignalService

建议新增：

```text
app/strategy/signal_service.py
```

职责：

1. 接收策略信号运行请求。
2. 如果传入 `snapshot_id`，使用指定快照。
3. 如果使用 `ensure_latest_snapshot`，先确保最新合格快照。
4. 调用 `StrategyInputBuilder` 构造输入。
5. 调用 `StrategyRunner` 运行策略。
6. dry-run 时只返回结果，不写策略库，也不懒生成 MarketContextSnapshot。
7. confirm-write 时写入 `strategy_signal_run` 和 `strategy_signal_result`。
8. 捕获异常并返回结构化结果。

禁止：

1. 不生成最终建议。
2. 不调用大模型。
3. 不发送 Hermes。
4. 不自动交易。
5. 不请求 Binance。
6. 不修改 K线表。

---

## 30. CLI 输出格式

CLI 输出应简洁。

示例：

```text
status=partial_success
exit_code=0
run_id=SSR-BTCUSDT-4H-1D-20260517T000000Z-xxxx
snapshot_id=MCS-BTCUSDT-4H-1D-20260517T000000Z-xxxx
strategy_count=3
success_count=2
failed_count=0
not_implemented_count=1
trace_id=...
message=策略信号运行完成。本阶段仅输出独立策略信号，不生成交易建议。
```

禁止输出：

1. 完整 K线数组。
2. 大量 debug 数据。
3. 开仓建议。
4. 平仓建议。
5. 止盈止损。
6. 仓位建议。
7. “微信发送成功”。
8. “已自动交易”。

---

## 31. 状态语义

### 31.1 run status

```text
success
partial_success
blocked
failed
```

### 31.2 result strategy_status

```text
success
no_signal
invalid
not_implemented
failed
```

### 31.3 blocked_reason 建议

```text
snapshot_not_found
snapshot_not_created
snapshot_not_ready
snapshot_stale
snapshot_build_failed
snapshot_quality_not_passed
snapshot_restore_failed
strategy_config_invalid
no_enabled_strategy
```

### 31.4 exit code 建议

```text
0 = success / partial_success
2 = blocked
4 = failed
```

---

## 32. 测试要求

新增：

```text
tests/strategy/
```

至少覆盖以下内容。

### 32.1 snapshot resolver

1. 有合格快照时直接复用。
2. 无合格快照时调用快照服务生成。
3. 快照生成 created 后继续运行。
4. 快照生成 blocked 时策略 blocked。
5. 快照生成 failed 时策略 blocked。
6. 不允许回退旧快照。
7. 4h 新收盘 K线未采集时 blocked。
8. 已有旧 snapshot 但不覆盖最新已收盘 K线时 blocked 或重新生成。
9. 幂等复用不会重复生成等价快照。
10. 不请求 Binance。
11. 不修改 K线表。

### 32.2 输入构建

1. 根据 snapshot_id 能还原 base / higher K线。
2. 查询数量必须等于 snapshot actual_count。
3. base K线按 open_time_ms 升序排列。
4. higher K线按 open_time_ms 升序排列。
5. snapshot 不存在时 blocked。
6. snapshot 状态不是 created 时 blocked。
7. 不请求 Binance。
8. 不修改 K线表。

### 32.3 registry

1. 能加载启用策略。
2. 策略名称唯一。
3. 禁用策略不运行。
4. 配置不存在时报明确错误。
5. 非 `BaseStrategy` 类型拒绝注册。

### 32.4 runner

1. 多个策略能独立运行。
2. 单个策略失败不影响其他策略。
3. 全部成功 => success。
4. 部分成功 => partial_success。
5. 全部失败 => failed。
6. 输入不合法 => blocked。

### 32.5 TrendStructureStrategy

1. 输入足够 K线时输出 success 或 no_signal。
2. reason_text 为中文。
3. reason_codes 为英文稳定代码。
4. 不输出开仓 / 平仓 / 止盈 / 止损字段。
5. 不请求 Binance。
6. 不写数据库。

### 32.6 VolatilityRiskStrategy

1. 能输出 risk_level。
2. 不输出最终 stop_trading。
3. 不输出交易建议。
4. reason_text 为中文。
5. reason_codes 为英文稳定代码。

### 32.7 GannPlaceholderStrategy

1. 返回 not_implemented。
2. 不伪造江恩判断。
3. reason_text 明确说明江恩策略尚未实现。

### 32.8 持久化

1. dry-run 不写 `strategy_signal_run`。
2. dry-run 不写 `strategy_signal_result`。
3. confirm-write 写入 run。
4. confirm-write 写入每个 strategy result。
5. reason_codes_json 可解析。
6. metrics_json 可解析。
7. 不写正式 K线表。
8. 不写建议生命周期表。
9. 不写大模型分析表。

---

## 33. 文档要求

必须新增：

```text
docs/implementation/16_strategy_signal_framework.md
```

文档至少说明：

1. 本阶段实现了哪些模块。
2. 为什么本阶段采用快照懒生成。
3. `ensure_latest_snapshot` 如何复用或生成快照。
4. 为什么不安排单独快照定时任务。
5. StrategyEvaluationInput 如何从 snapshot 构造。
6. StrategyRunner 的职责。
7. StrategyRegistry 的职责。
8. BaseStrategy 的职责。
9. 初始三个策略的职责。
10. strategy_signal_run 表结构。
11. strategy_signal_result 表结构。
12. dry-run 与 confirm-write 行为。
13. 为什么本阶段不生成交易建议。
14. 为什么本阶段不调用 DeepSeek。
15. 为什么本阶段不发送 Hermes 策略提醒。
16. 为什么本阶段不接入 scheduler。
17. 如何运行测试。
18. 后续如何扩展日线主策略。
19. 后续如何接入建议生命周期。
20. 后续如何接入大模型分析。

必须明确写入：

```text
本阶段只生成独立策略信号。
本阶段不生成最终交易建议。
本阶段不调用 DeepSeek。
本阶段不调用任何大模型。
本阶段不发送 Hermes 策略提醒。
本阶段不读取账户。
本阶段不读取持仓。
本阶段不自动交易。
本阶段不请求 Binance。
本阶段不修改正式 K线表。
本阶段不安排单独 MarketContextSnapshot 定时任务。
```

---

## 34. Codex 执行前强制阅读

Codex 开始修改代码前，必须先阅读：

1. `AGENTS.md`
2. `docs/rules/project_invariants.md`
3. `docs/requirements/01_project_scope.md`
4. `docs/requirements/02_data_collection_requirements.md`
5. `docs/requirements/03_database_and_quality_requirements.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/decisions/0003-kline-table-splitting.md`
10. `docs/decisions/0004-alerting-through-hermes.md`
11. `docs/plans/15_market_context_snapshot.md`
12. `docs/implementation/15_market_context_snapshot.md`
13. 当前文件：`docs/plans/16_strategy_signal_framework.md`

如果第 16 阶段 plan 与 `AGENTS.md` 或 `docs/rules/project_invariants.md` 冲突，以后两者为准。

---

## 35. 验收命令

至少运行：

```bash
python -m alembic upgrade head
python -m pytest tests/strategy
python -m scripts.check_project_invariants
```

如果条件允许，也运行：

```bash
python -m pytest
```

手动验证指定 snapshot：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <已有 created 状态的 snapshot_id> \
  --trigger-source cli \
  --dry-run
```

正式写入指定 snapshot：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <已有 created 状态的 snapshot_id> \
  --trigger-source cli \
  --confirm-write
```

手动验证 ensure latest snapshot：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

正式写入 ensure latest snapshot：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --confirm-write
```

如果命令失败，必须说明：

1. 哪个命令失败。
2. 失败原因。
3. 是否是环境问题。
4. 是否影响本阶段正确性判断。

---

## 36. Codex 输出总结要求

Codex 完成后，输出总结必须包含：

1. 修改了哪些文件。
2. 新增了哪些文件。
3. 是否读取并遵守 `AGENTS.md` 和 `docs/rules/project_invariants.md`。
4. 是否发现规则冲突。
5. 是否新增 Alembic migration。
6. 新增了哪些表。
7. 是否实现 StrategyEvaluationInput。
8. 是否实现 StrategyRunner。
9. 是否实现 StrategyRegistry。
10. 是否实现 SnapshotResolver / ensure_latest_snapshot。
11. 是否实现快照复用。
12. 是否避免重复生成等价快照。
13. 是否实现初始策略。
14. 是否支持 dry-run。
15. 是否支持 confirm-write。
16. 测试命令和结果。
17. 是否存在未完成项。
18. 是否存在需要人工确认的地方。

必须明确写：

```text
本阶段没有生成最终交易建议。
本阶段没有调用 DeepSeek。
本阶段没有调用任何大模型。
本阶段没有发送 Hermes 策略提醒。
本阶段没有读取账户。
本阶段没有读取持仓。
本阶段没有下单。
本阶段没有请求 Binance。
本阶段没有修改正式 K线表。
本阶段没有接入 scheduler。
本阶段没有安排单独快照定时任务。
```

---

## 37. 最终验收标准

第 16 阶段完成后，应满足：

1. 可以基于一个 `created` 状态的 `MarketContextSnapshot` 构造策略输入。
2. 可以通过 `ensure_latest_snapshot` 拿到最新合格快照。
3. 有合格快照时会复用，不重复生成。
4. 无合格快照时会调用快照服务生成。
5. 快照 blocked / failed 时策略运行 blocked。
6. 不允许用旧快照运行实时策略。
7. 策略输入来自 snapshot 还原的 K线窗口。
8. 策略层不直接查询最新 K线。
9. 策略层不请求 Binance。
10. 可以运行多个独立策略。
11. 单个策略失败不影响其他策略。
12. 可以写入一次 strategy_signal_run。
13. 可以写入多条 strategy_signal_result。
14. 每个策略输出自己的独立信号。
15. 不生成最终交易建议。
16. 不调用大模型。
17. 不发送 Hermes 策略提醒。
18. 不自动交易。
19. 不读取账户或持仓。
20. 不修改正式 K线表。
21. reason_text 使用中文。
22. reason_codes 使用稳定英文代码。
23. 策略框架不写死 4h，使用 base_interval / higher_interval 抽象。
24. 默认配置仍以 4h + 1d 运行。
25. 测试覆盖主要成功、失败、blocked、dry-run、confirm-write 路径。

---

## 38. 本阶段不解决的问题

以下内容后续再做：

1. 不做最终建议聚合。
2. 不做建议生命周期。
3. 不做 A-v1 / A-v2 建议版本链。
4. 不做 DeepSeek 分析。
5. 不做 GPT 风险审查。
6. 不做模型接力。
7. 不做模型横向对比。
8. 不做 Hermes 策略提醒。
9. 不做人工执行记录。
10. 不做策略复盘。
11. 不做收益评估。
12. 不做 Admin 后台。
13. 不做 scheduler 接入。
14. 不做 10 秒价格触发策略分析。
15. 不做真实江恩策略。

第 16 阶段只打通：

```text
ensure latest snapshot
↓
strategy input
↓
independent strategy signals
↓
signal persistence
```
