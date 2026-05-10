# 04 Alerting Through Hermes 实现说明

## 1. 功能：Hermes 固定模板报警

### 1.1 发起方式

后续业务 service 可以在异常边界处显式调用：

    app/alerting/service.py::send_alert

本阶段人工检查入口为：

    python -m scripts.check_alerting --dry-run

兼容 plan 原始命令：

    python -m scripts.check_hermes_alerting --dry-run

真实 Hermes 测试报警必须由用户手动显式触发：

    python -m scripts.check_alerting --send-real-alert

真实发送还必须同时满足：

- `HERMES_ENABLED=true`
- `HERMES_DRY_RUN=false`
- `HERMES_WEBHOOK_URL` 已配置

### 1.2 入口文件

报警 service：

`app/alerting/service.py`

入口方法：

- `send_alert()`
- `send_test_alert()`
- `format_alert_message()`

Hermes client：

`app/alerting/hermes_client.py`

入口方法：

- `HermesClient.send_alert_message()`
- `build_hermes_request()`
- `build_hermes_headers()`

固定模板：

`app/alerting/templates.py`

入口方法：

- `render_alert_message()`
- `supported_alert_type_values()`

### 1.3 核心调用链路

人工 dry-run 检查：

    scripts/check_alerting.py::main
        ↓
    scripts/check_alerting.py::collect_alerting_errors
        ↓
    app/alerting/service.py::send_test_alert
        ↓
    app/alerting/service.py::send_alert
        ↓
    app/alerting/templates.py::render_alert_message
        ↓
    app/alerting/hermes_client.py::HermesClient.send_alert_message
        ↓
    返回 skipped，不访问 Hermes

用户显式真实发送检查：

    scripts/check_alerting.py::main --send-real-alert
        ↓
    scripts/check_alerting.py::collect_alerting_errors
        ↓
    校验 HERMES_ENABLED=true、HERMES_DRY_RUN=false、HERMES_WEBHOOK_URL 非空
        ↓
    app/alerting/service.py::send_test_alert
        ↓
    app/alerting/hermes_client.py::HermesClient.send_alert_message
        ↓
    urllib.request.urlopen POST 到 HERMES_WEBHOOK_URL

后续业务 service 可选记录报警：

    future service
        ↓
    app/alerting/service.py::send_alert(repository=..., db_session=...)
        ↓
    app/storage/mysql/repositories/alert_message_repository.py::create_pending_alert_message
        ↓
    app/alerting/hermes_client.py::HermesClient.send_alert_message
        ↓
    app/storage/mysql/repositories/alert_message_repository.py::update_alert_message_result

## 2. 配置项

Hermes 配置统一由 `app/core/config.py::load_settings()` 读取。

配置项：

- `HERMES_ENABLED`
- `HERMES_DRY_RUN`
- `HERMES_WEBHOOK_URL`
- `HERMES_SECRET`
- `HERMES_TIMEOUT_SECONDS`
- `HERMES_MAX_RETRIES`

默认值：

- `HERMES_ENABLED=false`
- `HERMES_DRY_RUN=true`
- `HERMES_WEBHOOK_URL=`
- `HERMES_SECRET=`
- `HERMES_TIMEOUT_SECONDS=10`
- `HERMES_MAX_RETRIES=2`

配置边界：

本功能不在业务代码中散落 `os.getenv`。
本功能不打印完整 Hermes webhook。
本功能不打印 Hermes secret、token、Authorization 或 cookie。
本功能不请求 Binance。
本功能不读写 Redis。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及自动交易。

## 3. Hermes client 调用方式

### 3.1 请求构造

`app/alerting/hermes_client.py::build_hermes_payload()` 构造 JSON payload：

- `event_type`
- `alert_type`
- `severity`
- `title`
- `message`
- `source`
- `trace_id`
- `occurred_at_utc`
- `not_trading_advice`

`title`、`message`、`source`、`trace_id` 在进入 payload 前会先调用 `sanitize_text()`。
`HermesClient.send_alert_message()` 构造请求前会把当前配置中的 `HERMES_WEBHOOK_URL`
和 `HERMES_SECRET` 作为额外敏感值传入请求构造流程，确保发给 Hermes 的 payload
不包含 webhook、secret、`password=...`、`webhook=...` 或 `token=...` 原文。

