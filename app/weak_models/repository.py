"""Repository for 27A weak model / factor layer.

本文件属于 `app/weak_models` 模块，负责读取 SSR 和 SSR 绑定的
MarketContextSnapshot，并在 confirm-write 时写入 `weak_model_run`、
`weak_model_result`、`weak_model_aggregation`。
本文件不负责弱模型计算，不请求 Binance，不发送 Hermes，不读写 Redis，不调用
DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.time_utils import ensure_utc_aware, now_utc
from app.market_context.snapshot_repository import (
    MarketContextSnapshotRepository,
    create_default_market_context_snapshot_repository,
)
from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.storage.mysql.models.weak_model import WeakModelAggregation, WeakModelResult, WeakModelRun
from app.weak_models.types import (
    WeakModelAggregationSummary,
    WeakModelResultPayload,
    WeakModelRunPersistencePayload,
    json_dumps_compact,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class WeakModelRepository:
    """Read stage-16/15 inputs and write compact 27A audit rows."""

    def __init__(self, *, snapshot_repository: MarketContextSnapshotRepository | Any | None = None) -> None:
        self._snapshot_repository = snapshot_repository or create_default_market_context_snapshot_repository()

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any | None:
        """Return one SSR row by run_id without mutating data."""

        _require_sqlalchemy()
        stmt = select(StrategySignalRun).where(StrategySignalRun.run_id == run_id)
        return db_session.execute(stmt).scalars().first()

    def get_snapshot_by_snapshot_id(self, db_session: Any, *, snapshot_id: str) -> Any | None:
        """Return the SSR-bound snapshot row without mutating data."""

        return self._snapshot_repository.get_snapshot_by_snapshot_id(db_session, snapshot_id=snapshot_id)

    def restore_snapshot_kline_windows(self, db_session: Any, *, snapshot_id: str) -> Any:
        """Restore formal Kline windows through the stage-15 read-only contract."""

        return self._snapshot_repository.restore_snapshot_kline_windows(db_session, snapshot_id=snapshot_id)

    def upsert_run(self, db_session: Any, *, payload: WeakModelRunPersistencePayload) -> tuple[Any, str]:
        """Insert or update one `weak_model_run` row by weak_model_run_id."""

        _require_sqlalchemy()
        now = now_utc()
        row = self._get_run(db_session, weak_model_run_id=payload.weak_model_run_id)
        action = "updated"
        if row is None:
            row = WeakModelRun(weak_model_run_id=payload.weak_model_run_id, created_at_utc=now)
            db_session.add(row)
            action = "created"
        row.pipeline_run_id = payload.pipeline_run_id
        row.strategy_signal_run_id = payload.strategy_signal_run_id
        row.snapshot_id = payload.snapshot_id
        row.symbol = payload.symbol
        row.base_interval = payload.base_interval
        row.higher_interval = payload.higher_interval
        row.kline_slot_utc = ensure_utc_aware(payload.kline_slot_utc)
        row.run_status = payload.run_status
        row.trigger_source = payload.trigger_source
        row.model_count_total = payload.model_count_total
        row.model_count_enabled = payload.model_count_enabled
        row.model_count_executed = payload.model_count_executed
        row.model_count_failed = payload.model_count_failed
        row.trace_id = payload.trace_id
        row.updated_at_utc = now
        row.details_json = json_dumps_compact(dict(payload.details))
        _flush_if_possible(db_session)
        return row, action

    def upsert_result(self, db_session: Any, *, payload: WeakModelResultPayload) -> tuple[Any, str]:
        """Insert or update one `weak_model_result` row by result id."""

        _require_sqlalchemy()
        row = self._get_result(db_session, weak_model_result_id=payload.weak_model_result_id)
        action = "updated"
        if row is None:
            row = WeakModelResult(weak_model_result_id=payload.weak_model_result_id)
            db_session.add(row)
            action = "created"
        profile = payload.profile
        output = payload.output
        input_data = payload.input_data
        row.weak_model_run_id = payload.weak_model_run_id
        row.model_key = profile.model_key
        row.model_role = profile.model_role
        row.model_version = profile.model_version
        row.config_version = profile.config_version
        row.config_hash = profile.config_hash
        row.maturity_stage = profile.maturity_stage
        row.enabled = bool(profile.enabled)
        row.participation_mode = profile.participation_mode
        row.symbol = input_data.symbol
        row.base_interval = input_data.base_interval
        row.higher_interval = input_data.higher_interval
        row.kline_slot_utc = input_data.kline_slot_utc
        row.snapshot_id = input_data.snapshot_id
        row.status = output.status.value
        row.error_code = output.error_code
        row.error_message = output.error_message
        row.signal_score = _decimal_or_none(output.signal_score)
        row.direction_bias = output.direction_bias
        row.risk_score = _decimal_or_none(output.risk_score)
        row.risk_level = output.risk_level
        row.trade_permission = output.trade_permission
        row.veto_triggered = bool(output.veto_triggered)
        row.confirmation_score = _decimal_or_none(output.confirmation_score)
        row.supports_direction = output.supports_direction
        row.context_regime = output.context_regime
        row.context_score = _decimal_or_none(output.context_score)
        row.confidence = _decimal_or_zero(output.confidence)
        row.static_weight = _decimal_or_zero(profile.static_weight)
        row.effective_score = _decimal_or_zero(output.effective_score)
        row.input_summary_json = json_dumps_compact(dict(output.input_summary))
        row.evidence_json = json_dumps_compact(dict(output.evidence))
        row.raw_output_json = json_dumps_compact(dict(output.raw_output))
        row.created_at_utc = now_utc()
        _flush_if_possible(db_session)
        return row, action

    def upsert_aggregation(self, db_session: Any, *, aggregation: WeakModelAggregationSummary) -> tuple[Any, str]:
        """Insert or update one `weak_model_aggregation` row by aggregation id."""

        _require_sqlalchemy()
        row = self._get_aggregation(db_session, weak_model_aggregation_id=aggregation.weak_model_aggregation_id)
        action = "updated"
        if row is None:
            row = WeakModelAggregation(weak_model_aggregation_id=aggregation.weak_model_aggregation_id)
            db_session.add(row)
            action = "created"
        row.weak_model_run_id = aggregation.weak_model_run_id
        row.pipeline_run_id = aggregation.pipeline_run_id
        row.strategy_signal_run_id = aggregation.strategy_signal_run_id
        row.snapshot_id = aggregation.snapshot_id
        row.symbol = aggregation.symbol
        row.base_interval = aggregation.base_interval
        row.higher_interval = aggregation.higher_interval
        row.kline_slot_utc = aggregation.kline_slot_utc
        row.directional_score = _decimal_or_zero(aggregation.directional_score)
        row.directional_bias = aggregation.directional_bias
        row.directional_confidence = _decimal_or_zero(aggregation.directional_confidence)
        row.risk_level = aggregation.risk_level
        row.trade_permission = aggregation.trade_permission
        row.veto_triggered = bool(aggregation.veto_triggered)
        row.supporting_factors_json = json_dumps_compact(tuple(aggregation.supporting_factors))
        row.opposing_factors_json = json_dumps_compact(tuple(aggregation.opposing_factors))
        row.conflict_factors_json = json_dumps_compact(tuple(aggregation.conflict_factors))
        row.low_confidence_factors_json = json_dumps_compact(tuple(aggregation.low_confidence_factors))
        row.veto_factors_json = json_dumps_compact(tuple(aggregation.veto_factors))
        row.context_summary_json = json_dumps_compact(dict(aggregation.context_summary))
        row.summary_text = aggregation.summary_text
        row.created_at_utc = now_utc()
        row.details_json = json_dumps_compact(dict(aggregation.details))
        _flush_if_possible(db_session)
        return row, action

    def _get_run(self, db_session: Any, *, weak_model_run_id: str) -> Any | None:
        stmt = select(WeakModelRun).where(WeakModelRun.weak_model_run_id == weak_model_run_id)
        return db_session.execute(stmt).scalars().first()

    def _get_result(self, db_session: Any, *, weak_model_result_id: str) -> Any | None:
        stmt = select(WeakModelResult).where(WeakModelResult.weak_model_result_id == weak_model_result_id)
        return db_session.execute(stmt).scalars().first()

    def _get_aggregation(self, db_session: Any, *, weak_model_aggregation_id: str) -> Any | None:
        stmt = select(WeakModelAggregation).where(WeakModelAggregation.weak_model_aggregation_id == weak_model_aggregation_id)
        return db_session.execute(stmt).scalars().first()


def create_default_weak_model_repository() -> WeakModelRepository:
    """Create the default 27A weak model repository."""

    return WeakModelRepository()


def _decimal_or_none(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _decimal_or_zero(value: float | None) -> Decimal:
    return Decimal("0") if value is None else Decimal(str(value))


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for 27A weak model repository.")


__all__ = ["WeakModelRepository", "create_default_weak_model_repository"]
