# 24D Advice / Hermes 展示策略证据链实现说明

## 1. 功能：21 advice 接入 23F 与 24C 摘要

### 1.1 发起方式

用户或 scheduler 沿用 21A 入口：

```bash
python -m scripts.run_strategy_advice --review-aggregation-run-id MRAG-xxx --trigger-source cli --confirm-write
```

### 1.2 入口与调用链

```text
scripts/run_strategy_advice.py::main
    ↓
app/strategy_advice/service.py::run_strategy_advice
    ↓
app/strategy_advice/repository.py::get_review_aggregation_run_by_id
    ↓
app/strategy_advice/repository.py::get_latest_strategy_evidence_aggregation
    ↓
app/strategy_advice/repository.py::list_model_reviews_for_material_pack
    ↓
app/strategy_advice/evidence_chain.py::build_evidence_chain_summary
    ↓
app/strategy_advice/notification_payload.py::build_notification_payload
    ↓
app/strategy_advice/repository.py::create_lifecycle_review
```

### 1.3 读取配置

本功能未新增配置开关。21A 原有 dry-run / confirm-write 行为保持不变。

### 1.4 数据库读取

读取表：

- `model_review_aggregation_run`：21A 原始输入。
- `strategy_evidence_aggregation_result`：读取当前 `strategy_signal_run_id` 对应的 23F 公共证据链摘要。
- `model_analysis_run` / `model_analysis_result`：读取当前 `material_pack_id` 下的 24C 审查尝试和结果。

本功能不读取 `strategy_signal_result.strategy_payload_json`，不读取任何具体策略私有 payload。

### 1.5 数据库写入

沿用 21A 既有写入：

- `strategy_advice`
- `strategy_advice_lifecycle_review`
- `strategy_advice_event`
- `strategy_advice_trade_setup`

新增证据链摘要写入既有 JSON 字段：

- `strategy_advice_lifecycle_review.notification_payload_json.evidence_chain_summary`
- `strategy_advice_lifecycle_review.notification_payload_json.strategy_evidence_chain`
- `strategy_advice_lifecycle_review.notification_payload_json.model_review_summary`

未新增数据库表，未新增 Alembic migration。

### 1.6 降级规则

- 缺 23F：payload 标记 `strategy_evidence_chain.source=missing` 和 `strategy_evidence_missing`，不伪装证据完整。
- 缺 24C：payload 标记 `model_review_summary.source=missing` 和 `model_review_missing`，不伪装模型已审查。
- `boundary_violation` / `parse_failed` / `schema_invalid` / `stale_data` / `real_model_disabled` 等结果只透明展示为 rejected，不作为可采用模型审查。
- `low_quality` / `missing_evidence_refs` 等质量问题标记为 `low_weight`，只低权重展示。
- `mock_review` 标记为 `test_only`，不会被描述成真实大模型审查。

### 1.7 本功能不负责

- 不重新跑 23F。
- 不重新生成 18 material pack。
- 不调用大模型。
- 不修改 24C prompt。
- 不修改 23B / 23C / 23D / 23E / 23F 算法。
- 不生成自动交易、订单、账户、持仓、杠杆相关能力。

## 2. 功能：Hermes 通知展示证据链

### 2.1 发起方式

沿用 21B 通知入口：

```bash
python -m scripts.send_strategy_advice_notification --review-id ADVR-xxx --trigger-source cli --confirm-write
```

### 2.2 调用链

```text
scripts/send_strategy_advice_notification.py::main
    ↓
app/strategy_advice/notification_sender.py::send_strategy_advice_notification
    ↓
app/strategy_advice/notification_repository.py::get_lifecycle_review_by_id
    ↓
app/strategy_advice/notification_renderer.py::render_strategy_advice_notification
    ↓
app/strategy_advice/notification_repository.py::create_alert_message
```

### 2.3 Hermes 内容

通知渲染会在原 21B 中文消息中追加短证据链：

- 23F `candidate_bias`
- 23F `decision_readiness`
- 关键策略贡献摘要
- 风控闸门摘要
- 24C `review_decision`
- 24C `evidence_quality`
- 24C `recommendation_to_advice_layer`
- 24C 是否可采用，以及不可采用原因

消息会被限制在 1500 字以内。每个策略最多一行摘要，模型反驳最多两条，缺失证据最多三条。

### 2.4 Hermes 与外部服务

本功能只复用 21B 既有 Hermes 发送链路。24D 本身不新增 Hermes 客户端、不直接发送 Hermes、不调用大模型。

## 3. 边界自检

- 自动交易：未实现。
- 账户 / 持仓 / 订单 / 杠杆接口：未实现。
- K线表：未修改。
- 23F：只读取结果，不重新运行。
- 24C：只读取结果，不调用模型。
- `strategy_payload_json`：未读取。
- migration：未新增。
- scheduler：未修改。
- Hermes：只通过 21B 既有统一链路展示。

## 4. 对应测试

- `tests/strategy_advice/test_strategy_advice_service.py`
- `tests/strategy_advice/test_strategy_advice_notification_sender.py`
- `tests/model_analysis`
- `tests/strategy_aggregation`
- `tests/strategy`

默认 pytest 不请求 Binance，不连接真实 MySQL/Redis，不发送真实 Hermes，不调用真实大模型。
