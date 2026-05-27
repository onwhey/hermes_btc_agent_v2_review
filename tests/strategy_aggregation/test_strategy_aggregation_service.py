from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.strategy.aggregation.service import StrategyAggregationService
from app.strategy.aggregation.types import (
    AnalysisHypothesisDirection,
    ConflictLevel,
    MATERIAL_SCHEMA_VERSION,
    RiskGateStatus,
    StrategyAggregationRequest,
    StrategyAggregationStatus,
)
from scripts import run_strategy_aggregation as aggregation_cli


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeAggregationRepository:
    def __init__(
        self,
        *,
        strategy_run: Any | None = None,
        results: tuple[Any, ...] = (),
        restored_snapshot: Any | None = None,
        existing: Any | None = None,
        existing_after_unique_conflict: Any | None = None,
        evidence_aggregation: Any | None = None,
        create_material_pack_error: Exception | None = None,
    ) -> None:
        self.strategy_run = strategy_run
        self.results = results
        self.restored_snapshot = restored_snapshot
        self.existing = existing
        self.existing_after_unique_conflict = existing_after_unique_conflict
        self.evidence_aggregation = evidence_aggregation
        self.create_material_pack_error = create_material_pack_error
        self.unique_conflict_raised = False
        self.aggregation_rows: list[Any] = []
        self.material_rows: list[Any] = []
        self.restore_calls: list[str] = []
        self.result_calls = 0

    def get_existing_aggregation(self, _db_session: Any, **kwargs: Any) -> Any | None:
        candidate = self.existing
        if candidate is None and self.unique_conflict_raised:
            candidate = self.existing_after_unique_conflict
        statuses = kwargs.get("statuses")
        if candidate is not None and statuses is not None and getattr(candidate, "status", None) not in statuses:
            return None
        expected_material_schema_version = kwargs.get("material_schema_version")
        candidate_material_schema_version = getattr(candidate, "material_schema_version", None)
        if (
            candidate is not None
            and candidate_material_schema_version is not None
            and expected_material_schema_version is not None
            and candidate_material_schema_version != expected_material_schema_version
        ):
            return None
        return candidate

    def get_strategy_signal_run(self, _db_session: Any, *, run_id: str) -> Any | None:
        if self.strategy_run and self.strategy_run.run_id == run_id:
            return self.strategy_run
        return None

    def list_strategy_signal_results(self, _db_session: Any, *, run_id: str) -> tuple[Any, ...]:
        self.result_calls += 1
        return self.results if self.strategy_run and self.strategy_run.run_id == run_id else ()

    def restore_snapshot_kline_windows(self, _db_session: Any, *, snapshot_id: str) -> Any:
        self.restore_calls.append(snapshot_id)
        if self.restored_snapshot is None:
            raise RuntimeError("missing snapshot")
        return self.restored_snapshot

    def get_material_pack_by_aggregation_run_id(self, _db_session: Any, *, aggregation_run_id: str) -> Any | None:
        for row in self.material_rows:
            if row.aggregation_run_id == aggregation_run_id:
                return row
        return None

    def get_latest_strategy_evidence_aggregation(self, _db_session: Any, *, strategy_signal_run_id: str) -> Any | None:
        if self.evidence_aggregation is None:
            return None
        if self.evidence_aggregation.strategy_signal_run_id != strategy_signal_run_id:
            return None
        return self.evidence_aggregation

    def create_aggregation_run(self, _db_session: Any, *, payload: Any) -> Any:
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.aggregation_rows) + 1
        self.aggregation_rows.append(row)
        return row

    def create_material_pack(self, _db_session: Any, *, payload: Any) -> Any:
        if self.create_material_pack_error is not None:
            self.unique_conflict_raised = True
            raise self.create_material_pack_error
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.material_rows) + 1
        self.material_rows.append(row)
        return row

    def record_hermes_result(self, _db_session: Any, aggregation_row: Any, **kwargs: Any) -> Any:
        for key, value in kwargs.items():
            setattr(aggregation_row, key, value)
        return aggregation_row


class FakeAlertSender:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, event: Any, **_kwargs: Any) -> AlertSendResult:
        self.calls.append(event)
        return AlertSendResult(
            status=AlertSendStatus.SUBMITTED_TO_HERMES,
            message="submitted",
            submitted_at_utc=utc_at(18, 12),
            attempted_real_send=True,
        )


