# 03 Infra MySQL Redis 实现说明

## 1. 功能：MySQL engine 与 session 管理

### 1.1 发起方式

本功能由代码显式调用，不在模块 import 阶段自动连接 MySQL。

后续 repository 或 service 可调用：

    app/storage/mysql/database.py::get_engine
    app/storage/mysql/session.py::session_scope

人工基础设施检查入口：

    python -m scripts.check_infra

### 1.2 入口文件

`app/storage/mysql/database.py`

入口方法：

- `create_mysql_engine()`
- `get_engine()`
- `build_mysql_connection_url()`
- `render_redacted_mysql_connection_info()`

`app/storage/mysql/session.py`

入口方法：

- `create_session_factory()`
- `get_session_factory()`
- `get_db_session()`
- `session_scope()`

### 1.3 核心调用链路

    scripts/check_infra.py::main
        ↓
    scripts/check_infra.py::collect_infra_errors
        ↓
    app/storage/mysql/health.py::check_mysql_health
        ↓
    app/storage/mysql/database.py::create_mysql_engine
        ↓
    SQLAlchemy engine.connect
        ↓
    SELECT 1

后续业务模块的 session 生命周期建议链路：

    future service
        ↓
    app/storage/mysql/session.py::session_scope
        ↓
    app/storage/mysql/session.py::get_db_session
        ↓
    app/storage/mysql/session.py::get_session_factory
        ↓
    app/storage/mysql/database.py::get_engine

### 1.4 读取配置

MySQL 配置统一来自 `app/core/config.py::load_settings()`：

- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_CHARSET`
- `MYSQL_POOL_SIZE`
- `MYSQL_MAX_OVERFLOW`
- `MYSQL_POOL_RECYCLE`
- `MYSQL_POOL_PRE_PING`

`MYSQL_PORT`、连接池配置会转换为明确类型。`APP_ENV=test` 时，MySQL 显式连接检查只允许本机目标，避免测试环境误连远端数据库。

### 1.5 外部接口、数据库、Redis、Hermes

`create_mysql_engine()` 只创建 engine，不主动建立网络连接。

`check_mysql_health()` 会在被显式调用时连接 MySQL，并只执行：

    SELECT 1

本功能不请求外部 HTTP 接口。
本功能不创建业务表。
本功能不写入数据库。
本功能不读取业务数据库表。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不涉及 `trigger_source`。
本功能不涉及 `data_source`。

### 1.6 session 生命周期

`session_scope(commit_on_success=False)` 默认不自动提交，提交权交给调用方或后续 service 控制。

如果调用方传入 `commit_on_success=True`：

1. 正常退出时调用 `commit()`。
2. 上下文内发生异常时调用 `rollback()`。
3. 最终始终调用 `close()`。
4. 原异常继续向上抛出，不吞掉异常。

本阶段没有 repository，也没有业务写入，因此没有定义业务事务边界。

### 1.7 异常处理

异常类：

- `app/core/exceptions.py::InfrastructureError`
- `app/core/exceptions.py::DatabaseError`

异常路径：

1. `app/storage/mysql/database.py::build_mysql_connection_url()` 在配置缺失或测试环境目标不安全时抛出 `DatabaseError`。
2. `app/storage/mysql/database.py::create_mysql_engine()` 在依赖缺失或 engine 创建失败时抛出 `DatabaseError`。
3. `app/storage/mysql/health.py::check_mysql_health()` 捕获连接或 `SELECT 1` 异常，返回 `ok=False` 的脱敏结果。
4. `scripts/check_infra.py::collect_infra_errors()` 根据 health result 汇总错误。
5. `scripts/check_infra.py::main()` 有错误时返回 1。

本阶段不写入事件日志。
本阶段不发送 Hermes。
本阶段不重试。
本阶段不允许 `partial_success` 作为业务状态。
本阶段不修改正式数据。
本阶段不自动修复。

## 2. 功能：Redis client 与健康检查

### 2.1 发起方式

本功能由代码显式调用，不在模块 import 阶段自动连接 Redis。

后续短期状态模块可调用：

    app/storage/redis/client.py::get_client

人工基础设施检查入口：

    python -m scripts.check_infra

### 2.2 入口文件

`app/storage/redis/client.py`

入口方法：

- `create_redis_client()`
- `get_client()`
- `close_client()`
- `render_redacted_redis_connection_info()`

`app/storage/redis/health.py`

入口方法：

- `check_redis_health()`

### 2.3 核心调用链路

    scripts/check_infra.py::main
        ↓
    scripts/check_infra.py::collect_infra_errors
        ↓
    app/storage/redis/health.py::check_redis_health
        ↓
    app/storage/redis/client.py::create_redis_client
        ↓
    redis.Redis.ping

### 2.4 读取配置

Redis 配置统一来自 `app/core/config.py::load_settings()`：

- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`
- `REDIS_DB`
- `REDIS_SOCKET_TIMEOUT`
- `REDIS_DECODE_RESPONSES`

