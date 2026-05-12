# 10 WebSocket 10s 价格监控实现说明

## 1. 功能：BTCUSDT 10s 实时价格监控

### 1.1 发起方式

本功能由常驻进程启动，不由 scheduler 每 10 秒反复拉起。

人工调试入口：

```bash
python -m scripts.run_price_monitor_10s --symbol BTCUSDT --trigger-source cli
```

生产进程入口可以由 systemd 或 supervisor 启动：

```bash
python -m scripts.run_price_monitor_10s --symbol BTCUSDT --trigger-source systemd
python -m scripts.run_price_monitor_10s --symbol BTCUSDT --trigger-source supervisor
```

`trigger_source` 只允许 `cli`、`systemd`、`supervisor`。本功能不写正式 K线，因此不使用 K线写入的 `data_source`。

### 1.2 入口文件

入口文件：`scripts/run_price_monitor_10s.py`

入口方法：`main()`

CLI 参数：

- `--symbol`：默认 `BTCUSDT`。
- `--trigger-source`：必填，只允许 `cli|systemd|supervisor`。
- `--monitor-interval-seconds`：默认 10，必须大于等于 1。
- `--price-change-threshold`：默认 `0.01`，表示 1%。
- `--redis-key`：默认 `bitcoin_price`。
- `--redis-ttl-seconds`：默认 120，必须大于等于监控间隔。
- `--enable-price-alerts`：只控制 10s 价格波动提醒，不控制 K线质量失败报警。

脚本只做参数解析、配置构造、调用 service、打印结果和返回退出码。脚本不直接连接 WebSocket，不直接读写 Redis，不直接发送 Hermes，不请求 Binance REST，不写 MySQL。

### 1.3 核心 service

核心文件：`app/market_data/price_monitor/price_monitor_service.py`

核心方法：

- `PriceMonitorService.run_price_monitor()`
- `PriceMonitorService.handle_raw_ws_message()`
- `PriceMonitorService.run_monitor_loop()`
- `PriceMonitorService.check_latest_price_every_interval()`

标准调用链：

```text
scripts/run_price_monitor_10s.py::main
    ↓
app/market_data/price_monitor/price_monitor_service.py::run_price_monitor
    ↓
app/market_data/price_monitor/price_monitor_service.py::PriceMonitorService.run_price_monitor
    ↓
app/exchange/binance/websocket_market_client.py::BinanceWebSocketMarketClient.connect_and_listen
    ↓
app/market_data/price_monitor/price_monitor_service.py::handle_raw_ws_message
    ↓
app/market_data/price_monitor/price_event_parser.py::parse_agg_trade_event
    ↓
app/market_data/price_monitor/price_monitor_service.py::update_latest_price_event
    ↓
每 10 秒进入 check_latest_price_every_interval
    ↓
app/market_data/price_monitor/redis_price_state.py::load_previous_price_state
    ↓
app/market_data/price_monitor/price_change_detector.py::detect_price_change
    ↓
app/market_data/price_monitor/redis_price_state.py::save_current_price_state
    ↓
app/market_data/price_monitor/alert_throttle.py::InMemoryAlertThrottle.should_send_alert
    ↓
app/alerting/service.py::send_alert
```

## 2. 数据来源与 WebSocket

### 2.1 Binance WebSocket client

文件：`app/exchange/binance/websocket_market_client.py`

职责：

- 构建 Binance U 本位合约 public market WebSocket URL。
- 默认构建 stream：`btcusdt@aggTrade`。
- 使用 `/market/ws` 路由连接 public market stream。
- 接收原始 WebSocket 消息并交给上层 callback。
- 断线后按配置的最小/最大间隔退避重连。

本文件不解析价格，不写 Redis，不写 MySQL，不发送 Hermes，不请求 REST 最新价格，不调用 DeepSeek，不涉及交易执行。

URL 构建规则：

```text
BINANCE_WS_BASE_URL.rstrip("/") + "/" + symbol.lower() + "@aggTrade"
```

默认结果：

```text
wss://fstream.binance.com/market/ws/btcusdt@aggTrade
```

### 2.2 PriceEvent 解析

文件：`app/market_data/price_monitor/price_event_parser.py`

方法：`parse_agg_trade_event()`

解析 Binance `aggTrade` 原始消息，要求：

- `e == "aggTrade"`。
- `s` 必须等于请求 symbol，例如 `BTCUSDT`。
- `p` 必须能解析为 `Decimal` 且大于 0。
- `E` 必须存在并解析为毫秒时间戳。
- `T` 必须存在并解析为毫秒时间戳。
- 禁止使用 `float` 解析价格。

