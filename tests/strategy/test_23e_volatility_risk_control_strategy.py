from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.aggregation.rules import classify_strategy_results
from app.strategy.base import BaseStrategy
from app.strategy.common.result_adapter import adapt_strategy_result_to_signal
from app.strategy.common.result_contract import StrategyCommonResult, StrategyEvidenceItem, StrategyResult
from app.strategy.common.result_validator import validate_strategy_result
from app.strategy.evidence_context import EvidenceContext
from app.strategy.registry import StrategyRegistry
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.signal_service import StrategySignalService
from app.strategy.strategies.market_direction_regime_strategy import MarketDirectionRegimeStrategy
from app.strategy.strategies.volatility_risk_control_strategy import VolatilityRiskControlStrategy
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategySignal,
    StrategySignalRunRequest,
    StrategySignalStatus,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.flushes = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1
        for index, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                setattr(row, "id", index)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


class FakeRegistry:
    def __init__(self, strategies: tuple[Any, ...]) -> None:
        self._strategies = strategies

    def load_enabled_strategies(self) -> tuple[Any, ...]:
        return self._strategies


class FakeInputBuilder:
    def __init__(self, input_data: StrategyEvaluationInput) -> None:
        self.input_data = input_data

    def build_input_from_snapshot(self, *_args: Any, **_kwargs: Any) -> StrategyEvaluationInput:
        return self.input_data


class PublicMarketProvider(BaseStrategy):
    strategy_name = "market_direction_regime"
    strategy_version = "23B-test"
    strategy_role = "context"
    provides = ("primary_regime", "regime_phase", "market_environment_context")

    def __init__(self, *, primary_regime: str = "uptrend", regime_phase: str = "trend_continuation") -> None:
        self.primary_regime = primary_regime
        self.regime_phase = regime_phase

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        return market_result(input_data.trace_id, primary_regime=self.primary_regime, regime_phase=self.regime_phase)


class PublicKeyLevelProvider(BaseStrategy):
    strategy_name = "support_resistance_strategy"
    strategy_version = "23C-test"
    strategy_role = "support_resistance"
    provides = ("key_levels",)

    def __init__(self, key_levels: tuple[dict[str, Any], ...]) -> None:
        self.key_levels = key_levels

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        return key_level_result(input_data.trace_id, self.key_levels)


class PublicTriggerProvider(BaseStrategy):
    strategy_name = "breakout_pullback_trigger_strategy"
    strategy_version = "23D-test"
    strategy_role = "filter"
    provides = ("trigger_state", "volume_confirmation")

    def __init__(self, trigger: dict[str, Any]) -> None:
        self.trigger = trigger

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        return trigger_result(input_data.trace_id, self.trigger)


class FailingStrategy(BaseStrategy):
    strategy_name = "failing_23e_fixture"
    strategy_version = "v1"
    strategy_role = "context"

    def evaluate(self, _input_data: StrategyEvaluationInput) -> StrategyResult:
        raise RuntimeError("fixture failure")


def kline_row(
    index: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
    interval_value: str = "4h",
) -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    open_time_ms = 1_700_000_000_000 + index * interval_ms
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        open_price=Decimal(open_price),
        high_price=Decimal(high),
        low_price=Decimal(low),
        close_price=Decimal(close),
        volume=Decimal(volume),
    )


def stable_rows(count: int = 80, *, latest_close: str = "100") -> tuple[Any, ...]:
    rows = [
        kline_row(index, open_price="100", high="101", low="99", close="100")
        for index in range(count - 1)
    ]
    rows.append(kline_row(count - 1, open_price="100", high="101", low="99", close=latest_close))
    return tuple(rows)


def extreme_rows(count: int = 80) -> tuple[Any, ...]:
    rows = list(stable_rows(count - 1))
    rows.append(kline_row(count - 1, open_price="100", high="114", low="86", close="101"))
    return tuple(rows)


def higher_rows(count: int = 80) -> tuple[Any, ...]:
    return tuple(
        kline_row(index, open_price="100", high="102", low="98", close="100", interval_value="1d")
        for index in range(count)
    )


