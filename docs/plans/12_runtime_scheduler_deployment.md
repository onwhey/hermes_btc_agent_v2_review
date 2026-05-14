# 12 生产运行与调度部署计划

## 1. 阶段定位

12 阶段的目标是把 1-11 已完成的数据采集、价格监控、K线复核能力组织成可以在服务器长期运行的生产入口。

本阶段不是策略层，不做 DeepSeek 分析，不生成交易建议，不读取账户，不自动交易。

本阶段只解决三个运行问题：

1. 09 4h K线增量采集如何由 scheduler（调度器）定时运行。
2. 10 WebSocket（网络套接字长连接）10 秒价格监控如何作为常驻进程运行。
3. 11 每日 K线健康复核如何由 scheduler 每日运行。

12 完成后，系统应具备基础生产运行形态：

```text
scheduler 常驻进程
├── 定时触发 09 4h K线增量采集
└── 定时触发 11 每日 K线健康复核

price monitor 常驻进程
└── 运行 10 WebSocket 价格监控
```

两个常驻进程应通过 systemd（Linux 服务管理器）或等价进程管理工具部署。

---

## 2. 前置状态

本阶段基于已经完成并合并到 master 的 1-11：

1. 基础配置、日志、时间工具。
2. MySQL / Redis 基础设施。
3. Hermes 固定模板报警。
4. Binance REST 4h K线数据能力。
5. K线质量检查。
6. 手动 4h K线回补。
7. 4h K线增量采集。
8. WebSocket 10 秒价格监控。
9. 每日 K线健康复核。
10. 项目规则检查、pytest、alembic 已通过。
11. `v0.1.0-data-foundation` 已作为数据采集与监控底座稳定点。

12 不应重写 1-11 的核心业务逻辑。

---

## 3. 非目标

本阶段不做：

1. DeepSeek 分析。
2. 策略信号。
3. 交易建议。
4. 自动下单。
5. 自动平仓。
6. 自动调杠杆。
7. 账户读取。
8. 订单读取。
9. 仓位读取。
10. Binance 私有接口。
11. 自动修复 K线。
12. 自动回补额外范围。
13. 修改 08 / 09 的正式 K线写入规则。
14. 修改 10 的 WebSocket `aggTrade` 数据源。
15. 修改 11 的每日一次结果通知规则。

---

## 4. 总体运行拓扑

生产运行拆成两个长期进程。

### 4.1 scheduler 常驻进程

```text
systemd: hermes-btc-scheduler
        ↓
scripts/run_scheduler.py
        ↓
app/scheduler/runner.py
        ↓
09 4h K线增量采集 job
11 每日 K线健康复核 job
```

scheduler 只负责调度任务，不承载具体业务逻辑。

scheduler job（调度任务）应直接调用 app 层 service（业务服务），不得调用 scripts（脚本）。

### 4.2 price monitor 常驻进程

```text
systemd: hermes-btc-price-monitor
        ↓
scripts/run_price_monitor_10s.py
        ↓
10 WebSocket price monitor service
        ↓
Binance WebSocket btcusdt@aggTrade
        ↓
Redis bitcoin_price
        ↓
Hermes 价格波动提醒 / 系统异常提醒
```

10 是独立常驻进程，不属于 scheduler 的定时任务。

---

## 5. 09 增量采集运行方式

09 是定时任务，由 scheduler 触发。

scheduler 必须直接调用 09 的 app service，不得调用 `scripts.collect_4h_klines`。

允许：

```text
scheduler job
↓
app/market_data/collector service
```

禁止：

```text
scheduler job
↓
subprocess / runpy / python -m scripts.collect_4h_klines
```

09 的脚本入口只允许人工 CLI（命令行）调试：

```text
python -m scripts.collect_4h_klines --trigger-source cli
```

scheduler 触发时仍然可以传递：

```text
trigger_source=scheduler
```

但这个值只能由 scheduler job 直接调用 app service 时传入，不能通过 scripts 传入。

