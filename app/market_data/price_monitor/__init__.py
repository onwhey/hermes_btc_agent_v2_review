"""10s WebSocket price monitor package.

This package belongs to `app/market_data`.
It receives Binance public WebSocket price events, checks price movement on a
configured cadence, writes short-lived Redis state, and delegates fixed-template
alerts to `app/alerting`. It does not write formal Kline tables, request REST
latest prices, call DeepSeek, generate advice, or perform trading.
"""