def utc_at(day: int, hour: int) -> datetime:
    return datetime(2026, 5, day, hour, tzinfo=timezone.utc)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def kline_rows(count: int, *, interval_ms: int = 14_400_000, interval_value: str = "4h") -> tuple[Any, ...]:
    start_ms = ms(datetime(2026, 5, 1, tzinfo=timezone.utc))
    pattern = [Decimal("100"), Decimal("150"), Decimal("220"), Decimal("170"), Decimal("130")]
    rows: list[Any] = []
    for index in range(count):
        cycle = index // len(pattern)
        close = Decimal("60000") + Decimal(cycle * 260) + pattern[index % len(pattern)]
        open_time_ms = start_ms + index * interval_ms
        rows.append(
            SimpleNamespace(
                symbol="BTCUSDT",
                interval_value=interval_value,
                open_time_ms=open_time_ms,
                open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
                open_price=close - Decimal("8"),
                high_price=close + Decimal("40"),
                low_price=close - Decimal("45"),
                close_price=close,
                volume=Decimal("1000") + Decimal(index),
            )
        )
    return tuple(rows)


def restored_snapshot(*, future_base_row: bool = False) -> Any:
    rows_4h = list(kline_rows(40, interval_ms=14_400_000, interval_value="4h"))
    rows_1d = list(kline_rows(10, interval_ms=86_400_000, interval_value="1d"))
    snapshot_end_4h = rows_4h[-1].open_time_ms
    if future_base_row:
        future = SimpleNamespace(**rows_4h[-1].__dict__)
        future.open_time_ms = rows_4h[-1].open_time_ms + 14_400_000
        future.open_time_utc = datetime.fromtimestamp(future.open_time_ms / 1000, tz=timezone.utc)
        rows_4h.append(future)
    snapshot = SimpleNamespace(
        snapshot_id="MCS-stage18",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        start_4h_open_time_ms=rows_4h[0].open_time_ms,
        end_4h_open_time_ms=snapshot_end_4h,
        start_1d_open_time_ms=rows_1d[0].open_time_ms,
        end_1d_open_time_ms=rows_1d[-1].open_time_ms,
        actual_4h_count=40,
        actual_1d_count=10,
    )
    return SimpleNamespace(snapshot=snapshot, rows_4h=tuple(rows_4h), rows_1d=tuple(rows_1d))


def strategy_run(*, status: str = "success", run_id: str = "SSR-stage18", snapshot_id: str | None = "MCS-stage18") -> Any:
    return SimpleNamespace(
        run_id=run_id,
        snapshot_id=snapshot_id,
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        status=status,
        strategy_count=3,
        success_count=2,
        failed_count=0,
        invalid_count=0,
        not_implemented_count=1,
    )


def fake_stage16_signal(
    name: str,
    *,
    status: str = "success",
    direction: str = "neutral",
    risk: str = "medium",
    strength: str = "0.50",
) -> Any:
    return SimpleNamespace(
        id=len(name),
        strategy_name=name,
        strategy_version="v1",
        strategy_status=status,
        direction_bias=direction,
        risk_level=risk,
        signal_strength=Decimal(strength),
        reason_codes_json=json.dumps([f"{name}_code"], ensure_ascii=False),
        reason_text=f"{name} fake stage16 signal row.",
        metrics_json=json.dumps({"metric": "1"}, ensure_ascii=False),
        debug_json="{}",
        error_message=None,
    )


def service_with_repo(repo: FakeAggregationRepository, *, settings: AppSettings | None = None, alert: FakeAlertSender | None = None) -> StrategyAggregationService:
    return StrategyAggregationService(settings=settings or AppSettings(), repository=repo, alert_sender=alert or FakeAlertSender())


def run_request(*, confirm: bool = False) -> StrategyAggregationRequest:
    return StrategyAggregationRequest(
        strategy_signal_run_id="SSR-stage18",
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
        trace_id="trace-stage18",
    )


