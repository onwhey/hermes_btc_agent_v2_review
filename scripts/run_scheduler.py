"""Persistent phase-12 scheduler process entry.

Triggered by: a user or process manager such as systemd.
Manual execution: allowed for starting the long-running scheduler process.
Scheduler execution: this script is the scheduler process entry itself.
Required args: none.
Calls: `app/scheduler/runner.py::run_scheduler_forever`.
External effects: the runner writes Redis execution-slot keys, calls thin app
scheduler jobs for phases 09 and 11, and may send fixed-template Hermes system
alerts for scheduler wrapper failures. During startup, this script may send a
fixed-template Hermes system alert only when scheduler config parsing fails
after alerting settings have been loaded.
This script itself does not request Binance, read/write MySQL, read/write
Redis, call phase 09/11 scripts, modify formal Klines, repair data,
backfill extra ranges, call DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

import argparse
import time as time_module
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.config import AppSettings, ROOT_DIR, get_settings
from app.core.exceptions import ConfigError
from app.core.logger import configure_logging, get_logger
from app.scheduler.config import build_scheduler_runtime_config
from app.scheduler.runner import run_scheduler_forever

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
STARTUP_CONFIG_ERROR_ALERT_COOLDOWN_SECONDS = 300
STARTUP_CONFIG_ERROR_ALERT_COOLDOWN_FILE = ROOT_DIR / "logs" / "scheduler_startup_config_error_alert.cooldown"

LOGGER = get_logger("scripts.run_scheduler")
AlertSender = Callable[..., Any]


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for the persistent scheduler process."""

    return argparse.ArgumentParser(description="Run Hermes BTC phase-12 scheduler process.")


def _is_startup_config_error_alert_in_cooldown(cooldown_file: Path) -> bool:
    """Return whether startup config-error alert is inside local cooldown.

    This lightweight marker prevents systemd restart loops from repeatedly
    sending the same scheduler startup failure. It does not affect 09/11 service
    business notifications and does not read or write any trading data.
    """

    try:
        last_attempt_at = cooldown_file.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOGGER.warning("scheduler startup alert cooldown read failed: %s", exc)
        return False
    return (time_module.time() - last_attempt_at) < STARTUP_CONFIG_ERROR_ALERT_COOLDOWN_SECONDS


def _mark_startup_config_error_alert_attempt(cooldown_file: Path) -> None:
    """Persist a tiny local marker after a scheduler startup alert attempt."""

    try:
        cooldown_file.parent.mkdir(parents=True, exist_ok=True)
        cooldown_file.touch()
    except OSError as exc:
        LOGGER.warning("scheduler startup alert cooldown write failed: %s", exc)


def _send_scheduler_startup_config_error_alert(
    *,
    settings: AppSettings | None,
    error: ConfigError,
    alert_sender: AlertSender | None = None,
    cooldown_file: Path = STARTUP_CONFIG_ERROR_ALERT_COOLDOWN_FILE,
) -> bool:
    """Best-effort fixed-template alert for scheduler startup config failures.

    Parameters: `settings` must be available for alerting initialization;
    `error` is the sanitized ConfigError; `alert_sender` and `cooldown_file` are
    injectable for tests.
    Return value: True when an alert attempt was made, False when skipped.
    Failure scenarios: alerting initialization/send failures are logged and do
    not change the non-zero scheduler exit.
    External effects: may send one Hermes fixed-template system alert and may
    write a small local cooldown marker. It does not call DeepSeek, repair data,
    backfill Klines, run 09/11 jobs, call scripts, or perform trading.
    """

    if settings is None:
        LOGGER.error("scheduler config error before alerting settings loaded: %s", error)
        return False

    if _is_startup_config_error_alert_in_cooldown(cooldown_file):
        LOGGER.warning("scheduler startup config error alert suppressed by local cooldown: %s", error)
        return False

    _mark_startup_config_error_alert_attempt(cooldown_file)
    event = AlertEvent(
        alert_type=AlertType.SYSTEM_ERROR,
        severity=AlertSeverity.CRITICAL,
        title="Scheduler startup config error",
        summary="Scheduler did not start because runtime configuration parsing failed.",
        details={
            "scheduler_stage": "startup",
            "scheduler_started": False,
            "error_type": error.__class__.__name__,
            "error_message": str(error),
            "no_auto_repair": True,
            "no_auto_backfill": True,
            "no_trading": True,
        },
        source="scripts.run_scheduler",
        trace_id=uuid4().hex,
    )
    try:
        sender = alert_sender or _default_alert_sender
        sender(event, settings=settings, send_real_alert=True)
        return True
    except Exception as exc:  # noqa: BLE001 - startup must exit cleanly even when alerting fails.
        LOGGER.exception("scheduler startup config error alert failed: %s", exc)
        return False


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse operational args, validate scheduler config, and start the runner."""

    parser = build_arg_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_SUCCESS if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    settings: AppSettings | None = None
    try:
        settings = get_settings()
        configure_logging(settings)
        config = build_scheduler_runtime_config(settings)
    except ConfigError as exc:
        LOGGER.error("scheduler config error: %s", exc)
        print(f"scheduler config error: {exc}")
        _send_scheduler_startup_config_error_alert(settings=settings, error=exc)
        return EXIT_PARAMETER_ERROR

    try:
        run_scheduler_forever(settings=settings, config=config)
    except KeyboardInterrupt:
        LOGGER.info("scheduler process stopped by keyboard interrupt")
        return EXIT_SUCCESS

    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
