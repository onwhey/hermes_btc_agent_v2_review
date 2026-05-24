# 21 Strategy Advice Lifecycle Implementation

This document describes stage 21A and stage 21B. Stage 21A persists bounded
human strategy advice lifecycle state from an existing stage-20
`model_review_aggregation_run`. Stage 21B reads the 21A notification payload,
renders Chinese Hermes content, writes `alert_message` when confirmed, and only
sends Hermes when explicitly allowed. It does not implement 21C scheduler
automation.

## 1. Feature: CLI Strategy Advice Lifecycle Pass

### 1.1 Entry

Manual CLI only:

    python -m scripts.run_strategy_advice \
      --review-aggregation-run-id MRAG-xxx \
      --trigger-source cli \
      --dry-run

Confirm-write mode:

    python -m scripts.run_strategy_advice \
      --review-aggregation-run-id MRAG-xxx \
      --trigger-source cli \
      --confirm-write

`trigger_source` is restricted to `cli` in 21A. Scheduler entry is intentionally
not connected in this stage.

### 1.2 Entry File

`scripts/run_strategy_advice.py`

Entry method:

`main()`

The script parses CLI arguments, builds `StrategyAdviceRequest`, opens the
caller-owned MySQL session, calls the service, prints compact result lines, and
returns the service exit code. The script does not contain lifecycle state
machine logic, does not directly write business tables, does not send Hermes,
does not call model providers, does not modify formal Kline tables, and does
not perform trading.

### 1.3 Core Service

`app/strategy_advice/service.py`

Core methods:

- `StrategyAdviceService.run_strategy_advice()`
- `StrategyAdviceService._build_lifecycle_plan()`
- `run_strategy_advice()`

### 1.4 Core Call Chain

    scripts/run_strategy_advice.py::main
        -> app/storage/mysql/session.py::session_scope
        -> app/strategy_advice/service.py::run_strategy_advice
        -> app/strategy_advice/service.py::StrategyAdviceService.run_strategy_advice
        -> app/strategy_advice/repository.py::get_review_aggregation_run_by_id
        -> app/strategy_advice/repository.py::get_active_strategy_advice
        -> app/strategy_advice/lifecycle.py::build_advice_candidate_from_aggregation
        -> app/strategy_advice/service.py::StrategyAdviceService._build_lifecycle_plan
        -> app/strategy_advice/notification_payload.py::build_notification_payload
        -> app/strategy_advice/payload_builder.py::build_strategy_advice_payload
        -> app/strategy_advice/payload_builder.py::build_lifecycle_review_payload
        -> app/strategy_advice/payload_builder.py::build_event_payloads
        -> app/strategy_advice/trade_setup.py::build_trade_setup_payloads
        -> app/strategy_advice/repository.py::create_strategy_advice
        -> app/strategy_advice/repository.py::create_lifecycle_review
        -> app/strategy_advice/repository.py::create_strategy_advice_event
        -> app/strategy_advice/repository.py::create_strategy_advice_trade_setup

Persistence methods run only in `--confirm-write` mode. `--dry-run` builds the
same bounded plan but does not call repository create/update methods.

### 1.5 Configuration

This feature reads no new stage-21 environment variables.

It uses the existing MySQL session configuration through
`app/storage/mysql/session.py::session_scope`.

Stage-21A fixed boundaries:

- allowed `trigger_source`: `cli`
- scheduler: not connected
- model providers: not called
- Hermes: not sent
- automatic trading: disabled

### 1.6 External Interfaces

This feature does not request external interfaces.

This feature does not call DeepSeek, GPT, Claude, or any other model provider.

This feature does not call Binance.

This feature does not call Hermes.

### 1.7 Database Reads

`app/strategy_advice/repository.py::get_review_aggregation_run_by_id` reads:

- `model_review_aggregation_run`
  - looked up by `review_aggregation_run_id`
  - used as the immutable stage-20 source for stage-21A decisions

`app/strategy_advice/repository.py::get_active_strategy_advice` reads:

- `strategy_advice`
  - filtered by `symbol`, `base_interval`, `higher_interval`, and
    `advice_status = active`
  - newest active row is used for lifecycle continuation/update/terminal
    decisions

This feature does not read formal Kline tables, account data, private trading
state, Redis, or Hermes records.

### 1.8 Database Writes

