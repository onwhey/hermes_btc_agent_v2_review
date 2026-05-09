# AGENTS.md

本文档约束 Codex 或其他 AI 编程助手在本仓库中的工作方式。

Codex 必须先理解项目边界，再写代码。不能因为某个文档中出现了未来能力，就提前实现；不能因为用户没有重复强调，就绕过已有铁律。

---

## 1. 工作总原则

1. Codex 只负责在用户当前指定的分支和当前指定的 plan 范围内修改代码。
2. Git 分支创建、切换、合并、推送、删除，由用户人工完成。
3. Codex 不得自行执行生产迁移、连接生产数据库、提交真实密钥或清空用户已有文件。
4. Codex 每次开发前必须先读取 `docs/rules/project_invariants.md`。
5. 如果当前 plan、已有代码、已有文档与 `docs/rules/project_invariants.md` 冲突，Codex 必须停止实现并提示用户确认。
6. 如果 Codex 不确定某个能力是否属于当前阶段，必须停止并提示用户确认，不得自行扩大范围。

---

## 2. 必读文档顺序

Codex 实现任何 plan 前，必须按以下顺序阅读：

1. `docs/rules/project_invariants.md`
2. 当前用户指定的 `docs/plans/*.md`
3. 当前 plan 依赖的 `docs/decisions/*.md`
4. 当前 plan 依赖的 `docs/requirements/*.md`
5. 当前 plan 依赖的 `docs/architecture/*.md`
6. 前序阶段对应的 `docs/implementation/*.md`
7. 当前要修改的已有代码文件

不得只读 plan 就直接写代码。

---

## 3. 文档职责划分

Codex 阅读项目文档时，必须区分不同文档的职责。

1. `docs/rules/` 记录项目铁律和不可违反的上位约束。
2. `docs/requirements/` 定义业务需求，说明系统要实现什么、不能做什么。
3. `docs/architecture/` 定义系统结构、模块边界和数据流。
4. `docs/decisions/` 记录已经确定的重要架构决策。
5. `docs/plans/` 是具体开发计划，定义当前阶段要实现什么、创建哪些文件、如何验收。
6. `docs/implementation/` 是模块完成后的实现说明，用来解释代码实际调用链和业务生命周期。

Codex 写代码时，必须以当前用户指定的 `docs/plans/*.md` 为实际开发范围。

`requirements`、`architecture`、`decisions` 中提到的未来能力，如果当前 plan 没要求实现，Codex 不得提前实现。

---

## 4. 冲突处理规则

如果文档之间出现冲突，Codex 不得自行选择一个方向继续写代码。

业务规则冲突时，默认优先级：

1. `docs/rules/project_invariants.md`
2. `docs/decisions/`
3. `docs/requirements/`
4. `docs/architecture/`
5. `docs/plans/`
6. `docs/implementation/`
7. 现有代码

说明：

1. `AGENTS.md` 是 Codex 工作纪律文件，不用于推翻业务铁律。
2. 如果 `AGENTS.md` 与 `docs/rules/project_invariants.md` 冲突，必须停止并提示用户确认。
3. 如果某个 plan 与 decision 冲突，必须停止并提示用户确认。
4. 如果 implementation 与 plan 不一致，implementation 只能说明实际实现，不能反过来修改规则。

---

## 5. 当前开发范围规则

当前第一批 plans 的主线是数据底座和系统运行底座。

允许范围包括：

1. 项目骨架。
2. 配置、日志、异常、时间工具。
3. MySQL、Redis 基础连接。
4. Hermes 基础报警。
5. Binance REST K线能力。
6. 4h K线表结构、DTO、parser、基础 validator、repository。
7. K线质量检查。
8. 4h K线手动回补。
9. 4h K线增量采集。
10. WebSocket 10s 价格监控。

当前第一批 plans 不实现：

1. 江恩策略。
2. 趋势策略。
3. 多策略聚合。
4. DeepSeek 分析。
5. 交易建议。
6. 建议生命周期。
7. 策略复盘。
8. 模型复盘。
9. 自动交易。

如果后续用户新增 plan，以用户当前指定 plan 为准。

---

## 6. 项目核心交易目标

本项目不是自动交易机器人，也不是高频量化系统。

本项目目标是辅助用户进行 BTCUSDT U 本位合约的低频策略决策，追求低杠杆、稳定复利、长期可验证的盈利能力。

