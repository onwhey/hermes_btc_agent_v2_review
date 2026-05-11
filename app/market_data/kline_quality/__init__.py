"""Phase-07 Kline quality-check module boundary.

This package belongs to `app/market_data`.
It provides batch, database-context, and recent integrity quality checks for 4h
formal Klines. It is called by later collection services, the manual check script,
and tests. It does not write formal Kline rows, write Redis, schedule jobs, call
DeepSeek, repair data, or perform trading execution.
"""

from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist
from app.market_data.kline_quality.db_checker import check_kline_batch_against_database
from app.market_data.kline_quality.service import (
    check_against_database,
    record_quality_check_result,
    run_recent_kline_integrity_check,
)
from app.market_data.kline_quality.types import (
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    KlineQualityStatus,
)

__all__ = [
    "KlineQualityIssue",
    "KlineQualityIssueType",
    "KlineQualityReport",
    "KlineQualitySeverity",
    "KlineQualityStatus",
    "check_against_database",
    "check_kline_batch_against_database",
    "check_kline_batch_before_persist",
    "record_quality_check_result",
    "run_recent_kline_integrity_check",
]
