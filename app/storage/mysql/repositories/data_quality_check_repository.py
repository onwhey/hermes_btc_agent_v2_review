"""Repository for phase-07 Kline data quality check records.

This file belongs to `app/storage/mysql/repositories`.
It writes and reads only `data_quality_check` through caller-provided sessions.
It is called by the Kline quality service, manual check script, and tests.
It does not request Binance, write formal Kline rows, write Redis, send Hermes,
call DeepSeek, repair Klines, execute migrations, or perform trading execution.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.time_utils import now_utc, utc_aware_to_prc_aware
from app.market_data.kline_quality.types import KlineQualityReport, KlineQualityStatus
from app.storage.mysql.models.data_quality_check import DataQualityCheck

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class DataQualityCheckRepository:
    """Data access helper for `data_quality_check`.

    Parameters: none; callers pass sessions into each method.
    Return value: repository instance.
    Failure scenarios: database errors propagate to the caller.
    External service access: none.
    Data impact: may write/read only `data_quality_check`; never commits.
    """

    def create_quality_check_record(self, db_session: Any, report: KlineQualityReport) -> DataQualityCheck:
        """Create one quality-check record from a report.

        Parameters: caller-provided session and structured quality report.
        Return value: created `DataQualityCheck` row.
        Failure scenarios: session or database write errors propagate.
        External service access: none.
        Data impact: writes only `data_quality_check`, never formal Kline rows.
        """

        now = now_utc()
        now_prc = utc_aware_to_prc_aware(now)
        first_issue = report.first_issue
        record = DataQualityCheck(
            check_type=report.check_type,
            symbol=report.symbol,
            interval_value=report.interval_value,
            check_trigger_source=report.check_trigger_source,
            status=report.status.value,
            severity=report.severity.value,
            checked_count=report.checked_count,
            issue_count=report.issue_count,
            start_open_time_ms=report.start_open_time_ms,
            start_open_time_utc=report.start_open_time_utc,
            start_open_time_prc=report.start_open_time_prc,
            end_open_time_ms=report.end_open_time_ms,
            end_open_time_utc=report.end_open_time_utc,
            end_open_time_prc=report.end_open_time_prc,
            report_json=json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True),
            first_issue_type=first_issue.issue_type.value if first_issue else None,
            first_issue_message=first_issue.message if first_issue else None,
            alert_sent=False,
            alert_message_id=None,
            created_at_utc=now,
            created_at_prc=now_prc,
            updated_at_utc=now,
            updated_at_prc=now_prc,
        )
        db_session.add(record)
        if hasattr(db_session, "flush"):
            db_session.flush()
        return record

    def mark_quality_check_alert_sent(
        self,
        db_session: Any,
        data_quality_check: DataQualityCheck,
        *,
        alert_message_id: int | None = None,
    ) -> DataQualityCheck:
        """Mark a quality-check record as having an alert result.

        Parameters: caller-owned session, record, and optional alert message id.
        Return value: updated record.
        Failure scenarios: database update errors propagate.
        External service access: none.
        Data impact: updates only `data_quality_check` alert fields.
        """

        data_quality_check.alert_sent = True
        data_quality_check.alert_message_id = alert_message_id
        now = now_utc()
        data_quality_check.updated_at_utc = now
        data_quality_check.updated_at_prc = utc_aware_to_prc_aware(now)
        if hasattr(db_session, "flush"):
            db_session.flush()
        return data_quality_check

    def get_latest_by_type(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        check_type: str,
    ) -> DataQualityCheck | None:
        """Return the latest quality-check record for one check type.

        Parameters: caller-owned session plus symbol, interval, and check type.
        Return value: ORM row or `None`.
        Failure scenarios: SQLAlchemy or database errors propagate.
        External service access: none.
        Data impact: reads only `data_quality_check`.
        """

        _require_sqlalchemy()
        stmt = (
            select(DataQualityCheck)
            .where(DataQualityCheck.symbol == symbol)
            .where(DataQualityCheck.interval_value == interval_value)
            .where(DataQualityCheck.check_type == check_type)
            .order_by(DataQualityCheck.created_at_utc.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def list_recent_failed(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        limit: int = 20,
    ) -> list[DataQualityCheck]:
        """List recent failed quality-check records.

        Parameters: caller-owned session, symbol, interval, and max row count.
        Return value: rows newest first.
        Failure scenarios: invalid limit or database errors propagate.
        External service access: none.
        Data impact: reads only `data_quality_check`.
        """

        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        _require_sqlalchemy()
        stmt = (
            select(DataQualityCheck)
            .where(DataQualityCheck.symbol == symbol)
            .where(DataQualityCheck.interval_value == interval_value)
            .where(DataQualityCheck.status == KlineQualityStatus.FAILED.value)
            .order_by(DataQualityCheck.created_at_utc.desc())
            .limit(limit)
        )
        return list(db_session.execute(stmt).scalars().all())


def create_default_data_quality_check_repository() -> DataQualityCheckRepository:
    """Create the default phase-07 quality-check repository object."""

    return DataQualityCheckRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for DataQualityCheckRepository queries")