`build_hermes_request()` 将 payload 序列化为 UTF-8 JSON bytes。

`build_hermes_headers()` 使用 `HERMES_SECRET` 对原始 JSON bytes 计算 HMAC-SHA256，并放入 `X-Webhook-Signature`。header 中不包含明文 secret。

### 3.2 真实发送边界

`HermesClient.send_alert_message()` 只有在以下全部满足时才会访问外部 Hermes：

1. 调用方传入 `send_real_alert=True`。
2. `HERMES_ENABLED=true`。
3. `HERMES_DRY_RUN=false`。
4. `HERMES_WEBHOOK_URL` 非空。
5. `HERMES_TIMEOUT_SECONDS > 0`。

不满足时返回 `AlertSendStatus.SKIPPED` 或 `AlertSendStatus.FAILED`，不会访问网络。

### 3.3 响应处理

Hermes 返回 2xx 时：

- 返回 `AlertSendStatus.SENT`
- 保存脱敏后的 `channel_response`
- 记录 `sent_at_utc`

Hermes 返回非 2xx 或网络异常时：

- 在 `HERMES_MAX_RETRIES` 范围内重试
- 最终返回 `AlertSendStatus.FAILED`
- 错误原因脱敏
- `channel_response` 脱敏

本 client 不写数据库。
本 client 不读写 Redis。
本 client 不请求 Binance。
本 client 不发送 DeepSeek 请求。
本 client 不生成交易建议。

## 4. 固定模板渲染流程

模板文件：

`app/alerting/templates.py`

支持的固定模板类型：

- `system_check`
- `infra_error`
- `data_quality_error`
- `collector_error`
- `price_monitor_error`
- `system_error`
- `mysql_error`
- `redis_error`
- `kline_data_quality_error`
- `kline_integrity_check_failed`
- `manual_test_alert`

渲染流程：

1. `AlertEvent` 校验报警类型、严重级别和 UTC aware 时间。
2. `render_alert_message()` 读取固定模板标题。
3. 使用 `app/core/time_utils.py` 格式化 UTC 时间与 PRC 展示时间。
4. 使用 `app/alerting/sanitizer.py` 脱敏 title、summary、details、source 和 trace_id。
5. 模板固定写入“不是交易建议”。
6. 涉及 K 线异常的模板固定写入“系统没有自动修复数据，没有人工改数，也没有执行自动交易”。

本阶段模板不调用 DeepSeek。
本阶段模板不根据行情生成交易建议。
本阶段模板不自动修复 K 线数据。

## 5. Alerting service 调用流程

service 文件：

`app/alerting/service.py`

`send_alert()` 负责：

1. 调用 `format_alert_message()` 渲染固定模板。
2. 如调用方显式传入 repository 和 db_session，则先创建 pending 报警记录。
3. 调用 `HermesClient.send_alert_message()`。
4. 如已有 pending 记录，则更新发送结果。
5. 返回 `AlertSendResult`。

默认行为：

- 不传 repository 和 db_session 时，不连接 MySQL。
- `send_real_alert=False` 时，不真实发送 Hermes。
- 不读写 Redis。
- 不请求 Binance。
- 不调用 DeepSeek。
- 不涉及 scheduler。
- 不涉及 `trigger_source`。
- 不涉及 `data_source`。

## 6. alert_message 入库流程

模型文件：

`app/storage/mysql/models/alert_message.py`

Repository 文件：

`app/storage/mysql/repositories/alert_message_repository.py`

Migration 文件：

`migrations/versions/20260511_04_create_alert_message.py`

表名：

`alert_message`

字段：

- `id`
- `alert_type`
- `severity`
- `title`
- `message`
- `channel`
- `status`
- `source`
- `trace_id`
- `channel_response`
- `error_message`
- `retry_count`
- `http_status_code`
- `occurred_at_utc`
- `sent_at_utc`
- `created_at_utc`
- `updated_at_utc`

写入规则：

