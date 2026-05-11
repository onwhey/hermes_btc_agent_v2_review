# 08 4h Kline Manual Backfill Plan

## 1. 阶段目标

本阶段实现 BTCUSDT 4h K线手动回补能力。

本阶段是第一次真正把 Binance REST 官方已收盘 4h K线写入 `market_kline_4h` 正式 K线表。

本阶段负责：

1. 创建 4h K线手动回补 CLI 入口。
2. 创建 4h K线手动回补 service。
3. 通过 `BinanceRestClient.get_klines()` 拉取 Binance 官方 4h K线。
4. 过滤未收盘 K线。
5. 调用 06 阶段 parser 转换为内部 DTO。
6. 调用 07 阶段质量检查模块进行写库前检查。
7. 创建或补齐 `collector_event_log` 表。
8. 记录手动回补任务运行事件。
9. 将通过质量检查的 K线幂等写入 `market_kline_4h`。
10. 发现质量问题、blocked、failed、写入失败、任务异常或无法确认回补健康状态时，必须通过 `app/alerting` 发送 Hermes 固定模板报警。
11. 创建对应测试文件。
12. 创建对应实现说明文件。

本阶段只做**用户手动发起的 K线回补**，不做定时增量采集。

## 2. 本阶段明确不做

本阶段不得实现自动采集、scheduler、策略分析或交易功能。

禁止实现：

1. scheduler 定时任务。
2. 自动增量采集。
3. 每日自动复核任务。
4. 10s 价格监控。
5. WebSocket。
6. Redis 写入 `bitcoin_price`。
7. DeepSeek 或其他大模型调用。
8. 策略分析。
9. 交易建议。
10. 自动下单、自动平仓、自动调仓。
11. Binance 账户、订单、持仓、杠杆、保证金相关接口。
12. 人工直接修改 K线字段。
13. manual_repair。
14. human_edit。
15. manual_input。
16. system_repair。
17. 自动修复 K线。
18. 静默覆盖冲突 K线。

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
19. `docs/implementation/01_project_skeleton.md`
20. `docs/implementation/02_core_config_logging.md`
21. `docs/implementation/03_infra_mysql_redis.md`
22. `docs/implementation/04_alerting_through_hermes.md`
23. `docs/implementation/05_binance_rest_client.md`
24. `docs/implementation/06_market_kline_4h.md`
25. `docs/implementation/07_kline_quality_checker.md`

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
12. `app/alerting`

本阶段不得重复实现 Binance REST 请求、K线 parser、基础 validator、质量检查、数据库 session 和 Hermes 发送逻辑。

## 4. 建议分支

建议分支名：

`feature/08-4h-kline-manual-backfill`

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
app/market_data/backfill/
app/storage/mysql/
app/storage/mysql/models/
app/storage/mysql/repositories/
migrations/versions/
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
4. 删除 `app/storage/mysql/` 后重建。
5. 覆盖已有配置、日志、数据库、报警、Binance REST、K线基础模块、质量检查模块。
6. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/market_data/backfill/__init__.py
app/market_data/backfill/kline_4h_backfill_service.py
app/market_data/backfill/types.py

app/storage/mysql/models/collector_event_log.py
app/storage/mysql/repositories/collector_event_log_repository.py

migrations/versions/<revision>_create_collector_event_log.py

scripts/backfill_4h_klines.py
tests/test_4h_kline_manual_backfill.py
docs/implementation/08_4h_kline_manual_backfill.md
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. 手动回补模块定位

手动回补模块路径：

`app/market_data/backfill/`

该模块负责：

1. 接收用户手动指定的回补范围。
2. 校验回补参数。
3. 通过 Binance REST 拉取官方 K线。
4. 过滤未收盘 K线。
5. 解析 Binance raw kline。
6. 执行质量检查。
7. 记录 `collector_event_log`。
8. 将合格 K线幂等写入 `market_kline_4h`。
9. 发生质量问题、blocked、failed、写入失败、任务异常或无法确认回补健康状态时，必须通过 `app/alerting` 发送 Hermes 固定模板报警。

该模块不负责：

1. scheduler 定时采集。
2. 每日自动复核。
3. 10s 价格监控。
4. WebSocket。
5. 策略分析。
6. DeepSeek 分析。
7. 交易建议。
8. 自动交易。
9. 人工修改 K线。
10. 自动修复 K线。