def test_success_and_partial_strategy_signal_runs_can_project_long_hypothesis() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(status="partial_success"),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),
            fake_stage16_signal("fixture_risk_projection", direction="not_applicable", risk="medium", strength="0.30"),
            fake_stage16_signal(
                "fixture_not_implemented_placeholder",
                status="not_implemented",
                direction="not_applicable",
                risk="not_applicable",
            ),
        ),
        restored_snapshot=restored_snapshot(),
    )
    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request())

    assert result.status == StrategyAggregationStatus.PARTIAL_SUCCESS
    assert result.analysis_hypothesis_direction == AnalysisHypothesisDirection.LONG
    assert result.risk_gate_status in {RiskGateStatus.PASS, RiskGateStatus.CAUTION}
    assert result.effective_strategy_count == 2


def test_material_schema_version_is_v2_after_strategy_evidence_shape_change() -> None:
    assert MATERIAL_SCHEMA_VERSION == "material_schema_v2"


def test_blocked_and_failed_strategy_signal_runs_are_not_allowed() -> None:
    for status in ("blocked", "failed"):
        repo = FakeAggregationRepository(strategy_run=strategy_run(status=status))
        result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request())

        assert result.status == StrategyAggregationStatus.BLOCKED
        assert result.error_code == "strategy_signal_run_status_not_allowed"
        assert repo.restore_calls == []


def test_empty_results_and_missing_snapshot_are_blocked() -> None:
    missing_snapshot_repo = FakeAggregationRepository(strategy_run=strategy_run(snapshot_id=None))
    empty_results_repo = FakeAggregationRepository(strategy_run=strategy_run(), results=())

    missing_snapshot = service_with_repo(missing_snapshot_repo).run_strategy_aggregation(FakeSession(), request=run_request())
    empty_results = service_with_repo(empty_results_repo).run_strategy_aggregation(FakeSession(), request=run_request())

    assert missing_snapshot.status == StrategyAggregationStatus.BLOCKED
    assert missing_snapshot.error_code == "strategy_signal_run_snapshot_missing"
    assert empty_results.status == StrategyAggregationStatus.BLOCKED
    assert empty_results.error_code == "strategy_signal_result_empty"


def test_short_hypothesis_projection_and_conflict_level_rules() -> None:
    short_repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_short", direction="bearish_bias", risk="low", strength="0.80"),
            fake_stage16_signal("fixture_risk_projection", direction="not_applicable", risk="low", strength="0.20"),
        ),
        restored_snapshot=restored_snapshot(),
    )
    conflict_repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.70"),
            fake_stage16_signal("fixture_direction_hypothesis_short", direction="bearish_bias", risk="low", strength="0.72"),
        ),
        restored_snapshot=restored_snapshot(),
    )

    short_result = service_with_repo(short_repo).run_strategy_aggregation(FakeSession(), request=run_request())
    conflict_result = service_with_repo(conflict_repo).run_strategy_aggregation(FakeSession(), request=run_request())

    assert short_result.analysis_hypothesis_direction == AnalysisHypothesisDirection.SHORT
    assert conflict_result.conflict_level == ConflictLevel.HIGH
    assert conflict_result.analysis_hypothesis_direction == AnalysisHypothesisDirection.WAIT


def test_extreme_risk_blocks_direction_to_wait_or_stop_trading() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.90"),
            fake_stage16_signal("fixture_risk_projection", direction="not_applicable", risk="extreme", strength="0.90"),
        ),
        restored_snapshot=restored_snapshot(),
    )

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request())

    assert result.risk_gate_status == RiskGateStatus.BLOCKED_BY_VOLATILITY
    assert result.analysis_hypothesis_direction in {AnalysisHypothesisDirection.WAIT, AnalysisHypothesisDirection.STOP_TRADING}


