from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from app.alerting.service import format_alert_message
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.market_context import snapshot_alerts, snapshot_builder, snapshot_quality, snapshot_repository, snapshot_service
from app.market_context.snapshot_repository import MarketContextSnapshotRepository
from app.market_context.snapshot_service import build_market_context_snapshot
from app.market_context.snapshot_types import (
    EXIT_ALERT_FAILED,
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    MarketContextSnapshotRequest,
    MarketContextSnapshotRestoreError,
    MarketContextSnapshotResult,
    MarketContextSnapshotStatus,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS, KLINE_4H_INTERVAL_MS
from scripts import build_market_context_snapshot as snapshot_cli
from scripts import check_kline_integrity_1d as integrity_1d_cli

CURRENT_TIME_MS = int(datetime(2026, 5, 16, 8, 10, tzinfo=timezone.utc).timestamp() * 1000)
EXPECTED_4H_LATEST_MS = int(datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
EXPECTED_1D_LATEST_MS = int(datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
SNAPSHOT_NOT_TRADING_ADVICE_TEXT = "本提醒不是交易建议，不包含任何开仓、平仓、止盈、止损或仓位建议。"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeSnapshotRepository:
    def __init__(
        self,
        *,
        rows_4h: Iterable[Any] | None = None,
        rows_1d: Iterable[Any] | None = None,
        quality_4h_status: str | None = "healthy",
        quality_1d_status: str | None = "healthy",
        quality_4h_end_open_time_ms: int | None = None,
        quality_1d_end_open_time_ms: int | None = None,
        collector_4h_status: str | None = "success",
        collector_1d_status: str | None = "success",
        fail_on_read: bool = False,
        fail_on_create: bool = False,
    ) -> None:
        self.rows_4h = list(rows_4h if rows_4h is not None else valid_4h_rows())
        self.rows_1d = list(rows_1d if rows_1d is not None else valid_1d_rows())
        self.quality_4h_status = quality_4h_status
        self.quality_1d_status = quality_1d_status
        self.quality_4h_end_open_time_ms = quality_4h_end_open_time_ms
        self.quality_1d_end_open_time_ms = quality_1d_end_open_time_ms
        self.collector_4h_status = collector_4h_status
        self.collector_1d_status = collector_1d_status
        self.fail_on_read = fail_on_read
        self.fail_on_create = fail_on_create
        self.created_payloads: list[Any] = []
        self.wrote_4h = False
        self.wrote_1d = False
        self.kline_ref_write_count = 0

    def list_recent_4h_klines(self, _db_session: Any, *, symbol: str, limit: int) -> list[Any]:
        if self.fail_on_read:
            raise RuntimeError("snapshot read failed")
        assert symbol == "BTCUSDT"
        return sorted(self.rows_4h, key=lambda row: row.open_time_ms)[-limit:]

    def list_recent_1d_klines(self, _db_session: Any, *, symbol: str, limit: int) -> list[Any]:
        if self.fail_on_read:
            raise RuntimeError("snapshot read failed")
        assert symbol == "BTCUSDT"
        return sorted(self.rows_1d, key=lambda row: row.open_time_ms)[-limit:]

    def get_latest_collector_event(self, _db_session: Any, *, symbol: str, interval_value: str) -> Any | None:
        assert symbol == "BTCUSDT"
        status = self.collector_4h_status if interval_value == "4h" else self.collector_1d_status
        if status is None:
            return None
        return SimpleNamespace(id=101 if interval_value == "4h" else 201, status=status)

    def get_latest_daily_quality_check(self, _db_session: Any, *, symbol: str, interval_value: str) -> Any | None:
        assert symbol == "BTCUSDT"
        status = self.quality_4h_status if interval_value == "4h" else self.quality_1d_status
        if status is None:
            return None
        if interval_value == "4h":
            end_open_time_ms = self.quality_4h_end_open_time_ms
            rows = self.rows_4h
        else:
            end_open_time_ms = self.quality_1d_end_open_time_ms
            rows = self.rows_1d
        if end_open_time_ms is None and rows:
            end_open_time_ms = max(int(row.open_time_ms) for row in rows)
        return SimpleNamespace(
            id=301 if interval_value == "4h" else 401,
            status=status,
            end_open_time_ms=end_open_time_ms,
        )

    def create_snapshot(self, _db_session: Any, payload: Any) -> Any:
        if self.fail_on_create:
            raise RuntimeError("snapshot write failed")
        assert not hasattr(payload, "refs")
        self.created_payloads.append(payload)
        return SimpleNamespace(id=len(self.created_payloads), snapshot_id=payload.snapshot_id)

    def bulk_upsert(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("snapshot repository must not expose formal Kline writes")


class FakeAlertSender:
    def __init__(
        self,
        result: AlertSendResult | None = None,
    ) -> None:
        self.result = result or AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        event: Any,
        *,
        repository: Any,
        db_session: Any,
        send_real_alert: bool,
    ) -> AlertSendResult:
        self.calls.append(
            {
                "event": event,
                "repository": repository,
                "db_session": db_session,
                "send_real_alert": send_real_alert,
                "message": format_alert_message(event),
            }
        )
        return self.result


class FakeRestoreExecuteResult:
    def __init__(self, snapshot_row: Any | None) -> None:
        self.snapshot_row = snapshot_row

    def scalar_one_or_none(self) -> Any | None:
        return self.snapshot_row


class FakeRestoreSession:
    def __init__(self, snapshot_row: Any | None) -> None:
        self.snapshot_row = snapshot_row
        self.execute_calls = 0
        self.add_calls = 0

    def execute(self, _stmt: Any) -> FakeRestoreExecuteResult:
        self.execute_calls += 1
        return FakeRestoreExecuteResult(self.snapshot_row)

    def add(self, _row: Any) -> None:
        self.add_calls += 1
        raise AssertionError("restore must be read-only")


class FakeRangeKline4hRepository:
    def __init__(self, rows: Iterable[Any]) -> None:
        self.rows = list(rows)
        self.calls: list[dict[str, Any]] = []

    def list_by_time_range(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[Any]:
        self.calls.append(
            {
                "symbol": symbol,
                "interval_value": interval_value,
                "start_open_time_ms": start_open_time_ms,
                "end_open_time_ms": end_open_time_ms,
            }
        )
        return [
            row
            for row in sorted(self.rows, key=lambda item: item.open_time_ms)
            if row.symbol == symbol
            and row.interval_value == interval_value
            and start_open_time_ms <= row.open_time_ms <= end_open_time_ms
        ]


class FakeRangeKline1dRepository:
    def __init__(self, rows: Iterable[Any]) -> None:
        self.rows = list(rows)
        self.calls: list[dict[str, Any]] = []

    def list_by_time_range(
        self,
        _db_session: Any,
        *,
        symbol: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[Any]:
        self.calls.append(
            {
                "symbol": symbol,
                "start_open_time_ms": start_open_time_ms,
                "end_open_time_ms": end_open_time_ms,
            }
        )
        return [
            row
            for row in sorted(self.rows, key=lambda item: item.open_time_ms)
            if row.symbol == symbol and start_open_time_ms <= row.open_time_ms <= end_open_time_ms
        ]


def snapshot_request(
    *,
    dry_run: bool = False,
    confirm_write: bool = True,
    notify_on_blocked: bool = False,
    notify_on_failed: bool = False,
    lookback_4h_count: int = 3,
    lookback_1d_count: int = 3,
) -> MarketContextSnapshotRequest:
    return MarketContextSnapshotRequest(
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        lookback_4h_count=lookback_4h_count,
        lookback_1d_count=lookback_1d_count,
        dry_run=dry_run,
        confirm_write=confirm_write,
        notify_on_blocked=notify_on_blocked,
        notify_on_failed=notify_on_failed,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-market-context-test",
    )


def run_snapshot_with_fakes(
    repository: FakeSnapshotRepository | None = None,
    *,
    request: MarketContextSnapshotRequest | None = None,
    alert_sender: FakeAlertSender | None = None,
) -> tuple[Any, FakeSnapshotRepository, FakeSession, FakeAlertSender]:
    fake_repository = repository or FakeSnapshotRepository()
    fake_session = FakeSession()
    fake_alert_sender = alert_sender or FakeAlertSender()
    result = build_market_context_snapshot(
        db_session=fake_session,
        request=request or snapshot_request(),
        repository=fake_repository,
        alert_sender=fake_alert_sender,
        alert_repository=object(),
    )
    return result, fake_repository, fake_session, fake_alert_sender


def test_snapshot_generation_success_writes_window_index_and_summary_payload_only() -> None:
    result, repository, session, alert_sender = run_snapshot_with_fakes()

    assert result.status == MarketContextSnapshotStatus.CREATED
    assert result.exit_code == EXIT_SUCCESS
    assert len(repository.created_payloads) == 1
    assert session.commits == 1
    assert alert_sender.calls == []
    assert repository.wrote_4h is False
    assert repository.wrote_1d is False
    assert repository.kline_ref_write_count == 0

    payload = repository.created_payloads[0]
    payload_json = json.loads(payload.snapshot_payload_json)
    assert payload.status == MarketContextSnapshotStatus.CREATED
    assert payload_json["symbol"] == "BTCUSDT"
    assert payload_json["base_interval"] == "4h"
    assert payload_json["higher_interval"] == "1d"
    assert payload.lookback_4h_count == 3
    assert payload.lookback_1d_count == 3
    assert payload.actual_4h_count == 3
    assert payload.actual_1d_count == 3
    assert payload.start_4h_open_time_ms == EXPECTED_4H_LATEST_MS - (2 * KLINE_4H_INTERVAL_MS)
    assert payload.end_4h_open_time_ms == EXPECTED_4H_LATEST_MS
    assert payload.start_1d_open_time_ms == EXPECTED_1D_LATEST_MS - (2 * KLINE_1D_INTERVAL_MS)
    assert payload.end_1d_open_time_ms == EXPECTED_1D_LATEST_MS
    assert payload.latest_4h_quality_check_id == 301
    assert payload.latest_1d_quality_check_id == 401
    assert payload_json["lookback_4h_count"] == 3
    assert payload_json["lookback_1d_count"] == 3
    assert payload_json["actual_4h_count"] == 3
    assert payload_json["actual_1d_count"] == 3
    assert payload_json["start_4h_open_time_ms"] == payload.start_4h_open_time_ms
    assert payload_json["end_4h_open_time_ms"] == payload.end_4h_open_time_ms
    assert payload_json["start_1d_open_time_ms"] == payload.start_1d_open_time_ms
    assert payload_json["end_1d_open_time_ms"] == payload.end_1d_open_time_ms
    assert "klines" not in payload_json
    assert "kline_refs" not in payload_json
    payload_text = payload.snapshot_payload_json
    for ohlcv_key in (
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "quote_volume",
        "trade_count",
    ):
        assert ohlcv_key not in payload_text
    assert not hasattr(payload, "refs")
    assert_no_trading_advice_terms(payload.snapshot_payload_json)


def test_config_default_and_cli_override_lookback_counts(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        snapshot_cli,
        "get_settings",
        lambda: SimpleNamespace(
            market_context_symbol="BTCUSDT",
            market_context_base_interval="4h",
            market_context_higher_interval="1d",
            market_context_4h_lookback_count=7,
            market_context_1d_lookback_count=11,
        ),
    )

    parser = snapshot_cli.build_arg_parser()
    default_args = parser.parse_args(["--trigger-source", "cli", "--dry-run"])
    override_args = parser.parse_args(
        [
            "--trigger-source",
            "cli",
            "--dry-run",
            "--lookback-4h",
            "5",
            "--lookback-1d",
            "9",
        ]
    )

    assert default_args.lookback_4h == 7
    assert default_args.lookback_1d == 11
    assert override_args.lookback_4h == 5
    assert override_args.lookback_1d == 9


def test_snapshot_records_actual_configured_lookback_counts() -> None:
    result, repository, _session, _alert_sender = run_snapshot_with_fakes(
        FakeSnapshotRepository(
            rows_4h=valid_4h_rows(count=4),
            rows_1d=valid_1d_rows(count=2),
        ),
        request=snapshot_request(lookback_4h_count=4, lookback_1d_count=2),
    )

    assert result.status == MarketContextSnapshotStatus.CREATED
    payload = repository.created_payloads[0]
    payload_json = json.loads(payload.snapshot_payload_json)
    assert payload.lookback_4h_count == 4
    assert payload.lookback_1d_count == 2
    assert payload.actual_4h_count == 4
    assert payload.actual_1d_count == 2
    assert payload_json["lookback_4h_count"] == 4
    assert payload_json["lookback_1d_count"] == 2
    assert payload_json["actual_4h_count"] == 4
    assert payload_json["actual_1d_count"] == 2


def test_4h_uninitialized_returns_blocked_without_writing_or_binance() -> None:
    result, repository, _session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=[]),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )

    assert result.status == MarketContextSnapshotStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert "4h" in (result.blocked_reason or "")
    assert repository.created_payloads == []
    assert repository.wrote_4h is False


def test_1d_uninitialized_returns_blocked_without_writing_or_fake_daily_payload() -> None:
    result, repository, _session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_1d=[]),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )

    assert result.status == MarketContextSnapshotStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert "1d" in (result.blocked_reason or "")
    assert repository.created_payloads == []
    assert repository.wrote_1d is False


def test_4h_and_1d_stale_data_return_blocked_with_interval_reason() -> None:
    stale_4h = valid_4h_rows(latest_open_ms=EXPECTED_4H_LATEST_MS - KLINE_4H_INTERVAL_MS)
    result_4h, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=stale_4h),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert "4h" in (result_4h.blocked_reason or "")

    stale_1d = valid_1d_rows(latest_open_ms=EXPECTED_1D_LATEST_MS - KLINE_1D_INTERVAL_MS)
    result_1d, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_1d=stale_1d),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert "1d" in (result_1d.blocked_reason or "")