def strategy_input(base_rows: tuple[Any, ...] | None = None) -> StrategyEvaluationInput:
    active_base_rows = base_rows or stable_rows()
    active_higher_rows = higher_rows()
    return StrategyEvaluationInput(
        snapshot_id="MCS-23E",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        base_klines=active_base_rows,
        higher_klines=active_higher_rows,
        lookback_base_count=len(active_base_rows),
        lookback_higher_count=len(active_higher_rows),
        latest_base_open_time_ms=active_base_rows[-1].open_time_ms,
        latest_higher_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_start_open_time_ms=active_base_rows[0].open_time_ms,
        base_end_open_time_ms=active_base_rows[-1].open_time_ms,
        higher_start_open_time_ms=active_higher_rows[0].open_time_ms,
        higher_end_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_quality_check_id=301,
        higher_quality_check_id=401,
        trace_id="trace-23e",
        evaluated_at_utc=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )


def risk_strategy(**overrides: Any) -> VolatilityRiskControlStrategy:
    config = {
        "strategy_version": "23E-test",
        "strategy_role": "risk_control",
        "provides": [
            "volatility_risk",
            "trade_permission_filter",
            "risk_gate_decision",
            "reward_risk_feasibility",
            "chase_risk",
            "stop_distance_reference",
            "market_state_aware_risk_policy",
        ],
        "requires": (
            "role=context, provides=primary_regime",
            "role=support_resistance, provides=key_levels",
            "role=filter, provides=trigger_state",
        ),
        "consumes": (
            "common_result.primary_regime",
            "common_result.regime_phase",
            "common_result.key_levels",
            "common_result.trigger_state",
            "common_result.filter_decision",
        ),
        "lookback_bars": {"base": 80, "higher": 80},
        "minimum_required_bars": {"base": 40, "higher": 40},
        "atr": {"period": 14},
        "thresholds": {
            "high_atr_pct": "0.035",
            "extreme_atr_pct": "0.060",
            "high_range_expansion_ratio": "1.60",
            "extreme_range_expansion_ratio": "2.30",
            "high_chase_distance_pct": "0.020",
            "extreme_chase_distance_pct": "0.035",
            "min_rough_reward_risk_ratio": "1.50",
            "min_net_room_pct": "0.008",
            "fee_buffer_pct": "0.0004",
            "slippage_buffer_pct": "0.0010",
        },
        "risk_policy_mapping": {
            "primary_regime.uptrend": "trend_following_favorable",
            "primary_regime.range": "range_caution",
            "primary_regime.volatile": "volatile_defensive",
            "default": "default_conservative",
        },
        "risk_policy_profiles": {
            "default_conservative.default_decision": "wait",
            "default_conservative.max_chase_risk": "low",
            "trend_following_favorable.default_decision": "allow_with_caution",
            "trend_following_favorable.max_chase_risk": "medium",
            "trend_following_favorable.excessive_chase_action": "wait",
            "range_caution.default_decision": "wait",
            "range_caution.breakout_requires_volume": "true",
            "range_caution.max_chase_risk": "medium",
            "volatile_defensive.default_decision": "wait",
            "volatile_defensive.extreme_action": "block_all_candidates",
        },
    }
    config.update(overrides)
    return VolatilityRiskControlStrategy(config)


def support_level(**overrides: Any) -> dict[str, Any]:
    level = {
        "level_id": "S1",
        "level_type": "support",
        "level_group": "nearest_support",
        "zone_low": "96",
        "zone_high": "97",
        "zone_mid": "96.5",
        "confidence_score": "0.80",
        "current_relevance_score": "0.90",
        "role_flip_status": "none",
        "zone_quality": "clear",
    }
    level.update(overrides)
    return level


def resistance_level(**overrides: Any) -> dict[str, Any]:
    level = {
        "level_id": "R1",
        "level_type": "resistance",
        "level_group": "nearest_resistance",
        "zone_low": "110",
        "zone_high": "111",
        "zone_mid": "110.5",
        "confidence_score": "0.80",
        "current_relevance_score": "0.90",
        "role_flip_status": "none",
        "zone_quality": "clear",
    }
    level.update(overrides)
    return level


