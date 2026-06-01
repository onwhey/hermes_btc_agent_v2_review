from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.core.config import AppSettings
from app.model_review_aggregation.schema import ModelReviewAggregationResult, ModelReviewAggregationStatus
from app.model_review_chain.worker_schema import MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS, build_worker_result
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.strategy.types import StrategyRunStatus, StrategySignalRunResult
from app.strategy.aggregation.evidence_types import (
    CandidateBias,
    DecisionReadiness,
    EvidenceAggregationRunResult,
    EvidenceAggregationStatus,
)
from app.strategy.aggregation.types import StrategyAggregationResult, StrategyAggregationStatus
from app.strategy_advice.scheduler_schema import (
    StrategyAdviceSchedulerResult,
    StrategyAdviceSchedulerStatus,
)
from app.strategy.evidence_quality.types import (
    STRATEGY_EVIDENCE_QUALITY_ERROR_CODE,
    StrategyEvidenceQualityGateResult,
    StrategyEvidenceQualitySeverity,
    StrategyEvidenceQualityStatus,
)
from app.strategy_pipeline.locks import StrategyPipelineLock
from app.strategy_pipeline.service import StrategyPipelineService
from app.strategy_pipeline.types import (
    StrategyPipelineRequest,
    StrategyPipelineStatus,
    format_strategy_pipeline_result_lines,
)