def test_confirm_write_persists_aggregation_and_material_pack_with_required_sections() -> None:
    session = FakeSession()
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(status="partial_success"),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),
            fake_stage16_signal("fixture_risk_projection", direction="not_applicable", risk="medium", strength="0.30"),
            fake_stage16_signal(
                "fixture_not_implemented_placeholder",
                status="not_implemented",
                direction="not_applicable",
                risk="not_applicable",
            ),
        ),
        restored_snapshot=restored_snapshot(),
    )

    result = service_with_repo(repo).run_strategy_aggregation(session, request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.PARTIAL_SUCCESS
    assert session.commits >= 1
    assert len(repo.aggregation_rows) == 1
    assert len(repo.material_rows) == 1
    aggregation = repo.aggregation_rows[0]
    assert aggregation.material_schema_version == "material_schema_v2"
    assert aggregation.analysis_hypothesis_direction == "long"
    assert aggregation.analysis_hypothesis_semantics == "analysis_hypothesis_only"
    assert aggregation.direction_projection_source == "fixture_or_existing_signal_projection"
    assert aggregation.is_strategy_signal is False
    assert aggregation.is_trading_advice is False
    assert aggregation.is_executable is False
    assert aggregation.strategy_logic_implemented is False
    assert aggregation.promotion_allowed is False
    assert aggregation.promotion_requires_future_strategy_and_llm_stage is True
    assert not hasattr(aggregation, "candidate_direction")
    material = repo.material_rows[0].material_json
    assert repo.material_rows[0].material_schema_version == "material_schema_v2"
    assert material["material_schema_version"] == "material_schema_v2"
    assert material["swing"]["recent_swing_highs"]
    assert material["swing"]["recent_swing_lows"]
    assert material["volatility"]["atr_14"] is not None
    assert material["volatility"]["atr_percent"] is not None
    assert material["volatility"]["avg_range_percent_3"] is not None
    assert material["volatility"]["avg_range_percent_6"] is not None
    assert material["volatility"]["avg_range_percent_20"] is not None
    assert material["support_resistance"]["support_candidates"]
    assert material["support_resistance"]["resistance_candidates"]
    assert material["strategy_evidence"]["source"] == "legacy_strategy_results"
    assert material["strategy_evidence"]["aggregation_id"] is None
    assert "23F aggregation not found" in material["strategy_evidence"]["warning"]
    scenario = repo.aggregation_rows[0].candidate_scenarios_json["candidate_scenarios"][0]
    assert scenario["scenario_type"] == "long_hypothesis"
    assert scenario["activation_check"]
    assert scenario["validation_plan"]["activation_check"]
    assert repo.material_rows[0].question_json["questions"]
    assert repo.material_rows[0].data_window_json["base_kline_count"] == 40
    assert repo.material_rows[0].future_leakage_guard_json["uses_future_klines"] is False


def test_confirm_write_material_pack_prefers_23f_strategy_evidence() -> None:
    evidence_row = SimpleNamespace(
        aggregation_id="SEA-stage18",
        strategy_signal_run_id="SSR-stage18",
        status="success",
        candidate_bias="wait",
        candidate_confidence=Decimal("0.7200"),
        decision_readiness="wait_for_confirmation",
        strategy_evidence_summary_json=json.dumps({"summary": "23F evidence"}, ensure_ascii=False),
        decision_source_chain_json=json.dumps([{"strategy_name": "risk_gate"}], ensure_ascii=False),
        role_coverage_matrix_json=json.dumps({"risk_control": {"present": True}}, ensure_ascii=False),
        evidence_missing_json=json.dumps([], ensure_ascii=False),
        strategy_conflict_summary_json=json.dumps({"conflicts": []}, ensure_ascii=False),
        participation_summary_json=json.dumps({"decision_participant": 2}, ensure_ascii=False),
        observe_only_summary_json=json.dumps({"items": []}, ensure_ascii=False),
        risk_gate_summary_json=json.dumps({"formal_veto_applied": False}, ensure_ascii=False),
        model_review_focus_json=json.dumps({"review_points": ["review 23F evidence chain"]}, ensure_ascii=False),
        not_trading_advice=True,
    )
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),
        ),
        restored_snapshot=restored_snapshot(),
        evidence_aggregation=evidence_row,
    )

    service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    material = repo.material_rows[0].material_json
    assert repo.material_rows[0].material_schema_version == "material_schema_v2"
    assert material["material_schema_version"] == "material_schema_v2"
    strategy_evidence = material["strategy_evidence"]
    assert strategy_evidence["source"] == "strategy_evidence_aggregation_result"
    assert strategy_evidence["aggregation_id"] == "SEA-stage18"
    assert strategy_evidence["strategy_signal_run_id"] == "SSR-stage18"
    assert strategy_evidence["candidate_bias"] == "wait"
    assert strategy_evidence["decision_readiness"] == "wait_for_confirmation"
    assert strategy_evidence["strategy_evidence_summary"] == {"summary": "23F evidence"}
    assert strategy_evidence["decision_source_chain"] == [{"strategy_name": "risk_gate"}]
    assert strategy_evidence["model_review_focus"] == {"review_points": ["review 23F evidence chain"]}
    assert material["support_resistance"]["support_candidates"]


