"""统一日志初始化模块。

本文件属于 `app/core` 基础能力层，负责初始化项目 logger、控制日志级别、
提供控制台与文件输出，并对敏感信息做基础脱敏。
本文件不负责连接 MySQL、Redis、Binance 或 Hermes，不调用 DeepSeek，
不写业务表，不实现 scheduler、K 线采集或任何交易执行能力。
主要被脚本入口、后续基础设施模块和测试调用。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterable

from app.core.config import AppSettings, get_settings
from app.core.constants import (
    DEFAULT_LOG_FILE_NAME,
    LOG_DIR_NAME,
    SENSITIVE_FIELD_NAMES,
    SENSITIVE_TEXT_MARKERS,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = ROOT_DIR / LOG_DIR_NAME
LOGGER_NAME = "hermes_btc_agent"
LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class UtcFormatter(logging.Formatter):
    """UTC 日志时间 formatter。

    参数：同 `logging.Formatter`。
    返回值：formatter 对象。
    失败场景：格式字符串非法时由 logging 抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责业务审计记录、数据库事件记录或自动交易。
    """

    converter = time.gmtime


class SensitiveDataFilter(logging.Filter):
    """日志敏感信息脱敏 filter。

    参数：`sensitive_values` 是需要从日志消息中替换掉的实际敏感值集合。
    返回值：filter 对象。
    失败场景：无预期失败场景；异常由 logging 调用链处理。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类只做日志文本脱敏，不负责配置读取、密钥管理或自动交易。
    """

    def __init__(self, sensitive_values: Iterable[str] = ()) -> None:
        super().__init__()
        self._sensitive_values = tuple(value for value in sensitive_values if value)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact_sensitive_text(message, self._sensitive_values)
        record.args = ()
        return True


def redact_sensitive_text(
    message: str,
    sensitive_values: Iterable[str] = (),
) -> str:
    """脱敏日志文本。

    参数：`message` 是日志消息；`sensitive_values` 是必须隐藏的实际敏感值。
    返回值：脱敏后的字符串。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责打印日志或保存配置。
    """

    redacted = message
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "***REDACTED***")

    marker_pattern = "|".join(re.escape(marker) for marker in SENSITIVE_TEXT_MARKERS)
    redacted = re.sub(
        rf"(?i)\b({marker_pattern})\b(\s*[:=]\s*)([^,\s]+)",
        r"\1\2***REDACTED***",
        redacted,
    )
    return redacted


def _collect_sensitive_values(settings: AppSettings) -> tuple[str, ...]:
    values: list[str] = []
    for field_name in SENSITIVE_FIELD_NAMES:
        value = getattr(settings, field_name, "")
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _resolve_log_level(log_level: str) -> int:
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        return logging.INFO
    return level


def _build_formatter() -> UtcFormatter:
    return UtcFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


def _remove_sensitive_filters(
    filter_owner: logging.Logger | logging.Handler,
) -> None:
    filter_owner.filters = [
        log_filter
        for log_filter in filter_owner.filters
        if not isinstance(log_filter, SensitiveDataFilter)
    ]


def _replace_sensitive_filters(
    handler: logging.Handler,
    sensitive_values: Iterable[str],
) -> None:
    _remove_sensitive_filters(handler)
    handler.addFilter(SensitiveDataFilter(sensitive_values))


def _has_handler(logger: logging.Logger, handler_key: str) -> bool:
    return any(getattr(handler, "_hermes_handler_key", "") == handler_key for handler in logger.handlers)


def configure_logging(
    settings: AppSettings | None = None,
    *,
    enable_console: bool = True,
    enable_file: bool = True,
    log_file: Path | None = None,
) -> logging.Logger:
    """初始化项目 logger。

    参数：`settings` 提供日志级别和敏感配置；`enable_console` / `enable_file`
    控制输出目标；`log_file` 可覆盖默认日志文件路径。
    返回值：已配置的 `logging.Logger`。
    失败场景：日志目录或文件无法创建时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务事件入库、Hermes 报警或自动交易。
    """

    active_settings = settings or get_settings()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_resolve_log_level(active_settings.log_level))
    logger.propagate = False

    formatter = _build_formatter()
    sensitive_values = _collect_sensitive_values(active_settings)
    _remove_sensitive_filters(logger)

    if enable_console and not _has_handler(logger, "console"):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logger.level)
        _replace_sensitive_filters(console_handler, sensitive_values)
        console_handler._hermes_handler_key = "console"  # type: ignore[attr-defined]
        logger.addHandler(console_handler)

    if enable_file:
        resolved_log_file = log_file or (DEFAULT_LOG_DIR / DEFAULT_LOG_FILE_NAME)
        resolved_log_file.parent.mkdir(parents=True, exist_ok=True)
        file_key = f"file:{resolved_log_file.resolve()}"
        if not _has_handler(logger, file_key):
            file_handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logger.level)
            _replace_sensitive_filters(file_handler, sensitive_values)
            file_handler._hermes_handler_key = file_key  # type: ignore[attr-defined]
            logger.addHandler(file_handler)

    for handler in logger.handlers:
        handler.setLevel(logger.level)
        handler.setFormatter(formatter)
        _replace_sensitive_filters(handler, sensitive_values)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取项目 logger。

    参数：`name` 是可选子 logger 名称。
    返回值：项目根 logger 或子 logger。
    失败场景：配置读取失败时由 `get_settings()` 抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责连接基础设施、写业务表或自动交易。
    """

    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        configure_logging()
    if name:
        child_logger = logging.getLogger(f"{LOGGER_NAME}.{name}")
        child_logger.propagate = True
        return child_logger
    return logger
