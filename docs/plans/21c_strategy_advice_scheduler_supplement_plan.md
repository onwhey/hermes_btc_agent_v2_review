# 21C 补充计划：Strategy Advice Scheduler 自动链路

> 文件性质：本文件是 `docs/plans/21_strategy_advice_lifecycle_plan.md` 的 21C 补充计划。  
> 目的：补充 21C 的 scheduler 自动链路、MRAG 处理、幂等、失败恢复、Hermes 重试、锁和日志规则。  
> 本文件不推翻 21A / 21B 已完成设计，只补充 21C 自动化实现细节。

---

## 1. 阶段定位

21C 是 **strategy_advice 自动调度链路**。

21C 的职责是：

```text
scheduler runner 在 20 生成 model_review_aggregation_run 后
  -> 触发 21C
  -> 21C 调用 21A 生成建议生命周期
  -> 21C 调用 21B 准备或发送通知
  -> 记录自动任务执行日志
```

21C 不是新的策略层，不是新的模型审查层，不是新的通知渲染层。

21C 不允许：

```text
不调用 19
不直接请求 DeepSeek / GPT / Claude
不直接消费 analysis_material_pack 生成建议
不扫描未处理 material_pack
不重新实现 20 的模型复用、过期、chain 管理
不重新实现 21A 的 lifecycle 判断
不重新实现 21B 的通知渲染和发送逻辑
不自动交易
不读取账户
不下单
```

---

## 2. 主链路触发规则

21C 的主触发点：

```text
scheduler runner 在 20 生成可用的 model_review_aggregation_run 后触发 21C。
```

21C 的主输入是明确的：

```text
review_aggregation_run_id = MRAG-xxx
```

正式链路：

```text
4h K线采集成功
  -> 17 调度 16
  -> 16 使用 / 确保 15 快照
  -> 18 生成 analysis_material_pack
  -> 20 生成 model_review_aggregation_run
  -> scheduler runner 触发 21C(review_aggregation_run_id=MRAG-xxx)
  -> 21C 调用 21A
  -> 21C 调用 21B
```

注意：

```text
17 不直接调用 18
18 不直接调用 20
20 不直接调用 21
阶段串联由 scheduler runner 负责
```

---

## 3. MRAG 与 21 生命周期关系

`model_review_aggregation_run` 是 20 阶段结果表。  
它本身不代表已经进入建议生命周期。

21 生命周期从下面记录生成时才开始：

```text
strategy_advice_lifecycle_review.source_review_aggregation_run_id = MRAG-xxx
```

因此：

```text
MRAG 已生成，但没有 lifecycle_review
= 20 已完成，但 21 尚未处理
```

21C 不在 `model_review_aggregation_run` 表上新增简单的 `consumed=true/false` 状态。  
原因是“消费”语义不够明确，无法区分：

```text
正式生成 advice
continue active advice
wait_without_active_advice
skip stale MRAG
21A 成功但 21B 失败
Hermes 成功 / 失败
```

正确做法是由 21 侧记录处理结果。

---

## 4. MRAG 处理规则：最新正式处理，旧的审计跳过

21C 不需要提前知道数据库里有多少条未处理 MRAG。

每次执行时，按业务维度查询：

```text
symbol
base_interval
higher_interval
```

然后：

1. 找到当前最新 MRAG。
2. 查询一批尚未被 21 处理过的 MRAG。
3. 最新 MRAG 才能进入正式 21A/21B。
4. 旧 MRAG 只能写 stale skip 审计记录。

### 4.1 未处理 MRAG 判断

判断一条 MRAG 是否被 21 处理过：

```text
是否存在：
strategy_advice_lifecycle_review.source_review_aggregation_run_id = 当前 MRAG
```

存在：已处理。  
不存在：未处理。

建议增加数据库唯一约束：

```text
UNIQUE(strategy_advice_lifecycle_review.source_review_aggregation_run_id)
```

避免同一个 MRAG 被重复生成 lifecycle_review。

### 4.2 最新 MRAG

如果未处理 MRAG 是当前最新 MRAG：

```text
调用 21A
生成 strategy_advice_lifecycle_review / strategy_advice / event / trade_setup / notification_payload_json
再调用 21B
准备或发送通知
```

### 4.3 旧 MRAG

