# 11 Daily Kline Integrity Check Plan

## 1. 阶段目标

本阶段实现 4h K线一致性复核能力。

本阶段目标是：

1. 每日由 scheduler 定时触发一次自动复核。
2. 支持用户通过 CLI 手动触发指定范围复核。
3. 默认检查最近 100 根 BTCUSDT 4h 已收盘 K线。
4. 复核时重新请求 Binance REST 官方已收盘 K线。
5. 将 Binance REST 返回的官方 K线与数据库正式 K线按 `open_time_ms` 对齐比较。
6. 检查缺失、不连续、字段不一致、重复、未收盘误写入、非法 `data_source` 等问题。
7. 复核结果写入 `data_quality_check`，或者后续明确设计的 `kline_integrity_check_log`。
8. 发现异常时通过 Hermes 发送固定模板报警。
9. 复核任务本身不修复、不回补、不覆盖、不删除正式 K线。

本阶段是 K线一致性检查，不是 K线采集，也不是 K线回补。

---

## 2. 本阶段明确不做

本阶段禁止实现：

1. 写入 `market_kline_4h`。
2. 修改 `market_kline_4h`。
3. 删除 `market_kline_4h`。
4. 自动回补缺失 K线。
5. 自动覆盖字段不一致的 K线。
6. 自动修复不连续 K线。
7. 人工输入 K线字段。
8. `manual_repair`、`human_edit`、`manual_input`、`system_repair`。
9. 调用 DeepSeek 或其他大模型解释基础报警。
10. 生成策略建议。
11. 自动交易。
12. Binance 账户、订单、持仓、杠杆、保证金相关接口。

如果发现 K线异常，本阶段只报警，不修复。

---

## 3. 依赖文档

Codex 开始本阶段前必须阅读：

1. `docs/rules/project_invariants.md`
2. `docs/requirements/02_data_collection_requirements.md`
3. `docs/requirements/03_database_and_quality_requirements.md`
4. `docs/requirements/04_alerting_requirements.md`
5. `docs/architecture/data_flow.md`
6. `docs/architecture/module_boundaries.md`
7. `docs/decisions/0001-no-auto-trading.md`
8. `docs/decisions/0002-kline-source-and-time-rules.md`
9. `docs/decisions/0004-alerting-through-hermes.md`
10. `docs/plans/05_binance_rest_client.md`
11. `docs/plans/06_market_kline_4h.md`
12. `docs/plans/07_kline_quality_checker.md`
13. `docs/plans/08_4h_backfill.md`
14. `docs/plans/09_4h_incremental_collector.md`

本阶段必须复用：

1. `BinanceRestClient.get_server_time`
2. `BinanceRestClient.get_klines`
3. 4h K线 parser / DTO
4. K线字段校验能力
5. MySQL repository 查询能力
6. `data_quality_check` 记录能力
7. `app/alerting` Hermes 固定模板报警能力
8. scheduler 基础能力

---

## 4. 建议分支

建议分支名：

```text
feature/11-daily-kline-integrity-check
```

分支创建、切换、提交、推送、合并由用户人工执行。

Codex 不应自动创建、切换、合并、推送或删除 Git 分支。

---

## 5. 需要检查和补齐的目录

本阶段应检查以下目录是否存在，不存在才创建：

```text
app/market_data/kline_integrity/
app/scheduler/
scripts/
tests/
docs/implementation/
```

目录处理原则：

1. 已存在的目录只检查，不删除、不重建。
2. 不得清空已有 `docs/`。
3. 不得删除已有 requirements、architecture、decisions、plans。
4. 只允许补齐当前阶段缺失目录。

---

## 6. 需要检查和补齐的文件

本阶段建议检查和补齐：

```text
app/market_data/kline_integrity/__init__.py
app/market_data/kline_integrity/types.py
app/market_data/kline_integrity/kline_integrity_service.py
app/market_data/kline_integrity/kline_integrity_comparator.py
app/market_data/kline_integrity/kline_integrity_reporter.py

app/scheduler/jobs/daily_kline_integrity_check.py

scripts/check_kline_integrity.py

tests/test_daily_kline_integrity_check.py
docs/implementation/11_daily_kline_integrity_check.md
```

文件处理原则：

