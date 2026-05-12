"""统一配置读取模块。

本文件属于 `app/core` 基础能力层，负责从 `.env` 和系统环境变量读取配置。
本文件不负责建立 MySQL 或 Redis 连接，不请求 Binance，不发送 Hermes，
不调用 DeepSeek，不实现 K 线采集、scheduler 或任何交易执行能力。
主要被脚本入口、后续基础设施模块和测试调用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.core.constants import (
    APP_ENV_DEV,
    APP_ENV_PROD,
    APP_ENV_TEST,
    DEFAULT_APP_NAME,
    DEFAULT_BINANCE_BASE_URL,
    DEFAULT_BINANCE_KLINE_DEFAULT_LIMIT,
    DEFAULT_BINANCE_KLINE_MAX_LIMIT,
    DEFAULT_BINANCE_MAX_RETRIES,
    DEFAULT_BINANCE_RETRY_BACKOFF_SECONDS,
    DEFAULT_BINANCE_TIMEOUT_SECONDS,
    DEFAULT_BINANCE_WS_BASE_URL,
    DEFAULT_INTERVAL,
    DEFAULT_HERMES_DRY_RUN,
    DEFAULT_HERMES_ENABLED,
    DEFAULT_HERMES_MAX_RETRIES,
    DEFAULT_HERMES_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MYSQL_CHARSET,
    DEFAULT_MYSQL_MAX_OVERFLOW,
    DEFAULT_MYSQL_POOL_PRE_PING,
    DEFAULT_MYSQL_POOL_RECYCLE,
    DEFAULT_MYSQL_POOL_SIZE,
    DEFAULT_MYSQL_PORT,
    DEFAULT_REDIS_DB,
    DEFAULT_REDIS_DECODE_RESPONSES,
    DEFAULT_REDIS_PORT,
    DEFAULT_REDIS_SOCKET_TIMEOUT,
    DEFAULT_PRICE_MONITOR_ALERT_COOLDOWN_SECONDS,
    DEFAULT_PRICE_MONITOR_CHANGE_THRESHOLD,
    DEFAULT_PRICE_MONITOR_ENABLE_PRICE_ALERTS,
    DEFAULT_PRICE_MONITOR_INTERVAL_SECONDS,
    DEFAULT_PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS,
    DEFAULT_PRICE_MONITOR_REDIS_KEY,
    DEFAULT_PRICE_MONITOR_REDIS_TTL_SECONDS,
    DEFAULT_PRICE_MONITOR_SYMBOL,
    DEFAULT_PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS,
    DEFAULT_PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS,
    DEFAULT_PRICE_MONITOR_WS_STREAM,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEZONE,
    SENSITIVE_FIELD_NAMES,
)
from app.core.exceptions import ConfigError

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT_DIR / ".env"
ALLOWED_APP_ENVS = {APP_ENV_DEV, APP_ENV_TEST, APP_ENV_PROD}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

_SETTINGS: AppSettings | None = None


@dataclass(frozen=True, repr=False)
class AppSettings:
    """应用配置值对象。

    参数：字段来自 `.env`、系统环境变量或安全默认值。
    返回值：不可变配置对象。
    失败场景：对象创建前由 `load_settings()` 完成类型转换与校验。
    外部服务：本类不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责连接基础设施、校验真实服务可用性或自动交易。
    """

    app_name: str = DEFAULT_APP_NAME
    app_env: str = APP_ENV_DEV
    app_debug: bool = False
    log_level: str = DEFAULT_LOG_LEVEL
    timezone: str = DEFAULT_TIMEZONE
    mysql_host: str = ""
    mysql_port: int = DEFAULT_MYSQL_PORT
    mysql_database: str = ""
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_charset: str = DEFAULT_MYSQL_CHARSET
    mysql_pool_size: int = DEFAULT_MYSQL_POOL_SIZE
    mysql_max_overflow: int = DEFAULT_MYSQL_MAX_OVERFLOW
    mysql_pool_recycle: int = DEFAULT_MYSQL_POOL_RECYCLE
    mysql_pool_pre_ping: bool = DEFAULT_MYSQL_POOL_PRE_PING
    redis_host: str = ""
    redis_port: int = DEFAULT_REDIS_PORT
    redis_password: str = ""
    redis_db: int = DEFAULT_REDIS_DB
    redis_socket_timeout: float = DEFAULT_REDIS_SOCKET_TIMEOUT
    redis_decode_responses: bool = DEFAULT_REDIS_DECODE_RESPONSES
    binance_base_url: str = DEFAULT_BINANCE_BASE_URL
    binance_timeout_seconds: float = DEFAULT_BINANCE_TIMEOUT_SECONDS
    binance_max_retries: int = DEFAULT_BINANCE_MAX_RETRIES
    binance_retry_backoff_seconds: float = DEFAULT_BINANCE_RETRY_BACKOFF_SECONDS
    binance_default_symbol: str = DEFAULT_SYMBOL
    binance_default_interval: str = DEFAULT_INTERVAL
    binance_kline_default_limit: int = DEFAULT_BINANCE_KLINE_DEFAULT_LIMIT
    binance_kline_max_limit: int = DEFAULT_BINANCE_KLINE_MAX_LIMIT
    binance_ws_base_url: str = DEFAULT_BINANCE_WS_BASE_URL
    price_monitor_symbol: str = DEFAULT_PRICE_MONITOR_SYMBOL
    price_monitor_ws_stream: str = DEFAULT_PRICE_MONITOR_WS_STREAM
    price_monitor_interval_seconds: int = DEFAULT_PRICE_MONITOR_INTERVAL_SECONDS
    price_monitor_change_threshold: str = DEFAULT_PRICE_MONITOR_CHANGE_THRESHOLD
    price_monitor_redis_key: str = DEFAULT_PRICE_MONITOR_REDIS_KEY
    price_monitor_redis_ttl_seconds: int = DEFAULT_PRICE_MONITOR_REDIS_TTL_SECONDS
    price_monitor_alert_cooldown_seconds: int = DEFAULT_PRICE_MONITOR_ALERT_COOLDOWN_SECONDS
    price_monitor_enable_price_alerts: bool = DEFAULT_PRICE_MONITOR_ENABLE_PRICE_ALERTS
    price_monitor_ws_reconnect_min_seconds: float = DEFAULT_PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS
    price_monitor_ws_reconnect_max_seconds: float = DEFAULT_PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS
    price_monitor_no_event_timeout_seconds: int = DEFAULT_PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS
    hermes_webhook_url: str = ""
    hermes_secret: str = ""
    hermes_timeout_seconds: float = DEFAULT_HERMES_TIMEOUT_SECONDS
    hermes_max_retries: int = DEFAULT_HERMES_MAX_RETRIES
    hermes_enabled: bool = DEFAULT_HERMES_ENABLED
    hermes_dry_run: bool = DEFAULT_HERMES_DRY_RUN

    def redacted_dict(self) -> dict[str, object]:
        """返回脱敏后的配置字典。

        参数：无。
        返回值：字典，敏感字段存在值时显示为 `***REDACTED***`。
        失败场景：无预期失败场景。
        外部服务：不访问外部服务。
        数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
        本方法不负责输出日志或暴露完整配置。
        """

        result: dict[str, object] = {}
        for field_name, value in self.__dict__.items():
            if field_name in SENSITIVE_FIELD_NAMES and value:
                result[field_name] = "***REDACTED***"
            else:
                result[field_name] = value
        return result

    def public_dict(self) -> dict[str, object]:
        """返回允许展示的非敏感配置摘要。

        参数：无。
        返回值：只包含 `APP_NAME`、`APP_ENV`、`APP_DEBUG`、`LOG_LEVEL`、`TIMEZONE`。
        失败场景：无预期失败场景。
        外部服务：不访问外部服务。
        数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
        本方法不负责打印 `.env` 或敏感字段。
        """

        return {
            "APP_NAME": self.app_name,
            "APP_ENV": self.app_env,
            "APP_DEBUG": self.app_debug,
            "LOG_LEVEL": self.log_level,
            "TIMEZONE": self.timezone,
        }

    def __repr__(self) -> str:
        return f"AppSettings({self.redacted_dict()!r})"


def _parse_dotenv_line(raw_line: str, line_no: int) -> tuple[str, str] | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        raise ConfigError(f".env 第 {line_no} 行格式错误，必须使用 KEY=VALUE")

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ConfigError(f".env 第 {line_no} 行配置名为空")
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return key, value


def load_dotenv_values(env_file: Path | None = DEFAULT_ENV_FILE) -> dict[str, str]:
    """读取 `.env` 文件中的键值对。

    参数：`env_file` 是可选 `.env` 路径，传入 `None` 时跳过文件读取。
    返回值：配置键值字典；文件不存在时返回空字典。
    失败场景：文件行格式错误或读取失败时抛出 `ConfigError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责把配置写入系统环境变量，也不打印配置内容。
    """

    if env_file is None or not env_file.exists():
        return {}

    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"无法读取 .env 文件：{env_file}") from exc

    values: dict[str, str] = {}
    for line_no, line in enumerate(lines, start=1):
        parsed = _parse_dotenv_line(line, line_no)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _get_config_value(values: Mapping[str, str], key: str, default: str = "") -> str:
    return values.get(key, default).strip()


def _parse_bool_config(raw_value: str, key: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigError(f"{key} 必须是布尔值：true/false/1/0/yes/no/on/off")


def _parse_int_config(raw_value: str, key: str, default: int) -> int:
    if raw_value.strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数") from exc


def _parse_float_config(raw_value: str, key: str, default: float) -> float:
    if raw_value.strip() == "":
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字") from exc


def _parse_optional_bool_config(raw_value: str, key: str, default: bool) -> bool:
    if raw_value.strip() == "":
        return default
    return _parse_bool_config(raw_value, key)


def load_settings(
    *,
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> AppSettings:
    """加载应用配置。

    参数：`env_file` 是 `.env` 路径；`environ` 是可注入的环境变量映射。
    返回值：`AppSettings` 配置对象。
    失败场景：配置文件格式错误、布尔值或端口无法转换、运行环境非法。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes，不修改质量记录。
    本函数只读取配置，不负责连接真实基础设施或自动交易。
    """

    merged_values = load_dotenv_values(env_file)
    runtime_environ = os.environ if environ is None else environ
    merged_values.update({key: value for key, value in runtime_environ.items()})

    app_env = _get_config_value(merged_values, "APP_ENV", APP_ENV_DEV).lower()
    if app_env not in ALLOWED_APP_ENVS:
        allowed = ", ".join(sorted(ALLOWED_APP_ENVS))
        raise ConfigError(f"APP_ENV 必须是以下值之一：{allowed}")

    log_level = _get_config_value(merged_values, "LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()

    return AppSettings(
        app_name=_get_config_value(merged_values, "APP_NAME", DEFAULT_APP_NAME),
        app_env=app_env,
        app_debug=_parse_bool_config(
            _get_config_value(merged_values, "APP_DEBUG", "false"),
            "APP_DEBUG",
        ),
        log_level=log_level,
        timezone=_get_config_value(merged_values, "TIMEZONE", DEFAULT_TIMEZONE),
        mysql_host=_get_config_value(merged_values, "MYSQL_HOST"),
        mysql_port=_parse_int_config(
            _get_config_value(merged_values, "MYSQL_PORT", str(DEFAULT_MYSQL_PORT)),
            "MYSQL_PORT",
            DEFAULT_MYSQL_PORT,
        ),
        mysql_database=_get_config_value(merged_values, "MYSQL_DATABASE"),
        mysql_user=_get_config_value(merged_values, "MYSQL_USER"),
        mysql_password=_get_config_value(merged_values, "MYSQL_PASSWORD"),
        mysql_charset=_get_config_value(
            merged_values,
            "MYSQL_CHARSET",
            DEFAULT_MYSQL_CHARSET,
        ),
        mysql_pool_size=_parse_int_config(
            _get_config_value(
                merged_values,
                "MYSQL_POOL_SIZE",
                str(DEFAULT_MYSQL_POOL_SIZE),
            ),
            "MYSQL_POOL_SIZE",
            DEFAULT_MYSQL_POOL_SIZE,
        ),
        mysql_max_overflow=_parse_int_config(
            _get_config_value(
                merged_values,
                "MYSQL_MAX_OVERFLOW",
                str(DEFAULT_MYSQL_MAX_OVERFLOW),
            ),
            "MYSQL_MAX_OVERFLOW",
            DEFAULT_MYSQL_MAX_OVERFLOW,
        ),
        mysql_pool_recycle=_parse_int_config(
            _get_config_value(
                merged_values,
                "MYSQL_POOL_RECYCLE",
                str(DEFAULT_MYSQL_POOL_RECYCLE),
            ),
            "MYSQL_POOL_RECYCLE",
            DEFAULT_MYSQL_POOL_RECYCLE,
        ),
        mysql_pool_pre_ping=_parse_optional_bool_config(
            _get_config_value(
                merged_values,
                "MYSQL_POOL_PRE_PING",
                str(DEFAULT_MYSQL_POOL_PRE_PING).lower(),
            ),
            "MYSQL_POOL_PRE_PING",
            DEFAULT_MYSQL_POOL_PRE_PING,
        ),
        redis_host=_get_config_value(merged_values, "REDIS_HOST"),
        redis_port=_parse_int_config(
            _get_config_value(merged_values, "REDIS_PORT", str(DEFAULT_REDIS_PORT)),
            "REDIS_PORT",
            DEFAULT_REDIS_PORT,
        ),
        redis_password=_get_config_value(merged_values, "REDIS_PASSWORD"),
        redis_db=_parse_int_config(
            _get_config_value(merged_values, "REDIS_DB", str(DEFAULT_REDIS_DB)),
            "REDIS_DB",
            DEFAULT_REDIS_DB,
        ),
        redis_socket_timeout=_parse_float_config(
            _get_config_value(
                merged_values,
                "REDIS_SOCKET_TIMEOUT",
                str(DEFAULT_REDIS_SOCKET_TIMEOUT),
            ),
            "REDIS_SOCKET_TIMEOUT",
            DEFAULT_REDIS_SOCKET_TIMEOUT,
        ),
        redis_decode_responses=_parse_optional_bool_config(
            _get_config_value(
                merged_values,
                "REDIS_DECODE_RESPONSES",
                str(DEFAULT_REDIS_DECODE_RESPONSES).lower(),
            ),
            "REDIS_DECODE_RESPONSES",
            DEFAULT_REDIS_DECODE_RESPONSES,
        ),
        binance_base_url=_get_config_value(
            merged_values,
            "BINANCE_BASE_URL",
            DEFAULT_BINANCE_BASE_URL,
        ),
        binance_timeout_seconds=_parse_float_config(
            _get_config_value(
                merged_values,
                "BINANCE_TIMEOUT_SECONDS",
                str(DEFAULT_BINANCE_TIMEOUT_SECONDS),
            ),
            "BINANCE_TIMEOUT_SECONDS",
            DEFAULT_BINANCE_TIMEOUT_SECONDS,
        ),
        binance_max_retries=_parse_int_config(
            _get_config_value(
                merged_values,
                "BINANCE_MAX_RETRIES",
                str(DEFAULT_BINANCE_MAX_RETRIES),
            ),
            "BINANCE_MAX_RETRIES",
            DEFAULT_BINANCE_MAX_RETRIES,
        ),
        binance_retry_backoff_seconds=_parse_float_config(
            _get_config_value(
                merged_values,
                "BINANCE_RETRY_BACKOFF_SECONDS",
                str(DEFAULT_BINANCE_RETRY_BACKOFF_SECONDS),
            ),
            "BINANCE_RETRY_BACKOFF_SECONDS",
            DEFAULT_BINANCE_RETRY_BACKOFF_SECONDS,
        ),
        binance_default_symbol=_get_config_value(
            merged_values,
            "BINANCE_DEFAULT_SYMBOL",
            DEFAULT_SYMBOL,
        ),
        binance_default_interval=_get_config_value(
            merged_values,
            "BINANCE_DEFAULT_INTERVAL",
            DEFAULT_INTERVAL,
        ),
        binance_kline_default_limit=_parse_int_config(
            _get_config_value(
                merged_values,
                "BINANCE_KLINE_DEFAULT_LIMIT",
                str(DEFAULT_BINANCE_KLINE_DEFAULT_LIMIT),
            ),
            "BINANCE_KLINE_DEFAULT_LIMIT",
            DEFAULT_BINANCE_KLINE_DEFAULT_LIMIT,
        ),
        binance_kline_max_limit=_parse_int_config(
            _get_config_value(
                merged_values,
                "BINANCE_KLINE_MAX_LIMIT",
                str(DEFAULT_BINANCE_KLINE_MAX_LIMIT),
            ),
            "BINANCE_KLINE_MAX_LIMIT",
            DEFAULT_BINANCE_KLINE_MAX_LIMIT,
        ),
        binance_ws_base_url=_get_config_value(
            merged_values,
            "BINANCE_WS_BASE_URL",
            DEFAULT_BINANCE_WS_BASE_URL,
        ),
        price_monitor_symbol=_get_config_value(
            merged_values,
            "PRICE_MONITOR_SYMBOL",
            DEFAULT_PRICE_MONITOR_SYMBOL,
        ),
        price_monitor_ws_stream=_get_config_value(
            merged_values,
            "PRICE_MONITOR_WS_STREAM",
            DEFAULT_PRICE_MONITOR_WS_STREAM,
        ),
        price_monitor_interval_seconds=_parse_int_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_INTERVAL_SECONDS",
                str(DEFAULT_PRICE_MONITOR_INTERVAL_SECONDS),
            ),
            "PRICE_MONITOR_INTERVAL_SECONDS",
            DEFAULT_PRICE_MONITOR_INTERVAL_SECONDS,
        ),
        price_monitor_change_threshold=_get_config_value(
            merged_values,
            "PRICE_MONITOR_CHANGE_THRESHOLD",
            DEFAULT_PRICE_MONITOR_CHANGE_THRESHOLD,
        ),
        price_monitor_redis_key=_get_config_value(
            merged_values,
            "PRICE_MONITOR_REDIS_KEY",
            DEFAULT_PRICE_MONITOR_REDIS_KEY,
        ),
        price_monitor_redis_ttl_seconds=_parse_int_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_REDIS_TTL_SECONDS",
                str(DEFAULT_PRICE_MONITOR_REDIS_TTL_SECONDS),
            ),
            "PRICE_MONITOR_REDIS_TTL_SECONDS",
            DEFAULT_PRICE_MONITOR_REDIS_TTL_SECONDS,
        ),
        price_monitor_alert_cooldown_seconds=_parse_int_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_ALERT_COOLDOWN_SECONDS",
                str(DEFAULT_PRICE_MONITOR_ALERT_COOLDOWN_SECONDS),
            ),
            "PRICE_MONITOR_ALERT_COOLDOWN_SECONDS",
            DEFAULT_PRICE_MONITOR_ALERT_COOLDOWN_SECONDS,
        ),
        price_monitor_enable_price_alerts=_parse_optional_bool_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_ENABLE_PRICE_ALERTS",
                str(DEFAULT_PRICE_MONITOR_ENABLE_PRICE_ALERTS).lower(),
            ),
            "PRICE_MONITOR_ENABLE_PRICE_ALERTS",
            DEFAULT_PRICE_MONITOR_ENABLE_PRICE_ALERTS,
        ),
        price_monitor_ws_reconnect_min_seconds=_parse_float_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS",
                str(DEFAULT_PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS),
            ),
            "PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS",
            DEFAULT_PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS,
        ),
        price_monitor_ws_reconnect_max_seconds=_parse_float_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS",
                str(DEFAULT_PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS),
            ),
            "PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS",
            DEFAULT_PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS,
        ),
        price_monitor_no_event_timeout_seconds=_parse_int_config(
            _get_config_value(
                merged_values,
                "PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS",
                str(DEFAULT_PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS),
            ),
            "PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS",
            DEFAULT_PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS,
        ),
        hermes_webhook_url=_get_config_value(merged_values, "HERMES_WEBHOOK_URL"),
        hermes_secret=_get_config_value(merged_values, "HERMES_SECRET"),
        hermes_timeout_seconds=_parse_float_config(
            _get_config_value(
                merged_values,
                "HERMES_TIMEOUT_SECONDS",
                str(DEFAULT_HERMES_TIMEOUT_SECONDS),
            ),
            "HERMES_TIMEOUT_SECONDS",
            DEFAULT_HERMES_TIMEOUT_SECONDS,
        ),
        hermes_max_retries=_parse_int_config(
            _get_config_value(
                merged_values,
                "HERMES_MAX_RETRIES",
                str(DEFAULT_HERMES_MAX_RETRIES),
            ),
            "HERMES_MAX_RETRIES",
            DEFAULT_HERMES_MAX_RETRIES,
        ),
        hermes_enabled=_parse_optional_bool_config(
            _get_config_value(
                merged_values,
                "HERMES_ENABLED",
                str(DEFAULT_HERMES_ENABLED).lower(),
            ),
            "HERMES_ENABLED",
            DEFAULT_HERMES_ENABLED,
        ),
        hermes_dry_run=_parse_optional_bool_config(
            _get_config_value(
                merged_values,
                "HERMES_DRY_RUN",
                str(DEFAULT_HERMES_DRY_RUN).lower(),
            ),
            "HERMES_DRY_RUN",
            DEFAULT_HERMES_DRY_RUN,
        ),
    )


def get_settings(*, reload: bool = False) -> AppSettings:
    """获取缓存的应用配置对象。

    参数：`reload` 为 True 时重新读取 `.env` 与系统环境变量。
    返回值：`AppSettings` 配置对象。
    失败场景：同 `load_settings()`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责打印配置、连接基础设施或自动交易。
    """

    global _SETTINGS
    if _SETTINGS is None or reload:
        _SETTINGS = load_settings()
    return _SETTINGS

