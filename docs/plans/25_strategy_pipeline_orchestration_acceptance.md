# 25 策略链路统一编排验收记录

## 1. 当前验收状态

当前状态：

- 25A 手动 pipeline：已通过
- 25B scheduler 自动入口：已通过
- 25B 配置开关：已通过
- 18 already_exists AMP 复用修复：已通过
- 19 / 20 / 21 后半链路手动补测：已通过
- 最终 4h 自然调度观察：待完成

结论：

25 的代码功能验收已经通过，但最终 4h 自然调度观察尚未完成。最终观察通过前，暂不建议合并 master。

---

## 2. 关键提交

- 25B scheduler 自动入口与配置修复：`fda5e7d1dc4117dedeeee6ebc85a4f34467caa65`
- 18 already_exists AMP 复用修复：`eac1caf621ce551ec3c0b99e5d54bb0f80bafded`

---

## 3. 已验证内容

### 3.1 25A 手动 pipeline

已验证：

- 可手动执行 `scripts.run_strategy_pipeline`
- 可触发 17 / 16
- 可生成 SSR，即策略信号运行记录
- 可生成 23F 聚合结果 SEA
- 可生成 18 material pack，即 AMP
- 可进入 20C / 19 / 20A
- 可继续进入 21A / 21B

### 3.2 25B scheduler 自动入口

已验证：

- `STRATEGY_PIPELINE_SCHEDULER_ENABLED=true` 时，scheduler runner 在 09 采集成功后触发 25 pipeline
- runner 不再同时触发旧 17 自动链路
- 未出现旧 17 和新 25 双触发
- `kline_slot_utc` 来自 09 本轮采集结果，不由 25 自己猜测

### 3.3 配置开关

已验证：

- `STRATEGY_PIPELINE_AUTO_RUN_ENABLED` 已删除
- `STRATEGY_PIPELINE_SCHEDULER_ENABLED` 是唯一 scheduler 自动触发 25 的开关
- 默认不真实调用模型
- 默认不确认模型成本
- 默认不真实发送 Hermes

真实模型自动调用必须同时满足：

- `STRATEGY_PIPELINE_REAL_MODEL_ENABLED=true`
- `MODEL_REVIEW_REAL_MODEL_ENABLED=true`
- `STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST=true`

真实 Hermes 发送必须同时满足：

- `STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=true`
- `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=true`

### 3.4 18 already_exists 复用

已验证：

当 18 已存在 `success` / `partial_success` 的 AMP 时，25 pipeline 不再 blocked，而是复用已有 AMP 继续后续链路。

修复前问题：

- 25 卡在 `18_material_pack`
- `error_code=skipped`
- `message=Stage-18 aggregation skipped: already_exists`
- `material_pack_id` 为空

修复后：

- 可复用已有 AMP
- 可继续进入 20C / 19 / 20A / 21

---

## 4. 当前安全模式配置

当前用于最终 4h 自然观察的配置：

```env
STRATEGY_PIPELINE_ENABLED=true
STRATEGY_PIPELINE_SCHEDULER_ENABLED=true
STRATEGY_EVIDENCE_AGGREGATION_ENABLED=true

STRATEGY_PIPELINE_REAL_MODEL_ENABLED=false
STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST=false
MODEL_REVIEW_REAL_MODEL_ENABLED=false

STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED=false
STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=false
```

含义：

- 允许 scheduler 自动触发 25
- 不真实调用 DeepSeek
- 不真实发送最终建议 Hermes

---

## 5. 待完成：最终 4h 自然调度观察

等待下一根 4h K线自动采集成功后，执行：

```bash
mysql -u hermes_btc_agent_bool -p hermes_btc_agent_true -e "
SELECT
  id,
  pipeline_run_id,
  status,
  current_step,
  kline_slot_utc,
  strategy_signal_run_id,
  strategy_evidence_aggregation_id,
  material_pack_id,
  review_aggregation_run_id,
  advice_id,
  review_id,
  real_model_called,
  hermes_real_sent,
  error_code,
  error_message,
  created_at_utc
FROM strategy_pipeline_event_log
ORDER BY id DESC
LIMIT 5\G
"
```

通过标准：

- 有新的 `SP-...` pipeline 记录
- `kline_slot_utc` 是最新已收盘 4h K线
- `strategy_signal_run_id` 非空
- `strategy_evidence_aggregation_id` 非空
- `material_pack_id` 非空
- `real_model_called=0`
- `hermes_real_sent=0`
- 不再卡在 `18_material_pack already_exists`

---

## 6. 合并 master 前条件

必须满足：

- pytest 通过
- 25A / 25B / 18 复用均已验证
- 最终 4h 自然调度观察通过

最终 4h 自然观察通过前，不合并 master。

---

## 7. 后续补充记录位置

最终 4h 自然调度观察完成后，在本文件追加：

- 观察时间
- 最新 `pipeline_run_id`
- 最新 `kline_slot_utc`
- `status`
- `current_step`
- `strategy_signal_run_id`
- `strategy_evidence_aggregation_id`
- `material_pack_id`
- `real_model_called`
- `hermes_real_sent`
- 结论：通过 / 不通过
