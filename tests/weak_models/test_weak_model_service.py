from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.weak_models.base import BaseWeakModel
from app.weak_models.repository import WeakModelRepository
from app.weak_models.service import WeakModelService
from app.weak_models.types import (
    WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT,
    WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS,
    WeakModelAggregationSummary,
    WeakModelOutput,
    WeakModelProfile,
    WeakModelResultStatus,
    WeakModelRole,
    WeakModelRunRequest,
    WeakModelRunStatus,
)


def test_service_dry_run_does_not_write_and_uses_ssr_bound_snapshot() -> None:
    repo = FakeWeakModelRepository()
    service = WeakModelService(repository=repo, registry=_registry_with_default_models())

    result = service.run_weak_models_for_strategy_signal(FakeSession(), _request(dry_run=True, confirm_write=False))

    assert result.status == WeakModelRunStatus.DRY_RUN
    assert result.database_written is False
    assert result.snapshot_id == "MCS-1"
    assert result.model_count_executed == 2
    assert repo.run_payloads == []
    assert repo.result_payloads == []
    assert repo.restore_called == 1


def test_confirm_write_persists_run_results_and_aggregation() -> None:
    repo = FakeWeakModelRepository()
    session = FakeSession()
    service = WeakModelService(repository=repo, registry=_registry_with_default_models())

    result = service.run_weak_models_for_strategy_signal(session, _request(dry_run=False, confirm_write=True))

    assert result.status == WeakModelRunStatus.SUCCESS
    assert result.database_written is True
    assert session.commits == 1
    assert len(repo.run_payloads) == 1
    assert len(repo.result_payloads) == 2
    assert len(repo.aggregation_payloads) == 1
    assert repo.run_payloads[0].run_status == "success"
    assert repo.result_payloads[0].input_data.snapshot_id == "MCS-1"
    assert repo.result_payloads[0].output.input_summary["source"] == "test"
    assert repo.result_payloads[0].output.evidence["evidence"] == "present"


def test_snapshot_id_missing_blocks_and_does_not_choose_another_snapshot() -> None:
    repo = FakeWeakModelRepository()
    repo.ssr.snapshot_id = None
    service = WeakModelService(repository=repo, registry=_registry_with_default_models())

    result = service.run_weak_models_for_strategy_signal(FakeSession(), _request(dry_run=True, confirm_write=False))

    assert result.status == WeakModelRunStatus.BLOCKED
    assert result.error_code == WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT
    assert repo.snapshot_lookup_count == 0
    assert repo.restore_called == 0
    assert repo.run_payloads == []


def test_non_success_strategy_signal_run_status_blocks_before_snapshot_lookup() -> None:
    repo = FakeWeakModelRepository()
    repo.ssr.status = "partial_success"
    service = WeakModelService(repository=repo, registry=_registry_with_default_models())

    result = service.run_weak_models_for_strategy_signal(FakeSession(), _request(dry_run=True, confirm_write=False))

    assert result.status == WeakModelRunStatus.BLOCKED
    assert result.error_code == WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS
    assert repo.snapshot_lookup_count == 0
    assert repo.restore_called == 0
    assert result.outputs == ()


def test_snapshot_slot_mismatch_blocks() -> None:
    repo = FakeWeakModelRepository()
    service = WeakModelService(repository=repo, registry=_registry_with_default_models())
    mismatched_slot = datetime(2026, 5, 30, 4, tzinfo=timezone.utc)

    result = service.run_weak_models_for_strategy_signal(
        FakeSession(),
        _request(dry_run=True, confirm_write=False, kline_slot_utc=mismatched_slot),
    )

    assert result.status == WeakModelRunStatus.BLOCKED
    assert result.error_code == WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT
    assert "kline_slot_utc mismatch" in str(result.error_message)
    assert repo.restore_called == 0


