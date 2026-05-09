下面是 `docs/plans/10_price_monitor_10s.md` 的可直接复制版。

---

# 10 Price Monitor 10s Plan

## 1. 阶段目标

本阶段实现 BTCUSDT 合约实时价格监控能力。

本阶段目标是：

1. 通过 Binance U 本位合约 WebSocket（网络套接字）获取 BTCUSDT 实时成交价。
2. 每 10 秒对最新价格做一([Binance 开发者中心][1])ce`。
3. Redis TTL 固定为 2 分钟。
4. 当价格相对上一次记录价格变化超过阈值时，通过 Hermes 发送固定模板报警。
5. 避免重复刷屏。
6. 保证该模块不写 MySQL K线表、不生成交易建议、不调用 DeepSeek、不自动交易。

本阶段是实时价格监控，不是 4h K线采集。

## 2. 本阶段明确不做

本阶段不得实现 K线采集、策略分析或交易功能。

禁止实现：

1. REST 最新价格轮询。
2. 通过 REST 每 10 秒请求价格。
3. 4h K线采集。
4. 4h K线回补。
5. 每日 K线复核。
6. 写入 `market_kline_4h`。
7. 写入 `data_quality_check`。
8. 写入 `collector_event_log`。
9. DeepSeek 或其他大模型调用。
10. 策略分析。
11. 交易建议。
12. 自动下单、自动平仓、自动调仓。
13. Binance 账户、订单、持仓、杠杆、保证金相关接口。
14. 人工修改 K线。
15. 自动修复 K线。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/02_data_collection_requirements.md`
3. `docs/requirements/03_database_and_quality_requirements.md`
4. `docs/requirements/04_alerting_requirements.md`
5. `docs/architecture/system_architecture.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/architecture/data_flow.md`
8. `docs/decisions/0001-no-auto-trading.md`
9. `docs/decisions/0002-kline-source-and-time-rules.md`
10. `docs/decisions/0004-alerting-through-hermes.md`
11. `docs/plans/02_core_config_logging.md`
12. `docs/plans/03_infra_mysql_redis.md`
13. `docs/plans/04_alerting_through_hermes.md`
14. `docs/plans/09_4h_kline_incremental_collector.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`
5. `app/storage/redis/`
6. `app/alerting`

本阶段不得重复实现配置读取、日志初始化、Redis 客户端和 Hermes 发送逻辑。

## 4. 官方数据源说明

本阶段使用 Binance U 本位合约 WebSocket market stream。

优先使用：

```
<symbol>@aggTrade
```

用于获取 BTCUSDT 最新成交价。

原因：

1. `aggTrade` 是成交聚合流。
2. 它直接包含成交价格字段。
3. 它适合作为“最新成交价”监控来源。
4. Binance U 本位合约官方文档中，`aggTrade` 属于 `/market` 路由，推送频率为 100ms。

官方 WebSocket 路由要求：

```
wss://fstream.binance.com/market/ws/btcusdt@aggTrade
```

或者 combined stream：

```
wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade
```

Binance 官方文档说明，U 本位合约 WebSocket market stream 当前使用 `wss://fstream.binance.com/market` 路由，`aggTrade` stream 名称为 `<symbol>@aggTrade`，更新速度为 100ms。([Binance 开发者中心][1])ce 边界说明

本阶段默认不使用 `markPrice` 作为 `bitcoin_price` 的来源。

`markPrice` 可以后续用于风控参考，例如：

1. 标记价格偏离。
2. 强平风险参考。
3. 资金费率窗口参考。
4. 合约风控判断。

但本阶段的 `bitcoin_price` 代表：

```
BTCUSDT U 本位合约最新成交价
```

不是：

```
标记价格
指数价格
结算价格
现货价格
```

Binance 官方文档显示，`markPrice` stream 提供 mark price、index price、funding rate 等字段，更新速度为 1 秒或 3 秒。它和成交价不是同一个概念，不能混写进同一个 Redis key。([Binance 开发者中心][2])议分支名：

`feature/10-price-monitor-10s`

分支创建、切换、提交、推送、合并由用户人工执行。

Codex 不应自动执行以下 Git 操作：

