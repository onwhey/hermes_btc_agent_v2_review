# 05 Binance REST Client Plan

## 1. 阶段目标

本阶段实现 Binance USDⓈ-M Futures 公开 REST 客户端。

本阶段只为后续 4h 官方 K线采集、手动回补、K线复核提供 Binance REST 基础能力。

本阶段目标包括：

1. Binance REST 基础请求封装。
2. Binance REST 连通性检查。
3. Binance server time 查询。
4. Binance exchangeInfo 查询。
5. Binance 官方 K线 `/fapi/v1/klines` 查询。
6. 请求超时、重试、错误分类。
7. 请求参数校验。
8. 响应基础校验。
9. Binance REST 检查脚本。
10. 对应测试文件。
11. 对应实现说明文件。

本阶段不实现最新价格监控。

10s 价格监控后续必须走 Binance WebSocket，不走 REST 轮询。

## 2. 本阶段明确不做

本阶段不得实现行情采集入库、价格监控、报警、策略或交易业务。

禁止实现：

1. 4h K线入库。
2. 4h K线回补。
3. K线一致性复核。
4. K线质量检查入库。
5. collector_event_log。
6. data_quality_check。
7. 10s 价格监控。
8. WebSocket。
9. Redis 写入 `bitcoin_price`。
10. REST 最新价格轮询。
11. Hermes 报警。
12. DeepSeek 或其他大模型调用。
13. scheduler 定时任务。
14. 策略分析。
15. 交易建议。
16. 自动下单、自动平仓、自动调仓。
17. Binance 账户接口。
18. Binance 订单接口。
19. Binance 持仓接口。
20. Binance 杠杆接口。
21. Binance 保证金模式接口。
22. Binance 用户数据流。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/02_data_collection_requirements.md`
3. `docs/requirements/03_database_and_quality_requirements.md`
4. `docs/architecture/system_architecture.md`
5. `docs/architecture/module_boundaries.md`
6. `docs/architecture/data_flow.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/plans/01_project_skeleton.md`
10. `docs/plans/02_core_config_logging.md`
11. `docs/plans/03_infra_mysql_redis.md`
12. `docs/plans/04_alerting_through_hermes.md`
13. `docs/implementation/01_project_skeleton.md`
14. `docs/implementation/02_core_config_logging.md`
15. `docs/implementation/03_infra_mysql_redis.md`
16. `docs/implementation/04_alerting_through_hermes.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`

本阶段不得重复实现配置读取、日志初始化、时间转换和基础异常逻辑。

## 4. 建议分支

建议分支名：

`feature/05-binance-rest-client`

分支创建、切换、提交、推送、合并由用户人工执行。

Codex 不应自动执行以下 Git 操作：

1. 创建分支。
2. 切换分支。
3. 合并分支。
4. 推送远程仓库。
5. 删除分支。
6. 强制覆盖工作区。

Codex 只负责在用户已经切换好的当前分支内，根据本 plan 修改文件。

## 5. 需要检查和补齐的目录

本阶段应检查以下目录是否存在，不存在才创建：

```
app/exchange/
app/exchange/binance/
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

禁止执行类似以下危险操作：

1. 删除整个 `docs/` 后重建。
2. 清空已有文档目录。
3. 删除 `app/exchange/binance/` 后重建。
4. 覆盖已有配置、日志、数据库、报警模块。
5. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/exchange/__init__.py
app/exchange/binance/__init__.py
app/exchange/binance/client.py
app/exchange/binance/endpoints.py
app/exchange/binance/exceptions.py
app/exchange/binance/types.py
app/exchange/binance/validators.py

scripts/check_binance_rest.py
tests/test_binance_rest_client.py
docs/implementation/05_binance_rest_client.md

.env.example
pyproject.toml
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. Binance REST 模块边界

Binance REST 模块路径：

`app/exchange/binance/`

该模块只负责：

1. 维护 Binance REST base URL。
2. 维护允许使用的公开 REST endpoint。
3. 构造请求参数。
4. 发起 HTTP GET 请求。
5. 处理超时、网络异常、HTTP 错误。
6. 解析 JSON 响应。
7. 做基础响应校验。
8. 抛出可识别异常。
9. 返回 Binance 原始响应或轻量结构化响应。

该模块不负责：

