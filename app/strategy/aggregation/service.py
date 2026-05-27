"""Stage-18 strategy aggregation service.

Call chain:
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::get_strategy_signal_run
    -> app/strategy/aggregation/repository.py::list_strategy_signal_results
    -> app/strategy/aggregation/repository.py::restore_snapshot_kline_windows
    -> app/strategy/aggregation/material_builder.py::build_material_pack
    -> app/strategy/aggregation/repository.py::create_aggregation_run
    -> app/strategy/aggregation/repository.py::create_material_pack

Scheduler auto hook:
app/scheduler/jobs/strategy_aggregation_job.py::run_strategy_aggregation_after_signal_job
    -> app/strategy/aggregation/service.py::run_strategy_aggregation

This file belongs to `app/strategy/aggregation`. It consumes existing stage-16
strategy signal rows and the stage-15 snapshot restore contract, performs only
deterministic aggregation/material-pack construction, projects analysis
hypothesis directions from existing upstream rows, and optionally writes the
stage-18 tables.

It does not call `scripts/run_strategy_signals.py`, does not call the stage-16
StrategySignalService, does not call the stage-15 snapshot service, does not
request Binance REST/WebSocket, does not modify formal Kline tables, does not
read/write Redis, does not implement real strategy classes, does not judge
long/short from Klines, does not call DeepSeek or any large language model,
does not generate strategy signals or final trading advice, does not read
private trading state, and does not perform trading.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy.aggregation.candidate_scenario_builder import build_candidate_scenarios, build_validation_plan
from app.strategy.aggregation.hermes_formatter import build_strategy_aggregation_visible_body
from app.strategy.aggregation.indicators import latest_close_price
from app.strategy.aggregation.material_builder import build_future_leakage_guard, build_material_pack
from app.strategy.aggregation.payloads import (
    blocked_result,
    build_blocked_payload,
    build_result_from_decision,
    build_success_payload,
    failed_result,
)
from app.strategy.aggregation.repository import (
    StrategyAggregationRepository,
    create_default_strategy_aggregation_repository,
)
from app.strategy.aggregation.rules import (
    aggregation_status_from_inputs,
    build_aggregation_decision,
    build_evidence_json,
    build_support_resistance_probe,
    classify_strategy_results,
)
from app.strategy.aggregation.types import (
    AGGREGATION_VERSION,
    ANALYSIS_HYPOTHESIS_SEMANTICS,
    CANDIDATE_SCENARIO_VERSION,
    DIRECTION_PROJECTION_SOURCE,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    INDICATOR_VERSION,
    MATERIAL_SCHEMA_VERSION,
    AggregationRiskLevel,
    AnalysisMaterialPackPersistencePayload,
    AnalysisHypothesisConfidence,
    AnalysisHypothesisDirection,
    ConflictLevel,
    RiskGateStatus,
    StrategyAggregationHermesStatus,
    StrategyAggregationRequest,
    StrategyAggregationResult,
    StrategyAggregationStatus,
)

try:
    from sqlalchemy.exc import IntegrityError
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    IntegrityError = None  # type: ignore[assignment]

ALLOWED_AGGREGATION_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER})
ALLOWED_INPUT_RUN_STATUSES = frozenset({"success", "partial_success"})
FINAL_AGGREGATION_STATUSES = (
    StrategyAggregationStatus.SUCCESS.value,
    StrategyAggregationStatus.PARTIAL_SUCCESS.value,
)


class StrategyAggregationService:
    """Coordinate one stage-18 aggregation and material-pack attempt.

    Parameters: settings, repository, and alert sender are injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid request, missing stage-16 run/result rows,
    invalid snapshot windows, future-leakage guard failure, material build
    failure, database persistence failure, and Hermes failure are converted into
    structured results.
    External effects: confirm-write may write stage-18 tables and may send one
    Hermes notification according to config. Dry-run writes nothing and sends
    nothing.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: StrategyAggregationRepository | Any | None = None,
        alert_sender: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_aggregation_repository()
        self._alert_sender = alert_sender or _default_alert_sender

    def run_strategy_aggregation(
        self,
        db_session: Any,
        *,
        request: StrategyAggregationRequest,
    ) -> StrategyAggregationResult:
        """Run deterministic strategy aggregation for one existing stage-16 run.

        Parameters: caller-owned MySQL session and stage-18 request.
        Return value: compact `StrategyAggregationResult`.
        Failure scenarios: see class docstring.
        External effects: dry-run is read-only; confirm-write writes stage-18
        rows only after all deterministic material checks pass.
        """

        trace_id = request.trace_id or uuid.uuid4().hex
        aggregation_run_id = _build_aggregation_run_id(request.strategy_signal_run_id, trace_id=trace_id)
        material_pack_id = _build_material_pack_id(request.strategy_signal_run_id, trace_id=trace_id)
        invalid_result = _validate_request(request, aggregation_run_id=aggregation_run_id, trace_id=trace_id)
        if invalid_result is not None:
            return invalid_result

        try:
            existing = self._repository.get_existing_aggregation(
                db_session,
                strategy_signal_run_id=request.strategy_signal_run_id,
                aggregation_version=AGGREGATION_VERSION,
                material_schema_version=MATERIAL_SCHEMA_VERSION,
                indicator_version=INDICATOR_VERSION,
                candidate_scenario_version=CANDIDATE_SCENARIO_VERSION,
                statuses=FINAL_AGGREGATION_STATUSES,
            )
        except Exception as exc:  # noqa: BLE001 - database read failure is a service failure.
            _rollback_if_possible(db_session)
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                message="Strategy aggregation idempotency check failed.",
                error_message=str(exc),
            )
        if existing is not None:
            return self._build_skipped_result_from_existing(
                db_session,
                existing=existing,
                request=request,
                trace_id=trace_id,
            )

        try:
            strategy_run = self._repository.get_strategy_signal_run(
                db_session,
                run_id=request.strategy_signal_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - database read failure is a service failure.
            _rollback_if_possible(db_session)
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                message="Strategy signal run lookup failed.",
                error_message=str(exc),
            )
        if strategy_run is None:
            return blocked_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                message="strategy_signal_run does not exist.",
                error_code="strategy_signal_run_not_found",
            )

        input_blocked = self._validate_existing_run(strategy_run)
        if input_blocked is not None:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message=input_blocked["message"],
                error_code=input_blocked["error_code"],
                error_message=input_blocked.get("error_message"),
            )

        try:
            strategy_results = self._repository.list_strategy_signal_results(
                db_session,
                run_id=request.strategy_signal_run_id,
            )
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                snapshot_id=getattr(strategy_run, "snapshot_id", None),
                message="Strategy signal result lookup failed.",
                error_message=str(exc),
            )
        if not strategy_results:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message="strategy_signal_result is empty.",
                error_code="strategy_signal_result_empty",
            )

        try:
            restored_snapshot = self._repository.restore_snapshot_kline_windows(
                db_session,
                snapshot_id=str(getattr(strategy_run, "snapshot_id")),
            )
            future_guard = build_future_leakage_guard(restored_snapshot)
        except Exception as exc:  # noqa: BLE001
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message="Snapshot Kline window could not be restored for aggregation.",
                error_code="snapshot_restore_failed",
                error_message=str(exc),
            )
        if future_guard.get("uses_future_klines"):
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message="Future-leakage guard blocked stage-18 material construction.",
                error_code="future_leakage_guard_failed",
                error_message=json.dumps(future_guard, ensure_ascii=False, sort_keys=True, default=str),
            )

        vote_summary = classify_strategy_results(tuple(strategy_results))
        if vote_summary.effective_strategy_count <= 0:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message="No effective strategy signal is available for aggregation.",
                error_code="effective_strategy_count_insufficient",
            )

        decision = build_aggregation_decision(vote_summary)
        strategy_evidence_aggregation = None
        evidence_getter = getattr(self._repository, "get_latest_strategy_evidence_aggregation", None)
        if callable(evidence_getter):
            try:
                strategy_evidence_aggregation = evidence_getter(
                    db_session,
                    strategy_signal_run_id=request.strategy_signal_run_id,
                )
            except Exception:  # noqa: BLE001 - 23F bridge is optional for stage-18 compatibility.
                strategy_evidence_aggregation = None
        try:
            latest_close = latest_close_price(restored_snapshot.rows_4h)
            support_resistance_probe = build_support_resistance_probe(
                restored_snapshot=restored_snapshot,
                latest_close=latest_close,
                strategy_run=strategy_run,
            )
            candidate_scenarios_json = build_candidate_scenarios(
                decision=decision,
                vote_summary=vote_summary,
                latest_close=latest_close,
                support_resistance=support_resistance_probe,
                structure_state=str(support_resistance_probe.get("structure_state", "unknown")),
                volatility_state=str(support_resistance_probe.get("volatility_state", "unknown")),
            )
            material_pack = build_material_pack(
                strategy_signal_run=strategy_run,
                strategy_signal_results=tuple(strategy_results),
                restored_snapshot=restored_snapshot,
                vote_summary=vote_summary,
                decision=decision,
                candidate_scenarios_json=candidate_scenarios_json,
                strategy_evidence_aggregation=strategy_evidence_aggregation,
            )
        except ValueError as exc:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                strategy_run=strategy_run,
                aggregation_run_id=aggregation_run_id,
                trace_id=trace_id,
                message="Stage-18 material pack could not be built from the snapshot window.",
                error_code="material_pack_input_insufficient",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - material code failure is failed, not blocked.
            _rollback_if_possible(db_session)
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                snapshot_id=getattr(strategy_run, "snapshot_id", None),
                message="Stage-18 deterministic material construction failed.",
                error_message=str(exc),
            )

        status = aggregation_status_from_inputs(strategy_run=strategy_run, vote_summary=vote_summary)
        payload = build_success_payload(
            request=request,
            strategy_run=strategy_run,
            vote_summary=vote_summary,
            decision=decision,
            status=status,
            aggregation_run_id=aggregation_run_id,
            trace_id=trace_id,
            candidate_scenarios_json=candidate_scenarios_json,
            summary_json=material_pack.summary_json,
            evidence_json=build_evidence_json(vote_summary),
            conflict_json=material_pack.material_json["strategy_conflict_points"],  # type: ignore[index]
            validation_plan_json=material_pack.validation_plan_json,
            message=decision.message,
            hermes_enabled=self._settings.strategy_aggregation_hermes_enabled,
        )
        result = build_result_from_decision(
            request=request,
            strategy_run=strategy_run,
            vote_summary=vote_summary,
            decision=decision,
            status=status,
            aggregation_run_id=aggregation_run_id,
            material_pack_id=material_pack_id,
            trace_id=trace_id,
            message=decision.message,
        )
        if request.dry_run:
            return result

        try:
            aggregation_row = self._repository.create_aggregation_run(db_session, payload=payload)
            material_row = self._repository.create_material_pack(
                db_session,
                payload=AnalysisMaterialPackPersistencePayload(
                    material_pack_id=material_pack_id,
                    aggregation_run_id=aggregation_run_id,
                    strategy_signal_run_id=request.strategy_signal_run_id,
                    snapshot_id=str(getattr(strategy_run, "snapshot_id")),
                    symbol=str(getattr(strategy_run, "symbol")),
                    base_interval=str(getattr(strategy_run, "base_interval_value")),
                    higher_interval=str(getattr(strategy_run, "higher_interval_value")),
                    aggregation_version=AGGREGATION_VERSION,
                    material_schema_version=MATERIAL_SCHEMA_VERSION,
                    indicator_version=INDICATOR_VERSION,
                    candidate_scenario_version=CANDIDATE_SCENARIO_VERSION,
                    status=status,
                    material_json=material_pack.material_json,
                    question_json=material_pack.question_json,
                    validation_plan_json=material_pack.validation_plan_json,
                    summary_json=material_pack.summary_json,
                    data_window_json=material_pack.data_window_json,
                    future_leakage_guard_json=material_pack.future_leakage_guard_json,
                    trace_id=trace_id,
                    created_by=request.created_by,
                ),
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - persistence errors become structured failures.
            _rollback_if_possible(db_session)
            skipped_result = self._build_skipped_result_after_unique_conflict(
                db_session,
                request=request,
                trace_id=trace_id,
                exc=exc,
            )
            if skipped_result is not None:
                return skipped_result
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                snapshot_id=getattr(strategy_run, "snapshot_id", None),
                message="Strategy aggregation persistence failed.",
                error_message=str(exc),
            )

        result = replace(result, material_pack_id=getattr(material_row, "material_pack_id", material_pack_id))
        return self._record_hermes_and_return(
            db_session,
            aggregation_row=aggregation_row,
            result=result,
        )

    def _validate_existing_run(self, strategy_run: Any) -> dict[str, str] | None:
        status = str(getattr(strategy_run, "status", "") or "")
        if status not in ALLOWED_INPUT_RUN_STATUSES:
            return {
                "message": "strategy_signal_run status is not allowed for stage-18 aggregation.",
                "error_code": "strategy_signal_run_status_not_allowed",
                "error_message": f"status={status}",
            }
        if not getattr(strategy_run, "snapshot_id", None):
            return {
                "message": "strategy_signal_run has no snapshot_id.",
                "error_code": "strategy_signal_run_snapshot_missing",
            }
        return None

    def _return_or_persist_blocked(
        self,
        db_session: Any,
        *,
        request: StrategyAggregationRequest,
        strategy_run: Any,
        aggregation_run_id: str,
        trace_id: str,
        message: str,
        error_code: str,
        error_message: str | None = None,
    ) -> StrategyAggregationResult:
        result = blocked_result(
            request,
            aggregation_run_id=aggregation_run_id,
            material_pack_id=None,
            trace_id=trace_id,
            snapshot_id=getattr(strategy_run, "snapshot_id", None),
            message=message,
            error_code=error_code,
            error_message=error_message,
        )
        if request.dry_run:
            return result
        payload = build_blocked_payload(
            request=request,
            strategy_run=strategy_run,
            aggregation_run_id=aggregation_run_id,
            trace_id=trace_id,
            message=message,
            error_code=error_code,
            error_message=error_message,
            hermes_enabled=self._settings.strategy_aggregation_hermes_enabled,
        )
        try:
            aggregation_row = self._repository.create_aggregation_run(db_session, payload=payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            skipped_result = self._build_skipped_result_after_unique_conflict(
                db_session,
                request=request,
                trace_id=trace_id,
                exc=exc,
            )
            if skipped_result is not None:
                return skipped_result
            return failed_result(
                request,
                aggregation_run_id=aggregation_run_id,
                material_pack_id=None,
                trace_id=trace_id,
                snapshot_id=getattr(strategy_run, "snapshot_id", None),
                message="Blocked aggregation audit persistence failed.",
                error_message=str(exc),
            )
        return self._record_hermes_and_return(
            db_session,
            aggregation_row=aggregation_row,
            result=result,
        )

    def _build_skipped_result_from_existing(
        self,
        db_session: Any,
        *,
        existing: Any,
        request: StrategyAggregationRequest,
        trace_id: str,
    ) -> StrategyAggregationResult:
        material_pack = None
        try:
            material_pack = self._repository.get_material_pack_by_aggregation_run_id(
                db_session,
                aggregation_run_id=str(getattr(existing, "aggregation_run_id", "")),
            )
        except Exception:
            material_pack = None
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            aggregation_run_id=str(getattr(existing, "aggregation_run_id", "")),
            material_pack_id=getattr(material_pack, "material_pack_id", None),
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=trace_id,
            snapshot_id=getattr(existing, "snapshot_id", None),
            analysis_hypothesis_direction=_analysis_hypothesis_direction_or_none(getattr(existing, "analysis_hypothesis_direction", None)),
            analysis_hypothesis_confidence=_analysis_hypothesis_confidence_or_none(
                getattr(existing, "analysis_hypothesis_confidence", None)
            ),
            analysis_hypothesis_semantics=str(
                getattr(existing, "analysis_hypothesis_semantics", ANALYSIS_HYPOTHESIS_SEMANTICS)
                or ANALYSIS_HYPOTHESIS_SEMANTICS
            ),
            direction_projection_source=str(
                getattr(existing, "direction_projection_source", DIRECTION_PROJECTION_SOURCE)
                or DIRECTION_PROJECTION_SOURCE
            ),
            stop_trading_source=getattr(existing, "stop_trading_source", None),
            risk_gate_projection_source=getattr(existing, "risk_gate_projection_source", None),
            is_strategy_signal=bool(getattr(existing, "is_strategy_signal", False)),
            is_trading_advice=bool(getattr(existing, "is_trading_advice", False)),
            is_executable=bool(getattr(existing, "is_executable", False)),
            strategy_logic_implemented=bool(getattr(existing, "strategy_logic_implemented", False)),
            promotion_allowed=bool(getattr(existing, "promotion_allowed", False)),
            promotion_requires_future_strategy_and_llm_stage=bool(
                getattr(existing, "promotion_requires_future_strategy_and_llm_stage", True)
            ),
            risk_level=_risk_level_or_none(getattr(existing, "risk_level", None)),
            risk_gate_status=_risk_gate_or_none(getattr(existing, "risk_gate_status", None)),
            conflict_level=_conflict_level_or_none(getattr(existing, "conflict_level", None)),
            input_strategy_count=int(getattr(existing, "input_strategy_count", 0) or 0),
            input_success_count=int(getattr(existing, "input_success_count", 0) or 0),
            input_failed_count=int(getattr(existing, "input_failed_count", 0) or 0),
            input_invalid_count=int(getattr(existing, "input_invalid_count", 0) or 0),
            input_not_implemented_count=int(getattr(existing, "input_not_implemented_count", 0) or 0),
            effective_strategy_count=int(getattr(existing, "effective_strategy_count", 0) or 0),
            message=f"Stage-18 aggregation skipped: already_exists existing status={getattr(existing, 'status', '')}.",
            hermes_status=StrategyAggregationHermesStatus.NOT_REQUIRED,
            details={"skip_reason": "already_exists"},
        )

    def _build_skipped_result_after_unique_conflict(
        self,
        db_session: Any,
        *,
        request: StrategyAggregationRequest,
        trace_id: str,
        exc: Exception,
    ) -> StrategyAggregationResult | None:
        """Convert concurrent final-result unique conflicts into skipped.

        Only success/partial_success material packs are final. If a concurrent
        run wins the final unique constraint, this attempt re-queries the
        existing final row and returns skipped/already_exists instead of
        reporting a failed stage-18 run.
        """

        if not _is_unique_constraint_error(exc):
            return None
        try:
            existing = self._repository.get_existing_aggregation(
                db_session,
                strategy_signal_run_id=request.strategy_signal_run_id,
                aggregation_version=AGGREGATION_VERSION,
                material_schema_version=MATERIAL_SCHEMA_VERSION,
                indicator_version=INDICATOR_VERSION,
                candidate_scenario_version=CANDIDATE_SCENARIO_VERSION,
                statuses=FINAL_AGGREGATION_STATUSES,
            )
        except Exception:  # noqa: BLE001 - fall back to the original persistence failure.
            return None
        if existing is None:
            return None
        skipped = self._build_skipped_result_from_existing(
            db_session,
            existing=existing,
            request=request,
            trace_id=trace_id,
        )
        return replace(
            skipped,
            message="Stage-18 aggregation skipped: already_exists final success material pack.",
            details={**dict(skipped.details), "skip_reason": "already_exists", "unique_conflict_recovered": True},
        )

    def _record_hermes_and_return(
        self,
        db_session: Any,
        *,
        aggregation_row: Any,
        result: StrategyAggregationResult,
    ) -> StrategyAggregationResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_hermes(
            aggregation_row=aggregation_row,
            result=result,
        )
        try:
            self._repository.record_hermes_result(
                db_session,
                aggregation_row,
                hermes_status=hermes_status.value,
                hermes_message=hermes_message,
                hermes_error=hermes_error,
                hermes_sent_at_utc=hermes_sent_at_utc,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - notification result write must not rewrite aggregation status.
            _rollback_if_possible(db_session)
            return replace(
                result,
                hermes_status=StrategyAggregationHermesStatus.FAILED,
                error_message=result.error_message or f"Hermes status persistence failed: {exc}",
            )
        return replace(result, hermes_status=hermes_status)

    def _send_or_skip_hermes(
        self,
        *,
        aggregation_row: Any,
        result: StrategyAggregationResult,
    ) -> tuple[StrategyAggregationHermesStatus, str | None, str | None, datetime | None]:
        if not self._settings.strategy_aggregation_hermes_enabled:
            return StrategyAggregationHermesStatus.DISABLED, None, None, None
        if not _should_notify_status(self._settings, result.status):
            return StrategyAggregationHermesStatus.NOT_REQUIRED, None, None, None
        visible_body = build_strategy_aggregation_visible_body(result, aggregation_row)
        alert_event = AlertEvent(
            alert_type=AlertType.STRATEGY_AGGREGATION,
            severity=_alert_severity_for_status(result.status),
            title=_alert_title_for_status(result.status),
            summary=_alert_title_for_status(result.status),
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "aggregation_run_id": result.aggregation_run_id,
                "material_pack_id": result.material_pack_id or "",
                "strategy_signal_run_id": result.strategy_signal_run_id,
                "snapshot_id": result.snapshot_id or "",
                "status": result.status.value,
                "analysis_hypothesis_direction": result.analysis_hypothesis_direction.value if result.analysis_hypothesis_direction else "",
                "analysis_hypothesis_semantics": result.analysis_hypothesis_semantics,
                "direction_projection_source": result.direction_projection_source,
                "stop_trading_source": result.stop_trading_source or "",
                "risk_gate_projection_source": result.risk_gate_projection_source or "",
                "is_strategy_signal": result.is_strategy_signal,
                "is_trading_advice": result.is_trading_advice,
                "is_executable": result.is_executable,
                "strategy_logic_implemented": result.strategy_logic_implemented,
                "promotion_allowed": result.promotion_allowed,
                "promotion_requires_future_strategy_and_llm_stage": (
                    result.promotion_requires_future_strategy_and_llm_stage
                ),
                "no_large_model_call": True,
                "no_advice_lifecycle": True,
                "no_auto_trading": True,
            },
            source="app.strategy.aggregation.service",
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(
                alert_event,
                settings=self._settings,
                send_real_alert=True,
            )
        except Exception as exc:  # noqa: BLE001
            return StrategyAggregationHermesStatus.FAILED, visible_body, str(exc), None
        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            return (
                StrategyAggregationHermesStatus.SENT,
                visible_body,
                None,
                getattr(send_result, "submitted_at_utc", None) or now_utc(),
            )
        return (
            StrategyAggregationHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )


def run_strategy_aggregation(
    *,
    db_session: Any,
    request: StrategyAggregationRequest,
    service: StrategyAggregationService | None = None,
) -> StrategyAggregationResult:
    """Convenience app-service function used by CLI, scheduler, and tests."""

    active_service = service or create_default_strategy_aggregation_service()
    return active_service.run_strategy_aggregation(db_session, request=request)


def create_default_strategy_aggregation_service() -> StrategyAggregationService:
    """Create the default stage-18 strategy aggregation service."""

    return StrategyAggregationService()


def _validate_request(
    request: StrategyAggregationRequest,
    *,
    aggregation_run_id: str,
    trace_id: str,
) -> StrategyAggregationResult | None:
    problems: list[str] = []
    if not request.strategy_signal_run_id.strip():
        problems.append("strategy_signal_run_id is required")
    if request.trigger_source not in ALLOWED_AGGREGATION_TRIGGER_SOURCES:
        problems.append("trigger_source supports only cli or scheduler")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run strategy aggregation requires confirm_write")
    if not problems:
        return None
    return StrategyAggregationResult(
        status=StrategyAggregationStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        aggregation_run_id=aggregation_run_id,
        material_pack_id=None,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        message="Strategy aggregation request parameters are invalid.",
        error_message="; ".join(problems),
    )


def _build_aggregation_run_id(strategy_signal_run_id: str, *, trace_id: str) -> str:
    stable = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{strategy_signal_run_id}:{AGGREGATION_VERSION}:{MATERIAL_SCHEMA_VERSION}:{INDICATOR_VERSION}:{CANDIDATE_SCENARIO_VERSION}",
    ).hex[:12]
    return f"SAR-{stable}-{uuid.uuid4().hex[:8]}"


