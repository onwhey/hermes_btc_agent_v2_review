"""Tests for stage-22B manual execution Hermes/WeChat entry.

These tests use in-memory fakes. They do not request Binance, connect MySQL or
Redis, send real Hermes messages, call large language models, modify Kline
tables, or perform automatic trading.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.core.time_utils import now_utc
from app.manual_execution.hermes_entry.constants import (
    INTENT_STATUS_CANCELLED,
    INTENT_STATUS_EXECUTED,
    INTENT_STATUS_EXPIRED,
    INTENT_STATUS_PENDING_CONFIRMATION,
    INTENT_STATUS_VALIDATION_FAILED,
    SOURCE_CHANNEL_HERMES,
)
from app.manual_execution.hermes_entry.intent_schema import (
    InboundManualExecutionMessage,
    IntentActionRequest,
    ManualExecutionIntentServiceStatus,
)
from app.manual_execution.hermes_entry.intent_service import ManualExecutionIntentService
from app.manual_execution.hermes_entry import intent_service as intent_service_module
from app.manual_execution.hermes_entry import parser as parser_module
from app.manual_execution.schema import ManualExecutionResult, ManualExecutionServiceStatus


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeIntentRepository:
    def __init__(self) -> None:
        self.intents: dict[str, Any] = {}

    def get_intent_by_id(self, db_session: Any, *, intent_id: str) -> Any | None:
        del db_session
        return self.intents.get(intent_id)

    def create_intent(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = SimpleNamespace(**payload.__dict__)
        row.executed_manual_position_id = None
        row.executed_execution_id = None
        row.confirmed_at_utc = None
        row.cancelled_at_utc = None
        row.executed_at_utc = None
        row.failed_at_utc = None
        row.is_manual = True
        row.auto_trading_allowed = False
        self.intents[row.intent_id] = row
        return row

    def mark_status(self, db_session: Any, row: Any, *, status: str, now_utc_value: Any, **kwargs: Any) -> Any:
        del db_session
        row.status = status
        row.updated_at_utc = now_utc_value
        if status == "confirmed":
            row.confirmed_at_utc = now_utc_value
        if status == "executed":
            row.executed_at_utc = now_utc_value
        if status == "cancelled":
            row.cancelled_at_utc = now_utc_value
        if status in {"expired", "execution_failed", "failed"}:
            row.failed_at_utc = now_utc_value
        for key, value in kwargs.items():
            if value is not None:
                setattr(row, key, value)
        return row


class FakeManualExecutionService:
    def __init__(self) -> None:
        self.dry_run_requests: list[Any] = []
        self.write_requests: list[Any] = []

    def record_manual_execution(self, *, db_session: Any, request: Any) -> ManualExecutionResult:
        del db_session
        if request.dry_run:
            self.dry_run_requests.append(request)
            if request.manual_position_id == "MP-NOT-FOUND":
                return ManualExecutionResult(
                    status=ManualExecutionServiceStatus.BLOCKED,
                    exit_code=2,
                    action=request.action,
                    trace_id=request.trace_id,
                    dry_run=True,
                    error_code="manual_position_not_found",
                    error_message="manual_position_id does not exist: MP-NOT-FOUND",
                )
            if not request.advice_id:
                return ManualExecutionResult(
                    status=ManualExecutionServiceStatus.BLOCKED,
                    exit_code=2,
                    action=request.action,
                    trace_id=request.trace_id,
                    dry_run=True,
                    error_code="advice_not_found",
                    error_message="advice_id is required",
                )
            return ManualExecutionResult(
                status=ManualExecutionServiceStatus.DRY_RUN,
                exit_code=0,
                action=request.action,
                trace_id=request.trace_id,
                dry_run=True,
                manual_position_id=request.manual_position_id or "MP-PREVIEW",
                execution_id="MEX-PREVIEW",
                execution_snapshot={"fee_usdt": "0.06"},
            )
        self.write_requests.append(request)
        return ManualExecutionResult(
            status=ManualExecutionServiceStatus.SUCCESS,
            exit_code=0,
            action=request.action,
            trace_id=request.trace_id,
            dry_run=False,
            database_written=True,
            manual_position_id=request.manual_position_id or "MP-WRITTEN",
            execution_id=f"MEX-{len(self.write_requests)}",
            execution_snapshot={"fee_usdt": "0.06"},
        )


class FakeAlertSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> AlertSendResult:
        self.calls.append({"event": event, **kwargs})
        return AlertSendResult(status=AlertSendStatus.SKIPPED)


def test_natural_language_open_creates_pending_intent_only() -> None:
    repo, manual_service, _, result = _create_intent(_open_text())

    assert result.status == ManualExecutionIntentServiceStatus.PENDING_CONFIRMATION
    assert result.intent_id in repo.intents
    assert repo.intents[result.intent_id].status == INTENT_STATUS_PENDING_CONFIRMATION
    assert manual_service.write_requests == []
    assert len(manual_service.dry_run_requests) == 1


def test_confirm_mei_calls_stage_22a_write_successfully() -> None:
    repo, manual_service, session, result = _create_intent(_open_text())

    confirm_result = _service(repo, manual_service).confirm_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )

    assert confirm_result.status == ManualExecutionIntentServiceStatus.EXECUTED
    assert repo.intents[result.intent_id].status == INTENT_STATUS_EXECUTED
    assert confirm_result.manual_execution_database_written is True
    assert len(manual_service.write_requests) == 1


def test_duplicate_confirm_is_idempotent_and_does_not_write_twice() -> None:
    repo, manual_service, session, result = _create_intent(_open_text())
    service = _service(repo, manual_service)

    first = service.confirm_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )
    second = service.confirm_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )

    assert first.status == ManualExecutionIntentServiceStatus.EXECUTED
    assert second.idempotent is True
    assert len(manual_service.write_requests) == 1


def test_cancelled_intent_cannot_be_confirmed() -> None:
    repo, manual_service, session, result = _create_intent(_open_text())
    service = _service(repo, manual_service)

    cancel_result = service.cancel_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )
    confirm_result = service.confirm_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )

    assert cancel_result.status == ManualExecutionIntentServiceStatus.CANCELLED
    assert repo.intents[result.intent_id].status == INTENT_STATUS_CANCELLED
    assert confirm_result.status == ManualExecutionIntentServiceStatus.BLOCKED
    assert manual_service.write_requests == []


def test_expired_intent_cannot_be_confirmed() -> None:
    repo, manual_service, session, result = _create_intent(_open_text())
    repo.intents[result.intent_id].expires_at_utc = now_utc() - timedelta(minutes=1)

    confirm_result = _service(repo, manual_service).confirm_manual_execution_intent(
        db_session=session,
        request=IntentActionRequest(intent_id=result.intent_id or "", source_channel=SOURCE_CHANNEL_HERMES),
    )

    assert confirm_result.status == ManualExecutionIntentServiceStatus.EXPIRED
    assert repo.intents[result.intent_id].status == INTENT_STATUS_EXPIRED
    assert manual_service.write_requests == []


def test_missing_required_fields_are_blocked_before_stage_22a() -> None:
    repo, manual_service, _, result = _create_intent(
        "开多 BTCUSDT 成交价 60000 保证金 100U advice_id=ADV-1"
    )

    assert result.status == ManualExecutionIntentServiceStatus.VALIDATION_FAILED
    assert repo.intents[result.intent_id].status == INTENT_STATUS_VALIDATION_FAILED
    assert "notional_usdt" in (result.error_message or "")
    assert manual_service.dry_run_requests == []
    assert manual_service.write_requests == []


def test_wrong_manual_position_id_is_blocked_by_stage_22a_dry_run() -> None:
    repo, manual_service, _, result = _create_intent(
        "加仓 BTCUSDT 多单 MP-NOT-FOUND 成交价 60000 金额 500U 保证金 0U advice_id=ADV-1"
    )

    assert result.status == ManualExecutionIntentServiceStatus.VALIDATION_FAILED
    assert result.error_code == "manual_position_not_found"
    assert repo.intents[result.intent_id].status == INTENT_STATUS_VALIDATION_FAILED
    assert len(manual_service.dry_run_requests) == 1
    assert manual_service.write_requests == []


def test_add_position_zero_margin_is_parsed_as_zero_decimal() -> None:
    _, manual_service, _, result = _create_intent(
        "加仓 BTCUSDT 多单 MP-ABCDEF123456 成交价 60000 金额 500U 保证金 0U advice_id=ADV-1"
    )

    assert result.status == ManualExecutionIntentServiceStatus.PENDING_CONFIRMATION
    assert manual_service.dry_run_requests[0].margin_usdt == 0


def test_close_position_does_not_require_notional_or_margin() -> None:
    _, manual_service, _, result = _create_intent(
        "平仓 BTCUSDT 多单 MP-ABCDEF123456 成交价 62000 advice_id=ADV-1"
    )

    assert result.status == ManualExecutionIntentServiceStatus.PENDING_CONFIRMATION
    assert manual_service.dry_run_requests[0].notional_usdt is None
    assert manual_service.dry_run_requests[0].margin_usdt is None


def test_stage_22b_parser_and_service_do_not_reference_large_model_providers() -> None:
    combined_source = inspect.getsource(parser_module) + inspect.getsource(intent_service_module)

    assert "DeepSeek" not in combined_source
    assert "OpenAI" not in combined_source
    assert "Claude" not in combined_source


def _create_intent(text: str) -> tuple[FakeIntentRepository, FakeManualExecutionService, FakeSession, Any]:
    repo = FakeIntentRepository()
    manual_service = FakeManualExecutionService()
    session = FakeSession()
    result = _service(repo, manual_service).create_manual_execution_intent(
        db_session=session,
        message=InboundManualExecutionMessage(
            text=text,
            source_channel=SOURCE_CHANNEL_HERMES,
            confirm_write=True,
            dry_run=False,
        ),
    )
    return repo, manual_service, session, result


def _service(repo: FakeIntentRepository, manual_service: FakeManualExecutionService) -> ManualExecutionIntentService:
    return ManualExecutionIntentService(
        intent_repository=repo,
        manual_execution_service=manual_service,  # type: ignore[arg-type]
        settings=AppSettings(
            manual_execution_fee_rate="0.0002",
            manual_execution_receipt_send_enabled=False,
            manual_execution_hermes_entry_enabled=True,
            manual_execution_hermes_reply_send_enabled=False,
            manual_execution_intent_expire_minutes=10,
        ),
        alert_sender=FakeAlertSender(),
    )


def _open_text() -> str:
    return "开多 BTCUSDT 成交价 60000 金额 300U 保证金 100U advice_id=ADV-1"