1. 判断 K线是否应该入库。
2. 判断 K线是否连续。
3. 判断 K线是否已收盘。
4. 转换内部 Kline DTO。
5. 写 MySQL。
6. 写 Redis。
7. 写 collector_event_log。
8. 写 data_quality_check。
9. 发送 Hermes。
10. 调用 DeepSeek。
11. 生成交易建议。
12. 下单、撤单、平仓、调仓。
13. 价格监控。
14. WebSocket 连接。

重要边界：

`app/exchange/binance` 不得直接调用 `app/alerting`。

Binance client 只负责请求 Binance、解析响应、抛出异常或返回结果。是否报警由上层业务 service 判断。

## 8. Binance 配置要求

配置必须复用：

`app/core/config.py`

建议支持以下配置项：

```
BINANCE_BASE_URL
BINANCE_TIMEOUT_SECONDS
BINANCE_MAX_RETRIES
BINANCE_RETRY_BACKOFF_SECONDS
BINANCE_DEFAULT_SYMBOL
BINANCE_DEFAULT_INTERVAL
BINANCE_KLINE_DEFAULT_LIMIT
BINANCE_KLINE_MAX_LIMIT
```

建议默认值：

```
BINANCE_BASE_URL=https://fapi.binance.com
BINANCE_TIMEOUT_SECONDS=10
BINANCE_MAX_RETRIES=2
BINANCE_RETRY_BACKOFF_SECONDS=1
BINANCE_DEFAULT_SYMBOL=BTCUSDT
BINANCE_DEFAULT_INTERVAL=4h
BINANCE_KLINE_DEFAULT_LIMIT=10
BINANCE_KLINE_MAX_LIMIT=1500
```

要求：

1. `BINANCE_BASE_URL` 不得为空。
2. timeout 必须转换为数字。
3. max retries 必须转换为整数。
4. limit 必须转换为整数。
5. 不得从配置读取 API key。
6. 不得从配置读取 secret key。
7. 不得实现 signed request。
8. 不得打印完整请求参数中的敏感字段。

本阶段只调用公开市场数据接口，不需要 Binance API key。

## 9. `.env.example` 更新要求

如果 `.env.example` 已存在，只允许补齐缺失项，不得清空重写。

建议包含：

```
BINANCE_BASE_URL=https://fapi.binance.com
BINANCE_TIMEOUT_SECONDS=10
BINANCE_MAX_RETRIES=2
BINANCE_RETRY_BACKOFF_SECONDS=1
BINANCE_DEFAULT_SYMBOL=BTCUSDT
BINANCE_DEFAULT_INTERVAL=4h
BINANCE_KLINE_DEFAULT_LIMIT=10
BINANCE_KLINE_MAX_LIMIT=1500
```

禁止：

1. 写入 Binance API key。
2. 写入 Binance secret key。
3. 写入账户信息。
4. 写入真实 token。
5. 写入交易权限配置。
6. 覆盖已有有效配置。

## 10. 允许实现的公开 REST endpoint

本阶段只允许实现以下 Binance USDⓈ-M Futures 公开 REST endpoint：

1. `GET /fapi/v1/ping`
2. `GET /fapi/v1/time`
3. `GET /fapi/v1/exchangeInfo`
4. `GET /fapi/v1/klines`

用途：

1. `/fapi/v1/ping`：检查 REST API 连通性。
2. `/fapi/v1/time`：获取 Binance server time。
3. `/fapi/v1/exchangeInfo`：获取交易规则和 symbol 信息。
4. `/fapi/v1/klines`：获取官方 K线。

注意：

1. 4h 正式 K线后续必须来自 `/fapi/v1/klines` 返回的官方已收盘 K线。
2. 本阶段只封装接口，不做正式 K线写库。
3. 本阶段不实现 REST 最新价格接口。
4. 本阶段不实现 WebSocket。

## 11. 禁止实现的 Binance endpoint

本阶段禁止实现任何交易、账户、持仓、用户数据、最新价格轮询相关 endpoint。

禁止：

1. `/fapi/v1/order`
2. `/fapi/v1/batchOrders`
3. `/fapi/v1/allOrders`
4. `/fapi/v1/openOrders`
5. `/fapi/v1/account`
6. `/fapi/v2/account`
7. `/fapi/v3/account`
8. `/fapi/v1/positionRisk`
9. `/fapi/v2/positionRisk`
10. `/fapi/v3/positionRisk`
11. `/fapi/v1/leverage`
12. `/fapi/v1/marginType`
13. `/fapi/v1/listenKey`
14. `/fapi/v1/ticker/price`
15. `/fapi/v2/ticker/price`
16. 任何 signed endpoint。
17. 任何需要 API key 的 endpoint。
18. 任何会改变账户状态的 endpoint。

