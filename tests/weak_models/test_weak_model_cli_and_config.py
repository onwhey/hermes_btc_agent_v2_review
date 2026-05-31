from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from app.weak_models.config import WeakModelConfigError, load_weak_model_profiles
from app.weak_models.registry import WeakModelRegistry
from app.weak_models.types import EXIT_PARAMETER_ERROR, WeakModelRunResult, WeakModelRunStatus
from scripts import run_weak_models


def test_27a_migration_creates_only_weak_model_tables_without_snapshot_fk() -> None:
    migration_text = Path("migrations/versions/20260607_27a_weak_model_factor_layer.py").read_text(
        encoding="utf-8"
    )

    assert '"weak_model_run"' in migration_text
    assert '"weak_model_result"' in migration_text
    assert '"weak_model_aggregation"' in migration_text
    assert "market_kline_4h" not in migration_text
    assert "market_kline_1d" not in migration_text
    assert "fk_weak_model_run_ssr" in migration_text
    assert "fk_weak_model_run_snapshot" not in migration_text


def test_config_loader_skips_disabled_models_in_registry(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        registry_models=("trend_strength_directional", "volatility_risk_gate"),
        trend_enabled=False,
    )

    profiles = load_weak_model_profiles(tmp_path)
    enabled_models = WeakModelRegistry(config_dir=tmp_path).load_enabled_models()

    assert len(profiles) == 2
    assert {profile.model_key for profile in profiles} == {"trend_strength_directional", "volatility_risk_gate"}
    assert len(enabled_models) == 1
    assert enabled_models[0].profile.model_key == "volatility_risk_gate"


def test_config_loader_rejects_observe_only_weight(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        registry_models=("market_regime_context",),
        context_maturity="observe_only",
        context_weight=0.10,
    )

    try:
        load_weak_model_profiles(tmp_path)
    except WeakModelConfigError as exc:
        assert "observe_only" in str(exc)
    else:  # pragma: no cover - assertion path.
        raise AssertionError("expected WeakModelConfigError")


def test_cli_dry_run_is_default_and_does_not_request_write(capsys: Any) -> None:
    service = CapturingService()

    exit_code = run_weak_models.main(
        ["--strategy-signal-run-id", "SSR-1"],
        service=service,
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert service.request is not None
    assert service.request.dry_run is True
    assert service.request.confirm_write is False
    assert "database_written=false" in captured.out


def test_cli_confirm_write_passes_write_mode(capsys: Any) -> None:
    service = CapturingService(status=WeakModelRunStatus.SUCCESS, database_written=True)

    exit_code = run_weak_models.main(
        ["--strategy-signal-run-id", "SSR-1", "--confirm-write"],
        service=service,
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    assert exit_code == 0
    assert service.request is not None
    assert service.request.dry_run is False
    assert service.request.confirm_write is True
    assert "database_written=true" in capsys.readouterr().out


def test_cli_rejects_scheduler_trigger_source() -> None:
    exit_code = run_weak_models.main(
        ["--strategy-signal-run-id", "SSR-1", "--trigger-source", "scheduler"],
        service=CapturingService(),
        settings=SimpleNamespace(),
        session_scope_factory=_fake_session_scope,
    )

    assert exit_code == EXIT_PARAMETER_ERROR


class CapturingService:
    def __init__(
        self,
        *,
        status: WeakModelRunStatus = WeakModelRunStatus.DRY_RUN,
        database_written: bool = False,
    ) -> None:
        self.status = status
        self.database_written = database_written
        self.request: Any | None = None

    def run_weak_models_for_strategy_signal(self, db_session: Any, request: Any) -> WeakModelRunResult:
        self.request = request
        return WeakModelRunResult(
            status=self.status,
            exit_code=0,
            weak_model_run_id="WMR-1",
            trace_id="trace",
            strategy_signal_run_id=request.strategy_signal_run_id,
            snapshot_id="MCS-1",
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            database_written=self.database_written,
            database_action="created" if self.database_written else "dry_run",
        )


@contextmanager
def _fake_session_scope(**_: Any) -> Iterator[SimpleNamespace]:
    yield SimpleNamespace()


def _write_config(
    root: Path,
    *,
    registry_models: tuple[str, ...],
    trend_enabled: bool = True,
    context_maturity: str = "observe_only",
    context_weight: float = 0.0,
) -> None:
    registry = "models:\n" + "".join(f"  - {model}\n" for model in registry_models)
    (root / "registry.yaml").write_text(registry, encoding="utf-8")
    (root / "trend_strength_directional.yaml").write_text(
        _profile_yaml("trend_strength_directional", "directional", enabled=trend_enabled),
        encoding="utf-8",
    )
    (root / "volatility_risk_gate.yaml").write_text(
        _profile_yaml("volatility_risk_gate", "risk"),
        encoding="utf-8",
    )
    (root / "market_regime_context.yaml").write_text(
        _profile_yaml(
            "market_regime_context",
            "context",
            maturity_stage=context_maturity,
            static_weight=context_weight,
        ),
        encoding="utf-8",
    )


def _profile_yaml(
    model_key: str,
    role: str,
    *,
    enabled: bool = True,
    maturity_stage: str = "active",
    static_weight: float = 0.10,
) -> str:
    return f"""model_key: {model_key}
model_name: {model_key}
enabled: {str(enabled).lower()}
maturity_stage: {maturity_stage}
model_role: {role}
model_version: v1
config_version: test
config_hash: auto
input_intervals:
  - 4h
  - 1d
input_window:
  base_interval_limit: 120
  higher_interval_limit: 80
static_weight: {static_weight}
description: test
params:
  ma_fast: 20
"""
