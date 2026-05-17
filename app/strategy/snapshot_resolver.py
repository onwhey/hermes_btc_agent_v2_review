"""Resolve or lazily create the latest usable MarketContextSnapshot.

This file belongs to `app/strategy`. It connects stage 16 to stage 15 by
ensuring strategy runs use a created MarketContextSnapshot that covers the
latest closed base/higher Kline boundaries.
It does not run strategies, request Binance, write formal Kline tables, repair
Klines, send Hermes, call large language models, read account/position state,
generate final advice, connect scheduler jobs, or trade.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.time_utils import now_utc, utc_datetime_to_timestamp_ms
from app.market_context.snapshot_quality import (
    ACCEPTABLE_QUALITY_STATUSES,
    calculate_expected_latest_closed_open_time_ms,
)
from app.market_context.snapshot_repository import create_default_market_context_snapshot_repository
from app.market_context.snapshot_service import build_market_context_snapshot
from app.market_context.snapshot_types import (
    MarketContextSnapshotRequest,
    MarketContextSnapshotRestoreError,
    MarketContextSnapshotStatus,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.storage.mysql.models.market_context_snapshot import MarketContextSnapshot
from app.strategy.types import SnapshotResolveResult, StrategyRunStatus

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class SnapshotResolver:
    """Ensure a latest usable MarketContextSnapshot for strategy runs.

    Parameters: optional snapshot repository and snapshot service callable for
    tests.
    Return value: resolver instance.
    Failure scenarios: database errors or snapshot service failures are returned
    as blocked/failed structured results by the caller.
    External service access: none in this resolver; the called snapshot service
    also must not request Binance in stage 15.
    Data impact: may call stage-15 snapshot service to write a snapshot main row
    only for non-dry-run confirm-write requests when no reusable snapshot
    exists. It never writes formal Kline tables.
    """

    def __init__(
        self,
        *,
        snapshot_repository: Any | None = None,
        snapshot_service: Callable[..., Any] | None = None,
    ) -> None:
        self._snapshot_repository = snapshot_repository or create_default_market_context_snapshot_repository()
        self._snapshot_service = snapshot_service or build_market_context_snapshot

    def ensure_latest_snapshot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval_value: str,
        higher_interval_value: str,
        lookback_base_count: int,
        lookback_higher_count: int,
        dry_run: bool,
        confirm_write: bool,
        current_time_ms: int | None,
        trace_id: str,
    ) -> SnapshotResolveResult:
        """Return a reusable latest snapshot_id or lazily create one.

        This method never falls back to an older snapshot. If no reusable
        snapshot covers the theoretical latest closed Klines, it creates a new
        stage-15 snapshot only for non-dry-run confirm-write requests. Dry-run
        and unconfirmed requests are blocked without any database write side
        effect.
        """

        if base_interval_value != KLINE_4H_INTERVAL_VALUE or higher_interval_value != KLINE_1D_INTERVAL_VALUE:
            return SnapshotResolveResult(
                status=StrategyRunStatus.BLOCKED,
                snapshot_id=None,
                message="当前阶段只支持 4h + 1d 快照映射，策略运行被阻断。",
                blocked_reason="unsupported_snapshot_interval_mapping",
                trace_id=trace_id,
            )
        active_current_time_ms = current_time_ms or utc_datetime_to_timestamp_ms(now_utc())
        expected_base_open_time_ms = calculate_expected_latest_closed_open_time_ms(
            interval_value=base_interval_value,
            current_time_ms=active_current_time_ms,
        )
        expected_higher_open_time_ms = calculate_expected_latest_closed_open_time_ms(
            interval_value=higher_interval_value,
            current_time_ms=active_current_time_ms,
        )

        reusable_snapshot = self._find_first_reusable_snapshot(
            db_session,
            symbol=symbol,
            base_interval_value=base_interval_value,
            higher_interval_value=higher_interval_value,
            lookback_base_count=lookback_base_count,
            lookback_higher_count=lookback_higher_count,
            expected_base_open_time_ms=expected_base_open_time_ms,
            expected_higher_open_time_ms=expected_higher_open_time_ms,
        )
        if reusable_snapshot is not None:
            return SnapshotResolveResult(
                status=StrategyRunStatus.SUCCESS,
                snapshot_id=str(getattr(reusable_snapshot, "snapshot_id")),
                message="已复用覆盖最新已收盘 K线的 MarketContextSnapshot。",
                reused_existing_snapshot=True,
                trace_id=trace_id,
            )

        if dry_run or not confirm_write:
            return SnapshotResolveResult(
                status=StrategyRunStatus.BLOCKED,
                snapshot_id=None,
                message=(
                    "当前没有可复用的最新 MarketContextSnapshot，"
                    "dry-run 或未确认写入模式不会创建新快照，策略信号运行被阻断。"
                ),
                blocked_reason="snapshot_creation_requires_confirm_write",
                trace_id=trace_id,
            )

        snapshot_result = self._snapshot_service(
            db_session=db_session,
            request=MarketContextSnapshotRequest(
                symbol=symbol,
                base_interval_value=base_interval_value,
                higher_interval_value=higher_interval_value,
                trigger_source=TRIGGER_SOURCE_CLI,
                lookback_4h_count=lookback_base_count,
                lookback_1d_count=lookback_higher_count,
                dry_run=False,
                confirm_write=True,
                notify_on_blocked=False,
                notify_on_failed=False,
                created_by="strategy_signal_service",
                current_time_ms=active_current_time_ms,
                trace_id=trace_id,
            ),
            repository=self._snapshot_repository,
        )
        if getattr(snapshot_result, "status", None) != MarketContextSnapshotStatus.CREATED:
            return SnapshotResolveResult(
                status=StrategyRunStatus.BLOCKED,
                snapshot_id=getattr(snapshot_result, "snapshot_id", None),
                message="最新 MarketContextSnapshot 未就绪，策略信号运行被阻断。",
                blocked_reason=getattr(snapshot_result, "blocked_reason", None) or "snapshot_not_ready",
                error_message=getattr(snapshot_result, "error_message", None),
                trace_id=trace_id,
            )

        created_snapshot_id = str(getattr(snapshot_result, "snapshot_id"))
        try:
            self._snapshot_repository.restore_snapshot_kline_windows(db_session, snapshot_id=created_snapshot_id)
        except MarketContextSnapshotRestoreError as exc:
            return SnapshotResolveResult(
                status=StrategyRunStatus.BLOCKED,
                snapshot_id=created_snapshot_id,
                message="新生成的 MarketContextSnapshot 无法还原，策略信号运行被阻断。",
                blocked_reason="snapshot_restore_failed",
                error_message=str(exc),
                trace_id=trace_id,
            )
        return SnapshotResolveResult(
            status=StrategyRunStatus.SUCCESS,
            snapshot_id=created_snapshot_id,
            message="已生成并使用最新 MarketContextSnapshot。",
            created_new_snapshot=True,
            trace_id=trace_id,
        )

    def _find_first_reusable_snapshot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval_value: str,
        higher_interval_value: str,
        lookback_base_count: int,
        lookback_higher_count: int,
        expected_base_open_time_ms: int,
        expected_higher_open_time_ms: int,
    ) -> Any | None:
        candidates = self._list_reusable_snapshot_candidates(
            db_session,
            symbol=symbol,
            base_interval_value=base_interval_value,
            higher_interval_value=higher_interval_value,
            lookback_base_count=lookback_base_count,
            lookback_higher_count=lookback_higher_count,
            expected_base_open_time_ms=expected_base_open_time_ms,
            expected_higher_open_time_ms=expected_higher_open_time_ms,
        )
        for snapshot in candidates:
            snapshot_id = str(getattr(snapshot, "snapshot_id"))
            try:
                self._snapshot_repository.restore_snapshot_kline_windows(db_session, snapshot_id=snapshot_id)
            except MarketContextSnapshotRestoreError:
                continue
            return snapshot
        return None

    def _list_reusable_snapshot_candidates(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval_value: str,
        higher_interval_value: str,
        lookback_base_count: int,
        lookback_higher_count: int,
        expected_base_open_time_ms: int,
        expected_higher_open_time_ms: int,
    ) -> tuple[Any, ...]:
        custom_lookup = getattr(self._snapshot_repository, "list_reusable_created_snapshots", None)
        if callable(custom_lookup):
            return tuple(
                custom_lookup(
                    db_session,
                    symbol=symbol,
                    base_interval_value=base_interval_value,
                    higher_interval_value=higher_interval_value,
                    lookback_base_count=lookback_base_count,
                    lookback_higher_count=lookback_higher_count,
                    expected_base_open_time_ms=expected_base_open_time_ms,
                    expected_higher_open_time_ms=expected_higher_open_time_ms,
                )
            )

        _require_sqlalchemy()
        stmt = (
            select(MarketContextSnapshot)
            .where(MarketContextSnapshot.status == MarketContextSnapshotStatus.CREATED.value)
            .where(MarketContextSnapshot.symbol == symbol)
            .where(MarketContextSnapshot.base_interval_value == base_interval_value)
            .where(MarketContextSnapshot.higher_interval_value == higher_interval_value)
            .where(MarketContextSnapshot.lookback_4h_count == lookback_base_count)
            .where(MarketContextSnapshot.lookback_1d_count == lookback_higher_count)
            .where(MarketContextSnapshot.end_4h_open_time_ms >= expected_base_open_time_ms)
            .where(MarketContextSnapshot.end_1d_open_time_ms >= expected_higher_open_time_ms)
            .where(MarketContextSnapshot.latest_4h_data_quality_status.in_(ACCEPTABLE_QUALITY_STATUSES))
            .where(MarketContextSnapshot.latest_1d_data_quality_status.in_(ACCEPTABLE_QUALITY_STATUSES))
            .order_by(MarketContextSnapshot.created_at_utc.desc())
            .limit(5)
        )
        return tuple(db_session.execute(stmt).scalars().all())


def create_default_snapshot_resolver() -> SnapshotResolver:
    """Create the default stage-16 snapshot resolver."""

    return SnapshotResolver()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for SnapshotResolver queries")


__all__ = [
    "SnapshotResolver",
    "create_default_snapshot_resolver",
]
