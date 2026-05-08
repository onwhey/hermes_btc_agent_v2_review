## 项目核心交易目标

本项目不是自动交易机器人，也不是高频量化系统。

本项目目标是辅助用户进行 BTCUSDT U 本位合约的低频策略决策，追求低杠杆、稳定复利、长期可验证的盈利能力。

用户确认：严格遵循系统策略时，人工执行杠杆原则上不超过 5 倍。

Codex 开发时必须遵守：

1. 不实现自动下单；
2. 不实现自动平仓；
3. 不实现自动调仓；
4. 不实现账户读取后自动操作；
5. 不把系统设计成高频交易系统；
6. 不默认生成小空间短线建议；
7. 策略建议必须关注目标空间、失效条件、手续费、滑点、盈亏比和最大回撤；
8. 复盘评估必须围绕长期稳定复利，而不是单次暴利

## 代码注释要求

Codex 编写或修改代码时，必须保证注释清晰，方便用户后期审阅代码和理解业务流程。

### 1. 类、方法、脚本入口必须写清楚职责

每个重要类、业务方法、脚本入口函数都必须有注释。

注释至少说明：

1. 该方法负责什么功能。
2. 关键参数是什么意思。
3. 返回值是什么。
4. 可能失败的场景是什么。
5. 是否会访问外部服务。
6. 是否会写入 MySQL。
7. 是否会写入 Redis。
8. 是否会发送 Hermes 提醒。
9. 是否会影响数据质量记录或告警记录。

例如：

def insert_alert_message(...):
    """
    写入一条提醒消息记录。

    功能：
    - 将系统产生的提醒事件保存到 MySQL 的 alert_message 表。
    - 后续 Hermes 发送结果可以回写到 channel_response 字段。

    参数：
    - session: SQLAlchemy 数据库会话。
    - alert_event: 已构造好的提醒事件对象。

    返回：
    - 新增的 alert_message 记录对象。

    注意：
    - 本方法只负责入库，不负责直接发送微信。
    """

### 2. 关键业务逻辑必须写注释

以下场景必须写注释：

1. K线连续性判断。
2. 已收盘 K线判断。
3. 数据质量失败时为什么停止写库。
4. Hermes 告警触发条件。
5. Redis 冷却和去重逻辑。
6. 数据库 upsert 逻辑。
7. UTC 与 PRC 时间转换。
8. 未来策略建议、生命周期、复盘相关逻辑。

注释应解释业务含义和设计原因。

不要只写重复代码表面动作的无效注释。

例如，不要写：

i = i + 1  # i 加 1

应该写：

# 当前 K线与上一根 K线 open_time_ms 相差必须等于 interval_ms，
# 否则说明中间存在缺口，不能继续写入正式 K线表。

## 业务模块说明文件要求

Codex 每完成一个独立业务模块，必须同时提交一份模块说明文件，方便用户后期审阅代码和理解业务生命周期。

模块说明文件统一放在：

docs/implementation/

如果目录不存在，Codex 应创建该目录。

文件命名建议：

1. docs/implementation/01_project_skeleton_lifecycle.md
2. docs/implementation/02_infra_mysql_redis_lifecycle.md
3. docs/implementation/03_binance_rest_client_lifecycle.md
4. docs/implementation/04_market_kline_4h_lifecycle.md
5. docs/implementation/05_4h_backfill_and_collector_lifecycle.md

模块说明文件不需要写成详细教程，但必须说明当前业务的大致生命周期。

每份说明文件至少包含：

1. 模块目的。
2. 入口文件。
3. 入口方法。
4. 主要调用链路。
5. 关键文件和关键类。
6. 数据从哪里来。
7. 数据经过哪些校验。
8. 数据写入哪里。
9. 失败时如何记录。
10. 是否会触发 Hermes 微信提醒。
11. 相关配置项。
12. 相关数据库表。
13. 相关 Redis key。
14. 本模块不负责什么。

示例：

Binance REST 客户端模块说明：

1. 从 scripts/check_binance_rest.py 进入。
2. 调用 BinanceUmFuturesRestClient.get_server_time() 检查服务时间。
3. 调用 BinanceUmFuturesRestClient.get_klines() 获取 K线。
4. REST 客户端负责请求、重试、错误包装。
5. parser 负责把 Binance 原始数组转换为内部 row。
6. 当前模块不负责写入 MySQL。
7. 当前模块不负责数据质量检查。
8. 当前模块不负责发送 Hermes 提醒。

Codex 完成模块后，如果只提交代码、不提交对应说明文件，视为任务未完成。


## 可审阅性要求

Codex 编写代码时，必须优先保证代码可审阅、可维护、可测试。

### 1. 文件大小限制

原则上，单个 Python 业务文件不应超过 500 行。

如果单个文件超过 800 行，Codex 必须说明为什么不能拆分。

如果单个文件超过 1000 行，默认应拆分为多个职责清晰的文件。

禁止把多个业务职责塞进一个大文件。

例如，以下逻辑不应全部写在同一个文件中：

1. Binance REST 请求。
2. K线解析。
3. K线连续性检查。
4. MySQL 入库。
5. Redis 缓存。
6. Hermes 告警。
7. 定时任务入口。
8. 测试或 mock 逻辑。

