# 01 Project Skeleton 实现说明

## 1. 功能：项目基础骨架

### 1.1 发起方式

用户在当前分支内要求实现：

    docs/plans/01_project_skeleton.md

本阶段只创建 Python 项目骨架、占位目录、基础配置文件、基础检查脚本和基础测试文件。

### 1.2 入口文件

`scripts/check_project_skeleton.py`

入口方法：

`main()`

### 1.3 核心 service

本阶段没有核心 service。

原因：01 阶段只建立目录和工程边界，不实现业务流程。

### 1.4 调用链路

    用户 CLI
        ↓
    scripts/check_project_skeleton.py::main
        ↓
    scripts/check_project_skeleton.py::collect_project_skeleton_errors
        ↓
    importlib.import_module("app")

该调用链只检查本地文件系统和 Python 包导入，不调用 `app/` 内业务 service。

### 1.5 读取配置

本功能不读取运行配置。

`.env.example` 仅提供示例变量：

- `APP_NAME`
- `APP_ENV`
- `APP_DEBUG`
- `LOG_LEVEL`
- `TIMEZONE`
- `MYSQL_*`
- `REDIS_*`
- `BINANCE_BASE_URL`
- `HERMES_WEBHOOK_URL`
- `HERMES_SECRET`

`.env.example` 不包含真实密钥、真实 webhook、真实数据库密码或真实 token。

### 1.6 外部接口

本功能不请求外部接口。

本阶段没有 Binance REST 请求。
本阶段没有 WebSocket 连接。
本阶段没有 Hermes 请求。
本阶段没有 DeepSeek 或其他大模型调用。

### 1.7 数据库与 Redis

本功能不读取数据库。
本功能不写入数据库。
本功能不创建 MySQL 业务表。
本功能不执行 Alembic migration。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不创建 `bitcoin_price`。

### 1.8 Hermes

本功能不发送 Hermes。
本功能不写入 `alert_message`。
本功能不保存 `channel_response`。
本功能不调用 DeepSeek 或其他大模型生成报警内容。

### 1.9 scheduler、trigger_source 与 data_source

本功能不涉及 scheduler。
本功能不调用 scripts 触发业务任务。
本功能不涉及正式 K 线写入。
本功能不涉及 `trigger_source`。
本功能不涉及 `data_source`。

## 2. 本阶段创建的目录

### 2.1 应用目录

- `app/`：正式应用代码根包，本阶段只提供包边界。
- `app/core/`：后续放配置、日志、异常、时间工具，本阶段不实现。
- `app/exchange/`：后续放交易所公开行情接入边界，本阶段不实现请求。
- `app/exchange/binance/`：后续放 Binance 公开行情能力，本阶段不实现 REST 或 WebSocket。
- `app/storage/`：后续放存储层边界，本阶段不建立连接。
- `app/storage/mysql/`：后续放 MySQL session、model、repository，本阶段不创建表。
- `app/storage/redis/`：后续放 Redis 客户端和短期状态，本阶段不创建 key。
- `app/market_data/`：后续放行情采集、解析、校验、回补、复核，本阶段不实现。
- `app/alerting/`：后续放统一提醒业务层，本阶段不发送提醒。
- `app/scheduler/`：后续放定时任务入口，本阶段不定义 job。
- `app/monitoring/`：后续放健康检查和状态观测，本阶段不连接基础设施。

### 2.2 非应用目录

- `configs/`：非敏感配置目录，本阶段仅保留占位文件。
- `migrations/`：Alembic 迁移目录，本阶段仅保留占位文件，不生成业务 migration。
- `scripts/`：CLI 与检查入口目录，本阶段新增骨架检查脚本。
- `tests/`：测试目录，本阶段新增骨架测试。
- `logs/`：本地日志目录，本阶段只保留 `.gitkeep`，真实日志由 `.gitignore` 忽略。
- `docs/implementation/`：实现说明目录，本阶段新增本文件。

## 3. 本阶段创建或补齐的基础文件

- `pyproject.toml`：声明项目名称、Python 版本、包发现规则和 pytest 配置，只发现 `app*` 与 `scripts*` 包。
- `.env.example`：提供示例环境变量，不包含真实密钥。
- `.gitignore`：忽略 `.env`、虚拟环境、Python 缓存、pytest 缓存、本地日志和本地数据库文件。
- `alembic.ini`：Alembic 占位配置，不定义业务迁移，不连接真实数据库。
- `scripts/check_project_skeleton.py`：本地骨架检查入口，只检查 Python 版本、目录、文件和 `app` 导入。
- `tests/test_project_skeleton.py`：本地骨架测试，只检查导入、目录、文件和检查脚本。
- `app/**/__init__.py`：Python 包标记和模块边界说明，不包含业务逻辑。
- `configs/.gitkeep`：保留非敏感配置目录。
- `migrations/.gitkeep`：保留迁移目录。
- `logs/.gitkeep`：保留日志目录，真实日志不提交。

