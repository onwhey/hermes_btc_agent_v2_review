# 07 Kline Quality Checker Plan

## 1. 阶段目标

本阶段实现 BTCUSDT 4h K线质量检查能力。

本阶段目标是让后续 4h 手动回补、4h 定时增量采集、每日 K线复核可以复用统一的数据质量检查规则。

本阶段负责：

1. 创建 K线质量检查类型定义。
2. 创建 K线批次连续性检查。
3. 创建 K线未收盘检查。
4. 创建数据库已有 K线冲突检查。
5. 创建数据库最新 K线与新 K线连续性检查。
6. 创建最近 N 根 K线完整性复核能力。
7. 创建 `data_quality_check` 检查记录表。
8. 创建检查结果 repository。
9. 创建手动检查脚本。
10. 异常时调用 `app/alerting` 发送 Hermes 固定模板报警。
11. 创建对应测试文件。
12. 创建对应实现说明文件。

注意：

本阶段的“入库”只允许写入 `data_quality_check` 检查记录表。

本阶段不得写入、修改、覆盖、修复 `market_kline_4h` 正式 K线表。

## 2. 本阶段明确不做

本阶段不得实现 K线采集、K线回补、K线写入、策略分析或交易功能。

禁止实现：

1. 自动增量采集。
2. 手动回补写库流程。
3. scheduler 定时任务。
4. 自动执行每日复核任务。
5. 写入 `market_kline_4h` 正式 K线表。
6. 修改 `market_kline_4h` 正式 K线表。
7. 覆盖冲突 K线。
8. 自动修复 K线。
9. 自动回补 K线。
10. collector_event_log 表。
11. 10s 价格监控。
12. WebSocket。
13. Redis 写入 `bitcoin_price`。
14. DeepSeek 或其他大模型调用。
15. 策略分析。
16. 交易建议。
17. 自动下单、自动平仓、自动调仓。
18. Binance 账户、订单、持仓、杠杆、保证金相关接口。

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
18. `docs/implementation/01_project_skeleton.md`
19. `docs/implementation/02_core_config_logging.md`
20. `docs/implementation/03_infra_mysql_redis.md`
21. `docs/implementation/04_alerting_through_hermes.md`
22. `docs/implementation/05_binance_rest_client.md`
23. `docs/implementation/06_market_kline_4h.md`

本阶段必须复用：

1. `app/core/config.py`
2. `app/core/logger.py`
3. `app/core/time_utils.py`
4. `app/core/exceptions.py`
5. `app/exchange/binance/client.py`
6. `app/market_data/kline_dto.py`
7. `app/market_data/kline_parser.py`
8. `app/market_data/kline_validator.py`
9. `app/storage/mysql/repositories/market_kline_4h_repository.py`
10. `app/alerting`

本阶段不得重复实现配置读取、日志初始化、Binance REST 请求、K线 parser、Repository 和报警发送逻辑。

## 4. 建议分支

建议分支名：

`feature/07-kline-quality-checker`

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
app/market_data/kline_quality/
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
5. 覆盖已有配置、日志、数据库、报警、Binance REST、K线基础模块。
6. 用脚手架工具重置项目目录。

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```
app/market_data/kline_quality/__init__.py
app/market_data/kline_quality/types.py
app/market_data/kline_quality/rules.py
app/market_data/kline_quality/batch_checker.py
app/market_data/kline_quality/db_checker.py
app/market_data/kline_quality/integrity_checker.py
app/market_data/kline_quality/service.py
app/market_data/kline_quality/report_formatter.py

app/storage/mysql/models/data_quality_check.py
app/storage/mysql/repositories/data_quality_check_repository.py

migrations/versions/<revision>_create_data_quality_check.py

scripts/check_kline_quality_4h.py
tests/test_kline_quality_checker.py
docs/implementation/07_kline_quality_checker.md
```

文件处理原则：

1. 如果文件已经存在，Codex 必须先读取现有内容，再判断是否需要最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。
5. 如果现有文件内容与本 plan 不一致，应进行最小范围修改，并保留已有有效内容。
6. 如果不确定是否应该覆盖，必须停止并提示用户确认。

## 7. K线质量检查模块定位

K线质量检查模块路径：

`app/market_data/kline_quality/`

该模块负责：

