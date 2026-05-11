"""核心异常模块。

本文件属于 `app/core` 基础能力层，负责定义项目通用异常基类。
本文件不负责 MySQL、Redis、Binance、Hermes、DeepSeek 或交易执行的具体逻辑。
主要被后续业务模块、脚本入口和测试复用。
"""

from __future__ import annotations


class AppError(Exception):
    """项目异常基类。

    参数：`message` 是面向开发和排查的脱敏错误摘要。
    返回值：异常对象本身。
    失败场景：由调用方在业务或基础能力失败时显式抛出。
    外部服务：本类不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes，不修改质量记录。
    本类不负责自动修复、自动重试或任何交易执行。
    """


class ConfigError(AppError):
    """配置错误。

    参数：`message` 描述缺失、非法或不可转换的配置项，不能包含敏感值。
    返回值：异常对象本身。
    失败场景：配置读取、类型转换或环境校验失败。
    外部服务：本类不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责连接数据库、连接 Redis、请求外部接口或自动交易。
    """


class ValidationError(AppError):
    """通用校验错误。

    参数：`message` 描述校验失败原因，必须保持脱敏。
    返回值：异常对象本身。
    失败场景：基础字段、参数或调用前置条件不满足。
    外部服务：本类不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责 K 线质量检查、策略判断或自动交易。
    """


class ExternalServiceError(AppError):
    """外部服务错误基类。

    参数：`message` 描述外部服务失败摘要，不能包含密钥、token 或 webhook。
    返回值：异常对象本身。
    失败场景：后续外部服务客户端可以用它包装超时、网络错误或响应异常。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责重试、报警、数据库写入或自动交易。
    """


class InfrastructureError(AppError):
    """基础设施错误基类。

    参数：`message` 描述 MySQL、Redis 或迁移基础环境的脱敏失败摘要。
    返回值：异常对象本身。
    失败场景：基础设施依赖缺失、连接初始化失败或健康检查失败。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责业务重试、报警决策、数据修复或自动交易。
    """


class DatabaseError(InfrastructureError):
    """MySQL 基础设施错误。

    参数：`message` 描述 MySQL 配置、engine、session 或健康检查失败原因。
    返回值：异常对象本身。
    失败场景：连接配置不完整、依赖缺失、engine 创建失败或 `SELECT 1` 失败。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责创建业务表、迁移执行、Repository 逻辑或自动交易。
    """


class RedisError(InfrastructureError):
    """Redis 基础设施错误。

    参数：`message` 描述 Redis 配置、client 或健康检查失败原因。
    返回值：异常对象本身。
    失败场景：连接配置不完整、依赖缺失、client 创建失败或 ping 失败。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责 Redis 业务 key、价格监控、冷却逻辑或自动交易。
    """


class AlertingError(ExternalServiceError):
    """报警模块错误基类。

    参数：`message` 描述报警模板、发送或记录失败的脱敏原因。
    返回值：异常对象本身。
    失败场景：报警事件非法、固定模板缺失、Hermes 发送失败或记录更新失败。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责生成交易建议、调用 DeepSeek、修复 K 线数据或自动交易。
    """


class HermesError(AlertingError):
    """Hermes 发送错误。

    参数：`message` 描述 Hermes webhook 调用失败的脱敏摘要。
    返回值：异常对象本身。
    失败场景：Hermes 配置缺失、超时、网络失败或响应异常。
    外部服务：本类本身不访问外部服务。
    数据影响：本类不读写 MySQL，不读写 Redis，不保存 channel_response。
    本类不负责重试策略之外的补偿队列、DeepSeek 内容生成或自动交易。
    """


class KlineError(AppError):
    """Base error for market Kline structure, parsing, validation, and conflicts.

    Parameters: `message` is a sanitized diagnostic summary.
    Return value: exception instance.
    Failure scenarios: raised by market-data parser, validator, or repository helpers.
    External service access: this class does not access Binance or any other service.
    Data impact: this class does not read/write MySQL, read/write Redis, or send alerts.
    This class does not repair Kline data or perform any trading action.
    """


class KlineParseError(KlineError):
    """Raised when a Binance raw Kline row cannot be parsed safely.

    Parameters: `message` identifies the invalid field or shape without sensitive data.
    Return value: exception instance.
    Failure scenarios: short raw rows, invalid timestamps, or invalid Decimal fields.
    External service access: none.
    Data impact: no MySQL writes, Redis writes, alert sends, or trading execution.
    """


class KlineValidationError(KlineError):
    """Raised when a structured Kline DTO violates phase-06 field rules.

    Parameters: `message` names the failed validation rule.
    Return value: exception instance.
    Failure scenarios: invalid OHLC relationship, source mapping, trigger source, or time order.
    External service access: none.
    Data impact: no MySQL writes, Redis writes, alert sends, or trading execution.
    """


class KlineConflictError(KlineError):
    """Raised when an existing formal Kline conflicts with an incoming Kline.

    Parameters: `message` includes the unique key and conflicting field names.
    Return value: exception instance.
    Failure scenarios: repository upsert detects different core fields for the same Kline key.
    External service access: none.
    Data impact: the repository refuses to overwrite existing formal Kline data.
    """

