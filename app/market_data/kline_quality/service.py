"""Service facade for phase-07 Kline quality checks.

Call chain for pre-persist checks:
later collection service
    -> app/market_data/kline_quality/service.py::check_batch_before_persist
    -> app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    -> app/market_data/kline_validator.py::validate_market_kline

Call chain for recent integrity checks:
scripts/check_kline_quality_4h.py::main
    -> app/market_data/kline_quality/service.py::run_recent_kline_integrity_check
    -> app/market_data/kline_quality/integrity_checker.py::run_recent_kline_integrity_check
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines

This file belongs to `app/market_data/kline_quality`.
It orchestrates quality checks, optional `data_quality_check` records, and optional
fixed-template alerts. It does not write formal Kline rows, write Redis, call
DeepSeek, repair Klines, backfill gaps, overwrite conflicts, schedule jobs, or trade.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.batch_checker import (
    check_kline_batch_before_persist as check_batch_locally,
)
from app.market_data.kline_quality.db_checker import (
    check_kline_batch_against_database,
    check_kline_batch_with_database_context,
)
from app.market_data.kline_quality.integrity_checker import (
    BinanceKlineClientProtocol,
    KlineIntegrityReaderProtocol,
    run_recent_kline_integrity_check as run_integrity_check,
)
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_SERVICE,
    CHECK_TYPE_RECENT_KLINE_INTEGRITY,
    KlineQualityReport,
)

AlertSender = Callable[..., Any]


def check_batch_before_persist(
    klines: Iterable[MarketKlineDTO],
    *,
    server_time_ms: int,
    latest_db_kline: Any | None = None,
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
) -> KlineQualityReport:
    """Check a parsed batch before later formal Kline persistence.

    Parameters: `klines` are parsed DTOs; `server_time_ms` is supplied by Binance
    server time or an explicit caller fixture; `latest_db_kline` is optional context.
    Return value: report with failures or writable Klines.
    Failure scenarios: invalid trigger source raises; validation defects become report issues.
    External service access: none.
    Data impact: no MySQL formal Kline writes, Redis writes, Hermes sends, or fixes.
    """

    batch = tuple(klines)
    batch_report = check_batch_locally(
        batch,
        server_time_ms=server_time_ms,
        check_trigger_source=check_trigger_source,
    )
    if not batch_report.passed or latest_db_kline is None:
        return batch_report
    return check_kline_batch_with_database_context(
        batch,
        latest_db_kline=latest_db_kline,
        check_trigger_source=check_trigger_source,
    )


def check_against_database(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    server_time_ms: int,
    repository: Any | None = None,
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
) -> KlineQualityReport:
    """Check a batch locally and against existing database rows.

    Parameters: caller-owned session, parsed Klines, server time, optional read-only
    repository, and quality-check trigger source.
    Return value: report with duplicate existing rows filtered from `writable_klines`.
    Failure scenarios: local validation returns a failed report; database read failures propagate.
    External service access: none.
    Data impact: reads `market_kline_4h`; never writes formal Kline rows or commits.
    """

    batch = tuple(klines)
    batch_report = check_batch_locally(
        batch,
        server_time_ms=server_time_ms,
        check_trigger_source=check_trigger_source,
    )
    if not batch_report.passed:
        return batch_report
    return check_kline_batch_against_database(
        db_session,
        batch,
        repository=repository,
        check_trigger_source=check_trigger_source,
    )


def run_recent_kline_integrity_check(
    db_session: Any,
    *,
    symbol: str = DEFAULT_KLINE_SYMBOL,
    interval_value: str = KLINE_4H_INTERVAL_VALUE,
    limit: int = 100,
    check_trigger_source: str,
    binance_client: BinanceKlineClientProtocol | None = None,
    kline_repository: KlineIntegrityReaderProtocol | None = None,
    quality_repository: Any | None = None,
    server_time_ms: int | None = None,
    record_result: bool = True,
    send_alert: bool = False,
    alert_sender: AlertSender | None = None,
) -> KlineQualityReport:
    """Run a recent official-vs-database integrity check.

    Parameters: caller-owned session, symbol, interval, limit, explicit quality
    trigger, and injectable dependencies for tests.
    Return value: quality report.
    Failure scenarios: Binance/client/parser/database errors propagate; failed checks
    are represented in the returned report.
    External service access: may request Binance only when explicitly called without a fake client.
    Data impact: can write one `data_quality_check` record when `record_result=True`;
    never writes formal Kline rows, Redis, or scheduler state.
    """

    report = run_integrity_check(
        db_session,
        symbol=symbol,
        interval_value=interval_value,
        limit=limit,
        check_trigger_source=check_trigger_source,
        binance_client=binance_client,
        repository=kline_repository,
        server_time_ms=server_time_ms,
    )
    if record_result:
        record_quality_check_result(
            db_session,
            report,
            repository=quality_repository,
        )
    if send_alert:
        send_quality_alert_if_needed(
            report,
            alert_sender=alert_sender,
            send_real_alert=True,
        )
    return report


def record_quality_check_result(
    db_session: Any,
    report: KlineQualityReport,
    *,
    repository: Any | None = None,
) -> Any:
    """Persist one `data_quality_check` record for a report.

    Parameters: caller-owned session, report, and optional repository.
    Return value: created ORM row or fake row supplied by tests.
    Failure scenarios: database write failures propagate and should abort the caller's task.
    External service access: none.
    Data impact: writes only `data_quality_check`; never writes formal Kline rows or Redis.
    """

    if repository is None:
        from app.storage.mysql.repositories.data_quality_check_repository import (
            DataQualityCheckRepository,
        )

        repository = DataQualityCheckRepository()
    return repository.create_quality_check_record(db_session, report)


def send_quality_alert_if_needed(
    report: KlineQualityReport,
    *,
    alert_sender: AlertSender | None = None,
    send_real_alert: bool = False,
    db_session: Any | None = None,
    alert_repository: Any | None = None,
) -> Any | None:
    """Send a fixed-template quality alert only for failed reports.

    Parameters: `report` is a quality report; `send_real_alert` defaults to False
    and must be explicit to allow Hermes network access.
    Return value: alert result or `None` when the report passed.
    Failure scenarios: alert service exceptions propagate to the caller.
    External service access: default mode does not perform real Hermes sends.
    Data impact: may write `alert_message` only when the caller supplies its repository/session.
    """

    if report.passed:
        return None

    if alert_sender is None:
        from app.alerting.service import send_alert as alert_sender

    event = _build_quality_alert_event(report)
    return alert_sender(
        event,
        repository=alert_repository,
        db_session=db_session,
        send_real_alert=send_real_alert,
    )


def _build_quality_alert_event(report: KlineQualityReport) -> AlertEvent:
    alert_type = (
        AlertType.KLINE_INTEGRITY_CHECK_FAILED
        if report.check_type == CHECK_TYPE_RECENT_KLINE_INTEGRITY
        else AlertType.KLINE_DATA_QUALITY_ERROR
    )
    severity = AlertSeverity.CRITICAL if report.severity.value == "critical" else AlertSeverity.ERROR
    first_issue = report.first_issue
    summary = first_issue.message if first_issue is not None else "Kline quality check failed"
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title="Kline quality check failed",
        summary=summary,
        details={
            "check_type": report.check_type,
            "symbol": report.symbol,
            "interval_value": report.interval_value,
            "checked_count": report.checked_count,
            "issue_count": report.issue_count,
            "first_issue_type": first_issue.issue_type.value if first_issue else "",
            "report": format_quality_report_summary(report),
        },
        source="app.market_data.kline_quality.service",
    )