def _build_material_pack_id(strategy_signal_run_id: str, *, trace_id: str) -> str:
    stable = uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"{strategy_signal_run_id}:{MATERIAL_SCHEMA_VERSION}:{INDICATOR_VERSION}:{CANDIDATE_SCENARIO_VERSION}",
    ).hex[:12]
    return f"AMP-{stable}-{uuid.uuid4().hex[:8]}"


def _analysis_hypothesis_direction_or_none(value: Any) -> AnalysisHypothesisDirection | None:
    try:
        return AnalysisHypothesisDirection(str(value))
    except ValueError:
        return None


def _analysis_hypothesis_confidence_or_none(value: Any) -> AnalysisHypothesisConfidence | None:
    try:
        return AnalysisHypothesisConfidence(str(value))
    except ValueError:
        return None


def _risk_level_or_none(value: Any) -> AggregationRiskLevel | None:
    try:
        return AggregationRiskLevel(str(value))
    except ValueError:
        return None


def _risk_gate_or_none(value: Any) -> RiskGateStatus | None:
    try:
        return RiskGateStatus(str(value))
    except ValueError:
        return None


def _conflict_level_or_none(value: Any) -> ConflictLevel | None:
    try:
        return ConflictLevel(str(value))
    except ValueError:
        return None


def _should_notify_status(settings: AppSettings, status: StrategyAggregationStatus) -> bool:
    if status == StrategyAggregationStatus.SUCCESS:
        return settings.strategy_aggregation_hermes_notify_success
    if status == StrategyAggregationStatus.PARTIAL_SUCCESS:
        return settings.strategy_aggregation_hermes_notify_partial_success
    if status == StrategyAggregationStatus.BLOCKED:
        return settings.strategy_aggregation_hermes_notify_blocked
    if status == StrategyAggregationStatus.FAILED:
        return settings.strategy_aggregation_hermes_notify_failed
    if status == StrategyAggregationStatus.SKIPPED:
        return settings.strategy_aggregation_hermes_notify_skipped
    return False


def _alert_severity_for_status(status: StrategyAggregationStatus) -> AlertSeverity:
    if status == StrategyAggregationStatus.FAILED:
        return AlertSeverity.ERROR
    if status == StrategyAggregationStatus.BLOCKED:
        return AlertSeverity.WARNING
    if status == StrategyAggregationStatus.PARTIAL_SUCCESS:
        return AlertSeverity.NOTICE
    return AlertSeverity.INFO


def _alert_title_for_status(status: StrategyAggregationStatus) -> str:
    if status in (StrategyAggregationStatus.SUCCESS, StrategyAggregationStatus.PARTIAL_SUCCESS):
        return "BTC 策略聚合分析假设结果"
    return "BTC 策略聚合分析假设异常"


def _is_unique_constraint_error(exc: Exception) -> bool:
    if IntegrityError is not None and isinstance(exc, IntegrityError):
        text = str(getattr(exc, "orig", exc)).lower()
    else:
        text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "unique",
            "duplicate",
            "uq_",
            "uk_",
        )
    )


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


__all__ = [
    "ALLOWED_AGGREGATION_TRIGGER_SOURCES",
    "StrategyAggregationService",
    "create_default_strategy_aggregation_service",
    "run_strategy_aggregation",
]