1. 检查一批 K线是否连续。
2. 检查一批 K线是否重复。
3. 检查一批 K线是否包含未收盘 K线。
4. 检查新 K线与数据库最新 K线是否连续。
5. 检查新 K线是否与数据库已有 K线冲突。
6. 对最近 N 根数据库 K线和 Binance REST 官方 K线进行复核。
7. 生成结构化质量检查报告。
8. 必要时调用 `app/alerting` 发送 Hermes 固定模板报警。
9. 将检查结果写入 `data_quality_check`。

该模块不负责：

1. 写入正式 K线表。
2. 修改正式 K线表。
3. 删除正式 K线。
4. 自动回补 K线。
5. 自动修复 K线。
6. 判断交易方向。
7. 生成交易建议。
8. 调用 DeepSeek。
9. 执行 scheduler。
10. 执行自动下单。

## 8. 质量检查类型定义要求

建议文件：

`app/market_data/kline_quality/types.py`

建议定义：

1. `KlineQualityStatus`
2. `KlineQualitySeverity`
3. `KlineQualityIssueType`
4. `KlineQualityIssue`
5. `KlineQualityReport`
6. `KlineIntegrityCheckRequest`
7. `KlineIntegrityCheckResult`

建议 `KlineQualityStatus` 包含：

```
passed
failed
warning
```

建议 `KlineQualitySeverity` 包含：

```
info
warning
error
critical
```

建议 `KlineQualityIssueType` 至少包含：

```
invalid_field
duplicate_open_time
batch_not_continuous
db_gap
db_conflict
unclosed_kline
missing_in_db
extra_in_db
binance_compare_mismatch
open_time_misaligned
invalid_trigger_source
invalid_data_source_mapping
```

要求：

1. 类型定义不得依赖真实 MySQL 连接。
2. 类型定义不得请求 Binance。
3. 类型定义不得发送 Hermes。
4. 类型定义不得调用 DeepSeek。
5. 类型定义不得包含交易信号。

## 9. 基础规则模块要求

建议文件：

`app/market_data/kline_quality/rules.py`

本文件负责纯规则判断。

允许规则：

1. 判断 open_time 是否按 4h 周期对齐。
2. 判断相邻 K线 open_time 是否相差 4 小时。
3. 判断 close_time_ms 是否大于 open_time_ms。
4. 判断 close_time_ms 是否小于当前 Binance server time。
5. 判断 open_time_ms 是否重复。
6. 判断 batch 是否按 open_time_ms 升序排列。
7. 判断 data_source 与 trigger_source 映射是否正确。
8. 判断单根 K线是否通过 06 阶段基础 validator。

禁止：

1. 请求 Binance。
2. 读取 MySQL。
3. 写 MySQL。
4. 写 Redis。
5. 发送 Hermes。
6. 自动修复 K线。
7. 自动补齐缺失 K线。
8. 调用 DeepSeek。

`rules.py` 应尽量保持纯函数，便于测试。

## 10. 批次质量检查要求

建议文件：

`app/market_data/kline_quality/batch_checker.py`

批次检查用于后续回补、采集流程在写库前检查一批 K线。

允许检查：

1. 本批次 K线是否为空。
2. 本批次 K线是否按 open_time_ms 升序排列。
3. 本批次 K线是否存在重复 open_time_ms。
4. 本批次 K线是否连续。
5. 本批次 K线是否存在未收盘 K线。
6. 本批次 K线字段是否通过基础 validator。
7. 本批次 K线 `trigger_source` 是否合法。
8. 本批次 K线 `data_source` 映射是否正确。

要求：

1. 输入应是 DTO 列表。
2. 输出应是 `KlineQualityReport`。
3. 不得直接写正式 K线表。
4. 不得直接写 `data_quality_check`。
5. 不得请求 Binance。
6. 不得发送 Hermes。
7. 不得自动修复。
8. 不得自动回补。

说明：

批次检查只负责判断“这批 DTO 是否合格”，由上层 service 决定是否继续写库、记录检查结果或报警。

## 11. 数据库对比检查要求

建议文件：

`app/market_data/kline_quality/db_checker.py`

数据库对比检查用于后续采集、回补流程判断新 K线和已有 K线是否冲突。

允许检查：

1. 数据库最新 K线与新批次第一根 K线是否连续。
2. 新批次中是否已有 K线存在于数据库。
3. 已存在 K线是否字段一致。
4. 已存在 K线是否字段冲突。
5. 指定时间范围内数据库 K线是否缺失。
6. 指定时间范围内数据库 K线数量是否符合预期。