## 8. CLI 入口要求

建议文件：

`scripts/backfill_4h_klines.py`

该脚本是用户手动回补入口。

允许用户手动执行：

```
python -m scripts.backfill_4h_klines --symbol BTCUSDT --interval 4h --start-open-time-ms 1710000000000 --end-open-time-ms 1711440000000 --trigger-source cli
```

也可以支持 UTC 时间参数：

```
python -m scripts.backfill_4h_klines --symbol BTCUSDT --interval 4h --start-utc "2026-05-01T00:00:00Z" --end-utc "2026-05-07T00:00:00Z" --trigger-source cli
```

要求：

1. 本脚本只允许用户手动执行。
2. 本阶段不允许 scheduler 调用本脚本。
3. `--trigger-source` 本阶段只允许 `cli`。
4. 缺少 `--trigger-source` 必须拒绝执行。
5. 非法 `--trigger-source` 必须拒绝执行。
6. 脚本只负责解析参数、初始化配置和调用 service。
7. 脚本不得直接请求 Binance。
8. 脚本不得直接写数据库。
9. 脚本不得直接发送 Hermes。
10. 脚本不得承载核心业务逻辑。

文件顶部必须写清楚：

1. 这是手动 CLI 入口。
2. 只允许用户手动触发。
3. 本阶段不允许 scheduler 调用。
4. 只允许 `--trigger-source cli`。
5. 数据只能来自 Binance REST 官方接口。
6. 不允许 manual_repair。
7. 不允许人工修改正式 K线表。
8. 不允许自动修复。
9. 不允许自动下单。

## 9. 回补参数要求

脚本建议支持参数：

```
--symbol BTCUSDT
--interval 4h
--start-open-time-ms
--end-open-time-ms
--start-utc
--end-utc
--limit-per-request
--trigger-source cli
--dry-run
```

参数规则：

1. `--symbol` 默认 `BTCUSDT`。
2. `--interval` 默认 `4h`。
3. `--trigger-source` 必填，本阶段只允许 `cli`。
4. `--start-open-time-ms` / `--end-open-time-ms` 可以作为毫秒时间戳范围。
5. `--start-utc` / `--end-utc` 可以作为 UTC 时间范围。
6. 两种时间参数不能混乱使用。
7. 如果同时传毫秒和 UTC，应拒绝或明确优先级，建议拒绝。
8. `start` 必须小于 `end`。
9. 时间范围必须按 4h 周期对齐。
10. `limit-per-request` 不得超过 Binance K线接口最大 limit。
11. `--dry-run` 只检查流程，不写入 `market_kline_4h`。
12. 本阶段不允许恢复或新增用于控制失败报警的 CLI 开关。
13. 手动回补成功通知后续可设计为可选参数，例如 `--notify-success`；质量失败、blocked、failed、写入失败和任务异常报警不得设为可选。

禁止：

1. 缺省时间范围后自动猜测大范围。
2. 无限制回补。
3. 缺少 `trigger_source` 仍执行。
4. 非法 `trigger_source` 仍执行。
5. 将 PRC 时间作为业务时间边界。
6. 用本机当前时间判断未收盘 K线。

## 10. trigger_source 与 data_source 要求

本阶段手动回补只允许：

```
trigger_source = cli
```

写入正式 K线时必须映射为：

```
data_source = binance_rest_by_cli
```

禁止：

1. 使用 `binance_rest_by_scheduler`。
2. 使用 `manual_repair`。
3. 使用 `human_edit`。
4. 使用 `manual_input`。
5. 使用 `system_repair`。
6. 缺少 `trigger_source` 仍写入正式 K线表。
7. 自动猜测触发来源。
8. 根据是否经过 `scripts/*.py` 猜测 `data_source`。

注意：

`trigger_source` 表示任务如何触发。

`data_source` 表示数据来源与触发方式的组合。

即使是用户手动回补，K线数据本身也必须来自 Binance REST 官方接口。

## 11. 手动回补 Service 要求

建议文件：

`app/market_data/backfill/kline_4h_backfill_service.py`

建议方法：

