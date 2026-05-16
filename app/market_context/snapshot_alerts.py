"""Fixed-template Hermes alert helpers for MarketContextSnapshot outcomes.

This file belongs to `app/market_context`. It builds compact Chinese blocked
and failed alert events for stage-15 market fact snapshots, then delegates
sending to `app/alerting/service.py`.
It does not request Binance, write Kline tables, write Redis, call DeepSeek or
any large language model, generate trading advice, repair data, schedule jobs,
or trade.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.market_context.snapshot_types import (
    EXIT_ALERT_FAILED,
    MarketContextSnapshotRequest,
    MarketContextSnapshotResult,
    MarketContextSnapshotStatus,
)

_BOUNDARY_TEXT = "本次仅生成市场事实快照：系统没有自动修复数据，没有人工改数，没有自动回补，也没有执行自动交易。"
_SNAPSHOT_NOT_TRADING_ADVICE_TEXT = "本提醒不是交易建议，不包含任何开仓、平仓、止盈、止损或仓位建议。"


def send_market_context_snapshot_alert_and_adjust_exit_code(
    request: MarketContextSnapshotRequest,
    result: MarketContextSnapshotResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> MarketContextSnapshotResult:
    """Send a blocked/failed snapshot alert and adjust exit code when needed.

    Parameters: snapshot request/result, caller-owned session, and injectable
    alert dependencies.
    Return value: result with `alert_status` populated.
    Failure scenarios: alert submission failure returns `EXIT_ALERT_FAILED` while
    preserving the original snapshot status.
    External service access: only delegated alert sender may submit to Hermes.
    Data impact: may write `alert_message`; never writes Kline tables.
    """

    alert_result = _send_market_context_snapshot_alert(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
    )
    _commit_if_possible(db_session)
    if alert_result.status != AlertSendStatus.SUBMITTED_TO_HERMES:
        return replace(result, alert_status=alert_result.status.value, exit_code=EXIT_ALERT_FAILED)
    return replace(result, alert_status=alert_result.status.value)


def build_market_context_snapshot_alert_event(
    request: MarketContextSnapshotRequest,
    result: MarketContextSnapshotResult,
) -> AlertEvent:
    """Build one compact Chinese alert event for blocked or failed snapshots."""

    is_blocked = result.status == MarketContextSnapshotStatus.BLOCKED
    title = "市场上下文快照生成受阻" if is_blocked else "市场上下文快照生成失败"
    reason = result.blocked_reason if is_blocked else (result.error_message or result.message)
    alert_type = AlertType.KLINE_DATA_QUALITY_ERROR if is_blocked else AlertType.SYSTEM_ERROR
    severity = AlertSeverity.WARNING if is_blocked else AlertSeverity.ERROR
    final_trace_id = result.trace_id or request.trace_id or ""
    body = "\n".join(
        [
            f"币种周期：{request.symbol} {request.base_interval_value} + {request.higher_interval_value}",
            f"结果：{result.status.value}",
            "",
            "原因：",
            reason or "未提供原因，请按追踪ID排查。",
            "",
            "处理：",
            "系统没有生成可用于后续分析的正常快照，也没有修改正式 K线表。",
            "",
            "建议：",
            "请先检查 4h / 1d 增量采集、每日复核与正式 K线连续性；如需补齐，只能走 Binance REST 手动回补流程，禁止人工改数。",
            "",
            f"追踪ID：{final_trace_id}",
            "",
            _SNAPSHOT_NOT_TRADING_ADVICE_TEXT,
            "",
            f"边界声明：{_BOUNDARY_TEXT}",
        ]
    )
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title=title,
        summary=reason or title,
        details={
            WECHAT_VISIBLE_BODY_DETAIL_KEY: body,
            "_internal_context": {
                "snapshot_id": result.snapshot_id or "",
                "symbol": request.symbol,
                "base_interval_value": request.base_interval_value,
                "higher_interval_value": request.higher_interval_value,
                "status": result.status.value,
                "trace_id": final_trace_id,
                "dry_run": request.dry_run,
                "full_payload_in_message": False,
                "kline_array_in_message": False,
            },
        },
        source="app.market_context.snapshot_alerts",
        trace_id=final_trace_id,
    )


def _send_market_context_snapshot_alert(
    request: MarketContextSnapshotRequest,
    result: MarketContextSnapshotResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> AlertSendResult:
    active_alert_sender = alert_sender or _default_alert_sender()
    active_alert_repository = alert_repository or _default_alert_repository()
    event = build_market_context_snapshot_alert_event(request, result)
    return active_alert_sender(
        event,
        repository=active_alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _commit_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "commit"):
        db_session.commit()


def _default_alert_sender() -> Any:
    from app.alerting.service import send_alert

    return send_alert


def _default_alert_repository() -> Any:
    from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository

    return AlertMessageRepository()


__all__ = [
    "build_market_context_snapshot_alert_event",
    "send_market_context_snapshot_alert_and_adjust_exit_code",
]