def test_direction_hypothesis_outputs_are_not_strategy_signals_or_advice() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),
        ),
        restored_snapshot=restored_snapshot(),
    )

    service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    scenario = repo.aggregation_rows[0].candidate_scenarios_json["candidate_scenarios"][0]
    assert scenario["scenario_type"] == "long_hypothesis"
    assert scenario["scenario_semantics"] == "analysis_hypothesis_only"
    assert scenario["source"] == "fixture_or_existing_signal_projection"
    assert scenario["is_strategy_signal"] is False
    assert scenario["is_trading_advice"] is False
    assert scenario["is_executable"] is False
    assert scenario["strategy_logic_implemented"] is False
    assert scenario["promotion_allowed"] is False
    assert scenario["promotion_requires_future_strategy_and_llm_stage"] is True
    assert scenario["direction_projection_source"] == "fixture_or_existing_signal_projection"
    assert scenario["stop_trading_source"] is None


def test_no_upstream_direction_fixture_defaults_to_wait_hypothesis() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_neutral_projection", direction="neutral", risk="low", strength="0.10"),
            fake_stage16_signal("fixture_risk_projection", direction="not_applicable", risk="medium", strength="0.20"),
        ),
        restored_snapshot=restored_snapshot(),
    )

    service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    scenario = repo.aggregation_rows[0].candidate_scenarios_json["candidate_scenarios"][0]
    assert scenario["scenario_type"] == "wait_hypothesis"
    assert scenario["hypothesis_direction"] == "wait"
    assert scenario["is_strategy_signal"] is False
    assert scenario["is_trading_advice"] is False
    assert scenario["strategy_logic_implemented"] is False


def test_stop_trading_hypothesis_keeps_explicit_risk_gate_projection_source() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(
            fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),
            fake_stage16_signal("fixture_risk_gate_projection", direction="not_applicable", risk="extreme", strength="0.90"),
        ),
        restored_snapshot=restored_snapshot(),
    )

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    scenario = repo.aggregation_rows[0].candidate_scenarios_json["candidate_scenarios"][0]
    assert result.analysis_hypothesis_direction == AnalysisHypothesisDirection.STOP_TRADING
    assert result.stop_trading_source == "upstream_risk_gate_projection"
    assert scenario["scenario_type"] == "stop_trading_hypothesis"
    assert scenario["stop_trading_source"] == "upstream_risk_gate_projection"
    assert scenario["risk_gate_projection_source"] == "upstream_risk_gate_projection"
    assert scenario["is_trading_advice"] is False


def test_future_leakage_guard_blocks_rows_after_snapshot_window() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(future_base_row=True),
    )

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request())

    assert result.status == StrategyAggregationStatus.BLOCKED
    assert result.error_code == "future_leakage_guard_failed"
    assert repo.aggregation_rows == []


def test_same_strategy_signal_run_version_is_skipped_when_existing() -> None:
    existing = SimpleNamespace(
        aggregation_run_id="SAR-existing",
        snapshot_id="MCS-stage18",
        status="success",
        material_schema_version=MATERIAL_SCHEMA_VERSION,
        analysis_hypothesis_direction="long",
        risk_level="low",
        risk_gate_status="pass",
        conflict_level="low",
        input_strategy_count=2,
        input_success_count=2,
        input_failed_count=0,
        input_invalid_count=0,
        input_not_implemented_count=0,
        effective_strategy_count=2,
    )
    repo = FakeAggregationRepository(strategy_run=strategy_run(), existing=existing)

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.SKIPPED
    assert result.aggregation_run_id == "SAR-existing"
    assert repo.result_calls == 0
    assert result.details["skip_reason"] == "already_exists"


def test_existing_v1_material_pack_does_not_skip_v2_generation() -> None:
    existing_v1 = SimpleNamespace(
        aggregation_run_id="SAR-existing-v1",
        snapshot_id="MCS-stage18",
        status="success",
        material_schema_version="material_schema_v1",
        analysis_hypothesis_direction="long",
        risk_level="low",
        risk_gate_status="pass",
        conflict_level="low",
        input_strategy_count=2,
        input_success_count=2,
        input_failed_count=0,
        input_invalid_count=0,
        input_not_implemented_count=0,
        effective_strategy_count=2,
    )
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(),
        existing=existing_v1,
    )

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.SUCCESS
    assert result.aggregation_run_id != "SAR-existing-v1"
    assert len(repo.aggregation_rows) == 1
    assert len(repo.material_rows) == 1
    assert repo.aggregation_rows[0].material_schema_version == MATERIAL_SCHEMA_VERSION
    assert repo.material_rows[0].material_schema_version == MATERIAL_SCHEMA_VERSION
    assert repo.material_rows[0].material_json["strategy_evidence"]["source"] == "legacy_strategy_results"