1. `run_manual_4h_backfill(request)`
2. `build_binance_kline_request_ranges(request)`
3. `fetch_raw_klines_for_backfill(request_range)`
4. `filter_closed_klines(raw_klines, binance_server_time_ms)`
5. `parse_backfill_klines(raw_klines, symbol, interval_value, trigger_source)`
6. `check_backfill_quality(session, klines)`
7. `persist_backfill_klines(session, klines)`
8. `record_backfill_event_running(session, request)`
9. `record_backfill_event_success(session, event_id, result)`
10. `record_backfill_event_failed(session, event_id, error)`
11. `send_backfill_alert_if_needed(error_or_report)`

职责：

1. 控制完整回补流程。
2. 创建 `collector_event_log`。
3. 请求 Binance REST。
4. 调用 parser。
5. 调用质量检查。
6. 调用 repository 写入正式 K线。
7. 处理异常。
8. 质量问题、blocked、failed、写入失败、任务异常或无法确认回补健康状态时，必须发送 Hermes 固定模板报警。

禁止：

1. 自动修复 K线。
2. 自动覆盖冲突 K线。
3. 修改已有正式 K线。
4. 删除已有正式 K线。
5. 调用 DeepSeek。
6. 生成交易建议。
7. 执行交易。
8. 实现 scheduler。

## 12. K线写入任务锁要求

手动回补会写入正式 K线表，因此启动前必须先获取同一 `symbol + interval` 的 K线写入任务锁。

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
3. TTL 应大于一次正常回补任务的最长预期时间。
4. 如果锁已存在，本次回补必须拒绝或跳过，并记录 `collector_event_log.status = skipped`。
5. 锁存在时不得继续请求 Binance、不得继续质量检查、不得继续写正式 K线表。
6. 释放锁时必须校验 owner，只能释放当前任务自己持有的锁。
7. 不得只依赖 `collector_event_log.status = running` 判断是否并发。
8. 手动回补和增量采集必须共用同一 `symbol + interval` 写入锁，避免互相并发写入。

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

## 13. 手动回补调用链

本阶段实现的标准调用链必须是：

```
用户 CLI
    ↓
scripts/backfill_4h_klines.py::main
    ↓
app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
    ↓
app/core/task_lock.py 获取 kline_write:BTCUSDT:4h 锁
    ↓
collector_event_log_repository.create_running_event
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
返回回补结果
```

异常链路：

```
任意步骤失败
    ↓
回滚当前事务中未提交写入
    ↓
collector_event_log_repository.mark_failed 或 mark_blocked
    ↓
必须通过 app/alerting 发送 Hermes 固定模板报警
    ↓
CLI 返回非 0 状态码
```

## 14. Binance REST 请求要求

本阶段只能通过：

`BinanceRestClient.get_klines()`

请求 Binance K线。

禁止：

1. 在回补 service 中手写 Binance URL。
2. 在脚本中手写 Binance URL。
3. 直接使用 requests/httpx 绕过 `BinanceRestClient`。
4. 请求 REST 最新价格。
5. 使用 WebSocket。
6. 使用第三方行情源。
7. 使用人工输入数据作为 K线源。

允许调用：

1. `BinanceRestClient.get_server_time()`：用于未收盘过滤。
2. `BinanceRestClient.get_klines()`：用于获取官方 K线。

禁止调用：

1. order endpoint。
2. account endpoint。
3. position endpoint。
4. leverage endpoint。
5. margin endpoint。
6. listenKey。
7. ticker price endpoint。

## 15. 分批请求要求

Binance K线接口有 limit 限制，本阶段必须支持按时间范围分批请求。

要求：

1. 根据 start / end 时间范围拆分请求。
2. 每次请求 limit 不得超过 `BINANCE_KLINE_MAX_LIMIT`。
3. 请求范围必须按 4h 周期推进。
4. 不得无限循环。
5. 每个请求范围必须记录在日志或 event log 摘要中。
6. 如果某批请求失败，应停止当前回补任务。
7. 不得跳过失败批次继续写入后续数据，除非后续明确设计 partial_success。
8. 本阶段建议采用 all-or-nothing 事务策略，避免半成功数据污染。

## 16. 未收盘过滤要求

手动回补必须过滤未收盘 K线。

判断依据：

1. 优先使用 `BinanceRestClient.get_server_time()` 获取 Binance server time。
2. 如果 K线 `close_time_ms >= server_time_ms`，视为未收盘。
3. 未收盘 K线不得写入 `market_kline_4h`。
4. 如果请求结果中包含未收盘 K线，应记录为 filtered count。
5. 如果全部都是未收盘 K线，应阻断写入并返回明确结果。

