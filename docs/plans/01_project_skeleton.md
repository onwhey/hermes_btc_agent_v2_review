# 01 Project Skeleton Plan

## 1. 阶段目标

本阶段只建立 Python 项目的最小工程骨架，为后续配置、日志、数据库、Redis、Binance REST、K线采集、Hermes 报警等模块提供清晰目录边界。

本阶段的核心目标不是实现业务功能，而是让仓库具备：

1. 清晰的 Python 包结构。
2. 可安装的项目配置。
3. 可运行的基础检查命令。
4. 可执行的测试框架。
5. 明确的目录职责。
6. 不泄露密钥的环境变量示例。
7. 后续模块可以稳定扩展的基础结构。

## 2. 本阶段明确不做

本阶段不得实现任何业务功能。

禁止实现：

1. Binance REST 请求。
2. 4h K线采集。
3. 4h K线回补。
4. K线一致性复核。
5. 10s 价格监控。
6. MySQL 业务表。
7. Redis 业务读写。
8. Hermes 真实报警。
9. DeepSeek 或其他大模型调用。
10. 策略分析。
11. 交易建议。
12. 自动下单、自动平仓、自动调仓。
13. 账户、订单、持仓相关接口。
14. WebSocket 行情接入。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/architecture/system_architecture.md`
3. `docs/architecture/module_boundaries.md`
4. `docs/decisions/0001-no-auto-trading.md`
5. `docs/decisions/0002-kline-source-and-time-rules.md`
6. `docs/decisions/0004-alerting-through-hermes.md`

本阶段只根据这些文档建立目录和基础工程边界，不得提前实现后续文档中的业务逻辑。

## 4. 建议分支

建议分支名：

`feature/01-project-skeleton`

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

本阶段创建目录时必须遵守：

1. 如果目录已经存在，只检查并保留，不得删除后重建。
2. 不得覆盖、清空、移动已有 `docs/` 内容。
3. 不得删除已有 `requirements/`、`architecture/`、`decisions/`、`plans/` 文件。
4. 只允许补齐当前缺失的目录或占位文件。
5. `docs/implementation/` 如果已经存在，只保留；如果不存在，才创建。
6. `.gitkeep` 只在目录为空且需要 Git 跟踪时创建，不得覆盖已有文件。


第一阶段建议创建以下目录：

```text
app/
  __init__.py
  alerting/
    __init__.py
  core/
    __init__.py
  exchange/
    __init__.py
    binance/
      __init__.py
  storage/
    __init__.py
    mysql/
      __init__.py
    redis/
      __init__.py
  market_data/
    __init__.py
  scheduler/
    __init__.py
  monitoring/
    __init__.py

configs/
  .gitkeep

migrations/
  .gitkeep

scripts/
  __init__.py

tests/
  __init__.py

logs/
  .gitkeep

docs/
  implementation/
    .gitkeep