def test_existing_v2_material_pack_is_idempotently_skipped() -> None:
    existing_v2 = SimpleNamespace(
        aggregation_run_id="SAR-existing-v2",
        snapshot_id="MCS-stage18",
        status="success",
        material_schema_version=MATERIAL_SCHEMA_VERSION,
        analysis_hypothesis_direction="long",
        risk_level="low",
        risk_gate_status="pass",
        conflict_level="low",
        input_strategy_count=2,
        input_success_count=2,
        input_failed_count=0,
        input_invalid_count=0,
        input_not_implemented_count=0,
        effective_strategy_count=2,
    )
    repo = FakeAggregationRepository(strategy_run=strategy_run(), existing=existing_v2)

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.SKIPPED
    assert result.aggregation_run_id == "SAR-existing-v2"
    assert repo.result_calls == 0
    assert repo.aggregation_rows == []
    assert repo.material_rows == []


def test_blocked_and_failed_existing_attempts_do_not_lock_later_success_rerun() -> None:
    for existing_status in ("blocked", "failed"):
        existing_attempt = SimpleNamespace(
            aggregation_run_id=f"SAR-{existing_status}",
            snapshot_id="MCS-stage18",
            status=existing_status,
        )
        repo = FakeAggregationRepository(
            strategy_run=strategy_run(),
            results=(
                fake_stage16_signal(
                    "fixture_direction_hypothesis_long",
                    direction="bullish_bias",
                    risk="low",
                    strength="0.80",
                ),
            ),
            restored_snapshot=restored_snapshot(),
            existing=existing_attempt,
        )

        result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

        assert result.status == StrategyAggregationStatus.SUCCESS
        assert len(repo.aggregation_rows) == 1
        assert len(repo.material_rows) == 1


def test_confirm_write_blocked_attempt_persists_only_aggregation_audit_row() -> None:
    repo = FakeAggregationRepository(strategy_run=strategy_run(status="blocked"))

    result = service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.BLOCKED
    assert result.material_pack_id is None
    assert len(repo.aggregation_rows) == 1
    assert repo.aggregation_rows[0].status == StrategyAggregationStatus.BLOCKED
    assert len(repo.material_rows) == 0