如果未处理 MRAG 不是当前最新 MRAG：

```text
不调用正式 21A 建议生成逻辑
不调用 21B 通知
不创建 strategy_advice
不创建 strategy_advice_trade_setup
不发送 Hermes
不影响 active advice
不关闭 active advice
只新增 skip_stale_review_aggregation 审计记录
```

### 4.4 stale skip 记录

旧 MRAG 应写入一条 `strategy_advice_lifecycle_review`：

```text
source_review_aggregation_run_id = 旧 MRAG
lifecycle_action = skip_stale_review_aggregation
lifecycle_reason = 旧 MRAG 已被更新 MRAG 覆盖，跳过生成建议
reviewed_advice_id = NULL
result_advice_id = NULL
previous_advice_id = NULL
notification_required = false
notification_reason = stale MRAG skipped, no user notification
```

并建议写一条 `strategy_advice_event`：

```text
event_type = stale_review_aggregation_skipped
advice_id = NULL
related_review_id = 上述 lifecycle_review.review_id
event_reason = 旧 MRAG 被最新 MRAG 覆盖
event_payload_json = {
  "stale_review_aggregation_run_id": "MRAG-xxx",
  "superseded_by_review_aggregation_run_id": "MRAG-newer",
  "notification_required": false
}
```

该记录只是审计，不是建议。

---

## 5. partial_success 规则修正

注意：当前 20A 没有 `partial_success` 状态。  
20A 状态主要是：

```text
success
blocked
failed
skipped
```

`partial_success` 主要来自 18 阶段，表示材料包生成成功，但策略输入不完整。

例如：

```text
某个 enabled=true 的策略 not_implemented
某个策略 failed
某个策略 invalid
```

如果江恩策略 `enabled=false`，它不参与本轮，不应导致 partial_success。

21C 允许 18 的 partial_success 后续链路继续，但通知里必须继承并展示材料/策略输入不完整的风险信息。  
21C 不应错误写成“20A partial_success”。

---

## 6. 20 状态准入规则

21C 只处理已经存在的 `model_review_aggregation_run`。

建议第一版规则：

```text
20 status = success
  -> 允许进入 21A/21B

20 status = blocked
  -> 可进入 21A 生成 wait / stop_trading / blocked 类生命周期结果
  -> 通知中必须明确模型审查被阻断原因
  -> 不得伪装成正常模型审查

20 status = failed
  -> 默认不生成正式 advice
  -> 记录 21C scheduler failed / skipped 日志
  -> 不通知，除非后续专门设计系统异常告警

20 status = skipped
  -> 默认不生成正式 advice
  -> 记录 skipped 原因
```

如果后续实现发现 20A 当前 blocked 输出不足以安全生成 21A payload，第一版可以先只允许 `success` 正式进入 21A/21B，其他状态只写 21C 调度日志，禁止硬编建议。

---

## 7. 21A / 21B 恢复规则

21C 恢复只恢复 21 侧链路，不补跑 20。

### 7.1 没有 MRAG

如果没有可用 MRAG：

```text
21C 不补跑 20
记录 skipped / blocked
reason = no_review_aggregation_run_available
```

### 7.2 MRAG 有了，21A 没跑

如果：

```text
model_review_aggregation_run 存在
但没有 strategy_advice_lifecycle_review.source_review_aggregation_run_id = MRAG-xxx
```

处理：

```text
如果该 MRAG 是最新 MRAG：
  调用 21A
  再调用 21B

如果该 MRAG 是旧 MRAG：
  写 skip_stale_review_aggregation
```

### 7.3 21A 已成功，21B 未成功

如果：

```text
strategy_advice_lifecycle_review.review_id = ADVR-xxx
notification_required = true
notification_payload_json 有内容
```

但没有成功通知：

```text
没有 alert_message.related_review_id = ADVR-xxx 且 status 成功
也没有 strategy_advice_event.event_type = notification_sent
```

则视为：

```text
21A 已完成
21B 未完成
```

处理规则：

```text
只补跑 21B
不重跑 21A
不重新生成 lifecycle_review
不重新创建 strategy_advice
不重新创建 trade_setup
不改变 active advice
```

---

## 8. 21A / 21B 幂等规则

### 8.1 21A 幂等

