"""Binance public WebSocket market stream client.

Call chain:
scripts/run_price_monitor_10s.py::main
    -> app/market_data/price_monitor/price_monitor_service.py::run_price_monitor
    -> app/exchange/binance/websocket_market_client.py::BinanceWebSocketMarketClient.connect_and_listen

This file belongs to `app/exchange/binance`.
It builds Binance USD-M Futures public market WebSocket URLs, opens a persistent
market stream, reconnects on disconnects, and forwards raw messages to a caller
callback. It does not parse price events, write Redis, write MySQL, send Hermes,
call DeepSeek, request REST latest prices, generate advice, or perform trading.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.constants import DEFAULT_BINANCE_WS_BASE_URL
from app.core.logger import get_logger
from app.exchange.binance.exceptions import BinanceValidationError, BinanceWebSocketError
from app.exchange.binance.rest_client import normalize_binance_symbol

ALLOWED_MARKET_STREAMS = frozenset({"aggTrade"})

RawWebSocketHandler = Callable[[str], Any | Awaitable[Any]]
WebSocketConnectFactory = Callable[[str], Any]


@dataclass(frozen=True)
class BinanceWebSocketRuntimeConfig:
    """Runtime settings for a public Binance WebSocket market stream.

    Parameters: `symbol` is an uppercase public symbol; `stream_name` is the
    allowed public stream type; reconnect bounds are seconds.
    Return value: immutable config object.
    Failure scenarios: validation happens before construction in caller helpers.
    External service access: this object does not open a network connection.
    Data impact: no MySQL writes, Redis writes, Hermes sends, or trading actions.
    """

    symbol: str
    stream_name: str = "aggTrade"
    base_url: str = DEFAULT_BINANCE_WS_BASE_URL
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0


def validate_market_stream_name(stream_name: str) -> str:
    """Validate the current phase public WebSocket stream name.

    Parameters: `stream_name` is the configured market stream, currently only
    `aggTrade`.
    Return value: the validated stream name.
    Failure scenarios: unsupported streams raise `BinanceValidationError`.
    External service access: none.
    Data impact: no Redis, MySQL, Hermes, DeepSeek, REST, or trading.
    """

    normalized = stream_name.strip()
    if normalized not in ALLOWED_MARKET_STREAMS:
        allowed = ", ".join(sorted(ALLOWED_MARKET_STREAMS))
        raise BinanceValidationError(f"Unsupported Binance WebSocket stream: {stream_name}. Allowed: {allowed}")
    return normalized


def build_market_stream_name(symbol: str, stream_name: str = "aggTrade") -> str:
    """Build the Binance public market stream name.

    Parameters: `symbol` is a public symbol such as BTCUSDT; `stream_name` is
    the allowed stream type.
    Return value: Binance stream name, for example `btcusdt@aggTrade`.
    Failure scenarios: invalid symbol or stream raises validation errors.
    External service access: none.
    Data impact: no Redis writes, MySQL writes, Hermes sends, or trading.
    """

    return f"{normalize_binance_symbol(symbol).lower()}@{validate_market_stream_name(stream_name)}"


def build_market_ws_url(
    *,
    symbol: str,
    stream_name: str = "aggTrade",
    base_url: str = DEFAULT_BINANCE_WS_BASE_URL,
) -> str:
    """Build the Binance USD-M Futures public market WebSocket URL.

    Parameters: `symbol`, `stream_name`, and `base_url` define the target stream.
    Return value: a public market WebSocket URL.
    Failure scenarios: invalid base URL, symbol, or stream raises validation errors.
    External service access: none.
    Data impact: no Redis writes, MySQL writes, Hermes sends, REST calls, or trading.
    """

    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url.startswith(("ws://", "wss://")):
        raise BinanceValidationError("BINANCE_WS_BASE_URL must start with ws:// or wss://")
    return f"{normalized_base_url}/{build_market_stream_name(symbol, stream_name)}"


class BinanceWebSocketMarketClient:
    """Persistent Binance public market WebSocket client.

    Parameters: `settings` supplies URL and reconnect defaults; `connect_factory`
    can be injected by tests to avoid real Binance access.
    Return value: client instance.
    Failure scenarios: dependency missing, invalid stream config, network failure,
    callback failure, or repeated reconnects raise/log explicit errors.
    External service access: only `connect_and_listen()` opens a public WebSocket.
    Data impact: this class does not write Redis/MySQL, send Hermes, call REST,
    call DeepSeek, generate advice, or perform trading.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        connect_factory: WebSocketConnectFactory | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._connect_factory = connect_factory
        self._logger = get_logger("exchange.binance.websocket_market_client")

    def build_url(
        self,
        *,
        symbol: str | None = None,
        stream_name: str | None = None,
    ) -> str:
        """Build the configured public market WebSocket URL without connecting."""

        return build_market_ws_url(
            symbol=symbol or self._settings.price_monitor_symbol,
            stream_name=stream_name or self._settings.price_monitor_ws_stream,
            base_url=self._settings.binance_ws_base_url,
        )

    async def connect_and_listen(
        self,
        message_handler: RawWebSocketHandler,
        *,
        symbol: str | None = None,
        stream_name: str | None = None,
        reconnect_min_seconds: float | None = None,
        reconnect_max_seconds: float | None = None,
        stop_event: asyncio.Event | None = None,
        max_messages: int | None = None,
    ) -> None:
        """Connect to a public market stream and forward raw messages.

        Parameters: `message_handler` receives each raw WebSocket message;
        optional `stop_event` lets the service shut down cleanly; `max_messages`
        is for tests and controlled checks.
        Return value: none.
        Failure scenarios: missing `websockets` dependency raises
        `BinanceWebSocketError`; transient stream failures are retried with a
        bounded backoff.
        External service access: opens Binance public WebSocket when called.
        Data impact: no Redis/MySQL writes, no Hermes sends, no REST calls.
        """

        url = self.build_url(symbol=symbol, stream_name=stream_name)
        min_delay = _validate_reconnect_delay(
            reconnect_min_seconds
            if reconnect_min_seconds is not None
            else self._settings.price_monitor_ws_reconnect_min_seconds,
            "PRICE_MONITOR_WS_RECONNECT_MIN_SECONDS",
        )
        max_delay = _validate_reconnect_delay(
            reconnect_max_seconds
            if reconnect_max_seconds is not None
            else self._settings.price_monitor_ws_reconnect_max_seconds,
            "PRICE_MONITOR_WS_RECONNECT_MAX_SECONDS",
        )
        if max_delay < min_delay:
            raise BinanceValidationError("WebSocket reconnect max seconds must be >= min seconds")

        connect_factory = self._resolve_connect_factory()
        handled_messages = 0
        reconnect_delay = min_delay

        while stop_event is None or not stop_event.is_set():
            try:
                async with connect_factory(url) as websocket:
                    self._logger.info("Binance public WebSocket connected url=%s", url)
                    reconnect_delay = min_delay
                    async for raw_message in websocket:
                        if stop_event is not None and stop_event.is_set():
                            return
                        await _call_message_handler(message_handler, raw_message)
                        handled_messages += 1
                        if max_messages is not None and handled_messages >= max_messages:
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect is the desired boundary here.
                if stop_event is not None and stop_event.is_set():
                    return
                self._logger.warning(
                    "Binance public WebSocket disconnected url=%s error=%s reconnect_in=%s",
                    url,
                    exc,
                    reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(max_delay, reconnect_delay * 2 if reconnect_delay > 0 else max_delay)

    def _resolve_connect_factory(self) -> WebSocketConnectFactory:
        if self._connect_factory is not None:
            return self._connect_factory
        try:
            websockets_module = importlib.import_module("websockets")
        except ImportError as exc:
            raise BinanceWebSocketError("websockets dependency is required for Binance WebSocket monitoring") from exc
        return websockets_module.connect


async def _call_message_handler(handler: RawWebSocketHandler, raw_message: Any) -> None:
    result = handler(str(raw_message))
    if inspect.isawaitable(result):
        await result


def _validate_reconnect_delay(value: float, name: str) -> float:
    if value < 0:
        raise BinanceValidationError(f"{name} must be greater than or equal to 0")
    return float(value)