Confirm-write mode can write:

- `strategy_advice`
  - one row for a newly created root advice or one row for a new version
  - not written when advice only continues or enters a terminal status
- `strategy_advice_lifecycle_review`
  - one row for every successful lifecycle pass
  - this is not a backtest table and stores no PnL statistics
- `strategy_advice_event`
  - event stream rows such as `created`, `continued`, `superseded`,
    `activated`, `completed`, `invalidated`, `expired`, `closed`, and
    `notification_payload_created`
- `strategy_advice_trade_setup`
  - zero or one bounded conditional setup in 21A
  - this is not an order, not an automatic trading signal, and not an
    executable instruction

The service never writes stage-16/17/18/19/20 source rows and never modifies
formal Kline data.

### 1.9 New Migration

Migration:

`migrations/versions/20260526_21a_create_strategy_advice_tables.py`

Revision:

`20260526_21a`

Down revision:

`20260525_20b`

The migration creates four tables and inserts no business data.

## 2. Table Overview

### 2.1 `strategy_advice`

Purpose: versioned human strategy advice, for example A1/A2/A3 style business
state.

Important fields:

- `advice_id`
- `advice_code`
- `symbol`
- `base_interval`
- `higher_interval`
- `parent_advice_id`
- `root_advice_id`
- `previous_advice_id`
- `advice_path`
- `version_no`
- `advice_status`
- `advice_action`
- `directional_bias`
- `trade_permission`
- source ids from stage 20/18/16
- model-review transparency fields inherited from stage 20
- bounded summary JSON fields
- `is_trading_signal = false`
- `is_executable = false`
- `auto_trading_allowed = false`

There is no `is_final_strategy_advice` field.

Unique key:

- `advice_id`

Indexes include symbol/status, root id, parent id, source review id, material
pack id, and created time.

### 2.2 `strategy_advice_lifecycle_review`

Purpose: one 4h lifecycle review result over the current active advice state.

It is not a backtest table. It does not store win rate, max favorable movement,
max adverse movement, stop-loss hit status, or model quality scoring.

Important fields:

- `review_id`
- `reviewed_advice_id`
- `result_advice_id`
- `previous_advice_id`
- `lifecycle_action`
- `lifecycle_reason`
- source ids from stage 20/18/16
- inherited model-review status
- `notification_required`
- `notification_level`
- `notification_reason`
- `notification_payload_json`

Unique key:

- `review_id`

### 2.3 `strategy_advice_event`

Purpose: append-only lifecycle event stream for advice changes and generated
notification payloads.

Supported event types in 21A:

- `created`
- `continued`
- `updated`
- `superseded`
- `activated`
- `completed`
- `invalidated`
- `expired`
- `closed`
- `notification_payload_created`

Unique key:

- `event_id`

### 2.4 `strategy_advice_trade_setup`

Purpose: optional conditional setup structure under a strategy advice row.

This table is not an order table and is not an execution signal table. The
setup is only a bounded human-review structure. In 21A it can be absent.

Important boundaries:

- no generated entry price
- no generated stop-loss price
- no generated target price
- no position size
- no leverage
- no automatic execution permission

Unique keys:

- `setup_id`
- `advice_id`, `setup_rank`

## 3. Lifecycle State Machine

Lifecycle actions use stable English keys:

- `create_new_advice`
- `continue_active_advice`
- `update_active_advice`
- `close_active_advice`
- `complete_active_advice`
- `invalidate_active_advice`
- `expire_active_advice`
- `wait_without_active_advice`
- `stop_trading`

### 3.1 No Active Advice, Create New

When no active advice exists and the stage-20 aggregation permits a bounded
conditional or management-only human advice:

- insert one `strategy_advice`
- set `advice_status = active`
- set `lifecycle_action = create_new_advice`
- insert one lifecycle review
- insert `created`, `activated`, and `notification_payload_created` events
- optionally insert a bounded `strategy_advice_trade_setup`

### 3.2 Active Advice, No Substantial Change

When the existing active advice semantic signature matches the new candidate:

- do not insert a new `strategy_advice`
- keep the existing advice `active`
- set `lifecycle_action = continue_active_advice`
- insert one lifecycle review
- insert `continued` and `notification_payload_created` events
- `notification_required = true`
- `notification_level = brief`

### 3.3 Active Advice, Substantial Change

