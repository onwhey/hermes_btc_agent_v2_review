"""Scheduler runtime configuration for phase-12.

This file belongs to `app/scheduler`. It converts the unified application
settings into a scheduler-specific immutable config for the long-running
runner. It does not start jobs, call scripts, request Binance, read or write
MySQL/Redis, send Hermes, call DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from app.core.config import AppSettings, get_settings
from app.core.exceptions import ConfigError
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    """Immutable scheduler runtime settings.

    Parameters: values are loaded from `AppSettings` and validated before the
    runner uses them. Return value: plain config object. Failure scenarios:
    invalid intervals, invalid UTC time text, or invalid task limits raise
    `ConfigError`. External effects: none; no database, Redis, Hermes, Binance,
    DeepSeek, strategy, or trading access happens in this value object.
    """

    enabled: bool
    poll_interval_seconds: int
    running_lock_ttl_seconds: int
    completed_marker_ttl_seconds: int
    status_marker_ttl_seconds: int
    slot_log_cooldown_seconds: int
    kline_4h_incremental_collect_enabled: bool
    kline_4h_incremental_collect_symbol: str
    kline_4h_incremental_collect_interval: str
    kline_4h_incremental_collect_limit: int
    kline_4h_incremental_collect_utc_minutes_after_close: int
    kline_1d_incremental_collect_enabled: bool
    kline_1d_incremental_collect_symbol: str
    kline_1d_incremental_collect_interval: str
    kline_1d_incremental_collect_max_closed_count: int
    kline_1d_incremental_collect_lock_ttl_seconds: int
    kline_1d_incremental_collect_utc_time: time
    daily_kline_integrity_enabled: bool
    daily_kline_integrity_symbol: str
    daily_kline_integrity_interval: str
    daily_kline_integrity_limit: int
    daily_kline_integrity_utc_time: time
    daily_kline_1d_integrity_enabled: bool
    daily_kline_1d_integrity_symbol: str
    daily_kline_1d_integrity_interval: str
    daily_kline_1d_integrity_limit: int
    daily_kline_1d_integrity_notify_success: bool
    daily_kline_1d_integrity_lock_ttl_seconds: int
    daily_kline_1d_integrity_utc_time: time
    strategy_signal_scheduler_enabled: bool = False
    strategy_signal_symbol: str = "BTCUSDT"
    strategy_signal_base_interval: str = "4h"
    strategy_signal_higher_interval: str = "1d"
    strategy_signal_hermes_enabled: bool = False
    strategy_signal_hermes_notify_success: bool = True
    strategy_signal_hermes_notify_partial_success: bool = True
    strategy_signal_hermes_notify_blocked: bool = True
    strategy_signal_hermes_notify_failed: bool = True
    strategy_signal_hermes_notify_skipped: bool = False
    strategy_signal_scheduler_running_timeout_seconds: int = 900
    strategy_aggregation_auto_run_enabled: bool = False
    model_review_auto_run_enabled: bool = False
    model_review_scheduler_enabled: bool = False
    model_review_max_runs_per_4h: int = 2


def build_scheduler_runtime_config(
    settings: AppSettings | None = None,
) -> SchedulerRuntimeConfig:
    """Build and validate scheduler config from unified settings.

    Parameters: optional `AppSettings`; omitted value reads cached settings.
    Return value: `SchedulerRuntimeConfig`.
    Failure scenarios: invalid scheduler or job config raises `ConfigError`.
    External effects: this function only reads already-loaded config values.
    """

    active_settings = settings or get_settings()
    config = SchedulerRuntimeConfig(
        enabled=active_settings.scheduler_enabled,
        poll_interval_seconds=active_settings.scheduler_poll_interval_seconds,
        running_lock_ttl_seconds=active_settings.scheduler_running_lock_ttl_seconds,
        completed_marker_ttl_seconds=active_settings.scheduler_completed_marker_ttl_seconds,
        status_marker_ttl_seconds=active_settings.scheduler_status_marker_ttl_seconds,
        slot_log_cooldown_seconds=active_settings.scheduler_slot_log_cooldown_seconds,
        kline_4h_incremental_collect_enabled=active_settings.kline_4h_incremental_collect_enabled,
        kline_4h_incremental_collect_symbol=active_settings.kline_4h_incremental_collect_symbol.strip().upper(),
        kline_4h_incremental_collect_interval=active_settings.kline_4h_incremental_collect_interval,
        kline_4h_incremental_collect_limit=active_settings.kline_4h_incremental_collect_limit,
        kline_4h_incremental_collect_utc_minutes_after_close=(
            active_settings.kline_4h_incremental_collect_utc_minutes_after_close
        ),
        kline_1d_incremental_collect_enabled=active_settings.kline_1d_incremental_collect_enabled,
        kline_1d_incremental_collect_symbol=active_settings.kline_1d_incremental_collect_symbol.strip().upper(),
        kline_1d_incremental_collect_interval=active_settings.kline_1d_incremental_collect_interval,
        kline_1d_incremental_collect_max_closed_count=active_settings.kline_1d_incremental_collect_max_closed_count,
        kline_1d_incremental_collect_lock_ttl_seconds=active_settings.kline_1d_incremental_collect_lock_ttl_seconds,
        kline_1d_incremental_collect_utc_time=_parse_utc_hhmm(
            active_settings.kline_1d_incremental_collect_utc_time,
            key="KLINE_1D_INCREMENTAL_COLLECT_UTC_TIME",
        ),
        daily_kline_integrity_enabled=active_settings.daily_kline_integrity_enabled,
        daily_kline_integrity_symbol=active_settings.daily_kline_integrity_symbol.strip().upper(),
        daily_kline_integrity_interval=active_settings.daily_kline_integrity_interval,
        daily_kline_integrity_limit=active_settings.daily_kline_integrity_limit,
        daily_kline_integrity_utc_time=_parse_utc_hhmm(
            active_settings.daily_kline_integrity_utc_time,
            key="DAILY_KLINE_INTEGRITY_UTC_TIME",
        ),
        daily_kline_1d_integrity_enabled=active_settings.daily_kline_1d_integrity_enabled,
        daily_kline_1d_integrity_symbol=active_settings.daily_kline_1d_integrity_symbol.strip().upper(),
        daily_kline_1d_integrity_interval=active_settings.daily_kline_1d_integrity_interval,
        daily_kline_1d_integrity_limit=active_settings.daily_kline_1d_integrity_limit,
        daily_kline_1d_integrity_notify_success=active_settings.daily_kline_1d_integrity_notify_success,
        daily_kline_1d_integrity_lock_ttl_seconds=active_settings.daily_kline_1d_integrity_lock_ttl_seconds,
        daily_kline_1d_integrity_utc_time=_parse_utc_hhmm(
            active_settings.daily_kline_1d_integrity_utc_time,
            key="DAILY_KLINE_1D_INTEGRITY_UTC_TIME",
        ),
        strategy_signal_scheduler_enabled=active_settings.strategy_signal_scheduler_enabled,
        strategy_signal_symbol=active_settings.strategy_signal_symbol.strip().upper(),
        strategy_signal_base_interval=active_settings.strategy_signal_base_interval,
        strategy_signal_higher_interval=active_settings.strategy_signal_higher_interval,
        strategy_signal_hermes_enabled=active_settings.strategy_signal_hermes_enabled,
        strategy_signal_hermes_notify_success=active_settings.strategy_signal_hermes_notify_success,
        strategy_signal_hermes_notify_partial_success=(
            active_settings.strategy_signal_hermes_notify_partial_success
        ),
        strategy_signal_hermes_notify_blocked=active_settings.strategy_signal_hermes_notify_blocked,
        strategy_signal_hermes_notify_failed=active_settings.strategy_signal_hermes_notify_failed,
        strategy_signal_hermes_notify_skipped=active_settings.strategy_signal_hermes_notify_skipped,
        strategy_signal_scheduler_running_timeout_seconds=(
            active_settings.strategy_signal_scheduler_running_timeout_seconds
        ),
        strategy_aggregation_auto_run_enabled=active_settings.strategy_aggregation_auto_run_enabled,
        model_review_auto_run_enabled=active_settings.model_review_auto_run_enabled,
        model_review_scheduler_enabled=active_settings.model_review_scheduler_enabled,
        model_review_max_runs_per_4h=active_settings.model_review_max_runs_per_4h,
    )
    validate_scheduler_runtime_config(config)
    return config


def validate_scheduler_runtime_config(config: SchedulerRuntimeConfig) -> None:
    """Validate scheduler runtime config before the runner starts.

    Parameters: `config` is the scheduler config to validate.
    Return value: none.
    Failure scenarios: raises `ConfigError` with a sanitized message.
    External effects: none.
    """

    if config.poll_interval_seconds <= 0:
        raise ConfigError("SCHEDULER_POLL_INTERVAL_SECONDS 必须大于 0")
    if config.running_lock_ttl_seconds <= 0:
        raise ConfigError("SCHEDULER_RUNNING_LOCK_TTL_SECONDS 必须大于 0")
    if config.completed_marker_ttl_seconds <= 0:
        raise ConfigError("SCHEDULER_COMPLETED_MARKER_TTL_SECONDS 必须大于 0")
    if config.status_marker_ttl_seconds <= 0:
        raise ConfigError("SCHEDULER_STATUS_MARKER_TTL_SECONDS 必须大于 0")
    if config.slot_log_cooldown_seconds < 0:
        raise ConfigError("SCHEDULER_SLOT_LOG_COOLDOWN_SECONDS 必须大于或等于 0")
    if not config.kline_4h_incremental_collect_symbol:
        raise ConfigError("KLINE_4H_INCREMENTAL_COLLECT_SYMBOL 不能为空")
    if config.kline_4h_incremental_collect_interval != KLINE_4H_INTERVAL_VALUE:
        raise ConfigError("KLINE_4H_INCREMENTAL_COLLECT_INTERVAL 只能是 4h")
    if config.kline_4h_incremental_collect_limit <= 0:
        raise ConfigError("KLINE_4H_INCREMENTAL_COLLECT_LIMIT 必须大于 0")
    if not 0 <= config.kline_4h_incremental_collect_utc_minutes_after_close <= 59:
        raise ConfigError("KLINE_4H_INCREMENTAL_COLLECT_UTC_MINUTES_AFTER_CLOSE 必须在 0 到 59 之间")
    if not config.kline_1d_incremental_collect_symbol:
        raise ConfigError("KLINE_1D_INCREMENTAL_COLLECT_SYMBOL must not be empty")
    if config.kline_1d_incremental_collect_interval != KLINE_1D_INTERVAL_VALUE:
        raise ConfigError("KLINE_1D_INCREMENTAL_COLLECT_INTERVAL must be 1d")
    if config.kline_1d_incremental_collect_max_closed_count <= 0:
        raise ConfigError("KLINE_1D_INCREMENTAL_COLLECT_MAX_CLOSED_COUNT must be greater than 0")
    if config.kline_1d_incremental_collect_lock_ttl_seconds <= 0:
        raise ConfigError("KLINE_1D_INCREMENTAL_COLLECT_LOCK_TTL_SECONDS must be greater than 0")
    if not config.daily_kline_integrity_symbol:
        raise ConfigError("DAILY_KLINE_INTEGRITY_SYMBOL 不能为空")
    if config.daily_kline_integrity_interval != KLINE_4H_INTERVAL_VALUE:
        raise ConfigError("DAILY_KLINE_INTEGRITY_INTERVAL 只能是 4h")
    if config.daily_kline_integrity_limit <= 0:
        raise ConfigError("DAILY_KLINE_INTEGRITY_LIMIT 必须大于 0")


    if not config.daily_kline_1d_integrity_symbol:
        raise ConfigError("DAILY_KLINE_1D_INTEGRITY_SYMBOL must not be empty")
    if config.daily_kline_1d_integrity_interval != KLINE_1D_INTERVAL_VALUE:
        raise ConfigError("DAILY_KLINE_1D_INTEGRITY_INTERVAL must be 1d")
    if config.daily_kline_1d_integrity_limit <= 0:
        raise ConfigError("DAILY_KLINE_1D_INTEGRITY_LIMIT must be greater than 0")
    if config.daily_kline_1d_integrity_lock_ttl_seconds <= 0:
        raise ConfigError("DAILY_KLINE_1D_INTEGRITY_LOCK_TTL_SECONDS must be greater than 0")
    if not config.strategy_signal_symbol:
        raise ConfigError("STRATEGY_SIGNAL_SYMBOLS must not be empty")
    if config.strategy_signal_base_interval != KLINE_4H_INTERVAL_VALUE:
        raise ConfigError("STRATEGY_SIGNAL_BASE_INTERVAL must be 4h")
    if config.strategy_signal_higher_interval != KLINE_1D_INTERVAL_VALUE:
        raise ConfigError("STRATEGY_SIGNAL_HIGHER_INTERVAL must be 1d")
    if config.strategy_signal_scheduler_running_timeout_seconds <= 0:
        raise ConfigError("STRATEGY_SIGNAL_SCHEDULER_RUNNING_TIMEOUT_SECONDS must be greater than 0")
    if config.model_review_max_runs_per_4h < 0:
        raise ConfigError("MODEL_REVIEW_MAX_RUNS_PER_4H must be zero or greater")


def _parse_utc_hhmm(raw_value: str, *, key: str) -> time:
    parts = raw_value.strip().split(":")
    if len(parts) != 2:
        raise ConfigError(f"{key} 必须使用 HH:MM 格式")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"{key} 必须使用数字 HH:MM 格式") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"{key} 必须是有效 UTC 时间")
    return time(hour=hour, minute=minute)


__all__ = [
    "SchedulerRuntimeConfig",
    "build_scheduler_runtime_config",
    "validate_scheduler_runtime_config",
]
