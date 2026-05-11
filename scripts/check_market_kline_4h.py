"""Manual phase-06 check for 4h Kline foundation modules.

Triggered by: user CLI only.
Scheduler use: not allowed.
Required parameters: none.
App modules called: DTO, parser, validator, ORM model import, repository import.
Not responsible for: Binance requests, MySQL writes, Redis access, Hermes sends,
collection, backfill, integrity review, data repair, migrations, or trading.
Formal Kline table impact: no writes and no automatic migration execution.
"""

from __future__ import annotations

from pathlib import Path

from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_parser import parse_binance_kline
from app.market_data.kline_validator import validate_market_kline
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories.market_kline_4h_repository import MarketKline4hRepository

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_GLOB = "*_create_market_kline_4h.py"


def collect_market_kline_4h_errors() -> list[str]:
    """Run import and pure parsing checks for phase 06.

    Parameters: none.
    Return value: list of human-readable errors; empty means the check passed.
    Failure scenarios: import, parser, validator, model, repository, or migration
    existence problems are collected as strings.
    External service access: none.
    Data impact: no MySQL or Redis writes and no alert sends.
    """

    errors: list[str] = []
    try:
        raw_kline = [
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
        ]
        dto = parse_binance_kline(
            raw_kline,
            symbol="BTCUSDT",
            interval_value=KLINE_4H_INTERVAL_VALUE,
            trigger_source=TRIGGER_SOURCE_CLI,
        )
        validate_market_kline(dto)
    except Exception as exc:  # noqa: BLE001 - check script summarizes import/validation issues.
        errors.append(f"parser_or_validator_failed: {exc}")

    if getattr(MarketKline4h, "__name__", "") != "MarketKline4h":
        errors.append("MarketKline4h model import failed")

    repository = MarketKline4hRepository()
    if repository.__class__.__name__ != "MarketKline4hRepository":
        errors.append("MarketKline4hRepository import failed")

    migration_files = list((ROOT / "migrations" / "versions").glob(MIGRATION_GLOB))
    if not migration_files:
        errors.append("market_kline_4h migration file not found")

    return errors


def main() -> int:
    """CLI entry point for the phase-06 local check."""

    errors = collect_market_kline_4h_errors()
    if errors:
        print("market_kline_4h check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("market_kline_4h check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

