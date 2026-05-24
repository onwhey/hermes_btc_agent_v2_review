"""Tests for stage-21B strategy advice notification delivery.

These tests use in-memory repositories and a mock Hermes client. They do not
request Binance, connect real MySQL/Redis, send real Hermes, call stage 19,
call large model providers, connect scheduler, or modify Kline tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.strategy_advice.notification_renderer import render_strategy_advice_notification
from app.strategy_advice.notification_schema import (
    StrategyAdviceNotificationRequest,
    StrategyAdviceNotificationStatus,
)
from app.strategy_advice.notification_sender import StrategyAdviceNotificationSender
from app.strategy_advice.schema import AdviceEventType

CREATED_AT = datetime(2026, 5, 23, 4, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeNotificationRepository:
    """In-memory 21B repository that keeps tests side-effect free."""

    def __init__(self) -> None:
        self.reviews: dict[str, Any] = {}
        self.sent_event_reviews: set[str] = set()
        self.successful_alert_reviews: set[str] = set()
        self.alert_messages: list[Any] = []
        self.events: list[Any] = []

    def get_lifecycle_review_by_id(self, db_session: Any, *, review_id: str) -> Any | None:
        del db_session
        return self.reviews.get(review_id)

    def has_successful_notification_event(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return review_id in self.sent_event_reviews

    def has_successful_alert_message(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return review_id in self.successful_alert_reviews

    def has_prepared_notification_event(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return any(
            event.related_review_id == review_id
            and event.event_type == AdviceEventType.NOTIFICATION_PREPARED.value
            for event in self.events
        )

    def has_skipped_alert_message(self, db_session: Any, *, review_id: str) -> bool:
        del db_session
        return any(
            alert.related_review_id == review_id
            and alert.status == AlertSendStatus.SKIPPED.value
            for alert in self.alert_messages
        )

    def has_prepared_notification_artifact(self, db_session: Any, *, review_id: str) -> bool:
        return self.has_prepared_notification_event(
            db_session,
            review_id=review_id,
        ) or self.has_skipped_alert_message(db_session, review_id=review_id)

    def count_notification_delivery_events(self, db_session: Any, *, review_id: str) -> int:
        del db_session
        return sum(1 for event in self.events if event.related_review_id == review_id)

    def create_alert_message(
        self,
        db_session: Any,
        *,
        event: Any,
        message: str,
        related_type: str,
        related_id: str,
        related_review_id: str,
        initial_status: str,
        channel_response: dict[str, Any] | None = None,
    ) -> Any:
        del db_session
        row = SimpleNamespace(
            id=len(self.alert_messages) + 1,
            alert_type=event.alert_type.value,
            severity=event.severity.value,
            title=event.title,
            message=message,
            related_type=related_type,
            related_id=related_id,
            related_review_id=related_review_id,
            status=initial_status,
            channel_response=channel_response or {},
            error_message=None,
            retry_count=0,
            http_status_code=None,
            sent_at_utc=None,
        )
        self.alert_messages.append(row)
        return row

    def update_alert_message_result(self, db_session: Any, *, alert_message: Any, result: AlertSendResult) -> Any:
        del db_session
        alert_message.status = result.status.value
        alert_message.channel_response = dict(result.channel_response)
        alert_message.error_message = result.error_message or None
        alert_message.retry_count = result.retry_count
        alert_message.http_status_code = result.http_status_code
        alert_message.sent_at_utc = result.submitted_at_utc
        return alert_message

    def create_notification_event(
        self,
        db_session: Any,
        *,
        review_id: str,
        advice_id: str | None,
        event_type: AdviceEventType,
        event_reason: str,
        event_payload: dict[str, Any],
    ) -> Any:
        del db_session
        row = SimpleNamespace(
            event_id=f"EV-{len(self.events) + 1}",
            advice_id=advice_id,
            related_review_id=review_id,
            event_type=event_type.value,
            event_reason=event_reason,
            event_payload_json=json.dumps(event_payload, ensure_ascii=False),
        )
        self.events.append(row)
        if event_type == AdviceEventType.NOTIFICATION_SENT:
            self.sent_event_reviews.add(review_id)
        return row


class FakeHermesClient:
    def __init__(self, result: AlertSendResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def send_alert_message(self, event: Any, message: str, *, send_real_alert: bool = False) -> AlertSendResult:
        self.calls.append({"event": event, "message": message, "send_real_alert": send_real_alert})
        return self.result


def test_dry_run_renders_brief_notification_without_writes_or_hermes() -> None:
    repo = _repo_with_review(_review("ADVR-brief", level="brief", lifecycle_action="continue_active_advice"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES))
    result, session = _run(repo, client, "ADVR-brief")

    assert result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert result.notification_level == "brief"
    assert result.title == "BTC 4h 建议：延续上一条建议"
    assert "调用=否" in result.message_preview
    assert repo.alert_messages == []
    assert repo.events == []
    assert client.calls == []
    assert session.commit_count == 0


def test_dry_run_renders_full_notification_without_writes_or_hermes() -> None:
    review = _review("ADVR-full", level="full", lifecycle_action="update_active_advice", result_advice_id="ADV-2")
    rendered = render_strategy_advice_notification(review)

    assert "生命周期" in rendered.message
    assert "大模型状态" in rendered.message
    assert "风险状态" in rendered.message
    assert "来源追踪" in rendered.message
    assert "边界声明" in rendered.message


def test_notification_required_false_is_skipped_without_writes() -> None:
    repo = _repo_with_review(_review("ADVR-skip", notification_required=False))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES))
    result, session = _run(repo, client, "ADVR-skip")

    assert result.status == StrategyAdviceNotificationStatus.SKIPPED
    assert result.error_message == "notification_required=false"
    assert repo.alert_messages == []
    assert repo.events == []
    assert client.calls == []
    assert session.commit_count == 0


def test_empty_notification_payload_is_blocked() -> None:
    repo = _repo_with_review(_review("ADVR-empty", payload={}))
    result, _session = _run(repo, FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)), "ADVR-empty")

    assert result.status == StrategyAdviceNotificationStatus.BLOCKED
    assert result.error_code == "notification_payload_empty"
    assert repo.alert_messages == []
    assert repo.events == []


def test_wait_without_active_advice_uses_lifecycle_review_related_ref() -> None:
    repo = _repo_with_review(_review("ADVR-wait", result_advice_id=None, reviewed_advice_id=None))
    result, _session = _run(repo, FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)), "ADVR-wait")

    assert result.related_type == "strategy_advice_lifecycle_review"
    assert result.related_id == "ADVR-wait"


def test_result_advice_id_uses_strategy_advice_related_ref() -> None:
    repo = _repo_with_review(_review("ADVR-advice", result_advice_id="ADV-result"))
    result, _session = _run(repo, FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)), "ADVR-advice")

    assert result.related_type == "strategy_advice"
    assert result.related_id == "ADV-result"


def test_confirm_write_without_send_real_alert_prepares_alert_and_event_only() -> None:
    repo = _repo_with_review(_review("ADVR-prepare", result_advice_id="ADV-prepare"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES))
    result, session = _run(repo, client, "ADVR-prepare", confirm=True)

    assert result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert result.alert_status == AlertSendStatus.SKIPPED.value
    assert result.event_type == AdviceEventType.NOTIFICATION_PREPARED.value
    assert len(repo.alert_messages) == 1
    assert repo.events[0].event_type == AdviceEventType.NOTIFICATION_PREPARED.value
    assert client.calls == []
    assert session.commit_count == 1


def test_send_disabled_repeated_run_writes_prepared_only_once() -> None:
    repo = _repo_with_review(_review("ADVR-prepared-once", result_advice_id="ADV-prepared-once"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES))

    first, first_session = _run(repo, client, "ADVR-prepared-once", confirm=True)
    second, second_session = _run(repo, client, "ADVR-prepared-once", confirm=True)

    assert first.status == StrategyAdviceNotificationStatus.SUCCESS
    assert second.status == StrategyAdviceNotificationStatus.SKIPPED
    assert second.error_code == "notification_already_prepared"
    assert len(repo.alert_messages) == 1
    assert repo.alert_messages[0].status == AlertSendStatus.SKIPPED.value
    assert len(repo.events) == 1
    assert repo.events[0].event_type == AdviceEventType.NOTIFICATION_PREPARED.value
    assert client.calls == []
    assert first_session.commit_count == 1
    assert second_session.commit_count == 0


def test_send_enabled_after_skipped_prepared_sends_new_alert_without_updating_skipped() -> None:
    repo = _repo_with_review(_review("ADVR-prepared-then-send", result_advice_id="ADV-prepared-then-send"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES, attempted_real_send=True))

    prepared, _session = _run(repo, client, "ADVR-prepared-then-send", confirm=True)
    sent, _session = _run(repo, client, "ADVR-prepared-then-send", confirm=True, send_real=True)

    assert prepared.status == StrategyAdviceNotificationStatus.SUCCESS
    assert sent.status == StrategyAdviceNotificationStatus.SUCCESS
    assert len(repo.alert_messages) == 2
    assert repo.alert_messages[0].status == AlertSendStatus.SKIPPED.value
    assert repo.alert_messages[1].status == AlertSendStatus.SUBMITTED_TO_HERMES.value
    assert [event.event_type for event in repo.events] == [
        AdviceEventType.NOTIFICATION_PREPARED.value,
        AdviceEventType.NOTIFICATION_SENT.value,
    ]
    assert len(client.calls) == 1


def test_multiple_historical_skipped_rows_send_enabled_sends_once_by_review_id() -> None:
    repo = _repo_with_review(_review("ADVR-many-skipped", result_advice_id="ADV-many-skipped"))
    repo.alert_messages.append(SimpleNamespace(id=1, related_review_id="ADVR-many-skipped", status="skipped"))
    repo.alert_messages.append(SimpleNamespace(id=2, related_review_id="ADVR-many-skipped", status="skipped"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES, attempted_real_send=True))

    first, _session = _run(repo, client, "ADVR-many-skipped", confirm=True, send_real=True)
    second, _session = _run(repo, client, "ADVR-many-skipped", confirm=True, send_real=True)

    assert first.status == StrategyAdviceNotificationStatus.SUCCESS
    assert second.status == StrategyAdviceNotificationStatus.SKIPPED
    assert second.error_message == "notification_sent event already exists"
    assert len(client.calls) == 1
    assert [alert.status for alert in repo.alert_messages] == [
        "skipped",
        "skipped",
        AlertSendStatus.SUBMITTED_TO_HERMES.value,
    ]


def test_confirm_write_send_real_alert_calls_hermes_and_writes_sent_event() -> None:
    repo = _repo_with_review(_review("ADVR-send", result_advice_id="ADV-send"))
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES, attempted_real_send=True))
    result, session = _run(repo, client, "ADVR-send", confirm=True, send_real=True)

    assert result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert result.alert_status == AlertSendStatus.SUBMITTED_TO_HERMES.value
    assert result.event_type == AdviceEventType.NOTIFICATION_SENT.value
    assert len(client.calls) == 1
    assert client.calls[0]["send_real_alert"] is True
    assert repo.events[0].event_type == AdviceEventType.NOTIFICATION_SENT.value
    assert session.commit_count == 1


def test_hermes_failure_writes_failed_event_without_changing_review() -> None:
    review = _review("ADVR-fail", result_advice_id="ADV-fail", lifecycle_action="update_active_advice")
    repo = _repo_with_review(review)
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable"))
    result, _session = _run(repo, client, "ADVR-fail", confirm=True, send_real=True)

    assert result.status == StrategyAdviceNotificationStatus.FAILED
    assert result.event_type == AdviceEventType.NOTIFICATION_FAILED.value
    assert repo.events[0].event_type == AdviceEventType.NOTIFICATION_FAILED.value
    assert review.lifecycle_action == "update_active_advice"


def test_successful_notification_event_makes_later_attempt_skipped() -> None:
    repo = _repo_with_review(_review("ADVR-idem", result_advice_id="ADV-idem"))
    repo.sent_event_reviews.add("ADVR-idem")
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES))
    result, _session = _run(repo, client, "ADVR-idem", confirm=True, send_real=True)

    assert result.status == StrategyAdviceNotificationStatus.SKIPPED
    assert result.error_message == "notification_sent event already exists"
    assert client.calls == []
    assert repo.alert_messages == []


def test_successful_alert_message_makes_later_attempt_skipped() -> None:
    repo = _repo_with_review(_review("ADVR-alert-idem", result_advice_id="ADV-alert-idem"))
    repo.successful_alert_reviews.add("ADVR-alert-idem")
    result, _session = _run(
        repo,
        FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)),
        "ADVR-alert-idem",
        confirm=True,
        send_real=True,
    )

    assert result.status == StrategyAdviceNotificationStatus.SKIPPED
    assert result.error_message == "successful alert_message already exists for review_id"
    assert repo.alert_messages == []


def test_different_reviews_for_same_advice_are_not_deduplicated_by_advice_id() -> None:
    repo = FakeNotificationRepository()
    repo.reviews["ADVR-same-advice-1"] = _review("ADVR-same-advice-1", result_advice_id="ADV-shared")
    repo.reviews["ADVR-same-advice-2"] = _review("ADVR-same-advice-2", result_advice_id="ADV-shared")
    client = FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES, attempted_real_send=True))

    first_result, _first_session = _run(repo, client, "ADVR-same-advice-1", confirm=True, send_real=True)
    second_result, _second_session = _run(repo, client, "ADVR-same-advice-2", confirm=True, send_real=True)

    assert first_result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert second_result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert second_result.related_type == "strategy_advice"
    assert second_result.related_id == "ADV-shared"
    assert len(client.calls) == 2
    assert [alert.related_review_id for alert in repo.alert_messages] == [
        "ADVR-same-advice-1",
        "ADVR-same-advice-2",
    ]
    assert [event.related_review_id for event in repo.events] == [
        "ADVR-same-advice-1",
        "ADVR-same-advice-2",
    ]


def test_brief_notification_is_short_but_contains_model_status_and_boundary() -> None:
    rendered = render_strategy_advice_notification(_review("ADVR-brief-content", level="brief"))

    assert len(rendered.message) < 600
    assert "本轮系统已完成" in rendered.message
    assert "大模型状态" in rendered.message
    assert "不是自动交易" in rendered.message


def test_boundary_flags_remain_false_in_result_and_event_payload() -> None:
    repo = _repo_with_review(_review("ADVR-boundary", result_advice_id="ADV-boundary"))
    result, _session = _run(
        repo,
        FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)),
        "ADVR-boundary",
        confirm=True,
    )

    payload = json.loads(repo.events[0].event_payload_json)
    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False
    assert payload["is_trading_signal"] is False
    assert payload["is_executable"] is False
    assert payload["auto_trading_allowed"] is False


def test_scheduler_trigger_is_allowed_for_21c_entry() -> None:
    repo = _repo_with_review(_review("ADVR-scheduler"))
    service = StrategyAdviceNotificationSender(
        repository=repo,
        hermes_client=FakeHermesClient(AlertSendResult(status=AlertSendStatus.SUBMITTED_TO_HERMES)),
    )
    result = service.send_strategy_advice_notification(
        FakeSession(),
        request=StrategyAdviceNotificationRequest(
            review_id="ADVR-scheduler",
            trigger_source="scheduler",
            dry_run=True,
            confirm_write=False,
        ),
    )

    assert result.status == StrategyAdviceNotificationStatus.SUCCESS
    assert result.error_code is None


def _run(
    repo: FakeNotificationRepository,
    client: FakeHermesClient,
    review_id: str,
    *,
    confirm: bool = False,
    send_real: bool = False,
) -> tuple[Any, FakeSession]:
    session = FakeSession()
    service = StrategyAdviceNotificationSender(repository=repo, hermes_client=client)
    result = service.send_strategy_advice_notification(
        session,
        request=StrategyAdviceNotificationRequest(
            review_id=review_id,
            trigger_source=TRIGGER_SOURCE_CLI,
            dry_run=not confirm,
            confirm_write=confirm,
            send_real_alert=send_real,
        ),
    )
    return result, session


def _repo_with_review(review: Any) -> FakeNotificationRepository:
    repo = FakeNotificationRepository()
    repo.reviews[review.review_id] = review
    return repo


def _review(
    review_id: str,
    *,
    level: str = "brief",
    lifecycle_action: str = "wait_without_active_advice",
    result_advice_id: str | None = None,
    reviewed_advice_id: str | None = None,
    notification_required: bool = True,
    payload: dict[str, Any] | None = None,
) -> Any:
    active_payload = _payload(level=level, lifecycle_action=lifecycle_action, result_advice_id=result_advice_id)
    if payload is not None:
        active_payload = payload
    return SimpleNamespace(
        review_id=review_id,
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        result_advice_id=result_advice_id,
        reviewed_advice_id=reviewed_advice_id,
        previous_advice_id=reviewed_advice_id,
        lifecycle_action=lifecycle_action,
        lifecycle_reason="test lifecycle reason",
        source_review_aggregation_run_id="MRAG-test",
        source_material_pack_id="AMP-test",
        source_strategy_signal_run_id="SSR-test",
        source_snapshot_id="MCS-test",
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_basis="no_model_review",
        model_review_expired=False,
        model_review_chain_status="not_started",
        notification_required=notification_required,
        notification_level=level,
        notification_reason="test notification reason",
        notification_payload_json=json.dumps(active_payload, ensure_ascii=False),
        created_at_utc=CREATED_AT,
    )


def _payload(*, level: str, lifecycle_action: str, result_advice_id: str | None) -> dict[str, Any]:
    return {
        "schema_version": "strategy_advice_payload_v1",
        "lifecycle": {
            "action": lifecycle_action,
            "reason": "test lifecycle reason",
            "notification_level": level,
            "reviewed_advice_id": None,
            "result_advice_id": result_advice_id,
        },
        "advice": {
            "advice_id": result_advice_id,
            "advice_code": "A-test",
            "advice_path": result_advice_id,
            "advice_action": "wait",
            "directional_bias": "unknown",
            "trade_permission": "not_allowed",
            "risk_blocked": False,
        },
        "source": {
            "review_aggregation_run_id": "MRAG-test",
            "material_pack_id": "AMP-test",
            "strategy_signal_run_id": "SSR-test",
            "snapshot_id": "MCS-test",
        },
        "model_review": {
            "model_review_invoked": False,
            "model_review_invocation_mode": "none",
            "model_review_reused": False,
            "reused_model_analysis_run_id": "",
            "model_review_skip_reason": "本轮未调用大模型",
            "model_review_block_reason": "",
            "model_review_chain_status": "not_started",
            "model_review_basis": "no_model_review",
            "model_review_expired": False,
            "no_model_invocation_reason": "本轮未调用大模型",
        },
        "risk": {
            "risk_acceptability": "acceptable",
            "risk_blocked": False,
            "risk_warnings": [],
            "missing_evidence": [],
        },
        "strategy": {
            "strategy_conflict": "low",
            "evidence_quality": "sufficient",
        },
        "boundaries": {
            "is_trading_signal": False,
            "is_executable": False,
            "auto_trading_allowed": False,
        },
    }
