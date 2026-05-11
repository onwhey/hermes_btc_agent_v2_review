# 09 4h Kline Incremental Collector Plan

## 1. 阶段目标

本阶段实现 BTCUSDT 4h K线增量采集能力。

本阶段负责从 Binance REST 官方接口拉取最近若干根 4h K线，过滤未收盘 K线，检查与数据库已有 K线的连续性和一致性，并将合格的新 K线幂等写入 `market_kline_4h` 正式 K线表。

本阶段支持两种触发方式：

1. scheduler 定时触发。
2. 用户 CLI 手动触发。

两种触发方式必须通过 `trigger_source` 明确区分。

本阶段负责：

1. 创建 4h K线增量采集 service。
2. 创建 4h K线采集脚本入口。
3. 创建 scheduler job 调用入口。
4. 从 Binance REST 拉取最近若干根 4h K线。
5. 使用重叠拉取方式校验连续性。
6. 过滤未收盘 K线。
7. 调用 06 阶段 parser 转换为内部 DTO。
8. 调用 07 阶段质量检查模块进行写库前检查。
9. 记录 `collector_event_log`。
10. 将通过质量检查的新 K线幂等写入 `market_kline_4h`。
11. 发现断档、批次不连续、数据库接不上、质量失败、blocked、failed、写入失败、任务异常或无法确认采集健康状态时，必须通过 `app/alerting` 发送 Hermes 固定模板报警。
12. 创建测试文件。
13. 创建对应实现说明文件。

## 2. 本阶段明确不做

本阶段不得实现策略分析、价格监控或交易功能。

禁止实现：

1. 手动历史大范围回补流程。
2. 每日 K线复核任务。
3. 10s 价格监控。
4. WebSocket。
5. Redis 写入 `bitcoin_price`。
6. DeepSeek 或其他大模型调用。
7. 策略分析。
8. 交易建议。
9. 自动下单、自动平仓、自动调仓。
10. Binance 账户、订单、持仓、杠杆、保证金相关接口。
11. 人工直接修改 K线字段。
12. manual_repair。
13. human_edit。
14. manual_input。
15. system_repair。
16. 自动修复 K线。
17. 静默覆盖冲突 K线。

