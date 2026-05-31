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


@dataclass
class FakeStage17Event:
    event_id: str = "SSS-reused"
    status: str = "success"
    run_id: str | None = "SSR-reused"
    target_base_open_time_utc: datetime = SLOT
    created_at_utc: datetime = SLOT


class FakeRepository:
    def __init__(
        self,
        order: list[str] | None = None,
        latest_slot: datetime | None = SLOT,
        existing_evidence: FakeEvidenceAggregation | None = None,
        stage17_events: list[FakeStage17Event] | None = None,
    ) -> None:
        self.order = order if order is not None else []
        self.latest_slot = latest_slot
        self.existing_evidence = existing_evidence
        self.stage17_events = stage17_events or []
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
    assert order == ["17", "23f_lookup", "23f_create", "18", "20c", "20a", "21c"]
    assert result.strategy_signal_run_id == "SSR-test"
    assert result.strategy_evidence_aggregation_id == "SEA-created"
    assert result.material_pack_id == "AMP-test"
    assert result.model_analysis_run_id == "MAR-test"
    assert result.review_aggregation_run_id == "MRAG-test"
    assert result.advice_id == "ADV-test"
    assert result.review_id == "ADVR-test"


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
        stage17_events=[FakeStage17Event(event_id="SSS-failed", status="failed", run_id="SSR-bad")],
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
    assert order == ["17", "23f_lookup", "18", "20c", "20a", "21c"]
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
    stage17_service: FakeStage17 | None = None,
    stage23f_service: FakeStage23F | None = None,
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
        stage17_service=stage17_service or FakeStage17(order),
        stage23f_service=stage23f_service or FakeStage23F(order),
        stage18_service=FakeStage18(order),
        stage20_worker=stage20_worker or FakeStage20Worker(order),
        stage20a_service=stage20a_service or FakeStage20A(order),
        stage21_service=FakeStage21(order),
    )