1. 创建分支。
2. 切换分支。
3. 合并分支。
4. 推送远程仓库。
5. 删除分支。
6. 强制覆盖工作区。

Codex 只负责在用户已经切换好的当前分支内，根据本 plan 修改文件。

## 7. 需要检查和补齐的目录

本阶段应检查以下目录是否存在，不存在才创建：

```
app/market_data/
app/market_data/price_monitor/
app/storage/redis/
scripts/
tests/
docs/implementation/
```

目录处理原则：

1. 如果目录已经存在，只检查并保留，不得删除后重建。
2. 不得覆盖、清空、移动已有 `docs/` 内容。
3. 不得删除已有 `requirements/`、`architecture/`、`decisions/`、`plans/` 文件。
4. 只允许补齐当前缺失的目录或占位文件。
5. `.gitkeep` 只在目录为空且需要 Git 跟踪时创建，不得覆盖已有文件。

## 8. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/market_data/price_monitor/__init__.py
app/market_data/price_monitor/types.py
app/market_data/price_monitor/websocket_client.py
app/market_data/price_monitor/price_event_parser.py
app/market_data/price_monitor/redis_price_state.py
app/market_data/price_monitor/price_change_detector.py
app/market_data/price_monitor/alert_throttle.py
app/market_data/price_monitor/price_monitor_service.py

scripts/run_price_monitor_10s.py

tests/test_price_monitor_10s.py
docs/implementation/10_price_monitor_10s.md
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。

## 9. 模块定位

模块路径：

`app/market_data/price_monitor/`

该模块负责：

1. 连接 Binance WebSocket。
2. 接收 BTCUSDT 实时成交事件。
3. 解析最新成交价。
4. 在内存中保存最近收到的最新价格事件。
5. 每 10 秒执行一次价格监控判断。
6. 从 Redis 读取上一次保存的价格。
7. 判断价格变化是否超过阈值。
8. 将当前最新价格写入 Redis。
9. Redis TTL 设置为 2 分钟。
10. 必要时调用 `app/alerting` 发送 Hermes 固定模板报警。
11. 处理 WebSocket 断线重连。

该模块不负责：

1. K线采集。
2. K线回补。
3. K线复核。
4. MySQL K线写入。
5. 策略分析。
6. DeepSeek 分析。
7. 交易建议。
8. 自动交易。

## 10. 启动方式要求

本阶段建议创建：

`scripts/run_price_monitor_10s.py`

该脚本是价格监控进程启动入口。

允许用户手动执行：

```
python -m scripts.run_price_monitor_10s --symbol BTCUSDT --trigger-source cli
```

生产环境后续可以由 systemd 或 supervisor 启动：

```
python -m scripts.run_price_monitor_10s --symbol BTCUSDT --trigger-source systemd
```

本阶段不建议使用 scheduler 每 10 秒启动一次脚本。

错误方式：

```
scheduler 每 10 秒启动一次脚本
    ↓
连接一次 WebSocket
    ↓
收一次价格
    ↓
退出
```

这个方式禁止。

正确方式：

```
systemd / supervisor / 用户 CLI
    ↓
启动一个常驻进程
    ↓
进程内部维护 WebSocket 连接
    ↓
持续接收价格
    ↓
每 10 秒执行监控判断
```

## 11. 脚本入口要求

`scripts/run_price_monitor_10s.py` 只负责：

1. 解析参数。
2. 初始化配置。
3. 初始化日志。
4. 调用 `PriceMonitorService`。
5. 处理退出信号。

脚本不得：

1. 直接连接 WebSocket。
2. 直接解析 WebSocket 消息。
3. 直接读写 Redis。
4. 直接发送 Hermes。
5. 承载核心业务逻辑。
6. 请求 Binance REST。
7. 写 MySQL。
8. 调用 DeepSeek。
9. 执行交易。

文件顶部必须写清楚：

1. 这是 10s 价格监控启动入口。
2. 本脚本启动常驻进程。
3. 本脚本不应由 scheduler 每 10 秒反复拉起。
4. 数据来自 Binance WebSocket。
5. 不使用 REST 轮询价格。
6. 不写入 `market_kline_4h`。
7. 不生成交易建议。
8. 不自动交易。