要求：

1. 必须通过 `MarketKline4hRepository` 读取数据库。
2. 不得绕过 repository 直接拼接复杂业务 SQL。
3. 不得静默覆盖冲突 K线。
4. 不得删除数据库已有 K线。
5. 不得修改数据库已有 K线。
6. 不得自动回补缺失 K线。
7. 不得发送 Hermes。
8. 不得调用 DeepSeek。

说明：

`db_checker.py` 可以读取 `market_kline_4h`，但不得写入或修改 `market_kline_4h`。

## 12. 最近 N 根 K线完整性复核要求

建议文件：

`app/market_data/kline_quality/integrity_checker.py`

完整性复核用于检查数据库中最近 N 根 4h K线是否和 Binance REST 官方 K线一致。

默认检查数量：

```
100
```

允许配置：

```
KLINE_INTEGRITY_CHECK_DEFAULT_LIMIT=100
KLINE_INTEGRITY_CHECK_MAX_LIMIT=500
```

完整性复核流程：

```
触发方
    ↓
integrity_checker
    ↓
BinanceRestClient.get_klines()
    ↓
parser 转换 Binance raw kline
    ↓
过滤未收盘 K线
    ↓
读取 MySQL 已存 K线
    ↓
比对 open_time_ms、价格、成交量、成交额、成交笔数
    ↓
生成 KlineQualityReport
    ↓
记录 data_quality_check
    ↓
如发现异常，调用 app/alerting 发送 Hermes 固定模板报警
```

复核目的：

1. 检查过去 K线是否存在数据错误。
2. 检查过去 K线是否不连续。
3. 检查过去 K线是否缺失。
4. 检查是否出现未收盘 K线误写。
5. 检查数据库字段是否与 Binance 官方 REST 不一致。
6. 发现异常后提醒用户检查采集代码、调度、数据库写入、Binance REST 访问。

复核禁止：

1. 自动修复。
2. 自动回补。
3. 自动覆盖。
4. 自动删除。
5. 修改 `market_kline_4h` 正式 K线表。
6. 生成交易建议。
7. 调用 DeepSeek。
8. 自动下单。

## 13. Quality Service 要求

建议文件：

`app/market_data/kline_quality/service.py`

该文件负责协调质量检查流程。

建议方法：

1. `check_batch_before_persist(klines, latest_db_kline=None)`
2. `check_against_database(session, klines)`
3. `run_recent_kline_integrity_check(session, symbol, interval_value, limit, check_trigger_source)`
4. `record_quality_check_result(session, report)`
5. `send_quality_alert_if_needed(report)`

职责：

1. 调用 batch checker。
2. 调用 db checker。
3. 调用 integrity checker。
4. 写入 `data_quality_check` 检查记录。
5. 异常时调用 `app/alerting`。
6. 返回结构化检查结果。

禁止：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 自动回补。
4. 自动修复。
5. 启动 scheduler。
6. 调用 DeepSeek。
7. 生成交易建议。
8. 执行交易。

## 14. `data_quality_check` 表结构要求

本阶段允许创建 `data_quality_check` 表。

该表用于记录 K线质量检查、复核任务和检查结果。

建议字段：

```
id
check_type
symbol
interval_value
check_trigger_source

status
severity

checked_count
issue_count

start_open_time_ms
end_open_time_ms
start_open_time_utc
end_open_time_utc
start_open_time_prc
end_open_time_prc

report_json
first_issue_type
first_issue_message

alert_sent
alert_message_id

created_at_utc
created_at_prc
updated_at_utc
updated_at_prc
```

字段说明：

1. `check_type`：例如 `batch_before_persist`、`db_compare`、`recent_integrity_check`。
2. `symbol`：例如 `BTCUSDT`。
3. `interval_value`：例如 `4h`。
4. `check_trigger_source`：检查任务触发来源。
5. `status`：`passed`、`failed`、`warning`。
6. `severity`：`info`、`warning`、`error`、`critical`。
7. `checked_count`：检查的 K线数量。
8. `issue_count`：发现的问题数量。
9. `report_json`：结构化检查报告。
10. `first_issue_type`：首个问题类型，便于快速排查。
11. `first_issue_message`：首个问题说明。
12. `alert_sent`：是否已发送 Hermes。
13. `alert_message_id`：关联 `alert_message`，允许为空。