### 5.1 默认调度时间

默认建议在每根 4h K线理论收盘后延迟 5 分钟执行：

```text
UTC 00:05
UTC 04:05
UTC 08:05
UTC 12:05
UTC 16:05
UTC 20:05
```

含义是：4h K线在 UTC 00:00、04:00、08:00 等时间理论收盘后，系统等待 5 分钟，再调用 Binance REST `/fapi/v1/klines` 获取最近多根已收盘 K线。

延迟 5 分钟的目的：

1. 避免 Binance 最新已收盘 K线尚未稳定。
2. 降低网络抖动导致的误判。
3. 给 4h K线官方数据留出缓冲时间。

调度时间必须可配置，不应写死在业务代码中。

### 5.2 09 必须保持的业务规则

09 运行时必须保持：

1. 正式 K线数据源仍为 Binance USDT-M Futures REST `/fapi/v1/klines`。
2. 只写已收盘 K线。
3. 使用 Binance server time 过滤未收盘 K线。
4. 拉取多根最近已收盘 K线，不只拉最新一根。
5. 已存在且一致的 K线幂等跳过。
6. 已存在但字段冲突的 K线必须阻断，不覆盖。
7. 批次不连续必须阻断。
8. 数据库前后衔接异常必须阻断。
9. 正式 K线写入必须 all-or-nothing（全成功或全失败）。
10. 使用 Redis 写入锁。
11. Redis 锁释放必须 owner 原子校验。
12. blocked、failed、写入失败、任务异常、无法确认状态必须 Hermes 固定模板通知。
13. 不自动修复。
14. 不自动回补额外范围。
15. 不删除正式 K线。
16. 不调用 DeepSeek。
17. 不生成交易建议。
18. 不自动交易。

### 5.3 data_source 规则

09 已有触发来源与正式 K线 `data_source` 语义应保持：

```text
trigger_source=cli
data_source=binance_rest_by_cli
```

```text
trigger_source=scheduler
data_source=binance_rest_by_scheduler
```

scheduler 不应伪装成人工 CLI。

---

## 6. 10 WebSocket 价格监控运行方式

10 是常驻进程，不是 scheduler 任务。

必须保持：

1. 使用 Binance USDT-M Futures WebSocket。
2. 使用 `btcusdt@aggTrade`。
3. `bitcoin_price` 写入最新成交价。
4. 不使用 REST 每 10 秒轮询。
5. 不使用 `markPrice` 写入 `bitcoin_price`。
6. Redis key 仍为 `bitcoin_price`。
7. Redis TTL 仍为 120 秒。
8. 每 10 秒做一次价格变化判断。
9. 价格波动提醒不是交易建议。
10. 系统异常提醒必须有冷却，避免 Hermes 刷屏。

### 6.1 禁止的运行方式

禁止：

```text
scheduler 每 10 秒拉起 10
cron 每 10 秒拉起 10
REST 每 10 秒请求最新价格
```

10 应由独立 systemd service 长期运行。

### 6.2 推荐运行方式

推荐：

```text
systemd 启动 price monitor 常驻进程
price monitor 异常退出后由 systemd 自动重启
日志进入 systemd journal 或项目 logs
```

systemd service 应调用已有薄入口：

```text
python -m scripts.run_price_monitor_10s --trigger-source systemd
```

如果当前脚本支持的触发来源是 `cli|systemd|supervisor`，12 不应修改为 scheduler。

### 6.3 10 和 scheduler 的边界

scheduler 不负责启动、停止、重启 10。

scheduler 不应检测 10 的每 10 秒逻辑。

如后续需要监控 10 是否存活，应通过 systemd 状态、日志或独立运维检查实现，不应把 10 塞入 scheduler 任务循环。

### 6.4 10 的未来 detector 扩展预留

当前 10 已经包含 10 秒价格监控逻辑，不是纯调度器。

当前职责是：

