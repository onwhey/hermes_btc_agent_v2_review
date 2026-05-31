from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from app.weak_models.output_quality_service import WeakModelOutputQualityService
from app.weak_models.output_quality_types import (
    WeakModelQualityCheckRequest,
    WeakModelQualitySeverity,
    WeakModelQualityStatus,
)
from scripts import check_weak_model_output_quality


def test_directional_score_too_strong_produces_warning() -> None:
    result = _run_single_result(_target(directional_score=0.80))

    assert result.status == WeakModelQualityStatus.WARNING
    assert _issue_codes(result) == {"directional_score_too_strong"}
    assert result.should_block_pipeline is False


def test_confidence_too_high_produces_warning() -> None:
    target = _target(
        directional_score=0.20,
        results=(
            _result_row(
                model_key="trend_strength_directional",
                model_role="directional",
                signal_score=0.30,
                confidence=0.85,
            ),
        ),
    )

    result = _run_single_result(target)

    assert result.status == WeakModelQualityStatus.WARNING
    assert "confidence_too_high" in _issue_codes(result)


def test_risk_score_level_mismatch_produces_warning() -> None:
    target = _target(
        results=(
            _result_row(
                model_key="volatility_risk_gate",
                model_role="risk",
                risk_score=0.70,
                risk_level="low",
                trade_permission="allow",
                confidence=0.60,
            ),
        ),
    )

    result = _run_single_result(target)

    assert result.status == WeakModelQualityStatus.WARNING
    assert "risk_score_level_mismatch" in _issue_codes(result)


def test_veto_triggered_without_veto_factors_produces_warning() -> None:
    target = _target(veto_triggered=True, veto_factors_json="[]")

    result = _run_single_result(target)

    assert result.status == WeakModelQualityStatus.WARNING
    assert "veto_triggered_without_veto_factors" in _issue_codes(result)


def test_context_summary_missing_produces_warning() -> None:
    target = _target(context_summary_json="{}")

    result = _run_single_result(target)

    assert result.status == WeakModelQualityStatus.WARNING
    assert "context_summary_missing" in _issue_codes(result)


def test_observe_only_context_does_not_affect_directional_score_or_permission() -> None:
    target = _target(
        directional_score=0.50,
        risk_level="low",
        trade_permission="allow",
        results=(
            _result_row(
                model_key="active_direction",
                model_role="directional",
                signal_score=0.50,
                confidence=0.70,
            ),
                _result_row(
                    model_key="market_regime_context",
                    model_role="context",
                    maturity_stage="observe_only",
                    participation_mode="observe_only",
                    signal_score=None,
                    static_weight=0.0,
                    effective_score=0.0,
                    context_regime="range",
                context_score=0.70,
                confidence=0.70,
            ),
        ),
    )

    result = _run_single_result(target)

    assert result.status == WeakModelQualityStatus.PASSED
    assert result.severity == WeakModelQualitySeverity.INFO
    assert "observe_only_context_pollution" not in _issue_codes(result)


def test_quality_check_default_is_read_only_and_does_not_write() -> None:
    repo = FakeQualityRepository((_target(directional_score=0.80),))
    session = FakeSession()
    service = WeakModelOutputQualityService(repository=repo)

    report = service.check_weak_model_output_quality(
        session,
        request=WeakModelQualityCheckRequest(weak_model_run_id="WMR-1", dry_run=True, confirm_write=False),
    )

    assert report.results[0].database_written is False
    assert repo.upsert_payloads == []
    assert session.commits == 0


def test_confirm_write_persists_weak_model_quality_check() -> None:
    repo = FakeQualityRepository((_target(directional_score=0.80),))
    session = FakeSession()
    service = WeakModelOutputQualityService(repository=repo)

    report = service.check_weak_model_output_quality(
        session,
        request=WeakModelQualityCheckRequest(weak_model_run_id="WMR-1", dry_run=False, confirm_write=True),
    )

    assert report.results[0].database_written is True
    assert repo.upsert_payloads[0].weak_model_run_id == "WMR-1"
    assert repo.upsert_payloads[0].should_block_pipeline is False
    assert session.commits == 1


def test_quality_check_does_not_call_model_hermes_or_binance_paths() -> None:
    repo = FakeQualityRepository((_target(directional_score=0.20),))
    service = WeakModelOutputQualityService(repository=repo)

    report = service.check_weak_model_output_quality(
        FakeSession(),
        request=WeakModelQualityCheckRequest(weak_model_run_id="WMR-1"),
    )

    assert report.results[0].status == WeakModelQualityStatus.PASSED
    assert repo.model_call_count == 0
    assert repo.hermes_call_count == 0
    assert repo.binance_call_count == 0