禁止：

1. 使用本机时间作为唯一判断依据。
2. 未获取 Binance server time 仍继续写入。
3. 把未收盘 K线写入正式表。
4. 自动修复未收盘 K线。
5. 后续再覆盖未收盘 K线。

## 17. Parser 要求

本阶段必须复用 06 阶段：

`app/market_data/kline_parser.py`

要求：

1. 使用 `parse_binance_klines()` 转换 raw kline。
2. 传入 `trigger_source = cli`。
3. parser 自动生成 `data_source = binance_rest_by_cli`。
4. parser 生成 `raw_payload_json`。
5. parser 生成 `raw_payload_hash`。
6. parser 使用 UTC 时间作为业务时间。
7. parser 使用 PRC 时间作为展示辅助字段。

禁止：

1. 在回补 service 中重复实现 raw kline 解析。
2. 手工拼 DTO 绕过 parser。
3. 手工写 PRC 时间转换。
4. 使用 `+ timedelta(hours=8)` 代替 `app/core/time_utils.py`。

## 18. 质量检查要求

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

禁止：

1. 质量检查失败后仍强行写入。
2. 质量检查失败后自动修复。
3. 质量检查失败后自动回补更多 K线。
4. 静默跳过异常 K线后继续写入。
5. 调用 DeepSeek 判断是否可以写入。

## 19. 正式 K线写入要求

正式 K线写入必须通过：

`MarketKline4hRepository.bulk_upsert()`

要求：

1. 只写入通过质量检查的已收盘 K线。
2. 基于唯一键 `symbol + interval_value + open_time_ms` 幂等写入。
3. 已存在且字段一致的 K线可以跳过。
4. 不存在的 K线可以插入。
5. 已存在但字段冲突时必须阻断。
6. 不得静默覆盖。
7. 不得删除旧数据。
8. 不得修改旧数据。
9. 不得人工修复。
10. 不得自动修复。

建议事务规则：

1. 一个回补任务使用一个数据库事务。
2. 质量检查通过后再写正式 K线。
3. 写入失败则回滚本次正式 K线写入。
4. `collector_event_log` 应尽量记录最终失败状态。
5. 避免出现部分 K线写入成功、部分失败的不可解释状态。

## 20. collector_event_log 表结构要求

本阶段允许创建 `collector_event_log` 表。

该表用于记录采集、回补、复核相关任务运行事件。

虽然本阶段只实现手动回补，但该表后续也会被定时增量采集复用。

建议字段：

```
id
event_type
symbol
interval_value

trigger_source
data_source

status
severity

requested_start_open_time_ms
requested_end_open_time_ms
actual_start_open_time_ms
actual_end_open_time_ms

requested_count
fetched_count
parsed_count
closed_count
inserted_count
skipped_count
conflict_count
filtered_unclosed_count

quality_check_id
alert_message_id

error_code
error_message
trace_id

started_at_utc
started_at_prc
finished_at_utc
finished_at_prc

created_at_utc
created_at_prc
updated_at_utc
updated_at_prc
```

字段说明：

1. `event_type`：例如 `manual_backfill_4h`。
2. `trigger_source`：本阶段只允许 `cli`。
3. `data_source`：本阶段只允许 `binance_rest_by_cli`。
4. `status`：`running`、`success`、`failed`、`blocked`、`partial_success`、`skipped`。
5. `severity`：`info`、`warning`、`error`、`critical`。
6. `quality_check_id`：关联 `data_quality_check`，允许为空。
7. `alert_message_id`：关联 `alert_message`，允许为空。
8. `filtered_unclosed_count`：过滤掉的未收盘 K线数量。
9. `error_message`：必须脱敏后保存。

注意：

1. 本阶段可以创建 `collector_event_log`。
2. 本阶段不得创建策略表或建议表。
3. 本阶段不得把 `collector_event_log` 当作正式 K线表。
4. 本阶段不得在 event log 里保存未脱敏敏感信息。

## 21. collector_event_log Repository 要求

建议文件：

`app/storage/mysql/repositories/collector_event_log_repository.py`

建议类名：

`CollectorEventLogRepository`

允许方法：