1. 如果文件已存在，Codex 必须先读取现有内容，再最小修改。
2. 不得直接覆盖已有文件。
3. 不得清空已有文件后重写。
4. 不得删除已有 README、AGENTS、docs 文档或配置文件。

---

## 7. 复核触发方式

本阶段支持两类触发：

```text
check_trigger = scheduler
check_mode = daily_integrity_check
```

用于每日自动复核。

```text
check_trigger = cli
check_mode = manual_integrity_check
```

用于用户手动复核。

禁止：

1. 缺少 `check_trigger` 仍执行。
2. 非法 `check_trigger` 仍执行。
3. 将复核任务伪装成采集任务。
4. 使用 `trigger_source` / `data_source` 表示复核写入来源。
5. 使用 `collection_mode = recheck`。

说明：复核不是采集，不写正式 K线表，因此不应产生新的 K线 `data_source`。

---

## 8. 每日自动复核要求

每日自动复核由 scheduler 触发。

默认参数：

```text
symbol = BTCUSDT
interval = 4h
lookback_count = 100
check_mode = daily_integrity_check
check_trigger = scheduler
compare_source = binance_rest
```

流程：

```text
scheduler 每日触发
    ↓
app/scheduler/jobs/daily_kline_integrity_check.py
    ↓
app/market_data/kline_integrity/kline_integrity_service.py
    ↓
计算最近 100 根已收盘 4h K线范围
    ↓
调用 Binance REST 获取官方已收盘 K线
    ↓
查询数据库中相同范围正式 4h K线
    ↓
按 open_time_ms 对齐比较
    ↓
生成复核结果
    ↓
写 data_quality_check 或 kline_integrity_check_log
    ↓
如果存在异常，调用 app/alerting 发送 Hermes 固定模板报警
    ↓
任务结束
```

每日自动复核不得写入、修改、删除 `market_kline_4h`。

---

## 9. CLI 手动复核要求

建议入口：

```text
scripts/check_kline_integrity.py
```

允许用户执行：

```bash
python -m scripts.check_kline_integrity --symbol BTCUSDT --interval 4h --check-trigger cli --lookback-count 100
```

本阶段只支持最近 N 根已收盘 K线复核，不实现 `--start-time` / `--end-time` 指定时间范围。
如果传入 `--start-time` 或 `--end-time`，CLI 必须返回参数错误，不进入 Binance、MySQL、Redis 或 Hermes 调用。

脚本只负责：

1. 解析参数。
2. 校验 `check_trigger`。
3. 初始化配置和日志。
4. 调用 `KlineIntegrityService`。
5. 输出复核摘要。

脚本不得：

1. 直接请求 Binance。
2. 直接写数据库。
3. 直接拼 SQL。
4. 接受任何人工输入的 OHLCV 字段。
5. 修复 K线。
6. 回补 K线。
7. 删除 K线。

---

## 10. 复核范围计算要求

每日复核默认检查最近 100 根已收盘 4h K线。

要求：

1. 必须使用 Binance server time 判断当前已收盘边界。
2. 不得仅依赖本机时间判断收盘。
3. 只检查已收盘 K线。
4. 不得把未收盘 K线纳入字段一致性比较。
5. 4h 相邻 K线 `open_time_ms` 差值必须为 `14400000`。
6. 查询 Binance REST 时应拉取足够数量的 K线，避免边界缺失。
7. 查询数据库时必须按 `open_time_ms` 对齐相同范围。

---

## 11. 比较规则

复核时必须检查：

1. REST 有、DB 没有：数据库缺失。
2. DB 有、REST 没有：数据库存在异常 K线或范围计算错误。
3. REST 与 DB 同一 `open_time_ms` 的 OHLCV 核心字段不一致。
4. DB K线时间不连续。
5. REST K线时间不连续。
6. DB 存在重复 `symbol + interval + open_time_ms`。
7. DB 存在未收盘 K线。
8. DB 存在非法 `data_source`。
9. DB 存在 `manual_repair`、`human_edit`、`manual_input`、`system_repair` 等禁止来源。
10. 字段存在非法值，例如 high < low、volume < 0、close_time 异常。

字段比较应使用 Decimal 或字符串转 Decimal 后比较，不得使用 float。

---

## 12. 复核结果记录要求