如果 Codex 在本阶段添加以上功能，应视为越界。

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/requirements/01_project_scope.md`
2. `docs/requirements/02_data_collection_requirements.md`
3. `docs/requirements/03_database_and_quality_requirements.md`
4. `docs/requirements/04_alerting_requirements.md`
5. `docs/architecture/system_architecture.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/architecture/data_flow.md`
8. `docs/decisions/0001-no-auto-trading.md`
9. `docs/decisions/0002-kline-source-and-time-rules.md`
10. `docs/decisions/0003-kline-table-splitting.md`
11. `docs/decisions/0004-alerting-through-hermes.md`
12. `docs/plans/01_project_skeleton.md`
13. `docs/plans/02_core_config_logging.md`
14. `docs/plans/03_infra_mysql_redis.md`
15. `docs/plans/04_alerting_through_hermes.md`
16. `docs/plans/05_binance_rest_client.md`
17. `docs/plans/06_market_kline_4h.md`
18. `docs/plans/07_kline_quality_checker.md`
19. `docs/plans/08_4h_backfill.md`
20. `docs/implementation/01_project_skeleton.md`
21. `docs/implementation/02_core_config_logging.md`
22. `docs/implementation/03_infra_mysql_redis.md`
23. `docs/implementation/04_alerting_through_hermes.md`
24. `docs/implementation/05_binance_rest_client.md`
25. `docs/implementation/06_market_kline_4h.md`
26. `docs/implementation/07_kline_quality_checker.md`
27. `docs/implementation/08_4h_kline_manual_backfill.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`
5. `app/storage/mysql/session.py`
6. `app/exchange/binance/client.py`
7. `app/market_data/kline_parser.py`
8. `app/market_data/kline_validator.py`
9. `app/market_data/kline_quality/`
10. `app/storage/mysql/repositories/market_kline_4h_repository.py`
11. `app/storage/mysql/repositories/data_quality_check_repository.py`
12. `app/storage/mysql/repositories/collector_event_log_repository.py`
13. `app/alerting`

不得重复实现 Binance REST 请求、K线 parser、质量检查、Repository、Hermes 报警逻辑。

## 4. 建议分支

建议分支名：

`feature/09-4h-kline-incremental-collector`

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
app/market_data/
app/market_data/collector/
app/scheduler/
app/scheduler/jobs/
scripts/
tests/
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
3. 删除 `app/market_data/` 后重建。
4. 删除 `app/scheduler/` 后重建。
5. 覆盖已有配置、日志、数据库、报警、Binance REST、K线基础模块、质量检查模块、回补模块。
6. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/market_data/collector/__init__.py
app/market_data/collector/kline_4h_collector_service.py
app/market_data/collector/types.py

app/scheduler/__init__.py
app/scheduler/jobs/__init__.py
app/scheduler/jobs/collect_4h_klines_job.py

scripts/collect_4h_klines.py

tests/test_4h_kline_incremental_collector.py
docs/implementation/09_4h_kline_incremental_collector.md
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. 增量采集模块定位

增量采集模块路径：

`app/market_data/collector/`

该模块负责：

1. 获取数据库中最新一根 4h K线。
2. 根据最新 K线计算需要拉取的最近 K线范围。
3. 通过 Binance REST 拉取最近若干根官方 4h K线。
4. 使用重叠拉取检查已有数据是否一致。
5. 过滤未收盘 K线。
6. 解析 Binance raw kline。
7. 执行质量检查。
8. 记录 `collector_event_log`。
9. 幂等写入新的合格 K线。
10. 发现断档、批次不连续、数据库接不上、质量失败、blocked、failed、写入失败、任务异常或无法确认采集健康状态时，必须通过 `app/alerting` 发送 Hermes 固定模板报警。

该模块不负责：

1. 大范围历史回补。
2. 每日完整性复核。
3. 10s 价格监控。
4. WebSocket。
5. Redis 价格状态。
6. 策略分析。
7. DeepSeek 分析。
8. 交易建议。
9. 自动交易。
10. 人工修改 K线。
11. 自动修复 K线。

## 8. 触发方式要求

本阶段允许两种触发方式：

### 8.1 scheduler 触发

允许 scheduler 定时触发增量采集。

推荐调用方式：

```
app/scheduler/jobs/collect_4h_klines_job.py
    ↓
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection(trigger_source="scheduler")
```

也允许 scheduler 通过脚本触发，但必须显式传参：

```
python -m scripts.collect_4h_klines --trigger-source scheduler
```

无论使用哪种方式，都必须记录：

```
trigger_source = scheduler
data_source = binance_rest_by_scheduler
```

### 8.2 CLI 手动触发

允许用户手动执行：

```
python -m scripts.collect_4h_klines --trigger-source cli
```

CLI 手动触发时必须记录：

```
trigger_source = cli
data_source = binance_rest_by_cli
```

### 8.3 禁止事项

禁止：

1. 缺少 `--trigger-source` 仍执行。
2. 自动猜测触发来源。
3. scheduler 触发却记录为 `cli`。
4. CLI 触发却记录为 `scheduler`。
5. 根据是否经过 `scripts/*.py` 猜测 `data_source`。
6. 把手动增量采集伪装成自动采集。
7. 把自动采集伪装成手动采集。

## 9. CLI 入口要求

建议文件：

`scripts/collect_4h_klines.py`

该脚本是 4h K线增量采集入口，可由用户手动执行，也可由 scheduler 显式带参数调用。

允许：

```
python -m scripts.collect_4h_klines --trigger-source cli
```

允许：

```
python -m scripts.collect_4h_klines --trigger-source scheduler
```

脚本要求：

1. 必须要求传入 `--trigger-source`。
2. 允许值只有 `cli` 和 `scheduler`。
3. 缺少 `--trigger-source` 必须拒绝执行。
4. 非法 `--trigger-source` 必须拒绝执行。
5. 脚本只负责解析参数、初始化配置和调用 service。
6. 脚本不得直接请求 Binance。
7. 脚本不得直接写数据库。
8. 脚本不得直接发送 Hermes。
9. 脚本不得承载核心业务逻辑。

文件顶部必须写清楚：

1. 这是 4h 增量采集入口。
2. 支持 CLI 手动触发。
3. 支持 scheduler 显式带 `--trigger-source scheduler` 触发。
4. 数据只能来自 Binance REST 官方接口。
5. `trigger_source` 决定实际写入的 `data_source`。
6. 不允许 manual_repair。
7. 不允许人工修改正式 K线表。
8. 不允许自动修复。
9. 不允许自动下单。

## 10. Scheduler job 要求

建议文件：

`app/scheduler/jobs/collect_4h_klines_job.py`

职责：

1. 定义 4h K线增量采集 job 的入口方法。
2. 调用 collector service。
3. 明确传入 `trigger_source="scheduler"`。
4. 不直接请求 Binance。
5. 不直接写数据库。
6. 不直接发送 Hermes。
7. 不承载核心采集业务逻辑。

建议方法：

```
run_collect_4h_klines_job()
```

调用链：

```
scheduler
    ↓
app/scheduler/jobs/collect_4h_klines_job.py::run_collect_4h_klines_job
    ↓
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection(trigger_source="scheduler")
```

要求：

1. job 方法必须显式传入 `trigger_source="scheduler"`。
2. 不允许 job 内部自动猜测触发来源。
3. 不允许 job 绕过 service 直接写库。
4. 不允许 job 调用手动回补 service。
5. 不允许 job 调用 DeepSeek。
6. 不允许 job 执行交易。

## 11. 增量采集 Service 要求

建议文件：

`app/market_data/collector/kline_4h_collector_service.py`

建议方法：

1. `run_incremental_4h_collection(trigger_source)`
2. `validate_collection_trigger_source(trigger_source)`
3. `resolve_collection_data_source(trigger_source)`
4. `get_latest_db_kline(session, symbol, interval_value)`
5. `build_recent_kline_request(latest_db_kline, config)`
6. `fetch_recent_raw_klines(request)`
7. `filter_closed_klines(raw_klines, binance_server_time_ms)`
8. `parse_collected_klines(raw_klines, symbol, interval_value, trigger_source)`
9. `select_new_klines_for_persist(session, parsed_klines)`
10. `check_collection_quality(session, parsed_klines, new_klines)`
11. `persist_collected_klines(session, new_klines)`
12. `record_collection_event_running(session, request)`
13. `record_collection_event_success(session, event_id, result)`
14. `record_collection_event_failed(session, event_id, error)`
15. `record_collection_event_blocked(session, event_id, report)`
16. `send_collection_alert_if_needed(error_or_report)`

职责：

1. 控制完整增量采集流程。
2. 创建 `collector_event_log`。
3. 请求 Binance REST。
4. 过滤未收盘 K线。
5. 调用 parser。
6. 调用质量检查。
7. 写入新 K线。
8. 处理异常。
9. 断档、批次不连续、数据库接不上、质量失败、blocked、failed、写入失败、任务异常或无法确认采集健康状态时，必须发送 Hermes 固定模板报警。

禁止：

1. 自动修复 K线。
2. 自动覆盖冲突 K线。
3. 修改已有正式 K线。
4. 删除已有正式 K线。
5. 调用 DeepSeek。
6. 生成交易建议。
7. 执行交易。
8. 实现价格监控。

## 12. K线写入任务锁要求

增量采集会写入正式 K线表，因此启动前必须先获取同一 `symbol + interval` 的 K线写入任务锁。

推荐锁 key：

```text
kline_write:BTCUSDT:4h
```

推荐 owner：

```text
trace_id
```

要求：

1. 获取锁必须具备原子性，例如 Redis `SET key value NX EX seconds`。
2. 锁必须设置 TTL，避免进程异常退出后永久阻塞。
3. 如果锁已存在，本次增量采集必须拒绝或跳过，并记录 `collector_event_log.status = skipped`。
4. 锁存在时不得继续请求 Binance、不得继续质量检查、不得继续写正式 K线表。
5. 释放锁时必须校验 owner，只能释放当前任务自己持有的锁。
6. 不得只依赖 `collector_event_log.status = running` 判断是否并发。
7. 增量采集和手动回补必须共用同一 `symbol + interval` 写入锁，避免互相并发写入。

标准锁流程：

```text
生成 trace_id
    ↓
尝试获取 kline_write:BTCUSDT:4h 锁
    ↓
获取失败：记录 collector_event_log = skipped，并退出
    ↓
获取成功：创建 collector_event_log = running
    ↓
执行 Binance REST、解析、质量检查、幂等写库
    ↓
更新 collector_event_log = success / blocked / failed
    ↓
finally 校验 owner 后释放锁
```

## 13. 标准调用链

### 13.1 scheduler 触发调用链

```
scheduler
    ↓
app/scheduler/jobs/collect_4h_klines_job.py::run_collect_4h_klines_job
    ↓
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection(trigger_source="scheduler")
    ↓
app/core/task_lock.py 获取 kline_write:BTCUSDT:4h 锁
    ↓
collector_event_log_repository.create_running_event
    ↓
MarketKline4hRepository.get_latest
    ↓
BinanceRestClient.get_server_time
    ↓
BinanceRestClient.get_klines
    ↓
filter_closed_klines
    ↓
parse_binance_klines
    ↓
check_batch_before_persist
    ↓
check_against_database
    ↓
data_quality_check_repository.create_check_record
    ↓
MarketKline4hRepository.bulk_upsert
    ↓
collector_event_log_repository.mark_success
    ↓
返回采集结果
```

### 13.2 CLI 触发调用链

```
用户 CLI
    ↓
scripts/collect_4h_klines.py::main
    ↓
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection(trigger_source="cli")
    ↓
后续流程与 scheduler 相同
```

### 13.3 异常链路

```
任意步骤失败
    ↓
回滚当前事务中未提交写入
    ↓
collector_event_log_repository.mark_failed 或 mark_blocked
    ↓
必须通过 app/alerting 发送 Hermes 固定模板报警
    ↓
CLI 返回非 0 状态码，scheduler job 记录失败
```

## 14. 重叠拉取要求

增量采集不得只拉最新一根 K线。

必须采用“最近若干根 + 重叠校验”的方式。

示例：

数据库已有：

```
04:00
08:00
```

当 Binance REST 当前可获得：

```
04:00
08:00
12:00
```

系统应拉取最近若干根，例如：

```
04:00
08:00
12:00
```

然后：

1. 校验 04:00、08:00 与数据库已存数据一致。
2. 跳过已存在且一致的 04:00、08:00。
3. 写入新的 12:00。
4. 不得重复写入 04:00、08:00。
5. 不得只拉 12:00 而失去连续性校验能力。

如果上一次 12:00 采集失败，到了 16:00 时 REST 返回：

```
04:00
08:00
12:00
16:00
```

系统应：

1. 校验 04:00、08:00 与数据库一致。
2. 发现 12:00、16:00 是缺失的新 K线。
3. 在质量检查通过后写入 12:00 和 16:00。
4. 不得因为 04:00、08:00 已存在就整体跳过。
5. 不得只写 16:00 导致 12:00 永久缺失。

## 15. 拉取数量要求

建议配置项：

```
KLINE_4H_COLLECT_RECENT_LIMIT=10
KLINE_4H_COLLECT_MIN_OVERLAP=2
KLINE_4H_COLLECT_MAX_LIMIT=100
```

默认建议：

```
最近拉取 10 根 4h K线
```

原因：

1. 覆盖最近失败的采集窗口。
2. 允许校验数据库已有 K线和 Binance 官方数据是否一致。
3. 防止单次失败导致漏写。
4. 避免每次拉取过大范围造成无意义请求。

要求：

1. `recent_limit` 必须大于等于 `min_overlap + 1`。
2. `recent_limit` 不得超过配置上限。
3. 不得无限制拉取。
4. 不得每次全量拉取历史数据。
5. 大范围历史补齐应使用 08 手动回补，不应放在 09 增量采集。

## 16. Binance REST 请求要求

本阶段只能通过：

`BinanceRestClient.get_klines()`

请求 Binance K线。

允许调用：

1. `BinanceRestClient.get_server_time()`：用于未收盘过滤。
2. `BinanceRestClient.get_klines()`：用于获取官方 K线。

禁止：

1. 在 collector service 中手写 Binance URL。
2. 在脚本中手写 Binance URL。
3. 直接使用 requests/httpx 绕过 `BinanceRestClient`。
4. 请求 REST 最新价格。
5. 使用 WebSocket。
6. 使用第三方行情源。
7. 使用人工输入数据作为 K线源。

禁止调用：

1. order endpoint。
2. account endpoint。
3. position endpoint。
4. leverage endpoint。
5. margin endpoint。
6. listenKey。
7. ticker price endpoint。

## 17. 未收盘过滤要求

增量采集必须过滤未收盘 K线。

判断依据：

1. 优先使用 `BinanceRestClient.get_server_time()` 获取 Binance server time。
2. 如果 K线 `close_time_ms >= server_time_ms`，视为未收盘。
3. 未收盘 K线不得写入 `market_kline_4h`。
4. 如果请求结果中包含未收盘 K线，应记录为 `filtered_unclosed_count`。
5. 如果全部都是未收盘 K线，应阻断写入或返回“无可写入已收盘 K线”的明确结果。

禁止：

1. 使用本机时间作为唯一判断依据。
2. 未获取 Binance server time 仍继续写入。
3. 把未收盘 K线写入正式表。
4. 自动修复未收盘 K线。
5. 后续再覆盖未收盘 K线。

## 18. Parser 要求

本阶段必须复用 06 阶段：

`app/market_data/kline_parser.py`

要求：

1. 使用 `parse_binance_klines()` 转换 raw kline。
2. 传入真实 `trigger_source`。
3. parser 根据 `trigger_source` 生成正确 `data_source`。
4. `trigger_source = scheduler` 时，生成 `data_source = binance_rest_by_scheduler`。
5. `trigger_source = cli` 时，生成 `data_source = binance_rest_by_cli`。
6. parser 生成 `raw_payload_json`。
7. parser 生成 `raw_payload_hash`。
8. parser 使用 UTC 时间作为业务时间。
9. parser 使用 PRC 时间作为展示辅助字段。

禁止：

1. 在 collector service 中重复实现 raw kline 解析。
2. 手工拼 DTO 绕过 parser。
3. 手工写 PRC 时间转换。
4. 使用 `+ timedelta(hours=8)` 代替 `app/core/time_utils.py`。

## 19. 质量检查要求

正式写入 `market_kline_4h` 前，必须通过 07 阶段质量检查。

至少执行：

1. 单根基础字段校验。
2. 批次排序检查。
3. 批次重复 open_time 检查。
4. 批次连续性检查。
5. 未收盘检查。
6. data_source 与 trigger_source 映射检查。
7. 与数据库已有 K线冲突检查。
8. 与数据库最新 K线连续性检查。

检查失败时：

1. 不写入 `market_kline_4h`。
2. 写入 `data_quality_check`。
3. 更新 `collector_event_log` 为 `blocked` 或 `failed`。
4. 必须发送 Hermes 固定模板报警。
5. CLI 返回非 0 状态码。
6. scheduler job 记录失败。

禁止：

1. 质量检查失败后仍强行写入。
2. 质量检查失败后自动修复。
3. 质量检查失败后自动回补更多历史范围。
4. 静默跳过异常 K线后继续写入。
5. 调用 DeepSeek 判断是否可以写入。

## 20. 正式 K线写入要求

正式 K线写入必须通过：

`MarketKline4hRepository.bulk_upsert()`

要求：

1. 只写入通过质量检查的已收盘 K线。
2. 基于唯一键 `symbol + interval_value + open_time_ms` 幂等写入。
3. 已存在且字段一致的 K线应跳过。
4. 不存在的 K线可以插入。
5. 已存在但字段冲突时必须阻断。
6. 不得静默覆盖。
7. 不得删除旧数据。
8. 不得修改旧数据。
9. 不得人工修复。
10. 不得自动修复。

采集结果必须统计：

1. fetched_count
2. parsed_count
3. closed_count
4. inserted_count
5. skipped_count
6. conflict_count
7. filtered_unclosed_count

## 21. collector_event_log 要求

本阶段复用 08 阶段创建的：

`collector_event_log`

本阶段 event_type 建议为：

```
incremental_collect_4h
```

scheduler 触发时：

```
trigger_source = scheduler
data_source = binance_rest_by_scheduler
```

CLI 触发时：

```
trigger_source = cli
data_source = binance_rest_by_cli
```

必须记录：

1. event_type。
2. symbol。
3. interval_value。
4. trigger_source。
5. data_source。
6. status。
7. requested_count。
8. fetched_count。
9. parsed_count。
10. closed_count。
11. inserted_count。
12. skipped_count。
13. conflict_count。
14. filtered_unclosed_count。
15. actual_start_open_time_ms。
16. actual_end_open_time_ms。
17. quality_check_id。
18. alert_message_id。
19. error_code。
20. error_message。
21. started_at_utc / started_at_prc。
22. finished_at_utc / finished_at_prc。

状态规则：

1. `running`：任务开始。
2. `success`：任务完成，无阻断级异常。
3. `blocked`：质量检查阻断。
4. `failed`：请求、解析、数据库或未预期异常。
5. `partial_success`：本阶段原则上不应出现。
6. `skipped`：任务锁已存在，本次任务跳过或拒绝。

## 22. Event 状态规则

### running

创建时机：

1. `trigger_source` 校验通过后。
2. 请求 Binance 前。

### success

条件：

1. Binance 请求成功。
2. server time 获取成功。
3. parser 成功。
4. 未收盘过滤完成。
5. 质量检查通过。
6. 新 K线写入成功，或全部已存在且一致。
7. 无阻断级异常。

注意：

如果没有新 K线，但已有重叠 K线校验一致，可以记录为 success，并说明 inserted_count = 0。

### blocked

常见原因：

1. 批次不连续。
2. 存在字段冲突。
3. data_source 映射异常。
4. 全部 K线未收盘。
5. 与数据库最新 K线不连续。
6. 数据库已有 K线与 Binance 官方数据不一致。

blocked 表示系统按规则拒绝写入，不是程序崩溃。

### failed

常见原因：

1. Binance 请求失败。
2. server time 获取失败。
3. parser 异常。
4. 数据库异常。
5. alerting 异常导致主流程无法完成。
6. 未预期异常。

### partial_success

本阶段原则上不应出现。

要求：

1. 增量采集应优先采用事务避免 partial_success。
2. 如果出现 partial_success，implementation 必须写明原因。
3. 不得把 partial_success 当作普通成功。

## 23. 事务要求

本阶段必须明确事务边界。

建议：

1. `trigger_source` 参数校验在事务外完成。
2. Binance REST 请求在事务外完成。
3. parser 可在事务外完成。
4. 质量检查可在明确事务中读取数据库。
5. 正式 K线写入、`data_quality_check`、`collector_event_log` 状态更新应在明确事务中完成。
6. 如果正式 K线写入失败，应回滚本次正式 K线写入。
7. 如果 quality check 失败，不进入正式 K线写入。
8. 不得出现静默部分提交。

implementation 必须写清楚：

1. 哪些操作在同一事务。
2. 哪些操作先提交。
3. 失败时如何保证可追踪。
4. 是否可能出现 event log 存在但 K线未写入。
5. 是否可能出现 K线写入但 event log 更新失败。

## 24. 幂等要求

增量采集必须支持幂等。

规则：

1. 同一时间窗口重复采集，不得插入重复 K线。
2. 已存在且字段一致的 K线应跳过或计入 skipped。
3. 已存在但字段不一致，必须阻断。
4. 不得覆盖已有字段。
5. 不得删除已有字段。
6. 不得人工修复已有字段。
7. 重复运行应能通过 event log 看出每次运行结果。

## 25. 初次运行要求

如果数据库中没有任何 `market_kline_4h` 数据，本阶段不得自动拉取无限历史。

初次运行可采用以下策略之一：

### 推荐策略

要求用户先执行 08 手动回补，建立基础历史数据。

如果数据库为空，09 增量采集应：

1. 记录 `collector_event_log = blocked`。
2. 返回明确错误。
3. 提示用户先执行手动回补。
4. 必须发送 Hermes 固定模板报警。
5. 不自动猜测历史起点。

### 可选策略

如果后续明确允许，也可以从最近固定数量 K线初始化。

但本阶段不建议默认这样做。

禁止：

1. 数据库为空时自动全量回补。
2. 数据库为空时自动拉一年历史。
3. 数据库为空时用本机时间随便推算起点。
4. 数据库为空时无记录地静默退出。

## 26. Hermes 报警要求

本阶段必须继承 07 K线质量报警新规则：断档、批次不连续、数据库接不上、写入失败、任务异常、blocked、failed、无法确认健康状态时必须通过 `app/alerting` 发送 Hermes 固定模板报警。

必须报警场景：

1. Binance REST 请求失败。
2. Binance server time 获取失败。
3. 增量采集结果为空。
4. 数据库为空且不能增量采集。
5. 质量检查失败。
6. 质量检查返回 blocked。
7. 数据库字段冲突。
8. 新数据与数据库最新 K线接不上。
9. Binance REST 返回批次不连续。
10. K线不连续或断档。
11. 未收盘 K线被误写风险。
12. 正式 K线写入失败。
13. collector_event_log 写入失败。
14. 任务状态 failed。
15. 任务异常导致无法确认采集健康状态。
16. 未预期异常。

失败报警不得由 CLI 参数控制。

报警模板必须使用固定模板。

建议模板类型：

```
collector_failed
kline_data_quality_error
```

报警内容必须说明：

1. event_type。
2. symbol。
3. interval。
4. trigger_source。
5. data_source。
6. 拉取数量。
7. 已收盘数量。
8. 写入数量。
9. 跳过数量。
10. 异常类型。
11. 首个质量问题。
12. 明确系统没有自动修复。
13. 明确系统没有自动回补大范围历史数据。
14. 明确系统没有人工修改 K线。
15. 建议用户检查 Binance REST、采集代码、数据库写入、scheduler 配置。

禁止：

1. 调用 DeepSeek 生成报警。
2. 调用其他大模型生成报警。
3. 生成交易建议。
4. 自动下单。
5. 在 `app/exchange/binance` 中直接报警。
6. 在 `app/storage/mysql` 中直接报警。

## 27. 配置要求

建议新增配置：

```
KLINE_4H_COLLECT_SYMBOL=BTCUSDT
KLINE_4H_COLLECT_INTERVAL=4h
KLINE_4H_COLLECT_RECENT_LIMIT=10
KLINE_4H_COLLECT_MIN_OVERLAP=2
KLINE_4H_COLLECT_MAX_LIMIT=100
KLINE_4H_COLLECT_NOTIFY_SUCCESS=false
```

`KLINE_4H_COLLECT_NOTIFY_SUCCESS` 只控制采集成功通知，不得用于控制失败报警；断档、批次不连续、数据库接不上、质量失败、blocked、failed、写入失败、任务异常或无法确认采集健康状态时，必须发送 Hermes 固定模板报警。

如果 `.env.example` 已存在，只补齐缺失项，不得清空重写。

禁止：

1. 写入真实密钥。
2. 写入 Binance API key。
3. 写入 Binance secret key。
4. 写入账户信息。
5. 写入交易权限配置。

## 28. Scheduler 频率要求

4h K线采集不应刚好卡在 4h 整点立刻执行。

建议：

1. 每个 4h 周期结束后延迟数分钟执行。
2. 例如 UTC 00:05、04:05、08:05、12:05、16:05、20:05。
3. 具体调度表达式可以在 scheduler 实现中配置。
4. 不应每分钟频繁拉取 4h K线。
5. 不应每 10 秒拉取 4h K线。

如果本阶段只实现 job 方法，不实现完整 scheduler runtime，也必须在 implementation 写清楚：

1. job 方法在哪。
2. 期望调度频率是什么。
3. 未来如何接入 scheduler。
4. 当前是否已经真的创建定时任务。

## 29. CLI 参数要求

`scripts/collect_4h_klines.py` 建议支持参数：

```
--symbol BTCUSDT
--interval 4h
--recent-limit 10
--trigger-source cli|scheduler
```

规则：

1. `--symbol` 默认 `BTCUSDT`。
2. `--interval` 默认 `4h`。
3. `--recent-limit` 默认读取配置。
4. `--trigger-source` 必填。
5. `--trigger-source` 只允许 `cli` 或 `scheduler`。
6. 不允许使用 CLI 参数控制失败报警；增量采集失败报警必须按 07 新规则强制执行。

禁止：

1. 支持大范围 start/end 回补参数。
2. 支持人工输入 K线字段。
3. 缺少 trigger_source 仍执行。
4. 非法 trigger_source 仍执行。

说明：

如果用户需要按指定历史范围补数据，应使用 08 的手动回补脚本，不应使用 09 增量采集脚本。

## 30. 与 08 手动回补的边界

08 手动回补：

```
用户指定历史范围
    ↓
CLI 手动触发
    ↓
trigger_source = cli
    ↓
data_source = binance_rest_by_cli
```

09 增量采集：

```
scheduler 或 CLI 触发
    ↓
拉取最近若干根
    ↓
根据 trigger_source 记录 data_source
    ↓
写入新增的已收盘 K线
```

区别：

1. 08 面向历史范围。
2. 09 面向最近增量。
3. 08 不允许 scheduler。
4. 09 允许 scheduler，但必须显式记录 `trigger_source = scheduler`。
5. 08 和 09 都不允许人工改数。
6. 08 和 09 都必须使用 Binance REST 官方 K线。

## 31. 数据库影响

本阶段允许：

1. 读取 `market_kline_4h`。
2. 在质量检查通过后写入 `market_kline_4h`。
3. 写入 `data_quality_check`。
4. 写入 `collector_event_log`。

本阶段禁止：

1. 创建新表，除非前序阶段缺失且 plan 明确允许补齐。
2. 修改 `market_kline_4h` 表结构。
3. 删除 `market_kline_4h`。
4. 静默覆盖冲突 K线。
5. 自动修复 K线。
6. 自动执行 migration。
7. 创建策略表。
8. 创建建议表。
9. 人工修复 K线字段。

## 32. Redis 影响

本阶段允许连接 Redis，但仅允许用于 K线写入任务锁。

允许使用的锁 key 示例：

```text
kline_write:BTCUSDT:4h
```

允许操作：

1. 获取任务锁。
2. 查询任务锁。
3. 校验 owner 后释放任务锁。

本阶段不得创建或写入：

```text
bitcoin_price
```

本阶段不得把 Redis 用作行情缓存、K线存储或长期审计数据源。

价格监控和 `bitcoin_price` 写入应在后续 WebSocket 价格监控阶段实现。

## 33. Binance 影响

本阶段允许调用：

1. `BinanceRestClient.get_server_time()`
2. `BinanceRestClient.get_klines()`

禁止调用：

1. REST 最新价格接口。
2. WebSocket。
3. order endpoint。
4. account endpoint。
5. position endpoint。
6. leverage endpoint。
7. margin endpoint。
8. listenKey。

## 34. Hermes 影响

本阶段遇到断档、批次不连续、数据库接不上、质量失败、blocked、failed、写入失败、任务异常或无法确认采集健康状态时，必须调用 Hermes 固定模板报警；失败报警不得由 CLI 参数或配置项控制。

要求：

1. 必须通过 `app/alerting` 调用。
2. 必须使用固定模板。
3. 不得在底层 Binance client 中直接报警。
4. 不得在 MySQL repository 中直接报警。
5. 不得调用 DeepSeek。
6. 不得生成交易建议。
7. 报警内容必须说明“不自动修复、不人工修改、不自动交易”。

如果 Hermes 发送失败：

1. 应记录日志。
2. 应尽量更新 `collector_event_log` 中的报警状态。
3. 不得因此修改正式 K线表。
4. 不得无限重试。

## 35. WebSocket 和价格监控边界

本阶段不得实现 WebSocket。

本阶段不得实现 10s 价格监控。

本阶段不得创建或使用：

1. WebSocket client。
2. WebSocket manager。
3. WebSocket price event parser。
4. Price monitor service。
5. Redis `bitcoin_price`。
6. REST 最新价格查询。
7. REST 轮询价格。

10s 价格监控后续必须使用 Binance WebSocket 单独实现。

## 36. K线不可人工修改原则

本阶段必须严格遵守：

1. 不允许 manual_repair。
2. 不允许 human_edit。
3. 不允许 manual_input。
4. 不允许 system_repair。
5. 不允许人工直接修改 K线字段。
6. 不允许程序自动修复正式 K线。
7. 不允许质量检查任务自动修改正式 K线。
8. 不允许增量采集任务静默覆盖冲突 K线。

即使数据出现问题，也只能通过 08 手动 CLI 回补任务从 Binance REST 官方接口重新获取官方已收盘 K线，并按规则写入。

## 37. 交易安全边界

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

## 38. 测试要求

建议创建：

`tests/test_4h_kline_incremental_collector.py`

默认测试不得依赖真实 Binance、真实 MySQL、真实 Redis、真实 Hermes。

至少覆盖：

1. 缺少 `trigger_source` 时拒绝。
2. 非法 `trigger_source` 时拒绝。
3. `trigger_source = cli` 映射为 `data_source = binance_rest_by_cli`。
4. `trigger_source = scheduler` 映射为 `data_source = binance_rest_by_scheduler`。
5. scheduler job 显式传入 `trigger_source = scheduler`。
6. CLI 显式传入 `trigger_source = cli`。
7. service 会调用 BinanceRestClient mock。
8. service 会调用 get_server_time mock。
9. service 会过滤未收盘 K线。
10. service 会调用 parser。
11. service 会调用 quality checker。
12. quality failed 时不会写入 `market_kline_4h`。
13. quality failed 时会记录 `data_quality_check`。
14. quality failed 时会更新 `collector_event_log` 为 blocked。
15. Binance 请求失败时会更新 `collector_event_log` 为 failed。
16. 成功时会调用 `MarketKline4hRepository.bulk_upsert`。
17. 已存在一致数据会计入 skipped。
18. 字段冲突时不会覆盖。
19. 数据库为空时不会自动大范围回补。
20. 失败报警路径必须使用 alerting mock 验证，不发送真实 Hermes。
21. 不调用 DeepSeek。
22. 不写 Redis 行情缓存；任务锁通过 Redis mock 或测试替身覆盖。
23. 不实现 WebSocket。
24. 不涉及交易接口。

如果需要真实集成测试，必须使用显式开关，例如：

```
RUN_4H_COLLECTOR_INTEGRATION_TESTS=true
```

默认 `pytest` 不应访问真实外部服务。

## 39. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. 增量采集开始。
2. trigger_source。
3. data_source。
4. 拉取参数。
5. Binance 请求成功或失败。
6. 获取 raw kline 数量。
7. 过滤未收盘数量。
8. parser 成功数量。
9. quality check 结果。
10. 插入数量。
11. 跳过数量。
12. 冲突数量。
13. event log id。
14. alert message id。
15. 增量采集结束状态。

禁止记录：

1. 数据库密码。
2. 完整 `.env`。
3. Hermes webhook。
4. Hermes secret。
5. token。
6. Authorization。
7. cookie。
8. 账户信息。
9. 持仓信息。
10. 交易信息。

## 40. 异常要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增异常：

1. `KlineCollectorError`
2. `KlineCollectorParameterError`
3. `KlineCollectorBlockedError`
4. `KlineCollectorPersistError`

异常要求：

1. 参数错误必须明确指出参数名。
2. Binance 请求失败必须明确是外部请求失败。
3. 质量检查阻断必须明确首个质量问题。
4. 写库失败必须明确是存储异常。
5. 异常消息不得包含敏感信息。
6. 不得因为异常自动修复 K线。
7. 不得因为异常自动回补历史范围。
8. CLI 遇到失败应返回非 0 状态码。
9. scheduler job 遇到失败应记录失败状态并抛出或返回明确失败结果。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。

## 41. 交付物要求

本阶段完成后，Codex 必须交付：

1. 4h K线增量采集 service。
2. 4h K线采集 CLI。
3. scheduler job 入口。
4. 增量采集请求类型定义。
5. 增量采集流程测试文件。
6. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/09_4h_kline_incremental_collector.md`

说明文件必须描述：

1. 本模块入口。
2. CLI 参数。
3. scheduler job 入口文件和方法名。
4. scheduler 调用链。
5. CLI 调用链。
6. Binance REST 请求流程。
7. 重叠拉取逻辑。
8. 未收盘过滤流程。
9. parser 调用流程。
10. 质量检查流程。
11. `data_quality_check` 写入流程。
12. `collector_event_log` 写入流程。
13. `market_kline_4h` 写入流程。
14. 事务边界。
15. 幂等规则。
16. K线写入任务锁获取、失败、释放流程。
16. Hermes 报警流程。
17. `trigger_source` 与 `data_source` 的映射。
18. 不允许人工修改 K线的边界。
19. 本模块不负责的边界。

本阶段 implementation 文档必须遵守 `AGENTS.md` 中的“代码可读性与实现说明强制要求”，按功能写清楚入口文件、方法调用链、数据流、异常处理、测试方式和本模块边界。

本阶段说明文件不需要描述：

1. 手动历史大范围回补流程。
2. 每日完整性复核流程。
3. Redis 价格缓存流程。
4. WebSocket 价格监控流程。
5. 策略建议流程。
6. DeepSeek 分析流程。

原因：这些能力本阶段不实现。

## 42. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.collect_4h_klines --help` 可以运行。
2. 缺少 `--trigger-source` 时拒绝执行。
3. `--trigger-source` 非 `cli` 或 `scheduler` 时拒绝执行。
4. CLI 触发时记录 `trigger_source = cli`。
5. scheduler 触发时记录 `trigger_source = scheduler`。
6. CLI 触发时记录 `data_source = binance_rest_by_cli`。
7. scheduler 触发时记录 `data_source = binance_rest_by_scheduler`。
8. `pytest` 默认可以运行成功。
9. 默认测试不请求真实 Binance。
10. 默认测试不连接真实 MySQL。
11. 默认测试不连接真实 Redis，任务锁使用 mock 或测试替身。
12. 默认测试不发送真实 Hermes。
13. 未创建策略表。
14. 未创建建议表。
15. 未修改 `market_kline_4h` 表结构。
16. 质量检查失败时不写入 `market_kline_4h`。
17. 字段冲突时不覆盖旧 K线。
18. 成功时通过 repository 幂等写入 `market_kline_4h`。
19. 成功时记录 `collector_event_log = success`。
20. 质量阻断时记录 `collector_event_log = blocked`。
21. 异常失败时记录 `collector_event_log = failed`。
22. 失败报警路径必须使用 `app/alerting` mock 验证，不发送真实 Hermes。
23. 不调用 DeepSeek。
24. 不实现 WebSocket。
25. 不写入 Redis `bitcoin_price`。
26. 写正式 K线前必须获取 `kline_write:BTCUSDT:4h` 任务锁。
26. 不实现交易建议。
27. 不实现交易执行相关代码。
28. `docs/implementation/09_4h_kline_incremental_collector.md` 已创建或补齐。

## 43. 人工审查清单

合并前用户应人工检查：

1. 查看 CLI 是否强制 `--trigger-source`。
2. 查看 scheduler job 是否显式传入 `trigger_source = scheduler`。
3. 查看 CLI 是否可以传入 `trigger_source = cli`。
4. 查看是否存在自动猜测 trigger_source。
5. 查看是否存在 manual_repair / human_edit / manual_input / system_repair。
6. 查看是否绕过 BinanceRestClient 手写 Binance 请求。
7. 查看是否请求 REST 最新价格。
8. 查看是否实现 WebSocket。
9. 查看是否写 Redis。
10. 查看是否调用 DeepSeek。
11. 查看是否存在交易接口。
12. 查看是否静默覆盖 `market_kline_4h`。
13. 查看是否修改或删除已有 K线。
14. 查看 `collector_event_log` 是否记录 trigger_source 和 data_source。
15. 查看写库前是否调用质量检查。
16. 查看是否采用重叠拉取，而不是只拉最新一根。
17. 查看 implementation 是否写清楚文件、方法、调用链、数据流和边界。
18. 运行测试。
19. 运行 help 命令。

建议搜索：

```
grep -R "manual_repair" app scripts tests migrations
grep -R "human_edit" app scripts tests migrations
grep -R "manual_input" app scripts tests migrations
grep -R "system_repair" app scripts tests migrations
grep -R "ticker/price" app scripts tests
grep -R "websocket" app scripts tests
grep -R "bitcoin_price" app scripts tests
grep -R "DeepSeek" app scripts tests
grep -R "openai" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

还需要重点检查：

```
grep -R "update market_kline_4h" app scripts tests
grep -R "delete from market_kline_4h" app scripts tests
```

如果这些出现在增量采集模块中，应拒绝合并。

## 44. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 缺少 `trigger_source` 仍执行。
2. 自动猜测 `trigger_source`。
3. scheduler 触发却记录为 `cli`。
4. CLI 触发却记录为 `scheduler`。
5. 添加 manual_repair。
6. 添加 human_edit。
7. 添加 manual_input。
8. 添加 system_repair。
9. 人工修改 K线字段。
10. 自动修复 K线。
11. 静默覆盖冲突 K线。
12. 删除已有 K线。
13. 绕过 BinanceRestClient 请求 Binance。
14. 请求 REST 最新价格。
15. 实现 WebSocket。
16. 写入 Redis。
17. 创建 `bitcoin_price`。
18. 调用 DeepSeek。
19. 生成交易建议。
20. 创建策略表。
21. 创建建议表。
22. 实现任何交易执行代码。
23. 自动执行 Alembic migration。
24. 提交真实密钥。
25. 提交真实日志。
26. 提交 `.env`。
27. 删除、清空或覆盖已有文档。
28. 把核心采集逻辑写进 `scripts`。

## 45. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 查看 CLI 帮助：

   python -m scripts.collect_4h_klines --help

4. 人工确认没有静默覆盖、删除、修改正式 K线表的代码。

5. 人工确认没有 WebSocket、Redis 价格监控、DeepSeek、交易接口。

6. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 4h K线增量采集能力"

7. 用户自行推送分支，并进入代码审查流程。