When the semantic signature changes:

- update old active advice to `superseded`
- insert a new `strategy_advice`
- set new advice `active`
- set `parent_advice_id` to old advice
- preserve `root_advice_id`
- increment `version_no`
- set `advice_path = old.advice_path + "/" + new_advice_id`
- set `lifecycle_action = update_active_advice`
- insert one lifecycle review
- insert `superseded`, `created`, `activated`, and
  `notification_payload_created` events
- `notification_required = true`
- `notification_level = full`

### 3.4 Active Advice, Terminal Action

When the stage-20 summary explicitly carries a terminal lifecycle marker:

- do not insert a new `strategy_advice`
- update the active advice to `closed`, `completed`, `invalidated`, or
  `expired`
- keep `advice_path` unchanged
- insert one lifecycle review
- insert the matching terminal event and `notification_payload_created`
- `notification_required = true`
- `notification_level = full`

### 3.5 No Active Advice, Not Suitable

When no active advice exists and the current aggregation is not suitable for a
new active advice:

- do not insert `strategy_advice`
- set `lifecycle_action = wait_without_active_advice` or `stop_trading`
- insert one lifecycle review
- insert `notification_payload_created`
- `notification_required = true`
- notification level is `brief` for normal wait and `full` for risk blocked
  stop-trading posture

## 4. `advice_path` Rules

- New root advice: `advice_path = current advice_id`
- New version: `advice_path = parent.advice_path + "/" + current advice_id`
- Continued advice: no new `strategy_advice`, so `advice_path` stays unchanged
- Closed/completed/invalidated/expired advice: no new version, so
  `advice_path` stays unchanged

`advice_path` is a business id chain, not a numeric path such as `1/2/3`.

## 5. Candidate and Risk Rules

Candidate generation is implemented in:

`app/strategy_advice/lifecycle.py::build_advice_candidate_from_aggregation`

The first version is conservative. If stage-20 indicates unacceptable risk,
high strategy conflict, expired model review without fresh invocation,
`partial_success` model relay, failed/blocked model relay, insufficient
evidence, or non-success upstream aggregation status, 21A prefers:

- `wait`
- `avoid_trade`
- `stop_trading`

In those cases it does not create an active conditional trade setup.

## 6. Trade Setup Scope

Implemented in:

`app/strategy_advice/trade_setup.py::build_trade_setup_payloads`

21A can create zero or one setup only when:

- candidate action is `conditional_trade`
- trade permission is `conditionally_allowed`
- risk gates did not block the candidate

The setup stores only:

- manual-observation entry structure
- trigger condition text
- invalidation condition text
- risk boundary text
- empty target zones
- a bounded base-bar expiry
- source model keys inherited from stage 20

It does not generate entry price, stop-loss price, take-profit price, position
size, or leverage. It is not an order and not an automatic trading signal.

## 7. Notification Payload Scope

Implemented in:

`app/strategy_advice/notification_payload.py::build_notification_payload`

21A always builds structured notification fields for successful lifecycle
passes:

- `notification_required`
- `notification_level`
- `notification_reason`
- `notification_payload_json`

21A does not send Hermes. 21B will map the structured payload to user-facing
copy and delivery.

The payload includes:

- lifecycle action and reason
- advice ids and path
- source ids
- model-review invocation/reuse/expiration/chain status
- no-model invocation reason when no model was called
- reused model-analysis run id when reused
- explicit expired notice
- explicit partial-success notice
- boundary flags showing no model call, no Hermes send, no trading signal, no
  executable instruction, and no automatic trading

## 8. Model Review State Inheritance

21A inherits model-review transparency from stage 20 and stores it in advice,
lifecycle review, result DTOs, and notification payloads:

- `model_review_invoked`
- `model_review_invocation_mode`
- `model_review_reused`
- `reused_model_analysis_run_id`
- `model_review_skip_reason`
- `model_review_block_reason`
- `invoked_model_keys_json`
- `invoked_model_roles_json`
- `model_review_chain_status`
- `latest_model_review_at_utc`
- `model_review_basis`
- `model_review_expired`

21A does not re-run stage 19 and does not re-decide stage-20 model reuse,
expiration, chain, or step behavior.

## 9. Data Size Boundary

21A stores compact summaries and bounded structured payloads only.

It does not store:

