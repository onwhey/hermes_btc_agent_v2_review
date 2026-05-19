"""MySQL ORM model package boundary.

This package belongs to `app/storage/mysql`. It groups SQLAlchemy ORM model
modules for Alembic metadata import and repository usage.
It does not execute migrations, send Hermes, read/write Redis, request Binance,
call DeepSeek, or perform trading.

Current model modules include alert messages, formal 4h/1d Kline tables,
quality checks, collector event logs, MarketContextSnapshot, stage-16
strategy signal run/result tables, stage-17 scheduler events, and stage-18
strategy aggregation/material-pack tables, plus stage-19 model review-gate
attempt/result tables.
"""
