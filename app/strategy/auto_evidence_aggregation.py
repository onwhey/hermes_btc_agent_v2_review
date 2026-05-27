"""Stage-24A automatic strategy evidence aggregation hook.

This file belongs to `app/strategy`. It runs after stage-16 strategy signal
rows have already been committed and, when configured, calls the stage-23F
evidence aggregation service for the same `strategy_signal_run_id`.

This file does not run strategies, register strategies, request Binance, read
account/private state, generate final advice, create trade setup, call large
language models, or perform trading. It may write only the 23F aggregation row
through the 23F service and may create a fixed-template `alert_message` if the
post-write aggregation step fails.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from app.alerting.service import send_alert
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger
from app.storage.mysql.repositories.alert_message_repository import (
    create_default_alert_message_repository,
)
from app.strategy.aggregation.evidence_service import StrategyEvidenceAggregationService
from app.strategy.aggregation.evidence_types import (
    EvidenceAggregationRequest,
    EvidenceAggregationRunResult,
    EvidenceAggregationStatus,
)
from app.strategy.types import (
    StrategyRunStatus,
    StrategySignalRunRequest,
    StrategySignalRunResult,
)

AUTO_AGGREGATION_DETAIL_KEY = "strategy_evidence_aggregation"
AUTO_AGGREGATION_SOURCE = "app.strategy.auto_evidence_aggregation"


class StrategyEvidenceAggregationAutoHook:
    """Run the 23F post-step after stage-16 strategy result persistence.

    Parameters: settings, 23F service, alert sender, and alert repository are
    injectable for tests.
    Return value: `StrategySignalRunResult` with extra details describing the
    automatic aggregation outcome.
    Failure scenarios: 23F failures and alert failures are captured in details;
    already committed strategy signal rows are not rolled back.
    External services: only the injected/default alert sender may submit Hermes
    via the fixed alerting service when automatic 23F fails.
    Data impact: may write `strategy_evidence_aggregation_result` and
    `alert_message`; never writes formal Kline tables or trading data.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        evidence_service: StrategyEvidenceAggregationService | None = None,
        alert_sender: Any | None = None,
        alert_message_repository: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self._settings = settings
        self._evidence_service = evidence_service
        self._alert_sender = alert_sender or send_alert
        self._alert_message_repository = alert_message_repository or create_default_alert_message_repository()
        self._logger = logger or get_logger("strategy.auto_evidence_aggregation")

    def maybe_run_after_strategy_signal_persistence(
        self,
        db_session: Any,
        *,
        request: StrategySignalRunRequest,
        result: StrategySignalRunResult,
    ) -> StrategySignalRunResult:
        """Optionally run stage-23F after stage-16 rows are committed.

        Parameters: caller-owned MySQL session, original stage-16 request, and
        persisted stage-16 result.
        Return value: result with `details["strategy_evidence_aggregation"]`.
        Failure scenarios: automatic 23F failures become fixed-template alerts
        and details; this method does not convert the stage-16 run into a failed
        strategy run.
        External services: Hermes only through `app.alerting.service.send_alert`
        on automatic 23F failure.
        Data impact: dry-run writes nothing; confirm-write may write 23F and
        alert rows after stage-16 has already committed.
        """

        settings = self._active_settings()
        if not settings.strategy_evidence_aggregation_enabled:
            return _with_auto_aggregation_details(
                result,
                {
                    "enabled": False,
                    "status": "disabled",
                    "database_written": False,
                    "message": "STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false; 23F auto aggregation not triggered.",
                },
            )
        if request.dry_run or not request.confirm_write:
            return _with_auto_aggregation_details(
                result,
                {
                    "enabled": True,
                    "status": "skipped",
                    "reason": "dry_run_or_not_confirm_write",
                    "database_written": False,
                    "message": "dry-run or non-confirm-write mode; 23F auto aggregation not written.",
                },
            )
        if result.status not in {StrategyRunStatus.SUCCESS, StrategyRunStatus.PARTIAL_SUCCESS}:
            return _with_auto_aggregation_details(
                result,
                {
                    "enabled": True,
                    "status": "skipped",
                    "reason": "strategy_signal_run_not_success",
                    "database_written": False,
                    "message": "strategy signal run was not successful; 23F auto aggregation skipped.",
                },
            )

        try:
            aggregation_result = self._run_23f_service(
                db_session,
                request=EvidenceAggregationRequest(
                    strategy_signal_run_id=result.run_id,
                    trigger_source=request.trigger_source,
                    dry_run=False,
                    confirm_write=True,
                    created_by=request.created_by or "strategy_signal_service",
                    trace_id=result.trace_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - post-step failure must not roll back stage-16 rows.
            _rollback_if_possible(db_session)
            details = self._build_failure_details(
                request=request,
                result=result,
                error_code="strategy_evidence_aggregation_exception",
                error_message=str(exc),
            )
            self._log_auto_failure(details)
            return _with_auto_aggregation_details(
                result,
                self._send_failure_alert_and_commit(
                    db_session,
                    request=request,
                    result=result,
                    details=details,
                ),
            )

        details = _details_from_aggregation_result(aggregation_result)
        if _is_failed_auto_aggregation(aggregation_result):
            failure_details = self._build_failure_details(
                request=request,
                result=result,
                error_code=aggregation_result.error_code or "strategy_evidence_aggregation_failed",
                error_message=aggregation_result.error_message or aggregation_result.message,
                aggregation_result=aggregation_result,
            )
            self._log_auto_failure(failure_details)
            details.update(
                self._send_failure_alert_and_commit(
                    db_session,
                    request=request,
                    result=result,
                    details=failure_details,
                )
            )
        return _with_auto_aggregation_details(result, details)

    def _run_23f_service(
        self,
        db_session: Any,
        *,
        request: EvidenceAggregationRequest,
    ) -> EvidenceAggregationRunResult:
        active_service = self._evidence_service or StrategyEvidenceAggregationService()
        return active_service.run_strategy_evidence_aggregation(db_session, request=request)

    def _active_settings(self) -> AppSettings:
        return self._settings or get_settings()

    def _build_failure_details(
        self,
        *,
        request: StrategySignalRunRequest,
        result: StrategySignalRunResult,
        error_code: str,
        error_message: str,
        aggregation_result: EvidenceAggregationRunResult | None = None,
    ) -> dict[str, Any]:
        rerun_command = _manual_rerun_command(result.run_id)
        return {
            "enabled": True,
            "status": "failed",
            "database_written": bool(aggregation_result.database_written) if aggregation_result else False,
            "database_action": aggregation_result.database_action if aggregation_result else "none",
            "aggregation_id": aggregation_result.aggregation_id if aggregation_result else "",
            "strategy_signal_run_id": result.run_id,
            "symbol": request.symbol,
            "base_interval": request.base_interval_value,
            "higher_interval": request.higher_interval_value,
            "trigger_source": request.trigger_source,
            "error_code": error_code,
            "error_message": error_message,
            "trace_id": result.trace_id,
            "manual_rerun_available": True,
            "manual_rerun_command": rerun_command,
            "not_trading_advice": True,
            "message": "strategy signal rows were written, but automatic 23F evidence aggregation failed.",
        }

    def _send_failure_alert_and_commit(
        self,
        db_session: Any,
        *,
        request: StrategySignalRunRequest,
        result: StrategySignalRunResult,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        alert_event = AlertEvent(
            alert_type=AlertType.STRATEGY_EVIDENCE_AGGREGATION_FAILED,
            severity=AlertSeverity.ERROR,
            title="BTC 策略证据聚合失败",
            summary="策略信号已写入，但 23F 自动证据聚合失败，可手动补跑。",
            details=details,
            source=AUTO_AGGREGATION_SOURCE,
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(
                alert_event,
                settings=self._active_settings(),
                repository=self._alert_message_repository,
                db_session=db_session,
                send_real_alert=True,
            )
            _commit_if_possible(db_session)
            return {
                **dict(details),
                "alert_type": AlertType.STRATEGY_EVIDENCE_AGGREGATION_FAILED.value,
                "alert_status": getattr(getattr(send_result, "status", None), "value", str(getattr(send_result, "status", ""))),
                "alert_message": getattr(send_result, "message", ""),
                "alert_error_message": getattr(send_result, "error_message", ""),
            }
        except Exception as exc:  # noqa: BLE001 - alert failure must not rewrite stage-16 persistence.
            _rollback_if_possible(db_session)
            self._logger.error(
                "strategy evidence aggregation failure alert failed: run_id=%s error=%s",
                result.run_id,
                exc,
            )
            return {
                **dict(details),
                "alert_type": AlertType.STRATEGY_EVIDENCE_AGGREGATION_FAILED.value,
                "alert_status": "failed",
                "alert_error_message": str(exc),
            }

    def _log_auto_failure(self, details: Mapping[str, Any]) -> None:
        self._logger.error(
            "strategy evidence aggregation auto hook failed: run_id=%s error_code=%s error=%s trace_id=%s",
            details.get("strategy_signal_run_id", ""),
            details.get("error_code", ""),
            details.get("error_message", ""),
            details.get("trace_id", ""),
        )


def create_default_strategy_evidence_aggregation_auto_hook(
    *,
    settings: AppSettings | None = None,
) -> StrategyEvidenceAggregationAutoHook:
    """Create the default 24A auto hook used by stage-16 service."""

    return StrategyEvidenceAggregationAutoHook(settings=settings)


def _details_from_aggregation_result(result: EvidenceAggregationRunResult) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": result.status.value,
        "database_written": result.database_written,
        "database_action": result.database_action,
        "aggregation_id": result.aggregation_id,
        "strategy_signal_run_id": result.strategy_signal_run_id,
        "candidate_bias": result.candidate_bias.value if result.candidate_bias else "",
        "decision_readiness": result.decision_readiness.value if result.decision_readiness else "",
        "trace_id": result.trace_id,
        "message": result.message,
        "error_code": result.error_code or "",
        "error_message": result.error_message or "",
        "not_trading_advice": True,
    }


def _is_failed_auto_aggregation(result: EvidenceAggregationRunResult) -> bool:
    if result.status in {EvidenceAggregationStatus.FAILED, EvidenceAggregationStatus.BLOCKED}:
        return True
    return not result.database_written and result.status != EvidenceAggregationStatus.INSUFFICIENT_EVIDENCE


def _with_auto_aggregation_details(
    result: StrategySignalRunResult,
    details: Mapping[str, Any],
) -> StrategySignalRunResult:
    merged_details = dict(result.details or {})
    merged_details[AUTO_AGGREGATION_DETAIL_KEY] = dict(details)
    return replace(result, details=merged_details)


def _manual_rerun_command(strategy_signal_run_id: str) -> str:
    return (
        "python -m scripts.run_strategy_evidence_aggregation "
        f"--strategy-signal-run-id {strategy_signal_run_id} "
        "--trigger-source cli --confirm-write"
    )


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "AUTO_AGGREGATION_DETAIL_KEY",
    "StrategyEvidenceAggregationAutoHook",
    "create_default_strategy_evidence_aggregation_auto_hook",
]