- full Kline windows
- full market context
- full technical indicator series
- full model prompts
- full model responses
- full model relay context
- account or private trading state

If future stages need larger artifacts, they must use a separate reviewed
storage design rather than expanding these fields into a context dump.

## 10. Redis, Hermes, Scheduler, and Data Source

Redis:

This feature does not read Redis and does not write Redis.

Hermes:

This feature does not send Hermes. It only creates notification payload fields.

Scheduler:

This feature is not connected to scheduler in 21A. Scheduler automation is
reserved for 21C.

`trigger_source`:

This feature accepts only `cli`.

`data_source`:

This feature does not write formal Kline data and does not use a Kline
`data_source`.

## 11. Exceptions

Request validation happens in:

`app/strategy_advice/result_builder.py::validate_strategy_advice_request`

Invalid request cases return `StrategyAdviceServiceStatus.FAILED` with
`error_code = invalid_request` before any database read/write.

Stage-20 lookup failures are caught in:

`app/strategy_advice/service.py::StrategyAdviceService.run_strategy_advice`

The service rolls back the session if possible and returns a structured failure.

Missing stage-20 aggregation rows return `StrategyAdviceServiceStatus.BLOCKED`
with `error_code = review_aggregation_run_not_found`. No stage-21 rows are
written.

Active advice lookup failures and persistence failures are caught by the
service, rolled back if possible, and returned as structured failures.

Hermes failures cannot happen in 21A because Hermes is not called.

Model provider failures cannot happen in 21A because model providers are not
called.

Partial success is represented only when inherited from stage 20 model-review
chain status; 21A itself does not create a partial write success result.

## 12. Tests

Primary tests:

`tests/strategy_advice/test_strategy_advice_service.py`

Coverage includes:

- no active advice creates a new `strategy_advice`
- root `advice_path = advice_id`
- active advice continuation does not create a new advice
- continuation lifecycle review and brief notification
- active advice update supersedes old advice and creates a new version
- new version `advice_path = parent.advice_path + "/" + new advice_id`
- close/complete/invalidate/expire terminal actions
- no active advice wait/stop cases
- inherited model state in result and notification payload
- no active setup for high-risk conditions
- setup persistence capability
- dry-run writes nothing
- confirm-write writes rows
- boundary flags are all false
- no model calls
- no Hermes send
- no scheduler trigger

Default pytest uses in-memory/fake repositories for these tests and does not
request real Binance, real MySQL, real Redis, real Hermes, DeepSeek, GPT,
Claude, or any private trading endpoint.

Suggested checks:

    python -m pytest tests/strategy_advice -q
    python -m pytest tests/model_review_aggregation -q
    python -m pytest tests/model_review_chain -q
    python -m pytest tests -q
    python -m alembic current -v

## 13. Explicit Non-Goals in 21A

21A does not:

- call DeepSeek, GPT, Claude, or stage 19
- send Hermes
- connect scheduler
- enter 21B notification delivery
- enter 21C scheduler automation
- read account data
- place or manage orders
- create automatic execution signals
- generate position size or leverage
- modify Kline, snapshot, strategy signal, material pack, or stage-20 rows
- perform full backtesting or PnL review

## 14. Feature: 21B Strategy Advice Hermes Notification

21B only delivers notifications prepared by 21A. It does not re-run lifecycle
logic and does not create or update `strategy_advice` or
`strategy_advice_trade_setup`.

### 14.1 Entry

Dry-run preview:

    python -m scripts.send_strategy_advice_notification \
      --review-id ADVR-xxx \
      --trigger-source cli \
      --dry-run

Prepare alert/event rows without real Hermes submission:

    python -m scripts.send_strategy_advice_notification \
      --review-id ADVR-xxx \
      --trigger-source cli \
      --confirm-write

Prepare rows and explicitly submit to Hermes:

    python -m scripts.send_strategy_advice_notification \
      --review-id ADVR-xxx \
      --trigger-source cli \
      --confirm-write \
      --send-real-alert

`trigger_source` is restricted to `cli` in 21B. Scheduler triggering is reserved
for 21C.

### 14.2 Entry File

`scripts/send_strategy_advice_notification.py`

Entry method:

`main()`

The script parses CLI arguments, builds `StrategyAdviceNotificationRequest`,
opens the caller-owned MySQL session, calls the 21B sender service, prints a
compact preview/result, and returns the service exit code. It does not directly
write tables, does not directly call Hermes, does not call model providers, and
does not perform trading.

