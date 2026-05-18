# 17_strategy_signal_scheduler.md

# 第 17 阶段：策略信号调度编排

## 1. 阶段定位

第 17 阶段的目标是：在 4h K线采集成功后，由 scheduler 编排触发第 16 阶段策略信号运行，使系统能够自动生成并记录独立策略信号。

第 17 阶段不是最终交易建议层，不是大模型分析层，不是策略聚合层，也不是建议生命周期层。

第 17 阶段只负责：

1. 在合适的调度时机触发第 16 阶段；
2. 记录策略信号调度事件；
3. 防止同一根 4h K线被重复调度；
4. 按配置发送“独立策略信号通知”或“策略信号调度异常通知”；
5. 保留完整可追溯链路。

第 17 阶段不得直接生成最终操作建议，不得自动交易，不得读取账户、订单、持仓或 API 私钥。

---

## 2. 核心调用链

第 17 阶段必须遵守以下调用链：

```text
scheduler 编排层
    ↓
调用第 16 阶段 StrategySignalService
    ↓
第 16 阶段内部通过 SnapshotResolver 复用或懒生成第 15 阶段 MarketContextSnapshot
    ↓
第 16 阶段运行独立策略信号
    ↓
第 16 阶段写入 strategy_signal_run / strategy_signal_result
    ↓
第 17 阶段更新 strategy_signal_scheduler_event_log
    ↓
第 17 阶段按配置发送 Hermes 通知
```

第 17 阶段不得直接调用第 15 阶段 MarketContextSnapshot 服务。

第 17 阶段只能通过第 16 阶段的 `StrategySignalService` 间接使用第 15 阶段快照能力。MarketContextSnapshot 的复用、懒生成、质量门禁、blocked 结果都必须由第 16 阶段内部的 `SnapshotResolver` 负责。

---

## 3. 调度方式

第 17 阶段不新增一个独立固定时间策略任务作为首选方案。

第 17 阶段应在 scheduler 层做“4h K线增量采集成功后的后置编排”。

普通 4h 收盘时段：

```text
4h 增量采集成功
    ↓
scheduler 后置编排
    ↓
调用第 16 阶段 StrategySignalService
```

UTC 00:00 日线收盘边界需要特殊处理：

```text
UTC 00:00 同时意味着：
- 最新一根 4h K线收盘；
- 最新一根 1d K线收盘。

因此在 UTC 00:00 收盘边界后：
1. 4h 增量采集成功后，不应立即触发策略信号；
2. 必须等待 1d 增量采集成功；
3. 4h 和 1d 都采集成功后，才允许触发第 16 阶段策略信号。
```

注意：这里的 UTC 00:00 指的是 K线收盘边界，不一定等于 `target_base_open_time_utc`。4h K线的唯一目标身份仍以最新已收盘 4h K线的 `open_time_ms` 为准。

---

## 4. 与 4h collector 的边界

第 17 阶段不得把策略调用逻辑写进 4h collector service 内部。

错误边界：

```text
app/market_data/collector
    ↓
直接调用 StrategySignalService
```

正确边界：

```text
app/scheduler/runner 或 scheduler job 编排层
    ↓
调用 4h collector service
    ↓
采集成功后调用 strategy signal scheduler orchestration service
    ↓
strategy signal scheduler orchestration service 调用 StrategySignalService
```

collector 只负责 K线采集。strategy 只负责策略信号。scheduler 负责把两个服务编排起来。

---

## 5. StrategySignalService 调用参数

scheduler 调用第 16 阶段时，应等价于以下语义：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source scheduler \
  --confirm-write