def test_observe_only_runs_and_persists_but_does_not_affect_aggregation() -> None:
    active_profile = _profile("active_direction", WeakModelRole.DIRECTIONAL.value, static_weight=0.10)
    observe_profile = _profile(
        "observe_direction",
        WeakModelRole.DIRECTIONAL.value,
        maturity_stage="observe_only",
        static_weight=0.0,
    )
    registry = FakeRegistry(
        profiles=(active_profile, observe_profile),
        models=(
            FixedWeakModel(
                active_profile,
                WeakModelOutput(
                    model_key="active_direction",
                    model_role="directional",
                    signal_score=0.0,
                    confidence=1.0,
                    static_weight=0.10,
                ),
            ),
            FixedWeakModel(
                observe_profile,
                WeakModelOutput(
                    model_key="observe_direction",
                    model_role="directional",
                    signal_score=1.0,
                    confidence=1.0,
                    static_weight=0.0,
                ),
            ),
        ),
    )
    repo = FakeWeakModelRepository()
    service = WeakModelService(repository=repo, registry=registry)

    result = service.run_weak_models_for_strategy_signal(FakeSession(), _request(dry_run=False, confirm_write=True))

    assert len(result.outputs) == 2
    assert result.aggregation is not None
    assert result.aggregation.directional_score == 0.0
    assert result.aggregation.details["observe_only_output_count"] == 1
    assert len(repo.result_payloads) == 2


def test_model_failure_is_recorded_as_partial_success_without_external_calls() -> None:
    good_profile = _profile("good_direction", WeakModelRole.DIRECTIONAL.value)
    bad_profile = _profile("bad_direction", WeakModelRole.DIRECTIONAL.value)
    registry = FakeRegistry(
        profiles=(good_profile, bad_profile),
        models=(
            FixedWeakModel(
                good_profile,
                WeakModelOutput(
                    model_key="good_direction",
                    model_role="directional",
                    signal_score=0.5,
                    confidence=1.0,
                    static_weight=0.10,
                ),
            ),
            RaisingWeakModel(bad_profile),
        ),
    )
    repo = FakeWeakModelRepository()
    service = WeakModelService(repository=repo, registry=registry)

    result = service.run_weak_models_for_strategy_signal(FakeSession(), _request(dry_run=False, confirm_write=True))

    assert result.status == WeakModelRunStatus.PARTIAL_SUCCESS
    assert result.model_count_failed == 1
    assert result.database_written is True
    assert repo.external_call_count == 0
    assert any(payload.output.status == WeakModelResultStatus.FAILED for payload in repo.result_payloads)


def test_repository_writes_veto_factors_json_as_dedicated_column() -> None:
    repo = WeakModelRepository(snapshot_repository=SimpleNamespace())
    row = SimpleNamespace()
    repo._get_aggregation = lambda db_session, weak_model_aggregation_id: row  # type: ignore[method-assign]

    repo.upsert_aggregation(
        FakeSession(),
        aggregation=WeakModelAggregationSummary(
            weak_model_aggregation_id="WMA-1",
            weak_model_run_id="WMR-1",
            pipeline_run_id="SP-1",
            strategy_signal_run_id="SSR-1",
            snapshot_id="MCS-1",
            symbol="BTCUSDT",
            base_interval="4h",
            higher_interval="1d",
            kline_slot_utc=datetime(2026, 5, 31, 4, tzinfo=timezone.utc),
            directional_score=0.0,
            directional_bias="neutral",
            directional_confidence=0.0,
            risk_level="extreme",
            trade_permission="block",
            veto_triggered=True,
            supporting_factors=(),
            opposing_factors=(),
            conflict_factors=(),
            low_confidence_factors=(),
            veto_factors=("volatility_risk_gate",),
            context_summary={"regime": "unknown"},
            summary_text="test",
            details={},
        ),
    )

    assert row.veto_factors_json == '["volatility_risk_gate"]'
    assert row.details_json == "{}"


class FixedWeakModel(BaseWeakModel):
    def __init__(self, profile: WeakModelProfile, output: WeakModelOutput) -> None:
        super().__init__(profile)
        self._output = output

    def evaluate(self, input_data: Any) -> WeakModelOutput:
        return self._output


class RaisingWeakModel(BaseWeakModel):
    def evaluate(self, input_data: Any) -> WeakModelOutput:
        raise RuntimeError("boom")


class FakeRegistry:
    def __init__(self, *, profiles: tuple[WeakModelProfile, ...], models: tuple[BaseWeakModel, ...]) -> None:
        self._profiles = profiles
        self._models = models

    def load_profiles(self) -> tuple[WeakModelProfile, ...]:
        return self._profiles

    def load_enabled_models(self) -> tuple[BaseWeakModel, ...]:
        return self._models


