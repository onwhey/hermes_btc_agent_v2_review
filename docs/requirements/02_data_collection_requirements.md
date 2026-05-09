# BTCUSDT 行情数据采集需求

## 1. 文档目的

本文档定义 Hermes + DeepSeek BTC 合约策略辅助系统的数据采集层需求。

数据采集层是整个系统的第一基础层。

后续策略分析、DeepSeek 分析、微信提醒、回测验证、复盘统计，都依赖数据采集层提供可靠、连续、可追溯的行情数据。

本阶段的核心目标不是快速生成交易建议，而是先建立稳定、可信、可长期运行的数据基础。

## 2. 数据采集总体原则

数据采集层必须遵守以下原则：

1. 数据可靠性优先于采集速度。
2. 4h K线必须使用 Binance REST 已收盘 K线。
3. WebSocket 只用于实时价格监控，不作为 4h K线标准落库来源。
4. K线顺序必须根据 `open_time_ms` 或 `open_time_utc` 判断，禁止使用数据库自增 `id` 判断行情顺序。
5. 所有交易所原始时间以 UTC 为主。
6. 数据库中的时间字段必须优先保存 UTC 时间。
7. PRC 时间只能作为展示或辅助字段，不作为行情主判断依据。
8. 采集失败不能静默。
9. 数据缺口不能静默。
10. 不连续 K线不能直接进入主行情表。
11. 数据采集异常必须记录日志、写入数据库，并在必要时通过 Hermes 微信提醒用户。
12. 第一阶段不根据采集数据调用 DeepSeek 生成策略建议。

## 3. 数据来源

当前阶段主要使用 Binance BTCUSDT U 本位合约行情数据。

交易对：

`BTCUSDT`

市场类型：

`U 本位合约`

交易所：

`binance`

系统内部建议统一使用：

1. `exchange = binance`
2. `market_type = um_futures`
3. `symbol = BTCUSDT`

后续如果扩展其他交易所或其他交易对，不应破坏当前 BTCUSDT 主链路。

## 4. REST 和 WebSocket 的职责边界

### 4.1 REST 的职责

REST 用于获取标准化、可回补、可校验的行情数据。

REST 主要用于：

1. 历史 K线回补。
2. 增量 K线采集。
3. 获取已收盘 K线。
4. 数据缺口通过 Binance REST 回补。
5. 数据质量复查。
6. 获取服务器时间用于时间校验。

REST 是 K线主数据来源。

### 4.2 WebSocket 的职责

WebSocket 只用于实时价格监控。

WebSocket 主要用于：

1. 获取 BTCUSDT 最新价格。
2. 写入 Redis 短期价格状态。
3. 判断短时间价格波动。
4. 判断价格是否接近当前有效建议的关键区域。
5. 触发价格事件提醒。

WebSocket 禁止用于：

1. 自行拼接 4h K线并作为主行情入库。
2. 替代 Binance REST 已收盘 K线。
3. 作为策略主周期数据来源。
4. 高频交易。
5. 每 10 秒触发完整策略分析。

WebSocket 数据可以作为提醒依据，但不能作为主策略 K线依据。

## 5. K线周期规划

系统后续需要支持多个 K线周期，但不同周期的定位不同。

### 5.1 4h K线

4h 是当前系统的主策略周期。

第一阶段必须完整实现 4h K线采集链路。

4h K线用于：

1. 主趋势判断。
2. 主策略评估。
3. 每 4 小时建议更新。
4. 后续策略信号生成。
5. 后续 DeepSeek 分析输入。
6. 后续回测和复盘。

4h K线必须满足：

1. 使用 REST 获取。
2. 只保存已收盘 K线。
3. 入库前做连续性检查。
4. 缺口时不直接写入主表。
5. 缺口时触发采集异常记录和告警。
6. 排序和连续性判断必须基于 `open_time_ms`。
7. 不得基于数据库自增 `id` 判断顺序。

### 5.2 1day K线

1day K线用于大周期市场环境判断。