如果 Codex 添加以上 endpoint，应直接拒绝合并。

说明：

REST 最新价格接口不属于本阶段。

10s 价格监控必须在后续 WebSocket 阶段实现，不得在本阶段通过 REST ticker price 实现。

## 12. endpoints 模块要求

建议文件：

`app/exchange/binance/endpoints.py`

职责：

1. 集中定义允许的 endpoint path。
2. 防止 endpoint 字符串散落在业务代码中。
3. 明确只允许公开 K线和基础连通性 endpoint。
4. 明确禁止交易 endpoint 和 REST 最新价格 endpoint。

建议包含：

```
FUTURES_PING = "/fapi/v1/ping"
FUTURES_SERVER_TIME = "/fapi/v1/time"
FUTURES_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
FUTURES_KLINES = "/fapi/v1/klines"
```

禁止在本阶段定义：

```
FUTURES_TICKER_PRICE
FUTURES_TICKER_PRICE_V2
FUTURES_ORDER
FUTURES_ACCOUNT
FUTURES_POSITION
FUTURES_LEVERAGE
FUTURES_MARGIN_TYPE
FUTURES_LISTEN_KEY
```

## 13. client 模块要求

建议文件：

`app/exchange/binance/client.py`

建议类名：

`BinanceRestClient`

建议方法：

1. `ping()`
2. `get_server_time()`
3. `get_exchange_info()`
4. `get_klines(symbol, interval, start_time_ms=None, end_time_ms=None, limit=None)`

内部可提供私有方法：

1. `_request(method, path, params=None)`
2. `_get(path, params=None)`
3. `_build_url(path)`
4. `_handle_response(response)`
5. `_sleep_before_retry(attempt)`

要求：

1. 只允许 GET 请求。
2. 只允许本 plan 第 10 节列出的公开 endpoint。
3. 所有请求必须有 timeout。
4. 网络异常必须转换为项目可识别异常。
5. HTTP 非 2xx 必须转换为项目可识别异常。
6. JSON 解析失败必须转换为项目可识别异常。
7. Binance 返回错误码时必须转换为项目可识别异常。
8. 不得吞掉异常后返回空数据。
9. 不得把完整异常中的敏感信息写入日志。
10. 不得在 client 中调用报警模块。
11. 不得实现 `get_latest_price()`。
12. 不得实现 WebSocket。

## 14. 请求库要求

本阶段可选择：

1. `httpx`
2. `requests`

二选一即可，不要同时引入两个请求库。

推荐优先使用 `httpx`，但如果前面项目已经引入 `requests`，也可以继续使用 `requests`，以减少依赖复杂度。

要求：

1. 请求必须设置 timeout。
2. 不允许无超时请求。
3. 不允许无限重试。
4. 不允许在测试中真实依赖 Binance。
5. HTTP client 应便于 mock。

## 15. 重试要求

本阶段允许实现轻量重试。

建议规则：

1. 网络连接错误可以重试。
2. timeout 可以重试。
3. HTTP 5xx 可以重试。
4. HTTP 429 可以重试，但必须记录警告日志。
5. HTTP 4xx 一般不重试。
6. JSON 解析失败不重试。
7. 参数错误不重试。

要求：

1. 重试次数由配置控制。
2. 默认最多 2 次重试。
3. 重试间隔由配置控制。
4. 不得无限重试。
5. 不得因重试导致脚本长时间卡死。
6. 所有重试日志不得包含敏感信息。

## 16. 参数校验要求

参数校验建议放在：

`app/exchange/binance/validators.py`

必须校验：

1. symbol 非空。
2. symbol 默认 `BTCUSDT`。
3. interval 非空。
4. 本阶段默认允许 `4h`。
5. limit 必须为正整数。
6. limit 不得超过 `BINANCE_KLINE_MAX_LIMIT`。
7. start_time_ms 必须为毫秒时间戳或 None。
8. end_time_ms 必须为毫秒时间戳或 None。
9. 如果 start_time_ms 和 end_time_ms 同时存在，start_time_ms 必须小于 end_time_ms。

