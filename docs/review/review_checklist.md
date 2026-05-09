# Review Checklist

本清单用于每个 plan 完成后人工审查。它不是业务规则本身；业务规则以 `docs/rules/project_invariants.md` 为准。

## 1. 通用检查

1. 是否只实现当前 plan。
2. 是否读取并遵守 `docs/rules/project_invariants.md`。
3. 是否新增或修改了不相关模块。
4. 是否删除、清空或覆盖已有文档。
5. 是否创建或更新对应 `docs/implementation/*.md`。
6. 是否有必要测试或检查脚本。
7. 是否运行 `pytest` 或当前阶段要求的检查命令。
8. 是否运行 `python -m scripts.check_project_invariants`。

## 2. 自动交易检查

不得出现：

1. 自动下单。
2. 自动平仓。
3. 自动调仓。
4. 自动加仓。
5. 自动减仓。
6. order endpoint。
7. account endpoint。
8. position endpoint。
9. leverage endpoint。
10. listenKey 或 private user data stream。

## 3. K线数据检查

1. 正式 4h K线是否只来自 Binance REST `/fapi/v1/klines`。
2. 是否只写入已收盘 K线。
3. 是否使用 Binance server time 判断收盘状态。
4. 是否禁止人工输入、人工修改、人工修复 K线。
5. 是否禁止自动修复、自动覆盖、自动删除异常 K线。
6. 写正式 K线前是否通过质量检查。
7. 写正式 K线前是否获取同一 `symbol + interval` 的任务锁。
8. 任务锁是否有 TTL。
9. 释放任务锁时是否校验 owner。
10. 是否记录 `collector_event_log`。

## 4. 10s 价格监控检查

1. 是否使用 Binance WebSocket `btcusdt@aggTrade`。
2. 是否禁止 REST 每 10 秒轮询价格。
3. 是否只写 Redis `bitcoin_price`。
4. Redis TTL 是否为 2 分钟。
5. 是否有报警阈值。
6. 是否有报警冷却。
7. 是否不写 `market_kline_4h`。
8. 是否不生成交易建议。
9. 是否不调用 DeepSeek。
10. 是否不是 scheduler 每 10 秒反复拉起脚本。

## 5. K线复核检查

1. 每日复核是否默认检查最近 100 根已收盘 4h K线。
2. 复核是否只比较 Binance REST 官方 K线与 DB 正式 K线。
3. 复核是否不写、不改、不删 `market_kline_4h`。
4. 复核发现异常是否 Hermes 报警。
5. 复核是否不自动回补、不自动修复。
6. 复核报警是否使用固定模板。
7. 复核是否不调用 DeepSeek。

## 6. 建议 grep

```bash
grep -R "manual_repair" app scripts tests
grep -R "human_edit" app scripts tests
grep -R "manual_input" app scripts tests
grep -R "system_repair" app scripts tests
grep -R "ticker/price" app scripts tests
grep -R "/fapi/v1/ticker" app scripts tests
grep -R "DeepSeek" app scripts tests
grep -R "openai" app scripts tests
grep -R "order" app scripts tests
grep -R "position" app scripts tests
grep -R "leverage" app scripts tests
grep -R "account" app scripts tests
```

如果危险词只出现在文档禁止事项中，可以保留；如果出现在代码正向实现中，必须解释或拒绝合并。