`REDIS_PORT` 和 `REDIS_DB` 会转换为整数，`REDIS_SOCKET_TIMEOUT` 会转换为数字，`REDIS_DECODE_RESPONSES` 会转换为布尔值。

### 2.5 外部接口、数据库、Redis、Hermes

`create_redis_client()` 只创建 client 对象，不主动 ping。

`check_redis_health()` 会在被显式调用时连接 Redis，并只执行 ping。

本功能不请求外部 HTTP 接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis 业务 key。
本功能不写入 Redis 业务 key。
本功能不创建 `bitcoin_price`。
本功能不发送 Hermes。
本功能不调用 DeepSeek 或其他大模型。
本功能不涉及 scheduler。
本功能不涉及 `trigger_source`。
本功能不涉及 `data_source`。

### 2.6 异常处理

异常类：

- `app/core/exceptions.py::InfrastructureError`
- `app/core/exceptions.py::RedisError`

异常路径：

1. `app/storage/redis/client.py::create_redis_client()` 在配置缺失、依赖缺失或 client 构造失败时抛出 `RedisError`。
2. `app/storage/redis/health.py::check_redis_health()` 捕获 ping 异常，返回 `ok=False` 的脱敏结果。
3. `scripts/check_infra.py::collect_infra_errors()` 根据 health result 汇总错误。
4. `scripts/check_infra.py::main()` 有错误时返回 1。

本阶段不写 Redis 失败状态 key。
本阶段不发送 Hermes。
本阶段不重试。
本阶段不自动修复。
本阶段不修改正式数据。

## 3. 功能：Alembic 基础配置

### 3.1 入口文件

`alembic.ini`

`migrations/env.py`

入口方法：

- `migrations/env.py::run_migrations_offline()`
- `migrations/env.py::run_migrations_online()`

### 3.2 调用链路

用户手动执行 Alembic 命令时：

    alembic command
        ↓
    migrations/env.py
        ↓
    app/core/config.py::get_settings
        ↓
    app/storage/mysql/database.py::build_mysql_connection_url
        ↓
    app/storage/mysql/base.py::Base.metadata

### 3.3 边界

本阶段只保证 Alembic 可以读取统一配置和 metadata。

本阶段没有创建 migration 文件。
本阶段没有创建业务表。
本阶段没有执行 `alembic upgrade head`。
本阶段没有自动连接生产 MySQL。
只有用户手动执行 Alembic online 命令时，才可能连接 MySQL。

## 4. 功能：基础设施检查脚本

### 4.1 发起方式

用户手动执行：

    python -m scripts.check_infra

可选本地配置级检查：

    python -m scripts.check_infra --skip-mysql --skip-redis

### 4.2 入口文件

`scripts/check_infra.py`

入口方法：

- `main()`
- `collect_infra_errors()`

### 4.3 脚本职责

脚本只负责：

1. 加载配置。
2. 初始化 logger。
3. 输出当前 UTC / PRC 时间。
4. 调用 MySQL health check。
5. 调用 Redis health check。
6. 汇总错误并返回退出码。

脚本不负责：

