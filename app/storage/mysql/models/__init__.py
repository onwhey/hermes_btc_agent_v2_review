"""MySQL ORM model package boundary.

This package belongs to `app/storage/mysql`. It groups SQLAlchemy ORM model
modules for Alembic metadata import and repository usage.
It does not execute migrations, send Hermes, read/write Redis, request Binance,
call DeepSeek, or perform trading.

Current model modules include alert messages, formal 4h/1d Kline tables,
quality checks, collector event logs, MarketContextSnapshot, stage-16
strategy signal run/result tables, stage-17 scheduler events, stage-18
strategy aggregation/material-pack tables, stage-23F strategy evidence
aggregation tables, plus stage-19 model review-gate
attempt/result tables, stage-20A model review aggregation output tables,
stage-20B model review chain state tables, stage-21 strategy advice
lifecycle/scheduler tables, stage-22A manual execution feedback tables, and
stage-22B manual execution confirmation-intent tables, plus stage-25A manual
strategy pipeline event logs.
"""