用户确认：严格遵循系统策略时，人工执行杠杆原则上不超过 5 倍。

Codex 开发时必须遵守：

1. 不实现自动下单。
2. 不实现自动平仓。
3. 不实现自动调仓。
4. 不实现自动撤单。
5. 不实现自动调整杠杆。
6. 不实现自动调整保证金模式。
7. 不实现账户读取后自动操作。
8. 不把系统设计成高频交易系统。
9. 不默认生成小空间短线建议。
10. 后续策略建议必须关注目标空间、失效条件、手续费、滑点、盈亏比和最大回撤。
11. 后续复盘评估必须围绕长期稳定复利，而不是单次暴利。

---

## 7. 自动交易禁令

Codex 永远不得实现或封装以下能力：

1. Binance order endpoint。
2. Binance account endpoint。
3. Binance position endpoint。
4. Binance leverage endpoint。
5. Binance margin endpoint。
6. Binance listenKey。
7. Binance private user data stream。
8. 自动下单。
9. 自动平仓。
10. 自动调仓。
11. 自动加仓。
12. 自动减仓。
13. 自动撤单。
14. 自动调整杠杆。
15. 自动读取账户后自行做交易决策。

如果当前任务中需要出现以上任何内容，Codex 必须拒绝实现并提示用户确认。

---

## 8. K线数据铁律

Codex 必须严格遵守：

1. 正式 K线数据只能来自 Binance REST 官方 K线接口。
2. BTCUSDT 4h 正式 K线必须来自 Binance REST `/fapi/v1/klines`。
3. 正式 K线表不得写入 WebSocket 聚合出来的 K线。
4. 正式 K线表不得写入第三方行情源。
5. 正式 K线表不得写入人工输入数据。
6. 正式 K线表不得写入模拟数据。
7. 正式 K线表只允许保存已收盘 K线。
8. 判断 K线是否收盘时，应优先使用 Binance server time。
9. 质量检查失败时不得写入正式 K线表。
10. 字段冲突时不得静默覆盖。

禁止出现以下数据来源或修复方式：

1. `manual_repair`
2. `human_edit`
3. `manual_input`
4. `system_repair`
5. 人工直接修改 K线字段。
6. 人工手填 K线字段。
7. 程序自动修复正式 K线。
8. 程序自动覆盖冲突 K线。
9. 程序自动删除异常 K线。
10. 复核任务自动修改正式 K线。

手动回补的含义只能是：

```text
用户手动触发 CLI
    ↓
系统请求 Binance REST 官方 K线
    ↓
系统解析、校验、记录、写入
```

手动回补绝不是：

```text
用户手动输入 K线字段
    ↓
系统写入数据库
```

---

## 9. trigger_source 与 data_source 规则

凡是涉及正式 K线写入，Codex 必须明确处理 `trigger_source` 和 `data_source`。

允许的 K线写入 `trigger_source`：

```text
cli
scheduler
```

允许的正式 K线 `data_source`：

```text
binance_rest_by_cli
binance_rest_by_scheduler
```

映射规则：

```text
trigger_source = cli
    ↓
data_source = binance_rest_by_cli
```

```text
trigger_source = scheduler
    ↓
data_source = binance_rest_by_scheduler
```

禁止：

1. 缺少 `trigger_source` 仍写入正式 K线表。
2. 非法 `trigger_source` 仍继续执行。
3. 自动猜测触发来源。
4. 根据是否经过 `scripts/*.py` 猜测 `data_source`。
5. scheduler 触发却记录为 `cli`。
6. CLI 触发却记录为 `scheduler`。
7. 手动回补伪装成定时采集。
8. 定时采集伪装成手动回补。

说明：

1. 上述规则主要约束正式 K线写入。
2. 10s 价格监控可以有自己的进程触发来源，例如 `cli`、`systemd`、`supervisor`，但不得与 K线写入规则混淆。

---

## 10. REST 与 WebSocket 边界

Binance REST 用于：

1. 官方已收盘 K线采集。
2. 手动 K线回补。
3. K线完整性复核。
4. Binance server time。
5. exchange info、ping 等基础检查。

Binance WebSocket 用于：

1. 10s 实时价格监控。
2. 最新成交价事件。
3. 后续可能的实时行情事件。

