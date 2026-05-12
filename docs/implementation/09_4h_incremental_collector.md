# 09 4h Kline Incremental Collector Implementation

## 1. Feature: Incremental 4h Kline Collection

### Entry Points

Manual debug CLI:

```bash
python -m scripts.collect_4h_klines --trigger-source cli --dry-run
python -m scripts.collect_4h_klines --trigger-source cli --confirm-write
```

Service entry:

```text
app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection
```

The script is for manual debugging only. Scheduler integration is not implemented in this phase. A future scheduler job must call `run_incremental_4h_collection()` directly with `trigger_source=scheduler`; it must not call `scripts.collect_4h_klines`.

## 2. Data Source

The only formal Kline data source is:

```text
Binance USDT-M Futures REST /fapi/v1/klines
```

The service calls:

```text
app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
```

It never uses WebSocket Klines, third-party data, local generated data, or user-entered OHLCV fields for `market_kline_4h`.

## 3. Why Fetch Multiple Klines

The collector requests `limit + 1` recent Binance REST Klines, filters out unclosed Klines using Binance server time, sorts by UTC `open_time_ms`, and inspects the last `limit` closed Klines.

This prevents missed writes when one collection run failed. For example, if the database has `04:00` and `08:00`, and `12:00` was missed, a later run can fetch `04:00, 08:00, 12:00, 16:00`, skip the existing rows, and insert `12:00` and `16:00`.

## 4. Core Call Chain

```text
scripts/collect_4h_klines.py::main
    -> app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/storage/mysql/repositories/collector_event_log_repository.py::create_running_event
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines
    -> app/market_data/collector/quality.py::check_incremental_collect_quality
    -> app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    -> app/market_data/backfill/persistence.py::persist_backfill_klines
    -> app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert
    -> app/storage/mysql/repositories/collector_event_log_repository.py::mark_success
```

## 5. Quality Checks

The collector reuses phase-07 batch validation:

```text
app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
```

It then checks database context in:

```text
app/market_data/collector/quality.py::check_incremental_collect_quality
```

Checks include:

- Each Kline passes the phase-06 single-Kline validator.
- Batch rows are sorted by UTC `open_time_ms`.
- Batch rows have no duplicate `open_time_ms`.
- Adjacent rows differ by exactly 14,400,000 ms.
- Rows are closed by Binance `server_time_ms`.
- Existing database rows with the same `open_time_ms` are identical or the run is blocked.
- New rows connect to the nearest previous and next database neighbors.

## 6. Duplicate, Missing, Conflict, And Unclosed Rows

Existing and identical rows are marked as skipped context and are not written again.

Missing rows inside the fetched closed window are inserted only after the full batch and database context pass.

Conflicting existing rows block the run. The service does not overwrite formal Klines.

Unclosed rows are filtered before quality checks and are never written. If no closed Klines remain, the phase-07 empty-batch quality check blocks the run and triggers a fixed-template alert.

## 7. Writes And Transaction Semantics

Formal writes use:

```text
app/market_data/backfill/persistence.py::persist_backfill_klines
```

That helper wraps `MarketKline4hRepository.bulk_upsert()` in a nested transaction when the session supports savepoints. If persistence fails, the service rolls back and returns a failed result. Blocked and failed results do not commit partial formal Kline writes.

## 8. Redis Lock

Before any formal Kline write path, the service acquires:

```text
kline_write:{symbol}:{interval}
```

The owner is `trace_id`, and the TTL comes from `IncrementalKlineCollectRequest.lock_ttl_seconds`.

Release uses the phase-08 atomic Lua owner-check delete in:

```text
app/core/task_lock.py::RedisTaskLock.release_lock
```

If the lock is already held, the run is recorded as skipped and does not request Binance or write formal Klines. If Redis raises, the run fails, does not request Binance, and sends a fixed-template Hermes alert.

## 9. Event Log And Alerts

The service records `collector_event_log` with:

```text
event_type = kline_4h_incremental_collect
trigger_source = cli or scheduler
data_source = binance_rest_by_cli or binance_rest_by_scheduler
status = success / blocked / failed / skipped
```

It also records `data_quality_check` for quality reports.

Blocked, failed, database write failure, Redis failure, Binance failure, parser failure, validator failure, and event-log failure all trigger mandatory Hermes fixed-template alerts through:

```text
app/market_data/collector/alerts.py
app/alerting/service.py::send_alert
```

Failure alerts cannot be disabled by CLI arguments or config. The old optional failure-alert switch is not supported, and there is no failure-alert-off configuration.

Success notification is optional through `notify_success=True` or CLI `--notify-success`.

## 10. Boundaries

This phase does not implement scheduler integration.

This phase does not automatically repair formal Klines, fill extra ranges outside the fetched recent window, overwrite conflicts, delete formal Klines, read account data, call private Binance endpoints, call DeepSeek, generate strategy analysis, or execute any trade.

The script is not a scheduler entry point.

## 11. Tests

Default pytest uses fake Binance clients, fake repositories, fake Redis locks, fake sessions, and fake alert senders.

Default pytest does not request real Binance, connect real MySQL, connect real Redis, send real Hermes, call DeepSeek, or access private Binance endpoints.

Commands:

```bash
python -m py_compile app/core/task_lock.py
python -m pytest tests/test_kline_quality_checker.py
python -m pytest tests/test_4h_kline_manual_backfill.py
python -m pytest tests/test_4h_kline_incremental_collector.py
python -m pytest
python -m scripts.collect_4h_klines --help
```
