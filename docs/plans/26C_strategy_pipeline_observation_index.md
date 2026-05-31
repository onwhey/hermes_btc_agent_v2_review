# 26C 策略链路观察索引与初步复盘基础计划

## 1. 阶段定位

26C 属于“上线后观察与初步复盘基础”，但它不是完整复盘系统。

它的核心目的：

```text
为每一根已入库 4h K线建立一条干净、可追溯、可复盘的策略链路观察索引。
```

它回答的是：

```text
这一根 4h K线之后，系统有没有正式跑 pipeline？
正式采用哪一条 pipeline_run？
这条 pipeline 经过了哪些阶段？
26B 是否通过或阻断？
是否进入模型审查？
是否生成最终 advice？
是否发送 Hermes？
后续 28 复盘应该用哪条正式样本？
```

一句话：

```text
26C 防止后续复盘拿到脏样本。
```

---

## 2. 与前后阶段关系

```text
26A：只读观察，告诉用户 pipeline 有没有跑、卡在哪里。
26B：质量闸门，active 策略异常时阻断 18/20/21 并告警。
26C：沉淀正式观察样本，把每轮链路串成复盘索引。
28：基于 26C 索引做真正的策略复盘与模型复盘。
```

26C 只整理已有结果，不重新跑任何业务阶段。

---

## 3. 核心原则

### 3.1 不重新跑链路

26C 不允许重新触发：

```text
16 策略信号
23F/24 策略证据聚合
26B 质量闸门
18 材料包
19/20 模型审查
21 建议生命周期
Hermes 策略通知
```

26C 只能读取已有结果并生成观察索引。

### 3.2 不调用大模型

26C 不调用 DeepSeek / GPT / Claude。

### 3.3 不发送 Hermes

26C-A 第一版不发送 Hermes。若后续 26C 任务自身失败，只记录执行日志，不在第一版增加通知能力。

### 3.4 不做完整复盘分析

26C 不做：

```text
胜率统计
盈亏比统计
策略降权建议
模型优劣评估
交易绩效评估
后台报表
```

这些归 28。

---

## 4. 正式样本选择规则

这是 26C 最重要的设计。

### 4.1 默认正式样本

默认只有：

```text
trigger_source=scheduler
```

的 pipeline 可以进入正式观察样本。

### 4.2 CLI 样本处理

```text
trigger_source=cli
```

默认不进入正式复盘样本，只保留在原始 pipeline/event 审计表中。

26C 不建设“测试样本管理系统”，只做最小排除：

```text
cli pipeline 默认 excluded_from_review
scheduler pipeline 默认 eligible_for_review
```

### 4.3 同一 slot 多条 pipeline

同一个：

```text
symbol + base_interval + higher_interval + kline_slot_utc
```

如果存在多条 pipeline：

1. 优先选择 `trigger_source=scheduler`。
2. 多条 scheduler 时，选择最新一条终态 pipeline。
3. CLI 记录默认不参与 canonical 选择。
4. 若只有 CLI 记录，则观察记录可标记为 `only_cli_runs`，但 `eligible_for_review=false`。
5. 若没有任何 pipeline，则标记 `missing_pipeline`。

### 4.4 终态优先级

同一 slot 多条 scheduler 记录时，建议优先级：

```text
success / advice_generated
quality_blocked
expected_blocked_by_model_config
failed
unknown
```

同级别再按 `created_at_utc` 最新选择。

注意：

```text
MODEL_REVIEW_REAL_MODEL_ENABLED=false 导致停在 20C/19/20A，应标记为 expected_blocked_by_model_config。
这不是失败样本，但也不能作为 advice 表现样本。
```

---

## 5. 观察状态设计

建议状态：

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

建议再增加两个布尔字段：

```text
eligible_for_review
eligible_for_advice_performance_review
```

区别：

```text
eligible_for_review=true：
可作为运行链路观察样本。

eligible_for_advice_performance_review=true：
必须有最终 advice，才能进入 advice 表现复盘。
```

例子：

```text
26B failed:
eligible_for_review=true
eligible_for_advice_performance_review=false

真实模型关闭导致 expected_blocked:
eligible_for_review=true
eligible_for_advice_performance_review=false

21 生成 advice:
eligible_for_review=true
eligible_for_advice_performance_review=true
```

---

## 6. 建议新增表

建议新增：

```text
strategy_pipeline_observation
```

### 6.1 核心字段

```text
id
observation_id
symbol
base_interval
higher_interval
kline_slot_utc
kline_open_time_prc
kline_close_time_utc
kline_close_time_prc

canonical_pipeline_run_id
canonical_trigger_source
canonical_reason
duplicate_pipeline_count
excluded_pipeline_run_ids_json

observation_status
eligible_for_review
eligible_for_advice_performance_review

pipeline_status
pipeline_current_step
pipeline_error_code
pipeline_error_message

strategy_signal_run_id
strategy_evidence_aggregation_id
evidence_quality_check_id
material_pack_id
model_analysis_run_id
review_aggregation_run_id
advice_id
review_id
alert_message_id

evidence_quality_status
evidence_quality_should_block
evidence_quality_failed_roles_json
evidence_quality_failed_strategies_json

model_review_invoked
model_review_reused
real_model_called
real_model_blocked_by_config

hermes_real_sent
notification_status

created_at_utc
updated_at_utc
details_json
```

### 6.2 唯一约束

```text
UNIQUE(symbol, base_interval, higher_interval, kline_slot_utc)
```

一根 K线只保留一条观察索引。所有重复 pipeline 不在 26C 重复建行，只放进 `excluded_pipeline_run_ids_json` 和原始审计表中。

---

