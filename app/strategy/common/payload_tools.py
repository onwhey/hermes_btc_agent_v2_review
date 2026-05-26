"""JSON payload helpers for the strategy common contract.

This file belongs to `app/strategy/common`. It serializes and hashes bounded
strategy result payloads for validation and persistence.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping


def canonical_json_text(value: Any) -> str:
    """Return deterministic UTF-8 JSON text for a strategy payload.

    Parameters: JSON-ready value, dataclass, Decimal, datetime, tuple, or list.
    Return value: canonical JSON text with sorted keys.
    Failure scenarios: non-serializable values raise `TypeError`.
    External effects: none.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def payload_size_bytes(value: Any) -> int:
    """Return the UTF-8 byte size of the canonical JSON payload."""

    return len(canonical_json_text(value).encode("utf-8"))


def payload_sha256(value: Any) -> str:
    """Return the SHA-256 hash of the canonical JSON payload."""

    raw = canonical_json_text(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_json_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Validate that a payload is a JSON-serializable mapping.

    Parameters: mapping or `None`.
    Return value: original mapping, or an empty mapping for `None`.
    Failure scenarios: non-mapping values or non-serializable content raise.
    External effects: none.
    """

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("strategy payload must be a mapping")
    canonical_json_text(value)
    return value


def json_loads_mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping parsed from JSON text or an existing mapping."""

    if isinstance(value, Mapping):
        return value
    if value is None or value == "":
        return {}
    loaded = json.loads(str(value))
    if not isinstance(loaded, Mapping):
        raise TypeError("payload JSON must decode to an object")
    return loaded


def _json_default(value: Any) -> Any:
    if hasattr(value, "to_jsonable") and callable(value.to_jsonable):
        return value.to_jsonable()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


__all__ = [
    "canonical_json_text",
    "ensure_json_mapping",
    "json_loads_mapping",
    "payload_sha256",
    "payload_size_bytes",
]
