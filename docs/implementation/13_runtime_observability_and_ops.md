# 13 运行状态观测与运维检查入口实现说明

## 1. 功能：告警通道人工测试

### 1.1 发起方式

用户手动执行：

```bash
python -m scripts.check_alerting
python -m scripts.check_alerting --send-real-alert
```

### 1.2 入口文件

`scripts/check_alerting.py`

入口方法：

`main()`

### 1.3 职责边界

`check_alerting.py` 只测试 Hermes 固定模板和告警通道：

- 默认 dry-run，不真实发送 Hermes。
- 真实发送必须由用户显式传入 `--send-real-alert` 或 `--send-test`。
- 不检查系统运行状态。
- 不请求 Binance。
- 不采集 K线。
- 不读写 Redis。
- 不写正式 K线表。
- 不调用 DeepSeek 或其他大模型。
- 不执行自动交易。

真实发送结果为 `submitted_to_hermes` 时，脚本输出“已提交 Hermes”。
如果返回 `gateway_status=gateway_accepted`，脚本输出“Hermes 网关已接收”。
如果返回 `final_delivery_status=unknown`，脚本输出“BTC Agent 无法确认微信最终送达”。

Hermes HTTP 2xx 只代表 BTC Agent 已把消息提交给 Hermes 网关，不代表微信最终送达。
脚本不会输出“微信发送成功”或“微信已送达”。

## 2. 功能：运行状态人工检查

### 2.1 发起方式

用户手动执行：

```bash
python -m scripts.check_runtime_status
python -m scripts.check_runtime_status --send-alert
python -m scripts.check_runtime_status --lookback-hours 48
```

本脚本不允许 scheduler 调用，当前没有新增 scheduler job。

### 2.2 入口文件

`scripts/check_runtime_status.py`

入口方法：

`main()`

### 2.3 核心调用链路

默认只读检查：

```text
scripts/check_runtime_status.py::main
    ↓
app/monitoring/runtime_status.py::collect_runtime_status
    ↓
app/monitoring/runtime_status_readers.py::SystemdStatusChecker.is_active
app/storage/redis/client.py::create_redis_client
app/monitoring/runtime_status_readers.py::DefaultRuntimeMySqlReader
    ↓
app/monitoring/runtime_status_rendering.py::render_runtime_status_console
```

显式发送摘要：

```text
scripts/check_runtime_status.py::main --send-alert
    ↓
app/monitoring/runtime_status.py::collect_runtime_status
    ↓
app/monitoring/runtime_status_rendering.py::send_runtime_status_alert
    ↓
app/alerting/service.py::send_alert
    ↓
app/alerting/hermes_client.py::HermesClient.send_alert_message
```

如果 `alert_message` 表可用，`--send-alert` 会通过现有告警仓储记录发送结果；如果记录失败，脚本会降级为仅提交 Hermes 摘要，不影响只读检查报告。

### 2.4 检查内容

systemd 只读检查：

- `hermes-btc-price-monitor.service`，展示为“10 秒价格监控”。
- `hermes-btc-scheduler.service`，展示为“调度器”。
- `hermes-gateway.service`，展示为“Hermes 网关”。

Redis 只读检查：

- Redis 连接。
- `bitcoin_price` 是否存在及 TTL。
- `scheduler:running:*` 数量。
- `scheduler:completed:*` 数量。
- `scheduler:status:*` 数量。
- `scheduler:job:*` 旧 key 数量。

MySQL 只读检查：

- MySQL 连接。
- `market_kline_4h` 中 BTCUSDT 4h 最新 K线时间。
- 最近 100 根 4h K线数量。
- `collector_event_log` 中最近一次 4h 增量采集事件。
- `data_quality_check` 中最近一次每日 K线复核事件。
- `alert_message` 中最近几条告警提交记录。

K线新鲜度只根据数据库已有 K线判断，不请求 Binance。
当前未收盘 K线不要求存在，判断标准使用 UTC；北京时间只用于展示。

### 2.5 运行状态等级

总体等级从低到高：

