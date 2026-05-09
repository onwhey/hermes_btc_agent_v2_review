# 03 Infra MySQL Redis Plan

## 1. 阶段目标

本阶段实现项目基础设施层：

1. MySQL 连接配置。
2. SQLAlchemy engine 初始化。
3. SQLAlchemy session 管理。
4. Alembic 基础配置校验。
5. Redis 连接配置。
6. Redis client 初始化。
7. MySQL / Redis 健康检查脚本。
8. 基础设施测试。
9. 对应实现说明文件。

本阶段目标是让后续 K线表、Repository、采集事件日志、Hermes 报警记录、价格监控等模块可以复用稳定的 MySQL / Redis 基础设施。

本阶段只做基础设施，不做行情业务。

## 2. 本阶段明确不做

本阶段不得实现任何行情、报警、策略或交易业务。

禁止实现：

1. Binance REST 请求。
2. Hermes 报警。
3. 4h K线采集。
4. 4h K线回补。
5. K线一致性复核。
6. 10s 价格监控。
7. K线业务表。
8. K线 Repository。
9. collector_event_log 业务表。
10. data_quality_check 业务表。
11. alert_message 业务表。
12. 策略表。
13. 建议表。
14. DeepSeek 或其他大模型调用。
15. scheduler 定时任务。
16. 自动下单、自动平仓、自动调仓。
17. Binance 账户、订单、持仓相关接口。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/03_database_and_quality_requirements.md`
3. `docs/architecture/system_architecture.md`
4. `docs/architecture/module_boundaries.md`
5. `docs/decisions/0001-no-auto-trading.md`
6. `docs/decisions/0002-kline-source-and-time-rules.md`
7. `docs/plans/01_project_skeleton.md`
8. `docs/plans/02_core_config_logging.md`
9. `docs/implementation/01_project_skeleton.md`
10. `docs/implementation/02_core_config_logging.md`

本阶段必须复用 02 阶段的配置、日志、异常和时间工具。

不得重复实现配置读取、日志初始化和时间转换逻辑。

## 4. 建议分支

建议分支名：

`feature/03-infra-mysql-redis`

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
app/storage/mysql/
app/storage/redis/
scripts/
tests/
migrations/
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
3. 删除已有 migrations 目录后重新初始化。
4. 覆盖已有 Alembic 配置。
5. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/storage/mysql/__init__.py
app/storage/mysql/database.py
app/storage/mysql/session.py
app/storage/mysql/base.py
app/storage/mysql/health.py

app/storage/redis/__init__.py
app/storage/redis/client.py
app/storage/redis/health.py

scripts/check_infra.py
tests/test_infra_mysql_redis.py
docs/implementation/03_infra_mysql_redis.md
alembic.ini
migrations/env.py
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

## 7. MySQL 基础设施要求

MySQL 基础设施建议放在：

`app/storage/mysql/`

建议职责拆分：

1. `database.py`：负责创建 SQLAlchemy engine。
2. `session.py`：负责 sessionmaker 和 session 生命周期管理。
3. `base.py`：负责 SQLAlchemy declarative base。
4. `health.py`：负责 MySQL 健康检查。

本阶段允许：

1. 读取 MySQL 配置。
2. 创建 SQLAlchemy engine。
3. 创建 session factory。
4. 执行简单连接检查。
5. 执行 `SELECT 1` 级别健康检查。
6. 配置 Alembic 基础环境。

本阶段禁止：

1. 创建业务表。
2. 写入业务数据。
3. 删除数据库表。
4. 修改数据库表结构。
5. 创建 K线模型。
6. 创建告警模型。
7. 创建策略模型。
8. 创建建议模型。
9. 写 Repository 业务逻辑。
10. 在脚本中直接拼接业务 SQL。

## 8. MySQL 配置要求

配置必须复用：

`app/core/config.py`

需要支持的配置项：

```
MYSQL_HOST
MYSQL_PORT
MYSQL_DATABASE
MYSQL_USER
MYSQL_PASSWORD
MYSQL_CHARSET
```

如需新增连接池配置，可以加入：

```
MYSQL_POOL_SIZE
MYSQL_MAX_OVERFLOW
MYSQL_POOL_RECYCLE
MYSQL_POOL_PRE_PING
```

要求：

1. `MYSQL_PORT` 应转换为整数。
2. 密码不得打印到日志。
3. 数据库连接 URL 不得完整打印到日志。
4. 如果需要展示连接信息，只能展示 host、port、database、user，不展示 password。
5. `APP_ENV=test` 时不得误连生产数据库。

## 9. SQLAlchemy 要求

本阶段建议使用 SQLAlchemy。

允许新增依赖：

1. `SQLAlchemy`
2. `PyMySQL`
3. `alembic`

如果 02 阶段已经定义依赖管理方式，本阶段必须遵守，不得重构整个 `pyproject.toml`。

建议使用：

```
mysql+pymysql://
```

本阶段应提供统一 session 使用方式，例如：

1. `get_engine()`
2. `get_session_factory()`
3. `get_db_session()`
4. `session_scope()`

其中 `session_scope()` 应保证：

1. 正常执行时提交或交由调用方控制。
2. 异常时回滚。
3. 最终关闭 session。
4. 不吞掉异常。
5. 不把数据库密码写入异常日志。

注意：如果为了安全和清晰，第一阶段可选择让业务调用方显式 commit，本阶段只提供 session 生命周期管理。具体提交策略应在实现说明中写清楚。

## 10. Alembic 要求

本阶段可以配置 Alembic 基础环境，但不得创建业务迁移。

允许做：

1. 确保 `alembic.ini` 存在。
2. 确保 `migrations/env.py` 可以读取项目配置。
3. 确保 Alembic 能加载 SQLAlchemy metadata。
4. 确保 Alembic 使用项目统一配置生成数据库连接。
5. 保证 Alembic 配置不会硬编码真实密码。

禁止做：

1. 创建 K线表迁移。
2. 创建告警表迁移。
3. 创建采集事件表迁移。
4. 创建策略表迁移。
5. 创建建议表迁移。
6. 自动执行 `alembic upgrade head`。
7. 在没有用户确认的情况下连接生产库执行迁移。

本阶段的 Alembic 目标是“配置可用”，不是“创建业务表”。

业务表迁移应在后续对应 plan 中实现。

## 11. Redis 基础设施要求

Redis 基础设施建议放在：

`app/storage/redis/`

建议职责拆分：

1. `client.py`：负责 Redis client 初始化。
2. `health.py`：负责 Redis 健康检查。

本阶段允许：

1. 读取 Redis 配置。
2. 创建 Redis client。
3. 执行 ping 健康检查。
4. 提供统一 client 获取方法。

本阶段禁止：

1. 写入业务 key。
2. 读取业务 key。
3. 创建 `bitcoin_price`。
4. 实现 10s 价格监控。
5. 实现分布式锁业务逻辑。
6. 实现缓存行情数据。
7. 把 Redis 当成长期行情数据库。

Redis 在本项目中的定位：

1. 短期状态。
2. 临时缓存。
3. 后续 10s 价格监控中的最近价格状态。
4. 不承担长期历史行情事实存储。

长期行情数据必须放 MySQL。

## 12. Redis 配置要求

配置必须复用：

`app/core/config.py`

需要支持的配置项：

```
REDIS_HOST
REDIS_PORT
REDIS_PASSWORD
REDIS_DB
```

如需新增连接配置，可以加入：

```
REDIS_SOCKET_TIMEOUT
REDIS_DECODE_RESPONSES
```

要求：

1. `REDIS_PORT` 应转换为整数。
2. `REDIS_DB` 应转换为整数。
3. 密码不得打印到日志。
4. Redis URL 不得完整打印到日志。
5. Redis 不可用时，健康检查应明确失败，不得假装成功。

## 13. `.env.example` 更新要求

如果 `.env.example` 已存在，只允许补齐缺失项，不得清空重写。

建议包含：

```
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=hermes_btc_agent
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_CHARSET=utf8mb4
MYSQL_POOL_SIZE=5
MYSQL_MAX_OVERFLOW=10
MYSQL_POOL_RECYCLE=3600
MYSQL_POOL_PRE_PING=true

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
REDIS_SOCKET_TIMEOUT=5
REDIS_DECODE_RESPONSES=true
```

禁止：

1. 写入真实生产数据库密码。
2. 写入真实 Redis 密码。
3. 写入真实公网数据库地址。
4. 写入真实生产连接串。
5. 覆盖已有有效配置。

## 14. 日志要求

本阶段必须复用：

`app/core/logger.py`

MySQL / Redis 基础设施日志要求：

1. 可以记录连接检查开始。
2. 可以记录连接检查成功。
3. 可以记录连接检查失败。
4. 不得记录密码。
5. 不得记录完整连接串。
6. 不得记录 Redis password。
7. 不得记录 `.env` 完整内容。

日志示例可以包含：

1. MySQL host。
2. MySQL port。
3. MySQL database。
4. Redis host。
5. Redis port。
6. Redis db。

但必须排除：

1. password。
2. token。
3. secret。
4. Authorization。
5. cookie。

## 15. 健康检查脚本要求

建议创建或更新：

`scripts/check_infra.py`

该脚本允许检查：

1. 配置是否能加载。
2. MySQL 是否能连接。
3. MySQL `SELECT 1` 是否成功。
4. Redis 是否能连接。
5. Redis ping 是否成功。
6. 日志是否能正常输出。
7. 当前 UTC / PRC 时间是否能输出。

该脚本禁止：

1. 请求 Binance。
2. 发送 Hermes。
3. 写入 K线表。
4. 写入任何业务表。
5. 写入 Redis 业务 key。
6. 创建 `bitcoin_price`。
7. 执行 Alembic migration。
8. 调用 DeepSeek。
9. 启动 scheduler。
10. 执行任何交易相关逻辑。

示例运行方式：

```
python -m scripts.check_infra
```

说明：

1. `scripts/check_infra.py` 是人工 CLI 检查入口。
2. 本阶段不得让 scheduler 调用该脚本。
3. 该脚本不得承载业务逻辑。
4. 该脚本不得直接拼接复杂业务 SQL。
5. 该脚本只做基础设施健康检查。

## 16. 测试要求

建议创建：

`tests/test_infra_mysql_redis.py`

测试分为两类：

### 16.1 单元测试

单元测试不得依赖真实 MySQL / Redis。

至少覆盖：

1. MySQL 配置可以从 settings 读取。
2. MySQL 端口可以转换为整数。
3. Redis 配置可以从 settings 读取。
4. Redis 端口可以转换为整数。
5. Redis DB 可以转换为整数。
6. 数据库连接 URL 脱敏函数不显示密码。
7. Redis 连接信息脱敏函数不显示密码。
8. session factory 可构造。
9. Redis client 构造函数可被 mock。
10. 健康检查失败时返回明确错误或抛出可识别异常。

### 16.2 集成检查

真实 MySQL / Redis 连接检查可以通过人工脚本完成：

```
python -m scripts.check_infra
```

测试套件不得强制依赖开发者本地一定安装 MySQL / Redis。

如果需要集成测试，应使用环境变量显式开启，例如：

```
RUN_INFRA_INTEGRATION_TESTS=true
```

默认情况下，`pytest` 不应因为没有本地 MySQL / Redis 而失败。

## 17. 异常处理要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增基础设施异常，例如：

1. `DatabaseError`
2. `RedisError`
3. `InfrastructureError`

异常处理要求：

1. 连接失败必须明确报错。
2. 不得吞掉异常后返回成功。
3. 不得把密码写入异常消息。
4. 不得把完整连接串写入异常消息。
5. 健康检查失败应返回可读原因。
6. 脚本退出时应返回非 0 状态码。

本阶段不得新增交易相关异常。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。

## 18. 数据库影响

本阶段允许：

1. 建立 MySQL 连接。
2. 执行 `SELECT 1`。
3. 配置 Alembic 基础环境。
4. 读取数据库当前连接状态。

本阶段禁止：

1. 创建业务表。
2. 修改业务表。
3. 删除业务表。
4. 写入业务数据。
5. 写入 K线数据。
6. 写入告警数据。
7. 写入策略数据。
8. 自动执行迁移。

如果 MySQL 不可用，检查脚本应失败并输出明确错误，不得假装成功。

## 19. Redis 影响

本阶段允许：

1. 建立 Redis 连接。
2. 执行 ping。
3. 读取 Redis 连接状态。

本阶段禁止：

1. 写入 Redis 业务 key。
2. 写入 `bitcoin_price`。
3. 写入价格数据。
4. 写入 K线数据。
5. 写入报警状态。
6. 实现价格监控缓存。
7. 将 Redis 作为长期存储。

如果 Redis 不可用，检查脚本应失败并输出明确错误，不得假装成功。

## 20. Binance 影响

本阶段不得请求 Binance。

本阶段不得创建 Binance REST Client。

本阶段不得引入 Binance SDK。

本阶段不得访问：

1. `/fapi/v1/time`
2. `/fapi/v1/klines`
3. `/fapi/v1/ticker/price`
4. 任何 order/account/position 接口。

Binance 能力应在 `05_binance_rest_client.md` 中实现。

## 21. Hermes 影响

本阶段不得调用 Hermes。

本阶段不得创建 Hermes client。

本阶段不得发送任何报警。

本阶段不得保存 `channel_response`。

Hermes 能力应在 `04_alerting_through_hermes.md` 中实现。

## 22. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得实现定时任务。

本阶段不得让 scheduler 调用任何 scripts。

scheduler 与 `trigger_source` 的实际运行逻辑应在后续采集相关 plan 中实现。

## 23. 交易安全边界

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

## 24. 安全要求

本阶段必须防止基础设施信息泄露。

禁止：

1. 打印数据库密码。
2. 打印 Redis 密码。
3. 打印完整 MySQL URL。
4. 打印完整 Redis URL。
5. 打印完整 `.env`。
6. 把密码写入测试断言输出。
7. 把密码写入实现说明文件。
8. 把真实连接信息提交到仓库。

如需展示连接配置，只能展示脱敏后的信息。

示例：

```
mysql://user:***@127.0.0.1:3306/hermes_btc_agent
```

## 25. 交付物要求

本阶段完成后，Codex 必须交付：

1. MySQL 基础连接模块。
2. MySQL session 管理模块。
3. SQLAlchemy declarative base。
4. MySQL 健康检查模块。
5. Redis client 模块。
6. Redis 健康检查模块。
7. Alembic 基础配置。
8. `.env.example` 必要补充。
9. `pyproject.toml` 必要依赖补充。
10. 基础设施检查脚本。
11. 基础设施测试文件。
12. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/03_infra_mysql_redis.md`