返回 `PriceEvent`：

- `symbol`
- `price`
- `event_time_ms`
- `trade_time_ms`
- `received_at_utc`
- `received_at_prc`
- `source=binance_ws_agg_trade`

缺字段、非法 price、symbol 不匹配、事件类型不匹配都会抛出 `PriceEventParseError`。parser 不写 Redis、不写 MySQL、不发送 Hermes。

## 3. 10 秒监控循环

WebSocket 持续接收 `aggTrade` 消息，service 在内存中只保留最新有效 `PriceEvent`。

关键规则：

- 每条 WebSocket tick 只更新内存最新价格。
- 不在每条 tick 中写 Redis。
- 不在每条 tick 中发送 Hermes。
- 每隔 `monitor_interval_seconds`，默认 10 秒，执行一次监控判断。
- 每次监控判断读取 Redis 中上一轮价格，比较变化幅度，然后写入当前最新价格并刷新 TTL。

如果启动后暂时没有收到有效价格事件，service 会等待 `PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS`。超过该时间仍无有效价格时，返回 `no_recent_price` 并可发送固定模板异常报警。该路径不会使用 REST 补价格，也不会写旧价格冒充新价格。

## 4. Redis 状态

文件：`app/market_data/price_monitor/redis_price_state.py`

Redis key：

```text
bitcoin_price
```

默认 TTL：

```text
120 秒
```

Redis value 使用 JSON：

```json
{
  "symbol": "BTCUSDT",
  "price": "65000.12",
  "source": "binance_ws_agg_trade",
  "event_time_ms": 1710000000000,
  "trade_time_ms": 1710000000001,
  "saved_at_utc": "2026-05-12T00:00:00+00:00",
  "saved_at_prc": "2026-05-12T08:00:00+08:00",
  "saved_at_utc_display": "...",
  "saved_at_prc_display": "..."
}
```

读取 Redis 时：

- key 不存在返回 `None`，本轮只初始化状态，不报警。
- 非法 JSON、非法 price、字段缺失会抛出 `PriceStateParseError`，service 记录日志并把上一轮状态视为不可用。
- Redis driver 读取失败会抛出 `RedisError`，service 返回 failed 并发送固定模板系统异常报警。

写 Redis 时：

- 每次 10 秒监控判断都会写入当前最新价格并刷新 TTL。
- 写入失败抛出 `RedisError`，service 返回 failed 并发送固定模板系统异常报警。
- Redis 只保存实时状态，不作为长期行情库。

本功能不写 MySQL K线表，不写采集事件表，不写数据质量记录表。

## 5. 价格变化检测

文件：`app/market_data/price_monitor/price_change_detector.py`

方法：`detect_price_change()`

检测公式：

```text
abs(current_price - previous_price) / previous_price >= threshold
```

默认阈值：

```text
0.01
```

含义：价格变化幅度大于等于 1%。

检测规则：

- 使用 `Decimal` 计算，禁止 `float`。
- `previous_price` 不存在时不报警，只写入 Redis。
- `previous_price <= 0` 时不报警，并在结果中标记原因。
- `current_price <= 0` 时拒绝处理并返回 failed。
- 结果包含 `direction=up/down`、`previous_price`、`current_price`、`change_percent`、`threshold`。

价格变化提醒只是事件提醒，不是交易建议。

## 6. Hermes 报警

Hermes 发送统一通过 `app/alerting/service.py::send_alert`。

触发场景：

- 价格变化超过阈值，且 `PRICE_MONITOR_ENABLE_PRICE_ALERTS=true`，且未命中冷却。
- Redis 读取失败。
- Redis 写入失败。
- parser 连续异常。
- 超过无事件 timeout 仍没有有效价格。
- 监控循环异常退出。

报警内容由代码固定模板生成，包含：

- `symbol`
- `stream`
- `previous_price`
- `current_price`
- `change_percent`
- `direction`
- `threshold`
- `event_time_ms`
- `trade_time_ms`
- `monitor_interval_seconds`
- `redis_key`
- `source`
- 明确这是价格事件提醒

本功能不调用 DeepSeek 或其他大模型生成报警内容，不生成交易建议，不执行自动交易。

### 6.1 报警冷却

文件：`app/market_data/price_monitor/alert_throttle.py`

默认冷却：

```text
60 秒
```

规则：

