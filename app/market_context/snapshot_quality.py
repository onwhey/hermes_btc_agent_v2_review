"""Readiness checks for stage-15 MarketContextSnapshot generation.

This file belongs to `app/market_context`. It performs read-only checks over
formal 4h/1d Kline windows plus latest collector and quality records before a
snapshot can be used by later strategy stages.
It does not request Binance, write MySQL, write Redis, send Hermes, call
DeepSeek or any large language model, generate advice, repair Klines, or trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.time_utils import UTC, timestamp_ms_to_utc_datetime, utc_datetime_to_timestamp_ms
from app.market_data.kline_constants import (
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
)

ACCEPTABLE_QUALITY_STATUSES = frozenset({"passed", "healthy"})
BLOCKING_COLLECTOR_STATUSES = frozenset({"failed", "blocked", "error", "critical"})


@dataclass(frozen=True)
class IntervalSnapshotContext:
    """Read-only context for one interval used by MarketContextSnapshot."""

    interval_value: str
    interval_ms: int
    lookback_count: int
    rows: tuple[Any, ...]
    latest_collector_event: Any | None
    latest_quality_check: Any | None
    expected_latest_open_time_ms: int

    @property
    def actual_count(self) -> int:
        """Return the number of formal Klines read for this interval."""

        return len(self.rows)

    @property
    def latest_row(self) -> Any | None:
        """Return the latest row by UTC open time, if available."""

        return self.rows[-1] if self.rows else None

    @property
    def start_open_time_ms(self) -> int | None:
        """Return the first open time in the read window."""

        return _row_int(self.rows[0], "open_time_ms") if self.rows else None

    @property
    def end_open_time_ms(self) -> int | None:
        """Return the final open time in the read window."""

        return _row_int(self.rows[-1], "open_time_ms") if self.rows else None

    @property
    def latest_open_time_ms(self) -> int | None:
        """Return the latest formal Kline open time."""

        return self.end_open_time_ms

    @property
    def latest_quality_status(self) -> str | None:
        """Return the latest quality row status."""

        status = _row_value(self.latest_quality_check, "status")
        return str(status) if status is not None else None

    @property
    def latest_collector_event_id(self) -> int | None:
        """Return the latest collector event id, if available."""

        return _row_int(self.latest_collector_event, "id")

    @property
    def latest_quality_check_id(self) -> int | None:
        """Return the latest daily quality check id, if available."""

        return _row_int(self.latest_quality_check, "id")


@dataclass(frozen=True)
class SnapshotReadinessReport:
    """Combined quality result for 4h + 1d snapshot inputs."""

    symbol: str
    current_time_ms: int
    base_context: IntervalSnapshotContext
    higher_context: IntervalSnapshotContext
    blocked_reason: str | None = None

    @property
    def passed(self) -> bool:
        """Return whether snapshot generation may create a normal payload."""

        return self.blocked_reason is None


def check_market_context_snapshot_readiness(
    *,
    symbol: str,
    current_time_ms: int,
    rows_4h: tuple[Any, ...],
    rows_1d: tuple[Any, ...],
    latest_4h_collector_event: Any | None,
    latest_1d_collector_event: Any | None,
    latest_4h_quality_check: Any | None,
    latest_1d_quality_check: Any | None,
    lookback_4h_count: int,
    lookback_1d_count: int,
) -> SnapshotReadinessReport:
    """Check whether 4h and 1d data can form a usable fact snapshot.

    Parameters: formal Kline windows, latest collector/quality records, lookback
    counts, and current UTC milliseconds supplied by the service.
    Return value: `SnapshotReadinessReport`; blocked reasons are explicit and
    do not trigger repair, backfill, or Kline writes.
    Failure scenarios: unexpected row shape can raise conversion errors, which
    the service converts to `failed`.
    External service access: none.
    Data impact: read-only; this function never writes MySQL or sends Hermes.
    """

    base_context = IntervalSnapshotContext(
        interval_value=KLINE_4H_INTERVAL_VALUE,
        interval_ms=KLINE_4H_INTERVAL_MS,
        lookback_count=lookback_4h_count,
        rows=tuple(sorted(rows_4h, key=lambda row: _row_int(row, "open_time_ms") or 0)),
        latest_collector_event=latest_4h_collector_event,
        latest_quality_check=latest_4h_quality_check,
        expected_latest_open_time_ms=_expected_latest_closed_4h_open_time_ms(current_time_ms),
    )
    higher_context = IntervalSnapshotContext(
        interval_value=KLINE_1D_INTERVAL_VALUE,
        interval_ms=KLINE_1D_INTERVAL_MS,
        lookback_count=lookback_1d_count,
        rows=tuple(sorted(rows_1d, key=lambda row: _row_int(row, "open_time_ms") or 0)),
        latest_collector_event=latest_1d_collector_event,
        latest_quality_check=latest_1d_quality_check,
        expected_latest_open_time_ms=_expected_latest_closed_1d_open_time_ms(current_time_ms),
    )

    for context, label in ((base_context, "4h"), (higher_context, "1d")):
        reason = _first_blocking_reason_for_interval(context, label=label, current_time_ms=current_time_ms)
        if reason is not None:
            return SnapshotReadinessReport(
                symbol=symbol,
                current_time_ms=current_time_ms,
                base_context=base_context,
                higher_context=higher_context,
                blocked_reason=reason,
            )

    return SnapshotReadinessReport(
        symbol=symbol,
        current_time_ms=current_time_ms,
        base_context=base_context,
        higher_context=higher_context,
    )


def _first_blocking_reason_for_interval(
    context: IntervalSnapshotContext,
    *,
    label: str,
    current_time_ms: int,
) -> str | None:
    if not context.rows:
        return f"{label} 数据未初始化，无法生成 MarketContextSnapshot。"
    if context.actual_count < context.lookback_count:
        return (
            f"{label} K线数量不足，要求 {context.lookback_count} 根，"
            f"实际读取 {context.actual_count} 根。"
        )

    latest_open_time_ms = context.latest_open_time_ms
    if latest_open_time_ms is None:
        return f"{label} 最新 K线 open_time 缺失，无法生成 MarketContextSnapshot。"
    if latest_open_time_ms > context.expected_latest_open_time_ms:
        return f"{label} 最新 K线晚于理论最新已收盘 K线，疑似未收盘 K线误写。"
    if latest_open_time_ms < context.expected_latest_open_time_ms:
        lag_bars = (context.expected_latest_open_time_ms - latest_open_time_ms) // context.interval_ms
        return f"{label} 数据滞后理论最新已收盘 K线 {lag_bars} 根。"

    collector_status = _row_value(context.latest_collector_event, "status")
    if collector_status is not None and str(collector_status) in BLOCKING_COLLECTOR_STATUSES:
        return f"{label} 最近一次增量采集状态为 {collector_status}，快照生成被阻断。"

    quality_status = context.latest_quality_status
    if quality_status is None:
        return f"{label} 最近每日复核状态缺失，快照生成被阻断。"
    if quality_status not in ACCEPTABLE_QUALITY_STATUSES:
        return f"{label} 最近每日复核状态为 {quality_status}，快照生成被阻断。"

    unclosed_reason = _first_unclosed_or_misaligned_reason(context, label=label, current_time_ms=current_time_ms)
    if unclosed_reason is not None:
        return unclosed_reason

    return _first_continuity_reason(context, label=label)


def _first_unclosed_or_misaligned_reason(
    context: IntervalSnapshotContext,
    *,
    label: str,
    current_time_ms: int,
) -> str | None:
    for row in context.rows:
        open_time_ms = _required_row_int(row, "open_time_ms")
        close_time_ms = _required_row_int(row, "close_time_ms")
        if open_time_ms % context.interval_ms != 0:
            return f"{label} K线 open_time 未对齐 UTC 周期边界。"
        if close_time_ms != open_time_ms + context.interval_ms - 1:
            return f"{label} K线 close_time 与周期长度不匹配。"
        if close_time_ms >= current_time_ms:
            return f"{label} 窗口包含未收盘 K线，快照生成被阻断。"
    return None


def _first_continuity_reason(context: IntervalSnapshotContext, *, label: str) -> str | None:
    previous_open_time_ms: int | None = None
    for row in context.rows:
        current_open_time_ms = _required_row_int(row, "open_time_ms")
        if previous_open_time_ms is not None:
            expected_next = previous_open_time_ms + context.interval_ms
            # 连续性必须基于 open_time_ms。若相邻 open time 差值不是周期毫秒数，
            # 后续策略不能证明同一 snapshot 覆盖的是完整事实窗口。
            if current_open_time_ms != expected_next:
                return (
                    f"{label} K线窗口不连续，上一根 open_time_ms={previous_open_time_ms}，"
                    f"当前 open_time_ms={current_open_time_ms}。"
                )
        previous_open_time_ms = current_open_time_ms
    return None


def _expected_latest_closed_4h_open_time_ms(current_time_ms: int) -> int:
    current_time_utc = timestamp_ms_to_utc_datetime(current_time_ms)
    bucket_hour = (current_time_utc.hour // 4) * 4
    current_bucket = current_time_utc.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)
    return utc_datetime_to_timestamp_ms(current_bucket - timedelta(hours=4))


def _expected_latest_closed_1d_open_time_ms(current_time_ms: int) -> int:
    if current_time_ms <= KLINE_1D_INTERVAL_MS:
        raise ValueError("current_time_ms is too early to calculate latest closed 1d Kline")
    current_day_open_time_ms = (current_time_ms // KLINE_1D_INTERVAL_MS) * KLINE_1D_INTERVAL_MS
    return current_day_open_time_ms - KLINE_1D_INTERVAL_MS


def _row_value(row: Any, field_name: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(field_name)
    return getattr(row, field_name, None)


def _row_int(row: Any, field_name: str) -> int | None:
    value = _row_value(row, field_name)
    if value is None:
        return None
    return int(value)


def _required_row_int(row: Any, field_name: str) -> int:
    value = _row_int(row, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required for MarketContextSnapshot readiness check")
    return value


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "IntervalSnapshotContext",
    "SnapshotReadinessReport",
    "check_market_context_snapshot_readiness",
]
