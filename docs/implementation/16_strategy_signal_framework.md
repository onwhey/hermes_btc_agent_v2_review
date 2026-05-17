# 16 Strategy Signal Framework 实现说明

## 1. 本阶段实现模块

本阶段新增 `app/strategy/`，打通以下链路：

```text
scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::run_strategy_signals
    -> app/strategy/snapshot_resolver.py::SnapshotResolver.ensure_latest_snapshot
    -> app/strategy/input_builder.py::StrategyInputBuilder.build_input_from_snapshot
    -> app/strategy/runner.py::StrategyRunner.run_strategies
    -> app/strategy/result_repository.py::StrategySignalResultRepository.create_strategy_signal_run_with_results
```

新增模块：

1. `app/strategy/types.py`：StrategyEvaluationInput、StrategySignal、run/request/result DTO、状态枚举和持久化 payload。
2. `app/strategy/base.py`：BaseStrategy 统一策略接口。
3. `app/strategy/registry.py`：从 `configs/strategies/` 加载启用策略并校验名称、版本和类型。
4. `app/strategy/runner.py`：逐个运行独立策略，隔离单个策略异常。
5. `app/strategy/input_builder.py`：基于第 15 阶段 snapshot_id 只读还原 base / higher K线窗口。
6. `app/strategy/snapshot_resolver.py`：实现 ensure_latest_snapshot，复用或懒生成 MarketContextSnapshot。
7. `app/strategy/result_repository.py`：只写 `strategy_signal_run` / `strategy_signal_result`。
8. `app/strategy/signal_service.py`：策略信号主服务入口。
9. `app/strategy/strategies/`：初始三个策略。
10. `scripts/run_strategy_signals.py`：人工 CLI 入口，只解析参数并调用 app service。
11. `configs/strategies/`：策略注册和参数配置。

本阶段新增 Alembic migration：

`migrations/versions/20260518_16_create_strategy_signal_tables.py`

## 2. 为什么采用快照懒生成

第 16 阶段不安排单独 MarketContextSnapshot 定时任务。策略信号运行前通过 `ensure_latest_snapshot`：

1. 先计算当前理论最新已收盘 base / higher K线 open_time_ms。
2. 查询是否已有覆盖该窗口的 `status=created` 快照。
3. 如果快照可还原且质量状态合格，直接复用。
4. 如果没有合格快照，调用第 15 阶段 `MarketContextSnapshotService` 生成。
5. 如果生成结果为 blocked / failed，策略运行直接 blocked。
6. 不允许回退使用旧快照。

这样避免 04:05 已生成快照、04:06 再运行策略时重复生成等价快照，也避免把快照生成变成单独 scheduler 职责。

## 3. ensure_latest_snapshot

入口文件：

`app/strategy/snapshot_resolver.py`

入口方法：

`SnapshotResolver.ensure_latest_snapshot()`

读取数据库表：

1. `market_context_snapshot`
2. `market_kline_4h`
3. `market_kline_1d`

写入数据库表：

1. 默认不写。
2. 无可复用快照时，会调用第 15 阶段 `build_market_context_snapshot()`，由第 15 阶段服务在 confirm-write 模式下写入 `market_context_snapshot`。

不请求外部接口，不读取 Redis，不写入 Redis，不发送 Hermes，不调用 DeepSeek 或任何大模型，不读取账户，不读取持仓，不修改正式 K线表。

## 4. StrategyEvaluationInput 构造

入口文件：

`app/strategy/input_builder.py`

入口方法：

`StrategyInputBuilder.build_input_from_snapshot()`

职责：

1. 校验 snapshot 存在。
2. 校验 `snapshot.status = created`。
3. 校验 symbol / base_interval / higher_interval 与请求一致。
4. 通过第 15 阶段 repository 的 `restore_snapshot_kline_windows()` 只读还原 4h / 1d K线窗口。
5. 将当前 4h 映射为 `base_klines`，将 1d 映射为 `higher_klines`。
6. 校验还原数量和排序由第 15 阶段只读还原能力负责。

策略只能使用 `StrategyEvaluationInput`，不得自行查询最新 K线、请求 Binance、修复数据或写数据库。

## 5. BaseStrategy / Registry / Runner

`BaseStrategy` 定义统一接口：

```python
def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
    ...
```

`StrategyRegistry` 读取：