禁止：

1. 用 REST 每 10 秒轮询最新价格。
2. 用 WebSocket 数据写正式 4h K线表。
3. 用第三方行情源替代 Binance 官方 K线。
4. 在业务模块中绕过 `BinanceRestClient` 手写 REST 请求。
5. 在脚本中直接拼 Binance URL 请求核心数据。

4h K线主线：

```text
Binance REST /fapi/v1/klines
    ↓
官方已收盘 4h K线
    ↓
MySQL market_kline_4h
```

10s 实时价格监控主线：

```text
Binance WebSocket aggTrade
    ↓
最新成交价
    ↓
Redis bitcoin_price
    ↓
Hermes 价格波动提醒
```

---

## 11. scripts 边界

`scripts/*.py` 只能作为 CLI 入口、检查入口或进程启动入口。

Codex 必须遵守：

1. 核心业务逻辑必须放在 `app/` 内对应 service。
2. `scripts/*.py` 只解析参数、初始化配置、调用 service。
3. `scripts/*.py` 不得绕过 service 直接请求 Binance。
4. `scripts/*.py` 不得绕过 repository 直接写业务表。
5. `scripts/*.py` 不得直接发送 Hermes。
6. `scripts/*.py` 不得承载复杂业务流程。
7. 脚本是否允许 scheduler 调用，必须由对应 plan 明确说明。
8. 如果脚本允许 scheduler 调用，必须显式传入 `--trigger-source scheduler`。
9. 如果脚本只允许人工 CLI 调用，必须拒绝 scheduler 触发。
10. 10s 价格监控脚本不应由 scheduler 每 10 秒反复拉起。

---

## 12. scheduler 边界

Codex 必须遵守：

1. scheduler 只能调用明确允许的 job 或 service。
2. scheduler job 应尽量薄，只负责触发 service。
3. scheduler 调用 K线采集任务时必须记录 `trigger_source = scheduler`。
4. scheduler 不得伪装成人工 CLI。
5. scheduler 不得直接写业务数据库。
6. scheduler 不得直接请求 Binance 核心数据。
7. scheduler 不得直接发送业务报警。
8. 4h K线采集不应每 10 秒执行。
9. 10s 价格监控不应由 scheduler 每 10 秒反复启动脚本。
10. 10s 价格监控应由常驻进程维护 WebSocket 连接。

如果某个 plan 允许 scheduler 调用 `scripts/*.py`，必须显式传入 `--trigger-source scheduler`，并在 implementation 中写清楚。

---

## 13. 模块边界

Codex 必须按职责放置代码。

1. `app/core`：配置、日志、异常、时间工具等基础能力。
2. `app/exchange/binance`：Binance 公开行情接口和基础连接检查。
3. `app/storage/mysql`：MySQL model、session、repository。
4. `app/storage/redis`：Redis 客户端和 Redis 状态读写。
5. `app/market_data`：行情数据采集、解析、校验、回补、复核、价格监控。
6. `app/alerting`：Hermes 报警业务层。
7. `app/scheduler`：定时任务入口和 job。
8. `scripts`：人工入口、检查入口或常驻进程启动入口。
9. `tests`：测试代码。
10. `docs/implementation`：模块实现说明。

禁止把多个模块职责混进一个大文件。

---

## 14. 不得重复实现

Codex 不得因为多个文档都提到同一能力，就在多个模块中重复实现同一套逻辑。

必须优先复用已有模块：

1. Hermes 发送逻辑只能通过统一 `app/alerting` 实现。
2. Binance REST 请求逻辑只能通过统一 `app/exchange/binance` client 实现。
3. K线 parser 必须复用统一 parser。
4. K线质量检查必须复用统一 quality checker。
5. MySQL 写入必须通过统一 repository。
6. Redis key 管理必须通过统一 Redis storage 模块。
7. UTC 与 PRC 转换必须通过统一时间工具。
8. 基础配置读取必须复用 `app/core/config.py`。
9. 日志必须复用 `app/core/logger.py`。
10. 异常类型应复用或扩展 `app/core/exceptions.py`。

---

## 15. 文件修改安全规则

Codex 修改文件时必须遵守：