注意：

1. `data_quality_check` 记录检查结果。
2. 它不是正式 K线表。
3. 它不得保存人工修复后的 K线。
4. 它不得替代 `market_kline_4h`。
5. 它不得触发自动修复。

## 15. `check_trigger_source` 要求

`data_quality_check` 使用 `check_trigger_source` 表示检查任务由谁触发。

允许值：

```
cli
scheduler
service
```

含义：

1. `cli`：用户手动执行检查脚本。
2. `scheduler`：未来 scheduler 自动触发检查。
3. `service`：后续采集或回补 service 在业务流程中调用质量检查。

要求：

1. 写入 `data_quality_check` 时必须记录 `check_trigger_source`。
2. 不得缺省为空。
3. 不得自动猜测。
4. 非法值必须拒绝。
5. 不得与 `market_kline_4h.trigger_source` 混淆。

注意：

`market_kline_4h.trigger_source` 用于正式 K线写入来源。

`data_quality_check.check_trigger_source` 用于检查任务来源。

两者不是同一个字段。

## 16. `data_source` 与正式 K线边界

本阶段不得写入正式 K线表，因此不得生成新的正式 K线 `data_source`。

但本阶段检查报告中可以读取并展示已有 K线的：

1. `data_source`
2. `trigger_source`

检查规则必须验证已有 K线是否符合：

```
trigger_source = scheduler
    ↓
data_source = binance_rest_by_scheduler

trigger_source = cli
    ↓
data_source = binance_rest_by_cli
```

如果发现已有数据不符合映射规则，应记录为质量问题，并通过 Hermes 提醒用户检查采集代码或历史写入流程。

禁止：

1. 修改错误的 `data_source`。
2. 修改错误的 `trigger_source`。
3. 自动修复映射错误。
4. 人工直接改正式 K线字段。

## 17. 未收盘 K线检查要求

本阶段必须提供未收盘 K线检查能力。

判断方式：

1. 使用 Binance server time 作为当前时间参考。
2. 如果 K线 `close_time_ms` 大于等于 Binance server time，则认为该 K线未收盘。
3. 未收盘 K线不得进入后续正式写库流程。
4. 如果数据库中已存在未收盘 K线，应记录为严重质量问题。

要求：

1. 未收盘检查不得使用本机时间作为唯一依据。
2. 未收盘检查应优先使用 `BinanceRestClient.get_server_time()` 返回的时间。
3. 如果无法获取 Binance server time，应返回检查失败，不得假装通过。
4. 检查失败时可记录 `data_quality_check`。
5. 检查失败时可调用 Hermes 固定模板报警。

禁止：

1. 自动删除数据库中的未收盘 K线。
2. 自动覆盖数据库中的未收盘 K线。
3. 自动回补缺失 K线。

## 18. 连续性检查要求

4h K线连续性以 `open_time_ms` 为准。

4h 周期毫秒数：

```
4 * 60 * 60 * 1000 = 14400000
```

批次连续性规则：

1. 相邻两根 K线的 `open_time_ms` 差值必须等于 `14400000`。
2. 如果差值大于 `14400000`，说明中间缺失 K线。
3. 如果差值小于 `14400000`，说明存在重复或时间异常。
4. 检查结果必须指出异常前后两根 K线的 open_time_ms。

数据库连续性规则：

1. 如果数据库已有最新 K线，新批次第一根 K线应接在最新 K线之后。
2. 如果新批次第一根 K线早于或等于数据库最新 K线，需要检查是否重复或冲突。
3. 不得静默跳过异常。
4. 不得自动补齐缺失 K线。

## 19. 字段冲突检查要求

字段冲突是指同一唯一键下，数据库已有 K线和新获取的 Binance 官方 K线字段不一致。

唯一键：

```
symbol + interval_value + open_time_ms
```

至少比较字段：

1. close_time_ms
2. open_price
3. high_price
4. low_price
5. close_price
6. volume
7. quote_volume
8. trade_count
9. taker_buy_base_volume
10. taker_buy_quote_volume
11. raw_payload_hash，如果存在

冲突处理要求：

1. 记录冲突问题。
2. 记录冲突字段。
3. 记录数据库值。
4. 记录 Binance REST 新值。
5. 不覆盖数据库值。
6. 不删除数据库值。
7. 不自动修复。
8. 不自动回补。
9. 必要时 Hermes 报警。