```

说明：

1. 本阶段只创建目录和空包，不实现业务逻辑。
2. `app/` 是正式应用代码目录。
3. `scripts/` 是命令入口目录，不是业务逻辑目录。
4. `tests/` 是测试目录。
5. `logs/` 只保留 `.gitkeep`，不得提交真实日志文件。
6. `docs/implementation/` 用于后续模块实现说明，本阶段只创建目录。
7. `app/monitoring` 用于后续系统健康检查、运行状态观测和任务状态观测。
8. Hermes 基础报警模块不放在 `app/monitoring`，而放在 `app/alerting`。


## 6. 目录职责

### 6.1 `app/core`

用于后续放置：

1. 配置读取。
2. 日志初始化。
3. 时间工具。
4. 异常基类。
5. 通用常量。

本阶段只创建目录，不实现配置、日志和时间工具。

### 6.2 `app/exchange/binance`

用于后续放置 Binance U 本位合约 REST 客户端。

本阶段禁止实现 Binance 请求。

尤其禁止实现：

1. 下单接口。
2. 撤单接口。
3. 查账户接口。
4. 查持仓接口。
5. 调杠杆接口。
6. 调保证金模式接口。

### 6.3 `app/storage/mysql`

用于后续放置 MySQL 连接、SQLAlchemy 模型、Repository、迁移相关代码。

本阶段禁止创建业务表和业务 Repository。

### 6.4 `app/storage/redis`

用于后续放置 Redis 客户端和短期状态读写逻辑。

本阶段禁止实现 Redis 业务 key。

### 6.5 `app/market_data`

用于后续放置行情数据相关业务服务，例如：

1. 4h K线采集服务。
2. 4h K线回补服务。
3. K线一致性复核服务。
4. K线解析器。
5. K线质量检查器。

本阶段禁止实现以上服务。

### 6.6 `app/scheduler`

用于后续放置定时任务入口。

本阶段禁止实现定时任务。

后续如果 scheduler 触发脚本，必须显式传入 `--trigger-source scheduler`，但本阶段不实现该逻辑。

### 6.7 `app/monitoring`

用于后续放置系统监控和基础健康检查相关逻辑。

本阶段最多允许保留空目录，不得实现 Hermes 报警。

### 6.8 `scripts`

`scripts` 目录用于命令行入口。

本阶段可以创建示例检查脚本，但不得实现正式业务入口。

`scripts` 永远不得承载核心业务逻辑。后续脚本只能负责：

1. 解析命令行参数。
2. 校验必要参数。
3. 初始化配置和日志。
4. 调用 `app/` 内部 service。
5. 返回退出码。

`scripts` 禁止：

1. 直接请求 Binance。
2. 直接写数据库。
3. 直接拼接业务 SQL。
4. 直接实现 K线连续性检查。
5. 直接实现 Hermes 复杂报警。
6. 直接调用 DeepSeek。
7. 直接生成交易建议。

## 7. 需要检查和补齐的文件

本阶段处理文件时必须遵守：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

需要检查和补齐：

- `pyproject.toml`
- `.env.example`
- `.gitignore`
- `README.md`
- `AGENTS.md`
- `alembic.ini`
- `scripts/check_project_skeleton.py`
- `tests/test_project_skeleton.py`

其中 `README.md` 和 `AGENTS.md` 如果已经存在，本阶段只允许保留或补充最小占位信息；正式内容后续单独完善。

## 8. `pyproject.toml` 要求

`pyproject.toml` 应至少包含：

1. 项目名称。
2. Python 版本要求。
3. 包发现规则。
4. 基础开发依赖。
5. 测试配置。

建议项目名称：

```text
hermes-btc-agent
```

建议 Python 版本：

```text
>=3.10
```

注意：

1. 不要把 `logs`、`configs`、`migrations` 当成 Python 包发布。
2. 只应把 `app` 和必要的 `scripts` 纳入项目结构。
3. 避免出现“多个顶级包被错误发现”的问题。

## 9. `.env.example` 要求

`.env.example` 只能提供示例变量，不得包含真实密钥。

本阶段建议只保留基础变量：

```text
APP_NAME=hermes_btc_agent
APP_ENV=dev
APP_DEBUG=false
LOG_LEVEL=INFO
TIMEZONE=UTC
```

可以预留但不得填写真实值：

```text
MYSQL_HOST=
MYSQL_PORT=
MYSQL_DATABASE=
MYSQL_USER=
MYSQL_PASSWORD=

REDIS_HOST=
REDIS_PORT=
REDIS_PASSWORD=