```

但 scheduler 不得实际调用 `scripts/run_strategy_signals.py`。

scheduler 必须直接调用 app 层服务，并构造类似以下请求：

```text
symbol=BTCUSDT
base_interval=4h
higher_interval=1d
ensure_latest_snapshot=True
dry_run=False
confirm_write=True
trigger_source=scheduler
```

如果第 15/16 阶段返回 blocked，scheduler 不得绕过或强行生成策略信号。

---

## 6. 只处理最新一根 4h K线

第 17 阶段只处理最近一根理论已收盘 4h K线。

scheduler 启动、恢复或检测到可能漏跑时，只检查最近一根已收盘 4h K线是否已经存在策略信号调度记录。若不存在，则尝试生成一次策略信号。

第 17 阶段不得自动补跑更早的多根历史 4h K线，不得批量生成历史策略信号，不得补发历史 Hermes 通知。

历史信号回放、策略复盘、回测评估应在后续独立模块中实现。

---

## 7. 幂等设计

第 17 阶段采用数据库状态记录作为策略调度幂等机制。

以以下字段定义一次策略信号调度的唯一身份：

```text
symbol
base_interval
higher_interval
target_base_open_time_ms
```

例如：

```text
BTCUSDT
4h
1d
1779019200000
```

代表针对 BTCUSDT 某一根已收盘 4h K线执行一次 4h + 1d 策略信号调度。

必须通过数据库唯一约束防止同一根 4h K线被重复调度。

建议唯一约束：

```text
uk_strategy_signal_scheduler_target
(symbol, base_interval, higher_interval, target_base_open_time_ms)
```

### 7.1 状态流转

第 17 阶段的调度状态建议包括：

```text
waiting_upstream
running
success
partial_success
blocked
failed
skipped
```

含义：

- `waiting_upstream`：UTC 00:00 收盘边界已完成 4h 采集，但仍在等待 1d 采集完成；
- `running`：调度事件已开始，准备或正在调用第 16 阶段；
- `success`：第 16 阶段返回 success；
- `partial_success`：第 16 阶段返回 partial_success；
- `blocked`：第 15/16 阶段因快照、数据质量或输入条件阻断；
- `failed`：代码异常、数据库异常、Hermes 以外的核心流程异常；
- `skipped`：因已有记录、配置关闭、重复触发等原因跳过。

### 7.2 重复触发处理

若同一目标 K线已有记录：

- `running`：跳过，避免并发重复运行；
- `success` / `partial_success`：跳过，不重复运行；
- `blocked`：第 17 第一版不自动重试；
- `failed`：第 17 第一版不自动重试；
- `waiting_upstream`：仅在 1d 采集成功后允许进入 running；
- `skipped`：不自动重试。

第一版保持保守：已有记录就不创建第二条记录；失败或 blocked 后由用户人工判断，或后续阶段再设计显式重试机制。

如需记录重复触发，可以在同一条记录上更新：

```text
skip_count
last_skipped_at_utc
last_skip_reason
```

不得为同一目标 K线重复创建多条调度主记录。

---

## 8. 新增表：strategy_signal_scheduler_event_log

第 17 阶段必须新增 `strategy_signal_scheduler_event_log`，用于记录 scheduler 从 K线采集成功到策略信号运行完成之间的编排链路。

这张表不是策略结果表。策略结果仍然由第 16 阶段的 `strategy_signal_run` 和 `strategy_signal_result` 保存。

这张表负责回答：

1. scheduler 是否准备触发策略信号；
2. 针对哪一根 4h K线触发；
3. 是否等待 1d 采集；
4. 是否进入第 16 阶段；
5. 第 16 阶段返回什么状态；
6. 是否发送 Hermes；
7. Hermes 发送结果如何；
8. 若失败，失败发生在调度层、策略层还是通知层。

### 8.1 建议字段

```text
id
event_id

symbol
base_interval
higher_interval

target_base_open_time_ms
target_base_open_time_utc
target_base_close_time_ms
target_base_close_time_utc

target_higher_open_time_ms
target_higher_open_time_utc

status
trigger_source
trigger_reason

run_id
snapshot_id

upstream_4h_collector_event_id
upstream_1d_collector_event_id

strategy_count
success_count
failed_count
invalid_count
not_implemented_count

message
error_code
error_message

trace_id