def trigger_summary(**overrides: Any) -> dict[str, Any]:
    summary = {
        "trigger_state": "pullback_confirmed",
        "filter_decision": "passed",
        "tested_level_summary": {
            "level_id": "S1",
            "level_type": "support",
            "level_group": "role_flip_candidate",
            "zone_low": "98",
            "zone_high": "99",
            "zone_mid": "98.5",
            "role_flip_status": "resistance_to_support",
            "zone_quality": "clear",
        },
        "volume_state": "expanding",
        "volume_confirmation": "confirming",
    }
    summary.update(overrides)
    return summary


def market_result(trace_id: str, *, primary_regime: str, regime_phase: str) -> StrategyResult:
    return StrategyResult(
        strategy_name="market_direction_regime",
        strategy_version="23B-test",
        strategy_role="context",
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            market_bias="bullish_bias" if primary_regime == "uptrend" else "neutral",
            risk_level="low",
            signal_strength="0.70",
            confidence_score="0.70",
            reason_codes=("market_regime_classified", f"primary_regime_{primary_regime}", f"regime_phase_{regime_phase}"),
            reason_text="Public market context generated.",
            evidence_items=(
                StrategyEvidenceItem("market_context", "not_applicable", "0.70", "Public market context generated.", "market_direction_regime"),
            ),
            context_summary=f"regime={primary_regime}, phase={regime_phase}",
            not_trading_advice=True,
        ),
        strategy_model_material_json={"summary": "market"},
        strategy_payload_json={"primary_regime": "volatile"},
        trace_id=trace_id,
    )


def key_level_result(trace_id: str, key_levels: tuple[dict[str, Any], ...]) -> StrategyResult:
    return StrategyResult(
        strategy_name="support_resistance_strategy",
        strategy_version="23C-test",
        strategy_role="support_resistance",
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="low",
            signal_strength="0.70",
            confidence_score="0.70",
            reason_codes=("fixture_key_levels",),
            reason_text="Public key levels generated.",
            key_levels=key_levels,
            not_trading_advice=True,
        ),
        strategy_model_material_json={"summary": "levels"},
        strategy_payload_json={"key_levels": [resistance_level(zone_low="101", zone_high="102")]},
        trace_id=trace_id,
    )


def trigger_result(trace_id: str, trigger: dict[str, Any]) -> StrategyResult:
    return StrategyResult(
        strategy_name="breakout_pullback_trigger_strategy",
        strategy_version="23D-test",
        strategy_role="filter",
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="not_applicable",
            signal_strength="0.70",
            confidence_score="0.70",
            reason_codes=("fixture_trigger",),
            reason_text="Public trigger generated.",
            filter_status="pass" if trigger.get("filter_decision") == "passed" else "unknown",
            trigger_state=trigger.get("trigger_state"),
            filter_decision=trigger.get("filter_decision"),
            tested_level_summary=trigger.get("tested_level_summary"),
            volume_state=trigger.get("volume_state"),
            volume_confirmation=trigger.get("volume_confirmation"),
            not_trading_advice=True,
        ),
        strategy_model_material_json={"summary": "trigger"},
        strategy_payload_json={"trigger_state": "false_breakout"},
        trace_id=trace_id,
    )


def context_with_public_evidence(
    *,
    primary_regime: str = "uptrend",
    regime_phase: str = "trend_continuation",
    key_levels: tuple[dict[str, Any], ...] | None = None,
    trigger: dict[str, Any] | None = None,
) -> EvidenceContext:
    context = EvidenceContext.empty()
    for result in (
        market_result("trace-23e", primary_regime=primary_regime, regime_phase=regime_phase),
        key_level_result("trace-23e", key_levels or (support_level(), resistance_level())),
        trigger_result("trace-23e", trigger or trigger_summary()),
    ):
        context = context.with_signal(adapt_strategy_result_to_signal(result))
    return context