```text
configs/strategies/strategy_registry.yaml
configs/strategies/trend_structure_strategy.yaml
configs/strategies/volatility_risk_strategy.yaml
configs/strategies/gann_placeholder_strategy.yaml
```

并校验：

1. 策略名称不重复。
2. 策略类继承 `BaseStrategy`。
3. 策略版本存在。
4. 禁用策略不运行。

`StrategyRunner` 职责：

1. 加载所有启用策略。
2. 逐个运行策略。
3. 单个策略异常时返回该策略 `strategy_status=failed`，不影响其他策略。
4. 汇总 run status：`success`、`partial_success`、`blocked`、`failed`。

Runner 不写数据库，不发送 Hermes，不请求 Binance，不调用大模型。

## 6. 初始三个策略

### 6.1 TrendStructureStrategy

文件：

`app/strategy/strategies/trend_structure_strategy.py`

职责：

1. 使用 base K线窗口计算短/中均线位置。
2. 识别近期高低点结构。
3. 输出 `direction_bias`、`risk_level`、`signal_strength`、`reason_codes` 和中文 `reason_text`。

本策略只输出独立结构信号，不输出开仓、平仓、止盈、止损、仓位、杠杆或最终建议。

### 6.2 VolatilityRiskStrategy

文件：

`app/strategy/strategies/volatility_risk_strategy.py`

职责：

1. 使用 base K线窗口计算近期振幅比例。
2. 判断波动风险分层。
3. 输出 `risk_level`，`direction_bias=not_applicable`。

本策略不输出“停止交易”“必须空仓”等最终行动指令。

### 6.3 GannPlaceholderStrategy

文件：

`app/strategy/strategies/gann_placeholder_strategy.py`

职责：

1. 保留未来江恩策略扩展位。
2. 固定返回 `strategy_status=not_implemented`。
3. 明确说明本阶段不输出江恩判断。

本阶段不伪造江恩分析、不输出江恩买卖点、不输出江恩价格目标。

## 7. 数据库表结构

### 7.1 strategy_signal_run

记录一次策略信号运行批次。

字段：

```text
id
run_id
snapshot_id
symbol
base_interval_value
higher_interval_value
status
trigger_source
strategy_count
success_count
failed_count
invalid_count
not_implemented_count
blocked_reason
error_message
trace_id
started_at_utc
finished_at_utc
created_at_utc
updated_at_utc
```

索引：

1. `UNIQUE(run_id)`
2. `INDEX(snapshot_id)`
3. `INDEX(symbol, base_interval_value, higher_interval_value, created_at_utc)`
4. `INDEX(status, created_at_utc)`
5. `INDEX(trace_id)`

### 7.2 strategy_signal_result

记录每个策略的独立信号输出。

字段：

```text
id
run_id
snapshot_id
symbol
base_interval_value
higher_interval_value
strategy_name
strategy_version
strategy_status
direction_bias
risk_level
signal_strength
reason_codes_json
reason_text
metrics_json
debug_json
error_message
trace_id
created_at_utc
updated_at_utc
```

索引：

1. `INDEX(run_id)`
2. `INDEX(snapshot_id)`
3. `INDEX(strategy_name, strategy_version)`
4. `INDEX(strategy_status)`
5. `INDEX(direction_bias)`
6. `INDEX(risk_level)`
7. `INDEX(trace_id)`

结果表不保存完整 K线数组，不保存最终交易建议，不保存大模型输出。

## 8. StrategySignalService

入口文件：

`app/strategy/signal_service.py`

入口方法：

`run_strategy_signals()`

职责：

1. 接收 `StrategySignalRunRequest`。
2. 校验 `snapshot_id` 与 `ensure_latest_snapshot` 二选一。
3. 校验 `trigger_source=cli`。
4. snapshot-id 模式下使用指定快照。
5. ensure-latest 模式下先调用 `SnapshotResolver.ensure_latest_snapshot()`。
6. 调用 `StrategyInputBuilder.build_input_from_snapshot()`。
7. 调用 `StrategyRunner.run_strategies()`。
8. dry-run 只返回结果，不写 `strategy_signal_run` / `strategy_signal_result`。
9. confirm-write 写入 run 和 result 表。
10. 捕获异常并返回 structured failed / blocked 结果。

本 service 不请求外部接口，不读取 Redis，不写入 Redis，不发送 Hermes，不调用 DeepSeek 或任何大模型，不读取账户，不读取持仓，不修改正式 K线表。