hermes_enabled
hermes_status
hermes_message
hermes_error
hermes_sent_at_utc

skip_count
last_skipped_at_utc
last_skip_reason

started_at_utc
finished_at_utc
created_at_utc
updated_at_utc
```

### 8.2 event_id 格式

建议格式：

```text
SSS-BTCUSDT-4H-1D-20260517T220000Z-<trace_short>
```

其中：

- `SSS` 表示 Strategy Signal Scheduler；
- `BTCUSDT` 表示交易标的；
- `4H` 表示基础周期；
- `1D` 表示高级别周期；
- 时间建议使用目标 4h K线收盘边界 UTC 或调度开始 UTC；
- `trace_short` 使用 trace_id 前缀，方便日志排查。

### 8.3 与 strategy_signal_run 的关系

关系应为：

```text
strategy_signal_scheduler_event_log 1 条
        ↓
可能对应 0 或 1 条 strategy_signal_run
        ↓
strategy_signal_run 对应多条 strategy_signal_result
```

为什么是 0 或 1？

因为某些情况还没有进入第 16 阶段就已经跳过或失败，例如：

- 配置关闭；
- UTC 00:00 边界等待 1d 采集；
- 已有成功记录；
- running 记录未结束；
- 调用第 16 阶段前发生异常。

这些情况下可能没有 `run_id`，但必须有 scheduler 事件记录。

---

## 9. Hermes 通知需求

第 17 阶段支持 Hermes 通知，但必须通过 `.env` 配置控制。

第 17 阶段发送的是“独立策略信号通知”或“策略信号调度异常通知”，不是最终交易建议。

第 17 阶段不得发送以下内容：

- 开多建议；
- 开空建议；
- 加仓建议；
- 减仓建议；
- 平仓建议；
- 止盈建议；
- 止损建议；
- 仓位建议；
- 杠杆建议；
- 最终交易决策。

### 9.1 建议配置项

```env
STRATEGY_SIGNAL_SCHEDULER_ENABLED=false

STRATEGY_SIGNAL_HERMES_ENABLED=false
STRATEGY_SIGNAL_HERMES_NOTIFY_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_BLOCKED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_FAILED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_SKIPPED=false
```

默认建议：

```env
STRATEGY_SIGNAL_SCHEDULER_ENABLED=false
STRATEGY_SIGNAL_HERMES_ENABLED=false
```

原因：避免部署后一启动就自动写策略信号或自动发送微信通知。服务器测试时由用户显式开启。

### 9.2 发送规则

- `success`：按配置发送一条策略信号摘要；
- `partial_success`：按配置发送一条策略信号摘要；
- `blocked`：按配置发送一条阻断通知；
- `failed`：按配置发送一条失败通知；
- `skipped`：默认不发送，只写 event log；
- `waiting_upstream`：默认不发送，只写 event log。

### 9.3 每个 4h 周期最多一条第 17 通知

第 17 阶段不得每个策略单独发送 Hermes。

正确方式：

```text
每个 4h 周期最多发送一条策略信号摘要通知；
这条通知内部列出每个策略的独立结果。
```

错误方式：

```text
趋势策略发一条；
波动率策略发一条；
江恩策略发一条。
```

### 9.4 通知内容模板

success / partial_success 通知应包含：

```text
【BTC 独立策略信号已生成】

周期：BTCUSDT 4h + 1d
目标K线：<UTC 时间> / <PRC 时间>
运行状态：success 或 partial_success
策略数量：<strategy_count>
成功：<success_count>
失败：<failed_count>
无效：<invalid_count>
未实现：<not_implemented_count>

1. 趋势结构策略
状态：<strategy_status>
方向偏向：<direction_bias>
信号强度：<signal_strength>
风险等级：<risk_level>
理由：<reason_text 摘要>

2. 波动率风险策略
状态：<strategy_status>
方向偏向：<direction_bias>
信号强度：<signal_strength>
风险等级：<risk_level>
理由：<reason_text 摘要>