## 4. 异常处理

异常可能发生在：

- `scripts/check_project_skeleton.py::collect_project_skeleton_errors`
- `scripts/check_project_skeleton.py::main`

异常路径：

1. Python 版本低于 3.10 时，`collect_project_skeleton_errors()` 返回错误信息。
2. 关键目录缺失时，`collect_project_skeleton_errors()` 返回错误信息。
3. 关键文件缺失时，`collect_project_skeleton_errors()` 返回错误信息。
4. `app` 包无法导入时，`collect_project_skeleton_errors()` 捕获 `ImportError` 并返回错误信息。
5. `main()` 根据错误列表返回 1，检查通过返回 0。

本阶段不写入事件日志。
本阶段不发送 Hermes。
本阶段不重试。
本阶段不允许 `partial_success` 作为业务状态。
本阶段不修改正式数据。
本阶段不自动修复。

## 5. 对应测试

测试文件：

`tests/test_project_skeleton.py`

覆盖内容：

- `app` 包可导入。
- 核心目录存在。
- 基础文件存在。
- `scripts.check_project_skeleton` 可导入。
- 骨架检查函数返回空错误列表。

测试类型：

- 全部是本地 mock-free 骨架测试。
- 默认 `pytest` 不访问外部服务。
- 默认 `pytest` 不连接真实 MySQL。
- 默认 `pytest` 不连接真实 Redis。
- 默认 `pytest` 不发送真实 Hermes。
- 默认 `pytest` 不调用 DeepSeek。
- 默认 `pytest` 不访问交易接口。

本阶段没有集成测试开关。

## 6. 人工运行检查

建议按顺序运行：

    python -m scripts.check_project_skeleton
    python -m scripts.check_project_invariants
    pytest

这些命令都只做本地检查，不请求外部接口，不连接数据库，不连接 Redis，不发送 Hermes。

## 7. 本阶段明确没有实现

- 没有实现配置读取模块。
- 没有实现日志初始化模块。
- 没有实现时间工具。
- 没有实现 MySQL 连接。
- 没有实现 Redis 连接。
- 没有实现 Alembic 业务 migration。
- 没有实现 Binance REST Client。
- 没有实现 WebSocket 行情接入。
- 没有实现 4h K 线采集。
- 没有实现 4h K 线回补。
- 没有实现 K 线质量检查。
- 没有实现 10s 价格监控。
- 没有实现 Hermes 真实报警。
- 没有实现策略分析。
- 没有实现 DeepSeek 或其他大模型调用。
- 没有实现交易建议。
- 没有实现自动交易相关任何能力。

## 8. 后续 plans 继续实现的能力

- `02_core_config_logging.md`：配置、日志、异常、时间工具。
- `03_infra_mysql_redis.md`：MySQL 与 Redis 基础连接。
- `04_alerting_through_hermes.md`：Hermes 基础报警链路。
- `05_binance_rest_client.md`：Binance REST 公开行情客户端。
- `06_market_kline_4h.md`：4h K 线表结构、DTO、parser、validator、repository。
- `07_kline_quality_checker.md`：K 线质量检查。
- `08_4h_backfill.md`：4h K 线手动回补。
- `09_4h_incremental_collector.md`：4h K 线增量采集。
- `10_price_monitor_10s.md`：WebSocket 10s 价格监控。
- `11_daily_kline_integrity_check.md`：每日 K 线一致性复核。

## 9. 边界自检

- 自动交易：未实现。
- K 线数据来源：未实现采集，因此未写入任何 K 线。
- manual_repair / human_edit / manual_input / system_repair：未作为代码能力实现。
- REST / WebSocket 边界：未实现请求。
- trigger_source / data_source：本阶段不涉及正式 K 线写入。
- scripts 边界：检查脚本只做本地骨架检查。
- scheduler 边界：未实现 scheduler。
- DeepSeek 调用边界：未调用。
- Hermes 固定模板报警边界：未实现报警。
- MySQL / Redis 边界：未连接、未读写。
- 敏感信息提交：未提交真实密钥、真实日志或 `.env`。