## 12. 参数要求

建议支持参数：

```
--symbol BTCUSDT
--trigger-source cli|systemd|supervisor
--monitor-interval-seconds 10
--price-change-threshold 0.01
--redis-key bitcoin_price
--redis-ttl-seconds 120
--send-alert
```

参数规则：

1. `--symbol` 默认 `BTCUSDT`。
2. `--trigger-source` 必填。
3. `--trigger-source` 允许 `cli`、`systemd`、`supervisor`。
4. `--monitor-interval-seconds` 默认 10。
5. `--price-change-threshold` 默认 0.01，表示 1%。
6. `--redis-key` 默认 `bitcoin_price`。
7. `--redis-ttl-seconds` 默认 120。
8. `--send-alert` 表示允许发送 Hermes，仍受配置控制。

禁止：

1. 缺少 `trigger_source` 仍启动。
2. 非法 `trigger_source` 仍启动。
3. monitor interval 小于 1 秒。
4. Redis TTL 小于 monitor interval。
5. 使用 REST price endpoint 参数。
6. 输入人工价格。

## 13. 配置要求

建议新增配置：

```
PRICE_MONITOR_SYMBOL=BTCUSDT
PRICE_MONITOR_WS_STREAM=aggTrade
PRICE_MONITOR_INTERVAL_SECONDS=10
PRICE_MONITOR_CHANGE_THRESHOLD=0.01
PRICE_MONITOR_REDIS_KEY=bitcoin_price
PRICE_MONITOR_REDIS_TTL_SECONDS=120
PRICE_MONITOR_ALERT_COOLDOWN_SECONDS=60
PRICE_MONITOR_SEND_ALERT=true
PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS=1
PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS=60
```

如果 `.env.example` 已存在，只补齐缺失项，不得清空重写。

禁止写入：

1. Binance API key。
2. Binance secret key。
3. 交易权限配置。
4. 账户信息。
5. 真实 webhook secret。

本阶段使用公开 WebSocket market stream，不需要 Binance API key。

## 14. WebSocket client 要求

建议文件：

`app/market_data/price_monitor/websocket_client.py`

职责：

1. 构建 Binance U 本位合约 WebSocket URL。
2. 连接 WebSocket。
3. 接收消息。
4. 处理 ping / pong。
5. 检测断线。
6. 按退避策略重连。
7. 将原始消息交给上层 parser。
8. 不做业务判断。

要求：

1. 使用 `/market` 路由。
2. symbol 必须转为小写用于 stream name。
3. 默认 stream 为 `btcusdt@aggTrade`。
4. 不连接 private user data stream。
5. 不订阅账户、订单、持仓相关 stream。
6. 不写 Redis。
7. 不发送 Hermes。
8. 不写 MySQL。
9. 不调用 DeepSeek。

Binance 官方文档说明，WebSocket 连接支持 `/ws/<streamName>` 和 `/stream?streams=...` 两种模式，symbol 使用小写；单连接有 24 小时有效期，且服务端会发送 ping frame，客户端需要响应 pong。实现时必须考虑重连和 ping / pong。([Binance 开发者中心][1])vent parser 要求

建议文件：

`app/market_data/price_monitor/price_event_parser.py`

职责：

1. 解析 `aggTrade` 原始消息。
2. 提取 symbol。
3. 提取 price。
4. 提取 event_time_ms。
5. 提取 trade_time_ms。
6. 生成内部 `PriceEvent`。

`aggTrade` 字段映射：

```
e  event type
E  event time
s  symbol
p  price
q  quantity
T  trade time
m  buyer is maker
```

本阶段至少使用：

1. `s`：symbol。
2. `p`：成交价格。
3. `E`：事件时间。
4. `T`：成交时间。

要求：

1. price 必须转换为 Decimal。
2. 不得使用 float。
3. symbol 必须校验为 BTCUSDT。
4. 缺少关键字段必须抛出解析异常。
5. 非法 price 必须抛出解析异常。
6. parser 不写 Redis。
7. parser 不发 Hermes。
8. parser 不写 MySQL。