3. 江恩策略
状态：not_implemented
说明：当前仅为占位策略，未输出有效江恩信号。

run_id：<run_id>
snapshot_id：<snapshot_id>
event_id：<event_id>

说明：
这是独立策略信号，不是最终交易建议。
本阶段未进行策略聚合，未调用大模型，系统未自动交易。
```

blocked / failed 通知应包含：

```text
【BTC 策略信号调度异常】

周期：BTCUSDT 4h + 1d
目标K线：<UTC 时间> / <PRC 时间>
状态：blocked 或 failed
原因：<blocked_reason 或 error_message>
event_id：<event_id>
run_id：<run_id，如有>
snapshot_id：<snapshot_id，如有>
trace_id：<trace_id>

说明：
这是系统调度/策略信号异常通知，不是交易建议。
```

### 9.5 Hermes 发送状态记录

所有 Hermes 发送尝试和结果必须写入 `strategy_signal_scheduler_event_log`。

`hermes_status` 建议取值：

```text
disabled
not_required
sent
failed
```

含义：

- `disabled`：配置关闭；
- `not_required`：当前状态不需要发送，例如 skipped；
- `sent`：Hermes 调用成功；
- `failed`：Hermes 调用失败。

必须区分：

1. 策略没跑；
2. 策略跑了但配置关闭；
3. 策略跑了但 Hermes 失败；
4. Hermes 成功但微信侧可能因 24 小时交互窗口限制而未送达。

---

## 10. partial_success 处理规则

`partial_success` 在第 17 阶段视为正常可接受结果。

当前阶段由于 `GannPlaceholderStrategy` 仍为占位策略，`partial_success` 是预期结果。

第 17 阶段不应将 `partial_success` 视为系统失败，也不应触发失败告警。

第 17 阶段应：

1. 记录 scheduler event status = `partial_success`；
2. 按配置发送 Hermes 策略信号摘要；
3. 在通知中明确展示策略总数、成功数、失败数、invalid 数、not_implemented 数；
4. 保留每个策略的状态和简要结论。

`partial_success` 不代表最终交易建议有效。它只代表独立策略信号层产生了部分有效结果。

---

## 11. 未来通知层级原则

第 17 阶段当前可以发送 Hermes，是因为目前策略信号层是系统最高已实现分析层。

后续当策略聚合层、大模型分析层、最终建议生命周期层上线后，应采用“最高已实现决策层通知”原则：

```text
低层成功结果默认入库；
最高已实现决策层负责生成面向用户的一条汇总消息。
```

未来建议的通知层级：

```env
STRATEGY_SIGNAL_HERMES_ENABLED=true/false
STRATEGY_AGGREGATION_HERMES_ENABLED=true/false
LLM_ANALYSIS_HERMES_ENABLED=true/false
FINAL_ADVICE_HERMES_ENABLED=true/false
```

用户可以全开，也可以全关。

但任何阶段都不得让同一 4h 周期因为多个内部层级而不可控地连续发送多条常规交易相关消息。

第 17 阶段的 Hermes 通知必须明确标注：

```text
这是独立策略信号，不是最终交易建议。
```

---

## 12. 与建议生命周期的边界

第 17 阶段每 4h 重新生成当前独立策略信号，但不判断旧建议是否继续、更新、关闭或失效。

第 17 不处理：

- active advice；
- advice chain；
- continue；
- update；
- close；
- invalidate；
- complete；
- 旧建议审核；
- 新建议创建。

后续建议生命周期层负责管理最终 advice 对象。

第 17 阶段只提供后续生命周期审核所需的底层证据：

```text
strategy_signal_run
strategy_signal_result
strategy_signal_scheduler_event_log
```

正确理解：

```text
每 4h 都重新分析市场状态；
但不是每 4h 都新建一条最终建议。
```

---

## 13. 配置需求

第 17 阶段至少需要新增或扩展以下配置：

```env
STRATEGY_SIGNAL_SCHEDULER_ENABLED=false
STRATEGY_SIGNAL_SYMBOLS=BTCUSDT
STRATEGY_SIGNAL_BASE_INTERVAL=4h
STRATEGY_SIGNAL_HIGHER_INTERVAL=1d