1. `create_running_event(request)`
2. `mark_success(event_id, result)`
3. `mark_failed(event_id, error)`
4. `mark_blocked(event_id, report)`
5. `mark_partial_success(event_id, result)`
6. `attach_quality_check(event_id, quality_check_id)`
7. `attach_alert_message(event_id, alert_message_id)`
8. `get_latest_by_type(symbol, interval_value, event_type)`

Repository 负责：

1. 写入 `collector_event_log`。
2. 更新任务状态。
3. 查询最近任务。
4. 不直接请求 Binance。
5. 不直接发送 Hermes。
6. 不写入正式 K线表。
7. 不修改正式 K线表。

禁止：

1. 自动修复 K线。
2. 自动回补 K线。
3. 修改 `market_kline_4h`。
4. 删除 `market_kline_4h`。
5. 调用 DeepSeek。
6. 执行交易相关逻辑。

## 22. Event 状态规则

本阶段状态规则：

### running

任务已创建，流程开始执行。

创建时机：

1. 参数校验通过后。
2. 请求 Binance 前。

### success

任务成功完成。

条件：

1. Binance 请求成功。
2. 解析成功。
3. 未收盘过滤完成。
4. 质量检查通过。
5. 正式 K线写入成功或全部为已存在一致数据。
6. 无阻断级异常。

### blocked

任务被质量检查阻断。

常见原因：

1. 批次不连续。
2. 存在字段冲突。
3. data_source 映射异常。
4. 全部 K线未收盘。
5. 与数据库最新 K线不连续。

blocked 表示系统按规则拒绝写入，不是程序崩溃。

### failed

任务执行失败。

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

1. 手动回补应优先采用事务避免 partial_success。
2. 只有在确实无法回滚某些外部副作用时，才允许记录 partial_success。
3. 如果出现 partial_success，implementation 必须写明原因。

### skipped

任务被跳过或拒绝。

常见原因：

1. 同一 `symbol + interval` 的 K线写入任务锁已存在。
2. 当前已有手动回补或增量采集正在运行。

skipped 表示系统按并发控制规则拒绝执行，不是数据质量失败，也不是程序崩溃。

## 23. Hermes 报警要求

本阶段必须继承 07 K线质量报警新规则：质量问题、blocked、failed、无法确认健康状态时必须通过 `app/alerting` 发送 Hermes 固定模板报警。

必须报警场景：

1. Binance REST 请求失败。
2. Binance server time 获取失败。
3. 回补结果为空。
4. 质量检查失败。
5. 质量检查返回 blocked。
6. 数据库字段冲突。
7. K线不连续。
8. 未收盘 K线被误写风险。
9. 正式 K线写入失败。
10. collector_event_log 写入失败。
11. 任务状态 failed。
12. 任务异常导致无法确认回补健康状态。
13. 未预期异常。

失败报警不得由 CLI 参数控制，不允许恢复 `07` 已废弃的失败报警开关语义。

报警模板必须使用固定模板。

建议模板类型：

```
collector_failed
kline_data_quality_error
kline_integrity_check_failed
```

报警内容必须说明：

1. event_type。
2. symbol。
3. interval。
4. trigger_source。
5. data_source。
6. 请求范围。
7. 实际获取数量。
8. 已收盘数量。
9. 写入数量。
10. 异常类型。
11. 首个质量问题。
12. 明确系统没有自动修复。
13. 明确系统没有自动回补更多数据。
14. 明确系统没有人工修改 K线。
15. 建议用户检查 Binance REST、采集代码、数据库写入、任务参数。

禁止：

1. 调用 DeepSeek 生成报警。
2. 调用其他大模型生成报警。
3. 生成交易建议。
4. 自动下单。
5. 在 `app/exchange/binance` 中直接报警。
6. 在 `app/storage/mysql` 中直接报警。

## 24. dry-run 要求

本阶段建议支持 `--dry-run`。

dry-run 行为：

1. 可以请求 Binance REST。
2. 可以解析 K线。
3. 可以执行质量检查。
4. 可以生成检查报告。
5. 可以写入 `data_quality_check`，是否写入由实现说明明确。
6. 可以写入 `collector_event_log`，状态应标明 dry-run，是否写入由实现说明明确。
7. 不得写入 `market_kline_4h`。
8. 不得修改 `market_kline_4h`。
9. 不得删除 `market_kline_4h`。
10. dry-run 默认不发送 Hermes；如果 dry-run 发现质量问题、blocked、failed 或任务异常，应按 07 新规则记录并触发固定模板报警，且不得写入 `market_kline_4h`。

