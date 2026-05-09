# 02 Core Config Logging Plan

## 1. 阶段目标

本阶段实现项目基础核心能力：

1. 统一配置读取。
2. 统一日志初始化。
3. 统一 UTC / PRC 时间工具。
4. 基础异常类。
5. 基础常量。
6. 核心模块检查脚本。
7. 对应测试文件。
8. 对应实现说明文件。

本阶段目标是让后续 MySQL、Redis、Binance REST、Hermes、K线采集等模块可以复用同一套配置、日志、时间工具和异常边界。

## 2. 本阶段明确不做

本阶段不得实现任何业务功能。

禁止实现：

1. MySQL 连接。
2. MySQL 读写。
3. Redis 连接。
4. Redis 读写。
5. Binance REST 请求。
6. Hermes 报警。
7. scheduler 定时任务。
8. K线采集。
9. K线回补。
10. K线一致性复核。
11. 10s 价格监控。
12. DeepSeek 或其他大模型调用。
13. 策略分析。
14. 交易建议。
15. 自动下单、自动平仓、自动调仓。
16. 账户、订单、持仓相关接口。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/03_database_and_quality_requirements.md`
3. `docs/architecture/module_boundaries.md`
4. `docs/architecture/system_architecture.md`
5. `docs/decisions/0001-no-auto-trading.md`
6. `docs/decisions/0002-kline-source-and-time-rules.md`
7. `docs/plans/01_project_skeleton.md`
8. `docs/implementation/01_project_skeleton.md`

本阶段必须基于 01 阶段已有目录和文件进行最小增量修改，不得删除、清空或重建已有文件。

## 4. 建议分支

建议分支名：

`feature/02-core-config-logging`

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
app/core/
scripts/
tests/
docs/implementation/
logs/
```

目录处理原则：

1. 如果目录已经存在，只检查并保留，不得删除后重建。
2. 不得覆盖、清空、移动已有 `docs/` 内容。
3. 不得删除已有 `requirements/`、`architecture/`、`decisions/`、`plans/` 文件。
4. 只允许补齐当前缺失的目录或占位文件。
5. `logs/` 目录可以存在，但不得提交真实日志文件。
6. `.gitkeep` 只在目录为空且需要 Git 跟踪时创建，不得覆盖已有文件。

禁止执行类似以下危险操作：

1. 删除整个 `docs/` 后重建。
2. 清空已有文档目录。
3. 覆盖已有 requirements / architecture / decisions / plans。
4. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/core/__init__.py
app/core/config.py
app/core/logger.py
app/core/time_utils.py
app/core/exceptions.py
app/core/constants.py
scripts/check_core_config_logging.py
tests/test_core_config_logging.py
docs/implementation/02_core_config_logging.md
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

## 7. 配置模块要求

配置模块建议放在：

`app/core/config.py`

本模块负责：

1. 从 `.env` 和系统环境变量读取配置。
2. 提供统一 settings 对象。
3. 支持 APP_DEBUG。
4. 支持不同运行环境。
5. 对基础配置做类型转换。
6. 不在日志中打印敏感配置值。

建议配置类至少支持：

```
APP_NAME
APP_ENV
APP_DEBUG
LOG_LEVEL
TIMEZONE
```

可预留但不得主动连接：

```
MYSQL_HOST
MYSQL_PORT
MYSQL_DATABASE
MYSQL_USER
MYSQL_PASSWORD

REDIS_HOST
REDIS_PORT
REDIS_PASSWORD

BINANCE_BASE_URL

HERMES_WEBHOOK_URL
HERMES_SECRET
```

注意：

1. 本阶段可以读取 MySQL / Redis / Binance / Hermes 的环境变量。
2. 本阶段不得使用这些配置去建立连接。
3. 本阶段不得请求外部服务。
4. 本阶段不得验证真实服务是否可用。
5. `.env` 不得提交到仓库。
6. `.env.example` 不得包含真实密钥。

## 8. `.env.example` 更新要求

如果 `.env.example` 已存在，只允许补齐缺失项，不得清空重写。

建议包含：