1day K线可以作为第一阶段可选扩展。

1day K线后续用于：

1. 判断大趋势。
2. 判断市场环境。
3. 区分牛市、熊市、震荡市。
4. 作为多策略环境过滤条件。
5. 辅助判断 4h 策略是否顺势。

1day 数据量较小，可以较早接入，但不得影响 4h 主链路稳定性。

### 5.3 1m K线

1m K线不是主策略周期。

1m K线主要用于复盘行情细节。

1m K线后续用于：

1. 分析插针。
2. 分析快速波动。
3. 分析价格提醒触发前后的走势。
4. 分析止损或止盈触发过程。
5. 分析 4h K线内部结构。
6. 对异常行情做证据保存。

第一阶段不要求一年级别全量采集 1m K线。

第一阶段可以：

1. 预留 1m K线采集结构。
2. 预留 1m K线专用表。
3. 采集最近短窗口数据。
4. 在价格事件触发时，后续保存事件前后若干小时 1m 数据。

不得因为 1m 数据量过大，拖慢 4h 主链路开发。

## 6. K线采集方式

系统应支持通用 K线采集能力。

可以抽象为：

`collect_klines(symbol, interval, limit)`

或类似形式。

但通用采集能力不代表所有周期混用同一张表。

代码层可以复用采集逻辑。

数据库层建议按周期分表。

例如：

1. `market_kline_1m`
2. `market_kline_4h`
3. `market_kline_1d`

这样做的原因：

1. 不同周期数据量差异很大。
2. 不同周期查询场景不同。
3. 不同周期索引压力不同。
4. 4h 是主策略周期，需要更严格的数据质量规则。
5. 后续 1m 数据增长很快，不应拖累 4h 查询。

## 7. 已收盘 K线规则

系统只能把已收盘 K线作为标准行情写入主 K线表。

对于 REST 返回的 K线，必须判断该 K线是否已经收盘。

判断原则：

1. 当前交易所服务器时间必须大于 K线 close_time。
2. 未收盘 K线不能写入主 K线表。
3. 未收盘 K线可以临时用于观察，但不得作为主策略依据。
4. 4h 主策略评估必须基于最新已收盘 4h K线。
5. 不能因为本地时间误差错误判断 K线是否收盘。

实现时应优先使用交易所服务器时间做收盘判断。

## 8. 历史回补需求

历史回补用于补齐过去的 K线数据。

历史回补应支持：

1. 指定交易对。
2. 指定周期。
3. 指定开始时间。
4. 指定结束时间。
5. 指定每批请求数量。
6. 自动分页。
7. 自动过滤未收盘 K线。
8. 自动去重。
9. 自动 upsert。
10. 自动记录回补事件。

历史回补必须具备幂等性。

同一段历史数据执行多次，不应产生重复数据。

唯一性应基于：

1. `exchange`
2. `market_type`
3. `symbol`
4. `interval`
5. `open_time_ms`

历史回补不得依赖数据库自增 `id` 判断顺序。

## 9. 增量采集需求

增量采集用于持续采集最新已收盘 K线。

4h 增量采集建议在每个 4h K线收盘后延迟一小段时间执行，避免交易所数据刚收盘时存在短暂延迟。

增量采集应支持：

1. 获取最近若干根 K线。
2. 过滤未收盘 K线。
3. 检查本批次 K线是否连续。
4. 检查本批次第一根 K线是否能与数据库最新 K线衔接。
5. 只在连续性通过后写入主 K线表。
6. 如果发现缺口，停止写入主表。
7. 如果发现缺口，记录采集事件。
8. 如果发现缺口，记录数据质量异常。
9. 如果发现缺口，通过 Hermes 微信提醒用户。
10. 如果 REST 请求失败，记录失败并提醒。

增量采集不能因为某次失败而静默跳过。

如果 12:00 的 4h K线漏采，16:00 才恢复，系统必须识别 12:00 缺口，而不是直接写入 16:00。

## 10. K线连续性规则

K线连续性必须基于时间判断。