## 16. 类型定义要求

建议文件：

`app/market_data/price_monitor/types.py`

建议定义：

1. `PriceEvent`
2. `PriceState`
3. `PriceChangeResult`
4. `PriceMonitorConfig`
5. `PriceMonitorStatus`
6. `PriceAlertDecision`

`PriceEvent` 至少包含：

```
symbol
price
event_time_ms
trade_time_ms
received_at_utc
received_at_prc
source
```

`PriceState` 至少包含：

```
symbol
price
event_time_ms
trade_time_ms
saved_at_utc
saved_at_prc
source
```

`source` 固定为：

```
binance_ws_agg_trade
```

禁止使用：

```
binance_rest_price
manual_input
human_edit
```

## 17. Redis 状态要求

建议文件：

`app/market_data/price_monitor/redis_price_state.py`

Redis key：

```
bitcoin_price
```

TTL：

```
120 秒
```

写入要求：

1. 每次 10s 监控判断时，都要写入当前最新价格。
2. 即使没有触发报警，也要更新 Redis。
3. Redis TTL 每次写入都刷新为 2 分钟。
4. 写入失败必须记录日志。
5. 写入失败可以触发 Hermes 固定模板报警。
6. 不得因为 Redis 失败写 MySQL K线表。
7. 不得把 Redis 当作长期行情库。

Redis value 建议保存 JSON 字符串：

```
{
  "symbol": "BTCUSDT",
  "price": "65000.12",
  "source": "binance_ws_agg_trade",
  "event_time_ms": 123456789,
  "trade_time_ms": 123456789,
  "saved_at_utc": "...",
  "saved_at_prc": "..."
}
```

如果后续有其他模块强依赖 `bitcoin_price` 是纯价格字符串，必须在 implementation 中明确说明，并改为：

1. `bitcoin_price` 保存纯价格字符串。
2. `bitcoin_price_meta` 保存 JSON 元数据。

本阶段默认建议使用 JSON，因为可排查性更好。

## 18. 价格变化检测要求

建议文件：

`app/market_data/price_monitor/price_change_detector.py`

检测规则：

```
abs(current_price - previous_price) / previous_price >= threshold
```

默认 threshold：

```
0.01
```

表示：

```
1%
```

要求：

1. 使用 Decimal 计算。
2. previous_price 不存在时，不触发价格变化报警，只写 Redis。
3. previous_price <= 0 时，不触发价格变化报警，并记录异常。
4. current_price <= 0 时，拒绝处理。
5. 价格变化方向必须记录为 `up` 或 `down`。
6. 检测结果必须包含变化百分比。
7. 检测结果必须包含旧价格和新价格。

禁止：

1. 使用 float 计算价格变化。
2. 单纯字符串比较价格。
3. 没有旧价格时胡乱报警。
4. 把价格变化报警解释成交易建议。

## 19. 10 秒监控节奏要求

本阶段不是每收到一条 WebSocket 消息就报警。

正确逻辑：

```
WebSocket 持续接收价格
    ↓
内存中只保留最新 PriceEvent
    ↓
每 10 秒取一次最新 PriceEvent
    ↓
与 Redis 中上一次价格比较
    ↓
判断是否超过阈值
    ↓
写入 Redis
    ↓
必要时报警
```

要求：

1. WebSocket 接收频率可以高于 10 秒。
2. 监控判断频率默认 10 秒。
3. Redis 写入频率默认 10 秒。
4. 报警不应高于冷却规则允许频率。
5. 不得每个 WebSocket tick 都写 Redis。
6. 不得每个 WebSocket tick 都发 Hermes。
7. 不得每 10 秒重连 WebSocket。

## 20. 报警冷却要求

建议文件：

`app/market_data/price_monitor/alert_throttle.py`

默认冷却时间：

```
60 秒
```

规则：

1. 同一 symbol、同一 alert type，在冷却时间内最多报警一次。
2. 冷却状态可以先存在内存中。
3. 后续如需多进程部署，再改为 Redis 冷却锁。
4. 冷却命中时，应记录日志但不发 Hermes。
5. 冷却不影响 Redis 最新价格写入。

报警类型建议：