```
APP_NAME=hermes_btc_agent
APP_ENV=dev
APP_DEBUG=false
LOG_LEVEL=INFO
TIMEZONE=UTC

MYSQL_HOST=
MYSQL_PORT=3306
MYSQL_DATABASE=
MYSQL_USER=
MYSQL_PASSWORD=

REDIS_HOST=
REDIS_PORT=6379
REDIS_PASSWORD=

BINANCE_BASE_URL=https://fapi.binance.com

HERMES_WEBHOOK_URL=
HERMES_SECRET=
```

禁止：

1. 写入真实 `.env` 内容。
2. 写入真实数据库密码。
3. 写入真实 Hermes webhook。
4. 写入真实 token。
5. 写入真实 secret。
6. 写入真实 cookie。
7. 写入完整生产连接串。

## 9. `pyproject.toml` 更新要求

如需新增依赖，必须最小化。

允许考虑的依赖：

1. `python-dotenv`
2. `pydantic`
3. `pydantic-settings`
4. `pytest`

如果 01 阶段已经定义依赖管理方式，本阶段必须遵循 01 阶段结构，不得擅自重构整个 `pyproject.toml`。

禁止：

1. 引入 Web 框架。
2. 引入 Binance SDK。
3. 引入交易 SDK。
4. 引入大模型 SDK。
5. 引入数据库 ORM。
6. 引入 Redis 客户端。
7. 引入调度器。
8. 引入过多无关依赖。

SQLAlchemy、Alembic、Redis 客户端应在后续基础设施阶段处理，不属于本阶段。

## 10. 日志模块要求

日志模块建议放在：

`app/core/logger.py`

本模块负责：

1. 初始化项目日志器。
2. 根据 `LOG_LEVEL` 控制日志等级。
3. 支持控制台输出。
4. 支持文件输出到 `logs/` 目录。
5. 确保日志目录不存在时可安全创建。
6. 避免重复添加 handler。
7. 避免输出敏感信息。
8. 提供统一的 `get_logger()` 方法或等价方法。

日志格式至少包含：

1. 时间。
2. 日志级别。
3. logger 名称。
4. 消息内容。

建议日志时间使用 UTC。

禁止：

1. 在日志中打印完整 `.env`。
2. 在日志中打印数据库密码。
3. 在日志中打印 Hermes webhook 完整 URL。
4. 在日志中打印 token。
5. 在日志中打印 secret。
6. 在日志中打印 Authorization。
7. 在日志中打印 cookie。
8. 在日志初始化时连接外部服务。

## 11. 时间工具要求

时间工具建议放在：

`app/core/time_utils.py`

本项目业务判断统一使用 UTC。

PRC 时间只用于：

1. 用户阅读。
2. 日志辅助展示。
3. 人工排查。
4. 必要的展示字段。

禁止在业务代码里到处手写 `+ timedelta(hours=8)`。

必须提供统一辅助函数。

建议至少包含：

1. 获取当前 UTC 时间。
2. 获取当前 PRC 时间。
3. UTC naive datetime 转 PRC naive datetime。
4. UTC aware datetime 转 PRC aware datetime。
5. 毫秒时间戳转 UTC datetime。
6. UTC datetime 转毫秒时间戳。
7. 判断时间是否为 UTC。
8. 必要的格式化函数。

必须保留这个函数或等价函数：

`utc_naive_to_prc_naive()`

后续写入或解析 Binance 时间字段时，必须优先调用 `app/core/time_utils.py` 中的 UTC 转 PRC 辅助函数，不得在业务代码里重复手写 +8 小时。

时间规则：

1. Binance 返回时间按 UTC 处理。
2. K线排序以 `open_time_ms` 或 UTC 时间为准。
3. 策略判断以后也以 UTC 为准。
4. PRC 时间不得作为业务排序依据。
5. PRC 时间不得作为 K线连续性判断依据。

## 12. 常量模块要求

常量模块建议放在：

`app/core/constants.py`

本阶段可定义基础常量，例如：

```
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "4h"
APP_ENV_DEV = "dev"
APP_ENV_TEST = "test"
APP_ENV_PROD = "prod"
```

本阶段不得定义复杂业务常量。

禁止在本阶段定义：

1. 策略参数。
2. 止盈止损参数。
3. 杠杆参数。
4. 仓位参数。
5. 交易信号参数。
6. Binance 下单相关常量。
7. WebSocket 订阅常量。

## 13. 异常模块要求