对于 4h K线：

相邻两根 K线的 `open_time_ms` 差值必须等于 4 小时对应的毫秒数。

即：

`next.open_time_ms - current.open_time_ms = 4h_ms`

对于 1day K线：

相邻两根 K线的 `open_time_ms` 差值必须等于 1 天对应的毫秒数。

对于 1m K线：

相邻两根 K线的 `open_time_ms` 差值必须等于 1 分钟对应的毫秒数。

禁止使用数据库自增 `id` 判断 K线是否连续。

原因：

1. 后续回补可能导致 id 不连续。
2. 后续通过 Binance REST 回补历史数据或记录历史数据冲突，可能导致 id 顺序和行情时间不一致。
3. 数据库 id 只代表入库顺序，不代表行情顺序。
4. 策略分析必须以行情时间为准。

## 11. 缺口处理规则

发现 K线缺口时，系统不得直接把后续 K线写入主行情表。

例如数据库已有：

1. 04:00
2. 08:00

本次采集到：

1. 16:00

系统必须识别缺少：

1. 12:00

此时应该：

1. 停止写入 16:00 到主 K线表。
2. 记录采集事件。
3. 记录数据质量异常。
4. 触发 Hermes 微信提醒。
5. 等待手动触发或定时触发 Binance REST 回补任务补齐缺口。
6. 缺口通过 Binance REST 官方已收盘 K线回补完成后，再继续主链路。

这样做的目的：

1. 防止策略基于不连续行情做判断。
2. 防止历史复盘时无法解释策略依据。
3. 防止主策略周期数据被污染。

## 12. 脏数据处理规则

以下情况应视为异常数据：

1. 缺失 open_time。
2. 缺失 close_time。
3. 缺失 open_price / high_price / low_price / close_price。
4. high_price 小于 low_price。
5. high_price 小于 open_price 或 close_price。
6. low_price 大于 open_price 或 close_price。
7. volume 为负数。
8. quote_volume 为负数。
9. interval 不符合目标周期。
10. symbol 不符合目标交易对。
11. open_time_ms 不是目标周期边界。
12. K线未收盘却准备写入主表。
13. 同一批数据内部出现重复 open_time_ms。
14. 同一唯一键出现不同价格数据，且未明确记录变更原因。

异常数据不得直接进入主行情表。

异常数据应记录到数据质量检查结果中，并根据严重程度触发告警。

## 13. 数据回补与冲突记录规则

本项目不允许人工直接修改 K线数据。

无论是历史缺口、采集失败、数据异常，还是后续发现交易所返回数据发生变化，正式 K线表中的 K线值都只能来自 Binance REST 官方已收盘 K线。

允许的处理方式：

1. 定时增量采集通过 Binance REST 自动补齐短期漏采。
2. 用户手动触发回补脚本，由脚本调用 Binance REST 拉取官方已收盘 K线。
3. 系统针对指定时间范围重新拉取 Binance REST K线并执行幂等写入。
4. 如果重新拉取后发现同一根 K线关键字段与数据库已有记录不一致，默认不得覆盖正式 K线表，应记录数据冲突、写入数据质量检查结果，并通过 Hermes 报警。

禁止的处理方式：

1. 禁止人工直接修改 `open_price`、`high_price`、`low_price`、`close_price`。
2. 禁止人工直接修改 `volume`、`quote_volume`、`trade_count`、`taker_buy_volume`、`taker_buy_quote_volume`。
3. 禁止使用 `manual_repair` 作为 K线数据来源。
4. 禁止使用 `system_repair` 作为 K线数据来源。
5. 禁止将人工录入的价格、成交量或成交额写入正式 K线表。
6. 禁止静默覆盖关键行情字段。

数据回补与冲突记录应遵守以下原则：

1. 回补必须可追溯。
2. 重新拉取前后的数据变化应有记录。
3. 不应静默覆盖关键行情字段。
4. 如果同一根 K线重新获取后价格不同，应记录数据冲突状态。
5. 数据冲突需要进入采集事件、数据质量检查结果或审计记录。
6. 策略已经引用过的历史 K线，不应被无记录地修改。