1. 修改已有文件前，必须先读取现有内容。
2. 只做当前 plan 必要的最小修改。
3. 不得清空已有文件后重写。
4. 不得删除已有文档目录。
5. 不得删除已有代码目录。
6. 不得覆盖用户已有配置。
7. 不得提交 `.env`。
8. 不得提交真实日志。
9. 不得提交真实密钥。
10. 不确定是否可以覆盖时，必须停止并提示用户确认。

禁止执行类似危险操作：

1. 删除整个 `docs/` 后重建。
2. 删除整个 `app/` 后重建。
3. 用脚手架工具重置项目。
4. 大范围格式化无关文件。
5. 无说明修改大量无关模块。

---

## 16. 文件大小和单职责要求

Codex 编写代码时，必须优先保证可审阅、可维护、可测试。

原则：

1. 单个 Python 业务文件原则上不应超过 500 行。
2. 如果单个文件超过 800 行，Codex 必须说明为什么不能拆分。
3. 如果单个文件超过 1000 行，默认应拆分为多个职责清晰的文件。
4. 一个文件应只有一个主要职责。
5. 一个开发分支原则上只做一个模块。
6. 一个提交原则上只解决一个明确问题。

推荐拆分方式：

1. `client`：外部接口请求。
2. `parser`：数据解析。
3. `validator`：数据校验。
4. `repository`：数据库读写。
5. `service`：业务编排。
6. `dispatcher`：提醒分发。
7. `script`：命令行入口。
8. `job`：定时任务入口。
9. `test`：测试代码。

禁止为了方便把所有逻辑写进一个脚本。

---

## 17. 注释要求

Codex 编写或修改代码时，必须保证注释清晰，方便用户后期审阅代码和理解业务流程。

每个重要类、业务方法、脚本入口函数都必须有注释。

注释至少说明：

1. 该类或方法负责什么功能。
2. 关键参数是什么意思。
3. 返回值是什么。
4. 可能失败的场景是什么。
5. 是否会访问外部服务。
6. 是否会写入 MySQL。
7. 是否会写入 Redis。
8. 是否会发送 Hermes。
9. 是否会影响数据质量记录或告警记录。
10. 本方法明确不负责什么。

以下场景必须写注释：

1. K线连续性判断。
2. 已收盘 K线判断。
3. 数据质量失败时为什么停止写库。
4. Hermes 告警触发条件。
5. Redis 冷却和去重逻辑。
6. 数据库 upsert 逻辑。
7. UTC 与 PRC 时间转换。
8. 回补、复核、定时任务。
9. 未来策略建议、生命周期、复盘相关逻辑。

注释应解释业务意图和设计原因，不要只翻译代码表面动作。

错误示例：

```python
i = i + 1  # i 加 1
```

正确示例：

```python
# 当前 K线与上一根 K线 open_time_ms 相差必须等于 interval_ms，
# 否则说明中间存在缺口，不能继续写入正式 K线表。
```

---

## 18. 方法命名要求

方法名必须清晰表达用途。

禁止使用含糊命名，例如：

1. `handle()`
2. `process()`
3. `run()`
4. `do_task()`
5. `execute()`

除非该方法所在类、文件或脚本上下文已经非常明确。

优先使用明确命名，例如：

1. `fetch_closed_klines_from_binance()`
2. `validate_kline_continuity()`
3. `upsert_market_kline_4h()`
4. `send_kline_integrity_alert()`
5. `run_manual_backfill()`
6. `run_scheduled_incremental_collection()`
7. `record_collector_event_success()`
8. `record_collector_event_failure()`

方法名应尽量说明：

1. 动作是什么。
2. 操作对象是什么。
3. 是否会写入数据。
4. 是否是手动触发或定时触发。
5. 是否是校验、采集、回补、复核、报警或记录。

---

## 19. 新增核心文件顶部说明要求

每个新增核心文件必须在文件顶部说明职责。

至少包括：

1. 本文件属于哪个模块。
2. 本文件负责什么。
3. 本文件不负责什么。
4. 主要被谁调用。
5. 是否会访问外部服务。
6. 是否会读写数据库。
7. 是否会读写 Redis。
8. 是否会发送 Hermes。
9. 是否会调用 DeepSeek。
10. 是否涉及交易执行。

示例：

```text
本文件只负责 Binance REST 公开 K线接口请求。
本文件不负责 K线入库。
本文件不负责 K线连续性校验。
本文件不负责 Hermes 报警。
本文件不实现订单、账户、持仓、杠杆相关接口。
```