```
price_change_threshold_exceeded
price_monitor_ws_disconnected
price_monitor_redis_write_failed
price_monitor_no_recent_price
```

禁止：

1. 极端行情下每 10 秒无限刷屏。
2. 每条 WebSocket 消息都报警。
3. 把冷却状态写入 MySQL K线表。
4. 用 DeepSeek 判断是否报警。

## 21. PriceMonitorService 要求

建议文件：

`app/market_data/price_monitor/price_monitor_service.py`

建议方法：

1. `run_price_monitor(request)`
2. `validate_monitor_request(request)`
3. `start_websocket_listener()`
4. `handle_raw_ws_message(raw_message)`
5. `update_latest_price_event(price_event)`
6. `run_monitor_loop()`
7. `check_latest_price_every_interval()`
8. `load_previous_price_state_from_redis()`
9. `detect_price_change(current_event, previous_state)`
10. `save_current_price_to_redis(current_event)`
11. `send_price_alert_if_needed(change_result)`
12. `send_monitor_health_alert_if_needed(error)`
13. `shutdown_gracefully()`

职责：

1. 协调 WebSocket client。
2. 协调 parser。
3. 协调 Redis state。
4. 协调 price detector。
5. 协调 alert throttle。
6. 协调 Hermes 报警。
7. 处理进程退出。

禁止：

1. 请求 Binance REST 最新价格。
2. 写 MySQL K线表。
3. 调用 K线采集 service。
4. 调用 K线回补 service。
5. 调用 DeepSeek。
6. 生成交易建议。
7. 执行交易。

## 22. 标准调用链

标准调用链：

```
用户 CLI / systemd / supervisor
    ↓
scripts/run_price_monitor_10s.py::main
    ↓
app/market_data/price_monitor/price_monitor_service.py::run_price_monitor
    ↓
websocket_client.connect_and_listen
    ↓
price_event_parser.parse_agg_trade_event
    ↓
price_monitor_service.update_latest_price_event
    ↓
每 10 秒 monitor loop
    ↓
redis_price_state.load_previous_price_state
    ↓
price_change_detector.detect_price_change
    ↓
redis_price_state.save_current_price_state
    ↓
alert_throttle.should_send_alert
    ↓
app/alerting 发送 Hermes 固定模板报警
```

异常链路：

```
WebSocket 断线 / Redis 失败 / parser 异常 / 无最新价格
    ↓
记录日志
    ↓
根据异常类型决定是否 Hermes 报警
    ↓
WebSocket 断线按退避策略重连
    ↓
不写 MySQL K线表
    ↓
不调用 DeepSeek
    ↓
不自动交易
```

## 23. WebSocket 断线重连要求

必须支持断线重连。

要求：

1. WebSocket 异常断开后自动重连。
2. 使用指数退避或递增退避。
3. 最小重连间隔默认 1 秒。
4. 最大重连间隔默认 60 秒。
5. 重连失败要记录日志。
6. 长时间重连失败可以触发 Hermes 固定模板报警。
7. 重连成功后应恢复正常监控。
8. 不得因为断线改用 REST 价格轮询，除非未来 plan 明确允许。

需要考虑：

1. Binance 单连接 24 小时有效期。
2. 服务端 ping / pong。
3. 网络断开。
4. JSON 解析错误。
5. 空消息或异常消息。

## 24. 无最新价格处理

如果 WebSocket 已连接但超过一定时间没有最新价格事件，应认为监控异常。

建议配置：

```
PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS=30
```

处理规则：

1. 如果 30 秒没有收到有效 PriceEvent，记录 warning。
2. 可以发送 Hermes 固定模板报警。
3. 不写入旧价格冒充新价格。
4. 不使用 REST 补价格。
5. 不生成交易建议。
6. 不自动交易。

## 25. Hermes 报警要求

本阶段允许调用 Hermes。

必须通过：

`app/alerting`

允许报警场景：

1. 价格变化超过阈值。
2. WebSocket 长时间断开。
3. WebSocket 重连连续失败。
4. Redis 读取失败。
5. Redis 写入失败。
6. 长时间未收到有效价格事件。
7. parser 连续异常。
8. 价格为非法值。
9. 监控进程异常退出。

