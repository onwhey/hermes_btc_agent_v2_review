"""Stage-16 strategy signal service orchestration.

This file belongs to `app/strategy`.
It receives strategy signal run requests, resolves a MarketContextSnapshot,
builds StrategyEvaluationInput, runs independent strategies, and optionally
writes strategy signal run/result rows.

Call chain:

User CLI
    -> scripts/run_strategy_signals.py::main
    -> app/strategy/signal_service.py::run_strategy_signals
    -> app/strategy/snapshot_resolver.py::ensure_latest_snapshot
    -> app/strategy/input_builder.py::build_input_from_snapshot
    -> app/strategy/runner.py::run_strategies
    -> app/strategy/result_repository.py::create_strategy_signal_run_with_results

This file does not request Binance, write formal Kline tables, write Redis,
send Hermes, call DeepSeek or any large language model, read account/position
state, generate final advice, connect scheduler jobs, or trade.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.strategy.input_builder import StrategyInputBuilder
from app.strategy.result_repository import create_default_strategy_signal_result_repository
from app.strategy.runner import StrategyRunner
from app.strategy.snapshot_resolver import SnapshotResolver
from app.strategy.types import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    StrategyConfigError,
    StrategyInputBuildError,
    StrategyRunPersistencePayload,
    StrategyRunStatus,
    StrategySignal,
    StrategySignalPersistencePayload,
    StrategySignalRunRequest,
    StrategySignalRunResult,
    StrategySignalStatus,
)

ALLOWED_STRATEGY_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})


class StrategySignalService:
    """Coordinate one strategy signal run without producing final advice.

    Parameters: optional resolver, input builder, runner, and repository for
    tests.
    Return value: service instance.
    Failure scenarios: invalid parameters, snapshot not ready, input restore
    failure, strategy config failure, strategy exceptions, or persistence
    errors are returned as structured run results.
    External service access: none directly. The resolver may call the stage-15
    snapshot service only for non-dry-run confirm-write requests, which reads
    MySQL and may write only MarketContextSnapshot.
    Data impact: dry-run writes no strategy signal rows; confirm-write writes
    only strategy signal tables. Formal Kline tables are never modified.
    """

    def __init__(
        self,
        *,
        snapshot_resolver: SnapshotResolver | None = None,
        input_builder: StrategyInputBuilder | None = None,
        runner: StrategyRunner | None = None,
        result_repository: Any | None = None,
    ) -> None:
        self._snapshot_resolver = snapshot_resolver or SnapshotResolver()
        self._input_builder = input_builder or StrategyInputBuilder()
        self._runner = runner or StrategyRunner()
        self._result_repository = result_repository or create_default_strategy_signal_result_repository()

    def run_strategy_signals(
        self,
        db_session: Any,
        *,
        request: StrategySignalRunRequest,
    ) -> StrategySignalRunResult:
        """Run independent strategies for one snapshot request.

        Parameters: caller-owned MySQL session and structured request.
        Return value: `StrategySignalRunResult` with exit code and compact
        counts.
        Failure scenarios: this method catches business failures and database
        persistence failures, rolls back when possible, and returns structured
        failed/blocked results.
        External service access: no direct external calls.
        Data impact: see class docstring. This method never writes formal
        Kline tables or sends Hermes.
        """

        started_at_utc = now_utc()
        trace_id = request.trace_id or uuid.uuid4().hex
        run_id = _build_strategy_signal_run_id(
            request=request,
            started_at_utc=started_at_utc,
            trace_id=trace_id,
        )

        invalid_result = _validate_strategy_signal_run_request(
            request=request,
            run_id=run_id,
            trace_id=trace_id,
            started_at_utc=started_at_utc,
        )
        if invalid_result is not None:
            return invalid_result

        snapshot_id = request.snapshot_id
        details: dict[str, Any] = {
            "dry_run": request.dry_run,
            "confirm_write": request.confirm_write,
            "ensure_latest_snapshot": request.ensure_latest_snapshot,
        }

        if request.ensure_latest_snapshot:
            try:
                resolved_snapshot = self._snapshot_resolver.ensure_latest_snapshot(
                    db_session,
                    symbol=request.symbol,
                    base_interval_value=request.base_interval_value,
                    higher_interval_value=request.higher_interval_value,
                    lookback_base_count=request.lookback_base_count,
                    lookback_higher_count=request.lookback_higher_count,
                    dry_run=request.dry_run,
                    confirm_write=request.confirm_write,
                    current_time_ms=request.current_time_ms,
                    trace_id=trace_id,
                )
            except Exception as exc:  # noqa: BLE001 - resolver/database failures are service failures.
                _rollback_if_possible(db_session)
                result = StrategySignalRunResult(
                    status=StrategyRunStatus.FAILED,
                    exit_code=EXIT_FAILED,
                    run_id=run_id,
                    trace_id=trace_id,
                    snapshot_id=None,
                    message="Snapshot resolution failed before strategy signals could run.",
                    error_message=str(exc),
                    details=details,
                )
                return self._persist_strategy_run_result_if_requested(
                    db_session,
                    request=request,
                    result=result,
                    started_at_utc=started_at_utc,
                )
            details.update(
                {
                    "reused_existing_snapshot": resolved_snapshot.reused_existing_snapshot,
                    "created_new_snapshot": resolved_snapshot.created_new_snapshot,
                }
            )
            if resolved_snapshot.status != StrategyRunStatus.SUCCESS:
                result = StrategySignalRunResult(
                    status=StrategyRunStatus.BLOCKED,
                    exit_code=EXIT_BLOCKED,
                    run_id=run_id,
                    trace_id=trace_id,
                    snapshot_id=resolved_snapshot.snapshot_id,
                    message=resolved_snapshot.message,
                    blocked_reason=resolved_snapshot.blocked_reason or "snapshot_not_ready",
                    error_message=resolved_snapshot.error_message,
                    details=details,
                )
                return self._persist_strategy_run_result_if_requested(
                    db_session,
                    request=request,
                    result=result,
                    started_at_utc=started_at_utc,
                )
            snapshot_id = resolved_snapshot.snapshot_id

        try:
            input_data = self._input_builder.build_input_from_snapshot(
                db_session,
                snapshot_id=str(snapshot_id),
                symbol=request.symbol,
                base_interval_value=request.base_interval_value,
                higher_interval_value=request.higher_interval_value,
                trace_id=trace_id,
            )
        except StrategyInputBuildError as exc:
            result = StrategySignalRunResult(
                status=StrategyRunStatus.BLOCKED,
                exit_code=EXIT_BLOCKED,
                run_id=run_id,
                trace_id=trace_id,
                snapshot_id=snapshot_id,
                message="StrategyEvaluationInput could not be built, strategy signal run is blocked.",
                blocked_reason=_blocked_reason_from_input_error(str(exc)),
                error_message=str(exc),
                details=details,
            )
            return self._persist_strategy_run_result_if_requested(
                db_session,
                request=request,
                result=result,
                started_at_utc=started_at_utc,
            )

        try:
            runner_result = self._runner.run_strategies(input_data)
        except StrategyConfigError as exc:
            result = StrategySignalRunResult(
                status=StrategyRunStatus.BLOCKED,
                exit_code=EXIT_BLOCKED,
                run_id=run_id,
                trace_id=trace_id,
                snapshot_id=snapshot_id,
                message="Strategy configuration is invalid, strategy signal run is blocked.",
                blocked_reason="strategy_config_invalid",
                error_message=str(exc),
                details=details,
            )
            return self._persist_strategy_run_result_if_requested(
                db_session,
                request=request,
                result=result,
                started_at_utc=started_at_utc,
            )
        except Exception as exc:  # noqa: BLE001 - service boundary returns structured failure.
            result = StrategySignalRunResult(
                status=StrategyRunStatus.FAILED,
                exit_code=EXIT_FAILED,
                run_id=run_id,
                trace_id=trace_id,
                snapshot_id=snapshot_id,
                message="Strategy signal runner failed before producing a complete batch.",
                error_message=str(exc),
                details=details,
            )
            return self._persist_strategy_run_result_if_requested(
                db_session,
                request=request,
                result=result,
                started_at_utc=started_at_utc,
            )

        counts = _count_strategy_signal_statuses(runner_result.signals)
        status = runner_result.status
        exit_code = _exit_code_for_run_status(status)
        result = StrategySignalRunResult(
            status=status,
            exit_code=exit_code,
            run_id=run_id,
            trace_id=trace_id,
            snapshot_id=snapshot_id,
            message=runner_result.message,
            blocked_reason=runner_result.blocked_reason,
            error_message=runner_result.error_message,
            strategy_count=counts["strategy_count"],
            success_count=counts["success_count"],
            failed_count=counts["failed_count"],
            invalid_count=counts["invalid_count"],
            not_implemented_count=counts["not_implemented_count"],
            signals=runner_result.signals,
            details=details,
        )
        return self._persist_strategy_run_result_if_requested(
            db_session,
            request=request,
            result=result,
            started_at_utc=started_at_utc,
        )

    def _persist_strategy_run_result_if_requested(
        self,
        db_session: Any,
        *,
        request: StrategySignalRunRequest,
        result: StrategySignalRunResult,
        started_at_utc: datetime,
    ) -> StrategySignalRunResult:
        """Persist run/result rows only for confirm-write non-dry-run requests."""

        if request.dry_run or not request.confirm_write:
            return result

        finished_at_utc = now_utc()
        run_payload = StrategyRunPersistencePayload(
            run_id=result.run_id,
            snapshot_id=result.snapshot_id,
            symbol=request.symbol,
            base_interval_value=request.base_interval_value,
            higher_interval_value=request.higher_interval_value,
            status=result.status,
            trigger_source=request.trigger_source,
            strategy_count=result.strategy_count,
            success_count=result.success_count,
            failed_count=result.failed_count,
            invalid_count=result.invalid_count,
            not_implemented_count=result.not_implemented_count,
            blocked_reason=result.blocked_reason,
            error_message=result.error_message,
            trace_id=result.trace_id,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
        )
        signal_payloads = tuple(
            StrategySignalPersistencePayload(
                run_id=result.run_id,
                snapshot_id=str(result.snapshot_id),
                symbol=request.symbol,
                base_interval_value=request.base_interval_value,
                higher_interval_value=request.higher_interval_value,
                signal=signal,
                trace_id=result.trace_id,
            )
            for signal in result.signals
            if result.snapshot_id
        )

        try:
            run_row, _result_rows = self._result_repository.create_strategy_signal_run_with_results(
                db_session,
                run_payload=run_payload,
                signal_payloads=signal_payloads,
            )
            _commit_if_possible(db_session)
            return replace(
                result,
                run_row_id=getattr(run_row, "id", None),
                message=f"{result.message} {_strategy_persistence_success_message(result)}",
            )
        except Exception as exc:  # noqa: BLE001 - persistence errors become structured failures.
            _rollback_if_possible(db_session)
            return StrategySignalRunResult(
                status=StrategyRunStatus.FAILED,
                exit_code=EXIT_FAILED,
                run_id=result.run_id,
                trace_id=result.trace_id,
                snapshot_id=result.snapshot_id,
                message="Strategy signal persistence failed.",
                error_message=str(exc),
                details=result.details,
            )


def run_strategy_signals(
    *,
    db_session: Any,
    request: StrategySignalRunRequest,
    service: StrategySignalService | None = None,
) -> StrategySignalRunResult:
    """Convenience app-service function used by the CLI and tests."""

    active_service = service or create_default_strategy_signal_service()
    return active_service.run_strategy_signals(db_session, request=request)


def create_default_strategy_signal_service() -> StrategySignalService:
    """Create the default stage-16 strategy signal service."""

    return StrategySignalService()


def _validate_strategy_signal_run_request(
    *,
    request: StrategySignalRunRequest,
    run_id: str,
    trace_id: str,
    started_at_utc: datetime,
) -> StrategySignalRunResult | None:
    problems: list[str] = []
    if bool(request.snapshot_id) == bool(request.ensure_latest_snapshot):
        problems.append("Exactly one of snapshot_id or ensure_latest_snapshot is required")
    if request.trigger_source not in ALLOWED_STRATEGY_TRIGGER_SOURCES:
        problems.append("trigger_source supports only cli in stage 16")
    if request.lookback_base_count <= 0:
        problems.append("lookback_base_count must be greater than 0")
    if request.lookback_higher_count <= 0:
        problems.append("lookback_higher_count must be greater than 0")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run strategy signal persistence requires confirm_write")
    if not problems:
        return None
    return StrategySignalRunResult(
        status=StrategyRunStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        run_id=run_id,
        trace_id=trace_id,
        snapshot_id=request.snapshot_id,
        message="Strategy signal request parameters are invalid.",
        error_message="; ".join(problems),
        details={
            "started_at_utc": started_at_utc.isoformat(),
            "dry_run": request.dry_run,
            "confirm_write": request.confirm_write,
        },
    )


def _build_strategy_signal_run_id(
    *,
    request: StrategySignalRunRequest,
    started_at_utc: datetime,
    trace_id: str,
) -> str:
    timestamp = started_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"SSR-{request.symbol}-{request.base_interval_value.upper()}-"
        f"{request.higher_interval_value.upper()}-{timestamp}-{trace_id[:8]}"
    )


def _count_strategy_signal_statuses(signals: tuple[StrategySignal, ...]) -> dict[str, int]:
    return {
        "strategy_count": len(signals),
        "success_count": sum(
            1
            for signal in signals
            if signal.strategy_status in (StrategySignalStatus.SUCCESS, StrategySignalStatus.NO_SIGNAL)
        ),
        "failed_count": sum(1 for signal in signals if signal.strategy_status == StrategySignalStatus.FAILED),
        "invalid_count": sum(1 for signal in signals if signal.strategy_status == StrategySignalStatus.INVALID),
        "not_implemented_count": sum(
            1 for signal in signals if signal.strategy_status == StrategySignalStatus.NOT_IMPLEMENTED
        ),
    }


def _blocked_reason_from_input_error(error_text: str) -> str:
    if "snapshot_not_found" in error_text:
        return "snapshot_not_found"
    if "snapshot_not_created" in error_text:
        return "snapshot_not_created"
    if "snapshot_restore_failed" in error_text:
        return "snapshot_restore_failed"
    if "mismatch" in error_text:
        return "snapshot_mismatch"
    return "snapshot_input_invalid"


def _exit_code_for_run_status(status: StrategyRunStatus) -> int:
    if status in (StrategyRunStatus.SUCCESS, StrategyRunStatus.PARTIAL_SUCCESS):
        return EXIT_SUCCESS
    if status == StrategyRunStatus.BLOCKED:
        return EXIT_BLOCKED
    return EXIT_FAILED


def _strategy_persistence_success_message(result: StrategySignalRunResult) -> str:
    """Describe exactly which strategy persistence rows were written."""

    if result.signals:
        return "策略信号运行记录和结果记录已写入。"
    return "策略信号运行审计记录已写入，未写入策略结果记录。"


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_STRATEGY_TRIGGER_SOURCES",
    "StrategySignalService",
    "create_default_strategy_signal_service",
    "run_strategy_signals",
]
