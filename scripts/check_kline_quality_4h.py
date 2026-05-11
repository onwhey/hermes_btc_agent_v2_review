"""Manual CLI entry for phase-07 4h Kline quality checks.

Triggered by: a user running `python -m scripts.check_kline_quality_4h`.
Manual execution: allowed.
Scheduler execution: not allowed in phase 07; this script accepts only `--trigger-source cli`.
Required args: none. Default mode performs only a local smoke check.
Real check args: `--run-real-check` plus optional `--symbol`, `--interval`, `--limit`, and `--send-alert`.
Calls: `app/market_data/kline_quality/service.py::run_recent_kline_integrity_check`.
Business logic: lives in `app/market_data/kline_quality`, not in this script.
Database impact: none by default; writes only `data_quality_check` when `--run-real-check` is used.
Redis impact: none.
Hermes impact: no real send unless the user passes both `--run-real-check` and `--send-alert`
and Hermes config allows it.
Formal Kline impact: never writes, overwrites, deletes, or fixes `market_kline_4h`.
Trading impact: none.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)
from app.market_data.kline_parser import parse_binance_klines
from app.market_data.kline_quality.report_formatter import format_quality_report_lines
from app.market_data.kline_quality.service import run_recent_kline_integrity_check
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_CLI

SAMPLE_RAW_KLINES = [
    [
        1_700_006_400_000,
        "35000.10000000",
        "36000.20000000",
        "34000.30000000",
        "35500.40000000",
        "123.45600000",
        1_700_020_799_999,
        "4567890.12300000",
        9876,
        "66.70000000",
        "2345678.90000000",
        "0",
    ],
    [
        1_700_006_400_000 + KLINE_4H_INTERVAL_MS,
        "35500.40000000",
        "36500.20000000",
        "34500.30000000",
        "36000.40000000",
        "223.45600000",
        1_700_020_799_999 + KLINE_4H_INTERVAL_MS,
        "5567890.12300000",
        8876,
        "76.70000000",
        "3345678.90000000",
        "0",
    ],
]


def collect_kline_quality_4h_errors() -> list[str]:
    """Run a pure local import and batch-check smoke test.

    Parameters: none.
    Return value: list of error messages; empty means the local check passed.
    Failure scenarios: parser or quality checker defects are captured as strings.
    External service access and data impact: none; no MySQL, Binance, Redis, or Hermes.
    """

    from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist

    errors: list[str] = []
    try:
        klines = parse_binance_klines(
            SAMPLE_RAW_KLINES,
            symbol=DEFAULT_KLINE_SYMBOL,
            interval_value=KLINE_4H_INTERVAL_VALUE,
            trigger_source=TRIGGER_SOURCE_CLI,
        )
        report = check_kline_batch_before_persist(
            klines,
            server_time_ms=klines[-1].close_time_ms + 1,
            check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
        )
        if not report.passed:
            errors.extend(issue.message for issue in report.issues)
    except Exception as exc:  # noqa: BLE001 - manual smoke check must report all failures.
        errors.append(str(exc))
    return errors


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the phase-07 manual CLI parser."""

    parser = argparse.ArgumentParser(description="Run a phase-07 4h Kline quality check.")
    parser.add_argument("--run-real-check", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--interval", default=KLINE_4H_INTERVAL_VALUE, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--trigger-source", default=CHECK_TRIGGER_SOURCE_CLI, choices=[CHECK_TRIGGER_SOURCE_CLI])
    parser.add_argument("--send-alert", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a safe local check by default, or the real check when explicitly requested.

    Parameters: optional argv for tests; defaults to process argv.
    Return value: shell exit code.
    Failure scenarios: local smoke failures return non-zero; real-check database, Binance,
    parser, or checker errors propagate to the shell.
    External service access: none by default; may request Binance public REST only with
    `--run-real-check`.
    Data impact: none by default; real mode writes only `data_quality_check`, not formal Klines.
    """

    args = build_arg_parser().parse_args(argv)
    if not args.run_real_check:
        errors = collect_kline_quality_4h_errors()
        if errors:
            for error in errors:
                print(f"local_smoke_error={error}")
            return 1
        print("local_smoke_check=passed")
        return 0

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as db_session:
        report = run_recent_kline_integrity_check(
            db_session,
            symbol=args.symbol,
            interval_value=args.interval,
            limit=args.limit,
            check_trigger_source=args.trigger_source,
            send_alert=args.send_alert,
        )
    for line in format_quality_report_lines(report):
        print(line)
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