## 20. Hermes 报警要求

本阶段允许在质量检查发现异常时调用 `app/alerting`。

允许报警场景：

1. 最近 N 根完整性复核失败。
2. 数据库 K线缺失。
3. 数据库 K线不连续。
4. 数据库 K线字段与 Binance 官方 REST 不一致。
5. 数据库中存在未收盘 K线。
6. data_source 与 trigger_source 映射异常。
7. Binance REST 复核请求失败。
8. 数据质量检查无法完成。

报警模板必须使用固定模板。

建议使用模板类型：

```
kline_data_quality_error
kline_integrity_check_failed
```

报警内容必须说明：

1. 检查类型。
2. symbol。
3. interval。
4. 检查范围。
5. 问题数量。
6. 首个问题类型。
7. 首个问题说明。
8. 建议用户检查采集代码、调度、数据库写入、Binance REST 访问。
9. 明确系统没有自动修复。
10. 明确系统没有自动回补。
11. 明确系统没有修改正式 K线表。

禁止：

1. 调用 DeepSeek 生成报警。
2. 调用其他大模型生成报警。
3. 生成交易建议。
4. 自动下单。
5. 自动修复 K线后再报警。
6. 在 `app/exchange/binance` 中直接报警。
7. 在 `app/storage/mysql` 中直接报警。

## 21. 检查记录 Repository 要求

建议文件：

`app/storage/mysql/repositories/data_quality_check_repository.py`

建议类名：

`DataQualityCheckRepository`

允许方法：

1. `create_check_record(report)`
2. `mark_alert_sent(check_id, alert_message_id)`
3. `get_latest_by_type(symbol, interval_value, check_type)`
4. `list_recent_failed(symbol, interval_value, limit)`

Repository 负责：

1. 写入 `data_quality_check`。
2. 更新报警关联状态。
3. 查询最近检查结果。
4. 不直接发送 Hermes。
5. 不直接请求 Binance。
6. 不修改正式 K线表。

禁止：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动修复数据。
5. 调用 DeepSeek。
6. 执行交易相关逻辑。

## 22. Alembic Migration 要求

本阶段允许新增 Alembic migration，用于创建 `data_quality_check` 表。

要求：

1. migration 文件名应清楚表达用途。
2. 只创建 `data_quality_check` 表。
3. 不创建 collector_event_log 表。
4. 不创建策略表。
5. 不创建建议表。
6. 不修改 `market_kline_4h` 表。
7. 不插入业务数据。
8. 不写真实密钥。
9. 不硬编码生产数据库连接。

禁止 Codex 自动执行：

```
alembic upgrade head
```

迁移执行由用户人工决定。

Codex 可以生成 migration 文件，但不得自动连接数据库执行迁移。

## 23. 手动检查脚本要求

建议创建：

`scripts/check_kline_quality_4h.py`

该脚本用于用户手动检查最近 N 根 BTCUSDT 4h K线质量。

建议支持参数：

```
--symbol BTCUSDT
--interval 4h
--limit 100
--trigger-source cli
--send-alert
```

默认规则：

1. `--symbol` 默认 `BTCUSDT`。
2. `--interval` 默认 `4h`。
3. `--limit` 默认 `100`。
4. `--trigger-source` 本阶段只允许 `cli`。
5. 默认发现异常时可以记录 `data_quality_check`。
6. 是否真实发送 Hermes 取决于 `--send-alert` 和 Hermes 配置。
7. 即使发送 Hermes，也必须使用固定模板。
8. 不调用 DeepSeek。

允许该脚本：

1. 调用 `run_recent_kline_integrity_check()`。
2. 请求 Binance REST `/fapi/v1/klines`。
3. 读取 MySQL `market_kline_4h`。
4. 写入 `data_quality_check`。
5. 在用户显式允许时发送 Hermes 报警。

禁止该脚本：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动回补 K线。
5. 自动修复 K线。
6. 启动 scheduler。
7. 写 Redis。
8. 创建 `bitcoin_price`。
9. 调用 DeepSeek。
10. 下单、撤单、调杠杆、读账户、读持仓。

示例运行方式：

```
python -m scripts.check_kline_quality_4h --symbol BTCUSDT --interval 4h --limit 100 --trigger-source cli
```

如需允许异常时发送 Hermes：