说明文件必须描述：

1. 本模块入口。
2. MySQL 初始化方式。
3. session 管理方式。
4. Redis 初始化方式。
5. 健康检查流程。
6. Alembic 基础配置说明。
7. 敏感信息脱敏原则。
8. 本模块不负责的边界。
9. 后续哪些模块会复用本模块。

本阶段说明文件不需要描述：

1. K线入库流程。
2. K线校验流程。
3. Binance 请求流程。
4. Hermes 告警流程。
5. 采集流程。
6. 回补流程。
7. 复核流程。

原因：这些能力本阶段不实现。

## 26. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_infra` 可以在配置正确时运行成功。
2. MySQL 不可用时，检查脚本明确失败，不假装成功。
3. Redis 不可用时，检查脚本明确失败，不假装成功。
4. `pytest` 默认可以运行成功。
5. 默认测试不依赖真实 MySQL / Redis。
6. `app.storage.mysql.database` 可以被正常导入。
7. `app.storage.mysql.session` 可以被正常导入。
8. `app.storage.mysql.base` 可以被正常导入。
9. `app.storage.redis.client` 可以被正常导入。
10. 数据库连接信息不会打印密码。
11. Redis 连接信息不会打印密码。
12. `.env.example` 不包含真实密钥。
13. 未创建任何业务表。
14. 未写入任何业务数据。
15. 未创建 `bitcoin_price`。
16. 未请求 Binance。
17. 未调用 Hermes。
18. 未实现 scheduler。
19. 未实现交易执行相关代码。
20. `docs/implementation/03_infra_mysql_redis.md` 已创建或补齐。

