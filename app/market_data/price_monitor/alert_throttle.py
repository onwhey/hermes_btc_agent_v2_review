"""In-memory alert cooldown for the 10s price monitor.

This file belongs to `app/market_data/price_monitor`.
It tracks short-lived in-process cooldowns for price monitor alerts. It does
not write Redis, write MySQL, send Hermes, call DeepSeek, request Binance,
generate advice, or perform trading.
"""

from __future__ import annotations

from datetime import datetime

from app.core.time_utils import now_utc


class InMemoryAlertThrottle:
    """Simple per-process alert cooldown.

    Parameters: `cooldown_seconds` defines the minimum seconds between alerts
    for the same `symbol + alert_type`.
    Return value: throttle instance.
    Failure scenarios: none expected; negative cooldown behaves like zero.
    External service access: none.
    Data impact: no Redis/MySQL writes, Hermes sends, DeepSeek, or trading.
    """

    def __init__(self, cooldown_seconds: int = 60) -> None:
        self._cooldown_seconds = max(0, int(cooldown_seconds))
        self._last_sent_at: dict[tuple[str, str], datetime] = {}

    def should_send_alert(
        self,
        *,
        symbol: str,
        alert_type: str,
        now: datetime | None = None,
    ) -> bool:
        """Return True once per cooldown window for `symbol + alert_type`.

        Parameters: `symbol` and `alert_type` define the cooldown key; `now`
        can be injected by tests.
        Return value: True when caller should send and the timestamp is recorded.
        Failure scenarios: none expected.
        External service access: none.
        Data impact: no Redis writes; cooldown never blocks Redis price updates.
        """

        active_now = now or now_utc()
        key = (symbol.strip().upper(), alert_type.strip())
        previous = self._last_sent_at.get(key)
        if previous is None:
            self._last_sent_at[key] = active_now
            return True
        elapsed = (active_now - previous).total_seconds()
        if elapsed >= self._cooldown_seconds:
            self._last_sent_at[key] = active_now
            return True
        return False

