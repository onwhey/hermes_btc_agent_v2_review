"""Scheduler job package.

This package belongs to `app/scheduler`.
It contains thin job entry points that call app services directly. It does not
request Binance, write business tables, send Hermes, read/write Redis, call
DeepSeek, or perform trading execution by itself.
"""