### 14.3 Core Service

`app/strategy_advice/notification_sender.py`

Core methods:

- `StrategyAdviceNotificationSender.send_strategy_advice_notification()`
- `StrategyAdviceNotificationSender._persist_and_maybe_send()`
- `send_strategy_advice_notification()`

### 14.4 Core Call Chain

    scripts/send_strategy_advice_notification.py::main
        -> app/storage/mysql/session.py::session_scope
        -> app/strategy_advice/notification_sender.py::send_strategy_advice_notification
        -> app/strategy_advice/notification_sender.py::StrategyAdviceNotificationSender.send_strategy_advice_notification
        -> app/strategy_advice/notification_repository.py::get_lifecycle_review_by_id
        -> app/strategy_advice/notification_renderer.py::render_strategy_advice_notification
        -> app/strategy_advice/notification_repository.py::has_successful_notification_event
        -> app/strategy_advice/notification_repository.py::has_successful_alert_message
        -> app/strategy_advice/notification_repository.py::create_alert_message
        -> app/alerting/hermes_client.py::HermesClient.send_alert_message
           (only with --confirm-write --send-real-alert)
        -> app/strategy_advice/notification_repository.py::update_alert_message_result
        -> app/strategy_advice/notification_repository.py::create_notification_event

### 14.5 Inputs

21B reads one row from:

- `strategy_advice_lifecycle_review`
  - `review_id`
  - `result_advice_id`
  - `reviewed_advice_id`
  - `previous_advice_id`
  - `lifecycle_action`
  - `lifecycle_reason`
  - source ids
  - inherited model-review fields
  - `notification_required`
  - `notification_level`
  - `notification_reason`
  - `notification_payload_json`

21B reads the existing event/alert state for idempotency:

- `strategy_advice_event`
  - checks `related_review_id = review_id`
  - checks `event_type = notification_sent`
- `alert_message`
  - checks `alert_type = strategy_advice`
  - checks `related_review_id = review_id`
  - checks successful status

21B does not read stage-20 rows directly and does not re-judge advice action,
direction, permission, model reuse, model expiration, chain status, or risk
acceptability.

### 14.6 Database Writes

`--dry-run` writes nothing.

`--confirm-write` can write:

- `alert_message`
  - `alert_type = strategy_advice`
  - Chinese `title` and `message`
  - `severity`
  - `status = skipped` when real Hermes send is not requested
  - `related_type`
  - `related_id`
  - `related_review_id`
  - `trace_id`
- `strategy_advice_event`
  - `notification_prepared`

`--confirm-write --send-real-alert` can write:

- `alert_message`
  - starts as `pending`
  - then updates to Hermes client result status such as
    `submitted_to_hermes`, `submit_failed`, `gateway_rejected`, or `skipped`
- `strategy_advice_event`
  - `notification_sent` on Hermes gateway submission success
  - `notification_failed` on failed/rejected submission
  - `notification_skipped` when the Hermes client/config skips real send

Hermes failure never changes `strategy_advice` status and never changes the
21A lifecycle decision.

### 14.7 New Migration

Migrations:

`migrations/versions/20260527_21b_add_alert_message_related_fields.py`

`migrations/versions/20260528_21b_add_alert_message_review_id.py`

Revisions:

`20260527_21b`

`20260528_21b`

Down revisions:

`20260526_21a`

`20260527_21b`

The migrations reuse the existing `alert_message` table and add only:

- `related_type`
- `related_id`
- `related_review_id`

All columns are nullable for compatibility with older alert rows. Indexes are
added for lookup and idempotency checks. `related_review_id` is the idempotency
anchor for stage-21B notifications; `related_type` and `related_id` remain the
business object reference used by alert browsing and audit views.

### 14.8 Related Type and Related ID Rule

21B implements the required fallback:

- if `result_advice_id` exists:
  - `related_type = strategy_advice`
  - `related_id = result_advice_id`
- else if `reviewed_advice_id` exists:
  - `related_type = strategy_advice`
  - `related_id = reviewed_advice_id`
- else:
  - `related_type = strategy_advice_lifecycle_review`
  - `related_id = review_id`

This supports `wait_without_active_advice` reviews that do not have any
strategy advice row.

