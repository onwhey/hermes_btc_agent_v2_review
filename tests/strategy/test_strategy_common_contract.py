from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.common.context_view import StrategyContextView
from app.strategy.common.result_adapter import adapt_strategy_result_to_signal
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyKeyLevel,
    StrategyResult,
    StrategyRiskFlag,
    StrategyRole,
    StrategyScenarioCandidate,
)
from app.strategy.common.result_validator import validate_strategy_result
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.types import (
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategySignalPersistencePayload,
    StrategySignalStatus,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1
        for index, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                setattr(row, "id", index)


class FakeRegistry:
    def __init__(self, strategies: tuple[Any, ...]) -> None:
        self._strategies = strategies

    def load_enabled_strategies(self) -> tuple[Any, ...]:
        return self._strategies


class InvalidContractStrategy:
    strategy_name = "invalid_contract"
    strategy_version = "v1"

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=StrategyRole.DIRECTIONAL.value,
            strategy_status=StrategySignalStatus.SUCCESS.value,
            common_result=StrategyCommonResult(
                market_bias="bullish_bias",
                risk_level="medium",
                signal_strength="0.50",
                confidence_score="0.50",
                reason_codes=("missing_scenario",),
                reason_text="Directional result intentionally misses scenario candidates.",
            ),
            trace_id=input_data.trace_id,
        )


def kline_row(index: int, *, interval_value: str = "4h") -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    open_time_ms = 1_700_000_000_000 + index * interval_ms
    close = Decimal("50000") + Decimal(index)
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        high_price=close + Decimal("100"),
        low_price=close - Decimal("100"),
        close_price=close,
    )


def strategy_input() -> StrategyEvaluationInput:
    base_rows = tuple(kline_row(index, interval_value="4h") for index in range(5))
    higher_rows = tuple(kline_row(index, interval_value="1d") for index in range(3))
    return StrategyEvaluationInput(
        snapshot_id="MCS-contract",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        base_klines=base_rows,
        higher_klines=higher_rows,
        lookback_base_count=len(base_rows),
        lookback_higher_count=len(higher_rows),
        latest_base_open_time_ms=base_rows[-1].open_time_ms,
        latest_higher_open_time_ms=higher_rows[-1].open_time_ms,
        base_start_open_time_ms=base_rows[0].open_time_ms,
        base_end_open_time_ms=base_rows[-1].open_time_ms,
        higher_start_open_time_ms=higher_rows[0].open_time_ms,
        higher_end_open_time_ms=higher_rows[-1].open_time_ms,
        base_quality_check_id=1,
        higher_quality_check_id=2,
        trace_id="trace-contract",
        evaluated_at_utc=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )


def valid_directional_result() -> StrategyResult:
    return StrategyResult(
        strategy_name="contract_fixture",
        strategy_version="v1",
        strategy_role=StrategyRole.DIRECTIONAL.value,
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            market_bias="bullish_bias",
            risk_level="medium",
            signal_strength="0.60",
            confidence_score="0.55",
            reason_codes=("fixture_reason",),
            reason_text="Fixture directional observation.",
            scenario_candidates=(
                StrategyScenarioCandidate(
                    scenario_type="long_candidate",
                    direction_bias="bullish_bias",
                    activation_condition="Observe continuation above the reference zone.",
                    invalidation_condition="Observation weakens below the reference zone.",
                    risk_boundary="Recent range is the observation boundary.",
                    observation_period_bars=3,
                ),
            ),
            not_trading_advice=True,
        ),
        strategy_model_material_json={"summary": "bounded"},
        strategy_payload_json={"private_fixture": {"ignored_by_common_layer": True}},
        trace_id="trace-contract",
    )


def test_context_view_is_read_only_projection_of_strategy_input() -> None:
    view = StrategyContextView.from_evaluation_input(strategy_input())

    assert view.snapshot_id == "MCS-contract"
    assert view.base_window_count == 5
    assert view.higher_window_count == 3
    assert view.latest_base_close() == Decimal("50004")
    assert view.recent_base_range(2) == (Decimal("49903"), Decimal("50104"))


def test_valid_result_adapts_to_legacy_strategy_signal_fields() -> None:
    result = valid_directional_result()
    validation = validate_strategy_result(result)
    signal = adapt_strategy_result_to_signal(result, validation=validation)

    assert validation.passed is True
    assert signal.strategy_status == StrategySignalStatus.SUCCESS
    assert signal.direction_bias.value == "bullish_bias"
    assert signal.contract_version == "strategy_result_contract_v1"
    assert signal.strategy_role == "directional"
    assert signal.common_payload_hash
    assert signal.validation_status == "passed"
    assert signal.strategy_payload_json["private_fixture"]["ignored_by_common_layer"] is True