## 9. dry-run 与 confirm-write

dry-run：

1. 不写 `strategy_signal_run`。
2. 不写 `strategy_signal_result`。
3. 会执行 snapshot 校验、输入构建和策略计算。
4. 如果使用 ensure-latest 且当前没有可复用 snapshot，resolver 会调用第 15 阶段 snapshot service 生成前置 MarketContextSnapshot；这是策略输入的事实快照，不是策略结果表。

confirm-write：

1. 非 dry-run 写入必须显式传入 `--confirm-write`。
2. 写入 `strategy_signal_run`。
3. 对每个策略写入一条 `strategy_signal_result`。
4. repository 不 commit，commit/rollback 由 service 控制。
5. 持久化失败时回滚并返回 `status=failed`。

## 10. CLI 入口

文件：

`scripts/run_strategy_signals.py`

使用已有 snapshot：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <created_snapshot_id> \
  --trigger-source cli \
  --dry-run
```

确保最新 snapshot 后运行：

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```

CLI 只解析参数、创建 request、打开 session、调用 `app/strategy/signal_service.py::run_strategy_signals` 并输出摘要。CLI 不直接查表、不直接写表、不请求 Binance、不调用大模型、不发送 Hermes、不生成最终交易建议。

## 11. 配置

策略配置文件：

```text
configs/strategies/strategy_registry.yaml
configs/strategies/trend_structure_strategy.yaml
configs/strategies/volatility_risk_strategy.yaml
configs/strategies/gann_placeholder_strategy.yaml
```

MarketContextSnapshot lookback 默认仍读取第 15 阶段配置：

```text
MARKET_CONTEXT_4H_LOOKBACK_COUNT=180
MARKET_CONTEXT_1D_LOOKBACK_COUNT=365
```

CLI 可通过 `--lookback-base` / `--lookback-higher` 覆盖本次 ensure-latest 使用的数量。

## 12. 异常处理

1. 参数非法：`StrategySignalService` 返回 `status=failed`、`exit_code=1`，不写库。
2. snapshot 缺失、非 created、不可还原：返回 `status=blocked`、`exit_code=2`。
3. ensure-latest 生成 snapshot blocked / failed：策略运行 blocked，不回退旧快照。
4. 策略配置非法：返回 `status=blocked`、`blocked_reason=strategy_config_invalid`。
5. 单个策略异常：Runner 隔离为该策略 `strategy_status=failed`，其他策略继续运行。
6. 持久化异常：service rollback，返回 `status=failed`、`exit_code=4`。

## 13. 本阶段不负责

本阶段只生成独立策略信号。
本阶段不生成最终交易建议。
本阶段不调用 DeepSeek。
本阶段不调用任何大模型。
本阶段不发送 Hermes 策略提醒。
本阶段不读取账户。
本阶段不读取持仓。
本阶段不自动交易。
本阶段不请求 Binance。
本阶段不修改正式 K线表。
本阶段不安排单独 MarketContextSnapshot 定时任务。
本阶段不接入 scheduler。
本阶段不创建建议生命周期表、策略复盘表、大模型分析表或关键证据 K线表。

## 14. 后续扩展边界

后续如果扩展日线主策略，应先评估 MarketContextSnapshot 是否需要支持新的 base / higher 映射，不应在第 16 阶段重构第 15 阶段表结构。

后续如果接入建议生命周期，应单独设计建议聚合层。第 16 阶段的 `StrategySignal` 不是最终建议。

后续如果接入大模型分析，应只在独立模型分析阶段进行，不得让第 16 阶段策略信号框架调用 DeepSeek、GPT、Claude 或其他大模型。

## 15. 测试

默认测试不请求真实 Binance、不连接真实 MySQL、不连接真实 Redis、不发送真实 Hermes、不调用 DeepSeek、不访问交易接口。

运行：

```bash
python -m pytest tests/strategy
python -m scripts.check_project_invariants
```

如环境允许：

```bash
python -m pytest
```

用户可手动运行：

```bash
python -m scripts.run_strategy_signals \
  --snapshot-id <created_snapshot_id> \
  --trigger-source cli \
  --dry-run
```

```bash
python -m scripts.run_strategy_signals \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --ensure-latest-snapshot \
  --trigger-source cli \
  --dry-run
```
