from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import AppSettings
from app.model_review_aggregation.schema import ModelReviewAggregationResult, ModelReviewAggregationStatus
from app.model_review_chain.worker_schema import MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS, build_worker_result
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.strategy.aggregation.types import StrategyAggregationResult, StrategyAggregationStatus
from app.strategy_advice.scheduler_schema import (
    StrategyAdviceSchedulerResult,
    StrategyAdviceSchedulerStatus,
)
from app.strategy_pipeline.locks import StrategyPipelineLock
from app.strategy_pipeline.service import StrategyPipelineService
from app.strategy_pipeline.types import StrategyPipelineRequest, StrategyPipelineStatus


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


class FakeRepository:
    def __init__(self, order: list[str] | None = None, latest_slot: datetime | None = SLOT) -> None:
        self.order = order if order is not None else []
        self.latest_slot = latest_slot
        self.created_events: list[Any] = []
        self.updated_events: list[Any] = []

    def resolve_latest_base_kline_slot_utc(self, db_session: Any, *, symbol: str, base_interval: str) -> datetime | None:
        return self.latest_slot

    def get_latest_strategy_evidence_aggregation(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
    ) -> FakeEvidenceAggregation:
        self.order.append("23f")
        return FakeEvidenceAggregation()

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
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def run_after_collector_success(self, db_session: Any, *, request: Any) -> StrategySignalSchedulerResult:
        self.order.append("17")
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-test",
            trace_id=request.trace_id,
            message="stage17 ok",
            target_base_open_time_ms=request.upstream_latest_base_open_time_ms,
            run_id="SSR-test",
            snapshot_id="MCS-test",
            strategy_count=4,
            success_count=4,
        )


class FakeStage18:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def run_strategy_aggregation(self, db_session: Any, *, request: Any) -> StrategyAggregationResult:
        self.order.append("18")
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SUCCESS,
            exit_code=0,
            aggregation_run_id="SAR-test",
            material_pack_id="AMP-test",
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=request.trace_id,
            message="stage18 ok",
        )


class FakeStage20Worker:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.requests: list[Any] = []

    def run_model_review_chain_worker(self, db_session: Any, *, request: Any) -> Any:
        self.order.append("20c")
        self.requests.append(request)
        return build_worker_result(
            status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS,
            trace_id=request.trace_id,
            material_pack_id=request.material_pack_id,
            model_review_reused=True,
            reused_model_analysis_run_id="MAR-test",
            summary_text="stage20c ok",
        )


class FakeStage20A:
    def __init__(self, order: list[str]) -> None:
        self.order = order

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
            model_review_reused=True,
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
    assert order == ["17", "23f", "18", "20c", "20a", "21c"]
    assert result.strategy_signal_run_id == "SSR-test"
    assert result.strategy_evidence_aggregation_id == "SEA-test"
    assert result.material_pack_id == "AMP-test"
    assert result.model_analysis_run_id == "MAR-test"
    assert result.review_aggregation_run_id == "MRAG-test"
    assert result.advice_id == "ADV-test"
    assert result.review_id == "ADVR-test"


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
    stage20_worker: FakeStage20Worker | None = None,
    settings: AppSettings | None = None,
) -> StrategyPipelineService:
    return StrategyPipelineService(
        settings=settings or AppSettings(strategy_pipeline_enabled=True),
        repository=repository or FakeRepository(order=order),
        lock_manager=lock_manager or FakeLockManager(),
        stage17_service=FakeStage17(order),
        stage18_service=FakeStage18(order),
        stage20_worker=stage20_worker or FakeStage20Worker(order),
        stage20a_service=FakeStage20A(order),
        stage21_service=FakeStage21(order),
    )

