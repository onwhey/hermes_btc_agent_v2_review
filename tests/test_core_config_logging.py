from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import AppSettings, load_settings
from app.core.exceptions import AppError, ConfigError, ExternalServiceError, ValidationError
from app.core.logger import configure_logging, redact_sensitive_text
from app.core.time_utils import (
    PRC_TIME_ZONE,
    now_prc,
    now_utc,
    timestamp_ms_to_utc_datetime,
    utc_aware_to_prc_aware,
    utc_datetime_to_timestamp_ms,
    utc_naive_to_prc_naive,
)
from scripts.check_core_config_logging import collect_core_config_logging_errors


def test_settings_can_load_with_defaults_without_external_services() -> None:
    settings = load_settings(env_file=None, environ={})

    assert settings.app_name == "hermes_btc_agent"
    assert settings.app_env == "dev"
    assert settings.app_debug is False
    assert settings.log_level == "INFO"


def test_app_debug_can_be_converted_from_environment() -> None:
    settings = load_settings(env_file=None, environ={"APP_DEBUG": "true"})

    assert settings.app_debug is True


def test_settings_repr_redacts_sensitive_values() -> None:
    settings = load_settings(
        env_file=None,
        environ={
            "MYSQL_PASSWORD": "mysql-secret",
            "HERMES_SECRET": "hermes-secret",
            "HERMES_WEBHOOK_URL": "https://example.invalid/private",
        },
    )

    rendered = repr(settings)

    assert "mysql-secret" not in rendered
    assert "hermes-secret" not in rendered
    assert "https://example.invalid/private" not in rendered


def test_logger_repeated_initialization_does_not_duplicate_same_file_handler(tmp_path) -> None:
    settings = AppSettings(log_level="INFO")
    log_file = tmp_path / "app.log"

    logger = configure_logging(settings, enable_console=False, enable_file=True, log_file=log_file)
    configure_logging(settings, enable_console=False, enable_file=True, log_file=log_file)

    matching_handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_hermes_handler_key", "") == f"file:{log_file.resolve()}"
    ]

    assert len(matching_handlers) == 1


def test_logger_redacts_sensitive_text() -> None:
    message = redact_sensitive_text(
        "password=abc secret:xyz webhook=https://example.invalid/hook",
        sensitive_values=("abc", "xyz"),
    )

    assert "abc" not in message
    assert "xyz" not in message
    assert "https://example.invalid/hook" not in message
    assert "***REDACTED***" in message


def test_utc_and_prc_now_return_aware_datetimes() -> None:
    assert now_utc().tzinfo is not None
    assert now_prc().tzinfo is not None


def test_utc_naive_to_prc_naive_converts_correctly() -> None:
    source = datetime(2026, 1, 1, 0, 0, 0)
    expected = source.replace(tzinfo=timezone.utc).astimezone(PRC_TIME_ZONE).replace(tzinfo=None)

    assert utc_naive_to_prc_naive(source) == expected


def test_utc_aware_to_prc_aware_converts_correctly() -> None:
    source = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    converted = utc_aware_to_prc_aware(source)

    assert converted.tzinfo is not None
    assert converted.utcoffset() == PRC_TIME_ZONE.utcoffset(converted)


def test_millisecond_timestamp_and_utc_datetime_round_trip() -> None:
    source = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    timestamp_ms = utc_datetime_to_timestamp_ms(source)

    assert timestamp_ms_to_utc_datetime(timestamp_ms) == source


def test_core_exceptions_can_be_instantiated() -> None:
    for error_class in (AppError, ConfigError, ValidationError, ExternalServiceError):
        assert str(error_class("message")) == "message"


def test_core_config_logging_check_passes_without_external_services() -> None:
    assert collect_core_config_logging_errors() == []