1. `AlertMessageRepository.create_pending_alert_message()` 只创建 `pending` 记录。
2. `AlertMessageRepository.update_alert_message_result()` 只更新同一条记录的发送结果。
3. `channel_response` 入库前通过 `sanitize_mapping()` 脱敏。
4. repository 不 commit，事务边界由调用方控制。
5. repository 不直接发送 Hermes。

数据库边界：

默认测试不连接真实 MySQL。
默认检查脚本不写 MySQL。
只有后续 service 显式传入 session 并调用 repository 时，才会写入 `alert_message`。
本阶段没有执行 Alembic upgrade。
本阶段没有自动执行数据库迁移。
本 migration 只创建 `alert_message` 表。
本阶段未创建 K 线表、采集事件表、数据质量表、策略表或建议表。

## 7. 脱敏流程

脱敏文件：

`app/alerting/sanitizer.py`

脱敏对象：

- webhook
- secret
- token
- password
- Authorization
- cookie
- signature
- `channel_response`
- 错误消息中的敏感值

日志仍统一通过：

`app/core/logger.py`

Hermes client 和 service 不打印完整 webhook，不打印 secret，不打印完整请求头认证值。

## 8. 检查脚本入口

主入口：

`scripts/check_alerting.py`

兼容入口：

`scripts/check_hermes_alerting.py`

入口方法：

`main()`

检查内容：

1. 加载配置。
2. 初始化 logger，文件日志关闭。
3. 渲染必需固定模板。
4. dry-run 调用 `send_test_alert()`。
5. 如果显式真实发送，则先校验 Hermes 配置。

默认命令：

    python -m scripts.check_alerting --dry-run

兼容命令：

    python -m scripts.check_hermes_alerting --dry-run

真实发送命令：

    python -m scripts.check_alerting --send-real-alert

脚本边界：

本脚本默认不真实发送 Hermes。
本脚本不请求 Binance。
本脚本不写正式 K 线表。
本脚本不写 Redis。
本脚本不执行 migration。
本脚本不调用 DeepSeek。
本脚本不生成交易建议。
本阶段未提供 scheduler job，也不应被 scheduler 配置引用。

## 9. 哪些操作会真实发送 Hermes

只有以下操作会尝试真实发送 Hermes：

    python -m scripts.check_alerting --send-real-alert

或代码显式调用：

    HermesClient.send_alert_message(..., send_real_alert=True)

并且配置同时满足：

- `HERMES_ENABLED=true`
- `HERMES_DRY_RUN=false`
- `HERMES_WEBHOOK_URL` 非空

本次实现和测试没有真实发送 Hermes。

## 10. 哪些操作不会真实发送 Hermes

以下操作不会真实发送 Hermes：

- `python -m scripts.check_alerting --dry-run`
- `python -m scripts.check_hermes_alerting --dry-run`
- 默认 pytest
- `send_test_alert(send_real_alert=False)`
- `send_alert(send_real_alert=False)`
- `HermesClient.send_alert_message(send_real_alert=False)`
- `HERMES_ENABLED=false`
- `HERMES_DRY_RUN=true`
- `HERMES_WEBHOOK_URL` 未配置

## 11. 异常处理

可能异常点：

- `app/alerting/types.py::AlertEvent.__post_init__`
- `app/alerting/templates.py::render_alert_message`
- `app/alerting/hermes_client.py::build_hermes_request`
- `app/alerting/hermes_client.py::HermesClient.send_alert_message`
- `app/storage/mysql/repositories/alert_message_repository.py::create_pending_alert_message`
- `app/storage/mysql/repositories/alert_message_repository.py::update_alert_message_result`
- `scripts/check_alerting.py::collect_alerting_errors`

异常路径：

1. 报警类型或严重级别非法时，`AlertEvent` 抛出 `ValidationError`。
2. 模板缺失时，`render_alert_message()` 抛出 `ValidationError`。
3. payload 无法序列化时，`build_hermes_request()` 抛出 `HermesError`。
4. Hermes 配置未启用或 dry-run 时，client 返回 `skipped`，不访问外部服务。
5. Hermes 网络失败或非 2xx 时，client 返回 `failed`，错误与响应均脱敏。
6. repository 创建记录失败时，service 写脱敏日志并继续调用 Hermes client。
7. repository 更新结果失败时，service 写脱敏日志，不改写 Hermes 发送结果。
8. 检查脚本汇总错误并返回非 0 状态码。