MRAG 级别：

```text
strategy_advice_lifecycle_review.source_review_aggregation_run_id
```

同一 MRAG 默认只能生成一条 lifecycle_review。  
建议数据库唯一约束：

```text
UNIQUE(source_review_aggregation_run_id)
```

### 8.2 21B 幂等

通知级别：

```text
review_id
alert_message.related_review_id
```

同一个 review_id 默认只能成功通知一次。

不得用 `advice_id` 阻断后续通知，因为同一个 active advice 可能对应多个 continue lifecycle_review，每轮都需要 brief 通知证明系统正常运行。

---

## 9. Hermes 失败重试规则

Hermes 通知失败只影响 21B 通知链路，不影响建议生命周期。

规则：

```text
失败后每 5 分钟重试一次
最多重试 3 次
只重试 21B
不重跑 21A
不重新生成 lifecycle_review
不重新创建 strategy_advice
不重新创建 trade_setup
不改变 active advice 状态
```

微信 24 小时窗口导致投递失败时，也只能记录通知失败，不能把 advice 标记为失败。

---

## 10. 配置开关与 trigger_source

21C 自动任务启用和 Hermes 真实发送必须使用独立 env 开关。

建议新增：

```env
STRATEGY_ADVICE_SCHEDULER_ENABLED=false
STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=false
```

含义：

```text
STRATEGY_ADVICE_SCHEDULER_ENABLED
= 是否允许 scheduler 自动触发 21C

STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED
= 是否允许 21C 自动真实发送 Hermes
```

运行规则：

```text
scheduler enabled=false
  -> 21C 不自动跑

scheduler enabled=true
notification send=false
  -> 21C 可自动生成 lifecycle_review / alert_message
  -> 不真实发送 Hermes

scheduler enabled=true
notification send=true
  -> 21C 可自动生成建议并真实发送 Hermes
```

`trigger_source` 必须保留并记录：

```text
cli        手动触发
scheduler  scheduler 自动触发
```

scheduler 自动链路必须传：

```text
trigger_source=scheduler
```

手动验证必须传：

```text
trigger_source=cli
```

21C 即使自动任务启用，也不能绕过 Hermes 真实发送开关。

---

## 11. 锁与并发控制

21C 并发控制采用：

```text
Redis 临时锁 + 数据库幂等兜底
```

Redis 锁 key 建议：

```text
strategy_advice_21c:{symbol}:{base_interval}:{higher_interval}:{review_aggregation_run_id}
```

示例：

```text
strategy_advice_21c:BTCUSDT:4h:1d:MRAG-xxx
```

作用：

```text
防止同一个 MRAG 被多个 scheduler 进程同时处理
```

锁应有 TTL，例如 5 至 10 分钟，避免进程崩溃后永久卡死。

但不能只靠 Redis 锁。  
数据库仍必须用：

```text
strategy_advice_lifecycle_review.source_review_aggregation_run_id
```

作为 MRAG 处理幂等兜底。

---

## 12. 21C 任务日志

21C 需要任务执行日志能力。

优先复用现有 scheduler 日志。  
如果现有日志无法表达 21C 执行状态，则新增轻量表：

```text
strategy_advice_scheduler_event_log
```

该表不是建议生命周期表，不用于判断策略对错，只用于排查：

```text
21C 有没有启动
是否被 env 开关阻断
是否拿到 Redis 锁
处理了哪个 MRAG
21A 是否完成
21B 是否完成
Hermes 是否失败
失败原因是什么
```

建议字段：

```text
id
event_id
job_name
symbol
base_interval
higher_interval
review_aggregation_run_id
trigger_source
status
reason
trace_id
started_at_utc
finished_at_utc
details_json
created_at_utc
```

建议状态：

```text
started
success
skipped
failed
lock_skipped
disabled
```

### 12.1 任务失败记录

如果失败发生在已有 review_id 之后：

```text
写 strategy_advice_event
related_review_id = ADVR-xxx
event_type = notification_failed / scheduler_failed / 具体失败类型
```

如果失败发生在 review_id 生成之前：

```text
写 scheduler event log
不能无声失败
```

---

## 13. 21 通知内容补充要求

21B 通知中必须明确展示当前使用的 MRAG：

```text
review_aggregation_run_id = MRAG-xxx
```

