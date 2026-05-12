"""Typed objects for the 10s WebSocket price monitor.

This file belongs to `app/market_data/price_monitor`.
It defines request, event, Redis state, change result, and service result
objects. It does not connect to Binance, read/write Redis, read/write MySQL,
send Hermes, call DeepSeek, request REST latest prices, generate advice, or
perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from app.core.constants import (
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
)
from app.core.time_utils import now_utc, utc_aware_to_prc_aware

PRICE_SOURCE_BINANCE_WS_AGG_TRADE = "binance_ws_agg_trade"
TRIGGER_SOURCE_CLI = "cli"
TRIGGER_SOURCE_SYSTEMD = "systemd"
TRIGGER_SOURCE_SUPERVISOR = "supervisor"
ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES = frozenset(
    {TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SYSTEMD, TRIGGER_SOURCE_SUPERVISOR}
)

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_ALERT_FAILED = 3
EXIT_RUNTIME_ERROR = 4


class PriceMonitorStatus(str, Enum):
    """Price monitor service status values."""

    INITIALIZED = "initialized"
    UPDATED = "updated"
    ALERTED = "alerted"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    NO_RECENT_PRICE = "no_recent_price"


@dataclass(frozen=True)
class PriceEvent:
    """Parsed Binance aggTrade price event.

    Parameters: price and event times come from Binance public WebSocket;
    `received_at_*` marks local receipt for diagnostics.
    Return value: immutable event object.
    Failure scenarios: parser rejects malformed input before construction.
    External service access: none in this object.
    Data impact: no Redis/MySQL writes, Hermes sends, DeepSeek, or trading.
    """

    symbol: str
    price: Decimal
    event_time_ms: int
    trade_time_ms: int
    received_at_utc: datetime = field(default_factory=now_utc)
    received_at_prc: datetime | None = None
    source: str = PRICE_SOURCE_BINANCE_WS_AGG_TRADE

    def __post_init__(self) -> None:
        if self.received_at_prc is None:
            object.__setattr__(self, "received_at_prc", utc_aware_to_prc_aware(self.received_at_utc))


@dataclass(frozen=True)
class PriceState:
    """Redis price state for `bitcoin_price`.

    Parameters: state fields are short-lived real-time diagnostics only.
    Return value: immutable state object.
    Failure scenarios: parsing failures are raised before construction.
    External service access: none in this object.
    Data impact: this object does not write Redis/MySQL or send Hermes.
    """

    symbol: str
    price: Decimal
    event_time_ms: int
    trade_time_ms: int
    saved_at_utc: datetime
    saved_at_prc: datetime
    source: str = PRICE_SOURCE_BINANCE_WS_AGG_TRADE


@dataclass(frozen=True)
class PriceChangeResult:
    """Result of Decimal price movement detection."""

    has_previous: bool
    exceeded: bool
    direction: str
    previous_price: Decimal | None
    current_price: Decimal
    change_ratio: Decimal
    change_percent: Decimal
    threshold: Decimal
    reason: str = ""


@dataclass(frozen=True)
class PriceMonitorConfig:
    """Runtime config for one price monitor process.

    Parameters: values come from CLI/config and are validated by the service.
    Return value: immutable config object.
    Failure scenarios: invalid values are rejected before the monitor starts.
    External service access: this object does not access external services.
    Data impact: no Redis/MySQL writes, Hermes sends, or trading.
    """

    symbol: str = DEFAULT_PRICE_MONITOR_SYMBOL
    trigger_source: str = ""
    ws_stream: str = DEFAULT_PRICE_MONITOR_WS_STREAM
    monitor_interval_seconds: int = DEFAULT_PRICE_MONITOR_INTERVAL_SECONDS
    price_change_threshold: Decimal = Decimal(DEFAULT_PRICE_MONITOR_CHANGE_THRESHOLD)
    redis_key: str = DEFAULT_PRICE_MONITOR_REDIS_KEY
    redis_ttl_seconds: int = DEFAULT_PRICE_MONITOR_REDIS_TTL_SECONDS
    alert_cooldown_seconds: int = DEFAULT_PRICE_MONITOR_ALERT_COOLDOWN_SECONDS
    enable_price_alerts: bool = DEFAULT_PRICE_MONITOR_ENABLE_PRICE_ALERTS
    reconnect_min_seconds: float = DEFAULT_PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS
    reconnect_max_seconds: float = DEFAULT_PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS
    no_event_timeout_seconds: int = DEFAULT_PRICE_MONITOR_NO_EVENT_TIMEOUT_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class PriceMonitorResult:
    """Result returned by one monitor check or startup failure."""

    status: PriceMonitorStatus
    exit_code: int
    message: str
    trace_id: str
    redis_written: bool = False
    alert_status: str = ""
    details: dict[str, object] = field(default_factory=dict)

