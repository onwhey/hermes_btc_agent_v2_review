"""Runtime status aggregation for the Hermes BTC Agent.

本文件属于运行观测模块，负责把 systemd、Redis、MySQL 与告警发送记录汇总为
一份只读运行状态报告。

本文件不触发 K线采集、不触发每日复核、不执行回补、不修复数据、不写 Redis、
不写正式 K线表、不请求 Binance、不调用 DeepSeek，也不涉及任何交易执行。
主要被 `scripts/check_runtime_status.py::main` 调用；仅在用户显式传入
`--send-alert` 时，通过 `app.alerting.service.send_alert` 发送一条摘要通知。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.config import AppSettings
from app.core.time_utils import UTC
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_MS, KLINE_4H_INTERVAL_VALUE
from app.monitoring.runtime_status_readers import DefaultRuntimeMySqlReader, SystemdStatusChecker
from app.monitoring.runtime_status_types import (
    LEVEL_LABELS,
    LEVEL_RANK,
    AlertRuntimeStatus,
    MySqlRuntimeStatus,
    RedisRuntimeStatus,
    RuntimeIssue,
    RuntimeMySqlReader,
    RuntimeStatusLevel,
    RuntimeStatusReport,
    ServiceRuntimeStatus,
)
from app.storage.redis.client import create_redis_client

SYSTEMD_SERVICES: tuple[tuple[str, str], ...] = (
    ("hermes-btc-price-monitor.service", "10 秒价格监控"),
    ("hermes-btc-scheduler.service", "调度器"),
    ("hermes-gateway.service", "Hermes 网关"),
)

CURRENT_ALERT_FAILURE_STATUSES = {"submit_failed", "gateway_rejected"}
CURRENT_ALERT_FAILURE_GATEWAY_STATUSES = {"submit_failed", "gateway_rejected"}
OLD_ALERT_STATUSES = {"sent", "failed", "delivered", "weixin_success"}


def collect_runtime_status(
    *,
    settings: AppSettings | None = None,
    lookback_hours: int = 24,
    kline_count: int = 100,
    systemd_checker: SystemdStatusChecker | None = None,
    redis_client: Any | None = None,
    mysql_reader: RuntimeMySqlReader | None = None,
    current_time_utc: datetime | None = None,
) -> RuntimeStatusReport:
    """Collect a read-only runtime status report.

    参数只用于选择读取范围和注入可 mock 的只读依赖。本方法不会写 MySQL、
    不写 Redis、不请求 Binance、不发送 Hermes。
    """

    settings = settings or AppSettings()
    now_utc = _ensure_utc(current_time_utc or datetime.now(tz=UTC))
    trace_id = uuid4().hex

    services = _check_systemd_services(systemd_checker or SystemdStatusChecker())
    redis_status = _build_redis_status(settings=settings, redis_client=redis_client)
    mysql_status, alert_status = _check_mysql_and_alert_status(
        mysql_reader or DefaultRuntimeMySqlReader(settings),
        now_utc=now_utc,
        lookback_hours=max(1, lookback_hours),
        kline_count=max(1, kline_count),
    )

    issues: list[RuntimeIssue] = []
    for service in services:
        if service.level is not RuntimeStatusLevel.NORMAL:
            issues.append(RuntimeIssue(service.level, "systemd", f"{service.display_name} 状态为{service.status_label}。"))
    issues.extend(redis_status.issues)
    issues.extend(mysql_status.issues)
    issues.extend(alert_status.issues)

    overall_level = _determine_overall_level(
        services=services,
        redis_status=redis_status,
        mysql_status=mysql_status,
        alert_status=alert_status,
        issues=issues,
    )

    return RuntimeStatusReport(
        overall_level=overall_level,
        trace_id=trace_id,
        checked_at_utc=now_utc,
        lookback_hours=max(1, lookback_hours),
        services=services,
        redis=redis_status,
        mysql=mysql_status,
        alert=alert_status,
        issues=issues,
    )


def _check_systemd_services(checker: SystemdStatusChecker) -> list[ServiceRuntimeStatus]:
    services: list[ServiceRuntimeStatus] = []
    for service_name, display_name in SYSTEMD_SERVICES:
        raw_status = checker.is_active(service_name)
        if raw_status == "active":
            status_label = "运行中"
            level = RuntimeStatusLevel.NORMAL
        elif raw_status == "unknown":
            status_label = "未知"
            level = RuntimeStatusLevel.WARNING
        else:
            status_label = "未运行"
            level = RuntimeStatusLevel.ERROR
        services.append(
            ServiceRuntimeStatus(
                service_name=service_name,
                display_name=display_name,
                raw_status=raw_status,
                status_label=status_label,
                level=level,
            )
        )
    return services


def _build_redis_status(*, settings: AppSettings, redis_client: Any | None) -> RedisRuntimeStatus:
    if redis_client is not None:
        return _check_redis_status(redis_client)
    try:
        created_client = create_redis_client(settings)
    except Exception as exc:  # noqa: BLE001 - 运行检查必须把 Redis 初始化失败转换为报告项。
        return RedisRuntimeStatus(
            connection_ok=False,
            level=RuntimeStatusLevel.ERROR,
            error_message=str(exc),
            issues=[RuntimeIssue(RuntimeStatusLevel.ERROR, "redis", f"Redis 无法初始化或连接失败：{exc}")],
        )
    return _check_redis_status(created_client)


def _check_redis_status(redis_client: Any) -> RedisRuntimeStatus:
    issues: list[RuntimeIssue] = []
    try:
        redis_client.ping()
        bitcoin_price_exists = _redis_key_exists(redis_client, "bitcoin_price")
        bitcoin_price_ttl = redis_client.ttl("bitcoin_price") if bitcoin_price_exists else None
        running_count = _count_redis_keys(redis_client, "scheduler:running:*")
        completed_count = _count_redis_keys(redis_client, "scheduler:completed:*")
        status_count = _count_redis_keys(redis_client, "scheduler:status:*")
        legacy_count = _count_redis_keys(redis_client, "scheduler:job:*")
    except Exception as exc:  # pragma: no cover - defensive wrapper around Redis drivers.
        return RedisRuntimeStatus(
            connection_ok=False,
            level=RuntimeStatusLevel.ERROR,
            error_message=str(exc),
            issues=[RuntimeIssue(RuntimeStatusLevel.ERROR, "redis", f"Redis 无法连接或读取失败：{exc}")],
        )

    level = RuntimeStatusLevel.NORMAL
    if not bitcoin_price_exists:
        level = RuntimeStatusLevel.WARNING
        issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "redis", "bitcoin_price 不存在，价格监控可能没有更新。"))
    elif bitcoin_price_ttl is None or bitcoin_price_ttl < 0:
        level = RuntimeStatusLevel.WARNING
        issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "redis", "bitcoin_price TTL 异常。"))

    if legacy_count:
        level = _max_level(level, RuntimeStatusLevel.NOTICE)
        issues.append(RuntimeIssue(RuntimeStatusLevel.NOTICE, "redis", "发现 scheduler:job:* 历史旧 key，可等待自然过期。"))

    return RedisRuntimeStatus(
        connection_ok=True,
        level=level,
        bitcoin_price_exists=bitcoin_price_exists,
        bitcoin_price_ttl=bitcoin_price_ttl,
        scheduler_running_count=running_count,
        scheduler_completed_count=completed_count,
        scheduler_status_count=status_count,
        scheduler_job_legacy_count=legacy_count,
        issues=issues,
    )


def _check_mysql_and_alert_status(
    reader: RuntimeMySqlReader,
    *,
    now_utc: datetime,
    lookback_hours: int,
    kline_count: int,
) -> tuple[MySqlRuntimeStatus, AlertRuntimeStatus]:
    mysql_issues: list[RuntimeIssue] = []
    alert_issues: list[RuntimeIssue] = []

    try:
        reader.check_connection()
        since_utc = now_utc - timedelta(hours=lookback_hours)
        latest_kline = reader.get_latest_kline(symbol=DEFAULT_KLINE_SYMBOL, interval_value=KLINE_4H_INTERVAL_VALUE)
        recent_klines = reader.list_recent_klines(
            symbol=DEFAULT_KLINE_SYMBOL,
            interval_value=KLINE_4H_INTERVAL_VALUE,
            limit=kline_count,
        )
        latest_collector = reader.get_latest_collector_event(
            symbol=DEFAULT_KLINE_SYMBOL,
            interval_value=KLINE_4H_INTERVAL_VALUE,
            since_utc=since_utc,
        )
        latest_quality = reader.get_latest_daily_quality_check(
            symbol=DEFAULT_KLINE_SYMBOL,
            interval_value=KLINE_4H_INTERVAL_VALUE,
            since_utc=since_utc,
        )
        recent_alerts = reader.list_recent_alert_messages(since_utc=since_utc, limit=10)
    except Exception as exc:  # pragma: no cover - DB drivers can raise many concrete types.
        mysql_status = MySqlRuntimeStatus(
            connection_ok=False,
            level=RuntimeStatusLevel.ERROR,
            error_message=str(exc),
            issues=[RuntimeIssue(RuntimeStatusLevel.ERROR, "mysql", f"MySQL 无法连接或读取失败：{exc}")],
        )
        alert_status = AlertRuntimeStatus(
            connection_ok=False,
            level=RuntimeStatusLevel.ERROR,
            error_message=str(exc),
            issues=[RuntimeIssue(RuntimeStatusLevel.ERROR, "alert", "无法读取最近告警记录。")],
        )
        return mysql_status, alert_status

    latest_kline_time = _row_value(latest_kline, "open_time_utc")
    if latest_kline_time is None:
        latest_kline_time = _open_time_ms_to_utc(_row_value(latest_kline, "open_time_ms"))
    latest_kline_time = _ensure_utc(latest_kline_time) if latest_kline_time else None

    mysql_level = _evaluate_kline_freshness(latest_kline_time, now_utc, mysql_issues)
    if len(recent_klines) < kline_count:
        mysql_level = _max_level(mysql_level, RuntimeStatusLevel.WARNING)
        mysql_issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "mysql", f"最近 {kline_count} 根 4h K线只读取到 {len(recent_klines)} 根。"))

    latest_collector_status = _row_value(latest_collector, "status")
    if latest_collector_status is None:
        mysql_level = _max_level(mysql_level, RuntimeStatusLevel.WARNING)
        mysql_issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "collector", "最近查询窗口内未读取到 4h 增量采集事件。"))
    elif latest_collector_status in {"failed", "blocked", "error", "critical"}:
        mysql_level = _max_level(mysql_level, RuntimeStatusLevel.ERROR)
        mysql_issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "collector", "最近一次 4h 增量采集异常。"))

    latest_quality_status = _row_value(latest_quality, "status")
    if latest_quality_status is None:
        mysql_level = _max_level(mysql_level, RuntimeStatusLevel.WARNING)
        mysql_issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "daily_kline_integrity", "最近查询窗口内未读取到每日 K线复核事件。"))
    elif latest_quality_status in {"failed", "error", "critical", "unhealthy"}:
        mysql_level = _max_level(mysql_level, RuntimeStatusLevel.ERROR)
        mysql_issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "daily_kline_integrity", "最近一次每日 K线复核异常。"))

    alert_status = _evaluate_alert_messages(recent_alerts, alert_issues)
    mysql_status = MySqlRuntimeStatus(
        connection_ok=True,
        level=mysql_level,
        latest_kline_open_time_utc=latest_kline_time,
        recent_kline_count=len(recent_klines),
        latest_collector_status=latest_collector_status,
        latest_daily_quality_status=latest_quality_status,
        issues=mysql_issues,
    )
    return mysql_status, alert_status


def _evaluate_kline_freshness(
    latest_open_time_utc: datetime | None,
    now_utc: datetime,
    issues: list[RuntimeIssue],
) -> RuntimeStatusLevel:
    if latest_open_time_utc is None:
        issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "kline", "数据库中未读取到 BTCUSDT 4h K线。"))
        return RuntimeStatusLevel.ERROR

    expected_latest = _expected_latest_closed_4h_open_time(now_utc)
    if latest_open_time_utc > expected_latest:
        issues.append(
            RuntimeIssue(
                RuntimeStatusLevel.ERROR,
                "kline",
                "最新 K线时间晚于当前应有的最新已收盘 K线，疑似未收盘 K线误写正式表或系统时间异常。",
            )
        )
        return RuntimeStatusLevel.ERROR

    lag_ms = int((expected_latest - latest_open_time_utc).total_seconds() * 1000)
    lag_bars = max(0, lag_ms // KLINE_4H_INTERVAL_MS)
    if lag_bars == 0:
        return RuntimeStatusLevel.NORMAL
    if lag_bars == 1:
        issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "kline", "最新已收盘 4h K线略有滞后。"))
        return RuntimeStatusLevel.WARNING
    if lag_bars >= 6:
        issues.append(RuntimeIssue(RuntimeStatusLevel.CRITICAL, "kline", "最新已收盘 4h K线严重滞后。"))
        return RuntimeStatusLevel.CRITICAL
    issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "kline", "最新已收盘 4h K线明显缺失。"))
    return RuntimeStatusLevel.ERROR


def _evaluate_alert_messages(alert_rows: list[Any], issues: list[RuntimeIssue]) -> AlertRuntimeStatus:
    if not alert_rows:
        return AlertRuntimeStatus(connection_ok=True, level=RuntimeStatusLevel.NORMAL)

    latest = alert_rows[0]
    latest_status = _row_value(latest, "status")
    channel_response = _channel_response_dict(_row_value(latest, "channel_response"))
    latest_gateway_status = _row_value(latest, "gateway_status") or channel_response.get("gateway_status")
    latest_final_status = _row_value(latest, "final_delivery_status") or channel_response.get("final_delivery_status")
    latest_trace_id = _row_value(latest, "trace_id")
    latest_failed = _is_current_alert_failure(latest_status, latest_gateway_status)
    latest_legacy = _is_legacy_alert_status(latest_status, latest_final_status)

    failed_count = 0
    legacy_count = 0
    consecutive_failed_count = 0
    counting_consecutive_failures = True
    for row in alert_rows:
        status = _row_value(row, "status")
        gateway_status = _row_value(row, "gateway_status") or _channel_response_dict(_row_value(row, "channel_response")).get("gateway_status")
        final_status = _row_value(row, "final_delivery_status") or _channel_response_dict(_row_value(row, "channel_response")).get("final_delivery_status")
        row_failed = _is_current_alert_failure(status, gateway_status)
        if row_failed:
            failed_count += 1
            if counting_consecutive_failures:
                consecutive_failed_count += 1
        else:
            counting_consecutive_failures = False
        if _is_legacy_alert_status(status, final_status):
            legacy_count += 1

    level = RuntimeStatusLevel.NORMAL
    if latest_failed:
        level = RuntimeStatusLevel.ERROR
        if consecutive_failed_count > 1:
            issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "alert", f"最近连续 {consecutive_failed_count} 次 Hermes 提交失败或网关拒绝。"))
        else:
            issues.append(RuntimeIssue(RuntimeStatusLevel.ERROR, "alert", "最近一次 Hermes 提交失败或网关拒绝。"))
    elif failed_count:
        level = _max_level(level, RuntimeStatusLevel.WARNING)
        issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "alert", "回看窗口内曾经出现 Hermes 提交失败，但最近一次已提交 Hermes。"))

    if latest_legacy:
        level = _max_level(level, RuntimeStatusLevel.WARNING)
        issues.append(RuntimeIssue(RuntimeStatusLevel.WARNING, "alert", "最近一次告警记录仍使用旧版送达状态，需要按新语义核对。"))
    elif legacy_count:
        level = _max_level(level, RuntimeStatusLevel.NOTICE)
        issues.append(RuntimeIssue(RuntimeStatusLevel.NOTICE, "alert", "回看窗口内存在旧版送达状态记录，可后续清理或忽略历史数据。"))

    return AlertRuntimeStatus(
        connection_ok=True,
        level=level,
        latest_status=latest_status,
        latest_gateway_status=latest_gateway_status,
        latest_final_delivery_status=latest_final_status,
        latest_trace_id=latest_trace_id,
        failed_count=failed_count,
        consecutive_failed_count=consecutive_failed_count,
        legacy_status_count=legacy_count,
        issues=issues,
    )


def _is_current_alert_failure(status: Any, gateway_status: Any) -> bool:
    return str(status) in CURRENT_ALERT_FAILURE_STATUSES or str(gateway_status) in CURRENT_ALERT_FAILURE_GATEWAY_STATUSES


def _is_legacy_alert_status(status: Any, final_status: Any) -> bool:
    return str(status) in OLD_ALERT_STATUSES or str(final_status) in OLD_ALERT_STATUSES


def _determine_overall_level(
    *,
    services: list[ServiceRuntimeStatus],
    redis_status: RedisRuntimeStatus,
    mysql_status: MySqlRuntimeStatus,
    alert_status: AlertRuntimeStatus,
    issues: list[RuntimeIssue],
) -> RuntimeStatusLevel:
    if not redis_status.connection_ok and not mysql_status.connection_ok:
        return RuntimeStatusLevel.CRITICAL

    service_errors = sum(1 for service in services if service.level is RuntimeStatusLevel.ERROR)
    core_errors = sum(
        1
        for level in (redis_status.level, mysql_status.level, alert_status.level)
        if level in {RuntimeStatusLevel.ERROR, RuntimeStatusLevel.CRITICAL}
    )
    if service_errors >= 2 or core_errors >= 2:
        return RuntimeStatusLevel.CRITICAL

    level = RuntimeStatusLevel.NORMAL
    for candidate in [*[service.level for service in services], redis_status.level, mysql_status.level, alert_status.level]:
        level = _max_level(level, candidate)
    for issue in issues:
        level = _max_level(level, issue.level)
    return level


def _count_redis_keys(redis_client: Any, pattern: str) -> int:
    scan_iter = getattr(redis_client, "scan_iter", None)
    if callable(scan_iter):
        return sum(1 for _ in scan_iter(pattern))
    keys = getattr(redis_client, "keys", None)
    if callable(keys):
        return len(keys(pattern))
    return 0


def _redis_key_exists(redis_client: Any, key: str) -> bool:
    return bool(redis_client.exists(key))


def _expected_latest_closed_4h_open_time(now_utc: datetime) -> datetime:
    now_utc = _ensure_utc(now_utc)
    bucket_hour = (now_utc.hour // 4) * 4
    current_bucket = now_utc.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)
    return current_bucket - timedelta(hours=4)


def _channel_response_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_value(row: Any, name: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _open_time_ms_to_utc(open_time_ms: Any) -> datetime | None:
    if open_time_ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(open_time_ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _max_level(left: RuntimeStatusLevel, right: RuntimeStatusLevel) -> RuntimeStatusLevel:
    return left if LEVEL_RANK[left] >= LEVEL_RANK[right] else right