异常模块建议放在：

`app/core/exceptions.py`

本阶段可以定义基础异常类：

1. `AppError`
2. `ConfigError`
3. `ValidationError`
4. `ExternalServiceError`

本阶段不得定义过多业务异常。

禁止提前定义：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。
6. DeepSeekError。

后续模块需要时再新增具体异常。

## 14. 基础检查脚本要求

建议创建：

`scripts/check_core_config_logging.py`

该脚本只允许检查：

1. 配置模块能否加载。
2. `APP_DEBUG` 是否能被读取。
3. `APP_ENV` 是否能被读取。
4. logger 是否能正常初始化。
5. 时间工具是否能正常返回 UTC 和 PRC 时间。
6. `utc_naive_to_prc_naive()` 是否可调用。
7. 基础异常类是否可导入。

禁止该脚本：

1. 请求 Binance。
2. 连接 MySQL。
3. 连接 Redis。
4. 发送 Hermes。
5. 写入数据库。
6. 写入 Redis。
7. 创建业务表。
8. 调用 DeepSeek。
9. 触发 scheduler。
10. 执行任何交易相关逻辑。

示例运行方式：

```
python -m scripts.check_core_config_logging
```

## 15. 测试要求

建议创建：

`tests/test_core_config_logging.py`

测试至少覆盖：

1. settings 对象可正常加载。
2. `APP_DEBUG` 可转换为布尔值。
3. `APP_ENV` 有默认值。
4. logger 初始化后可以输出日志。
5. 重复初始化 logger 不会重复添加 handler。
6. UTC 当前时间函数可返回 datetime。
7. PRC 当前时间函数可返回 datetime。
8. `utc_naive_to_prc_naive()` 转换结果正确。
9. 毫秒时间戳和 UTC datetime 可互相转换。
10. 基础异常类可以正常实例化。

测试不得依赖：

1. 真实 MySQL。
2. 真实 Redis。
3. 真实 Binance。
4. 真实 Hermes。
5. 真实 DeepSeek。
6. 网络环境。
7. 生产 `.env`。

## 16. 日志文件与 Git 管理

本阶段可以允许程序在本地运行时生成日志文件，例如：

```
logs/app.log
```

但真实日志文件不得提交。

必须确认 `.gitignore` 已忽略：

```
logs/*.log
logs/*.txt
```

允许提交：

```
logs/.gitkeep
```

如果 `.gitignore` 已存在，只允许补齐缺失规则，不得清空重写。

## 17. 安全要求

本阶段必须重点防止敏感信息泄露。

禁止：

1. 打印完整 settings。
2. 打印完整 `.env`。
3. 打印 MySQL 密码。
4. 打印 Redis 密码。
5. 打印 Hermes webhook 完整 URL。
6. 打印 Hermes secret。
7. 打印 token。
8. 打印 Authorization。
9. 打印 cookie。
10. 把敏感配置写入测试快照。
11. 把敏感配置写入实现说明文件。

如果需要展示配置，只能展示非敏感字段，例如：

```
APP_NAME
APP_ENV
APP_DEBUG
LOG_LEVEL
TIMEZONE
```

## 18. 数据库影响

本阶段不得创建、修改、删除任何数据库表。

本阶段不得执行 Alembic 迁移。

本阶段不得写入 MySQL。

本阶段不得读取 MySQL。

MySQL 连接和基础读写应在后续 `03_infra_mysql_redis.md` 中实现。

## 19. Redis 影响

本阶段不得连接 Redis。

本阶段不得写入 Redis key。

本阶段不得读取 Redis key。

尤其不得创建：

`bitcoin_price`

该 key 属于后续 10s 价格监控阶段。

## 20. Hermes 影响

本阶段不得调用 Hermes。

本阶段不得创建真实报警逻辑。

本阶段不得保存 `channel_response`。

本阶段可以预留 Hermes 配置字段，但不得使用该字段发送请求。

Hermes 能力应在 `04_alerting_through_hermes.md` 中实现。

## 21. Binance 影响

本阶段不得请求 Binance。

本阶段不得实现 Binance REST Client。

本阶段不得写任何 Binance API 调用代码。

本阶段不得引入 Binance SDK。

Binance REST Client 应在 `05_binance_rest_client.md` 中实现。