```text
WebSocket 持续接收 btcusdt@aggTrade
↓
内存保留最新价格
↓
每 10 秒做一次检查
↓
写 Redis bitcoin_price
↓
调用 price_change_detector 判断是否超过波动阈值
↓
必要时发 Hermes 价格波动提醒
```

后续可以预留 detector（检测器）扩展能力，但 12 阶段不实现策略 detector。

未来可演进为：

```text
最新价格事件
↓
10 秒检查循环
↓
detector pipeline
   ├── price_change_detector：价格波动检测，当前已有
   ├── strategy_entry_detector：策略买点检测，未来预留
   ├── strategy_exit_detector：策略卖点 / 减仓检测，未来预留
   └── risk_detector：风险检测，未来预留
↓
detector 返回是否需要提醒、提醒内容、提醒等级
↓
统一 alerting 发送
```

12 阶段只允许保留接口边界或文档预留，不得实现：

1. 策略买点检测。
2. 策略卖点检测。
3. 减仓 / 平仓提醒。
4. DeepSeek 分析。
5. 交易建议。
6. 自动交易。
7. 账户 / 订单 / 仓位 / 杠杆能力。

未来买卖点提醒应作为 detector 插件接入，而不是把策略逻辑硬写进 WebSocket 读取代码里。

---

## 7. 11 每日 K线健康复核运行方式

11 是每日定时任务，由 scheduler 触发。

scheduler 必须直接调用 11 的 app service，不得调用 `scripts.check_kline_integrity`。

允许：

```text
scheduler job
↓
app/market_data/kline_integrity service
```

禁止：

```text
scheduler job
↓
subprocess / runpy / python -m scripts.check_kline_integrity
```

### 7.1 默认调度时间

默认建议：

```text
UTC 00:30
```

这个时间应晚于 09 的 `UTC 00:05` 增量采集，避免 11 正在复核时 09 同时写入最近 K线，造成误报。

11 默认复核最近 100 根 BTCUSDT 4h 已收盘 K线，约覆盖 16 天以上，不只是检查当天 K线。

### 7.2 09 与 11 错峰说明

09 和 11 都涉及 4h K线状态，但职责不同：

```text
09：写入最新已收盘 K线
11：每日复核最近 N 根已收盘 K线是否健康
```

默认错峰：

```text
09：UTC 00:05 / 04:05 / 08:05 / 12:05 / 16:05 / 20:05
11：UTC 00:30
```

这样 11 在每日复核时，通常可以看到 09 已经完成 00:05 的增量采集结果。

如果 09 在 `UTC 00:05` 失败，11 在 `UTC 00:30` 发现数据库缺失并发送 `unhealthy` 每日结果通知，这是正确结果，不是误报。

### 7.3 11 每日结果通知规则

scheduler / `daily_integrity_check` 每次最终只发送一条 Hermes 固定模板每日结果通知。

状态只能是：

1. `healthy`：检查完成，K线健康。
2. `unhealthy`：检查完成，发现 K线质量问题。
3. `unknown`：任务异常，无法确认 K线健康。
4. `skipped`：任务被跳过，无法确认本次 K线健康。

同一次 scheduler 每日复核不得拆成“成功通知 + 失败报警”两套 Hermes 通知。

### 7.4 11 禁止事项

11 不得：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动调用 08 回补。
5. 自动调用 09 增量采集。
6. 自动修复任何 K线。
7. 调用 DeepSeek。
8. 生成交易建议。
9. 触碰交易接口。

---

## 8. scheduler 与 scripts 边界

### 8.1 scheduler 职责

scheduler 的职责：

1. 读取调度配置。
2. 按 UTC 时间触发 app service。
3. 传递真实 `trigger_source=scheduler`。
4. 记录任务开始、结束、失败日志。
5. 捕获任务异常。
6. 对 scheduler 层无法确认状态的异常发送 Hermes 固定模板系统异常通知。
7. 保持常驻运行。
8. 避免忙等。
9. 避免重复触发同一任务。

