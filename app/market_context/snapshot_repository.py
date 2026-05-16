"""Repository for MarketContextSnapshot storage and read-only context queries.

This file belongs to `app/market_context`. It coordinates storage-layer reads
for formal 4h/1d Klines, latest collector and quality rows, and writes only the
stage-15 snapshot tables through a caller-provided session.
It does not request Binance, write formal Kline tables, write Redis, send
Hermes, call DeepSeek or other large language models, generate trading advice,
read private trading state, or perform trading.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.time_utils import now_utc, timestamp_ms_to_utc_datetime
from app.market_context.snapshot_types import SnapshotKlineRef, SnapshotPersistencePayload
from app.market_data.collector.kline_1d_incremental_types import KLINE_1D_INCREMENTAL_EVENT_TYPE
from app.market_data.collector.types import COLLECTOR_EVENT_TYPE
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_integrity.kline_1d_integrity_types import CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY
from app.market_data.kline_quality.types import CHECK_TYPE_DAILY_KLINE_INTEGRITY
from app.storage.mysql.models.collector_event_log import CollectorEventLog
from app.storage.mysql.models.data_quality_check import DataQualityCheck
from app.storage.mysql.models.market_context_snapshot import (
    MarketContextSnapshot,
    MarketContextSnapshotKlineRef,
)
from app.storage.mysql.repositories.market_kline_1d_repository import (
    MarketKline1dRepository,
    create_default_market_kline_1d_repository,
)
from app.storage.mysql.repositories.market_kline_4h_repository import (
    MarketKline4hRepository,
    create_default_market_kline_4h_repository,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class MarketContextSnapshotRepository:
    """Data access helper for market context snapshots.

    Parameters: optional Kline repositories for test injection.
    Return value: repository instance.
    Failure scenarios: database errors propagate to the caller.
    External service access: none.
    Data impact: reads formal Kline/event/quality tables and writes only
    `market_context_snapshot` plus `market_context_snapshot_kline_ref`.
    """

    def __init__(
        self,
        *,
        kline_4h_repository: MarketKline4hRepository | None = None,
        kline_1d_repository: MarketKline1dRepository | None = None,
    ) -> None:
        self._kline_4h_repository = kline_4h_repository or create_default_market_kline_4h_repository()
        self._kline_1d_repository = kline_1d_repository or create_default_market_kline_1d_repository()

    def list_recent_4h_klines(self, db_session: Any, *, symbol: str, limit: int) -> list[Any]:
        """Read recent 4h formal Klines without modifying them."""

        return self._kline_4h_repository.list_recent(
            db_session,
            symbol=symbol,
            interval_value=KLINE_4H_INTERVAL_VALUE,
            limit=limit,
        )

    def list_recent_1d_klines(self, db_session: Any, *, symbol: str, limit: int) -> list[Any]:
        """Read recent 1d formal Klines without modifying them."""

        return self._kline_1d_repository.list_recent(db_session, symbol=symbol, limit=limit)

    def get_latest_collector_event(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
    ) -> Any | None:
        """Return the latest collector event for a snapshot dependency interval.

        Parameters: caller-owned session, symbol, and interval.
        Return value: latest `collector_event_log` row or `None`.
        Failure scenarios: unsupported interval raises `ValueError`; database errors propagate.
        External service access: none.
        Data impact: reads only `collector_event_log`.
        """

        _require_sqlalchemy()
        event_type = _collector_event_type_for_interval(interval_value)
        stmt = (
            select(CollectorEventLog)
            .where(CollectorEventLog.symbol == symbol)
            .where(CollectorEventLog.interval_value == interval_value)
            .where(CollectorEventLog.event_type == event_type)
            .order_by(CollectorEventLog.started_at_utc.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_daily_quality_check(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
    ) -> Any | None:
        """Return the latest daily integrity quality row for one interval."""

        _require_sqlalchemy()
        check_type = _daily_quality_check_type_for_interval(interval_value)
        stmt = (
            select(DataQualityCheck)
            .where(DataQualityCheck.symbol == symbol)
            .where(DataQualityCheck.interval_value == interval_value)
            .where(DataQualityCheck.check_type == check_type)
            .order_by(DataQualityCheck.created_at_utc.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_snapshot_with_refs(
        self,
        db_session: Any,
        payload: SnapshotPersistencePayload,
    ) -> MarketContextSnapshot:
        """Persist one snapshot and its Kline-reference rows.

        Parameters: caller-owned session and fully prepared persistence payload.
        Return value: created snapshot ORM row.
        Failure scenarios: database insert/unique errors propagate.
        External service access: none.
        Data impact: writes only snapshot tables; it never writes `market_kline_4h`
        or `market_kline_1d`, and never commits.
        """

        created_at_utc = now_utc()
        snapshot = MarketContextSnapshot(
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval_value=payload.base_interval_value,
            higher_interval_value=payload.higher_interval_value,
            status=payload.status.value,
            blocked_reason=payload.blocked_reason,
            error_message=payload.error_message,
            latest_4h_open_time_ms=payload.latest_4h_open_time_ms,
            latest_4h_open_time_utc=_open_time_ms_to_utc(payload.latest_4h_open_time_ms),
            latest_1d_open_time_ms=payload.latest_1d_open_time_ms,
            latest_1d_open_time_utc=_open_time_ms_to_utc(payload.latest_1d_open_time_ms),
            lookback_4h_count=payload.lookback_4h_count,
            lookback_1d_count=payload.lookback_1d_count,
            actual_4h_count=payload.actual_4h_count,
            actual_1d_count=payload.actual_1d_count,
            start_4h_open_time_ms=payload.start_4h_open_time_ms,
            end_4h_open_time_ms=payload.end_4h_open_time_ms,
            start_1d_open_time_ms=payload.start_1d_open_time_ms,
            end_1d_open_time_ms=payload.end_1d_open_time_ms,
            latest_4h_data_quality_status=payload.latest_4h_data_quality_status,
            latest_1d_data_quality_status=payload.latest_1d_data_quality_status,
            latest_4h_collector_event_id=payload.latest_4h_collector_event_id,
            latest_1d_collector_event_id=payload.latest_1d_collector_event_id,
            latest_4h_quality_check_id=payload.latest_4h_quality_check_id,
            latest_1d_quality_check_id=payload.latest_1d_quality_check_id,
            snapshot_payload_json=_normalize_json_text(payload.snapshot_payload_json),
            created_by=payload.created_by,
            trigger_source=payload.trigger_source,
            trace_id=payload.trace_id,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(snapshot)
        for ref in payload.refs:
            db_session.add(
                MarketContextSnapshotKlineRef(
                    snapshot_id=payload.snapshot_id,
                    symbol=ref.symbol,
                    interval_value=ref.interval_value,
                    market_kline_id=ref.market_kline_id,
                    open_time_ms=ref.open_time_ms,
                    open_time_utc=timestamp_ms_to_utc_datetime(ref.open_time_ms),
                    sequence_no=ref.sequence_no,
                    created_at_utc=created_at_utc,
                )
            )
        if hasattr(db_session, "flush"):
            db_session.flush()
        return snapshot


def create_default_market_context_snapshot_repository() -> MarketContextSnapshotRepository:
    """Create the default stage-15 market context snapshot repository."""

    return MarketContextSnapshotRepository()


def _collector_event_type_for_interval(interval_value: str) -> str:
    if interval_value == KLINE_4H_INTERVAL_VALUE:
        return COLLECTOR_EVENT_TYPE
    if interval_value == KLINE_1D_INTERVAL_VALUE:
        return KLINE_1D_INCREMENTAL_EVENT_TYPE
    raise ValueError(f"unsupported snapshot collector interval_value={interval_value}")


def _daily_quality_check_type_for_interval(interval_value: str) -> str:
    if interval_value == KLINE_4H_INTERVAL_VALUE:
        return CHECK_TYPE_DAILY_KLINE_INTEGRITY
    if interval_value == KLINE_1D_INTERVAL_VALUE:
        return CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY
    raise ValueError(f"unsupported snapshot quality interval_value={interval_value}")


def _open_time_ms_to_utc(value: int | None) -> Any | None:
    if value is None:
        return None
    return timestamp_ms_to_utc_datetime(value)


def _normalize_json_text(value: str | Mapping[str, object]) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for MarketContextSnapshotRepository queries")


__all__ = [
    "MarketContextSnapshotRepository",
    "create_default_market_context_snapshot_repository",
]