## 22. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得实现定时任务。

本阶段不得调用 scripts 触发采集。

`trigger_source` 规则属于后续采集入口和 scheduler 相关阶段，本阶段最多可以在文档中保留，不实现实际采集逻辑。

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

## 24. 交付物要求

本阶段完成后，Codex 必须交付：

1. 核心配置模块。
2. 核心日志模块。
3. 核心时间工具模块。
4. 基础异常模块。
5. 基础常量模块。
6. 必要的 `.env.example` 补充。
7. 必要的 `pyproject.toml` 最小依赖补充。
8. 核心检查脚本。
9. 核心测试文件。
10. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/02_core_config_logging.md`

说明文件必须描述：

1. 本模块入口。
2. 配置读取方式。
3. 日志初始化方式。
4. UTC / PRC 时间工具边界。
5. 敏感信息脱敏原则。
6. 本模块不负责的边界。
7. 后续哪些模块会复用本模块。

本阶段说明文件不需要描述：

1. 入库流程。
2. Hermes 告警流程。
3. Binance 请求流程。
4. K线校验流程。
5. 采集流程。
6. 回补流程。
7. 复核流程。

原因：这些能力本阶段不实现。

## 25. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_core_config_logging` 可以运行成功。
2. `pytest` 可以运行成功。
3. `app.core.config` 可以被正常导入。
4. `app.core.logger` 可以被正常导入。
5. `app.core.time_utils` 可以被正常导入。
6. `app.core.exceptions` 可以被正常导入。
7. `APP_DEBUG` 可以从环境变量读取并转换为布尔值。
8. logger 重复初始化不会重复添加 handler。
9. `utc_naive_to_prc_naive()` 可以正常工作。
10. 不存在 MySQL 连接代码。
11. 不存在 Redis 连接代码。
12. 不存在 Binance 请求代码。
13. 不存在 Hermes 请求代码。
14. 不存在 scheduler 代码。
15. 不存在交易执行相关代码。
16. `.env.example` 不包含真实密钥。
17. 日志输出不包含敏感配置值。
18. `docs/implementation/02_core_config_logging.md` 已创建或补齐。

## 26. 人工审查清单

合并前用户应人工检查：

1. 查看 `app/core/` 是否只包含本阶段允许的核心模块。
2. 查看 `.env.example` 是否没有真实密钥。
3. 查看 `pyproject.toml` 是否只做最小依赖补充。
4. 查看 `.gitignore` 是否忽略真实日志文件。
5. 搜索是否存在 MySQL 连接代码。
6. 搜索是否存在 Redis 连接代码。
7. 搜索是否存在 Binance 请求代码。
8. 搜索是否存在 Hermes 请求代码。
9. 搜索是否存在 DeepSeek 调用。
10. 搜索是否存在交易执行相关关键词。
11. 运行核心检查脚本。
12. 运行测试。

建议搜索：

```
grep -R "create_engine" app scripts tests
grep -R "redis" app scripts tests
grep -R "requests" app scripts tests
grep -R "httpx" app scripts tests
grep -R "Binance" app scripts tests
grep -R "Hermes" app scripts tests
grep -R "DeepSeek" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果搜索结果只是文档、注释或允许的环境变量，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 27. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 提前实现 MySQL 连接。
2. 提前实现 Redis 连接。
3. 提前请求 Binance。
4. 提前调用 Hermes。
5. 提前实现 scheduler。
6. 提前创建 K线表。
7. 提前创建告警表。
8. 提前实现 K线采集。
9. 提前实现 K线回补。
10. 提前实现 K线复核。
11. 提前实现 10s 价格监控。
12. 提前实现策略模块。
13. 提前实现 DeepSeek 调用。
14. 提前实现交易建议。
15. 实现任何交易执行代码。
16. 提交真实密钥。
17. 提交真实日志。
18. 提交 `.env`。
19. 把业务逻辑写进 `scripts`。
20. 删除、清空或覆盖已有文档。

## 28. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行检查：

   python -m scripts.check_core_config_logging
   pytest

3. 人工确认没有异常删除、覆盖或越界实现。

4. 用户确认无问题后再提交：

   git add .
   git commit -m "完成核心配置日志时间工具"

5. 用户自行推送分支，并进入代码审查流程。