- 同一 `symbol + alert_type` 在冷却期内最多报警一次。
- 冷却状态当前保存在进程内存。
- 冷却命中只抑制 Hermes 发送，不影响 Redis 写入。

### 6.2 Hermes 失败

如果 Hermes 发送失败：

- service 返回非 0 exit code。
- 不回滚或影响已经写入 Redis 的当前价格。
- 不写正式 K线表。
- 不执行自动修复。

## 7. WebSocket 断线与异常

`BinanceWebSocketMarketClient.connect_and_listen()` 负责连接和重连：

- WebSocket 异常断开时记录日志。
- 使用最小/最大重连间隔退避重连。
- 重连不切换到 REST 最新价格接口。
- 重连成功后继续把原始消息交给 service callback。

service 负责业务异常：

- parser 连续异常达到阈值后发送固定模板报警。
- Redis 异常返回 failed 并发送固定模板报警。
- 无最新价格超过 timeout 返回 `no_recent_price` 并发送固定模板报警。
- monitor loop 未捕获异常返回 failed 并发送固定模板报警。

## 8. 配置

配置统一从 `app/core/config.py` 读取，`.env.example` 已补齐示例项：

- `BINANCE_WS_BASE_URL`
- `PRICE_MONITOR_SYMBOL`
- `PRICE_MONITOR_WS_STREAM`
- `PRICE_MONITOR_INTERVAL_SECONDS`
- `PRICE_MONITOR_CHANGE_THRESHOLD`
- `PRICE_MONITOR_REDIS_KEY`
- `PRICE_MONITOR_REDIS_TTL_SECONDS`
- `PRICE_MONITOR_ALERT_COOLDOWN_SECONDS`
- `PRICE_MONITOR_ENABLE_PRICE_ALERTS`
- `PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS`
- `PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS`
- `PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS`

`PRICE_MONITOR_ENABLE_PRICE_ALERTS` 只控制 10s 价格波动提醒，不控制 K线质量失败报警。

## 9. 模块边界

`app/exchange/binance/websocket_market_client.py` 只负责 Binance public market WebSocket 连接和重连，不做业务判断。

`app/market_data/price_monitor/` 负责：

- 解析 aggTrade。
- 保存最新内存 PriceEvent。
- 每 10 秒读取 Redis 上一轮价格。
- 检测价格变化。
- 写 Redis `bitcoin_price`。
- 通过 `app/alerting` 发送固定模板价格提醒或异常提醒。

本阶段明确不做：

- 不请求 REST 最新价格接口。
- 不使用 markPrice 写 `bitcoin_price`。
- 不采集 4h K线。
- 不写 `market_kline_4h`。
- 不写 `collector_event_log`。
- 不写 `data_quality_check`。
- 不创建 Alembic migration。
- 不调用 DeepSeek。
- 不生成交易建议。
- 不实现自动交易。
- 不读取账户、订单、仓位、杠杆等私有能力。
- 不由 scheduler 每 10 秒拉起脚本。

## 10. 测试与检查

对应测试文件：

```text
tests/test_price_monitor_10s.py
```

默认测试使用 mock/fake：

- 不连接真实 Binance。
- 不连接真实 Redis。
- 不发送真实 Hermes。
- 不连接真实 MySQL。
- 不调用 DeepSeek。
- 不访问任何交易接口。

已覆盖：

- CLI 缺少或非法 `trigger_source` 时拒绝。
- WebSocket URL 为 `btcusdt@aggTrade`。
- parser 解析合法 aggTrade，并拒绝缺 price、非法 Decimal、symbol 不匹配。
- Decimal 价格变化检测上涨/下跌超过 1%。
- previous 不存在时不报警。
- Redis 写入 TTL=120。
- Redis 读取异常抛出明确错误。
- alert throttle 冷却期阻止重复报警。
- service 每 10 秒判断一次。
- WebSocket 断线重连可 mock。
- 超过阈值调用 alerting mock。
- 未超过阈值不调用 alerting。
- 无最新价格超过 timeout 生成异常状态。
- 不请求 REST 最新价格，不写 MySQL K线表，不调用 DeepSeek，不涉及交易接口。

人工检查命令：

```bash
python -m py_compile app/exchange/binance/websocket_market_client.py
python -m py_compile app/market_data/price_monitor/*.py
python -m py_compile scripts/run_price_monitor_10s.py
python -m scripts.run_price_monitor_10s --help
python -m pytest tests/test_price_monitor_10s.py
python -m pytest
```