```
python -m scripts.check_kline_quality_4h --symbol BTCUSDT --interval 4h --limit 100 --trigger-source cli --send-alert
```

说明：

1. 该脚本是人工 CLI 检查入口。
2. 本阶段不得创建 scheduler 调用该脚本。
3. 未来如需 scheduler 调用，应在 scheduler plan 中明确传入 `--trigger-source scheduler` 或直接调用 service。
4. 该脚本不得承载核心业务逻辑，核心逻辑必须在 `app/market_data/kline_quality/` 内。

## 24. 测试要求

建议创建：

`tests/test_kline_quality_checker.py`

默认测试不得依赖真实 Binance、真实 MySQL、真实 Redis、真实 Hermes。

至少覆盖：

1. KlineQualityIssue 可正常构造。
2. KlineQualityReport 可正常构造。
3. 批次连续性检查可通过合法连续 K线。
4. 批次连续性检查可发现缺失 K线。
5. 批次连续性检查可发现重复 open_time。
6. 未收盘检查可识别未收盘 K线。
7. data_source 与 trigger_source 映射异常可被识别。
8. db_checker 可通过 mock repository 发现 DB gap。
9. db_checker 可通过 mock repository 发现字段冲突。
10. integrity_checker 可通过 mock Binance client 和 mock repository 比对最近 N 根。
11. integrity_checker 发现 mismatch 后生成 failed report。
12. service 可以写入 data_quality_check repository mock。
13. service 可以在异常时调用 alerting mock。
14. service 不会写入 market_kline_4h。
15. service 不会自动修复 K线。
16. service 不会自动回补 K线。
17. migration 只创建 `data_quality_check` 表。
18. 默认测试不请求真实 Binance。
19. 默认测试不连接真实 MySQL。
20. 默认测试不发送真实 Hermes。
21. 默认测试不连接 Redis。
22. 默认测试不调用 DeepSeek。
23. 默认测试不涉及交易接口。

如果需要真实集成测试，必须使用显式开关，例如：

```
RUN_KLINE_QUALITY_INTEGRATION_TESTS=true
```

默认 `pytest` 不应访问真实外部服务。

## 25. 日志要求

本阶段必须复用：

`app/core/logger.py`

允许记录：

1. 检查开始。
2. 检查完成。
3. 检查失败。
4. 检查范围。
5. 检查数量。
6. 问题数量。
7. 首个问题类型。
8. Binance REST 复核失败。
9. 数据库读取失败。
10. data_quality_check 写入失败。
11. Hermes 报警发送结果。

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

## 26. 异常要求

本阶段应复用或扩展 `app/core/exceptions.py`。

允许新增异常：

1. `KlineQualityError`
2. `KlineIntegrityCheckError`
3. `KlineContinuityError`
4. `KlineDataMismatchError`
5. `KlineUnclosedError`

异常要求：

1. 检查失败必须明确失败原因。
2. Binance REST 失败必须明确是外部请求失败。
3. 数据库读取失败必须明确是存储异常。
4. report 生成失败必须明确是内部质量检查异常。
5. 异常消息不得包含敏感信息。
6. 不得因为异常自动修复 K线。
7. 不得因为异常自动回补 K线。

禁止新增：

1. OrderError。
2. PositionError。
3. TradeExecutionError。
4. AutoTradingError。
5. StrategySignalError。

## 27. 数据库影响

本阶段允许：

1. 创建 `data_quality_check` SQLAlchemy model。
2. 创建 `data_quality_check` Alembic migration。
3. 创建 `data_quality_check` repository。
4. 写入 K线质量检查记录。
5. 读取 `market_kline_4h` 用于质量检查。

本阶段禁止：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动执行 migration。
5. 创建 collector_event_log 表。
6. 创建策略表。
7. 创建建议表。
8. 人工修复 K线字段。
9. 自动修复 K线字段。

## 28. Redis 影响

本阶段不得连接 Redis。

本阶段不得写 Redis。

本阶段不得读取 Redis。

本阶段不得创建：

`bitcoin_price`

价格监控和 Redis 写入应在后续 WebSocket 价格监控阶段实现。

## 29. Binance 影响

本阶段允许在完整性复核中调用：

1. `BinanceRestClient.get_klines()`
2. `BinanceRestClient.get_server_time()`

用途：

1. 获取最近 N 根官方 K线用于和数据库对比。
2. 获取 Binance server time 用于判断未收盘 K线。