def test_role_specific_validator_rules_reject_missing_public_fields() -> None:
    directional = valid_directional_result()
    bad_directional = StrategyResult(
        strategy_name=directional.strategy_name,
        strategy_version=directional.strategy_version,
        strategy_role=directional.strategy_role,
        strategy_status=directional.strategy_status,
        common_result=StrategyCommonResult(
            market_bias="bullish_bias",
            risk_level="medium",
            signal_strength="0.60",
            confidence_score="0.55",
            reason_codes=("fixture_reason",),
            reason_text="Missing directional scenario fields.",
            scenario_candidates=(StrategyScenarioCandidate(scenario_type="long_candidate"),),
        ),
    )
    bad_support_resistance = StrategyResult(
        strategy_name="sr_fixture",
        strategy_version="v1",
        strategy_role=StrategyRole.SUPPORT_RESISTANCE.value,
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(reason_text="Missing key levels."),
    )
    bad_risk = StrategyResult(
        strategy_name="risk_fixture",
        strategy_version="v1",
        strategy_role=StrategyRole.RISK_CONTROL.value,
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(risk_level="high", reason_text="Missing risk flags."),
    )
    bad_placeholder = StrategyResult(
        strategy_name="placeholder_fixture",
        strategy_version="v1",
        strategy_role=StrategyRole.PLACEHOLDER.value,
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(key_levels=(StrategyKeyLevel(level_type="reference"),)),
    )

    assert validate_strategy_result(bad_directional).passed is False
    assert validate_strategy_result(bad_support_resistance).passed is False
    assert validate_strategy_result(bad_risk).passed is False
    assert validate_strategy_result(bad_placeholder).passed is False


def test_risk_control_result_accepts_risk_flags() -> None:
    result = StrategyResult(
        strategy_name="risk_fixture",
        strategy_version="v1",
        strategy_role=StrategyRole.RISK_CONTROL.value,
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            risk_level="high",
            signal_strength="0.70",
            confidence_score="0.60",
            reason_text="Risk observation fixture.",
            risk_flags=(
                StrategyRiskFlag(
                    risk_type="volatility_observation",
                    risk_level="high",
                    triggered=True,
                    reason="Fixture risk flag.",
                ),
            ),
        ),
    )

    assert validate_strategy_result(result).passed is True


def test_runner_converts_contract_validation_failure_to_invalid_signal() -> None:
    runner = StrategyRunner(registry=FakeRegistry((InvalidContractStrategy(),)))

    result = runner.run_strategies(strategy_input())

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert result.signals[0].strategy_status == StrategySignalStatus.INVALID
    assert result.signals[0].validation_status == "failed"
    assert result.signals[0].validation_errors_json


def test_repository_persists_common_contract_fields_without_replacing_legacy_fields() -> None:
    session = FakeSession()
    signal = adapt_strategy_result_to_signal(valid_directional_result())

    rows = StrategySignalResultRepository().create_strategy_signal_results(
        session,
        (
            StrategySignalPersistencePayload(
                run_id="SSR-contract",
                snapshot_id="MCS-contract",
                symbol="BTCUSDT",
                base_interval_value="4h",
                higher_interval_value="1d",
                signal=signal,
                trace_id="trace-contract",
            ),
        ),
    )

    row = rows[0]
    assert row.direction_bias == "bullish_bias"
    assert row.contract_version == "strategy_result_contract_v1"
    assert row.strategy_role == "directional"
    assert row.common_payload_json
    assert row.strategy_model_material_json
    assert row.strategy_payload_json
    assert row.common_payload_hash
    assert row.validation_status == "passed"


def test_stage23a_migration_adds_only_nullable_strategy_result_contract_fields() -> None:
    source = Path("migrations/versions/20260601_23a_strategy_common_contract.py").read_text(encoding="utf-8")

    assert 'revision: str = "20260601_23a"' in source
    assert 'down_revision: str | None = "20260531_22b"' in source
    for column_name in (
        "contract_version",
        "strategy_role",
        "common_payload_json",
        "strategy_model_material_json",
        "strategy_payload_json",
        "common_payload_hash",
        "validation_status",
        "validation_errors_json",
    ):
        assert f'"{column_name}"' in source
    assert "market_kline_4h" not in source
    assert "market_kline_1d" not in source