建议：

1. dry-run 默认记录日志即可。
2. 如果 dry-run 也写 `collector_event_log`，必须明确 `event_type` 或 report 中标识 dry-run。
3. dry-run 不应污染正式 K线表。

## 25. 事务要求

本阶段必须明确事务边界。

建议：

1. 参数校验在事务外完成。
2. Binance REST 请求在事务外完成。
3. parser 和质量检查可在事务外完成。
4. 正式 K线写入、`data_quality_check`、`collector_event_log` 状态更新应在明确事务中完成。
5. 如果正式 K线写入失败，应回滚正式 K线写入。
6. 如果 quality check 失败，不进入正式 K线写入。
7. 不得出现静默部分提交。

实现时可以根据项目 session 管理能力选择：

1. 一个大事务包住质量记录、正式写入、事件状态。
2. event log running 先提交，再后续更新状态。

无论选择哪种方式，implementation 必须写清楚：

1. 哪些操作在同一事务。
2. 哪些操作先提交。
3. 失败时如何保证可追踪。
4. 是否可能出现 event log 存在但 K线未写入。
5. 是否可能出现 K线写入但 event log 更新失败。

## 26. 幂等要求

手动回补必须支持幂等。

规则：

1. 同一时间范围重复回补，不得插入重复 K线。
2. 已存在且字段一致的 K线应跳过或计入 skipped。
3. 已存在但字段不一致，必须阻断。
4. 不得覆盖已有字段。
5. 不得删除已有字段。
6. 不得人工修复已有字段。
7. 重复运行应能通过 event log 看出每次运行结果。

## 27. 检查脚本与回补脚本区别

本阶段的 `scripts/backfill_4h_klines.py` 是真实回补入口。

它不同于前面的检查脚本。

允许：

1. 请求 Binance。
2. 读取 MySQL。
3. 写入 `data_quality_check`。
4. 写入 `collector_event_log`。
5. 在质量通过后写入 `market_kline_4h`。
6. 失败报警必须发送 Hermes 固定模板报警；成功通知可选。

禁止：

1. 写 Redis。
2. WebSocket。
3. scheduler 调用。
4. DeepSeek。
5. 交易执行。
6. 人工改数。
7. 自动修复。

## 28. 测试要求

建议创建：

`tests/test_4h_kline_manual_backfill.py`

默认测试不得依赖真实 Binance、真实 MySQL、真实 Redis、真实 Hermes。

至少覆盖：

1. CLI 参数缺少 `trigger_source` 时拒绝。
2. CLI 参数 `trigger_source != cli` 时拒绝。
3. 时间范围 start >= end 时拒绝。
4. 时间范围不按 4h 对齐时拒绝。
5. service 会调用 BinanceRestClient mock。
6. service 会调用 get_server_time mock。
7. service 会过滤未收盘 K线。
8. service 会调用 parser。
9. service 会调用 quality checker。
10. quality failed 时不会写入 `market_kline_4h`。
11. quality failed 时会记录 `data_quality_check`。
12. quality failed 时会更新 `collector_event_log` 为 blocked。
13. Binance 请求失败时会更新 `collector_event_log` 为 failed。
14. 成功时会调用 `MarketKline4hRepository.bulk_upsert`。
15. 已存在一致数据会计入 skipped。
16. 字段冲突时不会覆盖。
17. dry-run 不写入 `market_kline_4h`。
18. 失败报警路径必须使用 alerting mock 验证，不发送真实 Hermes。
19. 不调用 DeepSeek。
20. 不写 Redis 行情缓存；任务锁通过 Redis mock 或测试替身覆盖。
21. 不实现 scheduler。
22. 不实现 WebSocket。
23. 不涉及交易接口。
24. migration 只创建 `collector_event_log` 表。

如果需要真实集成测试，必须使用显式开关，例如：

```
RUN_4H_BACKFILL_INTEGRATION_TESTS=true
```

默认 `pytest` 不应访问真实外部服务。

## 29. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. 回补开始。
2. 回补参数。
3. 请求范围。
4. Binance 请求成功或失败。
5. 获取 raw kline 数量。
6. 过滤未收盘数量。
7. parser 成功数量。
8. quality check 结果。
9. 插入数量。
10. 跳过数量。
11. 冲突数量。
12. event log id。
13. alert message id。
14. 回补结束状态。

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

