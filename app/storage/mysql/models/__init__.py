"""MySQL ORM model 包边界。

本包属于 `app/storage/mysql` 存储层，负责放置 SQLAlchemy ORM model。
本包不负责执行 migration，不直接发送 Hermes，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不涉及任何交易执行。
04 阶段只新增 `alert_message` 报警记录模型。
"""

