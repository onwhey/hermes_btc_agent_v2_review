"""Stage-22A manual execution feedback service.

调用链：
用户 CLI
    ↓
scripts.record_manual_execution.py::main
    ↓
app.manual_execution.service.py::record_manual_execution
    ↓
app.manual_execution.repository.py::ManualExecutionRepository
    ↓
app.storage.mysql.models.manual_execution::{StrategyAdviceManualPosition, StrategyAdviceExecutionRecord}

本文件属于 `app/manual_execution`，负责校验用户主动反馈的人工执行动作、
调用 Decimal 计算、控制事务写入两张 22A 表，并在仓位关闭后通过统一
alerting 模块生成中文结算回执。
本文件不请求 Binance，不读取交易所账户，不同步真实仓位，不修改 K 线表，
不修改 strategy_advice 生命周期状态，不读写 Redis，不调用 DeepSeek，也不执行自动交易。
"""

from __future__ import annotations

from typing import Any, Callable

from app.alerting.service import send_alert
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings, get_settings
from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from app.core.time_utils import now_utc
from app.manual_execution.calculations import (
    ManualExecutionMath,
    ManualPositionState,
    calculate_existing_position_action,
    calculate_open_position,
    execution_snapshot,
    position_snapshot,
    state_from_row,
)
from app.manual_execution.constants import (
    ACTION_OPEN_POSITION,
    ALLOWED_EXECUTION_ACTIONS,
    ALLOWED_MANUAL_POSITION_STATUSES,
    ALLOWED_MANUAL_SIDES,
    MANUAL_TRIGGER_SOURCE_CLI,
    POSITION_STATUS_OPEN,
    RESOLUTION_AUTO_SINGLE_OPEN_POSITION,
    RESOLUTION_DIRECT,
    RESOLUTION_NOT_FOUND,
    RESOLUTION_NOT_REQUIRED_NEW_POSITION,
    RESOLUTION_NOT_UNIQUE,
    RESOLUTION_UNIQUE_BY_ADVICE_ID,
)
from app.manual_execution.decimal_utils import (
    parse_decimal_value,
    parse_fee_rate,
    parse_optional_decimal_value,
)
from app.manual_execution.id_utils import build_manual_execution_id, build_manual_position_id
from app.manual_execution.payloads import (
    execution_payload_from_math,
    position_payload_from_state,
    summary_from_row,
)
from app.manual_execution.receipt import (
    build_manual_execution_error_event,
    build_manual_execution_receipt_event,
    render_manual_execution_close_receipt,
)
from app.manual_execution.repository import ManualExecutionRepository, create_default_manual_execution_repository
from app.manual_execution.schema import (
    AdviceResolution,
    ManualExecutionRequest,
    ManualExecutionResult,
    ManualExecutionServiceStatus,
    ManualPositionListRequest,
    ManualPositionListResult,
    blocked_result,
    failed_result,
    success_exit_code,
)
from app.storage.mysql.repositories.alert_message_repository import (
    AlertMessageRepository,
    create_default_alert_message_repository,
)

AlertSender = Callable[..., AlertSendResult]
RECEIPT_FAILURE_ERROR_CODE = "manual_execution_receipt_failed"
RECEIPT_FAILURE_MESSAGE = "数据库已写入，但 Hermes 回执失败"