class FakeWeakModelRepository:
    def __init__(self) -> None:
        self.slot = datetime(2026, 5, 31, 4, tzinfo=timezone.utc)
        self.ssr = SimpleNamespace(
            run_id="SSR-1",
            snapshot_id="MCS-1",
            symbol="BTCUSDT",
            base_interval_value="4h",
            higher_interval_value="1d",
            status="success",
        )
        self.snapshot = SimpleNamespace(
            snapshot_id="MCS-1",
            symbol="BTCUSDT",
            base_interval_value="4h",
            higher_interval_value="1d",
            status="created",
            latest_4h_open_time_utc=self.slot,
        )
        self.run_payloads: list[Any] = []
        self.result_payloads: list[Any] = []
        self.aggregation_payloads: list[Any] = []
        self.snapshot_lookup_count = 0
        self.restore_called = 0
        self.external_call_count = 0

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any:
        return self.ssr if run_id == "SSR-1" else None

    def get_snapshot_by_snapshot_id(self, db_session: Any, *, snapshot_id: str) -> Any:
        self.snapshot_lookup_count += 1
        return self.snapshot if snapshot_id == "MCS-1" else None

    def restore_snapshot_kline_windows(self, db_session: Any, *, snapshot_id: str) -> Any:
        self.restore_called += 1
        return SimpleNamespace(
            rows_4h=_rows(count=120, start_price=60000, step=20, hours=4),
            rows_1d=_rows(count=80, start_price=58000, step=60, hours=24),
        )

    def upsert_run(self, db_session: Any, *, payload: Any) -> tuple[Any, str]:
        self.run_payloads.append(payload)
        return SimpleNamespace(), "created"

    def upsert_result(self, db_session: Any, *, payload: Any) -> tuple[Any, str]:
        self.result_payloads.append(payload)
        return SimpleNamespace(), "created"

    def upsert_aggregation(self, db_session: Any, *, aggregation: Any) -> tuple[Any, str]:
        self.aggregation_payloads.append(aggregation)
        return SimpleNamespace(), "created"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _registry_with_default_models() -> FakeRegistry:
    directional = _profile("directional_model", WeakModelRole.DIRECTIONAL.value)
    risk = _profile("risk_model", WeakModelRole.RISK.value)
    return FakeRegistry(
        profiles=(directional, risk),
        models=(
            FixedWeakModel(
                directional,
                WeakModelOutput(
                    model_key="directional_model",
                    model_role="directional",
                    signal_score=0.5,
                    confidence=0.8,
                    static_weight=0.10,
                    input_summary={"source": "test"},
                    evidence={"evidence": "present"},
                ),
            ),
            FixedWeakModel(
                risk,
                WeakModelOutput(
                    model_key="risk_model",
                    model_role="risk",
                    risk_score=0.2,
                    risk_level="low",
                    trade_permission="allow",
                    confidence=0.6,
                    static_weight=0.10,
                ),
            ),
        ),
    )


def _request(
    *,
    dry_run: bool,
    confirm_write: bool,
    kline_slot_utc: datetime | None = None,
) -> WeakModelRunRequest:
    return WeakModelRunRequest(
        strategy_signal_run_id="SSR-1",
        pipeline_run_id="SP-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=kline_slot_utc,
        dry_run=dry_run,
        confirm_write=confirm_write,
        trace_id="trace1234567890",
    )


def _rows(*, count: int, start_price: int, step: int, hours: int) -> tuple[SimpleNamespace, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[SimpleNamespace] = []
    for index in range(count):
        close = start_price + index * step
        rows.append(
            SimpleNamespace(
                open_time_ms=index * hours * 60 * 60 * 1000,
                open_time_utc=start + timedelta(hours=index * hours),
                open_price=close - 10,
                high_price=close + 120,
                low_price=close - 120,
                close_price=close,
            )
        )
    return tuple(rows)


def _profile(
    model_key: str,
    role: str,
    *,
    maturity_stage: str = "active",
    static_weight: float = 0.10,
) -> WeakModelProfile:
    return WeakModelProfile(
        model_key=model_key,
        model_name=model_key,
        enabled=True,
        maturity_stage=maturity_stage,
        model_role=role,
        model_version="v1",
        config_version="test",
        config_hash="hash",
        input_intervals=("4h", "1d"),
        input_window={"base_interval_limit": 120, "higher_interval_limit": 80},
        static_weight=static_weight,
        description="test",
        params={},
    )