## 27. 人工审查清单

合并前用户应人工检查：

1. 查看 `app/storage/mysql/` 是否只包含基础设施模块。
2. 查看 `app/storage/redis/` 是否只包含基础设施模块。
3. 查看 `scripts/check_infra.py` 是否只做基础设施检查。
4. 查看 `.env.example` 是否没有真实密钥。
5. 查看 `pyproject.toml` 是否只做必要依赖补充。
6. 查看 Alembic 配置是否没有硬编码真实密码。
7. 搜索是否存在 K线业务表。
8. 搜索是否存在 Binance 请求。
9. 搜索是否存在 Hermes 请求。
10. 搜索是否存在 Redis 业务 key。
11. 搜索是否存在交易执行相关关键词。
12. 运行测试。
13. 在本地配置 MySQL / Redis 后运行检查脚本。

建议搜索：

```
grep -R "market_kline" app scripts tests migrations
grep -R "collector_event_log" app scripts tests migrations
grep -R "data_quality_check" app scripts tests migrations
grep -R "bitcoin_price" app scripts tests
grep -R "fapi" app scripts tests
grep -R "Binance" app scripts tests
grep -R "Hermes" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果搜索结果只是文档、注释或允许的配置名，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 28. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 创建 K线业务表。
2. 创建采集事件表。
3. 创建数据质量检查表。
4. 创建告警业务表。
5. 创建策略表。
6. 创建建议表。
7. 写入 MySQL 业务数据。
8. 写入 Redis 业务 key。
9. 创建 `bitcoin_price`。
10. 请求 Binance。
11. 调用 Hermes。
12. 实现 scheduler。
13. 实现 K线采集。
14. 实现 K线回补。
15. 实现 K线复核。
16. 实现 10s 价格监控。
17. 实现策略模块。
18. 实现 DeepSeek 调用。
19. 实现交易建议。
20. 实现任何交易执行代码。
21. 提交真实密钥。
22. 提交真实日志。
23. 提交 `.env`。
24. 删除、清空或覆盖已有文档。
25. 把业务逻辑写进 `scripts`。

## 29. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 运行基础设施检查：

   python -m scripts.check_infra

4. 人工确认没有异常删除、覆盖或越界实现。

5. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 MySQL 和 Redis 基础设施"

6. 用户自行推送分支，并进入代码审查流程。
