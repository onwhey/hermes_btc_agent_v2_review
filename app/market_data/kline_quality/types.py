"""Shared types for phase-07 Kline quality checks.

This file belongs to `app/market_data/kline_quality`.
It defines quality-check reports, issues, status values, and trigger-source rules.
It is called by the quality checker, service, storage repository, script, and tests.
It does not request Binance, read or write MySQL, read or write Redis, send Hermes,
call DeepSeek, or perform any trading execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from app.core.exceptions import KlineQualityError
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_dto import MarketKlineDTO

CHECK_TRIGGER_SOURCE_CLI = "cli"
CHECK_TRIGGER_SOURCE_SCHEDULER = "scheduler"
CHECK_TRIGGER_SOURCE_SERVICE = "service"

ALLOWED_CHECK_TRIGGER_SOURCES = frozenset(
    {
        CHECK_TRIGGER_SOURCE_CLI,
        CHECK_TRIGGER_SOURCE_SCHEDULER,
        CHECK_TRIGGER_SOURCE_SERVICE,
    }
)

CHECK_TYPE_BATCH_BEFORE_PERSIST = "batch_before_persist"
CHECK_TYPE_DATABASE_CONTEXT = "database_context"
CHECK_TYPE_RECENT_KLINE_INTEGRITY = "recent_kline_integrity"


class KlineQualityStatus(str, Enum):
    """Status for one quality-check report.

    Parameters: none.
    Return value: enum values persisted into `data_quality_check.status`.
    Failure scenarios: invalid strings are rejected by `coerce_quality_status`.
    External service access and data impact: none.
    """

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


class KlineQualitySeverity(str, Enum):
    """Severity for quality-check issues and reports.

    Parameters: none.
    Return value: enum values persisted into `data_quality_check.severity`.
    Failure scenarios: invalid strings are rejected by `coerce_quality_severity`.
    External service access and data impact: none.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class KlineQualityIssueType(str, Enum):
    """Structured issue types returned by phase-07 checks.

    Parameters: none.
    Return value: enum values used in reports and optional quality records.
    Failure scenarios: none expected at enum definition time.
    External service access and data impact: none.
    """

    EMPTY_BATCH = "empty_batch"
    INVALID_KLINE = "invalid_kline"
    BATCH_SYMBOL_MISMATCH = "batch_symbol_mismatch"
    BATCH_INTERVAL_MISMATCH = "batch_interval_mismatch"
    BATCH_NOT_SORTED = "batch_not_sorted"
    DUPLICATE_OPEN_TIME = "duplicate_open_time"
    BATCH_NOT_CONTINUOUS = "batch_not_continuous"
    UNCLOSED_KLINE = "unclosed_kline"
    INSUFFICIENT_CLOSED_KLINES = "insufficient_closed_klines"
    DATABASE_NOT_CONTINUOUS = "database_not_continuous"
    DATABASE_CONFLICT = "database_conflict"
    MISSING_IN_DATABASE = "missing_in_database"
    EXTRA_IN_DATABASE = "extra_in_database"
    DATABASE_FIELD_MISMATCH = "database_field_mismatch"


_SEVERITY_RANK = {
    KlineQualitySeverity.INFO: 0,
    KlineQualitySeverity.WARNING: 1,
    KlineQualitySeverity.ERROR: 2,
    KlineQualitySeverity.CRITICAL: 3,
}


@dataclass(frozen=True)
class KlineQualityIssue:
    """One explicit quality-check issue.

    Parameters: `issue_type` classifies the problem; `message` explains it;
    optional time and field fields point to the affected Kline.
    Return value: immutable issue object.
    Failure scenarios: invalid enum strings raise in `__post_init__`.
    External service access and data impact: none.
    """

    issue_type: KlineQualityIssueType | str
    severity: KlineQualitySeverity | str
    message: str
    open_time_ms: int | None = None
    previous_open_time_ms: int | None = None
    next_open_time_ms: int | None = None
    field_name: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_type", coerce_issue_type(self.issue_type))
        object.__setattr__(self, "severity", coerce_quality_severity(self.severity))

    def to_dict(self) -> dict[str, object]:
        """Serialize one issue for `data_quality_check.report_json`.

        Parameters: none.
        Return value: JSON-serializable dictionary.
        Failure scenarios: none expected.
        External service access and data impact: none.
        """

        return {
            "issue_type": self.issue_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "open_time_ms": self.open_time_ms,
            "previous_open_time_ms": self.previous_open_time_ms,
            "next_open_time_ms": self.next_open_time_ms,
            "field_name": self.field_name,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
        }