Idempotency does not use `related_id` as the primary key. Multiple lifecycle
reviews can legitimately point at the same `strategy_advice`, especially
`continue_active_advice` brief notifications. Each review is therefore
deduplicated by `review_id`.

### 14.9 Notification Rendering

Renderer:

`app/strategy_advice/notification_renderer.py::render_strategy_advice_notification`

21B renders Chinese text only from persisted 21A fields and
`notification_payload_json`.

Full notifications include:

- lifecycle action, Chinese mapping, and reason
- advice action, directional bias, and trade permission
- model-review invocation/reuse/expiration/basis/chain status
- no-model reason, skip reason, and block reason when present
- explicit partial-success warning
- risk acceptability, strategy conflict, risk warnings, missing evidence, and
  risk-blocked state
- source ids
- boundary statement

Brief notifications remain short but still include:

- this run completed
- lifecycle action
- current advice action
- model status
- non-automatic-trading boundary

English keys remain unchanged in database rows. Chinese wording is only a
rendering layer.

### 14.10 Hermes Sending Modes

`--dry-run`:

- renders title/message preview
- writes no `alert_message`
- writes no `strategy_advice_event`
- sends no Hermes

`--confirm-write`:

- writes one `alert_message`
- writes `notification_prepared`
- sends no Hermes

`--confirm-write --send-real-alert`:

- writes one `alert_message`
- calls `app/alerting/hermes_client.py::HermesClient.send_alert_message`
- updates `alert_message` with the sanitized Hermes result
- writes `notification_sent`, `notification_failed`, or `notification_skipped`

The existing Hermes client still enforces Hermes configuration such as enabled,
dry-run, webhook URL, timeout, and retry settings.

### 14.11 Idempotency

By default, the same review is successfully sent only once.

21B skips without sending when:

- a `strategy_advice_event` already exists with
  `related_review_id = review_id` and `event_type = notification_sent`
- or an `alert_message` already exists for the same `related_review_id` with a
  successful status such as `submitted_to_hermes`, `accepted`, or `success`

It does not skip a new lifecycle review merely because the same
`strategy_advice` already had an earlier notification. The alert-level fallback
also uses `related_review_id`, so two different `review_id` values that share
one `result_advice_id` can both send their own brief/full notifications.

If a previous attempt wrote `notification_failed`, a later attempt can retry and
will write a new notification event. Existing failed rows are not overwritten.

When real Hermes sending is disabled (`send_real_alert=false`, including 21C
with `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=false`), 21B also checks the
same `review_id` for already prepared artifacts:

- `strategy_advice_event.related_review_id = review_id` with
  `event_type = notification_prepared`
- or `alert_message.related_review_id = review_id` with `status = skipped`

If either exists, 21B returns `notification_already_prepared` and writes no new
`alert_message` and no new `notification_prepared` event. This prevents
repeated scheduler runs from accumulating skipped notification rows.

`skipped` and `notification_prepared` are not treated as successful delivery.
If real Hermes sending is enabled later and the same `review_id` has no
`notification_sent` event and no successful alert status, 21B may create a new
real-send `alert_message` attempt. It does not update the historical skipped
row; that row remains an audit record that sending was disabled at that time.

`--force-resend` is not implemented in 21B.

### 14.12 Exceptions

Invalid request:

- handled by
  `app/strategy_advice/notification_sender.py::_validate_notification_request`
- returns `FAILED` with `error_code = invalid_request`
- writes nothing

Missing lifecycle review:

- returns `BLOCKED` with `error_code = lifecycle_review_not_found`
- writes nothing

Empty or malformed notification payload:

- returns `BLOCKED` with `error_code = notification_payload_empty` or
  `notification_payload_render_failed`
- writes nothing

Hermes failure:

- updates `alert_message` to the Hermes client result status
- writes `notification_failed`
- does not update advice status
- does not update lifecycle review
- does not modify upstream stage-20 rows

### 14.13 Redis, Scheduler, Models, and Trading

Redis:

21B does not read Redis and does not write Redis.

Scheduler:

21B is not connected to scheduler. Scheduler automation belongs to 21C.

Models:

21B does not call stage 19 and does not call DeepSeek, GPT, Claude, or any
other model provider.

Trading:

21B does not read accounts, does not create orders, does not generate position
size or leverage, and does not perform automatic trading.