scheduler 不应承载业务判断，例如：

1. 不直接请求 Binance。
2. 不直接写 `market_kline_4h`。
3. 不直接执行 K线质量判断。
4. 不直接发送业务级 Hermes 通知。
5. 不直接读写 10 的 `bitcoin_price`。
6. 不直接实现 09 / 11 的业务流程。

业务通知应优先由对应 service 产生。scheduler 只处理 scheduler 自身无法启动、调度异常、job 包装层异常等运行时问题。

### 8.2 scripts 职责

scripts 的职责：

1. 人工 CLI 入口。
2. 常驻进程启动薄入口。
3. 参数解析。
4. 初始化配置。
5. 调用 app service。
6. 打印结果。
7. 返回退出码。

scripts 不得承载核心业务逻辑。

### 8.3 scheduler 禁止事项

scheduler 禁止：

1. 调用 scripts。
2. 使用 subprocess 调 scripts。
3. 使用 runpy 调 scripts。
4. 拼接 `python -m scripts...`。
5. 每 10 秒拉起 10。
6. 自动修复 K线。
7. 自动回补额外范围。
8. 执行任何交易行为。

---

## 9. scheduler runner 设计原则

scheduler runner 是长期运行入口，用于定时触发 09 和 11。

建议新增：

```text
app/scheduler/config.py
app/scheduler/runner.py
app/scheduler/jobs/kline_4h_incremental_collect.py
scripts/run_scheduler.py
```

如果已有同类结构，应优先复用，不重复造一套。

### 9.1 runner 至少支持

runner 至少支持：

1. 启用 / 禁用 scheduler。
2. 启用 / 禁用 09 增量采集任务。
3. 启用 / 禁用 11 每日健康复核任务。
4. 配置 09 每 4 小时收盘后延迟几分钟执行。
5. 配置 11 每日 UTC 执行时间。
6. 捕获任务异常。
7. 记录任务开始、结束、失败日志。
8. 保证任务触发来源为 `scheduler`。
9. 不调用 scripts。
10. 不影响 10 price monitor。

### 9.2 调度实现方式

本阶段采用简单 UTC sleep 调度循环，不引入复杂调度库。

允许：

```text
while running:
    获取当前 UTC 时间
    判断 09 是否到达执行窗口
    判断 11 是否到达执行窗口
    执行到期 job
    sleep 一段时间
```

要求：

1. 不得忙等。
2. 不得每秒疯狂扫描。
3. 不得重复触发同一时间窗口内的同一 job。
4. 系统时间以 UTC 为调度基准。
5. PRC 时间只用于日志展示，不用于调度判断。

建议配置：

```env
SCHEDULER_POLL_INTERVAL_SECONDS=30
```

含义是 scheduler 每 30 秒醒来一次，检查是否有任务到期。

### 9.3 Redis slot 状态去重

为了避免进程重启、多实例误启动、同一分钟重复扫描导致重复执行，scheduler 应使用 Redis slot state 做任务时间窗口去重。
slot state 必须区分 running lock、completed marker 和 failed / skipped / blocked / stale / expired 状态。

09 slot id 按 4h 收盘后延迟时间生成，例如：

```text
2026-05-13T04:05Z
```

11 slot id 按 UTC 日期生成，例如：

```text
2026-05-13
```

Redis key 分为：

```text
scheduler:running:<job>:<slot>
scheduler:completed:<job>:<slot>
scheduler:status:<job>:<slot>
```

执行前先检查 completed marker，再尝试写入 running lock：

```text
completed marker 已存在：说明 slot 已成功处理，跳过
running lock 设置成功：允许执行任务
running lock 正常存在：说明其他执行者正在运行，本轮跳过
running lock 异常或过期：标记 stale / expired，并按实现规则安全处理
failed / skipped / blocked status marker 存在：TTL 内不自动重复执行同一 slot
```

