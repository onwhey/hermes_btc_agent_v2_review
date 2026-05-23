"""Tests for stage-21A strategy advice lifecycle service.

These tests use an in-memory repository. They do not request Binance, connect
MySQL/Redis, send Hermes, call stage 19, call large model providers, or modify
Kline tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.strategy_advice.schema import (
    AdviceAction,
    AdviceStatus,
    LifecycleAction,
    StrategyAdviceRequest,
    StrategyAdviceServiceStatus,
    TradePermission,
)
from app.strategy_advice.service import StrategyAdviceService

CREATED_AT = datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeStrategyAdviceRepository:
    """In-memory stage-21A repository used to keep tests side-effect free."""

    def __init__(self) -> None:
        self.aggregations: dict[str, Any] = {}
        self.active_advice: Any | None = None
        self.created_advice: list[Any] = []
        self.lifecycle_reviews: list[Any] = []
        self.events: list[Any] = []
        self.trade_setups: list[Any] = []
        self.status_updates: list[tuple[Any, str]] = []

    def get_review_aggregation_run_by_id(self, db_session: Any, *, review_aggregation_run_id: str) -> Any | None:
        del db_session
        return self.aggregations.get(review_aggregation_run_id)

    def get_active_strategy_advice(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
    ) -> Any | None:
        del db_session
        active = self.active_advice
        if active is None:
            return None
        if active.advice_status != AdviceStatus.ACTIVE.value:
            return None
        if active.symbol == symbol and active.base_interval == base_interval and active.higher_interval == higher_interval:
            return active
        return None

    def create_strategy_advice(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _row_from_payload(payload)
        self.created_advice.append(payload)
        if row.advice_status == AdviceStatus.ACTIVE.value:
            self.active_advice = row
        return row

    def update_strategy_advice_status(self, db_session: Any, advice_row: Any, *, advice_status: str, closed_at_utc: Any) -> Any:
        del db_session
        advice_row.advice_status = advice_status
        advice_row.closed_at_utc = closed_at_utc
        self.status_updates.append((advice_row, advice_status))
        if self.active_advice is advice_row and advice_status != AdviceStatus.ACTIVE.value:
            self.active_advice = None
        return advice_row

    def create_lifecycle_review(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        self.lifecycle_reviews.append(payload)
        return _row_from_payload(payload)

    def create_strategy_advice_event(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        self.events.append(payload)
        return _row_from_payload(payload)

    def create_strategy_advice_trade_setup(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        self.trade_setups.append(payload)
        return _row_from_payload(payload)


def test_no_active_advice_creates_new_strategy_advice() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))
    result, session = _run(repo, "MRAG-1", confirm=True)

    assert result.status == StrategyAdviceServiceStatus.SUCCESS
    assert result.lifecycle_action == LifecycleAction.CREATE_NEW_ADVICE
    assert result.advice_status == AdviceStatus.ACTIVE
    assert result.advice_action == AdviceAction.CONDITIONAL_TRADE
    assert result.trade_permission == TradePermission.CONDITIONALLY_ALLOWED
    assert len(repo.created_advice) == 1
    assert repo.created_advice[0].advice_path == repo.created_advice[0].advice_id
    assert result.advice_path == result.advice_id
    assert session.commit_count == 1


def test_new_advice_advice_path_equals_advice_id() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))

    result, _ = _run(repo, "MRAG-1", confirm=True)

    assert result.advice_id
    assert result.advice_path == result.advice_id
    assert repo.created_advice[0].advice_path == repo.created_advice[0].advice_id


def test_existing_active_without_substantial_change_continues_without_new_advice() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))
    created, _ = _run(repo, "MRAG-1", confirm=True)
    repo.aggregations["MRAG-2"] = _aggregation("MRAG-2", summary="create_new_advice conditional_trade bullish")

    result, _ = _run(repo, "MRAG-2", confirm=True)

    assert result.lifecycle_action == LifecycleAction.CONTINUE_ACTIVE_ADVICE
    assert result.result_advice_id == created.advice_id
    assert len(repo.created_advice) == 1
    assert len(repo.lifecycle_reviews) == 2
    assert any(event.event_type == "continued" for event in repo.events)


def test_continue_advice_uses_brief_notification() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))
    _run(repo, "MRAG-1", confirm=True)
    repo.aggregations["MRAG-2"] = _aggregation("MRAG-2", summary="create_new_advice conditional_trade bullish")

    result, _ = _run(repo, "MRAG-2", confirm=True)

    assert result.notification_required is True
    assert result.notification_level == "brief"
    assert repo.lifecycle_reviews[-1].notification_required is True
    assert repo.lifecycle_reviews[-1].notification_level == "brief"


def test_existing_active_with_substantial_change_supersedes_and_creates_new_version() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))
    first, _ = _run(repo, "MRAG-1", confirm=True)
    repo.aggregations["MRAG-2"] = _aggregation("MRAG-2", summary="conditional_trade bearish")

    result, _ = _run(repo, "MRAG-2", confirm=True)

    assert result.lifecycle_action == LifecycleAction.UPDATE_ACTIVE_ADVICE
    assert repo.status_updates[-1][1] == AdviceStatus.SUPERSEDED.value
    assert len(repo.created_advice) == 2
    assert result.result_advice_id != first.advice_id
    assert result.previous_advice_id == first.advice_id
    assert result.advice_path == f"{first.advice_path}/{result.advice_id}"
    assert repo.created_advice[-1].parent_advice_id == first.advice_id
    assert repo.created_advice[-1].root_advice_id == first.advice_id
    assert any(event.event_type == "superseded" for event in repo.events)
    assert any(event.event_type == "activated" for event in repo.events)
    assert result.notification_level == "full"


def test_new_version_advice_path_extends_parent_path() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-1", summary="create_new_advice conditional_trade bullish"))
    first, _ = _run(repo, "MRAG-1", confirm=True)
    repo.aggregations["MRAG-2"] = _aggregation("MRAG-2", summary="conditional_trade bearish")

    result, _ = _run(repo, "MRAG-2", confirm=True)

    assert result.advice_path == f"{first.advice_path}/{result.advice_id}"


def test_close_active_advice_does_not_create_new_version() -> None:
    repo = _repo_with_active()
    repo.aggregations["MRAG-close"] = _aggregation("MRAG-close", summary="close_active_advice")

    result, _ = _run(repo, "MRAG-close", confirm=True)

    assert result.lifecycle_action == LifecycleAction.CLOSE_ACTIVE_ADVICE
    assert repo.status_updates[-1][1] == AdviceStatus.CLOSED.value
    assert len(repo.created_advice) == 0
    assert result.result_advice_id == "ADV-ACTIVE"
    assert any(event.event_type == "closed" for event in repo.events)


@pytest.mark.parametrize(
    ("summary", "expected_action", "expected_status", "expected_event"),
    [
        ("complete_active_advice", LifecycleAction.COMPLETE_ACTIVE_ADVICE, AdviceStatus.COMPLETED, "completed"),
        ("invalidate_active_advice", LifecycleAction.INVALIDATE_ACTIVE_ADVICE, AdviceStatus.INVALIDATED, "invalidated"),
        ("expire_active_advice", LifecycleAction.EXPIRE_ACTIVE_ADVICE, AdviceStatus.EXPIRED, "expired"),
    ],
)
def test_terminal_actions_record_lifecycle_and_event(
    summary: str,
    expected_action: LifecycleAction,
    expected_status: AdviceStatus,
    expected_event: str,
) -> None:
    repo = _repo_with_active()
    repo.aggregations[f"MRAG-{expected_event}"] = _aggregation(f"MRAG-{expected_event}", summary=summary)

    result, _ = _run(repo, f"MRAG-{expected_event}", confirm=True)

    assert result.lifecycle_action == expected_action
    assert repo.status_updates[-1][1] == expected_status.value
    assert any(event.event_type == expected_event for event in repo.events)
    assert len(repo.created_advice) == 0


def test_no_active_and_not_suitable_records_wait_without_active_advice() -> None:
    repo = _repo_with_aggregation(
        _aggregation(
            "MRAG-risk",
            risk="unacceptable",
            conflict="high",
            summary="conditional_trade bullish but risk unacceptable",
        )
    )

    result, _ = _run(repo, "MRAG-risk", confirm=True)

    assert result.lifecycle_action in {LifecycleAction.WAIT_WITHOUT_ACTIVE_ADVICE, LifecycleAction.STOP_TRADING}
    assert len(repo.created_advice) == 0
    assert len(repo.lifecycle_reviews) == 1
    assert len(repo.events) >= 1
    assert result.notification_required is True


def test_model_status_fields_are_inherited_and_payload_records_no_model_reason() -> None:
    repo = _repo_with_aggregation(
        _aggregation(
            "MRAG-model",
            invoked=False,
            reused=True,
            reused_run_id="MAR-OLD",
            expired=True,
            chain_status="partial_success",
            summary="model_review_expired partial_success",
        )
    )

    result, _ = _run(repo, "MRAG-model", confirm=True)
    model_payload = result.notification_payload_json["model_review"]

    assert result.model_review_invoked is False
    assert result.model_review_reused is True
    assert result.reused_model_analysis_run_id == "MAR-OLD"
    assert result.model_review_expired is True
    assert result.model_review_chain_status == "partial_success"
    assert model_payload["no_model_invocation_reason"]
    assert model_payload["reused_notice"]["reused_model_analysis_run_id"] == "MAR-OLD"
    assert "expired_notice" in model_payload
    assert "partial_success_notice" in model_payload


def test_high_risk_does_not_create_active_trade_setup() -> None:
    repo = _repo_with_aggregation(
        _aggregation(
            "MRAG-high-risk",
            risk="unacceptable",
            conflict="high",
            summary="create_new_advice conditional_trade bullish high risk",
        )
    )

    result, _ = _run(repo, "MRAG-high-risk", confirm=True)

    assert result.trade_setup_count == 0
    assert repo.trade_setups == []
    assert result.trade_permission == TradePermission.NOT_ALLOWED


def test_trade_setup_can_be_persisted_for_conditional_advice() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-setup", summary="create_new_advice conditional_trade bullish"))

    result, _ = _run(repo, "MRAG-setup", confirm=True)

    assert result.trade_setup_count == 1
    assert len(repo.trade_setups) == 1
    setup = repo.trade_setups[0]
    assert setup.advice_id == result.advice_id
    assert setup.permission == TradePermission.CONDITIONALLY_ALLOWED
    assert setup.entry_zone_json["price_generated"] is False
    assert setup.stop_loss_json["price_generated"] is False


def test_dry_run_does_not_write_rows() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-dry", summary="create_new_advice conditional_trade bullish"))

    result, session = _run(repo, "MRAG-dry", confirm=False)

    assert result.status == StrategyAdviceServiceStatus.SUCCESS
    assert result.dry_run is True
    assert repo.created_advice == []
    assert repo.lifecycle_reviews == []
    assert repo.events == []
    assert repo.trade_setups == []
    assert session.commit_count == 0


def test_confirm_write_persists_advice_review_event_and_setup_rows() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-write", summary="create_new_advice conditional_trade bullish"))

    result, session = _run(repo, "MRAG-write", confirm=True)

    assert result.created_advice_count == 1
    assert result.lifecycle_review_count == 1
    assert result.event_count >= 3
    assert result.trade_setup_count == 1
    assert len(repo.created_advice) == 1
    assert len(repo.lifecycle_reviews) == 1
    assert len(repo.events) >= 3
    assert len(repo.trade_setups) == 1
    assert session.commit_count == 1


def test_boundary_fields_are_false() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-boundary", summary="create_new_advice conditional_trade bullish"))

    result, _ = _run(repo, "MRAG-boundary", confirm=True)
    advice_payload = repo.created_advice[0]

    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False
    assert advice_payload.is_trading_signal is False
    assert advice_payload.is_executable is False
    assert advice_payload.auto_trading_allowed is False
    assert result.notification_payload_json["boundaries"]["is_executable"] is False
    assert result.notification_payload_json["boundaries"]["auto_trading_allowed"] is False


def test_service_does_not_call_model_send_hermes_or_scheduler() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-safe", summary="create_new_advice conditional_trade bullish"))

    result, _ = _run(repo, "MRAG-safe", confirm=True)

    assert result.details["stage21a_calls_model"] is False
    assert result.details["stage21a_sends_hermes"] is False
    assert result.notification_payload_json["boundaries"]["stage21a_calls_model"] is False
    assert result.notification_payload_json["boundaries"]["stage21a_sends_hermes"] is False


def test_scheduler_trigger_source_is_rejected() -> None:
    repo = _repo_with_aggregation(_aggregation("MRAG-scheduler", summary="create_new_advice conditional_trade bullish"))
    service = StrategyAdviceService(repository=repo)

    result = service.run_strategy_advice(
        FakeSession(),
        request=StrategyAdviceRequest(
            review_aggregation_run_id="MRAG-scheduler",
            trigger_source="scheduler",
            dry_run=True,
            confirm_write=False,
        ),
    )

    assert result.status == StrategyAdviceServiceStatus.FAILED
    assert result.error_code == "invalid_request"
    assert repo.created_advice == []


def _run(repo: FakeStrategyAdviceRepository, review_id: str, *, confirm: bool) -> tuple[Any, FakeSession]:
    session = FakeSession()
    service = StrategyAdviceService(repository=repo)
    result = service.run_strategy_advice(
        session,
        request=StrategyAdviceRequest(
            review_aggregation_run_id=review_id,
            trigger_source=TRIGGER_SOURCE_CLI,
            dry_run=not confirm,
            confirm_write=confirm,
            created_by="pytest",
            trace_id=f"trace-{review_id}",
        ),
    )
    return result, session


def _repo_with_aggregation(aggregation: Any) -> FakeStrategyAdviceRepository:
    repo = FakeStrategyAdviceRepository()
    repo.aggregations[aggregation.review_aggregation_run_id] = aggregation
    return repo


def _repo_with_active() -> FakeStrategyAdviceRepository:
    repo = FakeStrategyAdviceRepository()
    repo.active_advice = SimpleNamespace(
        advice_id="ADV-ACTIVE",
        advice_code="20260522-BTCUSDT-04-v1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        parent_advice_id=None,
        root_advice_id="ADV-ACTIVE",
        previous_advice_id=None,
        advice_path="ADV-ACTIVE",
        version_no=1,
        advice_status="active",
        advice_action="conditional_trade",
        directional_bias="bullish",
        trade_permission="conditionally_allowed",
        summary_text="existing active advice",
        risk_summary_json=json.dumps({"risk_acceptability": "acceptable", "risk_blocked": False}, sort_keys=True),
        strategy_summary_json=json.dumps(
            {
                "review_decision": "conditional_trade",
                "evidence_quality": "sufficient",
                "strategy_conflict": "low",
                "allowed_advice_mode": "conditional_trade",
            },
            sort_keys=True,
        ),
        model_summary_json=json.dumps(
            {
                "model_review_invocation_mode": "none",
                "model_review_reused": False,
                "model_review_basis": "current_model_review",
                "model_review_expired": False,
                "model_review_chain_status": "success",
            },
            sort_keys=True,
        ),
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
    )
    return repo


def _aggregation(
    review_id: str,
    *,
    summary: str,
    risk: str = "acceptable",
    conflict: str = "low",
    evidence: str = "sufficient",
    invoked: bool = False,
    reused: bool = False,
    reused_run_id: str | None = None,
    expired: bool = False,
    chain_status: str = "success",
) -> Any:
    return SimpleNamespace(
        review_aggregation_run_id=review_id,
        material_pack_id=f"AMP-{review_id}",
        aggregation_run_id=f"AGR-{review_id}",
        strategy_signal_run_id=f"SIG-{review_id}",
        snapshot_id=f"SNAP-{review_id}",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        status="success",
        model_review_invoked=invoked,
        model_review_invocation_mode="worker_real_model" if invoked else ("reused" if reused else "none"),
        model_review_reused=reused,
        reused_model_analysis_run_id=reused_run_id,
        model_review_skip_reason="no model called in this 21A test",
        model_review_block_reason=None,
        invoked_model_keys_json=json.dumps(["mock_review"] if invoked else [], sort_keys=True),
        invoked_model_roles_json=json.dumps(["review_gate"] if invoked else [], sort_keys=True),
        model_review_chain_status=chain_status,
        latest_model_review_at_utc=CREATED_AT,
        model_review_basis="reused_model_review" if reused else "current_model_review",
        model_review_expired=expired,
        review_decision_summary=summary,
        evidence_quality_summary=evidence,
        risk_acceptability_summary=risk,
        strategy_conflict_summary=conflict,
        allowed_advice_mode="conditional_trade" if "conditional_trade" in summary else "wait_only",
        directional_trade_allowed="conditional_trade" in summary,
        risk_warnings_json=json.dumps(["risk may expand"], sort_keys=True),
        missing_evidence_json=json.dumps([], sort_keys=True),
        summary_text=summary,
        created_at_utc=CREATED_AT,
    )


def _row_from_payload(payload: Any) -> Any:
    values = {}
    for field_name, value in payload.__dict__.items():
        if hasattr(value, "value"):
            values[field_name] = value.value
        else:
            values[field_name] = value
    return SimpleNamespace(**values)