## 15. 21B Tests

Primary tests:

`tests/strategy_advice/test_strategy_advice_notification_sender.py`

Coverage includes:

- dry-run brief rendering with no writes and no Hermes
- dry-run full rendering with lifecycle/advice/model/risk/source/boundary
- `notification_required=false` skipped
- empty notification payload blocked
- no-advice wait review related fallback
- `result_advice_id` related fallback
- confirm-write without real send writes prepared alert/event only
- confirm-write with real send uses a mock Hermes client
- Hermes success writes `notification_sent`
- Hermes failure writes `notification_failed` and does not alter advice/review
- successful sent event idempotency
- successful alert-message idempotency
- send-disabled repeated runs write only one skipped alert and one
  `notification_prepared` event per `review_id`
- send-enabled recovery after skipped/prepared rows creates a new send attempt
  without updating the historical skipped row
- multiple historical skipped rows for the same `review_id` do not cause
  multiple Hermes sends
- brief content remains short but includes model status and boundary
- boundary flags remain false
- scheduler trigger is accepted only as a service-level call from 21C; the 21B
  CLI remains manual `trigger_source=cli`

Existing alerting test:

`tests/test_alerting.py`

Default pytest does not send real Hermes. The 21B tests use a mock Hermes client
and in-memory repository.

## 16. Explicit Non-Goals in 21B

21B does not:

- create or update strategy advice lifecycle decisions
- create new `strategy_advice`
- create new `strategy_advice_trade_setup`
- call stage 19
- call DeepSeek, GPT, Claude, or other model providers
- modify stage-20 model review aggregation, reuse, expiration, chain, or step
  logic
- connect scheduler
- read account/private trading state
- place orders
- generate position size or leverage
- modify Kline, snapshot, strategy signal, material pack, or stage-20 rows

## 17. 21C Strategy Advice Scheduler Chain

21C connects the scheduler chain after stage 20. It does not make a new advice
decision engine. It calls the existing 21A service and existing 21B notification
sender.

### 17.1 Entry Points

Scheduler entry:

`app/scheduler/runner.py::_run_strategy_advice_after_model_review_if_needed`

calls:

`app/scheduler/jobs/strategy_advice_scheduler_job.py::run_strategy_advice_scheduler_after_model_review_job`

which calls:

`app/strategy_advice/scheduler_service.py::run_strategy_advice_scheduler`

Manual validation entry:

    python -m scripts.run_strategy_advice_scheduler \
      --review-aggregation-run-id MRAG-xxx \
      --trigger-source cli \
      --dry-run

    python -m scripts.run_strategy_advice_scheduler \
      --symbol BTCUSDT \
      --base-interval 4h \
      --higher-interval 1d \
      --trigger-source cli \
      --confirm-write

The CLI accepts only `trigger_source=cli`. Scheduler automation calls the job
directly with `trigger_source=scheduler`.

### 17.2 Config

21C reads:

- `STRATEGY_ADVICE_SCHEDULER_ENABLED`
- `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED`

Rules:

- when `STRATEGY_ADVICE_SCHEDULER_ENABLED=false`, scheduler runner does not
  trigger 21C
- when scheduler is enabled and notification send is false, 21C may generate
  lifecycle rows and prepared alert rows, but does not send Hermes
- when both are true, 21C may pass `send_real_alert=true` to the existing 21B
  sender

The CLI does not have a flag that bypasses
`STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED`.

### 17.3 Core Data Flow

    4h collector success
        -> stage 17 scheduler hook
        -> stage 18 aggregation hook
        -> stage 20 model-review worker hook
        -> stage 21C scheduler hook
        -> stage 21A strategy advice lifecycle service
        -> stage 21B notification sender

Scheduler runner still does not call stage 19 directly. 21C also does not call
stage 19, DeepSeek, GPT, Claude, or any model provider.

### 17.4 MRAG Processing

21C processes only `model_review_aggregation_run` rows. It does not scan
`analysis_material_pack` and does not generate advice from material packs.

For one `symbol/base_interval/higher_interval` scope:

- the newest unprocessed MRAG may enter 21A and then 21B
- older unprocessed MRAG rows are marked with
  `lifecycle_action = skip_stale_review_aggregation`