## 7. 26B 结果必须纳入观察链

26C 必须读取并保存 26B 结果：

```text
evidence_quality_check_id
evidence_quality_status
evidence_quality_should_block
failed_roles
failed_strategies
missing_fields
alert_status
```

后续 28 要能回答：

```text
26B failed 是否避免了错误 advice？
26B warning/failed 的样本后续市场表现如何？
哪个 active 策略最常导致质量闸门失败？
```

如果 26B failed：

```text
observation_status=quality_blocked
eligible_for_review=true
eligible_for_advice_performance_review=false
```

---

## 8. CLI 设计

建议新增：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 20 \
  --dry-run
```

写库必须显式：

```bash
python -m scripts.build_strategy_pipeline_observations \
  --symbol BTCUSDT \
  --base-interval 4h \
  --higher-interval 1d \
  --limit 20 \
  --confirm-write
```

可选参数：

```text
--kline-slot-utc
--refresh-existing
--trigger-source cli
```

CLI 输出应展示：

```text
slot
canonical_pipeline_run_id
observation_status
eligible_for_review
eligible_for_advice_performance_review
26B 状态
模型状态
advice 状态
重复 pipeline 数量
排除原因
```

---

## 9. 26C-A 第一版范围

26C-A 只做：

```text
1. 新增 observation 表
2. 新增只读查询与索引构建 service
3. 新增 CLI dry-run / confirm-write
4. 按 scheduler 优先规则选择 canonical pipeline
5. 默认排除 cli pipeline
6. 串联 SSR / SEA / EQC / AMP / MRAG / ADVR / alert_message
7. 幂等 upsert observation
8. 测试覆盖
```

26C-A 不接 scheduler 自动任务。是否接入自动任务，等 26C-A 稳定后再决定。

---

## 10. 后续 26C-B 预留：市场后续表现原始记录

26C-B 可选，不属于 26C-A。

它只做原始市场表现采集，不做结论：

```text
advice 后第 1 根 / 第 2 根 / 第 3 根 4h K线
后续最高价
后续最低价
后续收盘价
是否触及目标区
是否触及失效区
```

但 26C-B 仍不做：

```text
胜率
盈亏比
策略优劣
模型评分
降权建议
```

这些放到 28。

---

## 11. 需要读取的数据来源

26C 可以读取：

```text
market_kline_4h
strategy_pipeline_event_log
strategy_signal_run
strategy_signal_result
strategy_evidence_aggregation_result
strategy_evidence_quality_check_result
strategy_aggregation_material_pack
model_review_aggregation_run
strategy_advice
strategy_advice_lifecycle_review
alert_message
```

不得请求 Binance REST。不得读取交易所账户。不得读取真实仓位。

---

## 12. 幂等规则

同一个：

```text
symbol + base_interval + higher_interval + kline_slot_utc
```

重复执行 26C：

```text
如果 observation 不存在，则创建。
如果 observation 存在，则更新 canonical 和阶段 ID。
不得重复创建 observation。
```

如果历史手动测试造成 duplicate pipeline：

```text
observation 只保留一个 canonical_pipeline_run_id
excluded_pipeline_run_ids_json 记录被排除项
duplicate_pipeline_count 记录数量
```

---

## 13. 测试要求

新增：

```text
tests/strategy_pipeline_observation/
```

至少覆盖：

1. scheduler pipeline 生成 canonical observation。
2. cli pipeline 默认不进入 canonical。
3. 同一 slot 多条 cli，状态为 only_cli_runs。
4. 同一 slot scheduler + cli，选择 scheduler。
5. 多条 scheduler，按终态优先级和时间选择 canonical。
6. 没有 pipeline，生成 missing_pipeline observation。
7. 26B passed 被写入 observation。
8. 26B failed 生成 quality_blocked，且 advice_performance 不 eligible。
9. 模型关闭导致 20C blocked，标记 expected_blocked_by_model_config。
10. advice 存在时，eligible_for_advice_performance_review=true。
11. 重复运行不重复创建 observation。
12. 26C 不调用模型。
13. 26C 不发送 Hermes。
14. 26C 不重新跑 16/23F/18/20/21。
15. CLI dry-run 不写库。
16. CLI confirm-write 才写库。

回归测试：

```bash
python -m pytest tests/strategy_pipeline_observation -q
python -m pytest tests/strategy_pipeline -q
python -m pytest tests/strategy_observability -q
python -m pytest tests/strategy_evidence_quality -q
python -m pytest -q
```

---

## 14. 验收标准

26C-A 验收通过条件：

```text
1. observation 表迁移成功。
2. dry-run 能输出最近 N 根 K线的观察索引候选。
3. confirm-write 能写入 observation。
4. 重复执行不产生重复 observation。
5. scheduler 样本优先，cli 样本默认排除。
6. duplicate pipeline 能正确记录 duplicate_count 和 excluded_pipeline_run_ids。
7. 26B passed / failed 能被 observation 正确引用。
8. 模型关闭的 expected_blocked 不被误判为失败。
9. 不调用大模型。
10. 不发送 Hermes。
11. 不重新触发任何业务阶段。
12. 全量 pytest 通过。
```

---

## 15. 明确禁止

26C 禁止：

```text
调用大模型
发送 Hermes
重新跑策略
重新生成材料包
重新生成模型审查
重新生成 advice
自动交易
读取账户
读取仓位
请求 Binance REST
做胜率统计
做策略评分
做模型评分
做后台报表
管理测试样本
```

---

## 16. 当前优先级

26C-A 当前只做“观察索引”。

不要一开始就做 26C-B 市场后续表现。先把正式样本和测试样本隔离清楚，否则 28 复盘数据会被污染。