## 30. 异常要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增异常：

1. `KlineBackfillError`
2. `KlineBackfillParameterError`
3. `KlineBackfillBlockedError`
4. `KlineBackfillPersistError`

异常要求：

1. 参数错误必须明确指出参数名。
2. Binance 请求失败必须明确是外部请求失败。
3. 质量检查阻断必须明确首个质量问题。
4. 写库失败必须明确是存储异常。
5. 异常消息不得包含敏感信息。
6. 不得因为异常自动修复 K线。
7. 不得因为异常自动回补更多范围。
8. CLI 遇到失败应返回非 0 状态码。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。

## 31. 数据库影响

本阶段允许：

1. 创建 `collector_event_log` SQLAlchemy model。
2. 创建 `collector_event_log` Alembic migration。
3. 创建 `collector_event_log` repository。
4. 写入 `collector_event_log`。
5. 写入 `data_quality_check`。
6. 在质量检查通过后写入 `market_kline_4h`。
7. 读取 `market_kline_4h` 用于冲突检查。

本阶段禁止：

1. 修改已有 `market_kline_4h` 字段。
2. 删除 `market_kline_4h`。
3. 静默覆盖冲突 K线。
4. 自动修复 K线。
5. 自动执行 migration。
6. 创建策略表。
7. 创建建议表。
8. 人工修复 K线字段。

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

本阶段遇到质量问题、blocked、failed、写入失败、任务异常或无法确认回补健康状态时，必须调用 Hermes 固定模板报警；失败报警不得由 CLI 参数控制。

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

## 35. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得创建定时任务。

本阶段不得让 scheduler 调用：

`scripts/backfill_4h_klines.py`

未来 scheduler 增量采集应在 09 阶段实现，并使用独立采集 service 或明确传入 `--trigger-source scheduler`。

本阶段手动回补只能是：

```
trigger_source = cli
data_source = binance_rest_by_cli
```

## 36. WebSocket 和价格监控边界

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

## 37. K线不可人工修改原则

本阶段必须严格遵守：

1. 不允许 manual_repair。
2. 不允许 human_edit。
3. 不允许 manual_input。
4. 不允许 system_repair。
5. 不允许人工直接修改 K线字段。
6. 不允许程序自动修复正式 K线。
7. 不允许复核任务自动修改正式 K线。
8. 不允许回补任务静默覆盖冲突 K线。

即使数据出现问题，也只能通过本阶段手动 CLI 回补任务从 Binance REST 官方接口重新获取官方已收盘 K线，并按规则写入。

注意：

“手动回补”不是“手动改数”。

手动回补的含义是：

```
用户手动触发 CLI
    ↓
系统请求 Binance REST 官方 K线
    ↓
系统按规则校验和写入
```

不是：

```
用户手动输入 K线字段
    ↓
系统写入数据库
```

后者绝对禁止。

## 38. 交易安全边界

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

## 39. 交付物要求

本阶段完成后，Codex 必须交付：

