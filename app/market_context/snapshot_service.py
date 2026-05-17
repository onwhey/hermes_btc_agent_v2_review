"""Market context snapshot service orchestration.

本文件属于 app/market_context 模块，负责 BTCUSDT 4h + 1d 市场事实快照的业务编排。
本文件不负责策略判断、交易建议、自动交易、账户读取、持仓读取或正式 K线写入。
本文件主要被 scripts/build_market_context_snapshot.py::main 调用，也可被测试直接调用。
本文件不请求外部服务，不请求 Binance REST，不请求 Binance WebSocket。
本文件会读取 MySQL 中的正式 K线、采集事件和质量复核记录；仅在 confirm-write 时写入快照表。
本文件不读写 Redis。
本文件只在调用方显式开启 notify-on-blocked / notify-on-failed 时通过 app/alerting 发送 Hermes。
本文件不调用 DeepSeek 或任何大模型。
本文件不涉及交易执行。

调用链：

用户 CLI
    ↓
scripts/build_market_context_snapshot.py::main
    ↓
app/market_context/snapshot_service.py::build_market_context_snapshot
    ↓
app/market_context/snapshot_repository.py::list_recent_4h_klines
app/market_context/snapshot_repository.py::list_recent_1d_klines
app/market_context/snapshot_repository.py::get_latest_collector_event
app/market_context/snapshot_repository.py::get_latest_daily_quality_check
    ↓
app/market_context/snapshot_quality.py::check_market_context_snapshot_readiness
    ↓
app/market_context/snapshot_builder.py::build_market_context_snapshot_payload
app/market_context/snapshot_repository.py::create_snapshot
    ↓
app/market_context/snapshot_alerts.py::send_market_context_snapshot_alert_and_adjust_exit_code
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.core.time_utils import now_utc, timestamp_ms_to_utc_datetime, utc_datetime_to_timestamp_ms
from app.market_context.snapshot_alerts import (
    send_market_context_snapshot_alert_and_adjust_exit_code,
)
from app.market_context.snapshot_builder import (
    build_blocked_snapshot_payload,
    build_failed_snapshot_payload,
    build_market_context_snapshot_payload,
)
from app.market_context.snapshot_quality import (
    SnapshotReadinessReport,
    check_market_context_snapshot_readiness,
)
from app.market_context.snapshot_repository import create_default_market_context_snapshot_repository
from app.market_context.snapshot_types import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    MarketContextSnapshotRequest,
    MarketContextSnapshotResult,
    MarketContextSnapshotStatus,
)
from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

SUPPORTED_SYMBOL = DEFAULT_KLINE_SYMBOL
SUPPORTED_BASE_INTERVAL = KLINE_4H_INTERVAL_VALUE
SUPPORTED_HIGHER_INTERVAL = KLINE_1D_INTERVAL_VALUE
ALLOWED_SNAPSHOT_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})


def build_market_context_snapshot(
    *,
    db_session: Any,
    request: MarketContextSnapshotRequest,
    repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
) -> MarketContextSnapshotResult:
    """Build one BTCUSDT 4h + 1d MarketContextSnapshot.

    参数：
        db_session: MySQL session-like object. 本方法会读取正式 K线、事件和复核记录；
            仅在 request.confirm_write=True 且非 dry-run 时写入快照表。
        request: 快照请求，包含周期、lookback、dry-run、confirm-write 和告警开关。
        repository: 可选 repository 注入，测试可传入 fake repository；默认使用
            create_default_market_context_snapshot_repository。
        alert_sender: 可选 Hermes sender，只有 notify-on-blocked / notify-on-failed 开启时使用。
        alert_repository: 可选告警 repository，只有发送 Hermes 时用于记录 alert_message。

    返回：
        MarketContextSnapshotResult，包含 created / blocked / failed 状态、退出码和简要原因。

    失败场景：
        参数非法、K线未初始化、数据滞后、最近复核失败、数量不足、未收盘、
        K线不连续、数据库读取/写入异常或 Hermes 发送异常。

    外部服务：
        本方法自身不请求 Binance、不请求 Redis、不调用大模型；Hermes 仅按显式参数触发。

    明确不负责：
        不生成交易建议，不写 market_kline_4h / market_kline_1d，不自动回补，不接入 scheduler。
    """

    trace_id = request.trace_id or uuid.uuid4().hex
    generated_at_utc = now_utc()
    current_time_ms = request.current_time_ms or utc_datetime_to_timestamp_ms(generated_at_utc)
    snapshot_id = _build_snapshot_id(
        request=request,
        generated_at_utc=generated_at_utc,
        trace_id=trace_id,
    )

    invalid_result = _validate_market_context_snapshot_request(
        request=request,
        snapshot_id=snapshot_id,
        trace_id=trace_id,
    )
    if invalid_result is not None:
        if request.notify_on_failed:
            return send_market_context_snapshot_alert_and_adjust_exit_code(
                request=request,
                result=invalid_result,
                db_session=db_session,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )
        return invalid_result

    active_repository = repository or create_default_market_context_snapshot_repository()

    try:
        rows_4h = tuple(
            active_repository.list_recent_4h_klines(
                db_session,
                symbol=request.symbol,
                limit=request.lookback_4h_count,
            )
        )
        rows_1d = tuple(
            active_repository.list_recent_1d_klines(
                db_session,
                symbol=request.symbol,
                limit=request.lookback_1d_count,
            )
        )
        latest_4h_collector_event = active_repository.get_latest_collector_event(
            db_session,
            symbol=request.symbol,
            interval_value=request.base_interval_value,
        )
        latest_1d_collector_event = active_repository.get_latest_collector_event(
            db_session,
            symbol=request.symbol,
            interval_value=request.higher_interval_value,
        )
        latest_4h_quality_check = active_repository.get_latest_daily_quality_check(
            db_session,
            symbol=request.symbol,
            interval_value=request.base_interval_value,
        )
        latest_1d_quality_check = active_repository.get_latest_daily_quality_check(
            db_session,
            symbol=request.symbol,
            interval_value=request.higher_interval_value,
        )

        readiness = check_market_context_snapshot_readiness(
            symbol=request.symbol,
            current_time_ms=current_time_ms,
            rows_4h=rows_4h,
            rows_1d=rows_1d,
            latest_4h_collector_event=latest_4h_collector_event,
            latest_1d_collector_event=latest_1d_collector_event,
            latest_4h_quality_check=latest_4h_quality_check,
            latest_1d_quality_check=latest_1d_quality_check,
            lookback_4h_count=request.lookback_4h_count,
            lookback_1d_count=request.lookback_1d_count,
        )

        if not readiness.passed:
            result = _build_blocked_snapshot_result(
                request=request,
                readiness=readiness,
                snapshot_id=snapshot_id,
                trace_id=trace_id,
            )
            if request.confirm_write and not request.dry_run:
                blocked_payload = build_blocked_snapshot_payload(
                    snapshot_id=snapshot_id,
                    readiness=readiness,
                    blocked_reason=readiness.blocked_reason or "市场上下文快照前置检查未通过。",
                    trigger_source=request.trigger_source,
                    created_by=request.created_by,
                    trace_id=trace_id,
                )
                snapshot_row = active_repository.create_snapshot(db_session, blocked_payload)
                _commit_if_possible(db_session)
                result = replace(
                    result,
                    snapshot_row_id=getattr(snapshot_row, "id", None),
                    message="市场上下文快照生成受阻，blocked 记录已写入。",
                )
            return _maybe_send_market_context_alert(
                request=request,
                result=result,
                db_session=db_session,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )

        created_payload = build_market_context_snapshot_payload(
            snapshot_id=snapshot_id,
            readiness=readiness,
            trigger_source=request.trigger_source,
            created_by=request.created_by,
            trace_id=trace_id,
        )
        result = _build_created_snapshot_result(
            request=request,
            readiness=readiness,
            snapshot_id=snapshot_id,
            trace_id=trace_id,
        )
        if request.dry_run:
            return replace(
                result,
                message="dry-run 已完成市场上下文快照生成校验，未写入快照表。",
            )

        snapshot_row = active_repository.create_snapshot(db_session, created_payload)
        _commit_if_possible(db_session)
        return replace(
            result,
            snapshot_row_id=getattr(snapshot_row, "id", None),
            message="市场上下文快照已写入。",
        )
    except Exception as exc:  # noqa: BLE001 - service boundary returns structured failed status.
        _rollback_if_possible(db_session)
        snapshot_row_id = _try_persist_failed_snapshot_record(
            db_session=db_session,
            repository=active_repository,
            request=request,
            snapshot_id=snapshot_id,
            trace_id=trace_id,
            current_time_ms=current_time_ms,
            error_message=str(exc),
        )
        result = MarketContextSnapshotResult(
            status=MarketContextSnapshotStatus.FAILED,
            exit_code=EXIT_FAILED,
            trace_id=trace_id,
            snapshot_id=snapshot_id,
            message="市场上下文快照生成失败。",
            error_message=str(exc),
            lookback_4h_count=request.lookback_4h_count,
            lookback_1d_count=request.lookback_1d_count,
            snapshot_row_id=snapshot_row_id,
        )
        if request.notify_on_failed:
            return send_market_context_snapshot_alert_and_adjust_exit_code(
                request=request,
                result=result,
                db_session=db_session,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )
        return result


def _validate_market_context_snapshot_request(
    *,
    request: MarketContextSnapshotRequest,
    snapshot_id: str,
    trace_id: str,
) -> MarketContextSnapshotResult | None:
    """Validate snapshot parameters without touching MySQL, Redis, Hermes, or Binance."""

    problems: list[str] = []
    if request.symbol != SUPPORTED_SYMBOL:
        problems.append(f"symbol 仅支持 {SUPPORTED_SYMBOL}")
    if request.base_interval_value != SUPPORTED_BASE_INTERVAL:
        problems.append(f"base_interval 仅支持 {SUPPORTED_BASE_INTERVAL}")
    if request.higher_interval_value != SUPPORTED_HIGHER_INTERVAL:
        problems.append(f"higher_interval 仅支持 {SUPPORTED_HIGHER_INTERVAL}")
    if request.lookback_4h_count <= 0:
        problems.append("lookback_4h 必须大于 0")
    if request.lookback_1d_count <= 0:
        problems.append("lookback_1d 必须大于 0")
    if request.trigger_source not in ALLOWED_SNAPSHOT_TRIGGER_SOURCES:
        problems.append("trigger_source 仅支持 cli")
    if not request.dry_run and not request.confirm_write:
        problems.append("非 dry-run 写入必须显式传入 confirm-write")

    if not problems:
        return None
    return MarketContextSnapshotResult(
        status=MarketContextSnapshotStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        trace_id=trace_id,
        snapshot_id=snapshot_id,
        message="市场上下文快照参数非法。",
        error_message="；".join(problems),
        lookback_4h_count=request.lookback_4h_count,
        lookback_1d_count=request.lookback_1d_count,
    )


def _build_created_snapshot_result(
    *,
    request: MarketContextSnapshotRequest,
    readiness: SnapshotReadinessReport,
    snapshot_id: str,
    trace_id: str,
) -> MarketContextSnapshotResult:
    """Build a compact created result that does not expose the full payload."""

    return MarketContextSnapshotResult(
        status=MarketContextSnapshotStatus.CREATED,
        exit_code=EXIT_SUCCESS,
        trace_id=trace_id,
        snapshot_id=snapshot_id,
        message="市场上下文快照已生成。",
        lookback_4h_count=request.lookback_4h_count,
        lookback_1d_count=request.lookback_1d_count,
        actual_4h_count=readiness.base_context.actual_count,
        actual_1d_count=readiness.higher_context.actual_count,
        latest_4h_open_time_utc=_open_time_text(readiness.base_context.latest_open_time_ms),
        latest_1d_open_time_utc=_open_time_text(readiness.higher_context.latest_open_time_ms),
        details={
            "dry_run": request.dry_run,
            "confirm_write": request.confirm_write,
        },
    )


def _build_blocked_snapshot_result(
    *,
    request: MarketContextSnapshotRequest,
    readiness: SnapshotReadinessReport,
    snapshot_id: str,
    trace_id: str,
) -> MarketContextSnapshotResult:
    """Build a compact blocked result; blocked result never contains full Kline arrays."""

    message = "市场上下文快照生成受阻。"
    if request.dry_run:
        message = "dry-run 已完成市场上下文快照检查，结果为受阻，未写入快照表。"
    return MarketContextSnapshotResult(
        status=MarketContextSnapshotStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        trace_id=trace_id,
        snapshot_id=snapshot_id,
        message=message,
        blocked_reason=readiness.blocked_reason or "市场上下文快照前置检查未通过。",
        lookback_4h_count=request.lookback_4h_count,
        lookback_1d_count=request.lookback_1d_count,
        actual_4h_count=readiness.base_context.actual_count,
        actual_1d_count=readiness.higher_context.actual_count,
        latest_4h_open_time_utc=_open_time_text(readiness.base_context.latest_open_time_ms),
        latest_1d_open_time_utc=_open_time_text(readiness.higher_context.latest_open_time_ms),
        details={
            "dry_run": request.dry_run,
            "confirm_write": request.confirm_write,
        },
    )


def _maybe_send_market_context_alert(
    *,
    request: MarketContextSnapshotRequest,
    result: MarketContextSnapshotResult,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> MarketContextSnapshotResult:
    """Send Hermes only when blocked/failed notification is explicitly enabled."""

    if result.status == MarketContextSnapshotStatus.BLOCKED and request.notify_on_blocked:
        return send_market_context_snapshot_alert_and_adjust_exit_code(
            request=request,
            result=result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    if result.status == MarketContextSnapshotStatus.FAILED and request.notify_on_failed:
        return send_market_context_snapshot_alert_and_adjust_exit_code(
            request=request,
            result=result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def _try_persist_failed_snapshot_record(
    *,
    db_session: Any,
    repository: Any,
    request: MarketContextSnapshotRequest,
    snapshot_id: str,
    trace_id: str,
    current_time_ms: int,
    error_message: str,
) -> int | None:
    """Best-effort failed-row persistence; inability to write must not hide the original failure."""

    if request.dry_run or not request.confirm_write:
        return None
    try:
        failed_payload = build_failed_snapshot_payload(
            snapshot_id=snapshot_id,
            request=request,
            error_message=error_message,
            trace_id=trace_id,
            current_time_ms=current_time_ms,
        )
        snapshot_row = repository.create_snapshot(db_session, failed_payload)
        _commit_if_possible(db_session)
        return getattr(snapshot_row, "id", None)
    except Exception:  # noqa: BLE001 - this path preserves the original failed result.
        _rollback_if_possible(db_session)
        return None


def _build_snapshot_id(
    *,
    request: MarketContextSnapshotRequest,
    generated_at_utc: datetime,
    trace_id: str,
) -> str:
    """Build a human-readable unique snapshot id without external calls."""

    timestamp = generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"MCS-{request.symbol}-{request.base_interval_value.upper()}-"
        f"{request.higher_interval_value.upper()}-{timestamp}-{trace_id[:8]}"
    )


def _open_time_text(open_time_ms: int | None) -> str | None:
    """Render UTC open time for CLI output only; business logic uses milliseconds."""

    if open_time_ms is None:
        return None
    return timestamp_ms_to_utc_datetime(open_time_ms).isoformat()


def _commit_if_possible(db_session: Any) -> None:
    """Commit only snapshot/alert writes when the caller-provided session supports it."""

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    """Rollback a failed snapshot transaction without touching formal Kline tables."""

    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_SNAPSHOT_TRIGGER_SOURCES",
    "build_market_context_snapshot",
]