复核结果建议写入：

```text
data_quality_check
```

如果后续单独设计表，也可以写入：

```text
kline_integrity_check_log
```

记录内容至少包括：

1. trace_id。
2. symbol。
3. interval。
4. check_mode。
5. check_trigger。
6. compare_source。
7. lookback_count 或 start/end range。
8. checked_kline_count。
9. issue_count。
10. issue_summary。
11. started_at_utc / started_at_prc。
12. finished_at_utc / finished_at_prc。
13. status。
14. alert_sent。

如果 MySQL 不可用，必须写本地 emergency 日志，并尽量直接通过 Hermes 发送“复核结果无法完整记录”的系统报警。

---

## 13. Hermes 报警要求

复核发现异常时，必须通过 `app/alerting` 发送 Hermes 固定模板报警。

建议 alert type：

```text
kline_integrity_check_failed
```

报警内容必须包含：

1. trace_id。
2. symbol。
3. interval。
4. check_mode。
5. check_trigger。
6. compare_source。
7. checked_range。
8. issue_count。
9. issue_summary。
10. 是否已写入复核结果。
11. 明确说明系统没有自动修复。
12. 明确说明系统没有自动回补。
13. 明确说明系统没有自动交易。
14. 建议用户检查采集代码、调度器、数据库写入逻辑或 Binance REST 访问状态。

禁止：

1. 调用 DeepSeek 生成复核报警。
2. 把复核报警写成交易建议。
3. 报警后自动触发回补。
4. 报警后自动修改 K线。
5. 静默吞掉复核异常。

---

## 14. 任务并发要求

复核任务不写正式 K线表，因此不需要持有 K线写入锁。

但为了避免重复报警和重复消耗资源，仍应具备复核任务防重入能力。

规则：

1. 同一 `symbol + interval` 的复核任务不得并发运行。
2. scheduler 每日复核启动前应获取复核任务锁。
3. CLI 手动复核启动前也应获取复核任务锁。
4. 锁 key 示例：`kline_integrity_check:BTCUSDT:4h`。
5. `check_mode` 只进入 result details 或 quality metadata，不进入锁 key，避免 manual 与 scheduler 对同一 `symbol + interval` 并发复核。
6. 锁必须有 TTL。
7. 释放锁时必须校验 owner。
8. 获取锁失败时，本次任务应跳过或拒绝，并记录日志；如果有复核任务记录表，应记录 skipped。

注意：复核任务锁不是 K线写入锁，复核任务不得因持有锁而获得写入正式 K线表的权限。

---

## 15. 异常处理要求

必须处理以下异常：

1. Binance REST 请求失败。
2. Binance server time 获取失败。
3. MySQL 查询失败。
4. Redis 锁失败。
5. data_quality_check 写入失败。
6. Hermes 发送失败。
7. K线字段解析失败。
8. 时间范围计算失败。
9. 参数非法。

异常处理原则：

1. 复核失败不得假装成功。
2. 复核失败不得写入或修改正式 K线表。
3. 复核失败应记录日志。
4. 复核失败应尽量 Hermes 报警。
5. 异常信息不得包含密钥、token、webhook。

---

## 16. 测试要求

建议创建：

```text
tests/test_daily_kline_integrity_check.py
```

默认测试不得访问真实 Binance、真实 MySQL、真实 Redis、真实 Hermes。

至少覆盖：

1. 每日复核默认 lookback_count = 100。
2. CLI 缺少 `check_trigger` 时拒绝。
3. 非法 `check_trigger` 时拒绝。
4. 只比较已收盘 K线。
5. DB 缺失 K线能识别。
6. DB 多余 K线能识别。
7. 字段不一致能识别。
8. open_time_ms 不连续能识别。
9. 非法 `data_source` 能识别。
10. 禁止来源字段能识别。
11. 发现异常时调用 alerting mock。
12. 无异常时不报警。
13. 复核任务不写 `market_kline_4h`。
14. 复核任务不调用回补 service。
15. 复核任务不调用 DeepSeek。
16. 复核任务不涉及交易接口。
17. 复核任务锁获取失败时跳过或拒绝。

真实集成测试必须使用显式开关，例如：

```text
RUN_KLINE_INTEGRITY_INTEGRATION_TESTS=true
```