def test_recent_quality_failure_blocks_for_4h_and_1d() -> None:
    result_4h, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(quality_4h_status="failed"),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert "4h" in (result_4h.blocked_reason or "")
    assert "failed" in (result_4h.blocked_reason or "")

    result_1d, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(quality_1d_status="failed"),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert "1d" in (result_1d.blocked_reason or "")
    assert "failed" in (result_1d.blocked_reason or "")


def test_1d_quality_check_missing_blocks_without_kline_write_or_binance() -> None:
    result, repository, _session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(quality_1d_status=None),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )

    assert result.status == MarketContextSnapshotStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert "1d" in (result.blocked_reason or "")
    assert repository.created_payloads == []
    assert repository.wrote_4h is False
    assert repository.wrote_1d is False


def test_1d_quality_healthy_covering_latest_1d_allows_snapshot_to_continue() -> None:
    result, repository, session, alert_sender = run_snapshot_with_fakes(
        FakeSnapshotRepository(
            quality_1d_status="healthy",
            quality_1d_end_open_time_ms=EXPECTED_1D_LATEST_MS,
        ),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )

    assert result.status == MarketContextSnapshotStatus.CREATED
    assert result.exit_code == EXIT_SUCCESS
    assert repository.created_payloads == []
    assert session.commits == 0
    assert alert_sender.calls == []
    assert repository.wrote_4h is False
    assert repository.wrote_1d is False