本阶段可以先允许：

```
1m
4h
1d
```

说明：

1. `4h` 是第一阶段正式 K线主周期。
2. `1m` 和 `1d` 可作为后续数据基础预留。
3. 允许参数化不代表提前实现小周期策略。
4. 不得在本阶段实现多周期入库。

## 17. K线接口要求

`get_klines()` 只负责请求 Binance `/fapi/v1/klines` 并返回响应。

参数：

1. `symbol`
2. `interval`
3. `start_time_ms`
4. `end_time_ms`
5. `limit`

要求：

1. Python 内部参数使用 `start_time_ms`、`end_time_ms`。
2. 发送给 Binance 时转换为官方参数名 `startTime`、`endTime`、`limit`。
3. 不得在 client 中判断 K线是否已收盘。
4. 不得在 client 中判断 K线是否连续。
5. 不得在 client 中写入数据库。
6. 不得在 client 中构造内部 Kline DTO。
7. 不得在 client 中补齐缺失 K线。
8. 不得在 client 中过滤重复 K线。
9. 不得在 client 中比较 DB K线。
10. 不得在 client 中记录 `data_source`。
11. 不得在 client 中记录 `trigger_source`。

K线解析、过滤未收盘、连续性校验、字段合理性校验、`data_source` 映射、`trigger_source` 记录、幂等写入应在后续 market_data 相关模块实现。

## 18. server time 要求

`get_server_time()` 使用：

`GET /fapi/v1/time`

要求：

1. 返回 Binance serverTime 毫秒时间戳。
2. 可转换为 UTC datetime。
3. 不得用于修改本机时间。
4. 不得写数据库。
5. 不得写 Redis。
6. 不得自动报警。

server time 主要用于：

1. 连通性检查。
2. 排查本机时间与 Binance 时间差。
3. 后续采集任务的辅助诊断。

## 19. exchangeInfo 要求

`get_exchange_info()` 使用：

`GET /fapi/v1/exchangeInfo`

本阶段只封装查询，不做复杂业务解释。

允许后续用于：

1. 检查 BTCUSDT 是否存在。
2. 检查 symbol 状态。
3. 辅助排查 Binance 交易规则变化。

本阶段禁止：

1. 根据 exchangeInfo 自动下单。
2. 根据 exchangeInfo 调杠杆。
3. 根据 exchangeInfo 修改保证金模式。
4. 根据 exchangeInfo 自动生成交易建议。
5. 在本阶段实现复杂交易规则解析。

## 20. 价格监控数据源边界

本阶段不实现最新价格 REST 查询。

本阶段不实现：

1. `/fapi/v1/ticker/price`
2. `/fapi/v2/ticker/price`
3. `get_latest_price()`
4. REST 轮询价格。
5. 10s 价格监控。
6. Redis `bitcoin_price` 写入。
7. WebSocket。

后续 10s 价格监控必须使用 Binance WebSocket 接收价格数据。

后续 10s 价格监控大致边界为：

```
Binance WebSocket
    ↓
接收 BTCUSDT 实时价格事件
    ↓
价格监控 service
    ↓
读取 Redis 中上一轮价格
    ↓
比较价格变化幅度
    ↓
必要时调用 app/alerting 发送 Hermes 报警
    ↓
写入 Redis bitcoin_price，TTL 2 分钟
```

以上流程不属于本阶段。

## 21. 异常模块要求

建议文件：

`app/exchange/binance/exceptions.py`

允许定义：

1. `BinanceError`
2. `BinanceRequestError`
3. `BinanceTimeoutError`
4. `BinanceHTTPError`
5. `BinanceRateLimitError`
6. `BinanceResponseError`
7. `BinanceValidationError`

要求：

1. 异常应继承项目基础异常或清晰独立。
2. 异常消息不得包含敏感信息。
3. HTTP 状态码可以保留。
4. Binance 错误码可以保留。
5. request path 可以保留。
6. 完整 URL 如包含敏感参数不得保留。
7. 不得因为异常自动发送 Hermes。

## 22. 类型定义要求

建议文件：

`app/exchange/binance/types.py`

可以定义轻量类型，例如：

1. `BinanceKlineRaw`
2. `BinanceServerTime`
3. `BinanceRequestResult`

注意：