- stale MRAG rows do not create `strategy_advice`
- stale MRAG rows do not create `strategy_advice_trade_setup`
- stale MRAG rows do not send Hermes
- stale MRAG rows do not affect active advice

20 status handling:

- `success` and `blocked` are processable by 21A
- `failed` and `skipped` are scheduler-skipped and do not create formal advice

### 17.5 Idempotency

21A idempotency is based on:

`strategy_advice_lifecycle_review.source_review_aggregation_run_id`

21C adds a unique constraint:

`uq_strategy_advice_lifecycle_source_review`

If a lifecycle review already exists for an MRAG, 21C does not rerun 21A. It
checks whether 21B notification still needs recovery.

21B idempotency remains review-based:

- `strategy_advice_event.related_review_id = review_id` with
  `event_type = notification_sent`
- or successful `alert_message.related_review_id = review_id`
- when notification sending is disabled, existing `notification_prepared` or
  skipped `alert_message` rows for the same `review_id` are treated as
  already prepared, so 21C recovery does not append duplicate skipped rows

Advice id is not used to suppress a later lifecycle-review notification.

### 17.6 Notification Recovery and Retry

If 21A succeeded but 21B did not successfully send or prepare the required
notification, 21C runs only 21B.

Hermes retry rules:

- retry only notification sending
- do not rerun 21A
- do not recreate lifecycle reviews, advice, or trade setups
- wait 5 minutes after the latest `notification_failed`
- stop after 3 `notification_failed` events
- Hermes failure never changes advice status

### 17.7 Redis Lock

21C uses Redis temporary locks plus database idempotency.

Lock key:

`strategy_advice_21c:{symbol}:{base_interval}:{higher_interval}:{review_aggregation_run_id}`

Example:

`strategy_advice_21c:BTCUSDT:4h:1d:MRAG-xxx`

TTL:

`600` seconds.

If the lock is unavailable, 21C returns `lock_skipped` and does not process the
MRAG. The database unique constraint remains the final duplicate-write guard.

### 17.8 Scheduler Audit Log

21C adds table:

`strategy_advice_scheduler_event_log`

It records job name, MRAG id, trigger source, status, reason, trace id, start
and finish time, and compact details. It is not a lifecycle decision table and
is not used to judge strategy quality.

### 17.9 External Interfaces and Boundaries

Database reads:

- `model_review_aggregation_run`
- `strategy_advice_lifecycle_review`
- `strategy_advice_event`
- `alert_message`

Database writes:

- `strategy_advice_lifecycle_review`
- `strategy_advice_event`
- `strategy_advice`
- `strategy_advice_trade_setup`
- `alert_message`
- `strategy_advice_scheduler_event_log`

Redis:

21C reads/writes only the temporary lock key described above.

Hermes:

21C does not send Hermes directly. It delegates to 21B. Real send is possible
only when `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED=true`.

Models:

21C does not call stage 19 and does not call any model provider.

Trading:

21C does not read accounts, does not place orders, does not generate position
size or leverage, and does not perform automatic trading.

### 17.10 21C Tests

Primary tests:

- `tests/strategy_advice/test_strategy_advice_scheduler_service.py`
- `tests/scheduler/test_model_review_chain_worker_hook.py`

Coverage includes:

- scheduler disabled skips 21A/21B
- notification send disabled prepares only
- notification send enabled passes the real-send flag to 21B with mocked Hermes
- latest MRAG enters 21A/21B
- existing lifecycle review recovers only 21B
- old MRAG writes `skip_stale_review_aggregation`
- stale MRAG does not notify
- 21B failure recovery does not rerun 21A
- 5-minute and 3-attempt Hermes retry rules
- review-id notification idempotency
- Redis lock skip
- scheduler runner invokes 21C only when enabled

Default pytest does not request real Binance, real MySQL, real Redis, real
Hermes, stage 19, or any model provider.

## 18. Explicit Non-Goals in 21C

21C does not:

- call stage 19
- call DeepSeek, GPT, Claude, or other model providers
- scan unprocessed `analysis_material_pack`
- generate advice directly from material packs
- reimplement stage-20 model review reuse, expiration, chain, or step logic
- reimplement 21A lifecycle decisions
- reimplement 21B notification rendering or Hermes sending
- auto trade
- read account/private trading state
- place orders
- generate position size or leverage
- modify Kline, snapshot, strategy signal, material pack, or stage-20 rows