SLOT = datetime(2026, 5, 30, 4, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@dataclass
class FakeEvidenceAggregation:
    aggregation_id: str = "SEA-test"
    status: str = "success"
    candidate_bias: str = "wait"
    decision_readiness: str = "wait_for_confirmation"


@dataclass
class FakeStage17Event:
    event_id: str = "SSS-reused"
    status: str = "success"
    run_id: str | None = "SSR-reused"
    error_code: str | None = None
    target_base_open_time_utc: datetime = SLOT
    created_at_utc: datetime = SLOT


@dataclass
class FakeStrategySignalRun:
    run_id: str
    status: str
    snapshot_id: str | None = "MCS-test"


@dataclass
class FakeMaterialPack:
    material_pack_id: str
    aggregation_run_id: str = "SAR-existing"
    strategy_signal_run_id: str = "SSR-test"
    symbol: str = "BTCUSDT"
    base_interval: str = "4h"
    higher_interval: str = "1d"
    status: str = "success"
    created_at_utc: datetime = SLOT
    id: int = 1


@dataclass
class FakeWeakModelAggregation:
    weak_model_aggregation_id: str = "WMA-test"
    weak_model_run_id: str = "WMR-test"
    strategy_signal_run_id: str = "SSR-test"
    snapshot_id: str = "MCS-test"
    symbol: str = "BTCUSDT"
    base_interval: str = "4h"
    higher_interval: str = "1d"
    kline_slot_utc: datetime = SLOT
    directional_score: float = -0.5
    directional_bias: str = "bearish_bias"
    directional_confidence: float = 0.55
    risk_level: str = "medium"
    trade_permission: str = "allow"


@dataclass
class FakeWeakModelPackage:
    run: Any
    aggregation: Any


class FakeRepository:
    def __init__(
        self,
        order: list[str] | None = None,
        latest_slot: datetime | None = SLOT,
        existing_evidence: FakeEvidenceAggregation | None = None,
        stage17_events: list[FakeStage17Event] | None = None,
        strategy_signal_runs: dict[str, FakeStrategySignalRun] | None = None,
        material_packs: list[FakeMaterialPack] | None = None,
        weak_model_package: FakeWeakModelPackage | None = None,
        weak_model_quality_check: Any | None = None,
    ) -> None:
        self.order = order if order is not None else []
        self.latest_slot = latest_slot
        self.existing_evidence = existing_evidence
        self.stage17_events = stage17_events or []
        self.strategy_signal_runs = strategy_signal_runs or {}
        self.material_packs = material_packs or []
        self.weak_model_package = weak_model_package
        self.weak_model_quality_check = weak_model_quality_check
        self.created_events: list[Any] = []
        self.updated_events: list[Any] = []

    def resolve_latest_base_kline_slot_utc(self, db_session: Any, *, symbol: str, base_interval: str) -> datetime | None:
        return self.latest_slot

    def get_latest_strategy_evidence_aggregation(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
    ) -> FakeEvidenceAggregation | None:
        self.order.append("23f_lookup")
        return self.existing_evidence

    def get_strategy_signal_run_by_run_id(self, db_session: Any, *, run_id: str) -> FakeStrategySignalRun | None:
        self.order.append("strategy_run_lookup")
        return self.strategy_signal_runs.get(run_id)

    def get_latest_success_weak_model_package_for_strategy_run(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
        snapshot_id: str | None,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        kline_slot_utc: datetime,
    ) -> FakeWeakModelPackage | None:
        self.order.append("27a_lookup")
        package = self.weak_model_package
        if package is None:
            return None
        run = package.run
        aggregation = package.aggregation
        if getattr(run, "strategy_signal_run_id", None) != strategy_signal_run_id:
            return None
        if snapshot_id and getattr(run, "snapshot_id", None) != snapshot_id:
            return None
        if getattr(run, "symbol", None) != symbol:
            return None
        if getattr(run, "base_interval", None) != base_interval:
            return None
        if getattr(run, "higher_interval", None) != higher_interval:
            return None
        if getattr(run, "kline_slot_utc", None) != kline_slot_utc:
            return None
        if getattr(run, "run_status", None) != "success":
            return None
        if getattr(aggregation, "weak_model_run_id", None) != getattr(run, "weak_model_run_id", None):
            return None
        return package

    def get_latest_weak_model_quality_check_by_run_id(
        self,
        db_session: Any,
        *,
        weak_model_run_id: str,
    ) -> Any | None:
        self.order.append("27b_lookup")
        quality_check = self.weak_model_quality_check
        if quality_check is None:
            return None
        if getattr(quality_check, "weak_model_run_id", None) != weak_model_run_id:
            return None
        return quality_check

    def get_latest_reusable_material_pack_for_strategy_run(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
        symbol: str,
        base_interval: str,
        higher_interval: str,
    ) -> FakeMaterialPack | None:
        self.order.append("18_material_reuse_lookup")
        reusable = [
            pack
            for pack in self.material_packs
            if pack.strategy_signal_run_id == strategy_signal_run_id
            and pack.symbol == symbol
            and pack.base_interval == base_interval
            and pack.higher_interval == higher_interval
            and pack.status in {"success", "partial_success"}
        ]
        if not reusable:
            return None
        return sorted(reusable, key=lambda pack: (pack.created_at_utc, pack.id), reverse=True)[0]

    def get_latest_material_pack_for_strategy_run(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
        symbol: str,
        base_interval: str,
        higher_interval: str,
    ) -> FakeMaterialPack | None:
        self.order.append("18_material_latest_lookup")
        matches = [
            pack
            for pack in self.material_packs
            if pack.strategy_signal_run_id == strategy_signal_run_id
            and pack.symbol == symbol
            and pack.base_interval == base_interval
            and pack.higher_interval == higher_interval
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda pack: (pack.created_at_utc, pack.id), reverse=True)[0]

    def get_latest_reusable_stage17_scheduler_event(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> FakeStage17Event | None:
        self.order.append("17_reuse_lookup")
        reusable = [
            event
            for event in self.stage17_events
            if event.status in {"success", "partial_success"}
            and bool(event.run_id)
            and event.target_base_open_time_utc == target_base_open_time_utc
        ]
        if not reusable:
            return None
        return sorted(reusable, key=lambda event: event.created_at_utc, reverse=True)[0]

    def get_latest_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> FakeStage17Event | None:
        self.order.append("17_latest_lookup")
        matches = [
            event
            for event in self.stage17_events
            if event.target_base_open_time_utc == target_base_open_time_utc
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda event: event.created_at_utc, reverse=True)[0]

    def get_latest_retryable_failed_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> FakeStage17Event | None:
        self.order.append("17_retryable_lookup")
        matches = [
            event
            for event in self.stage17_events
            if event.target_base_open_time_utc == target_base_open_time_utc
            and event.status in {"failed", "blocked"}
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda event: event.created_at_utc, reverse=True)[0]

    def get_latest_in_progress_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> FakeStage17Event | None:
        self.order.append("17_in_progress_lookup")
        matches = [
            event
            for event in self.stage17_events
            if event.target_base_open_time_utc == target_base_open_time_utc
            and event.status in {"running", "waiting_upstream"}
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda event: event.created_at_utc, reverse=True)[0]

    def create_pipeline_event_log(self, db_session: Any, *, payload: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"payload": payload}
        self.created_events.append(payload)
        return row

    def update_pipeline_event_log(self, db_session: Any, *, row: Any, payload: Any, finished: bool) -> Any:
        self.updated_events.append((payload, finished))
        row["payload"] = payload
        row["finished"] = finished
        return row


class FakeLockManager:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.acquired_keys: list[str] = []
        self.released_keys: list[str] = []

    def acquire_strategy_pipeline_lock(self, *, lock: StrategyPipelineLock) -> StrategyPipelineLock:
        self.acquired_keys.append(lock.key)
        return StrategyPipelineLock(key=lock.key, owner=lock.owner, ttl_seconds=lock.ttl_seconds, acquired=self.acquired)

    def release_strategy_pipeline_lock(self, *, lock: StrategyPipelineLock) -> None:
        self.released_keys.append(lock.key)


class FakeStage17:
    def __init__(
        self,
        order: list[str],
        *,
        status: StrategySignalSchedulerStatus = StrategySignalSchedulerStatus.SUCCESS,
        run_id: str | None = "SSR-test",
        event_id: str = "SSS-test",
        message: str = "stage17 ok",
    ) -> None:
        self.order = order
        self.status = status
        self.run_id = run_id
        self.event_id = event_id
        self.message = message
        self.calls = 0

    def run_after_collector_success(self, db_session: Any, *, request: Any) -> StrategySignalSchedulerResult:
        self.calls += 1
        self.order.append("17")
        return StrategySignalSchedulerResult(
            status=self.status,
            event_id=self.event_id,
            trace_id=request.trace_id,
            message=self.message,
            target_base_open_time_ms=request.upstream_latest_base_open_time_ms,
            run_id=self.run_id,
            snapshot_id="MCS-test" if self.run_id else None,
            strategy_count=4 if self.run_id else 0,
            success_count=4 if self.run_id else 0,
        )


class FakeStage16:
    def __init__(
        self,
        order: list[str],
        *,
        status: StrategyRunStatus = StrategyRunStatus.SUCCESS,
        run_id: str = "SSR-retry",
        message: str = "stage16 retry ok",
        blocked_reason: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.order = order
        self.status = status
        self.run_id = run_id
        self.message = message
        self.blocked_reason = blocked_reason
        self.error_message = error_message
        self.calls = 0
        self.requests: list[Any] = []

    def run_strategy_signals(self, db_session: Any, *, request: Any) -> StrategySignalRunResult:
        self.calls += 1
        self.requests.append(request)
        self.order.append("16_retry")
        success = self.status in {StrategyRunStatus.SUCCESS, StrategyRunStatus.PARTIAL_SUCCESS}
        return StrategySignalRunResult(
            status=self.status,
            exit_code=0 if success else 4,
            run_id=self.run_id if success else "",
            trace_id=request.trace_id,
            snapshot_id="MCS-retry" if success else None,
            message=self.message,
            blocked_reason=self.blocked_reason,
            error_message=self.error_message,
            strategy_count=4 if success else 0,
            success_count=4 if success else 0,
        )


class FakeStage18:
    def __init__(
        self,
        order: list[str],
        *,
        status: StrategyAggregationStatus = StrategyAggregationStatus.SUCCESS,
        material_pack_id: str | None = "AMP-test",
        aggregation_run_id: str = "SAR-test",
        message: str = "stage18 ok",
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.order = order
        self.status = status
        self.material_pack_id = material_pack_id
        self.aggregation_run_id = aggregation_run_id
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.calls = 0

    def run_strategy_aggregation(self, db_session: Any, *, request: Any) -> StrategyAggregationResult:
        self.calls += 1
        self.order.append("18")
        success = self.status in {StrategyAggregationStatus.SUCCESS, StrategyAggregationStatus.PARTIAL_SUCCESS}
        return StrategyAggregationResult(
            status=self.status,
            exit_code=0 if success else 4,
            aggregation_run_id=self.aggregation_run_id,
            material_pack_id=self.material_pack_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=request.trace_id,
            message=self.message,
            error_code=self.error_code,
            details=self.details,
        )


class FakeStage23F:
    def __init__(
        self,
        order: list[str],
        *,
        status: EvidenceAggregationStatus = EvidenceAggregationStatus.SUCCESS,
    ) -> None:
        self.order = order
        self.status = status
        self.requests: list[Any] = []

    def run_strategy_evidence_aggregation(self, db_session: Any, *, request: Any) -> EvidenceAggregationRunResult:
        self.order.append("23f_create")
        self.requests.append(request)
        return EvidenceAggregationRunResult(
            status=self.status,
            exit_code=0,
            aggregation_id="SEA-created",
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=request.trace_id,
            database_written=self.status
            not in {EvidenceAggregationStatus.BLOCKED, EvidenceAggregationStatus.FAILED},
            database_action="created",
            candidate_bias=CandidateBias.WAIT,
            decision_readiness=DecisionReadiness.WAIT_FOR_CONFIRMATION,
            message="stage23f ok",
        )


class FakeStage26B:
    def __init__(
        self,
        order: list[str],
        *,
        status: StrategyEvidenceQualityStatus = StrategyEvidenceQualityStatus.PASSED,
        should_block_pipeline: bool = False,
        alert_status: str = "not_required",
        alert_error_message: str | None = None,
    ) -> None:
        self.order = order
        self.status = status
        self.should_block_pipeline = should_block_pipeline
        self.alert_status = alert_status
        self.alert_error_message = alert_error_message
        self.requests: list[Any] = []

    def run_strategy_evidence_quality_gate(self, db_session: Any, *, request: Any) -> StrategyEvidenceQualityGateResult:
        self.order.append("26b")
        self.requests.append(request)
        return StrategyEvidenceQualityGateResult(
            status=self.status,
            quality_check_id="EQC-test",
            pipeline_run_id=request.pipeline_run_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            strategy_evidence_aggregation_id=request.strategy_evidence_aggregation_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=request.kline_slot_utc,
            should_block_pipeline=self.should_block_pipeline,
            severity=(
                StrategyEvidenceQualitySeverity.CRITICAL
                if self.should_block_pipeline
                else StrategyEvidenceQualitySeverity.INFO
            ),
            error_code=STRATEGY_EVIDENCE_QUALITY_ERROR_CODE if self.should_block_pipeline else None,
            error_message="策略证据质量重大异常，已阻断 18 材料包。" if self.should_block_pipeline else None,
            alert_required=self.should_block_pipeline,
            alert_status=self.alert_status,
            alert_error_message=self.alert_error_message,
            database_written=True,
            database_action="created",
            trace_id=request.trace_id,
        )


class FakeStage27A:
    def __init__(
        self,
        order: list[str],
        *,
        status: str = "success",
        weak_model_run_id: str = "WMR-test",
        aggregation: FakeWeakModelAggregation | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.order = order
        self.status = status
        self.weak_model_run_id = weak_model_run_id
        self.aggregation = aggregation if aggregation is not None else FakeWeakModelAggregation(
            weak_model_run_id=weak_model_run_id
        )
        self.error_code = error_code
        self.error_message = error_message
        self.requests: list[Any] = []

    def run_weak_models_for_strategy_signal(self, db_session: Any, request: Any) -> Any:
        self.order.append("27a")
        self.requests.append(request)
        aggregation = self.aggregation if self.status == "success" else None
        return SimpleNamespace(
            status=self.status,
            weak_model_run_id=self.weak_model_run_id,
            weak_model_aggregation_id=getattr(aggregation, "weak_model_aggregation_id", None),
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id="MCS-test",
            aggregation=aggregation,
            database_action="run_created;aggregation_created" if self.status == "success" else "not_written",
            error_code=self.error_code,
            error_message=self.error_message,
            details={},
        )


class FakeStage27B:
    def __init__(
        self,
        order: list[str],
        *,
        status: str = "passed",
        quality_check_id: str = "WMQC-WMR-test",
        weak_model_run_id: str = "WMR-test",
        weak_model_aggregation_id: str = "WMA-test",
        raise_error: Exception | None = None,
        empty_report: bool = False,
    ) -> None:
        self.order = order
        self.status = status
        self.quality_check_id = quality_check_id
        self.weak_model_run_id = weak_model_run_id
        self.weak_model_aggregation_id = weak_model_aggregation_id
        self.raise_error = raise_error
        self.empty_report = empty_report
        self.requests: list[Any] = []

    def check_weak_model_output_quality(self, db_session: Any, *, request: Any) -> Any:
        self.order.append("27b")
        self.requests.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.empty_report:
            return SimpleNamespace(results=())
        return SimpleNamespace(
            results=(
                SimpleNamespace(
                    status=self.status,
                    quality_check_id=self.quality_check_id,
                    weak_model_run_id=self.weak_model_run_id,
                    weak_model_aggregation_id=self.weak_model_aggregation_id,
                    database_action="created",
                    error_code=None,
                    error_message=None,
                    details={},
                ),
            )
        )


def reusable_weak_model_package(
    *,
    weak_model_run_id: str = "WMR-reused",
    weak_model_aggregation_id: str = "WMA-reused",
    directional_score: float = -0.4,
    risk_level: str = "low",
    trade_permission: str = "allow",
) -> FakeWeakModelPackage:
    aggregation = FakeWeakModelAggregation(
        weak_model_aggregation_id=weak_model_aggregation_id,
        weak_model_run_id=weak_model_run_id,
        directional_score=directional_score,
        risk_level=risk_level,
        trade_permission=trade_permission,
    )
    run = SimpleNamespace(
        weak_model_run_id=weak_model_run_id,
        strategy_signal_run_id="SSR-test",
        snapshot_id="MCS-test",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=SLOT,
        run_status="success",
    )
    return FakeWeakModelPackage(run=run, aggregation=aggregation)


def reusable_quality_check(
    *,
    quality_check_id: str = "WMQC-reused",
    weak_model_run_id: str = "WMR-reused",
    weak_model_aggregation_id: str = "WMA-reused",
    status: str = "passed",
) -> Any:
    return SimpleNamespace(
        quality_check_id=quality_check_id,
        weak_model_run_id=weak_model_run_id,
        weak_model_aggregation_id=weak_model_aggregation_id,
        status=status,
    )


class FakeStage20Worker:
    def __init__(self, order: list[str], *, result_kwargs: dict[str, Any] | None = None) -> None:
        self.order = order
        self.result_kwargs = result_kwargs or {}
        self.requests: list[Any] = []

    def run_model_review_chain_worker(self, db_session: Any, *, request: Any) -> Any:
        self.order.append("20c")
        self.requests.append(request)
        values = {
            "status": MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS,
            "trace_id": request.trace_id,
            "material_pack_id": request.material_pack_id,
            "model_review_reused": True,
            "reused_model_analysis_run_id": "MAR-test",
            "summary_text": "stage20c ok",
        }
        values.update(self.result_kwargs)
        return build_worker_result(
            **values,
        )


class FakeStage20A:
    def __init__(self, order: list[str], *, model_review_reused: bool = True) -> None:
        self.order = order
        self.model_review_reused = model_review_reused

    def run_model_review_aggregation(self, db_session: Any, *, request: Any) -> ModelReviewAggregationResult:
        self.order.append("20a")
        return ModelReviewAggregationResult(
            status=ModelReviewAggregationStatus.SUCCESS,
            exit_code=0,
            review_aggregation_run_id="MRAG-test",
            material_pack_id=request.material_pack_id,
            aggregation_run_id="SAR-test",
            strategy_signal_run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id=request.trace_id,
            model_review_reused=self.model_review_reused,
            reused_model_analysis_run_id="MAR-test",
            summary_text="stage20a ok",
        )


class FakeStage21:
    def __init__(self, order: list[str], *, send_real_alert: bool = False) -> None:
        self.order = order
        self.send_real_alert = send_real_alert

    def run_strategy_advice_scheduler(self, db_session: Any, *, request: Any) -> StrategyAdviceSchedulerResult:
        self.order.append("21c")
        return StrategyAdviceSchedulerResult(
            status=StrategyAdviceSchedulerStatus.SUCCESS,
            exit_code=0,
            trace_id=request.trace_id,
            trigger_source=request.trigger_source,
            review_aggregation_run_id=request.review_aggregation_run_id,
            lifecycle_review_id="ADVR-test",
            notification_attempted=True,
            notification_status="skipped",
            send_real_alert=self.send_real_alert,
            dry_run=False,
            details={"stage21a_result": {"advice_id": "ADV-test"}},
            summary_text="stage21 ok",
        )


def test_dry_run_does_not_write_database_or_acquire_lock() -> None:
    repository = FakeRepository()
    lock_manager = FakeLockManager()
    service = StrategyPipelineService(
        settings=AppSettings(strategy_pipeline_enabled=True),
        repository=repository,
        lock_manager=lock_manager,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=True, confirm_write=False),
    )

    assert result.status == StrategyPipelineStatus.DRY_RUN
    assert result.kline_slot_source == "cli_argument"
    assert repository.created_events == []
    assert lock_manager.acquired_keys == []


def test_confirm_write_calls_existing_stage_services_in_pipeline_order() -> None:
    order: list[str] = []
    service = _build_full_success_service(order=order)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert order == [
        "17",
        "23f_lookup",
        "23f_create",
        "26b",
        "27a_lookup",
        "27a",
        "27b_lookup",
        "27b",
        "18",
        "20c",
        "20a",
        "21c",
    ]
    assert result.strategy_signal_run_id == "SSR-test"
    assert result.strategy_evidence_aggregation_id == "SEA-created"
    assert result.weak_model_run_id == "WMR-test"
    assert result.weak_model_aggregation_id == "WMA-test"
    assert result.weak_model_quality_check_id == "WMQC-WMR-test"
    assert result.weak_model_status == "success"
    assert result.weak_model_quality_status == "passed"
    assert result.weak_model_directional_score == -0.5
    assert result.weak_model_risk_level == "medium"
    assert result.weak_model_trade_permission == "allow"
    assert result.weak_model_pipeline_action == "created"
    assert result.weak_model_quality_pipeline_action == "created"
    assert result.material_pack_id == "AMP-test"
    assert result.model_analysis_run_id == "MAR-test"
    assert result.review_aggregation_run_id == "MRAG-test"
    assert result.advice_id == "ADV-test"
    assert result.review_id == "ADVR-test"


def test_scheduler_trigger_source_uses_same_27a_27b_before_stage18_order() -> None:
    order: list[str] = []
    service = _build_full_success_service(order=order)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            trigger_source="scheduler",
            dry_run=False,
            confirm_write=True,
            created_by="scheduler_strategy_pipeline",
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert order.index("27a") < order.index("27b") < order.index("18")
    assert result.weak_model_pipeline_action == "created"
    assert result.weak_model_quality_pipeline_action == "created"


def test_pipeline_reuses_existing_success_material_pack_after_stage18_already_exists() -> None:
    order: list[str] = []
    stage18 = FakeStage18(
        order,
        status=StrategyAggregationStatus.SKIPPED,
        material_pack_id=None,
        aggregation_run_id="SAR-existing",
        message="Stage-18 aggregation skipped: already_exists existing status=success.",
        error_code="skipped",
        details={"skip_reason": "already_exists"},
    )
    worker = FakeStage20Worker(order)
    repository = FakeRepository(
        order=order,
        material_packs=[
            FakeMaterialPack(
                material_pack_id="AMP-existing-success",
                aggregation_run_id="SAR-existing",
                status="success",
            )
        ],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage18_service=stage18,
        stage20_worker=worker,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.material_pack_id == "AMP-existing-success"
    assert worker.requests[0].material_pack_id == "AMP-existing-success"
    assert stage18.calls == 1
    assert order == [
        "17",
        "23f_lookup",
        "23f_create",
        "26b",
        "27a_lookup",
        "27a",
        "27b_lookup",
        "27b",
        "18",
        "18_material_reuse_lookup",
        "20c",
        "20a",
        "21c",
    ]
    assert result.details["stage18_reused_existing_material_pack"] is True
    assert result.details["stage18_reused_material_pack_id"] == "AMP-existing-success"
    assert result.details["stage18_reused_aggregation_run_id"] == "SAR-existing"
    assert result.details["stage18_reused_material_pack_status"] == "success"
    assert "material_pack_id=AMP-existing-success" in format_strategy_pipeline_result_lines(result)


def test_pipeline_reuses_existing_partial_success_material_pack_after_stage18_already_exists() -> None:
    order: list[str] = []
    stage18 = FakeStage18(
        order,
        status=StrategyAggregationStatus.SKIPPED,
        material_pack_id=None,
        aggregation_run_id="SAR-existing-partial",
        message="Stage-18 aggregation skipped: already_exists existing status=partial_success.",
        error_code="skipped",
        details={"skip_reason": "already_exists"},
    )
    worker = FakeStage20Worker(order)
    repository = FakeRepository(
        order=order,
        material_packs=[
            FakeMaterialPack(
                material_pack_id="AMP-existing-partial",
                aggregation_run_id="SAR-existing-partial",
                status="partial_success",
            )
        ],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage18_service=stage18,
        stage20_worker=worker,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.material_pack_id == "AMP-existing-partial"
    assert worker.requests[0].material_pack_id == "AMP-existing-partial"
    assert result.details["stage18_reused_material_pack_status"] == "partial_success"


def test_pipeline_blocks_stage18_already_exists_when_existing_material_pack_failed() -> None:
    order: list[str] = []
    stage18 = FakeStage18(
        order,
        status=StrategyAggregationStatus.SKIPPED,
        material_pack_id=None,
        message="Stage-18 aggregation skipped: already_exists existing status=failed.",
        error_code="skipped",
        details={"skip_reason": "already_exists"},
    )
    repository = FakeRepository(
        order=order,
        material_packs=[FakeMaterialPack(material_pack_id="AMP-failed", status="failed")],
    )
    service = _build_full_success_service(order=order, repository=repository, stage18_service=stage18)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "18_material_pack"
    assert result.error_code == "stage18_existing_material_pack_not_reusable"
    assert result.material_pack_id is None
    assert result.details["stage18_existing_material_pack_status"] == "failed"
    assert "20c" not in order


def test_pipeline_blocks_stage18_already_exists_when_reusable_material_pack_not_found() -> None:
    order: list[str] = []
    stage18 = FakeStage18(
        order,
        status=StrategyAggregationStatus.SKIPPED,
        material_pack_id=None,
        message="Stage-18 aggregation skipped: already_exists existing status=partial_success.",
        error_code="skipped",
        details={"skip_reason": "already_exists"},
    )
    service = _build_full_success_service(
        order=order,
        repository=FakeRepository(order=order),
        stage18_service=stage18,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "18_material_pack"
    assert result.error_code == "stage18_existing_material_pack_not_found"
    assert result.material_pack_id is None
    assert "18_material_reuse_lookup" in order
    assert "18_material_latest_lookup" in order
    assert "20c" not in order


def test_pipeline_reuses_successful_stage17_event_after_duplicate_skip() -> None:
    order: list[str] = []
    stage17 = FakeStage17(
        order,
        status=StrategySignalSchedulerStatus.SKIPPED,
        run_id=None,
        message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
    )
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-old", status="success", run_id="SSR-old")],
    )
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage17_service=stage17,
        stage23f_service=stage23f,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_signal_run_id == "SSR-old"
    assert stage17.calls == 1
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-old"
    assert order[:4] == ["17", "17_reuse_lookup", "23f_lookup", "23f_create"]
    assert result.details["reused_stage17_duplicate"] is True
    assert result.details["reused_strategy_signal_run_id"] == "SSR-old"
    assert result.details["reused_stage17_event_id"] == "SSS-old"
    final_payload, finished = repository.updated_events[-1]
    assert finished is True
    assert final_payload.details["reused_stage17_duplicate"] is True


def test_pipeline_blocks_duplicate_skip_when_only_failed_stage17_events_exist() -> None:
    order: list[str] = []
    stage17 = FakeStage17(
        order,
        status=StrategySignalSchedulerStatus.SKIPPED,
        run_id=None,
        message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
    )
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-failed", status="failed", run_id=None)],
    )
    service = _build_full_success_service(order=order, repository=repository, stage17_service=stage17)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "stage17_duplicate_reusable_run_not_found"
    assert result.strategy_signal_run_id is None
    assert "23f_lookup" not in order
    assert "18" not in order


def test_pipeline_retry_flag_still_reuses_existing_success_stage17_event() -> None:
    order: list[str] = []
    stage17 = FakeStage17(
        order,
        status=StrategySignalSchedulerStatus.SKIPPED,
        run_id=None,
        message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
    )
    stage16 = FakeStage16(order)
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-old", status="success", run_id="SSR-old")],
    )
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=stage17,
        stage23f_service=stage23f,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_signal_run_id == "SSR-old"
    assert stage17.calls == 0
    assert stage16.calls == 0
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-old"
    assert order[:3] == ["17_reuse_lookup", "23f_lookup", "23f_create"]
    assert result.details["reused_stage17_duplicate"] is True
    assert result.details.get("retry_failed_stage17") is not True


def test_pipeline_retry_failed_stage17_reruns_stage16_and_continues_to_23f() -> None:
    order: list[str] = []
    stage17 = FakeStage17(
        order,
        status=StrategySignalSchedulerStatus.SKIPPED,
        run_id=None,
        message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
    )
    stage16 = FakeStage16(order, run_id="SSR-retry")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-blocked",
                status="blocked",
                run_id=None,
                error_code="strategy_registry_config_error",
                created_at_utc=SLOT,
            ),
            FakeStage17Event(
                event_id="SSS-skipped-latest",
                status="skipped",
                run_id=None,
                created_at_utc=SLOT + timedelta(minutes=1),
            )
        ],
    )
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=stage17,
        stage23f_service=stage23f,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_signal_run_id == "SSR-retry"
    assert result.strategy_evidence_aggregation_id == "SEA-created"
    assert stage17.calls == 1
    assert stage16.calls == 1
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-retry"
    assert order[:7] == [
        "17_reuse_lookup",
        "17",
        "17_reuse_lookup",
        "17_in_progress_lookup",
        "17_retryable_lookup",
        "16_retry",
        "23f_lookup",
    ]
    assert result.details["retry_failed_stage17"] is True
    assert result.details["previous_stage17_event_id"] == "SSS-blocked"
    assert result.details["previous_stage17_status"] == "blocked"
    assert result.details["previous_stage17_run_id"] is None
    assert result.details["previous_strategy_signal_run_status"] == ""
    assert result.details["previous_stage17_error_code"] == "strategy_registry_config_error"
    assert result.details["new_strategy_signal_run_id"] == "SSR-retry"
    final_payload, finished = repository.updated_events[-1]
    assert finished is True
    assert final_payload.details["retry_failed_stage17"] is True
    assert final_payload.details["previous_stage17_event_id"] == "SSS-blocked"


def test_pipeline_retry_failed_stage17_with_blocked_strategy_run_allows_retry() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order, run_id="SSR-retry-after-blocked-run")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-blocked-with-run",
                status="blocked",
                run_id="SSR-old-blocked",
                error_code="strategy_config_invalid",
                created_at_utc=SLOT,
            )
        ],
        strategy_signal_runs={"SSR-old-blocked": FakeStrategySignalRun(run_id="SSR-old-blocked", status="blocked")},
    )
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
        stage23f_service=stage23f,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_signal_run_id == "SSR-retry-after-blocked-run"
    assert stage16.calls == 1
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-retry-after-blocked-run"
    assert "strategy_run_lookup" in order
    assert result.details["retry_failed_stage17"] is True
    assert result.details["previous_stage17_event_id"] == "SSS-blocked-with-run"
    assert result.details["previous_stage17_run_id"] == "SSR-old-blocked"
    assert result.details["previous_strategy_signal_run_status"] == "blocked"
    assert result.details["previous_stage17_error_code"] == "strategy_config_invalid"