1. 类型定义只描述 Binance REST 响应。
2. 不得定义内部正式 Kline DTO。
3. 不得定义 market_kline_4h ORM model。
4. 不得定义价格监控事件。
5. 不得定义 WebSocket 消息类型。
6. 不得定义交易信号类型。
7. 不得定义订单类型。
8. 不得定义持仓类型。

内部 Kline DTO 应在后续 market_data 模块中定义。

WebSocket 价格事件类型应在后续价格监控模块中定义。

## 23. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. Binance REST 请求开始。
2. Binance REST 请求成功。
3. Binance REST 请求失败。
4. 请求耗时。
5. retry 次数。
6. HTTP status code。
7. endpoint path。
8. symbol。
9. interval。

禁止记录：

1. API key。
2. secret。
3. Authorization。
4. cookie。
5. 完整 `.env`。
6. 完整异常堆栈中可能含敏感信息的 URL。
7. 交易相关信息。
8. 账户信息。
9. 持仓信息。

本阶段没有 API key，但仍要保留日志脱敏习惯。

## 24. 检查脚本要求

建议创建：

`scripts/check_binance_rest.py`

该脚本用于人工检查 Binance REST client。

允许检查：

1. 配置加载。
2. Binance ping。
3. Binance server time。
4. BTCUSDT exchangeInfo 中是否存在。
5. BTCUSDT 最近若干根 4h K线是否能获取。

禁止该脚本：

1. 查询 REST 最新价格。
2. 写 MySQL。
3. 写 Redis。
4. 创建 `bitcoin_price`。
5. 发送 Hermes。
6. 启动 scheduler。
7. 执行 K线采集入库。
8. 执行 K线回补。
9. 执行 K线复核。
10. 调用 DeepSeek。
11. 下单、撤单、调杠杆、读账户、读持仓。
12. 连接 WebSocket。

示例运行方式：

```
python -m scripts.check_binance_rest
```

说明：

1. 该脚本是人工 CLI 检查入口。
2. 本阶段不得让 scheduler 调用该脚本。
3. 该脚本不得承载业务逻辑。
4. 该脚本不得写数据库。
5. 该脚本不得发送报警。
6. 该脚本不得实现价格监控。

## 25. 测试要求

建议创建：

`tests/test_binance_rest_client.py`

默认测试不得依赖真实 Binance 网络。

至少覆盖：

1. client 可以正常初始化。
2. base URL 可以正确拼接。
3. ping endpoint path 正确。
4. time endpoint path 正确。
5. exchangeInfo endpoint path 正确。
6. klines endpoint path 正确。
7. endpoints 模块不包含 ticker price endpoint。
8. endpoints 模块不包含 order/account/position/leverage endpoint。
9. client 不存在 `get_latest_price()`。
10. client 不实现 WebSocket。
11. `get_klines()` 参数能正确转换。
12. limit 超过最大值时抛出校验异常。
13. start_time_ms >= end_time_ms 时抛出校验异常。
14. timeout 异常能转换为 BinanceTimeoutError。
15. HTTP 429 能转换为 BinanceRateLimitError。
16. HTTP 5xx 能按规则重试。
17. JSON 解析失败能转换为 BinanceResponseError。
18. 重试次数不会无限增长。
19. client 不调用 MySQL。
20. client 不调用 Redis。
21. client 不调用 Hermes。
22. client 不调用 DeepSeek。
23. client 不包含 signed request。

如果需要真实 Binance 集成测试，必须使用显式开关，例如：

```
RUN_BINANCE_INTEGRATION_TESTS=true
```

默认 `pytest` 不应访问 Binance。

## 26. pyproject 依赖要求

如需新增依赖，必须最小化。

允许新增：

1. `httpx`

或如果项目已使用 requests：

1. `requests`

只能选择一个 HTTP 请求库。

禁止新增：

1. Binance SDK。
2. ccxt。
3. 交易 SDK。
4. WebSocket SDK。
5. 大模型 SDK。
6. 量化交易框架。
7. 不必要的重型依赖。

本阶段不应引入账户交易相关依赖。

## 27. 数据库影响

本阶段不得连接 MySQL。

本阶段不得写 MySQL。

本阶段不得创建、修改、删除任何数据库表。

本阶段不得写入：

1. market_kline_4h。
2. collector_event_log。
3. data_quality_check。
4. alert_message。
5. 策略表。
6. 建议表。