slot state 应设置合理 TTL，避免 Redis 长期堆积历史 key。

建议：

1. running lock 使用短 TTL，避免任务异常退出后长期卡住。
2. completed marker 可使用较长 TTL，防止补跑机制重复执行已完成 slot。
3. failed / skipped / blocked status marker 应有 TTL，并明确 TTL 内不自动重复执行。
4. TTL 具体值可配置或由代码按任务类型给出稳定默认值。

如果 Redis 异常导致无法判断 slot state，scheduler 不应盲目执行任务，应记录日志并发送 Hermes 固定模板系统异常通知。

### 9.4 为什么本阶段不引入复杂调度库

当前只有两个定时任务：

```text
09：每 4 小时一次
11：每天一次
```

本阶段优先使用简单 sleep 循环 + Redis slot state 去重 + systemd 自动重启。

暂不引入 APScheduler（Python 调度库）等复杂调度库，原因：

1. 当前任务数量少。
2. 简单循环更容易审查。
3. 避免新增 timezone、job store、misfire、coalesce 等额外复杂度。
4. 避免 Codex 在调度库配置上引入新的边界错误。

后续如果任务数量明显增加，再评估是否引入专业调度库。

---

## 10. 09 scheduler job 设计原则

建议新增：

```text
app/scheduler/jobs/kline_4h_incremental_collect.py
```

职责：

1. 读取 09 相关配置。
2. 构造 09 增量采集请求。
3. 设置 `trigger_source=scheduler`。
4. 调用 09 app service。
5. 记录结果摘要。
6. 返回 job 结果给 runner。

不得：

1. 调用 `scripts.collect_4h_klines`。
2. 直接请求 Binance。
3. 直接写 `market_kline_4h`。
4. 直接拼接 Hermes 通知。
5. 绕过 09 service 的质量检查和写入规则。

---

## 11. 11 scheduler job 设计原则

当前已有：

```text
app/scheduler/jobs/daily_kline_integrity_check.py
```

12 应优先复用已有 job。

职责：

1. 读取 11 相关配置。
2. 构造每日复核请求。
3. 设置 `check_trigger=scheduler`。
4. 设置 `check_mode=daily_integrity_check`。
5. 调用 11 app service。
6. 返回 job 结果给 runner。

不得：

1. 调用 `scripts.check_kline_integrity`。
2. 直接请求 Binance。
3. 直接读写正式 K线。
4. 直接拆分 11 的每日结果通知机制。
5. 把 `healthy / unhealthy / unknown / skipped` 改成多条通知。

---

## 12. systemd 服务拆分原则

本阶段应提供 example 模板，不写死真实服务器路径，不包含真实密钥。

建议新增：

```text
deploy/systemd/hermes-btc-scheduler.service.example
deploy/systemd/hermes-btc-price-monitor.service.example
```

两个服务必须拆开。

### 12.1 scheduler 服务

`hermes-btc-scheduler.service` 负责：

1. 运行 scheduler runner。
2. 触发 09。
3. 触发 11。
4. 不运行 10 WebSocket。

示例启动命令应类似：

```text
python -m scripts.run_scheduler
```

具体命令以实际实现为准。

### 12.2 price monitor 服务

`hermes-btc-price-monitor.service` 负责：

1. 运行 10 WebSocket 价格监控。
2. 不触发 09。
3. 不触发 11。

示例启动命令应类似：

```text
python -m scripts.run_price_monitor_10s --trigger-source systemd
```

具体命令以当前 10 实现为准。

### 12.3 systemd example 要求

systemd example 必须满足：

1. 不包含真实密钥。
2. 不包含真实数据库密码。
3. 不包含真实 Hermes token。
4. 使用占位路径，例如 `/opt/hermes_btc_agent_v2`。
5. 明确 `WorkingDirectory`。
6. 明确 `EnvironmentFile`。
7. 明确 `ExecStart`。
8. 明确 `Restart=always` 或 `Restart=on-failure`。
9. 明确服务启动、停止、重启方式。
10. 明确日志查看方式。