---

## 20. scripts 入口说明要求

每个 `scripts/*.py` 文件顶部必须说明：

1. 这个脚本由谁触发。
2. 是否允许用户手动执行。
3. 是否允许 scheduler 调用。
4. 必须传哪些参数。
5. 会调用 `app/` 中哪个 service。
6. 不负责哪些业务逻辑。
7. 是否会写数据库。
8. 是否会写 Redis。
9. 是否会发送 Hermes。
10. 是否允许修改正式 K线表。
11. 是否允许自动修复数据。
12. 是否允许自动交易。

如果脚本允许 scheduler 调用，必须明确写清楚：

1. scheduler 调用时必须传入什么参数。
2. `--trigger-source` 的允许值。
3. scheduler 触发和 CLI 触发分别写入什么 `trigger_source`。
4. scheduler 触发和 CLI 触发分别写入什么 `data_source`。
5. 哪些模式只允许 CLI，不允许 scheduler。

---

## 21. service 调用链说明要求

每个核心 service 文件顶部必须写明调用链。

调用链必须写到文件和方法级别，不能只写模块名称。

示例：

```text
用户 CLI
    ↓
scripts/backfill_4h_klines.py::main
    ↓
app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
    ↓
app/exchange/binance/client.py::get_klines
    ↓
app/market_data/kline_parser.py::parse_binance_klines
    ↓
app/market_data/kline_quality/service.py::check_batch_before_persist
    ↓
app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert
```

---

## 22. implementation 文档强制要求

每个阶段完成后，Codex 必须创建或更新对应文件：

```text
docs/implementation/<plan编号>_<模块名>.md
```

implementation 必须按功能拆分，而不是简单罗列文件。

每个功能必须说明：

1. 功能名称。
2. 发起入口。
3. 入口文件路径。
4. 入口方法名。
5. 核心 service 文件路径。
6. 核心 service 方法名。
7. 调用链路。
8. 读取哪些配置。
9. 请求哪些外部接口。
10. 读取哪些数据库表。
11. 写入哪些数据库表。
12. 读取或写入哪些 Redis key。
13. 是否发送 Hermes。
14. 是否调用 DeepSeek 或其他大模型。
15. 是否涉及 scheduler。
16. 是否涉及 scripts。
17. 是否涉及 `trigger_source`。
18. 是否涉及 `data_source`。
19. 异常如何处理。
20. 不负责哪些边界。
21. 对应测试文件。
22. 如何人工运行检查脚本。

如果某个功能没有外部接口、没有数据库、没有 Redis、没有 Hermes，也必须明确写：

```text
本功能不请求外部接口。
本功能不读取数据库。
本功能不写入数据库。
本功能不读取 Redis。
本功能不写入 Redis。
本功能不发送 Hermes。
```

不得省略。

如果只提交代码、不提交对应 implementation，视为任务未完成。

---

## 23. implementation 示例结构

实现说明文件应使用类似结构：

```md
# 08 4h K线手动回补实现说明

## 1. 功能：手动回补 4h K线

### 1.1 发起方式

用户手动执行：

    python -m scripts.backfill_4h_klines --trigger-source cli ...

### 1.2 入口文件

`scripts/backfill_4h_klines.py`

入口方法：

`main()`

### 1.3 核心调用链路

    scripts/backfill_4h_klines.py::main
        ↓
    app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
        ↓
    app/exchange/binance/client.py::get_klines
        ↓
    app/market_data/kline_parser.py::parse_binance_klines
        ↓
    app/market_data/kline_quality/service.py::check_batch_before_persist
        ↓
    app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert

### 1.4 数据来源

数据只能来自：

`Binance REST /fapi/v1/klines`

禁止：

- manual_repair
- human_edit
- manual_input
- system_repair

### 1.5 入库流程

说明写入哪些表、哪些字段、如何保证幂等、唯一键是什么、冲突如何处理。

### 1.6 异常处理

说明 Binance 请求失败、字段冲突、K线不连续、数据库写入失败时如何处理。

### 1.7 Hermes 报警

说明哪些异常会触发 Hermes，使用哪个固定模板，且不调用 DeepSeek。

### 1.8 本功能不负责

- 不自动修复 K线
- 不人工修改 K线
- 不自动下单
- 不调用 DeepSeek
```