- `normal / 正常`：核心服务、Redis、MySQL、K线新鲜度、最近采集、每日复核和告警提交均正常。
- `notice / 注意`：存在 `scheduler:job:*` 历史旧 key、回看窗口内存在旧版告警状态但最新告警已恢复等不影响当前运行的信息。
- `warning / 警告`：非核心检查未知、K线轻微滞后、回看窗口内曾有 Hermes 提交失败但最新告警已恢复、最近一次告警仍是旧版状态等。
- `error / 错误`：核心服务停止、Redis/MySQL 不可读、最新已收盘 K线明显缺失、最近采集或每日复核失败、最新一次 Hermes 提交失败或网关拒绝。
- `critical / 严重`：多个核心服务同时异常、Redis 和 MySQL 同时异常、K线严重滞后或多个核心状态同时错误。

运行状态检查只从 `alert_message.status` 判断最近 Hermes 提交状态，不依赖数据库中不存在的 `gateway_status` 或 `final_delivery_status` 字段。
`sent`、`delivered`、`weixin_success` 等旧状态只用于识别历史记录。最新记录仍是旧状态时标记为 warning；最新记录已恢复且只是历史旧记录时标记为 notice。
如果最新一次告警已经是 `submitted_to_hermes`，窗口内早些时候的 `submit_failed` / `gateway_rejected` 只表示历史波动，不再直接把总体结论升为 error。

### 2.6 输出示例

控制台示例：

```text
【Hermes BTC 运行状态检查】

总体结论：正常

服务状态：
- 10 秒价格监控：运行中
- 调度器：运行中
- Hermes 网关：运行中

数据状态：
- 最新 BTCUSDT 4h K线：2026-05-15 04:00:00 UTC / 2026-05-15 12:00 北京时间
- 最近 100 根 K线：已读取 100 根，连续性以每日 K线复核为准
- 最近一次 4h 增量采集：成功
- 最近一次每日 K线复核：健康

Redis 状态：
- bitcoin_price：存在，TTL 正常
- scheduler running key：0
- scheduler completed key：3
- scheduler status key：0
- scheduler job 旧 key：3（历史残留，等待过期）

告警状态：
- 最近一次 Hermes 提交：已提交 Hermes
- 回看窗口内历史提交失败：无
- 旧版状态记录：无
- 说明：BTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。

结论：
系统当前运行正常。

注意：
本检查只读，不修复、不回补、不写正式 K线表，也不执行自动交易。
```

`--send-alert` 微信摘要示例：

```text
【Hermes BTC 运行状态检查】

级别：信息
总体结论：正常

服务状态：
10 秒价格监控、调度器、Hermes 网关均在运行。

数据状态：
最新 BTCUSDT 4h K线为 2026-05-15 04:00:00 UTC，最近采集成功，每日 K线复核健康。

告警状态：
最近一次 Hermes 提交：已提交 Hermes；回看窗口内历史提交失败：无；旧版状态记录：无。
本摘要将通过 Hermes 通道提交。
本报告反映发送前的系统状态；本次摘要提交结果见命令行输出。BTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。

追踪ID：<trace_id>
本提醒不是交易建议，系统没有执行自动交易。
```

脚本发送后的控制台输出示例：

```text
运行状态摘要已提交 Hermes。
网关状态：Hermes 网关已接收。
最终微信送达状态：未知，BTC Agent 无法确认微信最终送达。
追踪ID：<trace_id>
```

## 3. 两个脚本的职责区别

- `check_alerting.py` = 告警通道测试。它只验证固定模板和 Hermes 提交通道，不判断系统运行状态。
- `check_runtime_status.py` = 系统状态检查。它汇总 systemd、Redis、MySQL、K线新鲜度、采集/复核记录和告警提交记录，并可选发送一条精简摘要。

两者都不是 scheduler job，均不触发 K线采集、回补、修复或交易动作。

## 4. 配置、外部接口与数据影响

读取配置：

- MySQL 连接配置。
- Redis 连接配置。
- Hermes 配置，仅 `--send-alert` 或 `--send-real-alert` 时用于真实提交。

外部接口：

- `systemctl is-active`：只读查询本机服务状态。
- Redis：只读 `ping`、`exists`、`ttl`、`scan_iter`。
- MySQL：只读 SELECT 查询；`--send-alert` 仅允许写 `alert_message` 发送记录。
- Hermes：仅用户显式发送时提交一条摘要。

