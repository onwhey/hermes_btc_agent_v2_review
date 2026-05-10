# 05 Binance REST Client 实现说明

## 1. 功能：Binance U 本位公开 REST client

### 1.1 发起方式

本阶段提供代码入口，不自动发起请求。

可由用户手动检查脚本触发：

```bash
python -m scripts.check_binance_rest --request-real-binance
```

默认 dry-run：

```bash
python -m scripts.check_binance_rest
```

dry-run 只检查配置和 Kline 参数构造，不真实请求 Binance。

### 1.2 入口文件

核心入口文件：

`app/exchange/binance/rest_client.py`

入口方法：

- `BinanceRestClient.ping()`
- `BinanceRestClient.get_server_time()`
- `BinanceRestClient.get_exchange_info()`
- `BinanceRestClient.get_klines()`

### 1.3 核心调用链路

手动真实连通性检查：

```text
scripts/check_binance_rest.py::main
    ↓
scripts/check_binance_rest.py::collect_binance_rest_errors
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.ping
app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
app/exchange/binance/rest_client.py::BinanceRestClient.get_exchange_info
app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient._request_json
    ↓
app/exchange/binance/rest_client.py::default_http_get
```

测试调用链路：

```text
tests/test_binance_rest_client.py
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient
    ↓
FakeHttpGet
```

默认 pytest 使用 `FakeHttpGet`，不真实请求 Binance。

### 1.4 配置项

配置统一从 `app/core/config.py::load_settings()` 读取。

新增或使用的 Binance 配置项：

- `BINANCE_BASE_URL`：默认 `https://fapi.binance.com`
- `BINANCE_TIMEOUT_SECONDS`：每次 HTTP 请求 timeout
- `BINANCE_MAX_RETRIES`：最大重试次数，必须为非负整数
- `BINANCE_RETRY_BACKOFF_SECONDS`：重试等待基准秒数
- `BINANCE_DEFAULT_SYMBOL`：默认 `BTCUSDT`
- `BINANCE_DEFAULT_INTERVAL`：默认 `4h`
- `BINANCE_KLINE_DEFAULT_LIMIT`：默认 Kline 请求条数
- `BINANCE_KLINE_MAX_LIMIT`：Kline 请求最大条数

本功能不从业务代码中散落读取 `os.getenv`。

### 1.5 REST base URL 与 endpoint

REST base URL：

`https://fapi.binance.com`

本阶段只允许公开 REST endpoint：

- `/fapi/v1/ping`
- `/fapi/v1/time`
- `/fapi/v1/exchangeInfo`
- `/fapi/v1/klines`

明确禁止：

- Binance 私有接口
- API key / secret 签名请求
- account、order、position、leverage、margin、listenKey 相关接口
- `/fapi/v1/ticker/price` 作为 10s 价格监控来源
- WebSocket

### 1.6 HTTP 请求流程

`BinanceRestClient.__init__()` 只校验配置，不连接 Binance。

真实请求只发生在调用以下方法时：

- `ping()`
- `get_server_time()`
- `get_exchange_info()`
- `get_klines()`

请求流程：

```text
公开方法
    ↓
_request_json(path, params)
    ↓
_build_public_url(path)
    ↓
default_http_get(url, params, timeout)
    ↓
urllib.request.urlopen(..., timeout=...)
    ↓
_parse_successful_response(path, response)
```

所有 HTTP 请求都会设置 `timeout`。

### 1.7 timeout 与重试策略

timeout 由 `BINANCE_TIMEOUT_SECONDS` 控制。

重试由 `BINANCE_MAX_RETRIES` 控制：

- 不允许无限重试。
- `BINANCE_MAX_RETRIES=0` 时只请求一次。
- 仅对 timeout、网络错误、HTTP 429、HTTP 5xx 做有限重试。
- HTTP 4xx 非 429 直接失败。

重试等待由 `BINANCE_RETRY_BACKOFF_SECONDS` 控制。

### 1.8 错误处理

异常定义在：

`app/exchange/binance/exceptions.py`

异常路径：

- 配置或参数错误：`BinanceValidationError`
- timeout：`BinanceTimeoutError`
- 网络错误：`BinanceRequestError`
- HTTP 非成功状态：`BinanceHTTPError`
- HTTP 429：`BinanceRateLimitError`
- JSON 解析失败或响应结构异常：`BinanceResponseError`