def evaluate(
    *,
    base_rows: tuple[Any, ...] | None = None,
    context: EvidenceContext | None = None,
) -> StrategyResult:
    return risk_strategy().evaluate_with_evidence(strategy_input(base_rows), context or context_with_public_evidence())


def test_volatility_risk_control_outputs_risk_control_result() -> None:
    result = evaluate()
    common = result.common_result.to_jsonable()

    assert result.strategy_role == "risk_control"
    assert result.strategy_status == "success"
    assert validate_strategy_result(result).passed is True
    assert common["risk_gate_decision"] in {"allow", "allow_with_caution"}
    assert common["risk_scope"] == "current_candidate"
    assert common["global_market_risk"] == "normal"
    assert common["candidate_risk"] == "low"
    assert common["long_feasibility"] in {"acceptable", "favorable"}
    assert common["short_feasibility"] in {"poor", "invalid"}


def test_config_declares_role_provides_requires_and_consumes() -> None:
    source = Path("configs/strategies/volatility_risk_control_strategy.yaml").read_text(encoding="utf-8")
    registry = StrategyRegistry()
    strategy = next(item for item in registry.load_enabled_strategies() if item.strategy_name == "volatility_risk_control_strategy")

    assert "strategy_role: risk_control" in source
    assert "requires:" in source
    assert "consumes:" in source
    assert strategy.strategy_role == "risk_control"
    assert "risk_gate_decision" in strategy.provides
    assert "role=context, provides=primary_regime" in strategy.requires
    assert "common_result.key_levels" in strategy.consumes


def test_runner_orders_23e_after_public_context_key_levels_and_trigger() -> None:
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                risk_strategy(),
                PublicTriggerProvider(trigger_summary()),
                PublicKeyLevelProvider((support_level(), resistance_level())),
                PublicMarketProvider(),
            )
        )
    )

    result = runner.run_strategies(strategy_input())

    assert [signal.strategy_name for signal in result.signals] == [
        "breakout_pullback_trigger_strategy",
        "support_resistance_strategy",
        "market_direction_regime",
        "volatility_risk_control_strategy",
    ]
    assert result.signals[-1].common_payload_json["risk_gate_decision"] in {"allow", "allow_with_caution"}


def test_private_payloads_from_previous_strategies_are_not_read() -> None:
    result = evaluate()
    common = result.common_result.to_jsonable()

    assert common["selected_risk_policy_profile"] == "trend_following_favorable"
    assert result.strategy_payload_json["risk_policy_mapping_details"]["primary_regime"] == "uptrend"
    assert result.strategy_payload_json["public_context_snapshot"]["trigger_state"] == "pullback_confirmed"


def test_missing_market_context_uses_default_conservative_and_never_allows() -> None:
    context = EvidenceContext.empty()
    for result in (
        key_level_result("trace-23e", (support_level(), resistance_level())),
        trigger_result("trace-23e", trigger_summary()),
    ):
        context = context.with_signal(adapt_strategy_result_to_signal(result))

    result = evaluate(context=context)
    common = result.common_result.to_jsonable()

    assert common["selected_risk_policy_profile"] == "default_conservative"
    assert common["risk_gate_decision"] == "insufficient_context"
    assert common["risk_gate_decision"] != "allow"


def test_missing_key_levels_or_trigger_context_is_conservative() -> None:
    no_levels = EvidenceContext.empty()
    for result in (market_result("trace-23e", primary_regime="uptrend", regime_phase="trend_continuation"), trigger_result("trace-23e", trigger_summary())):
        no_levels = no_levels.with_signal(adapt_strategy_result_to_signal(result))
    no_trigger = EvidenceContext.empty()
    for result in (market_result("trace-23e", primary_regime="uptrend", regime_phase="trend_continuation"), key_level_result("trace-23e", (support_level(), resistance_level()))):
        no_trigger = no_trigger.with_signal(adapt_strategy_result_to_signal(result))

    assert evaluate(context=no_levels).common_result.to_jsonable()["risk_gate_decision"] == "insufficient_context"
    assert evaluate(context=no_trigger).common_result.to_jsonable()["risk_gate_decision"] == "insufficient_context"