def test_cli_default_dry_run_does_not_write(capsys: Any) -> None:
    repo = FakeQualityRepository((_target(directional_score=0.80),))
    service = WeakModelOutputQualityService(repository=repo)

    exit_code = check_weak_model_output_quality.main(
        ["--weak-model-run-id", "WMR-1"],
        service=service,
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert repo.upsert_payloads == []
    assert "status=warning" in captured.out
    assert "database_written=false" in captured.out


def test_cli_confirm_write_writes_quality_check(capsys: Any) -> None:
    repo = FakeQualityRepository((_target(directional_score=0.80),))
    service = WeakModelOutputQualityService(repository=repo)

    exit_code = check_weak_model_output_quality.main(
        ["--weak-model-run-id", "WMR-1", "--confirm-write"],
        service=service,
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(repo.upsert_payloads) == 1
    assert "database_written=true" in captured.out


def test_cli_parameter_error_returns_exit_code_2() -> None:
    exit_code = check_weak_model_output_quality.main(
        ["--limit", "0"],
        service=WeakModelOutputQualityService(repository=FakeQualityRepository(())),
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    assert exit_code == 2


def test_27b_migration_creates_only_quality_check_table() -> None:
    migration_text = Path("migrations/versions/20260609_27b_weak_model_quality_check.py").read_text(
        encoding="utf-8"
    )

    assert 'down_revision: str | None = "20260608_27a"' in migration_text
    assert "weak_model_quality_check" in migration_text
    assert "weak_model_result" in migration_text
    assert "weak_model_aggregation" in migration_text
    assert "op.create_table" in migration_text
    assert "op.drop_table" in migration_text
    assert "market_kline_4h" not in migration_text
    assert "market_kline_1d" not in migration_text


class FakeQualityRepository:
    def __init__(self, targets: tuple[Any, ...]) -> None:
        self.targets_by_run_id = {target.run.weak_model_run_id: target for target in targets}
        self.targets = targets
        self.upsert_payloads: list[Any] = []
        self.model_call_count = 0
        self.hermes_call_count = 0
        self.binance_call_count = 0

    def get_quality_target_by_run_id(self, db_session: Any, *, weak_model_run_id: str) -> Any | None:
        return self.targets_by_run_id.get(weak_model_run_id)

    def list_recent_quality_targets(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        limit: int,
    ) -> tuple[Any, ...]:
        return tuple(self.targets[:limit])

    def upsert_quality_check(self, db_session: Any, *, payload: Any) -> tuple[Any, str]:
        self.upsert_payloads.append(payload)
        return SimpleNamespace(), "created"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


@contextmanager
def _fake_session_scope(**_: Any) -> Iterator[FakeSession]:
    yield FakeSession()


def _run_single_result(target: Any) -> Any:
    repo = FakeQualityRepository((target,))
    service = WeakModelOutputQualityService(repository=repo)
    report = service.check_weak_model_output_quality(
        FakeSession(),
        request=WeakModelQualityCheckRequest(weak_model_run_id=target.run.weak_model_run_id),
    )
    return report.results[0]


def _issue_codes(result: Any) -> set[str]:
    return {issue.error_code for issue in result.issues}


def _target(
    *,
    directional_score: float = 0.20,
    risk_level: str = "low",
    trade_permission: str = "allow",
    veto_triggered: bool = False,
    veto_factors_json: str = "[]",
    context_summary_json: str | None = None,
    results: tuple[Any, ...] | None = None,
) -> Any:
    slot = datetime(2026, 5, 31, 4, tzinfo=timezone.utc)
    context_summary = context_summary_json or (
        '{"confidence":0.7,"context_score":0.7,"regime":"range",'
        '"source_model_key":"market_regime_context","source_maturity_stage":"observe_only",'
        '"source_participation_mode":"observe_only"}'
    )
    run = SimpleNamespace(
        weak_model_run_id="WMR-1",
        strategy_signal_run_id="SSR-1",
        snapshot_id="MCS-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=slot,
    )
    aggregation = SimpleNamespace(
        weak_model_aggregation_id="WMA-WMR-1",
        weak_model_run_id="WMR-1",
        strategy_signal_run_id="SSR-1",
        snapshot_id="MCS-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=slot,
        directional_score=directional_score,
        risk_level=risk_level,
        trade_permission=trade_permission,
        veto_triggered=veto_triggered,
        veto_factors_json=veto_factors_json,
        context_summary_json=context_summary,
    )
    active_results = results if results is not None else (_result_row(), _context_result_row())
    return SimpleNamespace(run=run, aggregation=aggregation, results=active_results)


def _result_row(
    *,
    model_key: str = "trend_strength_directional",
    model_role: str = "directional",
    status: str = "success",
    maturity_stage: str = "active",
    participation_mode: str = "active",
    signal_score: float | None = 0.20,
    risk_score: float | None = None,
    risk_level: str | None = None,
    trade_permission: str | None = None,
    veto_triggered: bool = False,
    confidence: float = 0.70,
    static_weight: float = 0.10,
    effective_score: float = 0.0,
    context_regime: str | None = None,
    context_score: float | None = None,
) -> Any:
    return SimpleNamespace(
        id=1,
        model_key=model_key,
        model_role=model_role,
        status=status,
        maturity_stage=maturity_stage,
        participation_mode=participation_mode,
        config_version="test",
        config_hash="hash",
        signal_score=signal_score,
        risk_score=risk_score,
        risk_level=risk_level,
        trade_permission=trade_permission,
        veto_triggered=veto_triggered,
        confidence=confidence,
        static_weight=static_weight,
        effective_score=effective_score,
        context_regime=context_regime,
        context_score=context_score,
        evidence_json='{"evidence":"present"}',
    )


def _context_result_row() -> Any:
    return _result_row(
        model_key="market_regime_context",
        model_role="context",
        maturity_stage="observe_only",
        participation_mode="observe_only",
        signal_score=None,
        confidence=0.70,
        static_weight=0.0,
        effective_score=0.0,
        context_regime="range",
        context_score=0.70,
    )