def test_quality_check_end_open_time_must_cover_latest_snapshot_kline_for_4h_and_1d() -> None:
    result_4h, repository_4h, _session_4h, _alert_4h = run_snapshot_with_fakes(
        FakeSnapshotRepository(
            quality_4h_status="healthy",
            quality_4h_end_open_time_ms=EXPECTED_4H_LATEST_MS - KLINE_4H_INTERVAL_MS,
        ),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert "4h" in (result_4h.blocked_reason or "")
    assert "未覆盖" in (result_4h.blocked_reason or "")
    assert repository_4h.created_payloads == []
    assert repository_4h.wrote_4h is False
    assert repository_4h.wrote_1d is False

    result_1d, repository_1d, _session_1d, _alert_1d = run_snapshot_with_fakes(
        FakeSnapshotRepository(
            quality_1d_status="passed",
            quality_1d_end_open_time_ms=EXPECTED_1D_LATEST_MS - KLINE_1D_INTERVAL_MS,
        ),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert "1d" in (result_1d.blocked_reason or "")
    assert "未覆盖" in (result_1d.blocked_reason or "")
    assert repository_1d.created_payloads == []
    assert repository_1d.wrote_4h is False
    assert repository_1d.wrote_1d is False


def test_insufficient_kline_count_blocks_for_4h_and_1d() -> None:
    result_4h, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=valid_4h_rows(count=2)),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert result_4h.actual_4h_count == 2
    assert "4h" in (result_4h.blocked_reason or "")

    result_1d, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_1d=valid_1d_rows(count=2)),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert result_1d.actual_1d_count == 2
    assert "1d" in (result_1d.blocked_reason or "")


