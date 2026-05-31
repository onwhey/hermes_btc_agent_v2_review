from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.weak_models.aggregation import WeakModelAggregator
from app.weak_models.models import (
    MarketRegimeContextModel,
    SupportDistanceConfirmationModel,
    TrendStrengthDirectionalModel,
    VolatilityRiskGateModel,
)
from app.weak_models.types import (
    WeakModelEvaluationInput,
    WeakModelOutput,
    WeakModelProfile,
    WeakModelResultStatus,
    WeakModelRole,
)


def test_each_weak_model_returns_role_specific_output() -> None:
    input_data = _input_data()
    outputs = (
        TrendStrengthDirectionalModel(_profile("trend_strength_directional", WeakModelRole.DIRECTIONAL.value)).evaluate(
            input_data
        ),
        VolatilityRiskGateModel(_profile("volatility_risk_gate", WeakModelRole.RISK.value)).evaluate(input_data),
        SupportDistanceConfirmationModel(
            _profile("support_distance_confirmation", WeakModelRole.CONFIRMATION.value)
        ).evaluate(input_data),
        MarketRegimeContextModel(_profile("market_regime_context", WeakModelRole.CONTEXT.value)).evaluate(input_data),
    )

    assert all(output.status == WeakModelResultStatus.SUCCESS for output in outputs)
    assert outputs[0].model_role == "directional"
    assert outputs[0].signal_score is not None
    assert outputs[0].direction_bias in {"bullish", "bearish", "neutral"}
    assert outputs[1].model_role == "risk"
    assert outputs[1].risk_score is not None
    assert outputs[1].trade_permission in {"allow", "caution", "block"}
    assert outputs[2].model_role == "confirmation"
    assert outputs[2].confirmation_score is not None
    assert outputs[2].supports_direction in {"long", "short", "neutral", "none"}
    assert outputs[3].model_role == "context"
    assert outputs[3].context_regime in {"trend", "range", "transition", "high_volatility", "low_volatility"}


def test_aggregation_uses_active_weighted_direction_and_excludes_observe_only() -> None:
    input_data = _input_data()
    profiles = {
        "active_a": _profile("active_a", WeakModelRole.DIRECTIONAL.value, static_weight=0.10),
        "active_b": _profile("active_b", WeakModelRole.DIRECTIONAL.value, static_weight=0.20),
        "observe": _profile(
            "observe",
            WeakModelRole.DIRECTIONAL.value,
            maturity_stage="observe_only",
            static_weight=0.0,
        ),
    }
    outputs = (
        WeakModelOutput(
            model_key="active_a",
            model_role="directional",
            signal_score=0.80,
            confidence=0.50,
            static_weight=0.10,
        ),
        WeakModelOutput(
            model_key="active_b",
            model_role="directional",
            signal_score=-0.20,
            confidence=1.00,
            static_weight=0.20,
        ),
        WeakModelOutput(
            model_key="observe",
            model_role="directional",
            signal_score=1.00,
            confidence=1.00,
            static_weight=0.0,
        ),
    )

    summary = WeakModelAggregator().aggregate(
        weak_model_run_id="WMR-1",
        input_data=input_data,
        outputs=outputs,
        profiles_by_key=profiles,
    )

    assert summary.directional_score == 0.0
    assert summary.directional_bias == "neutral"
    assert summary.directional_confidence == 0.25
    assert summary.details["observe_only_output_count"] == 1


def test_observe_only_context_is_summarized_without_affecting_formal_votes() -> None:
    input_data = _input_data()
    profiles = {
        "active_direction": _profile("active_direction", WeakModelRole.DIRECTIONAL.value, static_weight=0.10),
        "active_risk": _profile("active_risk", WeakModelRole.RISK.value, static_weight=0.10),
        "observe_context": _profile(
            "observe_context",
            WeakModelRole.CONTEXT.value,
            maturity_stage="observe_only",
            static_weight=0.0,
        ),
    }
    outputs = (
        WeakModelOutput(
            model_key="active_direction",
            model_role="directional",
            signal_score=0.8,
            direction_bias="bullish",
            confidence=1.0,
            static_weight=0.10,
        ),
        WeakModelOutput(
            model_key="active_risk",
            model_role="risk",
            risk_score=0.2,
            risk_level="low",
            trade_permission="allow",
            confidence=1.0,
            static_weight=0.10,
        ),
        WeakModelOutput(
            model_key="observe_context",
            model_role="context",
            context_regime="range",
            context_score=0.7,
            confidence=0.9,
            static_weight=0.0,
        ),
    )

    summary = WeakModelAggregator().aggregate(
        weak_model_run_id="WMR-1",
        input_data=input_data,
        outputs=outputs,
        profiles_by_key=profiles,
    )

    assert round(summary.directional_score, 6) == 0.8
    assert summary.trade_permission == "allow"
    assert summary.context_summary["regime"] == "range"
    assert summary.context_summary["source_model_key"] == "observe_context"
    assert summary.context_summary["source_maturity_stage"] == "observe_only"


def test_risk_veto_factors_are_exposed_in_aggregation_summary() -> None:
    input_data = _input_data()
    profiles = {
        "risk_veto": _profile("risk_veto", WeakModelRole.RISK.value, static_weight=0.10),
    }
    outputs = (
        WeakModelOutput(
            model_key="risk_veto",
            model_role="risk",
            risk_score=0.95,
            risk_level="extreme",
            can_veto=True,
            veto_triggered=True,
            trade_permission="block",
            confidence=1.0,
            static_weight=0.10,
        ),
    )

    summary = WeakModelAggregator().aggregate(
        weak_model_run_id="WMR-1",
        input_data=input_data,
        outputs=outputs,
        profiles_by_key=profiles,
    )

    assert summary.veto_triggered is True
    assert summary.trade_permission == "block"
    assert summary.veto_factors == ("risk_veto",)


def _input_data() -> WeakModelEvaluationInput:
    return WeakModelEvaluationInput(
        pipeline_run_id="SP-1",
        strategy_signal_run_id="SSR-1",
        snapshot_id="MCS-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=datetime(2026, 5, 31, 4, tzinfo=timezone.utc),
        base_klines=_rows(count=120, start_price=60000, step=25, hours=4),
        higher_klines=_rows(count=80, start_price=58000, step=75, hours=24),
        trace_id="trace-1",
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