后续策略建议如果已经基于某些 K线生成，应保存当时的行情快照。

不能只依赖主行情表当前值，否则后续行情冲突会破坏复盘依据。

## 14. Redis 数据需求

Redis 用于保存短期实时状态，不作为长期历史数据来源。

Redis 可以保存：

1. 最新价格。
2. 上一次价格。
3. 价格提醒冷却状态。
4. 最近一次提醒时间。
5. WebSocket 连接状态。
6. 采集任务短期运行状态。
7. 临时锁。
8. 去重 key。

例如实时价格 key 可以是：

`bitcoin_price`

但实际项目中建议后续逐步规范 key 命名，例如：

`market:price:binance:um_futures:BTCUSDT`

Redis 中的实时价格可以设置过期时间，例如 2 分钟。

Redis 数据过期不影响长期分析，因为长期数据必须保存在 MySQL 中。

## 15. MySQL 数据需求

MySQL 用于保存长期、可复盘、可审计的数据。

MySQL 应保存：

1. K线数据。
2. 采集事件日志。
3. 数据质量检查结果。
4. 提醒消息记录。
5. 后续策略信号。
6. 后续策略建议。
7. 后续策略运行快照。
8. 后续人工执行记录。
9. 后续复盘结果。

第一阶段至少需要支持：

1. 4h K线表。
2. 采集事件日志表。
3. 数据质量检查表。
4. alert_message 表。

后续可扩展：

1. 1m K线表。
2. 1day K线表。
3. strategy_run 表。
4. strategy_signal 表。
5. strategy_advice 表。
6. strategy_snapshot 表。

后续如进入清算压力、最小阻力方向、资金费率、持仓量等衍生品策略分析阶段，可扩展采集标记价格、指数价格、资金费率、持仓量、多空比例、强平事件等数据。

这些数据可作为未来策略层和复盘层的输入，但不属于第一阶段必须实现内容。

第一阶段仍以 Binance REST 已收盘 4h K线、数据质量检查和 Hermes 基础告警为主。

## 16. 时间字段规则

所有核心时间字段以 UTC 为准。

K线表必须保存：

1. `open_time_ms`
2. `open_time_utc`
3. `close_time_ms`
4. `close_time_utc`

其中：

1. `open_time_ms` 来自交易所原始数据。
2. `close_time_ms` 来自交易所原始数据。
3. UTC 时间字段由毫秒时间戳转换。
4. PRC 时间可以通过程序展示计算，不建议作为核心判断字段。

如果某些表后续需要 PRC 时间字段，只能作为展示或辅助字段，不能作为行情排序、连续性判断、策略判断依据。

## 17. 数据源标记与任务类型边界

每条正式 K线数据必须记录权威数据来源，并记录本次写入正式 K线表的实际触发来源。

本项目中的 `data_source` 不是“人工是否参与”的标签，而是“行情数值获取通道 + 实际触发来源”的审计标识。

对于 4h 主 K线表，`data_source` 第一阶段只允许以下值：

1. `binance_rest_by_scheduler`：定时任务或受控任务以 `trigger_source = scheduler` 触发 Binance REST 写入。
2. `binance_rest_by_cli`：用户手动命令行以 `trigger_source = cli` 触发 Binance REST 写入。

这两个值都表示：K线 OHLCV 等核心数值只能来自 Binance REST 官方已收盘 K线。区别只在于实际触发来源不同。

触发来源必须显式记录，不得由程序自动猜测。

如果通过脚本触发采集任务，脚本必须携带 `--trigger-source` 参数：

1. `--trigger-source scheduler`
2. `--trigger-source cli`

`data_source` 的取值由 `trigger_source` 决定：

1. `trigger_source = scheduler` 时，写入 `data_source = binance_rest_by_scheduler`。
2. `trigger_source = cli` 时，写入 `data_source = binance_rest_by_cli`。