class ManualExecutionService:
    """Business service for stage-22A manual execution feedback.

    Parameters: repository and alert dependencies are injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid user input returns blocked; database failures
    return failed after rollback; Hermes failures after a committed close write
    are reported without rolling back manual execution rows.
    External services: only the alerting module may call Hermes when explicitly
    enabled by config.
    Data impact: may write `strategy_advice_manual_position`,
    `strategy_advice_execution_record`, and `alert_message`.
    This class does not read exchange state, write Redis, alter Kline tables,
    call DeepSeek, or perform automatic trading.
    """

    def __init__(
        self,
        *,
        repository: ManualExecutionRepository | None = None,
        alert_repository: AlertMessageRepository | None = None,
        settings: AppSettings | None = None,
        alert_sender: AlertSender = send_alert,
    ) -> None:
        self._repository = repository or create_default_manual_execution_repository()
        self._alert_repository = alert_repository or create_default_alert_message_repository()
        self._settings = settings
        self._alert_sender = alert_sender
        self._logger = get_logger("manual_execution.service")

    def record_manual_execution(self, *, db_session: Any, request: ManualExecutionRequest) -> ManualExecutionResult:
        """Validate, preview, or persist one manual execution feedback action."""

        try:
            self._validate_common_request(request)
            settings = self._settings or get_settings()
            fee_rate = parse_fee_rate(settings.manual_execution_fee_rate)
            price = parse_decimal_value(request.price, "price")
            notional = parse_optional_decimal_value(request.notional_usdt, "notional_usdt")
            margin = parse_optional_decimal_value(request.margin_usdt, "margin_usdt")
            advice = self._resolve_advice(db_session, advice_id=request.advice_id.strip())
            if advice is None:
                return blocked_result(
                    request=request,
                    error_code="advice_not_found",
                    error_message=f"advice_id does not exist: {request.advice_id}",
                )
            advice_resolution = self._resolve_advice_metadata(db_session, advice_id=request.advice_id.strip())
            position_row, position_resolution, blocked = self._resolve_manual_position(
                db_session=db_session,
                request=request,
            )
            if blocked is not None:
                if request.manual_position_id and not request.dry_run:
                    self._send_manual_position_error_alert(
                        db_session=db_session,
                        request=request,
                        reason=blocked.error_message or blocked.error_code or "manual_position_id invalid",
                    )
                return blocked

            executed_at_utc = now_utc()
            if request.action == ACTION_OPEN_POSITION:
                if notional is None:
                    raise ValidationError("open_position notional_usdt is required")
                if margin is None:
                    raise ValidationError("open_position margin_usdt is required")
                math = calculate_open_position(
                    manual_position_id=build_manual_position_id(),
                    symbol=request.symbol.strip().upper(),
                    side=request.side.strip(),
                    advice_id=request.advice_id.strip(),
                    price=price,
                    notional_usdt=notional,
                    margin_usdt=margin,
                    fee_rate=fee_rate,
                    reason=request.reason.strip(),
                    note=request.note.strip(),
                    trigger_source=request.trigger_source.strip(),
                    created_by=request.created_by.strip() or "cli",
                    trace_id=request.trace_id,
                    executed_at_utc=executed_at_utc,
                )
            else:
                if position_row is None:
                    return blocked_result(
                        request=request,
                        error_code="manual_position_not_resolved",
                        error_message="manual_position_id is required unless exactly one open position matches symbol and side",
                    )
                math = calculate_existing_position_action(
                    action=request.action,
                    state=state_from_row(position_row),
                    advice_id=request.advice_id.strip(),
                    price=price,
                    notional_usdt=notional,
                    margin_usdt=margin,
                    fee_rate=fee_rate,
                    executed_at_utc=executed_at_utc,
                )
            return self._preview_or_persist(
                db_session=db_session,
                request=request,
                position_row=position_row,
                position_resolution=position_resolution,
                advice_resolution=advice_resolution,
                math=math,
                executed_at_utc=executed_at_utc,
            )
        except ValidationError as exc:
            return blocked_result(request=request, error_code="invalid_request", error_message=str(exc))
        except Exception as exc:  # noqa: BLE001 - service boundary converts unexpected failures to explicit result.
            _rollback_if_possible(db_session)
            return failed_result(
                request=request,
                error_code="manual_execution_failed",
                error_message=str(exc),
            )

    def list_manual_positions(self, *, db_session: Any, request: ManualPositionListRequest) -> ManualPositionListResult:
        """List manual positions for CLI inspection without modifying data."""

        trace_id = getattr(request, "trace_id", "") or ""
        if not trace_id:
            from uuid import uuid4

            trace_id = uuid4().hex
        try:
            if request.trigger_source != MANUAL_TRIGGER_SOURCE_CLI:
                raise ValidationError("check_manual_positions only supports trigger_source=cli")
            if request.status not in ALLOWED_MANUAL_POSITION_STATUSES:
                raise ValidationError("status must be open or closed")
            rows = self._repository.list_manual_positions(
                db_session,
                status=request.status,
                symbol=request.symbol.strip().upper() if request.symbol else None,
            )
            return ManualPositionListResult(
                status=ManualExecutionServiceStatus.SUCCESS,
                exit_code=0,
                trace_id=trace_id,
                positions=tuple(summary_from_row(row) for row in rows),
            )
        except ValidationError as exc:
            return ManualPositionListResult(
                status=ManualExecutionServiceStatus.BLOCKED,
                exit_code=2,
                trace_id=trace_id,
                error_code="invalid_request",
                error_message=str(exc),
            )

    def _validate_common_request(self, request: ManualExecutionRequest) -> None:
        if request.action not in ALLOWED_EXECUTION_ACTIONS:
            raise ValidationError(f"unsupported execution_action: {request.action}")
        if not request.advice_id.strip():
            raise ValidationError("advice_id is required")
        if not request.symbol.strip():
            raise ValidationError("symbol is required")
        if request.side.strip() not in ALLOWED_MANUAL_SIDES:
            raise ValidationError("side must be long or short")
        if request.trigger_source.strip() != MANUAL_TRIGGER_SOURCE_CLI:
            raise ValidationError("record_manual_execution only supports trigger_source=cli in stage 22A")
        if request.dry_run and request.confirm_write:
            raise ValidationError("dry_run and confirm_write cannot both be true")
        if not request.dry_run and not request.confirm_write:
            raise ValidationError("non-dry-run manual execution requires confirm_write")

    def _resolve_advice(self, db_session: Any, *, advice_id: str) -> Any | None:
        return self._repository.get_advice_by_id(db_session, advice_id=advice_id)

    def _resolve_advice_metadata(self, db_session: Any, *, advice_id: str) -> AdviceResolution:
        review_ids = self._repository.find_review_ids_for_advice(db_session, advice_id=advice_id)
        setup_ids = self._repository.find_setup_ids_for_advice(db_session, advice_id=advice_id)
        return AdviceResolution(
            advice_id=advice_id,
            review_id=review_ids[0] if len(review_ids) == 1 else None,
            setup_id=setup_ids[0] if len(setup_ids) == 1 else None,
            advice_resolution_method=RESOLUTION_DIRECT,
            setup_resolution_method=(
                RESOLUTION_UNIQUE_BY_ADVICE_ID
                if len(setup_ids) == 1
                else RESOLUTION_NOT_FOUND
                if not setup_ids
                else RESOLUTION_NOT_UNIQUE
            ),
        )

    def _resolve_manual_position(
        self,
        *,
        db_session: Any,
        request: ManualExecutionRequest,
    ) -> tuple[Any | None, str, ManualExecutionResult | None]:
        if request.action == ACTION_OPEN_POSITION:
            return None, RESOLUTION_NOT_REQUIRED_NEW_POSITION, None
        symbol = request.symbol.strip().upper()
        side = request.side.strip()
        if request.manual_position_id:
            row = self._repository.get_manual_position_by_id(
                db_session,
                manual_position_id=request.manual_position_id.strip(),
            )
            if row is None:
                return None, RESOLUTION_NOT_FOUND, blocked_result(
                    request=request,
                    error_code="manual_position_not_found",
                    error_message=f"manual_position_id does not exist: {request.manual_position_id}",
                )
            blocked = _validate_position_matches_request(row=row, request=request)
            return row, RESOLUTION_DIRECT, blocked
        rows = self._repository.find_open_manual_positions(db_session, symbol=symbol, side=side)
        if len(rows) == 1:
            return rows[0], RESOLUTION_AUTO_SINGLE_OPEN_POSITION, None
        if not rows:
            return None, RESOLUTION_NOT_FOUND, blocked_result(
                request=request,
                error_code="manual_position_required",
                error_message="no open manual_position matches symbol and side",
            )
        return None, RESOLUTION_NOT_UNIQUE, blocked_result(
            request=request,
            error_code="multiple_open_manual_positions",
            error_message="multiple open manual_position rows match symbol and side; manual_position_id is required",
        )

    def _preview_or_persist(
        self,
        *,
        db_session: Any,
        request: ManualExecutionRequest,
        position_row: Any | None,
        position_resolution: str,
        advice_resolution: AdviceResolution,
        math: ManualExecutionMath,
        executed_at_utc: Any,
    ) -> ManualExecutionResult:
        execution_id = build_manual_execution_id()
        position_payload = position_payload_from_state(math.position_after)
        execution_payload = execution_payload_from_math(
            request=request,
            math=math,
            execution_id=execution_id,
            advice_resolution=advice_resolution,
            position_resolution=position_resolution,
            executed_at_utc=executed_at_utc,
        )
        if request.dry_run:
            return ManualExecutionResult(
                status=ManualExecutionServiceStatus.DRY_RUN,
                exit_code=success_exit_code(dry_run=True),
                action=request.action,
                trace_id=request.trace_id,
                dry_run=True,
                manual_position_id=math.position_after.manual_position_id,
                execution_id=execution_id,
                warnings=math.warnings,
                position_snapshot=position_snapshot(math.position_after),
                execution_snapshot=execution_snapshot(math),
            )
        try:
            if request.action == ACTION_OPEN_POSITION:
                self._repository.create_manual_position(db_session, payload=position_payload)
            elif position_row is not None:
                self._repository.update_manual_position_from_payload(db_session, position_row, payload=position_payload)
            self._repository.create_execution_record(db_session, payload=execution_payload)
            _commit_if_possible(db_session)
        except Exception:
            _rollback_if_possible(db_session)
            raise

        receipt_status: str | None = None
        receipt_failed = False
        receipt_message = ""
        result_status = ManualExecutionServiceStatus.SUCCESS
        error_code: str | None = None
        error_message: str | None = None
        if math.position_after.status != POSITION_STATUS_OPEN:
            try:
                receipt_message, receipt_status, receipt_failed = self._send_close_receipt(
                    db_session=db_session,
                    request=request,
                    position_state=math.position_after,
                )
            except Exception as exc:  # noqa: BLE001 - receipt failure must not undo committed manual execution rows.
                receipt_status = "receipt_failed"
                receipt_failed = True
                result_status = ManualExecutionServiceStatus.PARTIAL_SUCCESS
                error_code = RECEIPT_FAILURE_ERROR_CODE
                error_message = f"{RECEIPT_FAILURE_MESSAGE}: {exc}"
                self._logger.error(
                    "manual execution receipt failed after committed database write: trace_id=%s error=%s",
                    request.trace_id,
                    exc,
                )
            if receipt_failed:
                result_status = ManualExecutionServiceStatus.PARTIAL_SUCCESS
                error_code = RECEIPT_FAILURE_ERROR_CODE
                error_message = error_message or RECEIPT_FAILURE_MESSAGE
        return ManualExecutionResult(
            status=result_status,
            exit_code=success_exit_code(dry_run=False),
            action=request.action,
            trace_id=request.trace_id,
            dry_run=False,
            manual_position_id=math.position_after.manual_position_id,
            execution_id=execution_id,
            database_written=True,
            receipt_required=math.position_after.status != POSITION_STATUS_OPEN,
            receipt_status=receipt_status,
            receipt_failed=receipt_failed,
            error_code=error_code,
            error_message=error_message,
            warnings=math.warnings,
            position_snapshot=position_snapshot(math.position_after),
            execution_snapshot=execution_snapshot(math),
            receipt_message=receipt_message,
        )

    def _send_close_receipt(
        self,
        *,
        db_session: Any,
        request: ManualExecutionRequest,
        position_state: ManualPositionState,
    ) -> tuple[str, str, bool]:
        execution_records = self._repository.list_execution_records_for_position(
            db_session,
            manual_position_id=position_state.manual_position_id,
        )
        receipt_text = render_manual_execution_close_receipt(
            position_state=position_state,
            execution_records=execution_records,
        )
        event = build_manual_execution_receipt_event(
            position_state=position_state,
            receipt_text=receipt_text,
            trace_id=request.trace_id,
        )
        return self._send_alert_and_commit(db_session=db_session, event=event, receipt_text=receipt_text)

    def _send_manual_position_error_alert(
        self,
        *,
        db_session: Any,
        request: ManualExecutionRequest,
        reason: str,
    ) -> None:
        event = build_manual_execution_error_event(
            manual_position_id=request.manual_position_id or "",
            symbol=request.symbol.strip().upper(),
            side=request.side.strip(),
            reason=reason,
            trace_id=request.trace_id,
        )
        self._send_alert_and_commit(db_session=db_session, event=event, receipt_text="")

    def _send_alert_and_commit(self, *, db_session: Any, event: Any, receipt_text: str) -> tuple[str, str, bool]:
        settings = self._settings or get_settings()
        try:
            send_result = self._alert_sender(
                event,
                settings=settings,
                repository=self._alert_repository,
                db_session=db_session,
                send_real_alert=settings.manual_execution_receipt_send_enabled,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - alert failure must not roll back already committed execution rows.
            _rollback_if_possible(db_session)
            self._logger.error("manual execution Hermes receipt failed after database write: %s", exc)
            return receipt_text, "alert_record_failed", True
        failed = send_result.status in {AlertSendStatus.SUBMIT_FAILED, AlertSendStatus.GATEWAY_REJECTED}
        if failed:
            self._logger.warning(
                "manual execution Hermes receipt failed after database write: trace_id=%s status=%s",
                event.trace_id,
                send_result.status.value,
            )
        return receipt_text, send_result.status.value, failed


def record_manual_execution(
    *,
    db_session: Any,
    request: ManualExecutionRequest,
    service: ManualExecutionService | None = None,
) -> ManualExecutionResult:
    """Convenience app-service function used by CLI and tests."""

    active_service = service or ManualExecutionService()
    return active_service.record_manual_execution(db_session=db_session, request=request)


def list_manual_positions(
    *,
    db_session: Any,
    request: ManualPositionListRequest,
    service: ManualExecutionService | None = None,
) -> ManualPositionListResult:
    """Convenience app-service function for listing manual positions."""

    active_service = service or ManualExecutionService()
    return active_service.list_manual_positions(db_session=db_session, request=request)


def _validate_position_matches_request(row: Any, request: ManualExecutionRequest) -> ManualExecutionResult | None:
    if getattr(row, "status", None) != POSITION_STATUS_OPEN:
        return blocked_result(
            request=request,
            error_code="manual_position_closed",
            error_message="manual_position is already closed",
        )
    if getattr(row, "symbol", "").upper() != request.symbol.strip().upper():
        return blocked_result(
            request=request,
            error_code="manual_position_symbol_mismatch",
            error_message="manual_position symbol does not match request symbol",
        )
    if getattr(row, "side", "") != request.side.strip():
        return blocked_result(
            request=request,
            error_code="manual_position_side_mismatch",
            error_message="manual_position side does not match request side",
        )
    return None


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()
