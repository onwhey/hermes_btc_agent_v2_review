"""Stage-22B Hermes/WeChat manual execution intent service.

调用链：
Hermes/WeChat inbound text or CLI simulation
    ↓
app.manual_execution.hermes_entry.intent_service.py::handle_inbound_manual_execution_message
    ↓
app.manual_execution.hermes_entry.parser.py::parse_manual_execution_intent_text
    ↓
app.manual_execution.hermes_entry.intent_repository.py::ManualExecutionIntentRepository
    ↓
app.manual_execution.service.py::ManualExecutionService.record_manual_execution

本文件属于 `app/manual_execution/hermes_entry`，负责把自然语言入口限定为
待确认 intent，并在用户确认 MEI-xxx 后调用 22A service 写库。
本文件不重写 22A 盈亏、手续费、均价、保证金算法；不请求 Binance，不读取
交易所账户，不同步真实仓位，不修改 K 线表，不修改 strategy_advice 生命周期，
不读写 Redis，不调用大模型，也不执行自动交易。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Mapping

from app.alerting.service import send_alert
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings, get_settings
from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from app.core.time_utils import ensure_utc_aware, now_utc
from app.manual_execution.hermes_entry.constants import (
    ALLOWED_SOURCE_CHANNELS,
    INBOUND_COMMAND_CANCEL,
    INBOUND_COMMAND_CONFIRM,
    INBOUND_COMMAND_CREATE,
    INTENT_STATUS_CANCELLED,
    INTENT_STATUS_CONFIRMED,
    INTENT_STATUS_EXECUTED,
    INTENT_STATUS_EXECUTION_FAILED,
    INTENT_STATUS_EXPIRED,
    INTENT_STATUS_PARSE_FAILED,
    INTENT_STATUS_PENDING_CONFIRMATION,
    INTENT_STATUS_VALIDATION_FAILED,
    SOURCE_CHANNEL_CLI,
    SOURCE_CHANNEL_HERMES,
    SOURCE_CHANNEL_WECHAT,
    VALIDATION_STATUS_BLOCKED,
    VALIDATION_STATUS_OK,
)
from app.manual_execution.hermes_entry.id_utils import build_manual_execution_intent_id
from app.manual_execution.hermes_entry.intent_repository import (
    ManualExecutionIntentRepository,
    create_default_manual_execution_intent_repository,
)
from app.manual_execution.hermes_entry.intent_schema import (
    InboundManualExecutionMessage,
    IntentActionRequest,
    ManualExecutionIntentPersistencePayload,
    ManualExecutionIntentResult,
    ManualExecutionIntentServiceStatus,
    ParsedManualExecutionIntent,
    intent_blocked_exit_code,
    intent_failed_exit_code,
    intent_success_exit_code,
)
from app.manual_execution.hermes_entry.parser import (
    normalize_manual_execution_text,
    parse_inbound_manual_execution_command,
    parse_manual_execution_intent_text,
)
from app.manual_execution.hermes_entry.service_helpers import (
    blocked_action_result,
    bounded_optional_text,
    bounded_text,
    commit_if_possible,
    json_dumps_bounded,
    manual_execution_error_message_for_user,
    manual_request_from_parsed,
    none_if_empty,
    parsed_payload,
    rollback_if_possible,
    trigger_source_for_channel,
    with_validation_error,
)
from app.manual_execution.hermes_entry.templates import (
    build_manual_execution_intent_event,
    render_already_executed_text,
    render_blocked_text,
    render_cancelled_text,
    render_executed_text,
    render_expired_text,
    render_parse_failed_text,
    render_pending_confirmation_text,
    render_validation_failed_text,
)
from app.manual_execution.schema import ManualExecutionRequest, ManualExecutionServiceStatus
from app.manual_execution.service import ManualExecutionService
from app.storage.mysql.repositories.alert_message_repository import (
    AlertMessageRepository,
    create_default_alert_message_repository,
)

AlertSender = Callable[..., AlertSendResult]


class ManualExecutionIntentService:
    """Business service for stage-22B manual execution confirmation intents.

    Parameters: repositories, 22A manual service, and alert sender are
    injectable for tests.
    Return value: service instance.
    Failure scenarios: parse/validation failures create blocked intent rows
    when writes are confirmed; confirm failures never write 22A rows unless the
    22A service reports success.
    External services: only unified alerting may call Hermes when enabled by
    config.
    Data impact: writes the 22B intent table and, after confirmation only,
    delegates 22A table writes to `ManualExecutionService`.
    """

    def __init__(
        self,
        *,
        intent_repository: ManualExecutionIntentRepository | None = None,
        manual_execution_service: ManualExecutionService | None = None,
        alert_repository: AlertMessageRepository | None = None,
        settings: AppSettings | None = None,
        alert_sender: AlertSender = send_alert,
    ) -> None:
        self._intent_repository = intent_repository or create_default_manual_execution_intent_repository()
        self._manual_execution_service = manual_execution_service or ManualExecutionService(settings=settings)
        self._alert_repository = alert_repository or create_default_alert_message_repository()
        self._settings = settings
        self._alert_sender = alert_sender
        self._logger = get_logger("manual_execution.hermes_entry.intent_service")

    def handle_inbound_manual_execution_message(
        self,
        *,
        db_session: Any,
        message: InboundManualExecutionMessage,
    ) -> ManualExecutionIntentResult:
        """Route one inbound text to create, confirm, or cancel intent flow."""

        command, parsed_intent_id = parse_inbound_manual_execution_command(message.text)
        if command == INBOUND_COMMAND_CONFIRM and parsed_intent_id:
            return self.confirm_manual_execution_intent(
                db_session=db_session,
                request=IntentActionRequest(
                    intent_id=parsed_intent_id,
                    source_channel=message.source_channel,
                    source_message_id=message.source_message_id,
                    source_user_id=message.source_user_id,
                    dry_run=message.dry_run,
                    confirm_write=message.confirm_write,
                    trace_id=message.trace_id,
                ),
            )
        if command == INBOUND_COMMAND_CANCEL and parsed_intent_id:
            return self.cancel_manual_execution_intent(
                db_session=db_session,
                request=IntentActionRequest(
                    intent_id=parsed_intent_id,
                    source_channel=message.source_channel,
                    source_message_id=message.source_message_id,
                    source_user_id=message.source_user_id,
                    dry_run=message.dry_run,
                    confirm_write=message.confirm_write,
                    trace_id=message.trace_id,
                ),
            )
        return self.create_manual_execution_intent(db_session=db_session, message=message)

    def create_manual_execution_intent(
        self,
        *,
        db_session: Any,
        message: InboundManualExecutionMessage,
    ) -> ManualExecutionIntentResult:
        """Parse one user text and create only a pending or blocked 22B intent row."""

        try:
            self._validate_inbound_message(message)
            settings = self._settings or get_settings()
            parsed = parse_manual_execution_intent_text(message.text)
            now_value = now_utc()
            expires_at = now_value + timedelta(minutes=max(1, settings.manual_execution_intent_expire_minutes))
            if parsed.error_code:
                return self._persist_or_preview_blocked_intent(
                    db_session=db_session,
                    message=message,
                    parsed=parsed,
                    now_value=now_value,
                    expires_at=expires_at,
                    status=INTENT_STATUS_PARSE_FAILED
                    if parsed.error_code == "action_not_recognized"
                    else INTENT_STATUS_VALIDATION_FAILED,
                    reply_text=render_parse_failed_text(error_message=parsed.error_message or "")
                    if parsed.error_code == "action_not_recognized"
                    else render_validation_failed_text(error_message=parsed.error_message or ""),
                )

            dry_run_result = self._preview_with_22a(db_session=db_session, message=message, parsed=parsed)
            if dry_run_result.status not in {ManualExecutionServiceStatus.DRY_RUN, ManualExecutionServiceStatus.SUCCESS}:
                parsed = with_validation_error(
                    parsed,
                    error_code=dry_run_result.error_code or "manual_execution_validation_failed",
                    error_message=manual_execution_error_message_for_user(
                        error_code=dry_run_result.error_code,
                        error_message=dry_run_result.error_message,
                    ),
                )
                return self._persist_or_preview_blocked_intent(
                    db_session=db_session,
                    message=message,
                    parsed=parsed,
                    now_value=now_value,
                    expires_at=expires_at,
                    status=INTENT_STATUS_VALIDATION_FAILED,
                    reply_text=render_validation_failed_text(error_message=parsed.error_message or ""),
                    dry_run_snapshot=dry_run_result.execution_snapshot,
                )

            intent_id = build_manual_execution_intent_id()
            dry_run_snapshot = dict(dry_run_result.execution_snapshot)
            reply_text = render_pending_confirmation_text(
                intent_id=intent_id,
                parsed=parsed,
                expires_at_utc=expires_at,
                dry_run_snapshot=dry_run_snapshot,
            )
            if message.dry_run and not message.confirm_write:
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.DRY_RUN,
                    exit_code=intent_success_exit_code(),
                    trace_id=message.trace_id,
                    intent_id=intent_id,
                    reply_text=reply_text,
                    parsed_payload=parsed_payload(parsed),
                    dry_run_snapshot=dry_run_snapshot,
                    expires_at_utc=expires_at,
                )
            payload = self._build_persistence_payload(
                intent_id=intent_id,
                status=INTENT_STATUS_PENDING_CONFIRMATION,
                message=message,
                parsed=parsed,
                now_value=now_value,
                expires_at=expires_at,
                validation_status=VALIDATION_STATUS_OK,
                dry_run_snapshot=dry_run_snapshot,
            )
            self._intent_repository.create_intent(db_session, payload=payload)
            commit_if_possible(db_session)
            alert_status = self._send_reply_after_committed_intent(
                db_session=db_session,
                reply_text=reply_text,
                trace_id=message.trace_id,
                summary="人工执行待确认草稿已生成。",
            )
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.PENDING_CONFIRMATION,
                exit_code=intent_success_exit_code(),
                trace_id=message.trace_id,
                intent_id=intent_id,
                intent_database_written=True,
                expires_at_utc=expires_at,
                reply_text=reply_text,
                alert_status=alert_status,
                parsed_payload=parsed_payload(parsed),
                dry_run_snapshot=dry_run_snapshot,
            )
        except ValidationError as exc:
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.BLOCKED,
                exit_code=intent_blocked_exit_code(),
                trace_id=message.trace_id,
                reply_text=render_blocked_text(intent_id=None, error_message=str(exc)),
                error_code="invalid_request",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - service boundary returns explicit failure.
            rollback_if_possible(db_session)
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.FAILED,
                exit_code=intent_failed_exit_code(),
                trace_id=message.trace_id,
                reply_text=render_blocked_text(intent_id=None, error_message=str(exc)),
                error_code="manual_execution_intent_failed",
                error_message=str(exc),
            )

    def confirm_manual_execution_intent(
        self,
        *,
        db_session: Any,
        request: IntentActionRequest,
    ) -> ManualExecutionIntentResult:
        """Confirm one pending MEI intent and delegate the real write to 22A."""

        try:
            row = self._intent_repository.get_intent_by_id(db_session, intent_id=request.intent_id.upper())
            if row is None:
                return blocked_action_result(
                    request=request,
                    error_code="intent_not_found",
                    error_message=f"没有找到确认码 {request.intent_id}，请重新输入正确的 MEI 编号。",
                )
            now_value = now_utc()
            if row.status == INTENT_STATUS_EXECUTED:
                reply_text = render_already_executed_text(
                    intent_id=row.intent_id,
                    manual_position_id=row.executed_manual_position_id,
                    execution_id=row.executed_execution_id,
                )
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.EXECUTED,
                    exit_code=intent_success_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    manual_execution_database_written=True,
                    manual_position_id=row.executed_manual_position_id,
                    execution_id=row.executed_execution_id,
                    reply_text=reply_text,
                    idempotent=True,
                )
            if row.status == INTENT_STATUS_CANCELLED:
                return blocked_action_result(
                    request=request,
                    intent_id=row.intent_id,
                    error_code="intent_cancelled",
                    error_message="该确认码已取消，不能再确认执行。",
                )
            if self._is_expired(row=row, now_value=now_value):
                if row.status == INTENT_STATUS_PENDING_CONFIRMATION:
                    self._intent_repository.mark_status(
                        db_session,
                        row,
                        status=INTENT_STATUS_EXPIRED,
                        now_utc_value=now_value,
                    )
                    commit_if_possible(db_session)
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.EXPIRED,
                    exit_code=intent_blocked_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    reply_text=render_expired_text(intent_id=row.intent_id),
                    error_code="intent_expired",
                    error_message="确认码已过期。",
                )
            if row.status != INTENT_STATUS_PENDING_CONFIRMATION:
                return blocked_action_result(
                    request=request,
                    intent_id=row.intent_id,
                    error_code="intent_not_pending",
                    error_message=f"该确认码当前状态为 {row.status}，不能确认执行。",
                )
            if request.dry_run and not request.confirm_write:
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.DRY_RUN,
                    exit_code=intent_success_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    reply_text=f"{row.intent_id} 可以确认；当前为 dry-run，未调用 22A 写库。",
                )

            self._intent_repository.mark_status(
                db_session,
                row,
                status=INTENT_STATUS_CONFIRMED,
                now_utc_value=now_value,
            )
            manual_request = self._manual_request_from_intent_row(row, request=request, confirm_write=True)
            manual_result = self._manual_execution_service.record_manual_execution(
                db_session=db_session,
                request=manual_request,
            )
            if not manual_result.database_written:
                self._intent_repository.mark_status(
                    db_session,
                    row,
                    status=INTENT_STATUS_EXECUTION_FAILED,
                    now_utc_value=now_utc(),
                    validation_error_code=manual_result.error_code or "manual_execution_blocked",
                    validation_error_message=manual_result.error_message or "22A 人工执行写入未完成。",
                )
                commit_if_possible(db_session)
                reply_text = render_blocked_text(
                    intent_id=row.intent_id,
                    error_message=manual_execution_error_message_for_user(
                        error_code=manual_result.error_code,
                        error_message=manual_result.error_message,
                    ),
                )
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.BLOCKED,
                    exit_code=intent_blocked_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    manual_execution_database_written=False,
                    reply_text=reply_text,
                    error_code=manual_result.error_code or "manual_execution_blocked",
                    error_message=manual_execution_error_message_for_user(
                        error_code=manual_result.error_code,
                        error_message=manual_result.error_message,
                    ),
                )

            self._intent_repository.mark_status(
                db_session,
                row,
                status=INTENT_STATUS_EXECUTED,
                now_utc_value=now_utc(),
                executed_manual_position_id=manual_result.manual_position_id,
                executed_execution_id=manual_result.execution_id,
            )
            commit_if_possible(db_session)
            reply_text = render_executed_text(
                intent_id=row.intent_id,
                manual_position_id=manual_result.manual_position_id,
                execution_id=manual_result.execution_id,
            )
            alert_status = self._send_reply_after_committed_intent(
                db_session=db_session,
                reply_text=reply_text,
                trace_id=request.trace_id,
                summary="人工执行确认已完成。",
            )
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.EXECUTED,
                exit_code=intent_success_exit_code(),
                trace_id=request.trace_id,
                intent_id=row.intent_id,
                intent_database_written=True,
                manual_execution_database_written=True,
                manual_position_id=manual_result.manual_position_id,
                execution_id=manual_result.execution_id,
                reply_text=reply_text,
                alert_status=alert_status,
            )
        except Exception as exc:  # noqa: BLE001 - confirmation boundary must not hide failures.
            rollback_if_possible(db_session)
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.FAILED,
                exit_code=intent_failed_exit_code(),
                trace_id=request.trace_id,
                intent_id=request.intent_id,
                reply_text=render_blocked_text(intent_id=request.intent_id, error_message=str(exc)),
                error_code="manual_execution_intent_confirm_failed",
                error_message=str(exc),
            )

    def cancel_manual_execution_intent(
        self,
        *,
        db_session: Any,
        request: IntentActionRequest,
    ) -> ManualExecutionIntentResult:
        """Cancel one pending MEI intent without calling 22A."""

        try:
            row = self._intent_repository.get_intent_by_id(db_session, intent_id=request.intent_id.upper())
            if row is None:
                return blocked_action_result(
                    request=request,
                    error_code="intent_not_found",
                    error_message=f"没有找到确认码 {request.intent_id}，请重新输入正确的 MEI 编号。",
                )
            if row.status == INTENT_STATUS_EXECUTED:
                return blocked_action_result(
                    request=request,
                    intent_id=row.intent_id,
                    error_code="intent_already_executed",
                    error_message="该确认码已执行成功，不能取消。",
                )
            if row.status == INTENT_STATUS_CANCELLED:
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.CANCELLED,
                    exit_code=intent_success_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    reply_text=render_cancelled_text(intent_id=row.intent_id),
                    idempotent=True,
                )
            if row.status != INTENT_STATUS_PENDING_CONFIRMATION:
                return blocked_action_result(
                    request=request,
                    intent_id=row.intent_id,
                    error_code="intent_not_pending",
                    error_message=f"该确认码当前状态为 {row.status}，不能取消。",
                )
            if request.dry_run and not request.confirm_write:
                return ManualExecutionIntentResult(
                    status=ManualExecutionIntentServiceStatus.DRY_RUN,
                    exit_code=intent_success_exit_code(),
                    trace_id=request.trace_id,
                    intent_id=row.intent_id,
                    intent_database_written=True,
                    reply_text=f"{row.intent_id} 可以取消；当前为 dry-run，未更新数据库。",
                )
            self._intent_repository.mark_status(
                db_session,
                row,
                status=INTENT_STATUS_CANCELLED,
                now_utc_value=now_utc(),
            )
            commit_if_possible(db_session)
            reply_text = render_cancelled_text(intent_id=row.intent_id)
            alert_status = self._send_reply_after_committed_intent(
                db_session=db_session,
                reply_text=reply_text,
                trace_id=request.trace_id,
                summary="人工执行待确认草稿已取消。",
            )
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.CANCELLED,
                exit_code=intent_success_exit_code(),
                trace_id=request.trace_id,
                intent_id=row.intent_id,
                intent_database_written=True,
                reply_text=reply_text,
                alert_status=alert_status,
            )
        except Exception as exc:  # noqa: BLE001 - service boundary converts unexpected failures to explicit result.
            rollback_if_possible(db_session)
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.FAILED,
                exit_code=intent_failed_exit_code(),
                trace_id=request.trace_id,
                intent_id=request.intent_id,
                reply_text=render_blocked_text(intent_id=request.intent_id, error_message=str(exc)),
                error_code="manual_execution_intent_cancel_failed",
                error_message=str(exc),
            )

    def _validate_inbound_message(self, message: InboundManualExecutionMessage) -> None:
        if message.source_channel not in ALLOWED_SOURCE_CHANNELS:
            raise ValidationError("source_channel must be cli, hermes, or wechat")
        if message.command not in {INBOUND_COMMAND_CREATE, INBOUND_COMMAND_CONFIRM, INBOUND_COMMAND_CANCEL}:
            raise ValidationError("unsupported inbound command")
        if message.source_channel in {SOURCE_CHANNEL_HERMES, SOURCE_CHANNEL_WECHAT}:
            settings = self._settings or get_settings()
            if not settings.manual_execution_hermes_entry_enabled:
                raise ValidationError("Hermes 人工执行入口未启用，未生成草稿。")
        if not normalize_manual_execution_text(message.text):
            raise ValidationError("text is required")
        if message.dry_run and message.confirm_write:
            raise ValidationError("dry_run and confirm_write cannot both be true")
        if not message.dry_run and not message.confirm_write:
            raise ValidationError("non-dry-run intent creation requires confirm_write")

    def _preview_with_22a(
        self,
        *,
        db_session: Any,
        message: InboundManualExecutionMessage,
        parsed: ParsedManualExecutionIntent,
    ) -> Any:
        manual_request = manual_request_from_parsed(
            parsed=parsed,
            trace_id=message.trace_id,
            dry_run=True,
            confirm_write=False,
            trigger_source=trigger_source_for_channel(message.source_channel),
        )
        return self._manual_execution_service.record_manual_execution(
            db_session=db_session,
            request=manual_request,
        )

    def _manual_request_from_intent_row(
        self,
        row: Any,
        *,
        request: IntentActionRequest,
        confirm_write: bool,
    ) -> ManualExecutionRequest:
        parsed = ParsedManualExecutionIntent(
            action=row.parsed_action,
            symbol=row.parsed_symbol,
            side=row.parsed_side,
            price=row.parsed_price,
            notional_usdt=row.parsed_notional_usdt,
            margin_usdt=row.parsed_margin_usdt,
            manual_position_id=row.parsed_manual_position_id,
            advice_id=row.parsed_advice_id,
            reason=row.parsed_reason or "",
            note=row.parsed_note or "",
            normalized_text=row.normalized_text,
        )
        return manual_request_from_parsed(
            parsed=parsed,
            trace_id=request.trace_id,
            dry_run=not confirm_write,
            confirm_write=confirm_write,
            trigger_source=trigger_source_for_channel(row.source_channel),
        )

    def _persist_or_preview_blocked_intent(
        self,
        *,
        db_session: Any,
        message: InboundManualExecutionMessage,
        parsed: ParsedManualExecutionIntent,
        now_value: Any,
        expires_at: Any,
        status: str,
        reply_text: str,
        dry_run_snapshot: Mapping[str, object] | None = None,
    ) -> ManualExecutionIntentResult:
        intent_id = build_manual_execution_intent_id()
        if message.dry_run and not message.confirm_write:
            return ManualExecutionIntentResult(
                status=ManualExecutionIntentServiceStatus.DRY_RUN,
                exit_code=intent_success_exit_code(),
                trace_id=message.trace_id,
                intent_id=intent_id,
                reply_text=reply_text,
                error_code=parsed.error_code,
                error_message=parsed.error_message,
                parsed_payload=parsed_payload(parsed),
                dry_run_snapshot=dry_run_snapshot or {},
            )
        payload = self._build_persistence_payload(
            intent_id=intent_id,
            status=status,
            message=message,
            parsed=parsed,
            now_value=now_value,
            expires_at=expires_at,
            validation_status=VALIDATION_STATUS_BLOCKED,
            dry_run_snapshot=dry_run_snapshot or {},
        )
        self._intent_repository.create_intent(db_session, payload=payload)
        commit_if_possible(db_session)
        alert_status = self._send_reply_after_committed_intent(
            db_session=db_session,
            reply_text=reply_text,
            trace_id=message.trace_id,
            summary="人工执行草稿已阻断。",
        )
        return ManualExecutionIntentResult(
            status=ManualExecutionIntentServiceStatus.PARSE_FAILED
            if status == INTENT_STATUS_PARSE_FAILED
            else ManualExecutionIntentServiceStatus.VALIDATION_FAILED,
            exit_code=intent_blocked_exit_code(),
            trace_id=message.trace_id,
            intent_id=intent_id,
            intent_database_written=True,
            reply_text=reply_text,
            alert_status=alert_status,
            error_code=parsed.error_code,
            error_message=parsed.error_message,
            parsed_payload=parsed_payload(parsed),
            dry_run_snapshot=dry_run_snapshot or {},
        )

    def _build_persistence_payload(
        self,
        *,
        intent_id: str,
        status: str,
        message: InboundManualExecutionMessage,
        parsed: ParsedManualExecutionIntent,
        now_value: Any,
        expires_at: Any,
        validation_status: str,
        dry_run_snapshot: Mapping[str, object],
    ) -> ManualExecutionIntentPersistencePayload:
        return ManualExecutionIntentPersistencePayload(
            intent_id=intent_id,
            status=status,
            source_channel=message.source_channel,
            source_message_id=message.source_message_id,
            source_user_id=message.source_user_id,
            raw_text=bounded_text(message.text, 4000),
            normalized_text=bounded_text(parsed.normalized_text, 4000),
            parsed_action=parsed.action,
            parsed_symbol=parsed.symbol,
            parsed_side=parsed.side,
            parsed_manual_position_id=parsed.manual_position_id,
            parsed_advice_id=parsed.advice_id,
            parsed_price=parsed.price,
            parsed_notional_usdt=parsed.notional_usdt,
            parsed_margin_usdt=parsed.margin_usdt,
            parsed_reason=none_if_empty(parsed.reason),
            parsed_note=none_if_empty(parsed.note),
            parsed_payload_json=json_dumps_bounded(parsed_payload(parsed), 4000),
            validation_status=validation_status,
            validation_error_code=parsed.error_code,
            validation_error_message=bounded_optional_text(parsed.error_message, 1000),
            missing_fields_json=json_dumps_bounded(list(parsed.missing_fields), 1000),
            dry_run_snapshot_json=json_dumps_bounded(dry_run_snapshot, 4000),
            expires_at_utc=expires_at,
            trace_id=message.trace_id,
            created_at_utc=now_value,
            updated_at_utc=now_value,
        )

    def _is_expired(self, *, row: Any, now_value: Any) -> bool:
        expires_at = ensure_utc_aware(getattr(row, "expires_at_utc", None))
        if expires_at is None:
            return False
        return expires_at <= now_value

    def _send_reply_after_committed_intent(
        self,
        *,
        db_session: Any,
        reply_text: str,
        trace_id: str,
        summary: str,
    ) -> str:
        settings = self._settings or get_settings()
        event = build_manual_execution_intent_event(reply_text=reply_text, trace_id=trace_id, summary=summary)
        try:
            send_result = self._alert_sender(
                event,
                settings=settings,
                repository=self._alert_repository,
                db_session=db_session,
                send_real_alert=settings.manual_execution_hermes_reply_send_enabled,
            )
            commit_if_possible(db_session)
            if send_result.status in {AlertSendStatus.SUBMIT_FAILED, AlertSendStatus.GATEWAY_REJECTED}:
                self._logger.warning(
                    "manual execution intent reply failed: trace_id=%s status=%s",
                    trace_id,
                    send_result.status.value,
                )
            return send_result.status.value
        except Exception as exc:  # noqa: BLE001 - reply failure must not roll back committed intent.
            rollback_if_possible(db_session)
            self._logger.error("manual execution intent Hermes reply failed after database write: %s", exc)
            return "alert_record_failed"


def handle_inbound_manual_execution_message(
    *,
    db_session: Any,
    message: InboundManualExecutionMessage,
    service: ManualExecutionIntentService | None = None,
) -> ManualExecutionIntentResult:
    """Convenience app-service function used by Hermes adapters, CLI, and tests."""

    active_service = service or ManualExecutionIntentService()
    return active_service.handle_inbound_manual_execution_message(db_session=db_session, message=message)


def create_manual_execution_intent(
    *,
    db_session: Any,
    message: InboundManualExecutionMessage,
    service: ManualExecutionIntentService | None = None,
) -> ManualExecutionIntentResult:
    """Convenience function for creating one pending 22B intent."""

    active_service = service or ManualExecutionIntentService()
    return active_service.create_manual_execution_intent(db_session=db_session, message=message)


def confirm_manual_execution_intent(
    *,
    db_session: Any,
    request: IntentActionRequest,
    service: ManualExecutionIntentService | None = None,
) -> ManualExecutionIntentResult:
    """Convenience function for confirming one pending 22B intent."""

    active_service = service or ManualExecutionIntentService()
    return active_service.confirm_manual_execution_intent(db_session=db_session, request=request)


def cancel_manual_execution_intent(
    *,
    db_session: Any,
    request: IntentActionRequest,
    service: ManualExecutionIntentService | None = None,
) -> ManualExecutionIntentResult:
    """Convenience function for cancelling one pending 22B intent."""

    active_service = service or ManualExecutionIntentService()
    return active_service.cancel_manual_execution_intent(db_session=db_session, request=request)


__all__ = [
    "ManualExecutionIntentService",
    "cancel_manual_execution_intent",
    "confirm_manual_execution_intent",
    "create_manual_execution_intent",
    "handle_inbound_manual_execution_message",
]