是否经过 `scripts/*.py` 文件不是判断依据；实际触发来源才是判断依据。

因此，以下值不得作为 4h 主 K线表的 `data_source`：

1. `manual_repair`
2. `system_repair`
3. `binance_websocket`
4. `manual_input`
5. `human_edit`
6. `binance_rest_backfill`
7. `binance_rest_incremental`

必须严格区分以下任务：

1. 增量采集
2. 手动回补
3. K线一致性复核

其中：

1. 增量采集和手动回补可能写入正式 4h K线表。
2. K线一致性复核只检查数据质量，不写入、不修复、不覆盖正式 4h K线表。

如果需要区分写入正式 K线表的采集任务目的，应在采集事件日志中记录 `collection_mode`。

第一阶段 `collection_mode` 只建议允许：

1. `incremental`
2. `manual_backfill`
3. `historical_backfill`

示例：

1. 定时任务通过 scheduler、cron、APScheduler 或受控脚本触发增量采集：
   - `trigger_source = scheduler`
   - `data_source = binance_rest_by_scheduler`
   - `collection_mode = incremental`

2. 用户命令行手动触发一次增量采集：
   - `trigger_source = cli`
   - `data_source = binance_rest_by_cli`
   - `collection_mode = incremental`

3. 用户命令行手动回补缺口：
   - `trigger_source = cli`
   - `data_source = binance_rest_by_cli`
   - `collection_mode = manual_backfill`

4. 初始化或较长历史区间回补：
   - `trigger_source = cli`
   - `data_source = binance_rest_by_cli`
   - `collection_mode = historical_backfill`

`collection_mode` 不得替代 `data_source`，也不得成为允许人工修改 K线值的理由。

K线一致性复核不得使用 `collection_mode = recheck`。复核任务不是采集任务，不应写入正式 K线表，也不应伪装成回补任务。

复核任务应单独记录检查语义，例如：

1. `check_mode = daily_integrity_check`
2. `check_mode = manual_integrity_check`
3. `check_trigger = scheduler`
4. `check_trigger = cli`
5. `compare_source = binance_rest`

WebSocket 来源不得写入 4h 主 K线表。

如果后续保存 WebSocket 事件，应写入单独事件表或提醒表，不应混入标准 K线表。

## 18. 采集事件日志

系统应记录采集事件。
采集事件日志是写入数据库的业务审计记录，用于追踪行情采集、回补、缺口、失败、重新拉取、数据冲突等事件。

采集事件日志不等同于系统文件日志。

系统文件日志包括 `logs/app.log`、错误日志、运行日志等，其日志级别、敏感信息过滤、按天轮转和 30 天保留规则，属于基础设施日志系统要求，后续在基础设施计划文档中单独定义。

采集事件包括：

1. 历史回补开始。
2. 历史回补完成。
3. 增量采集开始。
4. 增量采集完成。
5. REST 请求失败。
6. REST 响应异常。
7. 数据解析失败。
8. K线不连续。
9. 发现缺口。
10. 数据冲突或重新拉取结果不一致。
11. 写库成功。
12. 写库失败。
13. WebSocket 连接成功。
14. WebSocket 断开。
15. WebSocket 重连。

采集事件至少应包含：

1. 事件类型。
2. 事件级别。
3. 触发来源 `trigger_source`。
4. 数据来源 `data_source`。
5. 采集任务类型 `collection_mode`。
6. 交易所。
7. 市场类型。
8. 交易对。
9. 周期。
10. 开始时间。
11. 结束时间。
12. 影响的数据范围。
13. 请求参数摘要。
14. 返回数量。
15. 写入数量。
16. 跳过数量。
17. 错误信息。
18. 事件详情 JSON。
19. 创建时间。

`trigger_source` 必须显式记录。

允许值：

1. `scheduler`
2. `cli`

要求：

