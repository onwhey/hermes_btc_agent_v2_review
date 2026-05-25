"""Decimal helpers for stage-22A manual execution feedback.

This file belongs to `app/manual_execution`. It centralizes Decimal parsing,
formatting, and quantization so manual execution calculations never use float.
It does not read/write MySQL, read Redis, send Hermes, call DeepSeek, request
Binance, or perform automatic trading.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.core.exceptions import ValidationError

ZERO = Decimal("0")
ONE = Decimal("1")
DECIMAL_SCALE = Decimal("0.000000000000000001")


def parse_decimal_value(value: Decimal | str | int | None, field_name: str) -> Decimal:
    """Parse a required Decimal-compatible value and explicitly reject floats."""

    if value is None:
        raise ValidationError(f"{field_name} is required")
    if isinstance(value, float):
        raise ValidationError(f"{field_name} must use Decimal or string, not float")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field_name} must be Decimal-compatible") from exc
    if not parsed.is_finite():
        raise ValidationError(f"{field_name} must be finite")
    return parsed


def parse_optional_decimal_value(value: Decimal | str | int | None, field_name: str) -> Decimal | None:
    """Parse an optional Decimal-compatible value and explicitly reject floats."""

    if value is None:
        return None
    return parse_decimal_value(value, field_name)


def parse_fee_rate(value: Decimal | str | int | None) -> Decimal:
    """Parse and validate the configured fee rate."""

    fee_rate = parse_decimal_value(value, "manual_execution_fee_rate")
    if fee_rate < ZERO:
        raise ValidationError("manual_execution_fee_rate must be >= 0")
    return fee_rate


def decimal_to_text(value: Decimal | None) -> str:
    """Return a stable CLI-friendly Decimal string without using float."""

    if value is None:
        return ""
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def quantize_decimal(value: Decimal) -> Decimal:
    """Quantize persisted Decimal values to match the migration scale."""

    return value.quantize(DECIMAL_SCALE, rounding=ROUND_HALF_UP)


__all__ = [
    "DECIMAL_SCALE",
    "ONE",
    "ZERO",
    "decimal_to_text",
    "parse_decimal_value",
    "parse_fee_rate",
    "parse_optional_decimal_value",
    "quantize_decimal",
]