def test_unclosed_kline_blocks_for_4h_and_1d() -> None:
    unclosed_4h = valid_4h_rows(latest_open_ms=EXPECTED_4H_LATEST_MS + KLINE_4H_INTERVAL_MS)
    result_4h, repository_4h, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=unclosed_4h),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert "4h" in (result_4h.blocked_reason or "")
    assert repository_4h.created_payloads == []

    unclosed_1d = valid_1d_rows(latest_open_ms=EXPECTED_1D_LATEST_MS + KLINE_1D_INTERVAL_MS)
    result_1d, repository_1d, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_1d=unclosed_1d),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert "1d" in (result_1d.blocked_reason or "")
    assert repository_1d.created_payloads == []


def test_non_continuous_kline_window_blocks_for_4h_and_1d() -> None:
    broken_4h = valid_4h_rows()
    broken_4h[1] = clone_kline_with_open_time(broken_4h[1], broken_4h[1].open_time_ms + KLINE_4H_INTERVAL_MS)
    result_4h, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=broken_4h),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_4h.status == MarketContextSnapshotStatus.BLOCKED
    assert "4h" in (result_4h.blocked_reason or "")

    broken_1d = valid_1d_rows()
    broken_1d[1] = clone_kline_with_open_time(broken_1d[1], broken_1d[1].open_time_ms + KLINE_1D_INTERVAL_MS)
    result_1d, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_1d=broken_1d),
        request=snapshot_request(dry_run=True, confirm_write=False),
    )
    assert result_1d.status == MarketContextSnapshotStatus.BLOCKED
    assert "1d" in (result_1d.blocked_reason or "")


