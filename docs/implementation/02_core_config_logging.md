# 02 Core Config Logging 实现说明

## 1. 功能：统一配置读取

### 1.1 发起方式

本功能由 Python 代码导入调用，不提供业务 CLI。

本阶段人工检查入口为：

    python -m scripts.check_core_config_logging

### 1.2 入口文件

`app/core/config.py`

入口方法：

`load_settings()`

缓存入口：

`get_settings()`

### 1.3 核心调用链路

    scripts/check_core_config_logging.py::main
        ↓
    scripts/check_core_config_logging.py::collect_core_config_logging_errors
        ↓
    app/core/config.py::load_settings
        ↓
    app/core/config.py::load_dotenv_values

### 1.4 配置读取方式

`app/core/config.py` 只在配置模块内部读取环境变量。

读取顺序：

1. 读取项目根目录 `.env`，如果不存在则跳过。
2. 读取系统环境变量。
3. 系统环境变量覆盖 `.env` 同名配置。
4. 将基础字段转换为明确类型。
5. 返回不可变 `AppSettings` 对象。

本阶段支持配置项：

- `APP_NAME`
- `APP_ENV`
- `APP_DEBUG`
- `LOG_LEVEL`
- `TIMEZONE`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`
- `BINANCE_BASE_URL`
- `HERMES_WEBHOOK_URL`
- `HERMES_SECRET`

配置边界：

本功能不请求外部接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不涉及 `trigger_source`。
本功能不涉及 `data_source`。

## 2. 功能：统一日志初始化

### 2.1 入口文件

`app/core/logger.py`

入口方法：

`configure_logging()`

辅助入口：

`get_logger()`

### 2.2 核心调用链路

    scripts/check_core_config_logging.py::collect_core_config_logging_errors
        ↓
    app/core/logger.py::configure_logging
        ↓
    app/core/logger.py::redact_sensitive_text

### 2.3 日志流程

日志初始化流程：

1. 调用 `configure_logging(settings)`。
2. 根据 `settings.log_level` 设置日志级别。
3. 使用 UTC 时间 formatter。
4. 可启用控制台输出。
5. 可启用文件输出到 `logs/app.log`。
6. 日志目录不存在时安全创建。
7. 重复调用不会重复添加同一个 handler。
8. `SensitiveDataFilter` 对敏感文本做基础脱敏。

敏感信息脱敏原则：

- 不打印完整 `.env`。
- 不打印完整 settings。
- `AppSettings.__repr__()` 默认返回脱敏视图。
- 日志 filter 会脱敏配置中已知敏感值。
- 日志 filter 会脱敏 `password`、`secret`、`token`、`webhook`、`authorization`、`cookie` 等标记后的值。

日志边界：

本功能不请求外部接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不替代 `collector_event_log`、`data_quality_check` 或 `alert_message`。

## 3. 功能：UTC / PRC 时间工具

### 3.1 入口文件

`app/core/time_utils.py`

主要方法：

- `now_utc()`
- `now_prc()`
- `utc_naive_to_prc_naive()`
- `utc_aware_to_prc_aware()`
- `timestamp_ms_to_utc_datetime()`
- `utc_datetime_to_timestamp_ms()`
- `is_utc_datetime()`
- `format_datetime_with_timezone()`

### 3.2 核心调用链路

    scripts/check_core_config_logging.py::collect_core_config_logging_errors
        ↓
    app/core/time_utils.py::now_utc
        ↓
    app/core/time_utils.py::now_prc
        ↓
    app/core/time_utils.py::utc_naive_to_prc_naive

### 3.3 时间规则

UTC 用于：

- 后续 Binance 原始时间解释。
- 后续 K 线排序。
- 后续 K 线连续性判断。
- 后续任务时间判断。

PRC / 北京时间只用于：

- 用户阅读。
- 日志辅助展示。
- 人工排查。
- 必要展示字段。

实现边界：

- UTC 转 PRC 统一通过 `app/core/time_utils.py`。
- 代码没有在业务模块中手写 `+ timedelta(hours=8)`。
- Windows 或缺少系统时区数据库的环境下，`time_utils.py` 会在统一模块内部使用 PRC 固定时区 fallback，不要求业务代码自行处理时区偏移。
- `utc_naive_to_prc_naive()` 已保留，供后续写入展示字段时复用。
- PRC 结果不得作为业务排序或 K 线连续性判断依据。

本功能不请求外部接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。

## 4. 功能：基础异常与常量

### 4.1 入口文件

异常文件：

`app/core/exceptions.py`

常量文件：

`app/core/constants.py`

### 4.2 异常类

本阶段定义：

- `AppError`
- `ConfigError`
- `ValidationError`
- `ExternalServiceError`

本阶段未定义：

- 交易执行异常。
- 策略异常。
- DeepSeek 异常。
- K 线采集业务异常。
- MySQL / Redis 具体连接异常。

### 4.3 常量

本阶段只定义基础常量：

- 应用环境：`dev`、`test`、`prod`
- 默认应用名
- 默认日志级别
- 默认时区
- 默认交易对与周期占位
- 基础端口默认值
- 日志目录与日志文件名
- 敏感字段与敏感文本标记

本阶段未定义策略参数、止盈止损参数、仓位参数、交易信号参数或交易执行相关常量。

## 5. 脚本入口

脚本文件：

`scripts/check_core_config_logging.py`

入口方法：

`main()`

脚本只负责：

1. 加载配置模块。
2. 初始化 logger。
3. 调用 UTC / PRC 时间工具。
4. 实例化基础异常类。
5. 输出检查结果与退出码。

脚本不负责：

- 不请求 Binance。
- 不连接 MySQL。
- 不连接 Redis。
- 不发送 Hermes。
- 不写数据库。
- 不写 Redis。
- 不创建业务表。
- 不调用 DeepSeek。
- 不触发 scheduler。
- 不执行任何交易相关逻辑。

## 6. 异常处理

异常可能发生在：

- `app/core/config.py::load_dotenv_values`
- `app/core/config.py::load_settings`
- `app/core/logger.py::configure_logging`
- `app/core/time_utils.py::utc_naive_to_prc_naive`
- `app/core/time_utils.py::utc_aware_to_prc_aware`
- `scripts/check_core_config_logging.py::collect_core_config_logging_errors`

异常路径：

1. `.env` 行格式非法时，`load_dotenv_values()` 抛出 `ConfigError`。
2. `APP_DEBUG` 不能转成布尔值时，`load_settings()` 抛出 `ConfigError`。
3. `MYSQL_PORT` 或 `REDIS_PORT` 不能转成整数时，`load_settings()` 抛出 `ConfigError`。
4. `APP_ENV` 不属于 `dev/test/prod` 时，`load_settings()` 抛出 `ConfigError`。
5. 日志目录或文件无法创建时，`configure_logging()` 由 Python 运行时抛出异常。
6. 时间工具收到不符合函数要求的 naive / aware datetime 时抛出 `ValueError`。
7. 检查脚本捕获异常类型摘要并返回非 0 状态码。

本阶段不写入事件日志。
本阶段不发送 Hermes。
本阶段不重试。
本阶段不允许 `partial_success` 作为业务状态。
本阶段不修改正式数据。
本阶段不自动修复。

## 7. 对应测试

测试文件：

`tests/test_core_config_logging.py`

覆盖内容：

- settings 默认值加载。
- `APP_DEBUG` 布尔转换。
- settings repr 脱敏。
- logger 重复初始化不重复添加同一文件 handler。
- 日志文本脱敏。
- UTC / PRC 当前时间返回 aware datetime。
- `utc_naive_to_prc_naive()` 转换正确。
- aware UTC 转 PRC aware。
- 毫秒时间戳与 UTC datetime 互转。
- 基础异常类可实例化。
- 检查脚本函数不访问外部服务且返回通过。

测试类型：

- 全部是本地单元测试。
- 默认 `pytest` 不访问外部服务。
- 默认 `pytest` 不连接真实 MySQL。
- 默认 `pytest` 不连接真实 Redis。
- 默认 `pytest` 不发送真实 Hermes。
- 默认 `pytest` 不调用 DeepSeek。
- 默认 `pytest` 不访问交易接口。

本阶段没有集成测试开关。

## 8. 人工运行检查

建议按顺序运行：

    python -m scripts.check_project_skeleton
    python -m scripts.check_core_config_logging
    python -m scripts.check_project_invariants
    pytest

这些命令都只做本地检查，不请求外部接口，不连接数据库，不连接 Redis，不发送 Hermes。

## 9. 本阶段明确没有实现

- 没有实现 03 及后续 plans。
- 没有连接 MySQL。
- 没有连接 Redis。
- 没有请求 Binance。
- 没有发送 Hermes。
- 没有实现 K 线采集。
- 没有实现 K 线回补。
- 没有实现 K 线质量检查。
- 没有实现 10s 价格监控。
- 没有实现 scheduler。
- 没有实现策略分析。
- 没有实现 DeepSeek 或其他大模型调用。
- 没有实现交易建议。
- 没有实现自动交易相关任何能力。
- 没有执行 Alembic migration。
- 没有提交 `.env`、真实密钥或真实日志。

## 10. 后续模块复用

- `03_infra_mysql_redis.md`：复用配置、日志、异常、UTC / PRC 时间工具。
- `04_alerting_through_hermes.md`：复用配置、日志、异常、时间展示和敏感信息脱敏原则。
- `05_binance_rest_client.md`：复用配置、日志、异常和 UTC 时间工具。
- `06_market_kline_4h.md`：复用 UTC / PRC 时间工具和基础异常。
- `07_kline_quality_checker.md`：复用 UTC 时间判断和基础异常。
- `08_4h_backfill.md`、`09_4h_incremental_collector.md`、`11_daily_kline_integrity_check.md`：复用配置、日志、异常与时间工具。
- `10_price_monitor_10s.md`：复用配置、日志、异常与时间工具。

## 11. 边界自检

- 自动交易：未实现。
- K 线数据来源：未实现采集，因此未写入任何 K 线。
- manual_repair / human_edit / manual_input / system_repair：未作为代码能力实现。
- REST / WebSocket 边界：未实现请求。
- trigger_source / data_source：本阶段不涉及正式 K 线写入。
- scripts 边界：检查脚本只做本地核心模块检查。
- scheduler 边界：未实现 scheduler。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：未实现报警。
- MySQL / Redis 边界：未连接、未读写。
- 敏感信息提交：未提交真实密钥、真实日志或 `.env`。
