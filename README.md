# hermes_btc_agent_v2

Hermes + DeepSeek BTC 合约策略辅助系统。

本项目的当前阶段目标是先建设数据底座和运行底座：配置、日志、MySQL、Redis、Hermes 报警、Binance REST K线采集、K线质量检查、手动回补、增量采集、WebSocket 10s 价格监控、每日 K线一致性复核。

## 核心边界

1. 本系统不是自动交易系统。
2. 系统不得自动下单、平仓、调仓、加仓、减仓、撤单。
3. 正式 K线数据只能来自 Binance REST 官方 K线接口。
4. 10s 最新价格监控使用 Binance WebSocket，不使用 REST 每 10 秒轮询价格。
5. K线异常只能报警，不能人工改数，不能自动修复。
6. DeepSeek 和其他大模型不参与基础采集、基础报警、回补、复核、价格监控。

## 必读文档顺序

Codex 或其他 AI 编程助手开发前必须按顺序阅读：

1. `docs/rules/project_invariants.md`
2. `AGENTS.md`
3. 当前要实现的 `docs/plans/*.md`
4. 相关 `docs/decisions/*.md`
5. 相关 `docs/requirements/*.md`
6. 相关 `docs/architecture/*.md`
7. 前序阶段的 `docs/implementation/*.md`

## 文档目录

```text
docs/rules/          项目铁律
docs/requirements/   业务需求
docs/architecture/   系统结构、模块边界、数据流
docs/decisions/      已确认的重要决策
docs/plans/          Codex 施工蓝图
docs/implementation/ 每个模块完成后的实现说明
```

## 当前 plans

```text
01_project_skeleton.md
02_core_config_logging.md
03_infra_mysql_redis.md
04_alerting_through_hermes.md
05_binance_rest_client.md
06_market_kline_4h.md
07_kline_quality_checker.md
08_4h_backfill.md
09_4h_incremental_collector.md
10_price_monitor_10s.md
11_daily_kline_integrity_check.md
```

## Git 工作方式

1. 分支创建、切换、合并、推送由用户人工执行。
2. Codex 只在用户当前指定分支和当前指定 plan 范围内修改文件。
3. 每个 plan 完成后先审查，再合并到 `master`。
4. 如果发现文档冲突或规则不清，先修文档，不要硬写代码。

## 安全提醒

禁止提交 `.env`、真实密钥、真实 webhook、token、生产日志、账户信息、订单信息、持仓信息。