---

## 17. implementation 文档要求

本阶段完成后，Codex 必须创建：

```text
docs/implementation/11_daily_kline_integrity_check.md
```

说明文件必须描述：

1. 本模块入口。
2. scheduler job 入口文件和方法。
3. CLI 入口文件和参数。
4. 核心 service 文件和方法。
5. 每日 100 根 K线范围计算方法。
6. Binance REST 请求范围。
7. DB 查询范围。
8. REST 与 DB 对齐比较流程。
9. 问题分类。
10. data_quality_check 写入流程。
11. Hermes 报警流程。
12. 复核任务锁流程。
13. 异常处理流程。
14. 本模块不修复、不回补、不覆盖、不删除 K线的边界。
15. 对应测试和人工检查命令。

---

## 18. 验收标准

本阶段完成后，必须满足：

1. `python -m scripts.check_kline_integrity --help` 可以运行。
2. 缺少 `--check-trigger` 时拒绝执行。
3. 非法 `--check-trigger` 时拒绝执行。
4. scheduler job 能触发每日自动复核 service。
5. 默认检查最近 100 根已收盘 4h K线。
6. 复核使用 Binance REST 官方 K线作为对比源。
7. 复核按 `open_time_ms` 对齐比较。
8. 能识别缺失、不连续、字段不一致、非法来源等问题。
9. 发现异常时通过 `app/alerting` 发送 Hermes 固定模板报警。
10. 不写入、修改、删除 `market_kline_4h`。
11. 不自动回补。
12. 不自动修复。
13. 不调用 DeepSeek。
14. 不生成交易建议。
15. 不涉及交易接口。
16. 默认测试不访问真实外部服务。
17. `docs/implementation/11_daily_kline_integrity_check.md` 已创建或补齐。

---

## 19. 人工审查清单

合并前用户应人工检查：

1. 查看是否写入、修改、删除 `market_kline_4h`。
2. 查看是否自动触发回补。
3. 查看是否出现 `manual_repair`、`human_edit`、`manual_input`、`system_repair` 的正向使用。
4. 查看是否调用 DeepSeek。
5. 查看是否调用交易接口。
6. 查看是否通过 `app/alerting` 发送固定模板报警。
7. 查看是否默认检查最近 100 根已收盘 4h K线。
8. 查看是否使用 Binance REST 官方 K线作为对比源。
9. 查看 implementation 是否写清楚完整流程。
10. 运行测试。

建议搜索：

```bash
grep -R "manual_repair" app scripts tests
grep -R "human_edit" app scripts tests
grep -R "manual_input" app scripts tests
grep -R "system_repair" app scripts tests
grep -R "market_kline_4h" app/market_data/kline_integrity scripts/check_kline_integrity.py tests
grep -R "DeepSeek" app scripts tests
grep -R "openai" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
```

如果发现复核任务自动修复、自动回补、自动覆盖或自动删除正式 K线，应拒绝合并。

---

## 20. 当前分支验收补充

1. CLI 统一入口为 `scripts/check_kline_integrity.py`，人工运行命令为：

```bash
python -m scripts.check_kline_integrity --check-trigger cli --lookback-count 100
```

2. `--trigger-source` 和 `--limit` 仅作为兼容别名保留；文档和测试以 `--check-trigger`、`--lookback-count` 为主。
3. 本阶段只支持最近 N 根已收盘 K线复核，不实现 `--start-time` / `--end-time` 指定时间范围；如果传入范围参数，CLI 返回参数错误。
4. 复核锁 key 统一为 `kline_integrity_check:{symbol}:{interval_value}`，例如 `kline_integrity_check:BTCUSDT:4h`；`check_mode` 不进入锁 key。
5. `check_mode` 只进入 result details 或 quality metadata，用于审计，不用于区分并发锁。
6. 本分支只提供 `app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job` 作为 scheduler 可调用 job 入口；不新增常驻 scheduler runner，也不自动启动每日调度进程。
7. 正式调度接入时必须直接调用该 job 或 app service，不得调用 `scripts/check_kline_integrity.py`。
8. 触发来源由入口硬编码：CLI 构造 `check_trigger=cli`，scheduler job 构造 `check_trigger=scheduler`；不再通过环境变量配置触发来源。
