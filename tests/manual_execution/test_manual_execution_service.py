"""Tests for stage-22A manual execution feedback.

These tests use in-memory fakes. They do not request Binance, connect MySQL or
Redis, send real Hermes messages, call DeepSeek, modify Kline tables, or perform
automatic trading.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.manual_execution.schema import (
    ManualExecutionRequest,
    ManualExecutionServiceStatus,
    ManualPositionListRequest,
)
from app.manual_execution.service import ManualExecutionService


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeManualExecutionRepository:
    """In-memory repository for side-effect-free stage-22A tests."""

    def __init__(self) -> None:
        self.advices: set[str] = {"ADV-1", "ADV-2"}
        self.review_ids_by_advice: dict[str, tuple[str, ...]] = {"ADV-1": ("ADVR-1",)}
        self.setup_ids_by_advice: dict[str, tuple[str, ...]] = {"ADV-1": ("SETUP-1",)}
        self.positions: dict[str, Any] = {}
        self.executions: list[Any] = []

    def get_advice_by_id(self, db_session: Any, *, advice_id: str) -> Any | None:
        del db_session
        return SimpleNamespace(advice_id=advice_id) if advice_id in self.advices else None

    def find_review_ids_for_advice(self, db_session: Any, *, advice_id: str) -> tuple[str, ...]:
        del db_session
        return self.review_ids_by_advice.get(advice_id, ())

    def find_setup_ids_for_advice(self, db_session: Any, *, advice_id: str) -> tuple[str, ...]:
        del db_session
        return self.setup_ids_by_advice.get(advice_id, ())

    def get_manual_position_by_id(self, db_session: Any, *, manual_position_id: str) -> Any | None:
        del db_session
        return self.positions.get(manual_position_id)

    def find_open_manual_positions(self, db_session: Any, *, symbol: str, side: str) -> tuple[Any, ...]:
        del db_session
        return tuple(
            row
            for row in self.positions.values()
            if row.symbol == symbol and row.side == side and row.status == "open"
        )

    def list_manual_positions(self, db_session: Any, *, status: str, symbol: str | None = None) -> tuple[Any, ...]:
        del db_session
        return tuple(
            row
            for row in self.positions.values()
            if row.status == status and (symbol is None or row.symbol == symbol)
        )

    def list_execution_records_for_position(self, db_session: Any, *, manual_position_id: str) -> tuple[Any, ...]:
        del db_session
        return tuple(row for row in self.executions if row.manual_position_id == manual_position_id)

    def create_manual_position(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _row_from_payload(payload)
        self.positions[row.manual_position_id] = row
        return row

    def create_execution_record(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _row_from_payload(payload)
        self.executions.append(row)
        return row

    def update_manual_position_from_payload(self, db_session: Any, manual_position_row: Any, *, payload: Any) -> Any:
        del db_session
        for key, value in payload.__dict__.items():
            if key in {"manual_position_id", "opened_at_utc", "created_at_utc", "created_by", "trace_id"}:
                continue
            setattr(manual_position_row, key, value)
        return manual_position_row


class FakeAlertSender:
    def __init__(self, status: AlertSendStatus = AlertSendStatus.SKIPPED) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> AlertSendResult:
        self.calls.append({"event": event, **kwargs})
        return AlertSendResult(status=self.status, error_message="failed" if self.status != AlertSendStatus.SKIPPED else "")


def test_open_position_creates_manual_position_and_execution_record() -> None:
    repo, session, result = _run(_open_request(confirm=True))

    assert result.status == ManualExecutionServiceStatus.SUCCESS
    assert result.database_written is True
    assert result.manual_position_id in repo.positions
    position = repo.positions[result.manual_position_id]
    execution = repo.executions[0]
    assert position.status == "open"
    assert position.avg_entry_price == Decimal("60000.000000000000000000")
    assert position.current_quantity_base_asset == Decimal("0.005000000000000000")
    assert position.margin_basis_usdt == Decimal("100.000000000000000000")
    assert position.effective_leverage == Decimal("3.000000000000000000")
    assert execution.advice_id == "ADV-1"
    assert execution.review_id == "ADVR-1"
    assert execution.setup_id == "SETUP-1"
    assert session.commit_count == 1


def test_open_position_missing_advice_id_is_blocked() -> None:
    repo, session, result = _run(_open_request(advice_id="", confirm=True))

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert repo.positions == {}
    assert repo.executions == []
    assert session.commit_count == 0


def test_open_position_margin_must_be_greater_than_one() -> None:
    repo, _, result = _run(_open_request(margin_usdt="1", confirm=True))

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert result.error_code == "invalid_request"
    assert repo.positions == {}


def test_add_position_with_zero_margin_keeps_margin_basis_and_raises_effective_leverage() -> None:
    repo, _, opened = _run(_open_request(confirm=True))
    result = _record(repo, _add_request(opened.manual_position_id, margin_usdt="0", confirm=True))

    position = repo.positions[opened.manual_position_id]
    assert result.status == ManualExecutionServiceStatus.SUCCESS
    assert position.margin_basis_usdt == Decimal("100.000000000000000000")
    assert position.current_cost_basis_usdt == Decimal("800.000000000000000000")
    assert position.effective_leverage == Decimal("8.000000000000000000")
    assert result.warnings


def test_add_position_with_positive_margin_accumulates_margin_basis() -> None:
    repo, _, opened = _run(_open_request(confirm=True))
    _record(repo, _add_request(opened.manual_position_id, margin_usdt="50", confirm=True))

    position = repo.positions[opened.manual_position_id]
    assert position.margin_basis_usdt == Decimal("150.000000000000000000")
    assert position.effective_leverage == Decimal("5.333333333333333333")


def test_wrong_manual_position_id_is_blocked_and_sends_error_alert() -> None:
    alert_sender = FakeAlertSender()
    repo, session, result = _run(
        _add_request("MP-NOT-FOUND", confirm=True),
        alert_sender=alert_sender,
    )

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert repo.executions == []
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].alert_type.value == "manual_execution_error"
    assert session.commit_count == 1


def test_reduce_position_keeps_average_entry_and_margin_basis() -> None:
    repo, _, opened = _run(_open_request(confirm=True))
    _record(repo, _add_request(opened.manual_position_id, margin_usdt="0", confirm=True))
    old_avg = repo.positions[opened.manual_position_id].avg_entry_price

    result = _record(repo, _reduce_request(opened.manual_position_id, price="60000", notional_usdt="500", confirm=True))

    position = repo.positions[opened.manual_position_id]
    assert result.status == ManualExecutionServiceStatus.SUCCESS
    assert position.avg_entry_price == old_avg
    assert position.margin_basis_usdt == Decimal("100.000000000000000000")
    assert position.current_quantity_base_asset == Decimal("0.005000000000000000")
    assert position.current_cost_basis_usdt == Decimal("300.000000000000000000")


def test_long_reduce_profit_formula_is_correct() -> None:
    repo, _, opened = _run(_open_request(confirm=True))

    _record(repo, _reduce_request(opened.manual_position_id, price="66000", notional_usdt="66", confirm=True))

    execution = repo.executions[-1]
    assert execution.quantity_base_asset == Decimal("0.001000000000000000")
    assert execution.gross_pnl_usdt == Decimal("6.000000000000000000")
    assert execution.net_pnl_usdt == Decimal("5.986800000000000000")


def test_short_reduce_profit_formula_is_correct() -> None:
    repo, _, opened = _run(_open_request(side="short", confirm=True))

    _record(repo, _reduce_request(opened.manual_position_id, side="short", price="54000", notional_usdt="54", confirm=True))

    execution = repo.executions[-1]
    assert execution.quantity_base_asset == Decimal("0.001000000000000000")
    assert execution.gross_pnl_usdt == Decimal("6.000000000000000000")
    assert execution.net_pnl_usdt == Decimal("5.989200000000000000")


def test_reduce_position_exceeding_quantity_is_blocked() -> None:
    repo, _, opened = _run(_open_request(confirm=True))

    result = _record(repo, _reduce_request(opened.manual_position_id, price="60000", notional_usdt="301", confirm=True))

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert len(repo.executions) == 1


def test_close_position_closes_position_and_zeroes_quantity_and_cost_basis() -> None:
    alert_sender = FakeAlertSender()
    repo, _, opened = _run(_open_request(confirm=True), alert_sender=alert_sender)

    result = _record(repo, _close_request(opened.manual_position_id, price="62000", confirm=True), alert_sender=alert_sender)

    position = repo.positions[opened.manual_position_id]
    assert result.status == ManualExecutionServiceStatus.SUCCESS
    assert position.status == "closed"
    assert position.current_quantity_base_asset == Decimal("0")
    assert position.current_cost_basis_usdt == Decimal("0")
    assert position.close_price == Decimal("62000.000000000000000000")
    assert position.total_close_notional_usdt == Decimal("310.000000000000000000")
    assert result.receipt_status == "skipped"
    assert len(alert_sender.calls) == 1


def test_take_profit_and_stop_loss_use_full_close_logic() -> None:
    repo, _, take_opened = _run(_open_request(confirm=True))
    take_result = _record(repo, _close_request(take_opened.manual_position_id, action="take_profit", price="62000", confirm=True))
    _, _, stop_opened = _run(_open_request(confirm=True), repo=repo)
    stop_result = _record(repo, _close_request(stop_opened.manual_position_id, action="stop_loss", price="59000", confirm=True))

    assert take_result.status == ManualExecutionServiceStatus.SUCCESS
    assert stop_result.status == ManualExecutionServiceStatus.SUCCESS
    assert repo.positions[take_opened.manual_position_id].status == "closed"
    assert repo.positions[stop_opened.manual_position_id].status == "closed"


def test_fee_accumulation_and_net_total_use_all_execution_fees_once() -> None:
    repo, _, opened = _run(_open_request(confirm=True))
    _record(repo, _add_request(opened.manual_position_id, margin_usdt="0", confirm=True))
    _record(repo, _reduce_request(opened.manual_position_id, price="60000", notional_usdt="500", confirm=True))

    position = repo.positions[opened.manual_position_id]
    assert position.total_fee_usdt == Decimal("0.260000000000000000")
    assert position.gross_realized_pnl_usdt == Decimal("0E-18")
    assert position.net_realized_pnl_usdt == Decimal("-0.260000000000000000")


def test_advice_id_is_required_but_not_unique_for_execution_records() -> None:
    repo, _, opened = _run(_open_request(advice_id="ADV-1", confirm=True))
    _record(repo, _add_request(opened.manual_position_id, advice_id="ADV-1", margin_usdt="0", confirm=True))

    assert [row.advice_id for row in repo.executions] == ["ADV-1", "ADV-1"]


def test_dry_run_does_not_write_database() -> None:
    repo, session, result = _run(_open_request())

    assert result.status == ManualExecutionServiceStatus.DRY_RUN
    assert result.database_written is False
    assert repo.positions == {}
    assert repo.executions == []
    assert session.commit_count == 0


def test_receipt_failure_does_not_rollback_manual_execution_rows() -> None:
    alert_sender = FakeAlertSender(status=AlertSendStatus.SUBMIT_FAILED)
    repo, session, opened = _run(_open_request(confirm=True), alert_sender=alert_sender)

    result = _record(repo, _close_request(opened.manual_position_id, confirm=True), alert_sender=alert_sender)

    assert result.database_written is True
    assert result.receipt_failed is True
    assert repo.positions[opened.manual_position_id].status == "closed"
    assert session.rollback_count == 0


def test_check_manual_positions_lists_open_rows() -> None:
    repo, session, opened = _run(_open_request(confirm=True))
    service = _service(repo)

    result = service.list_manual_positions(
        db_session=session,
        request=ManualPositionListRequest(symbol="BTCUSDT", status="open", trigger_source="cli"),
    )

    assert result.status == ManualExecutionServiceStatus.SUCCESS
    assert len(result.positions) == 1
    assert result.positions[0].manual_position_id == opened.manual_position_id


def test_multiple_open_positions_require_manual_position_id() -> None:
    repo, _, _ = _run(_open_request(confirm=True))
    _run(_open_request(confirm=True), repo=repo)

    result = _record(repo, _add_request(None, margin_usdt="0", confirm=True))

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert result.error_code == "multiple_open_manual_positions"


def test_decimal_inputs_reject_float_values() -> None:
    repo, _, result = _run(_open_request(price=60000.0, confirm=True))

    assert result.status == ManualExecutionServiceStatus.BLOCKED
    assert repo.positions == {}


def _run(
    request: ManualExecutionRequest,
    *,
    repo: FakeManualExecutionRepository | None = None,
    alert_sender: FakeAlertSender | None = None,
) -> tuple[FakeManualExecutionRepository, FakeSession, Any]:
    active_repo = repo or FakeManualExecutionRepository()
    session = FakeSession()
    result = _service(active_repo, alert_sender=alert_sender).record_manual_execution(
        db_session=session,
        request=request,
    )
    return active_repo, session, result


def _record(
    repo: FakeManualExecutionRepository,
    request: ManualExecutionRequest,
    *,
    alert_sender: FakeAlertSender | None = None,
) -> Any:
    session = FakeSession()
    return _service(repo, alert_sender=alert_sender).record_manual_execution(db_session=session, request=request)


def _service(repo: FakeManualExecutionRepository, alert_sender: FakeAlertSender | None = None) -> ManualExecutionService:
    return ManualExecutionService(
        repository=repo,
        settings=AppSettings(
            manual_execution_fee_rate="0.0002",
            manual_execution_receipt_send_enabled=False,
        ),
        alert_sender=alert_sender or FakeAlertSender(),
    )


def _open_request(
    *,
    advice_id: str = "ADV-1",
    side: str = "long",
    price: Any = "60000",
    notional_usdt: Any = "300",
    margin_usdt: Any = "100",
    confirm: bool = False,
) -> ManualExecutionRequest:
    return ManualExecutionRequest(
        action="open_position",
        advice_id=advice_id,
        symbol="BTCUSDT",
        side=side,
        price=price,
        notional_usdt=notional_usdt,
        margin_usdt=margin_usdt,
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
    )


def _add_request(
    manual_position_id: str | None,
    *,
    advice_id: str = "ADV-1",
    side: str = "long",
    price: Any = "60000",
    notional_usdt: Any = "500",
    margin_usdt: Any = "0",
    confirm: bool = False,
) -> ManualExecutionRequest:
    return ManualExecutionRequest(
        action="add_position",
        advice_id=advice_id,
        symbol="BTCUSDT",
        side=side,
        price=price,
        notional_usdt=notional_usdt,
        margin_usdt=margin_usdt,
        manual_position_id=manual_position_id,
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
    )


def _reduce_request(
    manual_position_id: str,
    *,
    side: str = "long",
    price: Any = "60000",
    notional_usdt: Any = "100",
    confirm: bool = False,
) -> ManualExecutionRequest:
    return ManualExecutionRequest(
        action="reduce_position",
        advice_id="ADV-1",
        symbol="BTCUSDT",
        side=side,
        price=price,
        notional_usdt=notional_usdt,
        manual_position_id=manual_position_id,
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
    )


def _close_request(
    manual_position_id: str,
    *,
    action: str = "close_position",
    price: Any = "62000",
    confirm: bool = False,
) -> ManualExecutionRequest:
    return ManualExecutionRequest(
        action=action,
        advice_id="ADV-1",
        symbol="BTCUSDT",
        side="long",
        price=price,
        manual_position_id=manual_position_id,
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
    )


def _row_from_payload(payload: Any) -> Any:
    values = dict(payload.__dict__)
    values["is_manual"] = True
    values["auto_trading_allowed"] = False
    return SimpleNamespace(**values)