底层 client 只抛出异常，不发送 Hermes。

本阶段不写事件表，不记录业务质量记录，不修改正式 K线数据。

## 2. 功能：公开 Kline 原始请求封装

### 2.1 入口

`app/exchange/binance/rest_client.py::BinanceRestClient.get_klines`

### 2.2 行为

该方法只请求公开 `/fapi/v1/klines`，返回 Binance 原始 JSON list。

本阶段不做：

- Kline DTO 转换
- 已收盘 K线判断
- K线连续性校验
- K线质量检查
- 写入 `market_kline_4h`
- 回补、增量采集、每日复核
- Hermes 业务报警

### 2.3 数据影响

本功能请求外部接口：仅在显式调用 `get_klines()` 时请求 Binance public REST。

本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek。
本功能不涉及 scheduler。
本功能不涉及 `trigger_source`。
本功能不涉及 `data_source`。
本功能不执行自动交易。

## 3. 功能：人工检查脚本

### 3.1 入口文件

`scripts/check_binance_rest.py`

入口方法：

`main()`

### 3.2 触发方式与边界

用户手动执行：

```bash
python -m scripts.check_binance_rest
```

默认不请求 Binance，只做 dry-run。

真实请求必须显式传参：

```bash
python -m scripts.check_binance_rest --request-real-binance
```

本阶段未提供 scheduler job，也不应被 scheduler 配置引用。

### 3.3 脚本调用链路

```text
scripts/check_binance_rest.py::main
    ↓
scripts/check_binance_rest.py::collect_binance_rest_errors
    ↓
app/exchange/binance/rest_client.py::build_kline_params
    ↓
dry-run 结束
```

真实请求模式：

```text
scripts/check_binance_rest.py::main
    ↓
scripts/check_binance_rest.py::collect_binance_rest_errors
    ↓
app/exchange/binance/rest_client.py::BinanceRestClient.ping
app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
app/exchange/binance/rest_client.py::BinanceRestClient.get_exchange_info
app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
```

### 3.4 脚本不负责

本脚本不写 MySQL。
本脚本不写 Redis。
本脚本不发送 Hermes。
本脚本不执行 migration。
本脚本不创建业务表。
本脚本不写正式 K线表。
本脚本不自动修复数据。
本脚本不调用 DeepSeek。
本脚本不执行自动交易。

## 4. 测试

对应测试文件：

`tests/test_binance_rest_client.py`

覆盖内容：

- 配置项从 `app/core/config.py` 统一读取
- timeout 会传入 HTTP transport
- `/fapi/v1/ping` 成功和失败
- `/fapi/v1/time` 解析为 UTC datetime
- `/fapi/v1/exchangeInfo` 响应校验
- `/fapi/v1/klines` 参数构造
- invalid limit 与时间范围拒绝
- timeout、429、5xx、invalid JSON、Binance 错误 body
- 有限重试，不无限重试
- 不暴露私有接口方法
- 不使用 REST ticker price 做 10s 价格监控
- check script dry-run 不请求真实 Binance
- check script 真实请求路径可通过 fake client 测试

默认 pytest 不请求真实 Binance。
默认 pytest 不连接 MySQL。
默认 pytest 不连接 Redis。
默认 pytest 不发送 Hermes。
默认 pytest 不调用 DeepSeek。
默认 pytest 不访问任何交易接口。

## 5. 本阶段不负责的边界

本阶段不实现 06 或后续 plans。

本阶段不创建 `market_kline_4h`。
本阶段不写入任何正式 K线数据。
本阶段不实现 Kline DTO、parser、validator、repository。
本阶段不实现 K线质量检查。
本阶段不实现手动回补。
本阶段不实现增量采集。
本阶段不实现每日复核。
本阶段不实现 10s 价格监控。
本阶段不使用 WebSocket。
本阶段不实现 Hermes 业务报警流程。
本阶段不调用 DeepSeek 或任何大模型。
本阶段不实现策略、交易建议或建议生命周期。
本阶段不实现任何自动交易相关代码。
本阶段不执行 alembic。
本阶段不自动执行数据库迁移。
