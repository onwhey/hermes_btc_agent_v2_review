"""Service orchestration for 27A weak model / factor layer.

Call chain:
scripts/run_weak_models.py::main
    -> app/weak_models/service.py::WeakModelService.run_weak_models_for_strategy_signal
    -> app/weak_models/repository.py::get_strategy_signal_run
    -> app/weak_models/repository.py::get_snapshot_by_snapshot_id
    -> app/weak_models/repository.py::restore_snapshot_kline_windows
    -> app/weak_models/registry.py::load_enabled_models
    -> app/weak_models/models.py::<weak model>.evaluate
    -> app/weak_models/aggregation.py::WeakModelAggregator.aggregate
    -> app/weak_models/repository.py::upsert_run/result/aggregation

This file belongs to `app/weak_models`. It validates the stage-16 SSR-bound
snapshot, executes local rule-based weak models, aggregates active outputs, and
optionally writes the three 27A audit tables. It never calls DeepSeek/GPT/Claude,
never requests Binance REST, never sends Hermes, never reads Redis, never reads
private trading state, never creates orders, and never performs automatic trading.
It also does not modify strategy algorithms, stage 18 material-pack logic, stage
19/20 model-review logic, or stage 21 advice lifecycle logic.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import ensure_utc_aware
from app.market_context.snapshot_types import MarketContextSnapshotRestoreError, MarketContextSnapshotStatus
from app.weak_models.aggregation import WeakModelAggregator
from app.weak_models.base import BaseWeakModel
from app.weak_models.registry import WeakModelRegistry, create_default_weak_model_registry
from app.weak_models.repository import WeakModelRepository, create_default_weak_model_repository
from app.weak_models.types import (
    WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
    WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS,
    WeakModelAggregationSummary,
    WeakModelEvaluationInput,
    WeakModelOutput,
    WeakModelProfile,
    WeakModelResultPayload,
    WeakModelResultStatus,
    WeakModelRunPersistencePayload,
    WeakModelRunRequest,
    WeakModelRunResult,
    WeakModelRunStatus,
    build_weak_model_result_id,
    build_weak_model_run_id,
    status_exit_code,
)


class WeakModelService:
    """Run 27A weak models against the SSR-bound market-context snapshot."""

    def __init__(
        self,
        *,
        repository: WeakModelRepository | Any | None = None,
        registry: WeakModelRegistry | Any | None = None,
        aggregator: WeakModelAggregator | None = None,
    ) -> None:
        self._repository = repository or create_default_weak_model_repository()
        self._registry = registry or create_default_weak_model_registry()
        self._aggregator = aggregator or WeakModelAggregator()

    def run_weak_models_for_strategy_signal(self, db_session: Any, request: WeakModelRunRequest) -> WeakModelRunResult:
        """Validate SSR snapshot, run weak models, and optionally persist results.

        Parameters: caller-owned DB session and a request containing
        `strategy_signal_run_id`. `kline_slot_utc` is only an extra validation
        guard; this service never calls stage 15 ensure-snapshot in 27A.
        Returns: a `WeakModelRunResult` suitable for CLI output.
        Failure scenarios: invalid or missing SSR/snapshot returns blocked;
        model/config/database failures return failed or partial_success as
        described in the result. External services are not accessed.
        Data impact: writes weak-model audit tables only when
        `confirm_write=True` and `dry_run=False`; it commits only its own
        caller-owned session after successful persistence.
        """

        weak_model_run_id = build_weak_model_run_id(request.strategy_signal_run_id, trace_id=request.trace_id)
        try:
            profiles = tuple(self._registry.load_profiles())
            models = tuple(self._registry.load_enabled_models())
        except Exception as exc:
            return self._failed_result(
                request,
                weak_model_run_id=weak_model_run_id,
                error_code="weak_model_config_invalid",
                error_message=str(exc),
            )

        profiles_by_key = {profile.model_key: profile for profile in profiles}
        ssr = self._repository.get_strategy_signal_run(db_session, run_id=request.strategy_signal_run_id)
        if ssr is None:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=None,
                kline_slot_utc=request.kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_message=f"strategy_signal_run_id={request.strategy_signal_run_id} 不存在",
                persist_allowed=False,
            )

        validation_error_code, validation_error = self._validate_strategy_signal_run(ssr, request)
        if validation_error is not None:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=_text_attr(ssr, "snapshot_id") or None,
                kline_slot_utc=request.kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_code=validation_error_code or WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
                error_message=validation_error,
            )

        snapshot_id = str(getattr(ssr, "snapshot_id"))
        snapshot = self._repository.get_snapshot_by_snapshot_id(db_session, snapshot_id=snapshot_id)
        if snapshot is None:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=snapshot_id,
                kline_slot_utc=request.kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_message=f"SSR 绑定的 snapshot_id={snapshot_id} 不存在",
            )

        snapshot_validation_error, kline_slot_utc = self._validate_snapshot(snapshot, ssr, request)
        if snapshot_validation_error is not None:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=snapshot_id,
                kline_slot_utc=kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_message=snapshot_validation_error,
            )
        if kline_slot_utc is None:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=snapshot_id,
                kline_slot_utc=None,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_message="snapshot.latest_4h_open_time_utc 缺失，无法确认 27A slot",
            )

        try:
            restored = self._repository.restore_snapshot_kline_windows(db_session, snapshot_id=snapshot_id)
        except MarketContextSnapshotRestoreError as exc:
            return self._blocked_result(
                db_session,
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=snapshot_id,
                kline_slot_utc=kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=len(models),
                error_message=f"SSR 绑定 snapshot 无法还原 K线窗口：{exc}",
            )

        input_data = WeakModelEvaluationInput(
            pipeline_run_id=request.pipeline_run_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id=snapshot_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=kline_slot_utc,
            base_klines=tuple(restored.rows_4h),
            higher_klines=tuple(restored.rows_1d),
            trace_id=request.trace_id,
        )
        outputs = tuple(self._evaluate_models(models, input_data))
        failed_count = sum(1 for output in outputs if output.status == WeakModelResultStatus.FAILED)
        if not models:
            return self._failed_result(
                request,
                weak_model_run_id=weak_model_run_id,
                snapshot_id=snapshot_id,
                kline_slot_utc=kline_slot_utc,
                model_count_total=len(profiles),
                model_count_enabled=0,
                error_code="no_enabled_weak_models",
                error_message="没有启用的 27A 弱模型配置",
            )

        aggregation = self._aggregator.aggregate(
            weak_model_run_id=weak_model_run_id,
            input_data=input_data,
            outputs=outputs,
            profiles_by_key=profiles_by_key,
        )
        if failed_count == len(models):
            status = WeakModelRunStatus.FAILED
        elif failed_count:
            status = WeakModelRunStatus.PARTIAL_SUCCESS
        else:
            status = WeakModelRunStatus.DRY_RUN if request.dry_run or not request.confirm_write else WeakModelRunStatus.SUCCESS

        result = WeakModelRunResult(
            status=status,
            exit_code=status_exit_code(status),
            weak_model_run_id=weak_model_run_id,
            trace_id=request.trace_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id=snapshot_id,
            weak_model_aggregation_id=aggregation.weak_model_aggregation_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=kline_slot_utc,
            model_count_total=len(profiles),
            model_count_enabled=len(models),
            model_count_executed=len(outputs),
            model_count_failed=failed_count,
            database_written=False,
            database_action="dry_run" if request.dry_run or not request.confirm_write else "pending",
            outputs=outputs,
            aggregation=aggregation,
            details={
                "dry_run": bool(request.dry_run),
                "confirm_write": bool(request.confirm_write),
                "mode": "strategy_signal_run_id",
                "not_trading_advice": True,
            },
        )
        if request.confirm_write and not request.dry_run:
            return self._persist_completed_result(db_session, request, result, input_data, profiles_by_key, aggregation)
        return result

    def _evaluate_models(self, models: tuple[BaseWeakModel, ...], input_data: WeakModelEvaluationInput) -> list[WeakModelOutput]:
        outputs: list[WeakModelOutput] = []
        for model in models:
            try:
                outputs.append(model.evaluate(input_data))
            except Exception as exc:  # pragma: no cover - exercised by service tests with a fake model.
                outputs.append(
                    WeakModelOutput(
                        model_key=model.profile.model_key,
                        model_role=model.profile.model_role,
                        status=WeakModelResultStatus.FAILED,
                        error_code="weak_model_evaluation_failed",
                        error_message=str(exc),
                        confidence=0.0,
                        static_weight=model.profile.static_weight,
                        raw_output={"not_trading_advice": True, "error_code": "weak_model_evaluation_failed"},
                    )
                )
        return outputs

    def _persist_completed_result(
        self,
        db_session: Any,
        request: WeakModelRunRequest,
        result: WeakModelRunResult,
        input_data: WeakModelEvaluationInput,
        profiles_by_key: dict[str, WeakModelProfile],
        aggregation: WeakModelAggregationSummary,
    ) -> WeakModelRunResult:
        run_action = self._persist_run_row(db_session, request, result)
        result_actions: list[str] = []
        for output in result.outputs:
            profile = profiles_by_key.get(output.model_key)
            if profile is None:
                continue
            _, action = self._repository.upsert_result(
                db_session,
                payload=WeakModelResultPayload(
                    weak_model_result_id=build_weak_model_result_id(result.weak_model_run_id, output.model_key),
                    weak_model_run_id=result.weak_model_run_id,
                    profile=profile,
                    output=output,
                    input_data=input_data,
                ),
            )
            result_actions.append(action)
        _, aggregation_action = self._repository.upsert_aggregation(db_session, aggregation=aggregation)
        _commit_if_possible(db_session)
        return _replace_result(
            result,
            database_written=True,
            database_action=f"run_{run_action};results_{len(result_actions)};aggregation_{aggregation_action}",
            status=WeakModelRunStatus.SUCCESS
            if result.status == WeakModelRunStatus.DRY_RUN
            else result.status,
            exit_code=status_exit_code(
                WeakModelRunStatus.SUCCESS if result.status == WeakModelRunStatus.DRY_RUN else result.status
            ),
        )

    def _persist_run_row(self, db_session: Any, request: WeakModelRunRequest, result: WeakModelRunResult) -> str:
        _, action = self._repository.upsert_run(
            db_session,
            payload=WeakModelRunPersistencePayload(
                weak_model_run_id=result.weak_model_run_id,
                pipeline_run_id=request.pipeline_run_id,
                strategy_signal_run_id=request.strategy_signal_run_id,
                snapshot_id=result.snapshot_id,
                symbol=result.symbol or request.symbol,
                base_interval=result.base_interval or request.base_interval,
                higher_interval=result.higher_interval or request.higher_interval,
                kline_slot_utc=result.kline_slot_utc,
                run_status=result.status.value,
                trigger_source=request.trigger_source,
                model_count_total=result.model_count_total,
                model_count_enabled=result.model_count_enabled,
                model_count_executed=result.model_count_executed,
                model_count_failed=result.model_count_failed,
                trace_id=result.trace_id,
                details=result.details,
            ),
        )
        return action

    def _blocked_result(
        self,
        db_session: Any,
        request: WeakModelRunRequest,
        *,
        weak_model_run_id: str,
        snapshot_id: str | None,
        kline_slot_utc: Any | None,
        model_count_total: int,
        model_count_enabled: int,
        error_code: str = WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
        error_message: str,
        persist_allowed: bool = True,
    ) -> WeakModelRunResult:
        slot = ensure_utc_aware(kline_slot_utc) if kline_slot_utc is not None else None
        result = WeakModelRunResult(
            status=WeakModelRunStatus.BLOCKED,
            exit_code=status_exit_code(WeakModelRunStatus.BLOCKED),
            weak_model_run_id=weak_model_run_id,
            trace_id=request.trace_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id=snapshot_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=slot,
            model_count_total=model_count_total,
            model_count_enabled=model_count_enabled,
            database_written=False,
            database_action="dry_run" if request.dry_run or not request.confirm_write else "not_written",
            blocked_reason=error_code,
            error_code=error_code,
            error_message=error_message,
            details={
                "mode": "strategy_signal_run_id",
                "dry_run": bool(request.dry_run),
                "confirm_write": bool(request.confirm_write),
                "not_trading_advice": True,
            },
        )
        if request.confirm_write and not request.dry_run and persist_allowed:
            run_action = self._persist_run_row(db_session, request=request, result=result)
            _commit_if_possible(db_session)
            return _replace_result(
                result,
                database_written=True,
                database_action=f"run_{run_action};blocked_no_results",
            )
        return result

    def _failed_result(
        self,
        request: WeakModelRunRequest,
        *,
        weak_model_run_id: str,
        snapshot_id: str | None = None,
        kline_slot_utc: Any | None = None,
        model_count_total: int = 0,
        model_count_enabled: int = 0,
        error_code: str,
        error_message: str,
    ) -> WeakModelRunResult:
        slot = ensure_utc_aware(kline_slot_utc) if kline_slot_utc is not None else None
        return WeakModelRunResult(
            status=WeakModelRunStatus.FAILED,
            exit_code=status_exit_code(WeakModelRunStatus.FAILED),
            weak_model_run_id=weak_model_run_id,
            trace_id=request.trace_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id=snapshot_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=slot,
            model_count_total=model_count_total,
            model_count_enabled=model_count_enabled,
            database_written=False,
            database_action="dry_run" if request.dry_run or not request.confirm_write else "not_written",
            error_code=error_code,
            error_message=error_message,
            details={
                "mode": "strategy_signal_run_id",
                "dry_run": bool(request.dry_run),
                "confirm_write": bool(request.confirm_write),
                "not_trading_advice": True,
            },
        )

    def _validate_strategy_signal_run(self, ssr: Any, request: WeakModelRunRequest) -> tuple[str | None, str | None]:
        run_status = _text_attr(ssr, "status")
        if run_status != "success":
            return (
                WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS,
                f"strategy_signal_run.status={run_status} is not success; 27A will not run weak models.",
            )
        if not _text_attr(ssr, "snapshot_id"):
            return (
                WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
                "SSR 缺少 snapshot_id，27A 不允许自行选择或生成快照",
            )
        mismatches: list[str] = []
        if _text_attr(ssr, "symbol") != request.symbol:
            mismatches.append(f"symbol: SSR={_text_attr(ssr, 'symbol')} request={request.symbol}")
        if _text_attr(ssr, "base_interval_value") != request.base_interval:
            mismatches.append(
                f"base_interval: SSR={_text_attr(ssr, 'base_interval_value')} request={request.base_interval}"
            )
        if _text_attr(ssr, "higher_interval_value") != request.higher_interval:
            mismatches.append(
                f"higher_interval: SSR={_text_attr(ssr, 'higher_interval_value')} request={request.higher_interval}"
            )
        return (
            WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
            "；".join(mismatches),
        ) if mismatches else (None, None)

    def _validate_snapshot(self, snapshot: Any, ssr: Any, request: WeakModelRunRequest) -> tuple[str | None, Any | None]:
        kline_slot_utc = ensure_utc_aware(getattr(snapshot, "latest_4h_open_time_utc", None))
        mismatches: list[str] = []
        if _text_attr(snapshot, "status") != MarketContextSnapshotStatus.CREATED.value:
            mismatches.append(f"snapshot.status={_text_attr(snapshot, 'status')} 不是 created")
        if _text_attr(snapshot, "symbol") != _text_attr(ssr, "symbol"):
            mismatches.append(
                f"snapshot.symbol={_text_attr(snapshot, 'symbol')} SSR.symbol={_text_attr(ssr, 'symbol')}"
            )
        if _text_attr(snapshot, "base_interval_value") != _text_attr(ssr, "base_interval_value"):
            mismatches.append(
                "snapshot.base_interval_value="
                f"{_text_attr(snapshot, 'base_interval_value')} SSR.base_interval_value={_text_attr(ssr, 'base_interval_value')}"
            )
        if _text_attr(snapshot, "higher_interval_value") != _text_attr(ssr, "higher_interval_value"):
            mismatches.append(
                "snapshot.higher_interval_value="
                f"{_text_attr(snapshot, 'higher_interval_value')} SSR.higher_interval_value={_text_attr(ssr, 'higher_interval_value')}"
            )
        if request.kline_slot_utc is not None and kline_slot_utc is not None:
            requested_slot = ensure_utc_aware(request.kline_slot_utc)
            if requested_slot != kline_slot_utc:
                mismatches.append(
                    "kline_slot_utc mismatch: "
                    f"request={requested_slot.isoformat()} snapshot={kline_slot_utc.isoformat()}"
                )
        return ("；".join(mismatches) if mismatches else None), kline_slot_utc


def create_default_weak_model_service() -> WeakModelService:
    """Create the default 27A weak model service."""

    return WeakModelService()


def _text_attr(row: Any, field_name: str) -> str:
    value = getattr(row, field_name, "")
    return "" if value is None else str(value)


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _replace_result(result: WeakModelRunResult, **changes: Any) -> WeakModelRunResult:
    values = dict(result.__dict__)
    values.update(changes)
    return WeakModelRunResult(**values)


__all__ = ["WeakModelService", "create_default_weak_model_service"]