禁止调用：

1. REST 最新价格接口。
2. WebSocket。
3. order endpoint。
4. account endpoint。
5. position endpoint。
6. leverage endpoint。
7. margin endpoint。
8. listenKey。

## 30. Hermes 影响

本阶段允许在质量检查异常时调用 Hermes。

要求：

1. 必须通过 `app/alerting` 调用。
2. 必须使用固定模板。
3. 不得在底层 Binance client 中直接报警。
4. 不得在 MySQL repository 中直接报警。
5. 不得调用 DeepSeek。
6. 不得生成交易建议。
7. 报警内容必须说明“不自动修复、不自动回补、不修改正式 K线表”。

如果 Hermes 发送失败：

1. 应记录日志。
2. 应更新 `data_quality_check` 中的报警状态，如已创建检查记录。
3. 不得因此修改正式 K线表。
4. 不得因此重试无限次。

## 31. Scheduler 影响

本阶段不得实现 scheduler。

本阶段不得创建定时任务。

本阶段只提供未来 scheduler 可调用的 service。

未来每日一次 K线复核应由 scheduler plan 明确实现：

```
scheduler
    ↓
app/market_data/kline_quality/service.py::run_recent_kline_integrity_check
    ↓
Binance REST /fapi/v1/klines
    ↓
MySQL market_kline_4h
    ↓
data_quality_check
    ↓
异常时 app/alerting → Hermes
```

本阶段不创建上述 scheduler job。

## 32. WebSocket 和价格监控边界

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

## 33. K线不可人工修改原则

本阶段必须严格遵守：

1. 不允许 manual_repair。
2. 不允许 human_edit。
3. 不允许 manual_input。
4. 不允许 system_repair。
5. 不允许人工直接修改 K线字段。
6. 不允许程序自动修复正式 K线。
7. 不允许复核任务自动修改正式 K线。
8. 不允许质量检查任务静默覆盖冲突 K线。

即使数据出现问题，也只能由后续手动 CLI 回补任务通过 Binance REST 官方接口重新获取官方已收盘 K线，并按规则写入。

本阶段只检查、记录、报警，不修复。

## 34. 交易安全边界

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

## 35. 交付物要求

本阶段完成后，Codex 必须交付：

1. K线质量检查类型定义。
2. 纯规则检查模块。
3. 批次质量检查模块。
4. 数据库对比检查模块。
5. 最近 N 根完整性复核模块。
6. 质量检查 service。
7. 检查报告格式化模块。
8. `data_quality_check` SQLAlchemy model。
9. `data_quality_check` Alembic migration。
10. `DataQualityCheckRepository`。
11. 手动 K线质量检查脚本。
12. K线质量检查测试文件。
13. 对应的模块说明文件。

模块说明文件必须放在：

`docs/implementation/07_kline_quality_checker.md`

说明文件必须描述：

1. 本模块入口。
2. 批次检查流程。
3. 数据库对比检查流程。
4. 最近 N 根完整性复核流程。
5. `data_quality_check` 表结构。
6. 检查结果写入流程。
7. Hermes 报警流程。
8. 不自动修复、不自动回补、不修改正式 K线表的边界。
9. `check_trigger_source` 的含义。
10. 本模块不负责的边界。
11. 后续哪些模块会复用本模块。

本阶段 implementation 文档必须遵守 `AGENTS.md` 中的“代码可读性与实现说明强制要求”，按功能写清楚入口文件、方法调用链、数据流、异常处理、测试方式和本模块边界。

本阶段说明文件不需要描述：

1. K线正式写入流程。
2. K线手动回补写库流程。
3. K线定时采集写库流程。
4. scheduler job 定义。
5. Redis 价格缓存流程。
6. WebSocket 价格监控流程。
7. 策略建议流程。

原因：这些能力本阶段不实现。