def test_strategy_aggregation_run_schema_uses_small_indexes_not_large_version_index() -> None:
    from sqlalchemy import UniqueConstraint

    from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyAggregationRun

    aggregation_table = StrategyAggregationRun.__table__
    aggregation_unique_names = {
        constraint.name
        for constraint in aggregation_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uk_strategy_aggregation_version" not in aggregation_unique_names
    assert "uq_strategy_aggregation_run_id" in aggregation_unique_names

    index_names = {index.name for index in aggregation_table.indexes}
    assert "idx_strategy_aggregation_version_status" not in index_names
    assert "idx_strategy_aggregation_signal_status" in index_names
    assert "idx_strategy_aggregation_created_at" in index_names

    signal_status_index = next(
        index
        for index in aggregation_table.indexes
        if index.name == "idx_strategy_aggregation_signal_status"
    )
    assert signal_status_index.unique is False
    assert [column.name for column in signal_status_index.columns] == ["strategy_signal_run_id", "status"]

    created_at_index = next(index for index in aggregation_table.indexes if index.name == "idx_strategy_aggregation_created_at")
    assert created_at_index.unique is False
    assert [column.name for column in created_at_index.columns] == ["created_at_utc"]

    material_table = AnalysisMaterialPack.__table__
    material_unique_names = {
        constraint.name
        for constraint in material_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uk_analysis_material_pack_version" not in material_unique_names
    assert "uk_analysis_material_pack_version_key" in material_unique_names


def test_stage18_migrations_do_not_create_large_strategy_aggregation_indexes() -> None:
    migration_18 = Path("migrations/versions/20260518_18_create_strategy_aggregation_material_pack.py")
    migration_19 = Path("migrations/versions/20260519_18_relax_strategy_aggregation_attempt_uniqueness.py")
    source_18 = migration_18.read_text(encoding="utf-8")
    source_19 = migration_19.read_text(encoding="utf-8")

    for source in (source_18, source_19):
        assert "\"uk_strategy_aggregation_version\"" not in source
        assert "\"idx_strategy_aggregation_version_status\"" not in source
    assert "\"idx_strategy_aggregation_signal_status\"" in source_18
    assert "\"idx_strategy_aggregation_created_at\"" in source_18
    assert "pass" in source_19


def test_concurrent_final_unique_conflict_returns_skipped_already_exists() -> None:
    existing_final = SimpleNamespace(
        aggregation_run_id="SAR-existing-final",
        snapshot_id="MCS-stage18",
        status="success",
        analysis_hypothesis_direction="long",
        analysis_hypothesis_confidence="medium",
        analysis_hypothesis_semantics="analysis_hypothesis_only",
        direction_projection_source="fixture_or_existing_signal_projection",
        risk_level="low",
        risk_gate_status="pass",
        conflict_level="low",
        input_strategy_count=2,
        input_success_count=2,
        input_failed_count=0,
        input_invalid_count=0,
        input_not_implemented_count=0,
        effective_strategy_count=2,
    )
    session = FakeSession()
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(),
        existing_after_unique_conflict=existing_final,
        create_material_pack_error=RuntimeError("UNIQUE constraint failed: uk_analysis_material_pack_version_key"),
    )

    result = service_with_repo(repo).run_strategy_aggregation(session, request=run_request(confirm=True))

    assert result.status == StrategyAggregationStatus.SKIPPED
    assert result.aggregation_run_id == "SAR-existing-final"
    assert result.details["skip_reason"] == "already_exists"
    assert result.details["unique_conflict_recovered"] is True
    assert session.rollbacks == 1


def test_hermes_off_does_not_send_and_on_records_status() -> None:
    off_alert = FakeAlertSender()
    off_repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(),
    )
    off_result = service_with_repo(off_repo, alert=off_alert).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))

    on_alert = FakeAlertSender()
    on_repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(),
    )
    on_settings = AppSettings(strategy_aggregation_hermes_enabled=True)
    on_result = service_with_repo(on_repo, settings=on_settings, alert=on_alert).run_strategy_aggregation(
        FakeSession(),
        request=run_request(confirm=True),
    )

    assert off_result.hermes_status.value == "disabled"
    assert off_alert.calls == []
    assert on_result.hermes_status.value == "sent"
    assert len(on_alert.calls) == 1
    assert "BTC 策略聚合分析假设" in on_alert.calls[0].title
    assert "BTC 策略聚合分析假设" in on_alert.calls[0].summary
    body = on_repo.aggregation_rows[0].hermes_message
    assert "【标题】BTC 策略聚合分析假设结果" in body
    assert "【摘要】" in body
    assert "【原因】" in body
    assert "【建议动作】" in body
    assert "这是分析假设，不是策略信号" in body
    assert "这不是最终交易建议" in body
    assert "本阶段未调用大模型" in body
    assert "本阶段未自动交易" in body