- 不请求 Binance。
- 不发送 Hermes。
- 不写 K 线表。
- 不写任何业务表。
- 不写 Redis 业务 key。
- 不执行 Alembic migration。
- 不调用 DeepSeek。
- 不启动 scheduler。
- 不执行任何交易相关逻辑。

## 5. 敏感信息脱敏原则

日志统一使用 `app/core/logger.py`。

MySQL 日志只展示脱敏摘要：

    mysql+pymysql://user:***REDACTED***@host:port/database?charset=utf8mb4

Redis 日志只展示脱敏摘要：

    redis://:***REDACTED***@host:port/db

本阶段不得打印：

- `.env` 完整内容
- MySQL password
- Redis password
- 完整 MySQL URL
- 完整 Redis URL
- webhook
- secret
- token
- Authorization
- cookie

## 6. 对应测试

测试文件：

`tests/test_infra_mysql_redis.py`

覆盖内容：

- MySQL / Redis 配置读取和类型转换。
- MySQL 连接摘要脱敏。
- Redis 连接摘要脱敏。
- MySQL URL 配置缺失和测试环境远端目标保护。
- SQLAlchemy engine 创建可以被 mock，且不会真实连接。
- session factory 和 `session_scope()` 生命周期。
- Redis client 构造可以被 mock，且不会真实 ping。
- Redis 测试环境远端目标保护。
- MySQL / Redis health failure 返回明确且脱敏的失败结果。
- `scripts/check_infra.py::collect_infra_errors(check_mysql=False, check_redis=False)` 不访问真实基础设施。

测试类型：

- 全部是本地单元测试。
- 默认 `pytest` 不连接真实 MySQL。
- 默认 `pytest` 不连接真实 Redis。
- 默认 `pytest` 不请求 Binance。
- 默认 `pytest` 不发送 Hermes。
- 默认 `pytest` 不调用 DeepSeek。
- 默认 `pytest` 不访问交易接口。

真实 MySQL / Redis 检查只通过用户手动运行：

    python -m scripts.check_infra

## 7. 本阶段明确没有实现

- 没有实现 04 或后续 plans。
- 没有实现 Hermes 报警发送。
- 没有请求 Binance。
- 没有实现 K 线采集。
- 没有创建 `market_kline_4h` 表。
- 没有创建 collector event、data quality 或 alert message 业务表。
- 没有创建策略表。
- 没有创建建议表。
- 没有写入任何业务数据。
- 没有执行 Alembic upgrade。
- 没有自动执行数据库迁移。
- 没有自动连接生产 MySQL。
- 没有自动连接生产 Redis。
- 没有实现价格监控。
- 没有实现 DeepSeek。
- 没有实现交易建议。
- 没有实现任何自动交易相关代码。
- 没有提交 `.env`、密钥或真实日志。

## 8. 后续模块复用

- `04_alerting_through_hermes.md` 后续可复用 Redis / MySQL 基础设施，但 Hermes 发送不在本阶段。
- `06_market_kline_4h.md` 后续会基于 `Base.metadata` 创建业务模型和 migration。
- `07_kline_quality_checker.md` 后续会使用 MySQL repository 记录质量结果。
- `08_4h_backfill.md`、`09_4h_incremental_collector.md`、`11_daily_kline_integrity_check.md` 后续会复用 session 管理。
- `10_price_monitor_10s.md` 后续会复用 Redis client 写短期价格状态。

## 9. 边界自检

- 自动交易：未实现。
- K 线数据来源：本阶段未采集、未写入任何 K 线。
- manual_repair / human_edit / manual_input / system_repair：未作为代码能力实现。
- REST / WebSocket 边界：未实现 Binance 请求或 WebSocket。
- trigger_source / data_source：本阶段不涉及正式 K 线写入。
- scripts 边界：`check_infra` 只做基础设施检查。
- scheduler 边界：本阶段未提供 scheduler job，也不应被 scheduler 配置引用。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：未实现报警发送。
- MySQL / Redis 边界：只有显式 health check 才会连接；测试默认不连接真实服务。
- 敏感信息提交：未提交真实密钥、真实日志或 `.env`。
