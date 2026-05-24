"""Tests for stage-21C strategy advice scheduler orchestration.

These tests use in-memory fakes. They do not request Binance, connect real
MySQL/Redis, send real Hermes, call stage 19, call model providers, scan
analysis_material_pack, or modify Kline tables.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertSendStatus
from app.core.exceptions import RedisError
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy_advice.notification_schema import (
    StrategyAdviceNotificationResult,
    StrategyAdviceNotificationStatus,
)
from app.strategy_advice.scheduler_locks import StrategyAdviceSchedulerLock
from app.strategy_advice.scheduler_schema import StrategyAdviceSchedulerRequest, StrategyAdviceSchedulerStatus
from app.strategy_advice.scheduler_service import StrategyAdviceSchedulerService
from app.strategy_advice.schema import AdviceEventType, LifecycleAction, StrategyAdviceResult, StrategyAdviceServiceStatus

NOW = datetime(2026, 5, 24, 4, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeSchedulerRepository:
    """In-memory 21C repository that never scans material packs."""

    def __init__(self) -> None:
        self.mrags: dict[str, Any] = {}
        self.reviews_by_source: dict[str, Any] = {}
        self.reviews_by_id: dict[str, Any] = {}
        self.lifecycle_reviews: list[Any] = []
        self.events: list[Any] = []
        self.scheduler_logs: list[Any] = []
        self.failed_counts: dict[str, int] = {}
        self.latest_failed_at: dict[str, datetime] = {}

    def get_review_aggregation_by_id(self, db_session: Any, *, review_aggregation_run_id: str) -> Any | None:
        del db_session
        return self.mrags.get(review_aggregation_run_id)

    def get_latest_review_aggregation_for_scope(self, db_session: Any, *, symbol: str, base_interval: str, higher_interval: str) -> Any | None:
        del db_session
        rows = [
            row
            for row in self.mrags.values()
            if row.symbol == symbol and row.base_interval == base_interval and row.higher_interval == higher_interval
        ]
        return sorted(rows, key=lambda row: (row.created_at_utc, row.id), reverse=True)[0] if rows else None

    def list_unprocessed_review_aggregations(self, db_session: Any, *, symbol: str, base_interval: str, higher_interval: str, limit: int) -> tuple[Any, ...]:
        del db_session
        rows = [
            row
            for row in self.mrags.values()
            if row.symbol == symbol
            and row.base_interval == base_interval
            and row.higher_interval == higher_interval
            and row.review_aggregation_run_id not in self.reviews_by_source
        ]
        return tuple(sorted(rows, key=lambda row: (row.created_at_utc, row.id), reverse=True)[:limit])

    def get_lifecycle_review_by_source_review_aggregation(self, db_session: Any, *, review_aggregation_run_id: str) -> Any | None:
        del db_session
        return self.reviews_by_source.get(review_aggregation_run_id)

    def get_lifecycle_review_by_id(self, db_session: Any, *, review_id: str) -> Any | None:
        del db_session
        return self.reviews_by_id.get(review_id)

    def list_notification_recovery_reviews(self, db_session: Any, *, symbol: str, base_interval: str, higher_interval: str, limit: int) -> tuple[Any, ...]:
        del db_session
        rows = [
            review
            for review in self.reviews_by_id.values()
            if review.symbol == symbol and review.base_interval == base_interval and review.higher_interval == higher_interval
        ]
        return tuple(sorted(rows, key=lambda row: row.created_at_utc, reverse=True)[:limit])

    def count_notification_failed_events(self, db_session: Any, *, review_id: str) -> int:
        del db_session
        return self.failed_counts.get(review_id, 0)

    def latest_notification_failed_at(self, db_session: Any, *, review_id: str) -> datetime | None:
        del db_session
        return self.latest_failed_at.get(review_id)

    def create_lifecycle_review(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _review_from_payload(payload)
        self.lifecycle_reviews.append(payload)
        self.reviews_by_source[payload.source_review_aggregation_run_id] = row
        self.reviews_by_id[payload.review_id] = row
        return row

    def create_strategy_advice_event(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        self.events.append(payload)
        values = dict(payload.__dict__)
        values["event_type"] = payload.event_type.value
        return SimpleNamespace(**values)

    def create_scheduler_event_log(self, db_session: Any, **kwargs: Any) -> Any:
        del db_session
        row = SimpleNamespace(**kwargs)
        self.scheduler_logs.append(row)
        return row


class FakeAdviceService:
    def __init__(self, repo: FakeSchedulerRepository, *, fail: bool = False) -> None:
        self.repo = repo
        self.fail = fail
        self.calls: list[Any] = []

    def run_strategy_advice(self, db_session: Any, *, request: Any) -> StrategyAdviceResult:
        del db_session
        self.calls.append(request)
        review_id = f"ADVR-{request.review_aggregation_run_id}"
        if self.fail:
            return StrategyAdviceResult(
                status=StrategyAdviceServiceStatus.FAILED,
                exit_code=4,
                review_id=review_id,
                review_aggregation_run_id=request.review_aggregation_run_id,
                trace_id=request.trace_id,
                error_code="stage21a_failed",
            )
        mrag = self.repo.mrags[request.review_aggregation_run_id]
        if request.confirm_write:
            review = _review(
                review_id,
                source_review_aggregation_run_id=request.review_aggregation_run_id,
                result_advice_id=f"ADV-{request.review_aggregation_run_id}",
                notification_required=True,
            )
            self.repo.reviews_by_source[request.review_aggregation_run_id] = review
            self.repo.reviews_by_id[review_id] = review
        return StrategyAdviceResult(
            status=StrategyAdviceServiceStatus.SUCCESS,
            exit_code=0,
            review_id=review_id,
            review_aggregation_run_id=request.review_aggregation_run_id,
            trace_id=request.trace_id,
            material_pack_id=mrag.material_pack_id,
            notification_required=True,
            notification_level="brief",
            dry_run=request.dry_run,
        )


class FakeNotificationSender:
    def __init__(self, *, status: StrategyAdviceNotificationStatus = StrategyAdviceNotificationStatus.SUCCESS) -> None:
        self.status = status
        self.calls: list[Any] = []

    def send_strategy_advice_notification(self, db_session: Any, *, request: Any) -> StrategyAdviceNotificationResult:
        del db_session
        self.calls.append(request)
        return StrategyAdviceNotificationResult(
            status=self.status,
            exit_code=0 if self.status != StrategyAdviceNotificationStatus.FAILED else 4,
            review_id=request.review_id,
            trace_id=request.trace_id,
            send_real_alert=request.send_real_alert,
            alert_status=AlertSendStatus.SUBMITTED_TO_HERMES.value if request.send_real_alert else AlertSendStatus.SKIPPED.value,
            event_type="notification_sent" if request.send_real_alert else "notification_prepared",
            error_code="hermes_failed" if self.status == StrategyAdviceNotificationStatus.FAILED else None,
        )


class FakeNotificationRepository:
    def __init__(self) -> None:
        self.sent_reviews: set[str] = set()
        self.success_alert_reviews: set[str] = set()

    def has_successful_notification_event(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return review_id in self.sent_reviews

    def has_successful_alert_message(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return review_id in self.success_alert_reviews


class FakeLockManager:
    def __init__(self, *, acquired: bool = True, raises: bool = False) -> None:
        self.acquired = acquired
        self.raises = raises
        self.acquired_locks: list[StrategyAdviceSchedulerLock] = []
        self.released_locks: list[StrategyAdviceSchedulerLock] = []

    def acquire_strategy_advice_lock(self, *, lock: StrategyAdviceSchedulerLock) -> bool:
        self.acquired_locks.append(lock)
        if self.raises:
            raise RedisError("lock unavailable")
        return self.acquired

    def release_strategy_advice_lock(self, *, lock: StrategyAdviceSchedulerLock) -> bool:
        self.released_locks.append(lock)
        return True


def test_scheduler_disabled_skips_without_21a_or_21b() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    result, _session, _lock = _run(repo, advice, sender, scheduler_enabled=False, trigger_source=TRIGGER_SOURCE_SCHEDULER)

    assert result.status == StrategyAdviceSchedulerStatus.DISABLED
    assert advice.calls == []
    assert sender.calls == []


def test_notification_send_disabled_prepares_notification_without_real_send() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    result, _session, _lock = _run(repo, advice, sender, notification_send_enabled=False)

    assert result.status == StrategyAdviceSchedulerStatus.SUCCESS
    assert sender.calls
    assert sender.calls[0].send_real_alert is False


def test_notification_send_enabled_uses_real_send_path_for_21b() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    result, _session, _lock = _run(repo, advice, sender, notification_send_enabled=True)

    assert result.status == StrategyAdviceSchedulerStatus.SUCCESS
    assert sender.calls[0].send_real_alert is True


def test_explicit_latest_mrag_calls_21a_and_21b() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    result, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest")

    assert result.lifecycle_review_id == "ADVR-MRAG-latest"
    assert len(advice.calls) == 1
    assert len(sender.calls) == 1


def test_existing_lifecycle_review_recovers_only_21b() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    review = _review("ADVR-existing", source_review_aggregation_run_id="MRAG-latest")
    repo.reviews_by_source["MRAG-latest"] = review
    repo.reviews_by_id["ADVR-existing"] = review
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()

    result, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest")

    assert result.notification_attempted is True
    assert advice.calls == []
    assert len(sender.calls) == 1


def test_old_mrag_gets_stale_skip_and_latest_gets_formal_processing() -> None:
    old = _mrag("MRAG-old", created_at=NOW - timedelta(hours=4), row_id=1)
    latest = _mrag("MRAG-latest", created_at=NOW, row_id=2)
    repo = _repo_with_mrags(old, latest)
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()

    result, _session, _lock = _run(repo, advice, sender)

    assert result.processed_mrag_count == 2
    assert result.stale_skipped_count == 1
    assert len(advice.calls) == 1
    assert repo.lifecycle_reviews[0].lifecycle_action == LifecycleAction.SKIP_STALE_REVIEW_AGGREGATION
    assert repo.lifecycle_reviews[0].notification_required is False
    assert repo.events[0].event_type == AdviceEventType.STALE_REVIEW_AGGREGATION_SKIPPED


def test_21b_failure_after_21a_does_not_rerun_21a_on_recovery() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    review = _review("ADVR-existing", source_review_aggregation_run_id="MRAG-latest")
    repo.reviews_by_source["MRAG-latest"] = review
    repo.reviews_by_id["ADVR-existing"] = review
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender(status=StrategyAdviceNotificationStatus.FAILED)

    result, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest")

    assert result.status == StrategyAdviceSchedulerStatus.FAILED
    assert advice.calls == []
    assert sender.calls[0].review_id == "ADVR-existing"


def test_notification_retry_waits_five_minutes_and_stops_after_three_failures() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    review = _review("ADVR-retry", source_review_aggregation_run_id="MRAG-latest")
    repo.reviews_by_source["MRAG-latest"] = review
    repo.reviews_by_id["ADVR-retry"] = review
    repo.failed_counts["ADVR-retry"] = 1
    repo.latest_failed_at["ADVR-retry"] = datetime.now(timezone.utc) - timedelta(minutes=4)
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()

    waiting, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest")
    repo.failed_counts["ADVR-retry"] = 3
    repo.latest_failed_at["ADVR-retry"] = datetime.now(timezone.utc) - timedelta(minutes=6)
    limited, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest")

    assert waiting.error_code == "notification_retry_waiting"
    assert limited.error_code == "notification_retry_limit_reached"
    assert sender.calls == []


def test_successful_review_id_notification_is_not_sent_twice() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    review = _review("ADVR-sent", source_review_aggregation_run_id="MRAG-latest")
    repo.reviews_by_source["MRAG-latest"] = review
    repo.reviews_by_id["ADVR-sent"] = review
    notification_repo = FakeNotificationRepository()
    notification_repo.sent_reviews.add("ADVR-sent")
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()

    result, _session, _lock = _run(repo, advice, sender, review_aggregation_run_id="MRAG-latest", notification_repo=notification_repo)

    assert result.status == StrategyAdviceSchedulerStatus.SKIPPED
    assert result.error_code == "notification_already_sent"
    assert sender.calls == []


def test_redis_lock_held_results_in_lock_skipped_without_21a() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-latest", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    result, _session, lock = _run(repo, advice, sender, lock_manager=FakeLockManager(acquired=False))

    assert result.status == StrategyAdviceSchedulerStatus.LOCK_SKIPPED
    assert result.error_code == "lock_already_held"
    assert advice.calls == []
    assert lock.acquired_locks[0].key == "strategy_advice_21c:BTCUSDT:4h:1d:MRAG-latest"


def test_cli_trigger_records_cli_and_scheduler_trigger_records_scheduler() -> None:
    repo = _repo_with_mrags(_mrag("MRAG-cli", created_at=NOW))
    advice = FakeAdviceService(repo)
    sender = FakeNotificationSender()
    _run(repo, advice, sender, trigger_source=TRIGGER_SOURCE_CLI)

    assert repo.scheduler_logs[-1].trigger_source == TRIGGER_SOURCE_CLI


def _run(
    repo: FakeSchedulerRepository,
    advice: FakeAdviceService,
    sender: FakeNotificationSender,
    *,
    review_aggregation_run_id: str | None = None,
    scheduler_enabled: bool = True,
    notification_send_enabled: bool = False,
    trigger_source: str = TRIGGER_SOURCE_SCHEDULER,
    lock_manager: FakeLockManager | None = None,
    notification_repo: FakeNotificationRepository | None = None,
) -> tuple[Any, FakeSession, FakeLockManager]:
    session = FakeSession()
    active_lock = lock_manager or FakeLockManager()
    active_notification_repo = notification_repo or FakeNotificationRepository()
    service = StrategyAdviceSchedulerService(
        settings=SimpleNamespace(
            strategy_advice_scheduler_enabled=scheduler_enabled,
            strategy_advice_notification_send_enabled=notification_send_enabled,
        ),
        repository=repo,
        advice_service=advice,
        notification_sender=sender,
        notification_repository=active_notification_repo,
        lock_manager=active_lock,
    )
    result = service.run_strategy_advice_scheduler(
        session,
        request=StrategyAdviceSchedulerRequest(
            review_aggregation_run_id=review_aggregation_run_id,
            trigger_source=trigger_source,
            dry_run=False,
            confirm_write=True,
            trace_id="trace-21c",
        ),
    )
    return result, session, active_lock


def _repo_with_mrags(*mrags: Any) -> FakeSchedulerRepository:
    repo = FakeSchedulerRepository()
    for mrag in mrags:
        repo.mrags[mrag.review_aggregation_run_id] = mrag
    return repo


def _mrag(
    review_aggregation_run_id: str,
    *,
    created_at: datetime,
    row_id: int = 1,
    status: str = "success",
) -> Any:
    return SimpleNamespace(
        id=row_id,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=f"AMP-{review_aggregation_run_id}",
        aggregation_run_id=f"AGR-{review_aggregation_run_id}",
        strategy_signal_run_id=f"SIG-{review_aggregation_run_id}",
        snapshot_id=f"SNAP-{review_aggregation_run_id}",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        status=status,
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_basis="no_model_review",
        model_review_expired=False,
        model_review_chain_status="not_started",
        created_at_utc=created_at,
    )


def _review(
    review_id: str,
    *,
    source_review_aggregation_run_id: str,
    result_advice_id: str | None = "ADV-existing",
    notification_required: bool = True,
) -> Any:
    return SimpleNamespace(
        review_id=review_id,
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        source_review_aggregation_run_id=source_review_aggregation_run_id,
        source_material_pack_id=f"AMP-{source_review_aggregation_run_id}",
        result_advice_id=result_advice_id,
        reviewed_advice_id=None,
        notification_required=notification_required,
        notification_payload_json="{\"source\":{\"review_aggregation_run_id\":\"MRAG\"}}",
        notification_level="brief",
        created_at_utc=NOW,
    )


def _review_from_payload(payload: Any) -> Any:
    return SimpleNamespace(
        review_id=payload.review_id,
        symbol=payload.symbol,
        base_interval=payload.base_interval,
        higher_interval=payload.higher_interval,
        source_review_aggregation_run_id=payload.source_review_aggregation_run_id,
        source_material_pack_id=payload.source_material_pack_id,
        result_advice_id=payload.result_advice_id,
        reviewed_advice_id=payload.reviewed_advice_id,
        lifecycle_action=payload.lifecycle_action.value,
        notification_required=payload.notification_required,
        notification_payload_json="{}",
        notification_level=payload.notification_level,
        created_at_utc=NOW,
    )
