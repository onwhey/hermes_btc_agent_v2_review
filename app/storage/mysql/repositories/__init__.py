"""MySQL repository 包边界。

本包属于 `app/storage/mysql` 存储层，负责放置可复用 Repository。
Repository 只处理数据库读写，不直接请求 Binance，不直接发送 Hermes，
不读写 Redis，不调用 DeepSeek，不涉及任何交易执行。
04 阶段只新增 `alert_message` 报警记录 Repository。
"""