本功能不请求 Binance REST，不请求 WebSocket，不调用 DeepSeek 或其他大模型。

数据库写入：

- 默认模式不写任何表。
- `--send-alert` 仅可能写入 `alert_message`，沿用现有 `app/alerting` 发送记录机制。
- 不写 `market_kline_4h`。
- 不写 `collector_event_log`。
- 不写 `data_quality_check`。

Redis 写入：

- 不写 Redis。
- 不删除 Redis key。
- 不修改 scheduler running/completed/status/旧 key。

`trigger_source` / `data_source`：

- 本功能不涉及正式 K线写入，因此不产生新的 `trigger_source` 或 `data_source`。

## 5. 异常处理

- systemd 不存在、非 systemd 环境或查询超时：服务状态标记为“未知”，总体最高可升为 warning，不让脚本崩溃。
- Redis 不可连接或读取失败：Redis 状态标记为 error。
- MySQL 不可连接或表字段不可读：MySQL 状态标记为 error，报告继续渲染。
- 最新 K线缺失或滞后：根据滞后程度标记 warning、error 或 critical。
- 最近采集或每日复核失败：标记 error。
- 最新一次 Hermes 提交失败或网关拒绝：标记 error。
- 最新一次 Hermes 已提交但回看窗口内曾失败：标记 warning，不误判为当前提交失败。
- 连续多次最近告警失败：标记 error。
- 最新一次告警仍是旧版状态：标记 warning；仅历史旧版状态且最新已恢复：标记 notice。
- `alert_message` 回看窗口内无记录：显示“暂无告警发送记录”，不因空表标记 error。
- `--send-alert` 写 `alert_message` 失败：记录日志后降级为仅提交 Hermes，不修改 K线、Redis 或 scheduler 状态。

## 6. 测试

对应测试文件：

- `tests/test_alerting.py`
- `tests/test_runtime_status.py`

覆盖内容：

- `check_alerting.py` 真实发送输出“已提交 Hermes”，并说明网关状态与最终微信送达未知。
- 默认运行状态检查不调用真实 Hermes。
- `--send-alert` 发送中文精简摘要，不输出“微信发送成功”。
- Redis 正常、旧 key notice、Redis 异常 error。
- MySQL 最新 K线、K线滞后、采集失败、每日复核失败。
- 告警新状态语义、空表、历史失败恢复后的 warning、旧状态 notice / warning。
- 摘要通知不展开 Redis key 列表、SQL 查询结果或内部字典。

测试命令：

```bash
# Windows PowerShell
.\.venv\Scripts\python.exe -m pytest tests/test_alerting.py tests/test_runtime_status.py

# Linux
python -m pytest tests/test_alerting.py tests/test_runtime_status.py
```

默认 pytest 不请求 Binance、不连接真实 Redis/MySQL、不发送真实 Hermes、不调用 DeepSeek。
测试通过 mock / fake reader 覆盖 app 层逻辑。

## 7. 本功能不负责

- 不修改 scheduler slot 状态模型。
- 不修改 K线采集逻辑。
- 不修改每日 K线复核算法。
- 不修改 Hermes gateway。
- 不新增自动修复。
- 不新增自动回补。
- 不新增人工改数能力。
- 不新增自动交易。
- 不允许 scheduler 调用 `check_runtime_status.py`。
- 不把 `check_alerting.py` 与 `check_runtime_status.py` 合并。

## 8. 数据库迁移

本阶段不新增数据库迁移。

原因：运行状态检查只读复用现有表和模型；`--send-alert` 复用已有 `alert_message` 发送记录表，不需要新表或新字段。

## 9. 危险关键词说明

代码中出现 `delivered`、`weixin_success` 等旧状态字符串，仅用于识别历史 `alert_message` 记录并标记 warning。
这些字符串不会作为用户可见成功文案输出，也不会被解释为微信最终送达。

文档和注释中出现 `DeepSeek`、`Binance` 等词，仅用于说明禁止调用边界；本功能代码不请求 Binance，不调用 DeepSeek。
