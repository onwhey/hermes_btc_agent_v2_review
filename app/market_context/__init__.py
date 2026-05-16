"""Market context snapshot module.

This package belongs to the stage-15 market fact layer. It builds and persists
BTCUSDT 4h + 1d MarketContextSnapshot records for later strategy stages.
It does not create `app/strategy`, request Binance, call large language models,
generate trading advice, modify formal Kline tables, read account state, or
perform trading.
"""
