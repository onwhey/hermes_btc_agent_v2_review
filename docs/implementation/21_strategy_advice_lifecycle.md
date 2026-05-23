# 21 Strategy Advice Lifecycle Implementation

This document describes stage 21A only. Stage 21A persists bounded human
strategy advice lifecycle state from an existing stage-20
`model_review_aggregation_run`. It does not implement 21B Hermes delivery and
does not implement 21C scheduler automation.

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