报警模板必须使用固定模板。

建议模板类型：

```
price_change_threshold_exceeded
price_monitor_ws_disconnected
price_monitor_redis_error
price_monitor_no_recent_price
price_monitor_runtime_error
```

价格变化报警内容必须包含：

1. symbol。
2. price_source。
3. previous_price。
4. current_price。
5. change_percent。
6. direction。
7. event_time。
8. monitor_interval_seconds。
9. threshold。
10. 明确这不是交易建议。
11. 明确系统没有自动下单。

禁止：

1. 调用 DeepSeek 生成报警。
2. 调用其他大模型生成报警。
3. 把报警写成买入或卖出建议。
4. 自动下单。
5. 在 WebSocket client 中直接报警。
6. 在 Redis repository 中直接报警。

## 26. Redis 影响

本阶段允许读写 Redis。

允许 key：

```
bitcoin_price
```

可选 key：

```
bitcoin_price_meta
```

如果实现 Redis 冷却锁，可选：

```
price_monitor_alert_cooldown:BTCUSDT:price_change_threshold_exceeded
```

要求：

1. `bitcoin_price` TTL 必须为 120 秒。
2. 每次监控判断都刷新 TTL。
3. Redis 连接失败不得导致写 MySQL。
4. Redis 连接失败不得导致自动交易。
5. Redis 只保存实时状态，不作为长期历史行情库。

禁止：

1. 写入 K线数据到 Redis 当作正式历史数据。
2. 用 Redis 替代 MySQL K线表。
3. 写入账户、订单、持仓信息。
4. 写入密钥。

## 27. MySQL 影响

本阶段默认不写 MySQL。

本阶段不得：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 写入 `collector_event_log`。
5. 写入 `data_quality_check`。
6. 创建新表。
7. 创建策略表。
8. 创建建议表。

说明：

价格变化报警如果 `app/alerting` 内部会写 `alert_message`，可以复用 04 阶段已有报警记录逻辑。

但 price monitor 自身不得直接写业务 MySQL 表。

## 28. Binance 影响

本阶段允许使用：

1. Binance U 本位合约 WebSocket market stream。
2. `btcusdt@aggTrade`。

本阶段禁止使用：

1. REST 最新价格接口。
2. REST K线接口。
3. WebSocket private stream。
4. user data stream。
5. order endpoint。
6. account endpoint。
7. position endpoint。
8. leverage endpoint。
9. margin endpoint。
10. listenKey。

## 29. Scheduler 影响

本阶段不建议 scheduler 每 10 秒调用脚本。

允许后续 scheduler 做：

1. 监控进程健康检查。
2. 检查 `bitcoin_price` TTL 是否正常。
3. 检查进程是否存活。

但本阶段默认不实现 scheduler job。

本阶段禁止：

1. scheduler 每 10 秒拉起 `scripts/run_price_monitor_10s.py`。
2. scheduler 每 10 秒请求 REST price。
3. scheduler 直接写 Redis price。
4. scheduler 直接发送价格变化报警。

## 30. 与 09 K线采集的边界

09 负责：

```
Binance REST /fapi/v1/klines
    ↓
官方已收盘 4h K线
    ↓
MySQL market_kline_4h
```

10 负责：

```
Binance WebSocket aggTrade
    ↓
最新成交价
    ↓
Redis bitcoin_price
    ↓
Hermes 价格波动报警
```

两者禁止混用：

1. 10 不写 K线表。
2. 09 不写 Redis `bitcoin_price`。
3. 10 不采集 4h K线。
4. 09 不使用 WebSocket 最新价。
5. 10 的价格报警不是交易建议。
6. 09 的 K线采集不是实时价格监控。

## 31. 交易安全边界

本阶段以及后续所有阶段均禁止实现：

1. 自动下单。
2. 自动平仓。
3. 自动调仓。
4. 自动加仓。
5. 自动减仓。
6. 读取账户后自动决策。
7. Binance order 接口。
8. Binance account 接口。
9. Binance position 接口。
10. 杠杆调整接口。
11. 保证金模式调整接口。

