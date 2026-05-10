"""MySQL 存储基础设施子包。

本包在 03 阶段承载 engine、session、declarative base 和健康检查基础能力。
导入本包不连接 MySQL，不创建表，不写入数据，不发送 Hermes，不实现交易执行。
"""