---

## 13. 配置规划

允许新增或整理配置。

### 13.1 scheduler 配置

```env
SCHEDULER_ENABLED=true
SCHEDULER_POLL_INTERVAL_SECONDS=30
```

### 13.2 09 增量采集配置

```env
KLINE_4H_INCREMENTAL_COLLECT_ENABLED=true
KLINE_4H_INCREMENTAL_COLLECT_SYMBOL=BTCUSDT
KLINE_4H_INCREMENTAL_COLLECT_INTERVAL=4h
KLINE_4H_INCREMENTAL_COLLECT_LIMIT=6
KLINE_4H_INCREMENTAL_COLLECT_UTC_MINUTES_AFTER_CLOSE=5
```

如果 09 已有同类配置，应优先复用已有命名，避免重复配置。

### 13.3 11 每日复核配置

```env
DAILY_KLINE_INTEGRITY_ENABLED=true
DAILY_KLINE_INTEGRITY_SYMBOL=BTCUSDT
DAILY_KLINE_INTEGRITY_INTERVAL=4h
DAILY_KLINE_INTEGRITY_LIMIT=100
DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS=true
DAILY_KLINE_INTEGRITY_UTC_TIME=00:30
```

12 scheduler 每日复核调度时间只读取 `DAILY_KLINE_INTEGRITY_UTC_TIME`。
旧的 `DAILY_KLINE_INTEGRITY_SCHEDULE_HOUR_UTC` /
`DAILY_KLINE_INTEGRITY_SCHEDULE_MINUTE_UTC` 不作为 scheduler 调度依据。
如果旧配置仍存在于某个部署环境中，12 scheduler 也不得读取它们来决定每日复核时间。

必须保持：

```text
DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS
```

只影响 manual CLI 成功健康通知，不得关闭 scheduler 每日结果通知。

### 13.4 10 价格监控配置

12 不应重写 10 配置，只应在 systemd 和部署文档中引用现有配置。

10 是否运行由独立的 `hermes-btc-price-monitor.service` 启动、停止、重启控制，
不通过 `PRICE_MONITOR_ENABLED` 控制。12 不新增 `PRICE_MONITOR_ENABLED`，
也不让该配置影响 scheduler、09、11 或 10 的系统异常通知。

必须保持：

```env
PRICE_MONITOR_SYMBOL=BTCUSDT
PRICE_MONITOR_WS_STREAM=aggTrade
PRICE_MONITOR_REDIS_KEY=bitcoin_price
PRICE_MONITOR_REDIS_TTL_SECONDS=120
```

价格波动提醒仍可由：

```env
PRICE_MONITOR_ENABLE_PRICE_ALERTS=true
```

控制。

但该配置只控制 10 的价格波动提醒，不得控制系统异常通知，不得控制 07 / 08 / 09 / 11 的 K线质量通知。

### 13.5 Redis slot state 配置

允许新增：

```env
SCHEDULER_RUNNING_LOCK_TTL_SECONDS=1800
SCHEDULER_COMPLETED_MARKER_TTL_SECONDS=259200
SCHEDULER_STATUS_MARKER_TTL_SECONDS=86400
SCHEDULER_SLOT_LOG_COOLDOWN_SECONDS=300
```

不再使用单一 job slot TTL 表达 running 和 completed 两种含义。
running lock、completed marker、status marker 和日志冷却应分别配置或使用稳定默认值。

### 13.6 禁止配置

禁止新增：

```env
KLINE_4H_COLLECT_SEND_ALERT
PRICE_MONITOR_SEND_ALERT
```

禁止新增任何用于关闭 K线失败报警、任务异常报警、每日健康结果通知的配置项。

---

## 14. 报警与通知规则

12 不改变现有报警规则。

必须保持：

