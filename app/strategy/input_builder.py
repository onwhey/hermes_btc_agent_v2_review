"""Build StrategyEvaluationInput from a MarketContextSnapshot.

This file belongs to `app/strategy`. It restores formal Kline windows from a
snapshot_id through the stage-15 read-only repository and maps them into the
generic base/higher strategy input abstraction.
It does not request Binance, write MySQL, write Redis, send Hermes, call large
language models, read account/position state, generate final advice, or trade.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.market_context.snapshot_repository import create_default_market_context_snapshot_repository
from app.market_context.snapshot_types import MarketContextSnapshotRestoreError, MarketContextSnapshotStatus
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.strategy.types import StrategyEvaluationInput, StrategyInputBuildError


class StrategyInputBuilder:
    """Build strategy input from one created MarketContextSnapshot.

    Parameters: optional snapshot repository for tests.
    Return value: builder instance.
    Failure scenarios: missing snapshot, non-created snapshot, unsupported
    interval mapping, or failed restoration raises `StrategyInputBuildError`.
    External service access: none.
    Data impact: read-only; the stage-15 repository reads snapshot and formal
    Kline tables without repair or backfill.
    """

    def __init__(self, *, snapshot_repository: Any | None = None) -> None:
        self._snapshot_repository = snapshot_repository or create_default_market_context_snapshot_repository()

    def build_input_from_snapshot(
        self,
        db_session: Any,
        *,
        snapshot_id: str,
        symbol: str,
        base_interval_value: str,
        higher_interval_value: str,
        trace_id: str,
    ) -> StrategyEvaluationInput:
        """Restore a snapshot and create the single allowed strategy input object."""

        snapshot = self._snapshot_repository.get_snapshot_by_snapshot_id(db_session, snapshot_id=snapshot_id)
        if snapshot is None:
            raise StrategyInputBuildError("snapshot_not_found")
        if str(getattr(snapshot, "status", "")) != MarketContextSnapshotStatus.CREATED.value:
            raise StrategyInputBuildError("snapshot_not_created")
        if getattr(snapshot, "symbol", None) != symbol:
            raise StrategyInputBuildError("snapshot_symbol_mismatch")
        if getattr(snapshot, "base_interval_value", None) != base_interval_value:
            raise StrategyInputBuildError("snapshot_base_interval_mismatch")
        if getattr(snapshot, "higher_interval_value", None) != higher_interval_value:
            raise StrategyInputBuildError("snapshot_higher_interval_mismatch")
        if base_interval_value != KLINE_4H_INTERVAL_VALUE or higher_interval_value != KLINE_1D_INTERVAL_VALUE:
            raise StrategyInputBuildError("unsupported_snapshot_interval_mapping")

        try:
            restored = self._snapshot_repository.restore_snapshot_kline_windows(db_session, snapshot_id=snapshot_id)
        except MarketContextSnapshotRestoreError as exc:
            raise StrategyInputBuildError(f"snapshot_restore_failed: {exc}") from exc

        base_klines = tuple(restored.rows_4h)
        higher_klines = tuple(restored.rows_1d)
        return StrategyEvaluationInput(
            snapshot_id=snapshot_id,
            symbol=symbol,
            base_interval_value=base_interval_value,
            higher_interval_value=higher_interval_value,
            base_klines=base_klines,
            higher_klines=higher_klines,
            lookback_base_count=int(getattr(snapshot, "lookback_4h_count")),
            lookback_higher_count=int(getattr(snapshot, "lookback_1d_count")),
            latest_base_open_time_ms=int(getattr(snapshot, "latest_4h_open_time_ms")),
            latest_higher_open_time_ms=int(getattr(snapshot, "latest_1d_open_time_ms")),
            base_start_open_time_ms=int(getattr(snapshot, "start_4h_open_time_ms")),
            base_end_open_time_ms=int(getattr(snapshot, "end_4h_open_time_ms")),
            higher_start_open_time_ms=int(getattr(snapshot, "start_1d_open_time_ms")),
            higher_end_open_time_ms=int(getattr(snapshot, "end_1d_open_time_ms")),
            base_quality_check_id=_optional_int(getattr(snapshot, "latest_4h_quality_check_id", None)),
            higher_quality_check_id=_optional_int(getattr(snapshot, "latest_1d_quality_check_id", None)),
            trace_id=trace_id,
            evaluated_at_utc=now_utc(),
        )


def _optional_int(value: Any | None) -> int | None:
    if value is None:
        return None
    return int(value)


__all__ = ["StrategyInputBuilder"]