def test_dry_run_does_not_write_snapshot_and_does_not_alert_by_default() -> None:
    result, repository, session, alert_sender = run_snapshot_with_fakes(
        request=snapshot_request(dry_run=True, confirm_write=False)
    )

    assert result.status == MarketContextSnapshotStatus.CREATED
    assert result.exit_code == EXIT_SUCCESS
    assert repository.created_payloads == []
    assert session.commits == 0
    assert alert_sender.calls == []
    assert "kline_refs" not in "\n".join(snapshot_cli.format_market_context_snapshot_result_lines(result))


def test_restore_snapshot_by_id_returns_4h_and_1d_windows_from_formal_tables() -> None:
    rows_4h = valid_4h_rows()
    rows_1d = valid_1d_rows()
    snapshot_row = build_snapshot_row_from_rows(rows_4h=rows_4h, rows_1d=rows_1d)
    session = FakeRestoreSession(snapshot_row)
    repo_4h = FakeRangeKline4hRepository(rows_4h)
    repo_1d = FakeRangeKline1dRepository(rows_1d)
    repository = MarketContextSnapshotRepository(
        kline_4h_repository=repo_4h,
        kline_1d_repository=repo_1d,
    )

    restored = repository.restore_snapshot_kline_windows(session, snapshot_id=snapshot_row.snapshot_id)

    assert restored.snapshot is snapshot_row
    assert [row.open_time_ms for row in restored.rows_4h] == [row.open_time_ms for row in rows_4h]
    assert [row.open_time_ms for row in restored.rows_1d] == [row.open_time_ms for row in rows_1d]
    assert len(restored.rows_4h) == snapshot_row.actual_4h_count
    assert len(restored.rows_1d) == snapshot_row.actual_1d_count
    assert repo_4h.calls[0]["start_open_time_ms"] == snapshot_row.start_4h_open_time_ms
    assert repo_4h.calls[0]["end_open_time_ms"] == snapshot_row.end_4h_open_time_ms
    assert repo_1d.calls[0]["start_open_time_ms"] == snapshot_row.start_1d_open_time_ms
    assert repo_1d.calls[0]["end_open_time_ms"] == snapshot_row.end_1d_open_time_ms
    assert session.add_calls == 0