1. K线质量问题、blocked、failed、写入失败、任务异常、无法确认健康状态，必须 Hermes 固定模板通知。
2. 11 scheduler 每日复核最终只发送一条结果通知。
3. 10 价格波动提醒可由 `PRICE_MONITOR_ENABLE_PRICE_ALERTS` 控制。
4. 10 系统异常通知不能被价格提醒开关关闭。
5. 10 系统异常通知必须有冷却，避免刷屏。
6. 所有基础系统通知不得调用 DeepSeek。
7. 所有基础系统通知不得包含交易建议。

通知中禁止出现：

```text
做多
做空
开仓
平仓
止盈
止损
仓位建议
杠杆建议
```

### 14.1 scheduler 层异常通知

scheduler 层只处理运行器自身异常，例如：

1. runner 启动失败。
2. job 包装层异常。
3. 配置解析异常。
4. 调度循环异常退出。
5. Redis 执行槽无法判断，导致 scheduler 无法安全决定是否执行任务。

如果 `scripts/run_scheduler.py` 启动阶段发生配置解析异常，且 alerting 配置已经可以正常初始化，
scheduler 应尽力通过 `app/alerting` 发送固定模板系统异常通知。通知必须说明 scheduler 未启动、
原因是配置错误，并明确不自动修复、不自动回补、不自动交易；不得调用 DeepSeek，不得生成交易建议。

如果配置错误导致 alerting 本身也无法初始化，scheduler 不应强行发送通知，只记录清晰错误日志并返回非 0 退出码。
为了避免 systemd 反复重启导致 Hermes 刷屏，启动配置异常通知应使用已有固定模板，并采用轻量冷却或复用既有冷却能力。

如果 09 / 11 service 已经产生了业务结果通知，scheduler 不应再重复发送同一业务事件通知。

避免：

```text
09 service 已报警
scheduler 又为同一次 09 失败再报警
```

scheduler 层应以“自身是否无法调度 / job wrapper 是否异常”为边界，避免重复通知。

---

## 15. 数据安全与交易边界

12 必须继续保持 advice-only / no-trading 边界。

禁止：

1. Binance API key。
2. Binance secret。
3. 私有签名接口。
4. 账户读取。
5. 订单读取。
6. 持仓读取。
7. 杠杆读取或调整。
8. 下单。
9. 撤单。
10. 平仓。
11. 自动交易。
12. 自动调仓。
13. 任何交易执行能力。

systemd example、文档、测试中不得出现真实密钥、真实密码、真实 token。

---

## 16. 测试要求

12 必须补充测试，至少覆盖：

1. scheduler runner 能加载配置。
2. scheduler disabled 时不运行 09 / 11。
3. 09 enabled 时，scheduler job 调用 09 app service。
4. 09 disabled 时不会运行。
5. 11 enabled 时，scheduler job 调用 11 app service。
6. 11 disabled 时不会运行。
7. 09 job 传递 `trigger_source=scheduler`。
8. 11 job 传递 `check_trigger=scheduler` 和 `check_mode=daily_integrity_check`。
9. scheduler 不调用 scripts。
10. scheduler 不使用 subprocess 调 scripts。
11. scheduler 不使用 runpy 调 scripts。
12. scheduler 不拼接 `python -m scripts...`。
13. scheduler 不会每 10 秒拉起 10。
14. 10 price monitor 与 scheduler 是两个独立运行入口。
15. scheduler 任务包装层异常时记录日志并发送固定模板系统异常通知。
16. Redis 执行槽已存在时，同一时间窗口不重复执行同一 job。
17. Redis 执行槽写入失败时，不盲目执行任务，并发送固定模板系统异常通知。
18. systemd example 不包含真实密钥。
19. systemd example 中 price monitor 和 scheduler 是两个独立服务。
20. 不恢复 `--send-alert`。
21. 不新增 `PRICE_MONITOR_SEND_ALERT`。
22. 不新增 `KLINE_4H_COLLECT_SEND_ALERT`。
23. 不调用 DeepSeek。
24. 不涉及交易、账户、订单、仓位、杠杆接口。
25. 12 不实现策略 detector，不生成买卖点提醒。

