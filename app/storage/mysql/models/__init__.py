"""MySQL ORM model package boundary.

This package belongs to `app/storage/mysql`. It groups SQLAlchemy ORM model
modules for Alembic metadata import and repository usage.
It does not execute migrations, send Hermes, read/write Redis, request Binance,
call DeepSeek, or perform trading.

Current model modules include alert messages, formal 4h/1d Kline tables,
quality checks, collector event logs, MarketContextSnapshot, and stage-16
strategy signal run/result tables.
"""