本阶段不写 collector event log。
本阶段不发送补偿报警队列。
本阶段不重试数据库写入。
本阶段不允许自动修复数据。
本阶段不修改正式 K 线数据。

## 12. 对应测试

测试文件：

`tests/test_alerting.py`

覆盖内容：

- Hermes 配置读取和类型转换。
- 必需固定模板渲染。
- K 线异常模板说明不自动修复、不人工改数、不自动交易。
- 脱敏工具隐藏 password、secret、token、webhook、Authorization。
- Hermes disabled 时不会调用 transport。
- Hermes dry-run 时不会调用 transport。
- Hermes client 成功发送可被 mock。
- Hermes client 发出的 payload 会在网络发送前脱敏。
- Hermes client 失败返回 failed。
- HMAC header 不包含明文 secret。
- service 可使用 mock repository，不连接真实 MySQL。
- `alert_message` model 可导入。
- migration 只包含 `alert_message` 相关结构。
- dry-run 检查脚本不真实发送 Hermes。
- 配置不显式允许时，真实发送被拒绝。

测试类型：

全部是本地单元测试。
默认 pytest 不真实发送 Hermes。
默认 pytest 不请求 Binance。
默认 pytest 不连接真实 MySQL。
默认 pytest 不连接真实 Redis。
默认 pytest 不调用 DeepSeek。
默认 pytest 不访问交易接口。

真实 Hermes 检查只允许用户手动执行 `--send-real-alert`。

## 13. 本阶段明确没有实现

- 没有实现 05 或后续 plans。
- 没有请求 Binance。
- 没有实现 Binance REST client。
- 没有实现 K 线采集。
- 没有实现 K 线回补。
- 没有实现 K 线复核。
- 没有实现 10s 价格监控。
- 没有创建 `market_kline_4h` 表。
- 没有创建采集事件表。
- 没有创建数据质量检查表。
- 没有创建策略表。
- 没有创建建议表。
- 没有写入正式 K 线数据。
- 没有执行 `alembic upgrade head`。
- 没有自动执行数据库迁移。
- 没有实现 scheduler。
- 没有让 scheduler 调用检查脚本。
- 没有调用 DeepSeek 或其他大模型。
- 没有生成交易建议。
- 没有实现任何自动交易相关代码。
- 没有提交 `.env`、真实密钥或真实日志。

## 14. 后续模块复用

- `05_binance_rest_client.md` 可在基础系统异常时调用 `send_alert()`，但 Binance client 不应直接发送 Hermes。
- `06_market_kline_4h.md` 后续可复用 `alert_message` 表记录固定模板报警结果。
- `07_kline_quality_checker.md` 后续可用 `data_quality_error` 或 `kline_data_quality_error` 模板。
- `08_4h_backfill.md`、`09_4h_incremental_collector.md`、`11_daily_kline_integrity_check.md` 后续可用 `collector_error` 或 `kline_integrity_check_failed` 模板。
- `10_price_monitor_10s.md` 后续可用 `price_monitor_error` 模板，但本阶段没有实现价格监控。

## 15. 边界自检

- 自动交易：未实现。
- K 线数据来源：本阶段未采集、未写入任何 K 线。
- manual_repair / human_edit / manual_input / system_repair：未作为代码能力实现。
- REST / WebSocket 边界：未实现 Binance 请求或 WebSocket。
- trigger_source / data_source：本阶段不涉及正式 K 线写入。
- scripts 边界：检查脚本只做报警模块 dry-run 或用户显式真实发送测试。
- scheduler 边界：本阶段未提供 scheduler job，也不应被 scheduler 配置引用。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：已实现固定模板；默认不真实发送。
- MySQL / Redis 边界：默认不连接 MySQL / Redis；只有调用方显式传入 session 才能写 `alert_message`。
- 敏感信息提交：未提交真实密钥、真实日志或 `.env`。