### 2. 单文件单职责

每个文件应有清晰职责。

推荐拆分方式：

1. client：外部接口请求。
2. parser：数据解析。
3. validator：数据校验。
4. repository：数据库读写。
5. service：业务编排。
6. dispatcher：提醒分发。
7. script：命令行入口。
8. test：测试代码。

禁止为了方便把所有逻辑写进一个脚本。

### 3. 每次提交必须便于审查

Codex 每完成一个任务，必须在回复或模块说明文件中列出：

1. 本次修改了哪些文件。
2. 每个文件负责什么。
3. 本次新增了哪些入口。
4. 本次核心调用链路是什么。
5. 本次运行了哪些检查或测试。
6. 本次没有实现哪些内容。
7. 是否存在风险或待确认事项。

如果本次修改超过 500 行，Codex 必须说明修改集中在哪里，以及用户应该重点审查哪些文件和哪些方法。

### 4. 禁止大范围无说明改动

Codex 不得在一个任务中无说明地修改大量无关文件。

如果必须修改多个模块，必须先说明原因，并确认这些修改属于当前 plan 范围。

一个开发分支原则上只做一个模块。

一个提交原则上只解决一个明确问题。

### 5. 模块完成后必须提供实现说明

每个业务模块完成后，必须在 `docs/implementation/` 中提供对应说明文件。

说明文件必须帮助用户快速理解：

1. 从哪个脚本或入口开始。
2. 调用了哪些 service。
3. 调用了哪些 client。
4. 调用了哪些 validator。
5. 调用了哪些 repository。
6. 失败时如何记录。
7. 什么时候触发 Hermes 告警。
8. 本模块不负责什么。

如果只提交代码、不提交说明文件，视为任务未完成。

## 文档使用规则

Codex 阅读项目文档时，必须区分不同文档的职责。

### 1. 各类文档职责

`docs/requirements/` 只定义业务需求，说明系统要实现什么、不能做什么。

`docs/architecture/` 只定义系统结构、模块边界和数据流，说明系统应该如何分层、数据如何流动、模块之间如何协作。

`docs/decisions/` 只记录已经确定的重要架构决策，说明哪些方向已经定死，不能被代码实现随意推翻。

`docs/plans/` 才是具体开发计划，说明当前阶段要创建哪些文件、实现哪些模块、交付哪些检查脚本和说明文档。

`docs/implementation/` 是模块完成后的实现说明，用来解释代码实际调用链路和业务生命周期。

### 2. 只有 plans 可以作为开发任务清单

Codex 不得把 requirements、architecture、decisions 中的描述直接当成“需要立刻实现的代码任务”。

Codex 写代码时，必须以当前任务指定的 `docs/plans/*.md` 为实际开发范围。

其他文档只能作为约束和背景。

如果 requirements、architecture、decisions 中提到未来能力，但当前 plan 没有要求实现，Codex 不得提前实现。

### 3. 遇到重复描述时的处理规则

如果多个文档重复描述同一条规则，Codex 应理解为同一约束的强调，不得因此重复实现代码。

例如：

1. 多个文档都说“不得自动交易”，只表示这是硬性边界，不表示要实现多个“禁止自动交易模块”。
2. 多个文档都说“K线异常必须 Hermes 提醒”，只表示所有相关流程都必须遵守，不表示要写多套提醒系统。
3. 多个文档都说“WebSocket 不得拼接正式 4h K线”，只表示数据源边界，不表示要写多个 WebSocket 禁止逻辑。

### 4. 冲突处理规则

如果不同文档之间出现冲突，Codex 不得自行选择一个方向继续写代码。

Codex 必须停止开发，并提示用户需要先统一文档。

优先级原则：

1. AGENTS.md
2. docs/requirements/
3. docs/architecture/
4. docs/decisions/
5. docs/plans/
6. 代码实现

但是如果 `docs/decisions/` 明确记录了某个架构决策，后续 plan 不得随意推翻该 decision。

### 5. 不得重复实现

Codex 不得因为多个文档都提到同一能力，就在多个模块中重复实现同一套逻辑。

例如：

1. Hermes 发送逻辑只能通过统一 alerting 模块实现。
2. Binance REST 请求逻辑只能通过统一 exchange client 实现。
3. K线连续性检查应通过统一 validator 或等价数据质量模块实现。
4. MySQL 写入应通过统一 Repository 实现。
5. Redis key 管理应通过统一 Redis storage 模块实现。
6. UTC 与 PRC 转换应通过统一时间工具实现。

如果发现已有模块可以复用，Codex 应优先复用，不得重新写一套相同能力。


### 6. 现在以 10 个 plan 为准



### Scheduler 与 scripts 边界

Codex 不得让 scheduler、cron、APScheduler 或任何定时任务系统调用 `scripts/*.py`。

`scripts/*.py` 只允许作为人工 CLI 入口。

定时任务必须直接调用 `app/` 下的业务 service，例如：

- `app/market_data/kline_4h_collector.py`
- `app/market_data/kline_integrity_checker.py`

禁止把核心业务逻辑写入 scripts。