同时建议展示：

```text
material_pack_id = AMP-xxx
strategy_signal_run_id = SSR-xxx
snapshot_id = MCS-xxx
```

原因：用户必须能判断收到的是当前 4h 周期建议，还是旧记录恢复/复用结果。

旧 MRAG 的 stale skip 不发送 Hermes。

---

## 14. 建议新增 / 调整的入口

建议新增 CLI 入口用于手动验证 21C：

```bash
python -m scripts.run_strategy_advice_scheduler \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --trigger-source cli \
  --dry-run
```

可选指定 MRAG：

```bash
python -m scripts.run_strategy_advice_scheduler \
  --review-aggregation-run-id MRAG-xxx \
  --trigger-source cli \
  --dry-run
```

confirm-write：

```bash
python -m scripts.run_strategy_advice_scheduler \
  --review-aggregation-run-id MRAG-xxx \
  --trigger-source cli \
  --confirm-write
```

是否真实发送 Hermes 必须受 env 控制，不得只靠 CLI 绕过。

---

## 15. 测试要求

至少覆盖：

```text
1. scheduler enabled=false 时 21C skipped，不调用 21A/21B。
2. scheduler enabled=true 但 notification send=false 时，只准备通知，不真实发 Hermes。
3. scheduler enabled=true 且 notification send=true 时，允许调用 21B 真实发送路径；测试中必须 mock Hermes。
4. 传入最新 MRAG 且未处理时，调用 21A/21B。
5. 传入 MRAG 已有 lifecycle_review 时，不重复生成 lifecycle_review。
6. 多条未处理 MRAG 时，最新 MRAG 正式处理，旧 MRAG 写 skip_stale_review_aggregation。
7. 旧 MRAG stale skip 不创建 advice、不创建 trade_setup、不发送 Hermes、不影响 active advice。
8. 21A 已完成但 21B 未完成时，只补跑 21B，不重跑 21A。
9. Hermes 失败后按 5 分钟、最多 3 次规则重试。
10. 同一 review_id 已成功通知后，不重复发送。
11. Redis 锁已存在时任务 lock_skipped。
12. Redis 锁失效时数据库唯一约束仍防止重复 lifecycle_review。
13. trigger_source=cli / scheduler 均能记录。
14. 任务失败在 review_id 前后分别写入正确日志。
15. 通知 payload / message 中包含 review_aggregation_run_id。
16. 不调用 19。
17. 不调用真实大模型。
18. 不扫描 analysis_material_pack 生成建议。
19. 不自动交易。
20. 全量测试通过。
```

---

## 16. 验收标准

21C 通过的最低标准：

```text
1. 20 生成 MRAG 后，scheduler 可以触发 21C。
2. 最新 MRAG 可以自动进入 21A/21B。
3. 旧 MRAG 不会补发建议，只写 stale skip 审计。
4. 同一 MRAG 不会重复生成 lifecycle_review。
5. 同一 review_id 不会重复发送通知。
6. 21A 成功、21B 失败时，下次只补跑 21B。
7. Hermes 失败按 5 分钟、最多 3 次重试。
8. 21C 自动启用和 Hermes 真实发送由两个 env 开关分别控制。
9. trigger_source 能区分 cli 和 scheduler。
10. Redis 锁和数据库幂等都生效。
11. 21C 执行日志可排查“任务有没有跑、跑到哪一步、为什么没通知”。
12. 通知里明确显示 MRAG。
13. 不调用 19，不请求大模型，不自动交易。
```

---

## 17. 非目标

21C 不做：

```text
不新增策略
不增强江恩逻辑
不做复盘
不做人工作单
不读账户
不自动下单
不实现 Admin
不重新设计 20
不重新设计 21A / 21B
不补跑 18 / 19 / 20
```

---

## 18. Codex 实现提醒

Codex 开发时必须：

```text
先读 AGENTS.md
先读 docs/rules/project_invariants.md
先读 21 正式 plan
先读 21A / 21B implementation
不得执行 git checkout / git switch / git branch
不得提交真实密钥
不得修改正式 K线数据
```

21C 的核心不是“把代码接起来”，而是：

```text
不重复
不补发旧建议
失败可恢复
通知可追踪
自动化可关闭
```