def test_pipeline_retry_failed_stage17_with_failed_strategy_run_allows_retry() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order, run_id="SSR-retry-after-failed-run")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-failed-with-run",
                status="failed",
                run_id="SSR-old-failed",
                created_at_utc=SLOT,
            )
        ],
        strategy_signal_runs={"SSR-old-failed": FakeStrategySignalRun(run_id="SSR-old-failed", status="failed")},
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert stage16.calls == 1
    assert result.details["previous_stage17_run_id"] == "SSR-old-failed"
    assert result.details["previous_strategy_signal_run_status"] == "failed"


def test_pipeline_retry_failed_stage17_with_invalid_strategy_run_allows_retry() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order, run_id="SSR-retry-after-invalid-run")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-blocked-invalid-run",
                status="blocked",
                run_id="SSR-old-invalid",
                created_at_utc=SLOT,
            )
        ],
        strategy_signal_runs={"SSR-old-invalid": FakeStrategySignalRun(run_id="SSR-old-invalid", status="invalid")},
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert stage16.calls == 1
    assert result.details["previous_stage17_run_id"] == "SSR-old-invalid"
    assert result.details["previous_strategy_signal_run_status"] == "invalid"


def test_pipeline_retry_failed_stage17_with_missing_strategy_run_allows_retry_and_records_missing() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order, run_id="SSR-retry-after-missing-run")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-blocked-missing-run",
                status="blocked",
                run_id="SSR-missing",
                created_at_utc=SLOT,
            )
        ],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert stage16.calls == 1
    assert result.details["previous_stage17_run_id"] == "SSR-missing"
    assert result.details["previous_strategy_signal_run_status"] == ""
    assert result.details["previous_stage17_run_missing"] is True


