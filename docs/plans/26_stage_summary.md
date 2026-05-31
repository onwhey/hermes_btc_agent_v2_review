# 26 阶段总结：上线后观察与策略证据质量闸门

## 1. 阶段定位

26 阶段用于解决策略主链路上线后的三个问题：

```text
1. 链路有没有正常跑
2. 策略证据有没有资格继续使用
3. 后续复盘应该使用哪一条正式样本
```

26 阶段不做策略开发，不做模型优化，不做完整复盘，不做后台。

---

## 2. 26A：策略链路运行观测

### 2.1 目标

26A 用于只读观察最近 N 根已入库 4h K线对应的策略 pipeline 状态。

它回答：

```text
这根 4h K线有没有对应 pipeline？
pipeline 跑到哪一步？
是 healthy、missing、duplicate、failed，还是 expected_blocked？
模型关闭导致的 blocked 是否属于合理阻断？
```

### 2.2 已实现能力

新增 CLI：

```bash
python -m scripts.check_strategy_pipeline_status \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 5
```

检查链路：

```text
K线
→ 25 pipeline
→ SSR
→ SEA
→ AMP
→ MRAG
→ ADVR
```

状态分类：

```text
healthy
expected_blocked
failed
missing
duplicate
unknown
```

### 2.3 边界

26A 不请求 Binance REST。  
26A 不判断 K线是否漏采。  
26A 不调用大模型。  
26A 不发送 Hermes。  
26A 不修改数据库。  
26A 不阻断主链路。

K线连续性和质量仍由 07/11 负责。

### 2.4 验收结论

26A 已通过。

---

## 3. 26B：策略证据质量闸门

### 3.1 目标

26B 用于防止 active 策略缺失、失败或关键证据不完整时，系统继续把残缺证据传给 18/20/21。

核心规则：

```text
任何正常运行策略缺失或出错 = 重大事故
必须阻断后续主链路
必须触发 Hermes 系统告警
```

### 3.2 正常运行策略定义

第一版按配置判断：

```text
enabled=true
且 maturity_stage=active
且满足以下任一条件：
- participation_mode=decision_participant
- can_veto=true
```

排除：

```text
enabled=false
experimental / internship / observe_only
decision_weight=0 的占位策略
gann_placeholder
```

### 3.3 主链路位置

26B 插入位置：

```text
16/17 策略信号
→ 23F/24 策略证据聚合
→ 26B 策略证据质量闸门
→ 18 材料包
→ 20 模型审查
→ 21 建议生命周期
```

如果 26B failed：

```text
不进入 18
不进入 20
不进入 21
不调用大模型
不生成 advice
不自动交易
```

### 3.4 已验证能力

已完成正向链路验证：

```text
26B status=passed
should_block_pipeline=false
继续进入 18
后续因真实模型关闭合理 blocked 于 20C
```

已完成失败路径验证：

```text
临时制造 support_resistance 缺少核心字段
26B status=failed
should_block_pipeline=true
current_step=26b_strategy_evidence_quality_gate
material_pack_id 为空
未进入 18/20/21
Hermes critical 告警被触发
Hermes 超时不回滚数据库
```

已完成审计幂等修复：

```text
26B 质量记录按 pipeline_run_id 独立记录
同一个 SEA 被不同 pipeline 复用时，不互相覆盖
quality_check_id 与 pipeline_run_id 保持一致
```

### 3.5 已知测试污染记录

测试期间曾制造一条历史脏记录：

```text
quality_check_id 仍包含旧 pipeline id
pipeline_run_id 已被后续测试覆盖
```

该记录属于修复前测试污染，不代表当前代码仍有问题。

后续正式复盘应依赖 26C 的 canonical observation 规则，默认排除 cli 测试样本。

### 3.6 验收结论

26B 已通过。

---

## 4. 26C-A：策略链路观察索引

### 4.1 目标

26C-A 用于建立每根 4h K线的正式观察索引，为后续 28 复盘准备干净样本。

它回答：

```text
这一根 4h K线应该采用哪条正式 pipeline 作为观察样本？
哪些 pipeline 是手动测试样本，应排除？
26B 质量结果是什么？
是否进入模型审查？
是否生成 advice？
是否具备 advice 表现复盘资格？
```

### 4.2 新增表

新增表：

```text
strategy_pipeline_observation
```

唯一约束：

```text
UNIQUE(symbol, base_interval, higher_interval, kline_slot_utc)
```

一根 K线只保留一条 observation。

### 4.3 正式样本选择规则

默认规则：

```text
trigger_source=scheduler 可进入正式观察样本
trigger_source=cli 默认排除
```

同一 slot 多条 pipeline：

```text
1. 优先选择 scheduler
2. 多条 scheduler 时选择最新终态记录
3. CLI 不参与 canonical 选择
4. 只有 CLI 时标记 only_cli_runs
5. 没有 pipeline 时标记 missing_pipeline
```

### 4.4 状态

当前支持：

```text
missing_pipeline
only_cli_runs
pipeline_failed
quality_blocked
expected_blocked_by_model_config
model_review_completed
advice_generated
notification_prepared
notification_sent
unknown
```

### 4.5 CLI

新增 CLI：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10
```

默认 dry-run，不写库。

写库必须显式：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 10 \
  --confirm-write
```

### 4.6 验证结果

已验证：

```text
dry-run 不写库
confirm-write 第一次 created
重复 confirm-write updated
COUNT(*) = 10
未重复堆积 observation
scheduler pipeline 优先成为 canonical
cli pipeline 默认排除
missing_pipeline 正确识别
only_cli_runs 正确识别
expected_blocked_by_model_config 正确识别
```

当前表中典型样本：

```text
2026-05-31 08:00:00
canonical_pipeline_run_id = scheduler pipeline
observation_status = expected_blocked_by_model_config
eligible_for_review = 1
eligible_for_advice_performance_review = 0
duplicate_pipeline_count = 4
```

这说明 26C 正确选择正式 scheduler 样本，并排除了手动测试 pipeline。

### 4.7 边界

26C-A 不重新跑 16/23F/26B/18/20/21。  
26C-A 不调用大模型。  
26C-A 不发送 Hermes。  
26C-A 不请求 Binance。  
26C-A 不做胜率统计。  
26C-A 不做策略评分。  
26C-A 不做模型评分。  
26C-A 不做后台。

### 4.8 验收结论

26C-A 已通过。

---

## 5. 26 阶段整体结论

26 阶段主线已完成：

```text
26A 策略链路运行观测：通过
26B 策略证据质量闸门：通过
26C-A 策略链路观察索引：通过
```

阶段目标已经达到：

```text
1. 能观察 pipeline 是否运行
2. active 策略异常时能硬阻断
3. 能为后续复盘筛选正式样本
4. 能排除 cli 测试样本污染
5. 能保留 26B 质量结果与 pipeline/advice 链路关系
```

26 阶段可以收尾。

---

## 6. 暂缓内容

以下内容暂缓，不属于 26 当前收尾范围：

```text
26C-B advice 后市场表现原始记录
26C 自动 scheduler
26C Hermes 日报
完整胜率统计
策略降权 / 禁用建议
模型表现评分
后台 / 面板
人工修正入口
```

这些后续分别放到 28 或 30。

---

## 7. 下一阶段

下一大模块：

```text
27 弱模型 / 因子层
```

但进入 27 前，需要保持一个原则：

```text
不要因为 26 已经能观察链路，就误以为策略已经被证明有效。
```

26 只证明系统链路可追踪、质量闸门可阻断、复盘样本可筛选。  
策略是否有效，要等 28 复盘和更长期样本验证。