STRATEGY_SIGNAL_HERMES_ENABLED=false
STRATEGY_SIGNAL_HERMES_NOTIFY_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_SIGNAL_HERMES_NOTIFY_BLOCKED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_FAILED=true
STRATEGY_SIGNAL_HERMES_NOTIFY_SKIPPED=false
```

如实现 running 超时保护，可增加：

```env
STRATEGY_SIGNAL_SCHEDULER_RUNNING_TIMEOUT_SECONDS=900
```

如果系统已有配置常量体系，应接入现有 Settings，不得在业务代码里硬编码散落配置。

---

## 14. 运行中断与 running 超时

第 17 阶段必须先写入 `running` 或 `waiting_upstream` 状态，再调用第 16 阶段。

如果进程在调用第 16 阶段前后崩溃，数据库里至少应该留下 scheduler event 记录，便于追溯。

建议第一版实现 running 超时保护：

1. 如果已有 `running` 记录且未超过超时时间，则新触发跳过；
2. 如果已有 `running` 记录超过超时时间，则标记为 `failed`，错误码为 `stale_running_timeout`；
3. 第一版不自动重跑该目标 K线；
4. 可按配置发送 failed Hermes 通知。

这样可以避免调度记录永久停留在 running。

---

## 15. 测试要求

第 17 阶段至少需要覆盖以下测试：

### 15.1 调用链测试

1. scheduler 后置编排必须直接调用 `StrategySignalService`；
2. scheduler 不得调用 `scripts/run_strategy_signals.py`；
3. scheduler 不得直接调用第 15 阶段 MarketContextSnapshot 服务。

### 15.2 参数测试

调用 `StrategySignalService` 时必须传入：

```text
trigger_source=scheduler
ensure_latest_snapshot=True
dry_run=False
confirm_write=True
```

### 15.3 幂等测试

1. 同一 `symbol + base_interval + higher_interval + target_base_open_time_ms` 只能创建一条 scheduler event；
2. 已有 `running` 时跳过；
3. 已有 `success` 时跳过；
4. 已有 `partial_success` 时跳过；
5. 已有 `blocked` 时不自动重跑；
6. 已有 `failed` 时不自动重跑。

### 15.4 UTC 00:00 协调测试

1. 普通 4h 收盘边界：4h 采集成功后可以触发策略信号；
2. UTC 00:00 收盘边界：4h 采集成功后应进入 `waiting_upstream` 或等待状态；
3. UTC 00:00 收盘边界：1d 采集成功后才触发策略信号；
4. 4h 和 1d 都完成后，只触发一次策略信号。

### 15.5 event log 测试

1. 调用第 16 阶段前先写 scheduler event；
2. 第 16 返回 success 后更新 event 为 success；
3. 第 16 返回 partial_success 后更新 event 为 partial_success；
4. 第 16 返回 blocked 后更新 event 为 blocked；
5. 第 16 抛出异常后更新 event 为 failed；
6. event 中正确保存 run_id、snapshot_id、trace_id、message、计数字段。

### 15.6 Hermes 测试

1. `STRATEGY_SIGNAL_HERMES_ENABLED=false` 时不发送 Hermes，并记录 hermes_status=disabled；
2. success 且配置开启时发送一条策略信号摘要；
3. partial_success 且配置开启时发送一条策略信号摘要；
4. blocked 且配置开启时发送一条阻断通知；
5. failed 且配置开启时发送一条失败通知；
6. skipped 默认不发送，并记录 hermes_status=not_required；
7. Hermes 发送失败时，不应把策略信号运行结果改成 failed，只应记录 hermes_status=failed 和 hermes_error。

### 15.7 禁止行为测试或静态检查

确认第 17 阶段：

1. 不调用 DeepSeek、GPT、Claude 或其他大模型；
2. 不读取账户、订单、持仓、API 私钥；
3. 不自动下单、平仓、加仓、减仓、撤单；
4. 不写 market_kline_4h 或 market_kline_1d；
5. 不做历史多根策略信号补跑；
6. 不生成最终交易建议。

---

## 16. 禁止事项

第 17 阶段不得引入以下行为：

1. 不得调用 DeepSeek、GPT、Claude 或其他大模型；
2. 不得生成最终交易建议；
3. 不得生成开仓、平仓、加仓、减仓、止盈、止损建议；
4. 不得读取账户、订单、持仓、API 私钥；
5. 不得自动下单、平仓、加仓、减仓、撤单；
6. 不得请求 Binance REST 做回补；
7. 不得请求 Binance WebSocket；
8. 不得修改正式 K线表；
9. 不得新增 manual_repair；
10. 不得人工修改 K线；
11. 不得把 scheduler 写成通过 scripts 间接执行；
12. 不得直接调用第 15 阶段快照服务；
13. 不得破坏第 16 阶段 dry-run / confirm-write 语义；
14. 不得把 partial_success 当成系统失败；
15. 不得自动补跑历史多根 4h K线；
16. 不得每个策略单独发送 Hermes；
17. 不得把第 17 Hermes 通知包装成最终交易建议。

---

## 17. 建议实现文件

Codex 可根据当前项目结构调整，但建议新增或修改以下文件：

```text
app/scheduler/jobs/strategy_signal_scheduler_job.py
app/scheduler/runner.py
app/strategy/scheduler_event_repository.py
app/storage/mysql/models/strategy_signal_scheduler_event.py
app/alerts/strategy_signal_templates.py
app/core/settings.py
app/core/constants.py