def test_pipeline_retry_failed_stage17_blocks_when_previous_run_is_successful() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order)
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-blocked-success-run",
                status="blocked",
                run_id="SSR-old-success",
                created_at_utc=SLOT,
            )
        ],
        strategy_signal_runs={"SSR-old-success": FakeStrategySignalRun(run_id="SSR-old-success", status="success")},
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "stage17_retry_existing_run_success"
    assert stage16.calls == 0
    assert "23f_lookup" not in order
    assert result.details["previous_stage17_run_id"] == "SSR-old-success"
    assert result.details["previous_strategy_signal_run_status"] == "success"


def test_pipeline_retry_failed_stage17_uses_old_failed_when_latest_event_is_skipped_duplicate() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order, run_id="SSR-retry-failed-old")
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(
                event_id="SSS-failed-old",
                status="failed",
                run_id=None,
                error_code="stage17_config_failed",
                created_at_utc=SLOT,
            ),
            FakeStage17Event(
                event_id="SSS-skipped-new",
                status="skipped",
                run_id=None,
                created_at_utc=SLOT + timedelta(minutes=2),
            ),
        ],
    )
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
        stage23f_service=stage23f,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_signal_run_id == "SSR-retry-failed-old"
    assert stage16.calls == 1
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-retry-failed-old"
    assert result.details["retry_failed_stage17"] is True
    assert result.details["previous_stage17_event_id"] == "SSS-failed-old"
    assert result.details["previous_stage17_status"] == "failed"