def test_unknown_market_context_does_not_default_allow() -> None:
    result = evaluate(context=context_with_public_evidence(primary_regime="unknown", regime_phase="unknown"))
    common = result.common_result.to_jsonable()

    assert common["selected_risk_policy_profile"] == "default_conservative"
    assert common["risk_gate_decision"] != "allow"


def test_extreme_volatility_blocks_all_candidates() -> None:
    result = evaluate(base_rows=extreme_rows())
    common = result.common_result.to_jsonable()

    assert common["volatility_state"] == "extreme_volatility"
    assert common["global_market_risk"] == "extreme"
    assert common["risk_gate_decision"] == "block_all_candidates"
    assert common["risk_scope"] == "all_candidates"


def test_high_chase_breakout_is_downgraded_to_wait() -> None:
    context = context_with_public_evidence(
        trigger=trigger_summary(
            trigger_state="breakout_confirmed",
            filter_decision="passed",
            tested_level_summary={
                "level_id": "R0",
                "level_type": "resistance",
                "level_group": "nearest_resistance",
                "zone_low": "98",
                "zone_high": "99",
                "zone_mid": "98.5",
                "role_flip_status": "none",
                "zone_quality": "clear",
            },
        )
    )
    result = evaluate(base_rows=stable_rows(latest_close="104"), context=context)
    common = result.common_result.to_jsonable()

    assert common["chase_risk"] in {"high", "extreme"}
    assert common["risk_gate_decision"] in {"wait", "block_current_candidate"}
    assert common["risk_scope"] == "current_candidate"


def test_false_breakout_blocks_current_candidate() -> None:
    result = evaluate(context=context_with_public_evidence(trigger=trigger_summary(trigger_state="false_breakout", filter_decision="blocked")))
    common = result.common_result.to_jsonable()

    assert common["candidate_risk"] == "extreme"
    assert common["risk_gate_decision"] == "block_current_candidate"
    assert common["risk_scope"] == "current_candidate"


def test_long_and_short_feasibility_are_calculated_separately() -> None:
    result = evaluate(
        context=context_with_public_evidence(
            key_levels=(
                support_level(zone_low="96", zone_high="97"),
                resistance_level(zone_low="101", zone_high="102"),
            )
        )
    )
    common = result.common_result.to_jsonable()

    assert common["long_feasibility"] in {"poor", "invalid"}
    assert common["short_feasibility"] in {"favorable", "marginal"}
    assert common["long_feasibility"] != "favorable"
    assert common["long_feasibility"] != common["short_feasibility"]


def test_fee_and_slippage_buffer_can_make_room_poor() -> None:
    strategy = risk_strategy(
        thresholds={
            "high_atr_pct": "0.035",
            "extreme_atr_pct": "0.060",
            "high_range_expansion_ratio": "1.60",
            "extreme_range_expansion_ratio": "2.30",
            "high_chase_distance_pct": "0.020",
            "extreme_chase_distance_pct": "0.035",
            "min_rough_reward_risk_ratio": "1.50",
            "min_net_room_pct": "0.008",
            "fee_buffer_pct": "0.0060",
            "slippage_buffer_pct": "0.0040",
        }
    )
    context = context_with_public_evidence(
        key_levels=(
            support_level(zone_low="96", zone_high="97"),
            resistance_level(zone_low="101", zone_high="102"),
        )
    )

    result = strategy.evaluate_with_evidence(strategy_input(), context)

    assert result.common_result.to_jsonable()["long_feasibility"] in {"poor", "invalid"}
    assert result.strategy_payload_json["fee_buffer_pct"] == "0.0060"


def test_common_result_keeps_private_calculation_details_out() -> None:
    result = evaluate()
    common = result.common_result.to_jsonable()
    payload = result.strategy_payload_json

    assert payload["atr_pct"] is not None
    assert payload["rough_long_reward_risk_ratio"] is not None
    assert "atr_pct" not in common
    assert "rough_long_reward_risk_ratio" not in common
    assert "risk_scoring_details" not in common