def test_restore_snapshot_uses_recorded_counts_not_current_config() -> None:
    rows_4h = valid_4h_rows(count=2)
    rows_1d = valid_1d_rows(count=2)
    snapshot_row = build_snapshot_row_from_rows(rows_4h=rows_4h, rows_1d=rows_1d)
    snapshot_row.lookback_4h_count = 240
    snapshot_row.lookback_1d_count = 730
    outside_4h_rows = valid_4h_rows(count=2, latest_open_ms=rows_4h[0].open_time_ms - KLINE_4H_INTERVAL_MS)
    outside_1d_rows = valid_1d_rows(count=2, latest_open_ms=rows_1d[0].open_time_ms - KLINE_1D_INTERVAL_MS)
    repository = MarketContextSnapshotRepository(
        kline_4h_repository=FakeRangeKline4hRepository(outside_4h_rows + rows_4h),
        kline_1d_repository=FakeRangeKline1dRepository(outside_1d_rows + rows_1d),
    )

    restored = repository.restore_snapshot_kline_windows(
        FakeRestoreSession(snapshot_row),
        snapshot_id=snapshot_row.snapshot_id,
    )

    assert len(restored.rows_4h) == 2
    assert len(restored.rows_1d) == 2


def test_restore_snapshot_fails_when_4h_count_mismatch() -> None:
    rows_4h = valid_4h_rows()
    rows_1d = valid_1d_rows()
    snapshot_row = build_snapshot_row_from_rows(rows_4h=rows_4h, rows_1d=rows_1d)
    snapshot_row.actual_4h_count = 4
    repository = MarketContextSnapshotRepository(
        kline_4h_repository=FakeRangeKline4hRepository(rows_4h),
        kline_1d_repository=FakeRangeKline1dRepository(rows_1d),
    )

    try:
        repository.restore_snapshot_kline_windows(FakeRestoreSession(snapshot_row), snapshot_id=snapshot_row.snapshot_id)
    except MarketContextSnapshotRestoreError as exc:
        assert "4h restore count mismatch" in str(exc)
    else:
        raise AssertionError("restore must fail when 4h actual_count does not match restored rows")


def test_restore_snapshot_fails_when_1d_count_mismatch() -> None:
    rows_4h = valid_4h_rows()
    rows_1d = valid_1d_rows()
    snapshot_row = build_snapshot_row_from_rows(rows_4h=rows_4h, rows_1d=rows_1d)
    snapshot_row.actual_1d_count = 4
    repository = MarketContextSnapshotRepository(
        kline_4h_repository=FakeRangeKline4hRepository(rows_4h),
        kline_1d_repository=FakeRangeKline1dRepository(rows_1d),
    )

    try:
        repository.restore_snapshot_kline_windows(FakeRestoreSession(snapshot_row), snapshot_id=snapshot_row.snapshot_id)
    except MarketContextSnapshotRestoreError as exc:
        assert "1d restore count mismatch" in str(exc)
    else:
        raise AssertionError("restore must fail when 1d actual_count does not match restored rows")


def test_failed_status_and_failed_hermes_notification_are_compact() -> None:
    alert_sender = FakeAlertSender()
    result, repository, session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(fail_on_read=True),
        request=snapshot_request(notify_on_failed=True),
        alert_sender=alert_sender,
    )

    assert result.status == MarketContextSnapshotStatus.FAILED
    assert result.exit_code == EXIT_FAILED
    assert "snapshot read failed" in (result.error_message or "")
    assert len(repository.created_payloads) == 1
    assert repository.created_payloads[0].status == MarketContextSnapshotStatus.FAILED
    assert result.snapshot_row_id == 1
    assert session.rollbacks == 1
    assert len(alert_sender.calls) == 1
    message = alert_sender.calls[0]["message"]
    assert "failed" in message
    assert SNAPSHOT_NOT_TRADING_ADVICE_TEXT in message
    assert "klines" not in message
    assert "open_time_ms" not in message
    assert "snapshot_payload_json" not in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message


def test_blocked_hermes_notification_is_chinese_compact_and_no_full_payload_or_kline_array() -> None:
    alert_sender = FakeAlertSender()
    result, _repository, _session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=[]),
        request=snapshot_request(confirm_write=True, notify_on_blocked=True),
        alert_sender=alert_sender,
    )

    assert result.status == MarketContextSnapshotStatus.BLOCKED
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = alert_sender.calls[0]["message"]
    assert event.severity.value == "warning"
    assert "BTCUSDT 4h + 1d" in message
    assert "blocked" in message
    assert "trace-market-context-test" in message
    assert SNAPSHOT_NOT_TRADING_ADVICE_TEXT in message
    assert "snapshot_payload_json" not in message
    assert "klines" not in message
    assert "open_time_ms" not in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "寰俊鍙戦€佹垚鍔" not in message
    assert "寰俊宸查€佽揪" not in message
    assert "delivered" not in message