---

## 24. 定时任务说明强制要求

凡是涉及 scheduler 的功能，implementation 必须写清楚：

1. 定时任务定义在哪个文件。
2. job 名称是什么。
3. job 调用哪个方法。
4. 是否调用 scripts。
5. 如果调用 scripts，传入什么 `--trigger-source`。
6. 实际写入的 `trigger_source` 是什么。
7. 实际写入的 `data_source` 是什么。
8. 失败是否报警。
9. 是否允许重试。
10. 是否会写数据库。
11. 是否会写 Redis。
12. 是否会发送 Hermes。
13. 是否会调用 DeepSeek。
14. 是否会修改正式 K线表。

不得只写“由定时任务触发”。

---

## 25. 数据流说明强制要求

凡是涉及数据写入，implementation 必须写清楚：

1. 数据从哪里来。
2. 经过哪些 parser。
3. 经过哪些 validator。
4. 经过哪些 service。
5. 经过哪些 repository。
6. 最终写入哪张表。
7. 写入哪些字段。
8. 唯一键是什么。
9. 幂等规则是什么。
10. 冲突如何处理。
11. 失败如何记录。
12. 是否触发 Hermes。
13. 是否允许自动修复。
14. 是否允许人工修改。
15. 是否允许覆盖正式数据。

---

## 26. 异常处理说明强制要求

凡是涉及外部请求、数据库、Redis、Hermes、scheduler、K线采集、回补、复核的功能，implementation 必须写清楚异常处理。

必须说明：

1. 异常发生在哪个文件。
2. 异常由哪个方法抛出。
3. 哪一层捕获异常。
4. 是否写入事件日志。
5. 是否发送 Hermes。
6. 是否重试。
7. 是否中断任务。
8. 是否允许 `partial_success`。
9. 是否会修改正式数据。
10. 是否会自动修复。

禁止只写：

```text
捕获异常并记录日志。
```

必须写清楚具体异常路径。

---

## 27. Hermes 报警规则

基础系统报警必须使用固定模板。

Codex 必须遵守：

1. Hermes 报警必须通过 `app/alerting` 统一发送。
2. K线采集异常、质量异常、系统异常、价格波动异常，不得调用 DeepSeek 生成报警内容。
3. `app/exchange/binance` 不得直接发送 Hermes。
4. `app/storage/mysql` 不得直接发送 Hermes。
5. `app/storage/redis` 不得直接发送 Hermes。
6. 底层 client 和 repository 只抛出异常或返回错误，由上层 service 决定是否报警。
7. 报警内容必须脱敏。
8. K线异常报警必须明确系统没有自动修复、没有人工改数、没有自动交易。
9. 价格波动报警必须明确这不是交易建议。
10. Hermes 发送失败不得导致修改正式 K线表。

implementation 中凡是涉及 Hermes，必须写清楚：

1. 由哪个 service 决定报警。
2. 调用 `app/alerting` 的哪个方法。
3. 使用哪个固定模板。
4. 发送什么严重级别。
5. 是否写入 `alert_message`。
6. 是否保存 `channel_response`。
7. `channel_response` 如何脱敏。
8. 失败如何处理。
9. 是否调用 DeepSeek。

---

## 28. DeepSeek 与大模型边界

当前第一批数据底座阶段不得调用 DeepSeek。

禁止在以下模块调用 DeepSeek 或其他大模型：

1. 基础系统报警。
2. K线采集。
3. K线回补。
4. K线复核。
5. K线质量检查。
6. 10s 价格监控。
7. Redis 状态维护。
8. MySQL repository。
9. Binance client。
10. scheduler job。

DeepSeek 后续只用于策略解释、建议分析、模型对比和复盘辅助。

DeepSeek 永远不得直接执行交易，永远不得直接写正式 K线。

---

## 29. 时间规则

Codex 必须遵守：

1. Binance 原始时间以 UTC 为准。
2. 业务排序以 UTC 时间或 Binance 毫秒时间戳为准。
3. K线连续性判断以 `open_time_ms` 为准。
4. 4h K线相邻 open time 差值必须是 `14400000` 毫秒。
5. PRC 时间只用于用户阅读、排查和展示。
6. PRC 时间不得作为业务排序依据。
7. PRC 时间不得作为 K线连续性判断依据。
8. 程序中不得到处手写 `+ timedelta(hours=8)`。
9. UTC 转 PRC 必须统一调用 `app/core/time_utils.py` 中的辅助函数。
10. 数据库中保留 PRC 时间字段时，必须明确它只是展示辅助字段。