## 36. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_kline_quality_4h --symbol BTCUSDT --interval 4h --limit 100 --trigger-source cli` 可以在配置正确时运行。
2. `pytest` 默认可以运行成功。
3. 默认测试不请求真实 Binance。
4. 默认测试不连接真实 MySQL。
5. 默认测试不连接 Redis。
6. 默认测试不发送真实 Hermes。
7. `data_quality_check` migration 只创建检查记录表。
8. 未创建 collector_event_log 表。
9. 未创建策略表。
10. 未创建建议表。
11. 未修改 `market_kline_4h` 表结构。
12. 未写入 `market_kline_4h` 正式 K线表。
13. 未修改 `market_kline_4h` 正式 K线表。
14. 未删除 `market_kline_4h` 正式 K线。
15. 批次连续性检查可以发现缺失。
16. 批次连续性检查可以发现重复。
17. 未收盘检查可以识别未收盘 K线。
18. DB 对比检查可以发现字段冲突。
19. 完整性复核可以对比 Binance REST 和数据库 K线。
20. 异常报告可以调用 `app/alerting` mock。
21. 不调用 DeepSeek。
22. 不实现 scheduler。
23. 不实现 WebSocket。
24. 不写入 Redis `bitcoin_price`。
25. 不实现交易建议。
26. 不实现交易执行相关代码。
27. `docs/implementation/07_kline_quality_checker.md` 已创建或补齐。

## 37. 人工审查清单

合并前用户应人工检查：

1. 查看 migration 是否只创建 `data_quality_check` 表。
2. 查看是否修改了 `market_kline_4h` 表结构。
3. 查看 quality checker 是否只检查、记录、报警，不修复。
4. 查看是否存在写入 `market_kline_4h` 的代码。
5. 查看是否存在修改 `market_kline_4h` 的代码。
6. 查看是否存在删除 `market_kline_4h` 的代码。
7. 查看是否存在 manual_repair / human_edit / manual_input / system_repair。
8. 查看 Hermes 报警是否通过 `app/alerting`。
9. 查看是否调用 DeepSeek。
10. 查看是否实现 scheduler。
11. 查看是否实现 WebSocket。
12. 查看检查脚本是否只作为人工 CLI 入口。
13. 查看测试是否默认 mock 外部服务。
14. 运行测试。
15. 运行检查脚本。

建议搜索：

```
grep -R "manual_repair" app scripts tests migrations
grep -R "human_edit" app scripts tests migrations
grep -R "manual_input" app scripts tests migrations
grep -R "system_repair" app scripts tests migrations
grep -R "collector_event_log" app scripts tests migrations
grep -R "strategy" app scripts tests migrations
grep -R "advice" app scripts tests migrations
grep -R "DeepSeek" app scripts tests
grep -R "openai" app scripts tests
grep -R "bitcoin_price" app scripts tests
grep -R "websocket" app scripts tests
grep -R "ticker/price" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果搜索结果只是文档、注释或允许的说明，需要人工判断；如果出现真实业务调用，应拒绝合并。

还需要重点检查是否存在类似：

```
update market_kline_4h
delete from market_kline_4h
insert into market_kline_4h
```

如果这些出现在质量检查模块中，应拒绝合并。

## 38. Codex 禁止事项汇总

Codex 在本阶段禁止：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动修复 K线。
5. 自动回补 K线。
6. 静默覆盖冲突 K线。
7. 创建 collector_event_log 表。
8. 实现 K线自动采集。
9. 实现 K线手动回补写库流程。
10. 实现 scheduler。
11. 调用 DeepSeek。
12. 生成交易建议。
13. 写入 Redis。
14. 创建 `bitcoin_price`。
15. 实现 WebSocket。
16. 实现 REST 最新价格查询。
17. 添加 manual_repair。
18. 添加 human_edit。
19. 添加 manual_input。
20. 添加 system_repair。
21. 实现任何交易执行代码。
22. 自动执行 Alembic migration。
23. 提交真实密钥。
24. 提交真实日志。
25. 提交 `.env`。
26. 删除、清空或覆盖已有文档。
27. 把核心检查逻辑写进 `scripts`。

## 39. 完成后的人工 Git 操作建议

以下操作由用户人工执行，不要求 Codex 自动执行：

1. 查看变更：

   git status
   git diff

2. 运行测试：

   pytest

3. 运行 K线质量检查脚本：

   python -m scripts.check_kline_quality_4h --symbol BTCUSDT --interval 4h --limit 100 --trigger-source cli

4. 人工确认 migration 没有创建越界表。

5. 人工确认没有写入、修改、删除正式 K线表的代码。

6. 人工确认没有异常删除、覆盖或越界实现。

7. 用户确认无问题后再提交：

   git add .
   git commit -m "完成 4h K线质量检查能力"

8. 用户自行推送分支，并进入代码审查流程。