def test_alert_uses_result_trace_id_before_request_trace_id() -> None:
    request = MarketContextSnapshotRequest(
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        trace_id="",
    )
    result = MarketContextSnapshotResult(
        status=MarketContextSnapshotStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        trace_id="trace-from-result",
        snapshot_id="snapshot-trace-test",
        blocked_reason="4h 最近每日复核未覆盖当前 snapshot 最新 K线。",
    )

    event = snapshot_alerts.build_market_context_snapshot_alert_event(request, result)
    message = format_alert_message(event)

    assert event.trace_id == "trace-from-result"
    assert "追踪ID：trace-from-result" in message
    assert SNAPSHOT_NOT_TRADING_ADVICE_TEXT in message


def test_hermes_submission_failure_adjusts_exit_code_without_changing_status() -> None:
    alert_sender = FakeAlertSender(AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED))
    result, *_ = run_snapshot_with_fakes(
        FakeSnapshotRepository(rows_4h=[]),
        request=snapshot_request(confirm_write=True, notify_on_blocked=True),
        alert_sender=alert_sender,
    )

    assert result.status == MarketContextSnapshotStatus.BLOCKED
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value


def test_market_context_sources_do_not_request_binance_modify_kline_tables_or_use_large_models() -> None:
    modules = [
        snapshot_alerts,
        snapshot_builder,
        snapshot_quality,
        snapshot_repository,
        snapshot_service,
    ]
    forbidden_imports = {
        "app.exchange.binance",
        "app.exchange.binance.rest_client",
        "app.storage.redis",
        "app.strategy",
        "app.llm",
        "app.ai",
    }
    for module in modules:
        imported_names = imported_module_names(inspect.getsource(module))
        assert forbidden_imports.isdisjoint(imported_names)

    source = "\n".join(inspect.getsource(module) for module in modules)
    source += Path("scripts/build_market_context_snapshot.py").read_text(encoding="utf-8")
    source += Path("scripts/check_kline_integrity_1d.py").read_text(encoding="utf-8")
    assert "market_context_snapshot_kline_ref" not in source
    assert "MarketContextSnapshotKlineRef" not in source
    assert "kline_ref_count" not in source
    forbidden_terms = [
        "BinanceRestClient",
        "get_klines(",
        "websocket",
        "create_" "order",
        "get_" "account",
        "get_" "position",
        "listen" "Key",
        "/fapi/v1/" "ticker",
        "DeepSeekClient",
        "openai_client",
        "StrategyRunner",
        "BaseStrategy",
    ]
    for term in forbidden_terms:
        assert term not in source
    assert "bulk_upsert(" not in source
    assert "market_kline_4h = " not in source
    assert "market_kline_1d = " not in source


def test_market_context_cli_rejects_scheduler_trigger_before_service() -> None:
    exit_code = snapshot_cli.main(["--trigger-source", "scheduler", "--dry-run"])

    assert exit_code != EXIT_SUCCESS


def test_1d_integrity_cli_is_manual_only_before_service() -> None:
    exit_code = integrity_1d_cli.main(["--trigger-source", "scheduler"])

    assert exit_code != EXIT_SUCCESS


def test_market_context_service_rejects_scheduler_trigger_in_stage_15() -> None:
    request = MarketContextSnapshotRequest(
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        trigger_source="scheduler",
        lookback_4h_count=3,
        lookback_1d_count=3,
        dry_run=True,
        confirm_write=False,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-scheduler-rejected",
    )
    result, repository, _session, _alert = run_snapshot_with_fakes(
        FakeSnapshotRepository(fail_on_read=True),
        request=request,
    )

    assert result.status == MarketContextSnapshotStatus.FAILED
    assert result.exit_code == EXIT_PARAMETER_ERROR
    assert "cli" in (result.error_message or "")
    assert repository.created_payloads == []