Binance REST client 不得依赖数据库。

## 28. Redis 影响

本阶段不得连接 Redis。

本阶段不得写 Redis。

本阶段不得读取 Redis。

本阶段不得创建：

`bitcoin_price`

价格监控和 Redis 写入应在后续 WebSocket 价格监控阶段实现。

## 29. Hermes 影响

本阶段不得调用 Hermes。

本阶段不得发送报警。

本阶段不得写 alert_message。

如果 Binance 请求失败：

1. client 抛出异常。
2. 上层 service 后续决定是否报警。
3. 本阶段不做报警编排。

## 30. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得创建定时任务。

本阶段不得让 scheduler 调用 `scripts/check_binance_rest.py`。

scheduler 与 `trigger_source` 的实际运行逻辑应在后续采集相关 plan 中实现。

## 31. K线数据源边界

本阶段实现的 `/fapi/v1/klines` 是后续正式 4h K线的唯一官方 REST 数据入口。

但本阶段不写正式 K线表。

后续正式 4h K线写入必须满足：

1. 来自 Binance REST `/fapi/v1/klines`。
2. 来自官方已收盘 K线。
3. 经过 parser 转换。
4. 经过未收盘过滤。
5. 经过连续性校验。
6. 经过字段合理性校验。
7. 经过幂等写入规则。
8. 记录正确 `data_source`。
9. 记录正确 `trigger_source`。

本阶段只提供 REST 获取能力，不实现以上流程。

## 32. WebSocket 边界

本阶段不得实现 WebSocket。

不得创建：

1. WebSocket client。
2. WebSocket manager。
3. WebSocket reconnect loop。
4. WebSocket price event parser。
5. WebSocket price monitor。
6. WebSocket 写 Redis 逻辑。

后续 10s 价格监控计划中再实现 WebSocket。

本阶段如果出现任何 WebSocket 代码，应视为越界。

## 33. 安全要求

本阶段必须防止误引入交易能力。

禁止：

1. 配置 API key。
2. 配置 secret key。
3. 实现 signed request。
4. 实现 POST/PUT/DELETE 请求。
5. 实现 order endpoint。
6. 实现 account endpoint。
7. 实现 position endpoint。
8. 实现 leverage endpoint。
9. 实现 margin endpoint。
10. 实现 listenKey。
11. 引入自动交易相关依赖。
12. 在文档中暗示系统可以自动交易。

本阶段所有请求必须是公开市场数据 GET 请求。

## 34. 交易安全边界

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

## 35. 交付物要求

本阶段完成后，Codex 必须交付：

1. Binance REST client。
2. Binance endpoint 常量。
3. Binance 请求参数校验模块。
4. Binance 异常模块。
5. Binance 响应类型定义。
6. `.env.example` 必要补充。
7. `pyproject.toml` 必要依赖补充。
8. Binance REST 检查脚本。
9. Binance REST 测试文件。
10. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/05_binance_rest_client.md`

说明文件必须描述：

1. 本模块入口。
2. 支持的公开 REST endpoint。
3. 禁止的交易 endpoint。
4. 禁止的 REST 最新价格 endpoint。
5. 请求超时和重试策略。
6. 参数校验流程。
7. 异常处理流程。
8. 日志脱敏原则。
9. 本模块不负责的边界。
10. 后续哪些模块会复用本模块。

本阶段说明文件不需要描述：

1. K线入库流程。
2. K线连续性校验流程。
3. K线复核流程。
4. Hermes 告警流程。
5. Redis 价格缓存流程。
6. WebSocket 价格监控流程。
7. 策略建议流程。

原因：这些能力本阶段不实现。

## 36. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_binance_rest` 可以在网络可用时运行成功。
2. Binance 网络不可用时，检查脚本明确失败，不假装成功。
3. `pytest` 默认可以运行成功。
4. 默认测试不依赖真实 Binance。
5. `app.exchange.binance.client` 可以正常导入。
6. `app.exchange.binance.endpoints` 可以正常导入。
7. `app.exchange.binance.exceptions` 可以正常导入。
8. `get_klines()` 使用 `/fapi/v1/klines`。
9. `ping()` 使用 `/fapi/v1/ping`。
10. `get_server_time()` 使用 `/fapi/v1/time`。
11. `get_exchange_info()` 使用 `/fapi/v1/exchangeInfo`。
12. 未实现 `/fapi/v1/ticker/price`。
13. 未实现 `/fapi/v2/ticker/price`。
14. 未实现 `get_latest_price()`。
15. 未实现 WebSocket。
16. 未实现任何 order endpoint。
17. 未实现任何 account endpoint。
18. 未实现任何 position endpoint。
19. 未实现任何 leverage endpoint。
20. 未实现 signed request。
21. 未读取 Binance API key。
22. 未读取 Binance secret key。
23. 未连接 MySQL。
24. 未连接 Redis。
25. 未调用 Hermes。
26. 未实现 scheduler。
27. 未写入 K线表。
28. 未写入 `bitcoin_price`。
29. 未实现交易建议。
30. 未实现交易执行相关代码。
31. `docs/implementation/05_binance_rest_client.md` 已创建或补齐。