@dataclass(frozen=True)
class KlineQualityReport:
    """Result of one quality-check run.

    Parameters: identity fields describe the checked symbol, interval, check type,
    trigger source, status, counts, issues, duplicate context, and rows safe for later write.
    Return value: immutable report object.
    Failure scenarios: invalid status, severity, or check trigger source raises.
    External service access: none.
    Data impact: this object itself does not write MySQL, Redis, Hermes, or formal Klines.
    """

    check_type: str
    symbol: str = DEFAULT_KLINE_SYMBOL
    interval_value: str = KLINE_4H_INTERVAL_VALUE
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE
    status: KlineQualityStatus | str = KlineQualityStatus.PASSED
    severity: KlineQualitySeverity | str = KlineQualitySeverity.INFO
    checked_count: int = 0
    issues: tuple[KlineQualityIssue, ...] = ()
    start_open_time_ms: int | None = None
    start_open_time_utc: datetime | None = None
    start_open_time_prc: datetime | None = None
    end_open_time_ms: int | None = None
    end_open_time_utc: datetime | None = None
    end_open_time_prc: datetime | None = None
    existing_open_time_ms: tuple[int, ...] = ()
    writable_klines: tuple[MarketKlineDTO, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", coerce_quality_status(self.status))
        object.__setattr__(self, "severity", coerce_quality_severity(self.severity))
        object.__setattr__(
            self,
            "check_trigger_source",
            coerce_check_trigger_source(self.check_trigger_source),
        )

    @property
    def issue_count(self) -> int:
        """Return the number of structured issues in this report."""

        return len(self.issues)

    @property
    def passed(self) -> bool:
        """Return whether this report fully passed."""

        return self.status == KlineQualityStatus.PASSED

    @property
    def first_issue(self) -> KlineQualityIssue | None:
        """Return the first issue, if any."""

        return self.issues[0] if self.issues else None

    def to_dict(self) -> dict[str, object]:
        """Serialize the report for `data_quality_check.report_json`.

        Parameters: none.
        Return value: JSON-serializable dictionary.
        Failure scenarios: none expected for report-generated values.
        External service access and data impact: none.
        """

        return {
            "check_type": self.check_type,
            "symbol": self.symbol,
            "interval_value": self.interval_value,
            "check_trigger_source": self.check_trigger_source,
            "status": self.status.value,
            "severity": self.severity.value,
            "checked_count": self.checked_count,
            "issue_count": self.issue_count,
            "start_open_time_ms": self.start_open_time_ms,
            "start_open_time_utc": _datetime_to_text(self.start_open_time_utc),
            "start_open_time_prc": _datetime_to_text(self.start_open_time_prc),
            "end_open_time_ms": self.end_open_time_ms,
            "end_open_time_utc": _datetime_to_text(self.end_open_time_utc),
            "end_open_time_prc": _datetime_to_text(self.end_open_time_prc),
            "existing_open_time_ms": list(self.existing_open_time_ms),
            "writable_open_time_ms": [kline.open_time_ms for kline in self.writable_klines],
            "issues": [issue.to_dict() for issue in self.issues],
            "metadata": dict(self.metadata),
        }


def coerce_check_trigger_source(value: str) -> str:
    """Validate a phase-07 quality-check trigger source.

    Parameters: `value` must be `cli`, `scheduler`, or `service`.
    Return value: normalized trigger source string.
    Failure scenarios: unsupported values raise `KlineQualityError`.
    External service access and data impact: none.
    """

    normalized = value.strip()
    if normalized not in ALLOWED_CHECK_TRIGGER_SOURCES:
        allowed = ", ".join(sorted(ALLOWED_CHECK_TRIGGER_SOURCES))
        raise KlineQualityError(f"unsupported Kline quality check_trigger_source: {value}; allowed={allowed}")
    return normalized


def coerce_quality_status(value: KlineQualityStatus | str) -> KlineQualityStatus:
    """Convert a string to `KlineQualityStatus`."""

    if isinstance(value, KlineQualityStatus):
        return value
    try:
        return KlineQualityStatus(value)
    except ValueError as exc:
        raise KlineQualityError(f"unsupported Kline quality status: {value}") from exc


def coerce_quality_severity(value: KlineQualitySeverity | str) -> KlineQualitySeverity:
    """Convert a string to `KlineQualitySeverity`."""

    if isinstance(value, KlineQualitySeverity):
        return value
    try:
        return KlineQualitySeverity(value)
    except ValueError as exc:
        raise KlineQualityError(f"unsupported Kline quality severity: {value}") from exc


def coerce_issue_type(value: KlineQualityIssueType | str) -> KlineQualityIssueType:
    """Convert a string to `KlineQualityIssueType`."""

    if isinstance(value, KlineQualityIssueType):
        return value
    try:
        return KlineQualityIssueType(value)
    except ValueError as exc:
        raise KlineQualityError(f"unsupported Kline quality issue type: {value}") from exc


def build_quality_report(
    *,
    check_type: str,
    klines: Sequence[MarketKlineDTO],
    issues: Sequence[KlineQualityIssue],
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
    existing_open_time_ms: Sequence[int] = (),
    writable_klines: Sequence[MarketKlineDTO] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> KlineQualityReport:
    """Build a complete report from Klines and issues.

    Parameters: `klines` are the inspected DTOs; `issues` are structured failures.
    Return value: `KlineQualityReport` with derived status, severity, range, and counts.
    Failure scenarios: invalid check trigger source raises `KlineQualityError`.
    External service access and data impact: none.
    """

    range_values = _range_values(klines)
    status = KlineQualityStatus.FAILED if issues else KlineQualityStatus.PASSED
    severity = _highest_severity(issues)
    safe_writable = tuple(writable_klines if writable_klines is not None else (klines if not issues else ()))
    return KlineQualityReport(
        check_type=check_type,
        symbol=klines[0].symbol if klines else DEFAULT_KLINE_SYMBOL,
        interval_value=klines[0].interval_value if klines else KLINE_4H_INTERVAL_VALUE,
        check_trigger_source=check_trigger_source,
        status=status,
        severity=severity,
        checked_count=len(klines),
        issues=tuple(issues),
        start_open_time_ms=range_values["start_open_time_ms"],
        start_open_time_utc=range_values["start_open_time_utc"],
        start_open_time_prc=range_values["start_open_time_prc"],
        end_open_time_ms=range_values["end_open_time_ms"],
        end_open_time_utc=range_values["end_open_time_utc"],
        end_open_time_prc=range_values["end_open_time_prc"],
        existing_open_time_ms=tuple(existing_open_time_ms),
        writable_klines=safe_writable if not issues else (),
        metadata=dict(metadata or {}),
    )


def _highest_severity(issues: Sequence[KlineQualityIssue]) -> KlineQualitySeverity:
    if not issues:
        return KlineQualitySeverity.INFO
    highest = KlineQualitySeverity.INFO
    for issue in issues:
        if _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[highest]:
            highest = issue.severity
    return highest


def _range_values(klines: Sequence[MarketKlineDTO]) -> dict[str, Any]:
    if not klines:
        return {
            "start_open_time_ms": None,
            "start_open_time_utc": None,
            "start_open_time_prc": None,
            "end_open_time_ms": None,
            "end_open_time_utc": None,
            "end_open_time_prc": None,
        }
    first = min(klines, key=lambda item: item.open_time_ms)
    last = max(klines, key=lambda item: item.open_time_ms)
    return {
        "start_open_time_ms": first.open_time_ms,
        "start_open_time_utc": first.open_time_utc,
        "start_open_time_prc": first.open_time_prc,
        "end_open_time_ms": last.open_time_ms,
        "end_open_time_utc": last.open_time_utc,
        "end_open_time_prc": last.open_time_prc,
    }


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