def test_market_context_migration_removes_obsolete_kline_ref_table_without_longtext() -> None:
    migration_text = Path("migrations/versions/20260516_15_create_market_context_snapshot.py").read_text(
        encoding="utf-8"
    )
    cleanup_migration_text = Path(
        "migrations/versions/20260517_15_remove_market_context_snapshot_kline_ref.py"
    ).read_text(encoding="utf-8")

    assert '"market_context_snapshot"' in migration_text
    assert '"market_context_snapshot_kline_ref"' in cleanup_migration_text
    assert "op.drop_table" in cleanup_migration_text
    assert "LONGTEXT" not in cleanup_migration_text.upper()
    assert "market_kline_4h" not in migration_text
    assert "market_kline_1d" not in migration_text
    assert "op.add_column" not in migration_text


def valid_4h_rows(*, count: int = 3, latest_open_ms: int = EXPECTED_4H_LATEST_MS) -> list[Any]:
    return build_kline_rows("4h", KLINE_4H_INTERVAL_MS, count=count, latest_open_ms=latest_open_ms)


def valid_1d_rows(*, count: int = 3, latest_open_ms: int = EXPECTED_1D_LATEST_MS) -> list[Any]:
    return build_kline_rows("1d", KLINE_1D_INTERVAL_MS, count=count, latest_open_ms=latest_open_ms)


def build_snapshot_row_from_rows(*, rows_4h: list[Any], rows_1d: list[Any]) -> Any:
    return SimpleNamespace(
        id=901,
        snapshot_id="MCS-BTCUSDT-4H-1D-RESTORE-TEST",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        status="created",
        start_4h_open_time_ms=rows_4h[0].open_time_ms,
        end_4h_open_time_ms=rows_4h[-1].open_time_ms,
        actual_4h_count=len(rows_4h),
        start_1d_open_time_ms=rows_1d[0].open_time_ms,
        end_1d_open_time_ms=rows_1d[-1].open_time_ms,
        actual_1d_count=len(rows_1d),
        lookback_4h_count=len(rows_4h),
        lookback_1d_count=len(rows_1d),
        trace_id="restore-trace",
    )


def build_kline_rows(interval_value: str, interval_ms: int, *, count: int, latest_open_ms: int) -> list[Any]:
    first_open_ms = latest_open_ms - (count - 1) * interval_ms
    return [
        build_kline_row(
            row_id=index + 1 if interval_value == "4h" else 10_000 + index + 1,
            interval_value=interval_value,
            interval_ms=interval_ms,
            open_time_ms=first_open_ms + index * interval_ms,
        )
        for index in range(count)
    ]


def build_kline_row(*, row_id: int, interval_value: str, interval_ms: int, open_time_ms: int) -> Any:
    return SimpleNamespace(
        id=row_id,
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        close_time_ms=open_time_ms + interval_ms - 1,
        close_time_utc=datetime.fromtimestamp((open_time_ms + interval_ms - 1) / 1000, tz=timezone.utc),
        open_price=Decimal("100.00"),
        high_price=Decimal("110.00"),
        low_price=Decimal("90.00"),
        close_price=Decimal("105.00"),
        volume=Decimal("1.23"),
        quote_volume=Decimal("123.45"),
        trade_count=123,
        taker_buy_base_volume=Decimal("0.50"),
        taker_buy_quote_volume=Decimal("50.00"),
    )


def clone_kline_with_open_time(row: Any, open_time_ms: int) -> Any:
    interval_ms = KLINE_4H_INTERVAL_MS if row.interval_value == "4h" else KLINE_1D_INTERVAL_MS
    clone = SimpleNamespace(**row.__dict__)
    clone.open_time_ms = open_time_ms
    clone.open_time_utc = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    clone.close_time_ms = open_time_ms + interval_ms - 1
    clone.close_time_utc = datetime.fromtimestamp(clone.close_time_ms / 1000, tz=timezone.utc)
    return clone


def assert_no_trading_advice_terms(payload_text: str) -> None:
    forbidden_terms = [
        "signal",
        "long",
        "short",
        "entry_price",
        "stop_loss",
        "take_profit",
        "position_size",
        "leverage",
        "stop_trading",
        "做多",
        "做空",
        "开仓",
        "平仓",
        "止盈",
        "止损",
        "仓位",
    ]
    lowered = payload_text.lower()
    for term in forbidden_terms:
        assert term.lower() not in lowered


def imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