def test_insufficient_kline_data_does_not_raise() -> None:
    result = risk_strategy().evaluate_with_evidence(strategy_input(stable_rows(10)), context_with_public_evidence())
    common = result.common_result.to_jsonable()

    assert result.strategy_status == "invalid"
    assert validate_strategy_result(result).passed is True
    assert common["risk_gate_decision"] == "insufficient_context"
    assert common["global_market_risk"] == "insufficient_data"


def test_disabled_23e_config_does_not_break_registry(tmp_path: Path) -> None:
    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - volatility_risk_control_strategy\n  - market_direction_regime\n",
        encoding="utf-8",
    )
    (tmp_path / "volatility_risk_control_strategy.yaml").write_text(
        "enabled: false\nstrategy_version: 23E-test\nstrategy_role: risk_control\nprovides:\n  - risk_gate_decision\n",
        encoding="utf-8",
    )
    (tmp_path / "market_direction_regime_strategy.yaml").write_text(
        "enabled: true\nstrategy_version: 23B-test\nstrategy_role: context\nprovides:\n  - primary_regime\nminimum_required_bars:\n  base: 10\n  higher: 10\n",
        encoding="utf-8",
    )
    registry = StrategyRegistry(
        config_dir=tmp_path,
        strategy_classes={
            "volatility_risk_control_strategy": VolatilityRiskControlStrategy,
            "market_direction_regime": MarketDirectionRegimeStrategy,
        },
    )

    assert [strategy.strategy_name for strategy in registry.load_enabled_strategies()] == ["market_direction_regime"]


def test_single_strategy_failure_does_not_stop_23e() -> None:
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                PublicMarketProvider(),
                PublicKeyLevelProvider((support_level(), resistance_level())),
                FailingStrategy(),
                PublicTriggerProvider(trigger_summary()),
                risk_strategy(),
            )
        )
    )

    result = runner.run_strategies(strategy_input())

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert result.signals[-1].strategy_name == "volatility_risk_control_strategy"
    assert result.signals[-1].common_payload_json["risk_gate_decision"] in {"allow", "allow_with_caution"}


def test_run_strategy_signals_persists_23e_result() -> None:
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(strategy_input()),
        runner=StrategyRunner(
            registry=FakeRegistry(
                (
                    PublicMarketProvider(),
                    PublicKeyLevelProvider((support_level(), resistance_level())),
                    PublicTriggerProvider(trigger_summary()),
                    risk_strategy(),
                )
            )
        ),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-23E",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-23e",
        ),
    )

    row = next(item for item in session.added if getattr(item, "strategy_name", "") == "volatility_risk_control_strategy")
    common = json.loads(row.common_payload_json)
    payload = json.loads(row.strategy_payload_json)
    assert result.status == StrategyRunStatus.SUCCESS
    assert session.commits == 1
    assert row.strategy_role == "risk_control"
    assert common["risk_gate_decision"] in {"allow", "allow_with_caution"}
    assert payload["atr_pct"] is not None


def test_stage18_reads_23e_result_without_crash() -> None:
    signal = adapt_strategy_result_to_signal(evaluate())
    row = SimpleNamespace(
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        strategy_status=signal.strategy_status.value,
        direction_bias=signal.direction_bias.value,
        risk_level=signal.risk_level.value,
        signal_strength=Decimal(str(signal.signal_strength)),
        reason_codes_json=json.dumps(list(signal.reason_codes), ensure_ascii=False),
        reason_text=signal.reason_text,
        metrics_json=json.dumps(signal.metrics, ensure_ascii=False),
        common_payload_json=json.dumps(signal.common_payload_json, ensure_ascii=False),
        strategy_payload_json=json.dumps(signal.strategy_payload_json, ensure_ascii=False),
        contract_version=signal.contract_version,
        strategy_role=signal.strategy_role,
        common_payload_hash=signal.common_payload_hash,
    )

    summary = classify_strategy_results((row,))

    assert summary.effective_strategy_count == 1