---

## 30. 安全与密钥规则

禁止提交：

1. `.env`
2. 真实数据库密码。
3. 真实 Redis 密码。
4. Hermes webhook。
5. Hermes secret。
6. Binance API key。
7. Binance secret key。
8. token。
9. Authorization。
10. cookie。
11. 生产日志。
12. 账户、订单、持仓敏感信息。

日志中禁止打印：

1. 完整 `.env`。
2. 密码。
3. secret。
4. token。
5. webhook。
6. Authorization。
7. cookie。
8. 账户敏感信息。
9. 持仓信息。
10. 交易信息。

---

## 31. 测试要求

默认测试必须安全。

Codex 必须遵守：

1. 默认 `pytest` 不请求真实 Binance。
2. 默认 `pytest` 不连接真实 MySQL。
3. 默认 `pytest` 不连接真实 Redis。
4. 默认 `pytest` 不发送真实 Hermes。
5. 默认 `pytest` 不调用 DeepSeek。
6. 默认 `pytest` 不访问交易接口。
7. 真实集成测试必须使用显式开关。
8. mock 测试必须覆盖关键边界。
9. 测试必须覆盖禁止项，例如不写 K线表、不调用 DeepSeek、不使用交易接口。
10. 每个 plan 必须有对应测试或检查脚本。

implementation 必须写清楚：

1. 对应测试文件。
2. 测试覆盖哪些功能。
3. 哪些测试是 mock。
4. 哪些测试需要真实外部服务。
5. 默认 pytest 是否访问外部服务。
6. 如何开启集成测试。
7. 人工检查脚本如何执行。

---

## 32. 每次交付回复要求

Codex 每完成一个任务，必须在回复或 implementation 中列出：

1. 本次修改了哪些文件。
2. 每个文件负责什么。
3. 本次新增了哪些入口。
4. 本次核心调用链路是什么。
5. 本次运行了哪些检查或测试。
6. 本次没有实现哪些内容。
7. 是否存在风险或待确认事项。
8. 是否违反 `docs/rules/project_invariants.md`。

如果本次修改超过 500 行，Codex 必须说明修改集中在哪里，以及用户应该重点审查哪些文件和哪些方法。

---

## 33. Codex 自检输出格式

Codex 每次完成一个 plan 后，必须输出类似自检结果：

```text
本次是否违反 docs/rules/project_invariants.md：
- 自动交易：未违反
- K线数据来源：未违反
- manual_repair / human_edit / manual_input / system_repair：未违反
- REST / WebSocket 边界：未违反
- trigger_source / data_source：未违反
- scripts 边界：未违反
- scheduler 边界：未违反
- DeepSeek 调用边界：未违反
- Hermes 固定模板报警边界：未违反
- MySQL / Redis 边界：未违反
- 敏感信息提交：未违反

本次修改文件：
1. ...

本次运行检查：
1. ...

本次明确没有实现：
1. ...

待用户确认：
1. ...
```

不得只回复“已完成”。

---

## 34. 人工审查辅助要求

每个 plan 的实现应支持用户用 grep 或脚本检查危险项。

Codex 应避免引入以下危险关键词，除非是文档中的禁止说明或测试中的拒绝场景：

```text
manual_repair
human_edit
manual_input
system_repair
order
position
leverage
account
listenKey
ticker/price
/fapi/v1/ticker
DeepSeek
openai
```

如果代码中必须出现某个危险词，必须在 implementation 中解释为什么出现，以及为什么不违规。

---

## 35. 禁止偷懒

Codex 不得只写：

```text
实现了 K线回补功能。
实现了报警功能。
实现了定时任务。
实现了数据库写入。
```

这种说明不合格。

必须写清楚：

1. 谁触发。
2. 哪个文件。
3. 哪个方法。
4. 调用谁。
5. 读什么。
6. 写什么。
7. 请求什么。
8. 失败怎么办。
9. 哪些事情不做。
10. 如何测试。
11. 如何人工检查。

如果 implementation 不能让用户根据文档完整追踪一次功能运行过程，则视为交付不合格。