def test_cli_dry_run_and_confirm_write_only_call_stage18_service(monkeypatch: Any, capsys: Any) -> None:
    fake_session = object()
    captured: list[StrategyAggregationRequest] = []

    @contextmanager
    def fake_session_scope(**_kwargs: Any) -> Any:
        yield fake_session

    def fake_run_strategy_aggregation(*, db_session: Any, request: StrategyAggregationRequest) -> Any:
        assert db_session is fake_session
        captured.append(request)
        return SimpleNamespace(
            status=StrategyAggregationStatus.SUCCESS,
            exit_code=0,
            aggregation_run_id="SAR-test",
            material_pack_id="AMP-test" if request.confirm_write else "",
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id="MCS-test",
            analysis_hypothesis_direction=AnalysisHypothesisDirection.LONG,
            analysis_hypothesis_confidence=SimpleNamespace(value="medium"),
            analysis_hypothesis_semantics="analysis_hypothesis_only",
            direction_projection_source="fixture_or_existing_signal_projection",
            stop_trading_source=None,
            risk_gate_projection_source=None,
            is_strategy_signal=False,
            is_trading_advice=False,
            is_executable=False,
            strategy_logic_implemented=False,
            promotion_allowed=False,
            promotion_requires_future_strategy_and_llm_stage=True,
            risk_level=SimpleNamespace(value="low"),
            risk_gate_status=SimpleNamespace(value="pass"),
            conflict_level=SimpleNamespace(value="low"),
            message="ok",
            error_message="",
        )

    monkeypatch.setattr(aggregation_cli, "session_scope", fake_session_scope)
    monkeypatch.setattr(aggregation_cli, "run_strategy_aggregation", fake_run_strategy_aggregation)

    dry_exit = aggregation_cli.main(["--strategy-signal-run-id", "SSR-test", "--trigger-source", "cli"])
    dry_output = _captured_key_values(capsys)
    confirm_exit = aggregation_cli.main(
        ["--strategy-signal-run-id", "SSR-test", "--trigger-source", "cli", "--confirm-write"]
    )
    confirm_output = _captured_key_values(capsys)

    assert dry_exit == 0
    assert dry_output["material_pack_id"] == ""
    assert captured[0].dry_run is True
    assert captured[0].confirm_write is False
    assert confirm_exit == 0
    assert confirm_output["material_pack_id"] == "AMP-test"
    assert captured[1].dry_run is False
    assert captured[1].confirm_write is True


def test_stage18_source_does_not_call_stage15_stage16_llm_or_binance() -> None:
    import app.strategy.aggregation.candidate_scenario_builder as candidate_module
    import app.strategy.aggregation.material_builder as material_module
    import app.strategy.aggregation.repository as repository_module
    import app.strategy.aggregation.rules as rules_module
    import app.strategy.aggregation.service as service_module
    import scripts.run_strategy_aggregation as script_module

    modules = (candidate_module, material_module, repository_module, rules_module, service_module, script_module)
    source = "\n".join(inspect.getsource(module) for module in modules)

    assert "from app.strategy.signal_service" not in source
    assert "scripts.run_strategy_signals" not in source
    assert "from app.market_context.snapshot_service" not in source
    assert "MarketContextSnapshotService" not in source
    assert "BinanceRestClient" not in source
    assert "DeepSeekClient" not in source
    assert "openai" not in source.lower()
    assert "/fapi/v1" not in source
    assert "getattr(row, \"strategy_payload_json\"" not in source
    assert "StrategySignalResult.strategy_payload_json" not in source


def test_stage18_source_does_not_instantiate_future_strategy_classes() -> None:
    import app.strategy.aggregation.candidate_scenario_builder as candidate_module
    import app.strategy.aggregation.material_builder as material_module
    import app.strategy.aggregation.repository as repository_module
    import app.strategy.aggregation.rules as rules_module
    import app.strategy.aggregation.service as service_module
    import scripts.run_strategy_aggregation as script_module

    modules = (candidate_module, material_module, repository_module, rules_module, service_module, script_module)
    source = "\n".join(inspect.getsource(module) for module in modules)

    for future_class_name in (
        "GannStrategy",
        "TrendStrategy",
        "SupportResistanceStrategy",
        "RiskControlStrategy",
    ):
        assert future_class_name not in source


def test_material_pack_does_not_generate_final_advice_fields() -> None:
    repo = FakeAggregationRepository(
        strategy_run=strategy_run(),
        results=(fake_stage16_signal("fixture_direction_hypothesis_long", direction="bullish_bias", risk="low", strength="0.80"),),
        restored_snapshot=restored_snapshot(),
    )
    service_with_repo(repo).run_strategy_aggregation(FakeSession(), request=run_request(confirm=True))
    payload = {
        "aggregation": repo.aggregation_rows[0].__dict__,
        "material": repo.material_rows[0].__dict__,
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)

    assert "context_upside_downside_ratio" in text
    for forbidden in (
        "final_advice",
        "open_position",
        "close_position",
        "take_profit",
        "stop_loss",
        "leverage",
        "reward_risk_ratio",
        "entry_price",
        "exit_price",
        "position_size",
        "candidate_direction",
    ):
        assert forbidden not in text


def _captured_key_values(capsys: Any) -> dict[str, str]:
    captured = capsys.readouterr().out.strip().splitlines()
    return dict(line.split("=", 1) for line in captured if "=" in line)
