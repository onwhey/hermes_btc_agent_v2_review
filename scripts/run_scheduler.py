"""Persistent phase-12 scheduler process entry.

Triggered by: a user or process manager such as systemd.
Manual execution: allowed for starting the long-running scheduler process.
Scheduler execution: this script is the scheduler process entry itself.
Required args: none.
Calls: `app/scheduler/runner.py::run_scheduler_forever`.
External effects: the runner writes Redis execution-slot keys, calls thin app
scheduler jobs for phases 09 and 11, and may send fixed-template Hermes system
alerts for scheduler wrapper failures.
This script itself does not request Binance, read/write MySQL, read/write
Redis, send Hermes, call phase 09/11 scripts, modify formal Klines, repair data,
backfill extra ranges, call DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.core.exceptions import ConfigError
from app.core.logger import configure_logging, get_logger
from app.scheduler.config import build_scheduler_runtime_config
from app.scheduler.runner import run_scheduler_forever

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1

LOGGER = get_logger("scripts.run_scheduler")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for the persistent scheduler process."""

    return argparse.ArgumentParser(description="Run Hermes BTC phase-12 scheduler process.")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse operational args, validate scheduler config, and start the runner."""

    parser = build_arg_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_SUCCESS if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    try:
        settings = get_settings()
        configure_logging(settings)
        config = build_scheduler_runtime_config(settings)
    except ConfigError as exc:
        print(f"scheduler config error: {exc}")
        return EXIT_PARAMETER_ERROR

    try:
        run_scheduler_forever(settings=settings, config=config)
    except KeyboardInterrupt:
        LOGGER.info("scheduler process stopped by keyboard interrupt")
        return EXIT_SUCCESS

    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