1. 4h K线手动回补 CLI。
2. 4h K线手动回补 service。
3. 回补请求类型定义。
4. `collector_event_log` SQLAlchemy model。
5. `collector_event_log` Alembic migration。
6. `CollectorEventLogRepository`。
7. 回补流程测试文件。
8. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/08_4h_kline_manual_backfill.md`

说明文件必须描述：

1. 本模块入口。
2. CLI 参数。
3. 手动回补调用链。
4. Binance REST 请求流程。
5. 未收盘过滤流程。
6. parser 调用流程。
7. 质量检查流程。
8. `data_quality_check` 写入流程。
9. `collector_event_log` 写入流程。
10. `market_kline_4h` 写入流程。
11. 事务边界。
12. 幂等规则。
13. K线写入任务锁获取、失败、释放流程。
13. Hermes 报警流程。
14. `trigger_source = cli` 与 `data_source = binance_rest_by_cli` 的映射。
15. 不允许人工修改 K线的边界。
16. 本模块不负责的边界。

本阶段 implementation 文档必须遵守 `AGENTS.md` 中的“代码可读性与实现说明强制要求”，按功能写清楚入口文件、方法调用链、数据流、异常处理、测试方式和本模块边界。

本阶段说明文件不需要描述：

1. scheduler job 定义。
2. 定时增量采集流程。
3. Redis 价格缓存流程。
4. WebSocket 价格监控流程。
5. 策略建议流程。
6. DeepSeek 分析流程。

原因：这些能力本阶段不实现。

## 40. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.backfill_4h_klines --help` 可以运行。
2. 缺少 `--trigger-source` 时拒绝执行。
3. `--trigger-source` 非 `cli` 时拒绝执行。
4. `pytest` 默认可以运行成功。
5. 默认测试不请求真实 Binance。
6. 默认测试不连接真实 MySQL。
7. 默认测试不连接真实 Redis，任务锁使用 mock 或测试替身。
8. 默认测试不发送真实 Hermes。
9. `collector_event_log` migration 只创建事件日志表。
10. 未创建策略表。
11. 未创建建议表。
12. 未修改 `market_kline_4h` 表结构。
13. 质量检查失败时不写入 `market_kline_4h`。
14. 字段冲突时不覆盖旧 K线。
15. dry-run 不写入 `market_kline_4h`。
16. 成功时通过 repository 幂等写入 `market_kline_4h`。
17. 成功时记录 `collector_event_log = success`。
18. 质量阻断时记录 `collector_event_log = blocked`。
19. 异常失败时记录 `collector_event_log = failed`。
20. 失败报警路径必须使用 `app/alerting` mock 验证，不发送真实 Hermes。
21. 不调用 DeepSeek。
22. 不实现 scheduler。
23. 不实现 WebSocket。
24. 不写入 Redis `bitcoin_price`。
25. 写正式 K线前必须获取 `kline_write:BTCUSDT:4h` 任务锁。
25. 不实现交易建议。
26. 不实现交易执行相关代码。
27. `docs/implementation/08_4h_kline_manual_backfill.md` 已创建或补齐。

## 41. 人工审查清单

合并前用户应人工检查：

1. 查看 CLI 是否只允许 `--trigger-source cli`。
2. 查看是否存在 scheduler 调用回补脚本。
3. 查看是否存在 manual_repair / human_edit / manual_input / system_repair。
4. 查看是否绕过 BinanceRestClient 手写 Binance 请求。
5. 查看是否请求 REST 最新价格。
6. 查看是否实现 WebSocket。
7. 查看是否写 Redis。
8. 查看是否调用 DeepSeek。
9. 查看是否存在交易接口。
10. 查看是否静默覆盖 `market_kline_4h`。
11. 查看是否修改或删除已有 K线。
12. 查看是否创建 `collector_event_log`。
13. 查看 `collector_event_log` 是否记录 trigger_source 和 data_source。
14. 查看回补写库前是否调用质量检查。
15. 查看异常时是否可以报警。
16. 查看 implementation 是否写清楚文件、方法、调用链、数据流和边界。
17. 运行测试。
18. 运行 help 命令。

建议搜索：

```
grep -R "manual_repair" app scripts tests migrations
grep -R "human_edit" app scripts tests migrations
grep -R "manual_input" app scripts tests migrations
grep -R "system_repair" app scripts tests migrations
grep -R "scheduler" app/market_data/backfill scripts/backfill_4h_klines.py tests
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

如果这些出现在手动回补模块中，应拒绝合并。

## 42. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 允许 scheduler 调用手动回补脚本。
2. 接受 `trigger_source = scheduler`。
3. 缺少 `trigger_source` 仍执行。
4. 使用 `binance_rest_by_scheduler`。
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
20. 实现 scheduler。
21. 创建策略表。
22. 创建建议表。
23. 实现任何交易执行代码。
24. 自动执行 Alembic migration。
25. 提交真实密钥。
26. 提交真实日志。
27. 提交 `.env`。
28. 删除、清空或覆盖已有文档。
29. 把核心回补逻辑写进 `scripts`。

## 43. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 查看 CLI 帮助：

   python -m scripts.backfill_4h_klines --help

4. 人工确认 migration 没有创建越界表。

5. 人工确认没有静默覆盖、删除、修改正式 K线表的代码。

6. 人工确认没有 scheduler、WebSocket、Redis 价格监控、DeepSeek、交易接口。

7. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 4h K线手动回补能力"

8. 用户自行推送分支，并进入代码审查流程。