测试不得依赖真实 Binance、真实 Redis、真实 MySQL、真实 Hermes，除非明确标注为手动集成测试。

---

## 17. 文档要求

12 实现完成后，应新增：

```text
docs/implementation/12_runtime_scheduler_deployment.md
```

实现文档必须说明：

1. 12 的实际文件结构。
2. scheduler runner 启动方式。
3. sleep 调度循环如何判断 09 / 11 到期。
4. Redis 执行槽去重规则。
5. 09 job 调用链。
6. 11 job 调用链。
7. 10 price monitor 为什么不进入 scheduler。
8. 10 detector 扩展只作为未来预留，12 不实现策略检测。
9. scripts 与 scheduler 边界。
10. systemd 服务模板使用方式。
11. 启动服务命令。
12. 停止服务命令。
13. 重启服务命令。
14. 查看日志方式。
15. 如何确认 scheduler 正在运行。
16. 如何确认 09 被调度。
17. 如何确认 10 写入 Redis。
18. 如何确认 11 每日结果通知。
19. 不自动交易、不调用 DeepSeek、不写真实密钥。

---

## 18. 验收标准

12 完成后必须满足：

1. 有清晰的 scheduler runner。
2. 有清晰的 09 scheduler job。
3. 有清晰的 11 scheduler job。
4. 10 price monitor 独立于 scheduler。
5. scheduler 不调用 scripts。
6. scheduler 不拉起 10。
7. scripts 只做薄入口。
8. scheduler 使用 UTC sleep 循环。
9. scheduler 使用 Redis 执行槽去重。
10. Redis 执行槽异常时不盲目执行任务。
11. systemd example 可用于部署参考。
12. systemd example 不含真实密钥。
13. `.env.example` 有必要配置且无真实秘密。
14. 测试覆盖 scheduler 边界。
15. 项目规则检查通过。
16. pytest 通过。
17. 不修改 07 / 08 / 09 / 10 / 11 核心业务规则。
18. 不新增交易能力。
19. 不新增 DeepSeek 策略能力。
20. 不实现策略 detector，只保留未来扩展边界。

---

## 19. 人工审查清单

合并前必须人工检查：

1. 是否从最新 master 开发。
2. 是否没有修改 08 / 09 正式 K线写入规则。
3. 是否没有修改 10 WebSocket `aggTrade` 数据源。
4. 是否没有修改 11 每日一次结果通知规则。
5. scheduler 是否直接调用 app service。
6. scheduler 是否没有调用 scripts。
7. scheduler 是否没有 subprocess / runpy。
8. scheduler 是否没有每 10 秒拉起 price monitor。
9. 10 是否仍是独立常驻进程。
10. 10 是否只预留 detector 扩展，没有实现策略买卖点检测。
11. systemd 是否拆成 scheduler 和 price monitor 两个服务。
12. 是否没有真实密钥。
13. 是否没有 Binance 私有接口。
14. 是否没有自动交易。
15. 是否没有 DeepSeek。
16. 是否没有恢复旧报警开关。
17. Redis 执行槽是否防止同一时间窗口重复执行。
18. Redis 执行槽异常时是否不会盲目执行任务。
19. 测试是否覆盖核心边界。
20. implementation 文档是否与代码一致。

---

## 20. 后续阶段关系

12 完成后，系统才算具备基础生产运行形态。

后续如果进入策略、信号、建议生命周期、DeepSeek 分析等阶段，必须在 12 稳定运行并产生可靠历史数据之后再进行。

12 不应提前实现策略层。

未来如果要让 10 秒价格监控结合策略提醒买卖点，应在后续阶段设计 detector 插件边界、策略信号表、提醒生命周期和复盘评估机制，而不是在 12 阶段直接把策略逻辑写入 10 price monitor。