1. `trigger_source` 不得为空。
2. `trigger_source` 不得由程序猜测。
3. 通过脚本触发采集时，必须显式传入 `--trigger-source`。
4. `trigger_source` 必须与 `data_source` 保持一致。
5. `trigger_source = scheduler` 时，`data_source` 必须为 `binance_rest_by_scheduler`。
6. `trigger_source = cli` 时，`data_source` 必须为 `binance_rest_by_cli`。

## 19. 数据质量检查

数据质量检查用于判断行情数据是否可以被策略层使用。

第一阶段至少检查：

1. K线是否连续。
2. K线是否重复。
3. K线是否缺失。
4. K线 OHLC 是否合理。
5. K线是否已收盘。
6. K线时间边界是否正确。
7. 数据源是否符合预期。
8. 最近一根 K线是否及时更新。

数据质量检查结果应写入数据库。

数据质量异常时，应根据严重程度触发提醒。

严重异常包括：

1. 4h K线缺口。
2. 最新 4h K线延迟未采集。
3. REST 连续失败。
4. 数据字段异常。
5. 主行情表写入失败。

如果 MySQL 不可用：
1. 不得假装已落库。
2. 必须写本地 emergency 日志。
3. 如 Redis 可用，可写短期 outbox / failure key。
4. 如 Hermes 可用，必须直接发送“数据库不可用”提醒。
5. MySQL 恢复后，可以由恢复任务补写故障摘要，但不得伪造原始发生时间。

## 20. 告警触发需求

数据采集层可以触发以下告警：

1. Binance REST 请求失败。
2. Binance REST 连续失败。
3. 4h K线缺口。
4. 4h K线不连续。
5. 4h K线延迟未采集。
6. 数据解析异常。
7. MySQL 写入失败。
8. Redis 写入失败。
9. WebSocket 断开或频繁重连。
10. 实时价格异常波动。
11. 实时价格触发关键价格区间。

告警应写入 `alert_message`。

告警应通过 Hermes Webhook 发送到个人微信。

告警发送结果应写入 `channel_response`。

## 21. 第一阶段数据采集验收标准

第一阶段数据采集层完成后，应满足：

1. 可以通过 REST 获取 BTCUSDT U 本位合约 K线。
2. 可以过滤未收盘 K线。
3. 可以回补历史 4h K线。
4. 重复执行历史回补不会产生重复数据。
5. 可以增量采集最新已收盘 4h K线。
6. 可以识别 4h K线缺口。
7. 发现 4h K线缺口时不写入后续主表数据。
8. 发现 4h K线缺口时写入采集事件日志。
9. 发现 4h K线缺口时写入数据质量检查结果。
10. 发现 4h K线缺口时触发 Hermes 微信提醒。
11. 可以通过 WebSocket 获取实时价格。
12. 实时价格可以写入 Redis。
13. Redis 实时价格 key 有过期时间。
14. 价格波动超过阈值时可以触发提醒。
15. 同一价格事件不会重复刷屏。
16. MySQL 保存长期数据。
17. Redis 只保存短期状态。
18. 所有核心时间判断基于 UTC。
19. K线排序、连续性判断、缺口判断，必须基于 `open_time_ms` 或 `open_time_utc`，不能基于数据库自增 `id`。
20. 4h 主行情表只能写入 Binance REST 已收盘 K线，不能使用 WebSocket 数据自行拼接 4h K线后写入主行情表。

## 22. 手动触发 Binance REST K线回补

系统允许提供命令行脚本，由用户手动触发指定交易对、周期、时间范围的 K线回补。

手动触发回补不等于人工修改 K线数据。脚本必须调用 Binance U 本位合约 REST 接口获取官方已收盘 K线，并按系统统一规则解析、校验、幂等写入。

手动 CLI 回补写入正式 K线表时：

- data_source = binance_rest_by_cli
- collection_mode = manual_backfill

禁止行为：

- 禁止人工录入 K线价格、成交量、成交额。
- 禁止人工直接修改正式 K线表的 open_price、high_price、low_price、close_price、volume、quote_volume 等核心字段。
- 禁止使用 manual_repair、system_repair、human_edit、manual_input 作为 K线数据来源。