migrations/versions/<new>_17_create_strategy_signal_scheduler_event_log.py

docs/implementation/17_strategy_signal_scheduler.md
```

如项目已有 scheduler job 文件或 alert template 文件，应优先复用现有结构，不要重复造平行体系。

---

## 18. 验收命令

完成后至少运行：

```bash
python -m scripts.check_project_invariants
python -m pytest tests/scheduler
python -m pytest tests/strategy
python -m pytest tests/market_context
python -m pytest
```

如服务器缺少 pytest，应先安装测试依赖，不得修改业务代码绕过测试。

---

## 19. 手动验证建议

服务器测试时建议流程：

1. 确认 `.env` 中开启策略信号 scheduler：

```env
STRATEGY_SIGNAL_SCHEDULER_ENABLED=true
STRATEGY_SIGNAL_HERMES_ENABLED=true
```

2. 手动触发或等待 4h 增量采集成功；
3. 检查 `strategy_signal_scheduler_event_log` 是否产生记录；
4. 检查 `strategy_signal_run` 和 `strategy_signal_result` 是否产生对应记录；
5. 检查 Hermes 是否收到一条独立策略信号摘要；
6. 再次触发同一目标 K线，确认不会重复生成策略信号；
7. UTC 00:00 收盘边界单独测试 4h/1d 协调逻辑。

---

## 20. 阶段完成标准

第 17 阶段完成时，应满足：

1. 4h 采集成功后，scheduler 可自动触发第 16 阶段策略信号；
2. UTC 00:00 收盘边界会等待 1d 采集完成后再触发策略信号；
3. 第 17 不直接调用第 15 阶段；
4. 第 17 不通过 scripts 间接调用第 16 阶段；
5. 同一根 4h K线不会重复调度；
6. 调度事件完整写入 `strategy_signal_scheduler_event_log`；
7. success / partial_success / blocked / failed 都能被正确记录；
8. partial_success 被视为正常可接受结果；
9. Hermes 通知可通过 `.env` 控制；
10. 每个 4h 周期最多发送一条第 17 策略信号摘要；
11. Hermes 发送状态被写入 scheduler event；
12. 不发送最终交易建议；
13. 不调用大模型；
14. 不自动交易；
15. 所有相关测试通过。