## 37. 人工审查清单

合并前用户应人工检查：

1. 查看 `app/exchange/binance/` 是否只包含公开 REST client 相关模块。
2. 查看 endpoint 常量是否只包含允许的公开市场数据 endpoint。
3. 查看是否存在 ticker price endpoint。
4. 查看是否存在 WebSocket 代码。
5. 查看是否存在 order/account/position/leverage/margin endpoint。
6. 查看是否存在 API key 或 secret key 配置。
7. 查看是否存在 signed request。
8. 查看是否存在 POST/PUT/DELETE 请求。
9. 查看是否存在 MySQL 写入。
10. 查看是否存在 Redis 写入。
11. 查看是否存在 Hermes 调用。
12. 查看检查脚本是否只做人工检查。
13. 查看测试是否默认 mock Binance。
14. 运行测试。
15. 在网络允许时运行 Binance REST 检查脚本。

建议搜索：

```
grep -R "ticker/price" app/exchange scripts tests
grep -R "get_latest_price" app/exchange scripts tests
grep -R "websocket" app/exchange scripts tests
grep -R "ws" app/exchange scripts tests
grep -R "order" app/exchange scripts tests
grep -R "account" app/exchange scripts tests
grep -R "position" app/exchange scripts tests
grep -R "leverage" app/exchange scripts tests
grep -R "marginType" app/exchange scripts tests
grep -R "listenKey" app/exchange scripts tests
grep -R "api_key" app/exchange scripts tests
grep -R "secret" app/exchange scripts tests
grep -R "signed" app/exchange scripts tests
grep -R "POST" app/exchange scripts tests
grep -R "create_engine" app/exchange scripts tests
grep -R "redis" app/exchange scripts tests
grep -R "Hermes" app/exchange scripts tests
grep -R "alert" app/exchange scripts tests
grep -R "market_kline" app/exchange scripts tests
grep -R "bitcoin_price" app/exchange scripts tests
```

如果搜索结果只是文档、注释或允许的错误文本，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 38. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 实现 REST 最新价格接口。
2. 实现 `get_latest_price()`。
3. 实现 WebSocket。
4. 实现 10s 价格监控。
5. 写入 Redis `bitcoin_price`。
6. 实现任何交易 endpoint。
7. 实现任何账户 endpoint。
8. 实现任何持仓 endpoint。
9. 实现任何杠杆 endpoint。
10. 实现任何保证金 endpoint。
11. 实现 signed request。
12. 读取 Binance API key。
13. 读取 Binance secret key。
14. 引入 Binance SDK。
15. 引入 ccxt。
16. 写入 MySQL。
17. 写入 Redis。
18. 调用 Hermes。
19. 调用 DeepSeek。
20. 实现 scheduler。
21. 实现 K线采集入库。
22. 实现 K线回补。
23. 实现 K线复核。
24. 创建 K线表。
25. 创建采集事件表。
26. 创建数据质量检查表。
27. 创建策略表。
28. 创建建议表。
29. 生成交易建议。
30. 实现任何交易执行代码。
31. 提交真实密钥。
32. 提交真实日志。
33. 提交 `.env`。
34. 删除、清空或覆盖已有文档。
35. 把业务采集逻辑写进 `scripts`。

## 39. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 运行 Binance REST 检查：

   python -m scripts.check_binance_rest

4. 人工确认没有异常删除、覆盖或越界实现。

5. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 Binance REST K线客户端"

6. 用户自行推送分支，并进入代码审查流程。

[1]: https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info "General Info | Binance Open Platform"