如果 Codex 添加任何交易执行相关代码，应直接拒绝合并。

## 32. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. price monitor 启动。
2. WebSocket URL，但不得包含密钥。
3. WebSocket 连接成功。
4. WebSocket 断开。
5. WebSocket 重连。
6. 解析价格成功。
7. 解析价格失败。
8. 当前价格。
9. 上一次 Redis 价格。
10. 价格变化百分比。
11. Redis 写入成功或失败。
12. Hermes 报警是否发送。
13. 冷却命中。
14. 进程退出。

禁止记录：

1. 完整 `.env`。
2. Redis 密码。
3. Hermes webhook。
4. Hermes secret。
5. token。
6. Authorization。
7. cookie。
8. 账户信息。
9. 持仓信息。
10. 交易信息。

## 33. 异常要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增异常：

1. `PriceMonitorError`
2. `PriceEventParseError`
3. `PriceMonitorRedisError`
4. `PriceMonitorWebSocketError`
5. `PriceMonitorConfigError`

异常要求：

1. WebSocket 异常必须明确是连接异常、消息异常或重连异常。
2. Redis 异常必须明确是读取失败还是写入失败。
3. parser 异常必须明确缺失字段或非法字段。
4. 配置异常必须明确参数名。
5. 异常消息不得包含敏感信息。
6. 异常不得触发自动交易。
7. 异常不得触发 DeepSeek 分析。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。

## 34. 测试要求

建议创建：

`tests/test_price_monitor_10s.py`

默认测试不得依赖真实 Binance、真实 Redis、真实 Hermes、真实 MySQL。

至少覆盖：

1. CLI 缺少 `trigger_source` 时拒绝。
2. CLI 非法 `trigger_source` 时拒绝。
3. WebSocket URL 构建正确。
4. symbol 转小写用于 stream name。
5. parser 能解析合法 aggTrade 消息。
6. parser 拒绝缺少 price 的消息。
7. parser 拒绝非法 Decimal price。
8. price detector 能识别上涨超过 1%。
9. price detector 能识别下跌超过 1%。
10. price detector 在 previous 不存在时不报警。
11. Redis state 写入时设置 TTL。
12. Redis state 读取异常时返回明确错误。
13. alert throttle 在冷却期内阻止重复报警。
14. service 每 10 秒判断一次的逻辑可被 mock。
15. WebSocket 断线会触发重连逻辑 mock。
16. 价格变化超过阈值时调用 alerting mock。
17. 未超过阈值时不调用 alerting。
18. 无最新价格时可生成异常状态。
19. 不请求 Binance REST。
20. 不写 MySQL。
21. 不调用 DeepSeek。
22. 不涉及交易接口。

如果需要真实集成测试，必须使用显式开关，例如：

```
RUN_PRICE_MONITOR_INTEGRATION_TESTS=true
```

默认 `pytest` 不应访问真实外部服务。

## 35. implementation 文档要求

本阶段完成后，Codex 必须创建：

`docs/implementation/10_price_monitor_10s.md`

说明文件必须描述：

1. 本模块入口。
2. CLI 参数。
3. WebSocket URL 构建规则。
4. 使用的 stream 名称。
5. PriceEvent 字段解析。
6. 10 秒监控循环。
7. Redis key。
8. Redis value 格式。
9. Redis TTL。
10. 价格变化检测公式。
11. 报警阈值。
12. 报警冷却规则。
13. Hermes 报警流程。
14. WebSocket 断线重连流程。
15. 无最新价格处理流程。
16. 本模块不写 MySQL K线表的边界。
17. 本模块不调用 REST 最新价格接口的边界。
18. 本模块不生成交易建议、不自动交易的边界。

本阶段 implementation 文档必须遵守 `AGENTS.md` 中的“代码可读性与实现说明强制要求”，按功能写清楚入口文件、方法调用链、数据流、异常处理、测试方式和本模块边界。