def test_pipeline_retry_failed_stage17_failure_stops_before_23f() -> None:
    order: list[str] = []
    stage16 = FakeStage16(
        order,
        status=StrategyRunStatus.FAILED,
        message="retry failed",
        error_message="strategy retry error",
    )
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-failed", status="failed", run_id=None)],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.FAILED
    assert result.error_code == "failed"
    assert result.error_message == "strategy retry error"
    assert "17" in order
    assert stage16.calls == 1
    assert "17_retryable_lookup" in order
    assert "23f_lookup" not in order
    assert "18" not in order
    assert result.details["retry_failed_stage17"] is True
    assert result.details["previous_stage17_event_id"] == "SSS-failed"
    final_payload, finished = repository.updated_events[-1]
    assert finished is True
    assert final_payload.details["retry_failed_stage17"] is True


def test_pipeline_retry_failed_stage17_blocks_when_only_skipped_duplicate_exists() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order)
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-skipped-only", status="skipped", run_id=None)],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "stage17_duplicate_reusable_run_not_found"
    assert stage16.calls == 0
    assert "17_retryable_lookup" in order
    assert "23f_lookup" not in order
    assert result.details.get("retry_failed_stage17") is not True


def test_pipeline_retry_failed_stage17_blocks_when_stage17_event_is_in_progress() -> None:
    order: list[str] = []
    stage16 = FakeStage16(order)
    repository = FakeRepository(
        order=order,
        stage17_events=[
            FakeStage17Event(event_id="SSS-failed-old", status="failed", run_id=None, created_at_utc=SLOT),
            FakeStage17Event(
                event_id="SSS-running-new",
                status="running",
                run_id=None,
                created_at_utc=SLOT + timedelta(minutes=3),
            ),
        ],
    )
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage16_service=stage16,
        stage17_service=FakeStage17(
            order,
            status=StrategySignalSchedulerStatus.SKIPPED,
            run_id=None,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            retry_failed_stage17=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "stage17_event_in_progress"
    assert stage16.calls == 0
    assert "17_retryable_lookup" not in order
    assert "23f_lookup" not in order
    assert result.details["stage17_retry_blocked_by_in_progress"] is True
    assert result.details["stage17_in_progress_event_id"] == "SSS-running-new"


def test_pipeline_blocks_duplicate_skip_when_success_event_has_empty_run_id() -> None:
    order: list[str] = []
    stage17 = FakeStage17(
        order,
        status=StrategySignalSchedulerStatus.SKIPPED,
        run_id=None,
        message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
    )
    repository = FakeRepository(
        order=order,
        stage17_events=[FakeStage17Event(event_id="SSS-empty", status="success", run_id=None)],
    )
    service = _build_full_success_service(order=order, repository=repository, stage17_service=stage17)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "stage17_duplicate_reusable_run_not_found"
    assert result.strategy_signal_run_id is None
    assert "23f_lookup" not in order


def test_pipeline_blocks_when_kline_slot_cannot_be_determined() -> None:
    order: list[str] = []
    service = _build_full_success_service(order=order, repository=FakeRepository(order=order, latest_slot=None))

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=None, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "kline_slot_not_found"
    assert order == []


def test_pipeline_skips_when_redis_lock_is_already_held() -> None:
    order: list[str] = []
    lock_manager = FakeLockManager(acquired=False)
    service = _build_full_success_service(order=order, lock_manager=lock_manager)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SKIPPED
    assert result.error_code == "pipeline_lock_already_held"
    assert result.lock_key == "strategy_pipeline:BTCUSDT:4h:1d:2026-05-30T04:00:00Z"
    assert order == []


def test_pipeline_does_not_duplicate_real_model_or_hermes_without_pipeline_gates() -> None:
    order: list[str] = []
    worker = FakeStage20Worker(order)
    service = _build_full_success_service(
        order=order,
        stage20_worker=worker,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_evidence_aggregation_enabled=True,
            model_review_real_model_enabled=True,
            strategy_pipeline_real_model_enabled=False,
            strategy_advice_notification_send_enabled=True,
            strategy_pipeline_notification_send_enabled=False,
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(
            kline_slot_utc=SLOT,
            dry_run=False,
            confirm_write=True,
            use_real_model=True,
            confirm_real_model_cost=True,
            send_real_hermes=True,
        ),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert worker.requests[0].confirm_real_model_cost is False
    assert result.real_model_called is False
    assert result.hermes_real_sent is False


def test_pipeline_real_model_called_false_for_mock_review() -> None:
    order: list[str] = []
    worker = FakeStage20Worker(
        order,
        result_kwargs={
            "model_review_invoked": True,
            "model_review_invocation_mode": "worker_real_model",
            "model_review_reused": False,
            "invoked_model_keys_json": ("mock_review",),
        },
    )
    service = _build_full_success_service(
        order=order,
        stage20_worker=worker,
        stage20a_service=FakeStage20A(order, model_review_reused=False),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.model_review_invoked is True
    assert result.model_review_reused is False
    assert result.real_model_called is False


def test_pipeline_real_model_called_false_when_review_is_reused() -> None:
    service = _build_full_success_service(order=[])

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.model_review_reused is True
    assert result.real_model_called is False


def test_pipeline_real_model_called_true_only_for_new_real_provider_call() -> None:
    order: list[str] = []
    worker = FakeStage20Worker(
        order,
        result_kwargs={
            "model_review_invoked": True,
            "model_review_invocation_mode": "worker_real_model",
            "model_review_reused": False,
            "invoked_model_keys_json": ("deepseek_primary_review",),
        },
    )
    service = _build_full_success_service(
        order=order,
        stage20_worker=worker,
        stage20a_service=FakeStage20A(order, model_review_reused=False),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.model_review_invoked is True
    assert result.model_review_reused is False
    assert result.real_model_called is True


def test_pipeline_reuses_existing_23f_without_creating_duplicate() -> None:
    order: list[str] = []
    repository = FakeRepository(order=order, existing_evidence=FakeEvidenceAggregation())
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(order=order, repository=repository, stage23f_service=stage23f)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_evidence_aggregation_id == "SEA-test"
    assert order == [
        "17",
        "23f_lookup",
        "26b",
        "27a_lookup",
        "27a",
        "27b_lookup",
        "27b",
        "18",
        "20c",
        "20a",
        "21c",
    ]
    assert stage23f.requests == []


def test_pipeline_creates_23f_when_missing_after_stage16() -> None:
    order: list[str] = []
    stage23f = FakeStage23F(order)
    service = _build_full_success_service(order=order, stage23f_service=stage23f)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.strategy_evidence_aggregation_id == "SEA-created"
    assert stage23f.requests[0].strategy_signal_run_id == "SSR-test"
    assert order[:3] == ["17", "23f_lookup", "23f_create"]


def test_pipeline_blocks_when_23f_is_disabled_and_does_not_run_18() -> None:
    order: list[str] = []
    service = _build_full_success_service(
        order=order,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_evidence_aggregation_enabled=False,
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.error_code == "strategy_evidence_aggregation_disabled"
    assert "18" not in order


def test_pipeline_blocks_after_26b_and_does_not_call_18_20_21() -> None:
    order: list[str] = []
    repository = FakeRepository(order=order)
    stage26b = FakeStage26B(
        order,
        status=StrategyEvidenceQualityStatus.FAILED,
        should_block_pipeline=True,
        alert_status="submitted_to_hermes",
    )
    service = _build_full_success_service(order=order, repository=repository, stage26b_service=stage26b)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "26b_strategy_evidence_quality_gate"
    assert result.error_code == STRATEGY_EVIDENCE_QUALITY_ERROR_CODE
    assert "18" not in order
    assert "20c" not in order
    assert "21c" not in order
    assert result.details["stage26b_result"]["quality_check_id"] == "EQC-test"
    assert result.details["stage26b_result"]["alert_status"] == "submitted_to_hermes"
    final_payload, finished = repository.updated_events[-1]
    assert finished is True
    assert final_payload.current_step == "26b_strategy_evidence_quality_gate"
    assert final_payload.error_code == STRATEGY_EVIDENCE_QUALITY_ERROR_CODE


def test_pipeline_records_26b_alert_failure_without_entering_stage18() -> None:
    order: list[str] = []
    repository = FakeRepository(order=order)
    stage26b = FakeStage26B(
        order,
        status=StrategyEvidenceQualityStatus.FAILED,
        should_block_pipeline=True,
        alert_status="submit_failed",
        alert_error_message="Hermes submit failed",
    )
    service = _build_full_success_service(order=order, repository=repository, stage26b_service=stage26b)

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert "18" not in order
    assert result.details["stage26b_result"]["alert_status"] == "submit_failed"
    assert result.details["stage26b_result"]["alert_error_message"] == "Hermes submit failed"
    final_payload, finished = repository.updated_events[-1]
    assert finished is True
    assert final_payload.details["stage26b_result"]["alert_status"] == "submit_failed"


def test_pipeline_reuses_existing_27a_and_27b_without_duplicate_generation() -> None:
    order: list[str] = []
    repository = FakeRepository(
        order=order,
        weak_model_package=reusable_weak_model_package(),
        weak_model_quality_check=reusable_quality_check(),
    )
    stage27a = FakeStage27A(order)
    stage27b = FakeStage27B(order)
    service = _build_full_success_service(
        order=order,
        repository=repository,
        stage27a_service=stage27a,
        stage27b_service=stage27b,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.weak_model_run_id == "WMR-reused"
    assert result.weak_model_aggregation_id == "WMA-reused"
    assert result.weak_model_quality_check_id == "WMQC-reused"
    assert result.weak_model_pipeline_action == "reused"
    assert result.weak_model_quality_pipeline_action == "reused"
    assert stage27a.requests == []
    assert stage27b.requests == []
    assert "27a" not in order
    assert "27b" not in order
    assert "18" in order


def test_pipeline_warning_quality_allows_stage18_and_records_warning_material_expectation() -> None:
    order: list[str] = []
    stage18 = FakeStage18(order, details={"weak_model_summary": {"status": "warning", "quality_status": "warning"}})
    service = _build_full_success_service(
        order=order,
        stage27b_service=FakeStage27B(order, status="warning"),
        stage18_service=stage18,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert result.weak_model_quality_status == "warning"
    assert stage18.calls == 1
    assert order.index("27b") < order.index("18")


def test_pipeline_blocks_critical_27b_before_stage18() -> None:
    order: list[str] = []
    stage18 = FakeStage18(order)
    service = _build_full_success_service(
        order=order,
        stage27b_service=FakeStage27B(order, status="critical"),
        stage18_service=stage18,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "27b_weak_model_quality_check"
    assert result.error_code == "weak_model_quality_critical"
    assert stage18.calls == 0
    assert "18" not in order


def test_pipeline_blocks_27a_failure_before_stage18() -> None:
    order: list[str] = []
    stage18 = FakeStage18(order)
    service = _build_full_success_service(
        order=order,
        stage27a_service=FakeStage27A(order, status="failed", error_code="weak_model_config_invalid"),
        stage18_service=stage18,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "27a_weak_model_run"
    assert result.error_code == "weak_model_config_invalid"
    assert result.weak_model_pipeline_action == "blocked"
    assert stage18.calls == 0
    assert "27b" not in order
    assert "18" not in order


def test_pipeline_blocks_27b_execution_failure_before_stage18() -> None:
    order: list[str] = []
    stage18 = FakeStage18(order)
    service = _build_full_success_service(
        order=order,
        stage27b_service=FakeStage27B(order, raise_error=RuntimeError("27B db error")),
        stage18_service=stage18,
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "27b_weak_model_quality_check"
    assert result.error_code == "weak_model_quality_check_failed"
    assert result.error_message == "27B db error"
    assert stage18.calls == 0
    assert "18" not in order


def test_pipeline_blocks_when_weak_model_switch_is_disabled() -> None:
    order: list[str] = []
    service = _build_full_success_service(
        order=order,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_evidence_aggregation_enabled=True,
            strategy_pipeline_weak_models_enabled=False,
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "27a_weak_model_run"
    assert result.error_code == "weak_model_disabled_by_config"
    assert result.weak_model_pipeline_action == "blocked"
    assert "18" not in order


def test_pipeline_blocks_when_weak_model_quality_gate_switch_is_disabled() -> None:
    order: list[str] = []
    service = _build_full_success_service(
        order=order,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_evidence_aggregation_enabled=True,
            strategy_pipeline_weak_model_quality_gate_enabled=False,
        ),
    )

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.status == StrategyPipelineStatus.BLOCKED
    assert result.current_step == "27b_weak_model_quality_check"
    assert result.error_code == "weak_model_quality_gate_disabled_by_config"
    assert result.weak_model_quality_pipeline_action == "blocked"
    assert "18" not in order


def test_pipeline_result_preserves_non_trading_boundary_fields() -> None:
    service = _build_full_success_service(order=[])

    result = service.run_strategy_pipeline(
        FakeSession(),
        request=StrategyPipelineRequest(kline_slot_utc=SLOT, dry_run=False, confirm_write=True),
    )

    assert result.is_final_trading_advice is False
    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False


def _build_full_success_service(
    *,
    order: list[str],
    repository: FakeRepository | None = None,
    lock_manager: FakeLockManager | None = None,
    stage16_service: FakeStage16 | None = None,
    stage17_service: FakeStage17 | None = None,
    stage23f_service: FakeStage23F | None = None,
    stage26b_service: FakeStage26B | None = None,
    stage27a_service: FakeStage27A | None = None,
    stage27b_service: FakeStage27B | None = None,
    stage18_service: FakeStage18 | None = None,
    stage20_worker: FakeStage20Worker | None = None,
    stage20a_service: FakeStage20A | None = None,
    settings: AppSettings | None = None,
) -> StrategyPipelineService:
    return StrategyPipelineService(
        settings=settings or AppSettings(
            strategy_pipeline_enabled=True,
            strategy_evidence_aggregation_enabled=True,
        ),
        repository=repository or FakeRepository(order=order),
        lock_manager=lock_manager or FakeLockManager(),
        stage16_service=stage16_service,
        stage17_service=stage17_service or FakeStage17(order),
        stage23f_service=stage23f_service or FakeStage23F(order),
        stage26b_service=stage26b_service or FakeStage26B(order),
        stage27a_service=stage27a_service or FakeStage27A(order),
        stage27b_service=stage27b_service or FakeStage27B(order),
        stage18_service=stage18_service or FakeStage18(order),
        stage20_worker=stage20_worker or FakeStage20Worker(order),
        stage20a_service=stage20a_service or FakeStage20A(order),
        stage21_service=FakeStage21(order),
    )
