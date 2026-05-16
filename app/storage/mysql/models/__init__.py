"""MySQL ORM model 包边界。

本包属于 `app/storage/mysql` 存储层，负责放置 SQLAlchemy ORM model。
本包不负责执行 migration，不直接发送 Hermes，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不涉及任何交易执行。
已包含 `alert_message`、`market_kline_4h`、`market_kline_1d`、
`data_quality_check`、`collector_event_log`、`market_context_snapshot`
等数据底座和事实快照模型。
"""