## 36. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.run_price_monitor_10s --help` 可以运行。
2. 缺少 `--trigger-source` 时拒绝执行。
3. 非法 `--trigger-source` 时拒绝执行。
4. WebSocket URL 使用 Binance U 本位合约 `/market` 路由。
5. 默认 stream 是 `btcusdt@aggTrade`。
6. parser 能解析 aggTrade price。
7. price 使用 Decimal。
8. 监控判断间隔默认 10 秒。
9. Redis key 默认为 `bitcoin_price`。
10. Redis TTL 默认为 120 秒。
11. 价格变化阈值默认为 1%。
12. 超过阈值时可调用 `app/alerting` mock。
13. 冷却期内不会重复刷屏。
14. WebSocket 断线有重连逻辑。
15. 默认测试不连接真实 Binance。
16. 默认测试不连接真实 Redis。
17. 默认测试不发送真实 Hermes。
18. 默认测试不连接真实 MySQL。
19. 不请求 REST 最新价格。
20. 不写入 `market_kline_4h`。
21. 不写入 `collector_event_log`。
22. 不写入 `data_quality_check`。
23. 不调用 DeepSeek。
24. 不生成交易建议。
25. 不实现交易执行相关代码。
26. `docs/implementation/10_price_monitor_10s.md` 已创建或补齐。

## 37. 人工审查清单

合并前用户应人工检查：

1. 查看是否使用 WebSocket，而不是 REST 价格轮询。
2. 查看 WebSocket URL 是否使用 `/market` 路由。
3. 查看 stream 是否为 `btcusdt@aggTrade`。
4. 查看是否写 Redis `bitcoin_price`。
5. 查看 Redis TTL 是否为 120 秒。
6. 查看是否每 10 秒判断一次，而不是每条消息都报警。
7. 查看是否有报警冷却。
8. 查看是否通过 `app/alerting` 发送 Hermes。
9. 查看是否调用 DeepSeek。
10. 查看是否写 MySQL K线表。
11. 查看是否写 `collector_event_log` 或 `data_quality_check`。
12. 查看是否存在 REST 最新价格接口。
13. 查看是否存在交易接口。
14. 查看 implementation 是否写清楚调用链。
15. 运行测试。
16. 运行 help 命令。

建议搜索：

```
grep -R "ticker/price" app scripts tests
grep -R "/fapi/v1/ticker" app scripts tests
grep -R "get_price" app scripts tests
grep -R "market_kline_4h" app/market_data/price_monitor scripts/run_price_monitor_10s.py tests
grep -R "collector_event_log" app/market_data/price_monitor scripts/run_price_monitor_10s.py tests
grep -R "data_quality_check" app/market_data/price_monitor scripts/run_price_monitor_10s.py tests
grep -R "DeepSeek" app scripts tests
grep -R "openai" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果发现 REST 最新价格轮询，应拒绝合并。

如果发现交易执行相关代码，应拒绝合并。

## 38. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 用 REST 每 10 秒请求价格。
2. 请求 REST 最新价格接口。
3. 写入 `market_kline_4h`。
4. 写入 `collector_event_log`。
5. 写入 `data_quality_check`。
6. 采集 K线。
7. 回补 K线。
8. 复核 K线。
9. 调用 DeepSeek。
10. 生成交易建议。
11. 自动下单。
12. 自动平仓。
13. 自动调仓。
14. 使用 Binance account endpoint。
15. 使用 Binance order endpoint。
16. 使用 Binance position endpoint。
17. 使用 Binance leverage endpoint。
18. 使用 private WebSocket。
19. 使用 listenKey。
20. 每条 WebSocket 消息都发报警。
21. 每条 WebSocket 消息都写 Redis。
22. scheduler 每 10 秒拉起脚本。
23. 提交真实密钥。
24. 提交真实日志。
25. 提交 `.env`。
26. 删除、清空或覆盖已有文档。

## 39. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 查看 CLI 帮助：

   python -m scripts.run_price_monitor_10s --help

4. 人工确认没有 REST 最新价格轮询。

5. 人工确认没有写 MySQL K线表。

6. 人工确认没有 DeepSeek、策略建议、交易接口。

7. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 10s WebSocket 价格监控能力"

8. 用户自行推送分支，并进入代码审查流程。

[1]: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams "Connect | Binance Open Platform"
[2]: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream "Mark Price Stream | Binance Open Platform"