BINANCE_BASE_URL=
HERMES_WEBHOOK_URL=
HERMES_SECRET=
```

禁止：

1. 提交真实 `.env`。
2. 提交真实 token。
3. 提交真实 secret。
4. 提交真实 webhook 完整地址。
5. 提交数据库密码。

## 10. `.gitignore` 要求

`.gitignore` 必须至少忽略：

```text
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
logs/*.log
logs/*.txt
*.sqlite
*.db
.DS_Store
.idea/
.vscode/
```

注意：

1. `logs/.gitkeep` 可以保留。
2. 真实日志文件不得提交。
3. 本地虚拟环境不得提交。

## 11. `alembic.ini` 要求

本阶段可以创建 `alembic.ini` 占位，但不得生成业务迁移。

注意：

1. 不创建 K线表。
2. 不创建告警表。
3. 不创建策略表。
4. 不创建建议表。
5. 不连接真实数据库执行迁移。

真正的 Alembic 配置和迁移应在后续 MySQL 阶段完成。

## 12. 基础检查脚本

可以创建：

```text
scripts/check_project_skeleton.py
```

该脚本只允许检查：

1. 当前 Python 版本。
2. 关键目录是否存在。
3. 关键文件是否存在。
4. `app` 包是否可导入。

禁止该脚本：

1. 请求 Binance。
2. 连接 MySQL。
3. 连接 Redis。
4. 发送 Hermes。
5. 写入数据库。
6. 写入 Redis。
7. 创建业务表。

示例运行方式：

```bash
python -m scripts.check_project_skeleton
```

## 13. 测试要求

可以创建：

```text
tests/test_project_skeleton.py
```

测试内容只覆盖：

1. `app` 包可导入。
2. 核心目录存在。
3. 基础文件存在。
4. `scripts.check_project_skeleton` 可导入。
5. `pyproject.toml` 存在。

本阶段不测试数据库、Redis、Binance、Hermes。

## 14. 日志要求

本阶段不实现正式日志系统。

可以在检查脚本中使用标准输出打印检查结果，但不得创建真实业务日志。

正式日志模块应在 `02_core_config_logging.md` 中实现。

## 15. 数据库影响

本阶段不得创建、修改、删除任何数据库表。

本阶段不得执行 Alembic 迁移。

本阶段不得写入 MySQL。

## 16. Redis 影响

本阶段不得连接 Redis。

本阶段不得写入 Redis key。

尤其不得创建：

```text
bitcoin_price
```

该 key 属于后续 10s 价格监控阶段。

## 17. Hermes 报警影响

本阶段不得调用 Hermes。

本阶段不得创建真实报警逻辑。

本阶段不得保存 `channel_response`。

Hermes 能力应在 `04_alerting_through_hermes.md` 中实现。

## 18. Binance 影响

本阶段不得请求 Binance。

本阶段不得实现 Binance REST Client。

本阶段不得写任何 Binance API 调用代码。

Binance REST Client 应在 `05_binance_rest_client.md` 中实现。

## 19. 交易安全边界

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

## 20. 与后续模块的边界

本阶段只为后续模块预留目录，不实现后续模块。

后续模块包括：

1. 配置、日志、时间工具。
2. MySQL / Redis 基础设施。
3. Hermes 报警。
4. Binance REST Client。
5. 4h K线表与 Repository。
6. K线质量检查。
7. 手动回补。
8. 自动增量采集。
9. 10s 价格监控。
10. 未来策略、建议生命周期、复盘评估。

## 21. 交付物要求

本阶段完成后，Codex 必须交付：

1. 项目基础目录结构。
2. `pyproject.toml`。
3. `.env.example`。
4. `.gitignore`。
5. `alembic.ini` 占位文件。
6. 基础检查脚本。
7. 基础测试文件。
8. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/01_project_skeleton.md`

说明文件必须描述：

1. 本阶段创建的目录。
2. 本阶段创建的基础文件。
3. 各主要目录的用途。
4. 哪些目录只是占位。
5. 本阶段没有实现哪些业务能力。
6. 后续哪个 plan 会继续完善相关能力。

本阶段说明文件不需要描述：

1. 数据校验流程。
2. 入库流程。
3. Hermes 告警流程。
4. Binance 请求流程。
5. K线采集流程。

原因：这些能力本阶段不实现。

## 22. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_project_skeleton` 可以运行成功。
2. `pytest` 可以运行成功。
3. `app` 包可以被正常导入。
4. 目录结构符合本 plan。
5. `.env.example` 不包含真实密钥。
6. `.gitignore` 已忽略 `.env`、`.venv`、日志文件、缓存目录。
7. 没有实现 Binance 请求。
8. 没有实现 MySQL 业务写入。
9. 没有实现 Redis 业务写入。
10. 没有实现 Hermes 报警。
11. 没有实现策略、建议、交易执行相关代码。
12. 没有新增任何自动交易相关接口。

## 23. 人工审查清单

合并前人工检查：

1. 查看目录结构是否符合本 plan。
2. 查看 `pyproject.toml` 是否避免错误包发现。
3. 查看 `.env.example` 是否没有真实密钥。
4. 查看 `.gitignore` 是否忽略敏感文件。
5. 搜索是否存在交易执行相关关键词。
6. 搜索是否存在 Binance API 请求代码。
7. 搜索是否存在 MySQL / Redis 业务逻辑。
8. 搜索是否存在 Hermes 调用。
9. 运行测试。
10. 运行基础检查脚本。

建议搜索：

```bash
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
grep -R "Binance" app scripts tests
grep -R "Hermes" app scripts tests
```

如果搜索结果只是文档或占位说明，需要人工判断；如果出现真实业务调用，应拒绝合并。

## 24. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 提前实现业务代码。
2. 提前连接 Binance。
3. 提前连接 MySQL。
4. 提前连接 Redis。
5. 提前调用 Hermes。
6. 提前创建 K线表。
7. 提前创建告警表。
8. 提前实现 scheduler。
9. 提前实现 scripts 业务入口。
10. 提前实现策略模块。
11. 提前实现 DeepSeek 调用。
12. 提前实现交易建议。
13. 实现任何交易执行代码。
14. 提交真实密钥。
15. 提交真实日志。
16. 提交 `.env`。
17. 把业务逻辑写进 `scripts`。

## 25. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

    git status
    git diff

2. 运行检查：

    python -m scripts.check_project_skeleton
    pytest

3. 人工确认没有异常删除、覆盖或越界实现。

4. 用户确认无问题后再提交：

    git add .
    git commit -m "完成项目骨架"

5. 用户自行推送分支，并进入代码审查流程。
