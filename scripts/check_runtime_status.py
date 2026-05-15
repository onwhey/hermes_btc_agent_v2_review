"""Hermes BTC Agent 运行状态人工检查入口。

触发方式：用户手动执行 `python -m scripts.check_runtime_status`。
是否允许用户手动执行：允许。
是否允许 scheduler 调用：不允许；本脚本没有 scheduler job，也不得由 scheduler 配置引用。
必须参数：无；默认只输出控制台报告。`--send-alert` 必须由用户显式传入。
调用 service：`app/monitoring/runtime_status.py::collect_runtime_status`。
不负责业务逻辑：不请求 Binance，不采集 K线，不回补，不复核，不修复数据。
数据库影响：默认只读；`--send-alert` 只允许通过现有告警链路写 `alert_message` 发送记录。
Redis 影响：只读，不新增、不删除、不修改任何 key。
Hermes 影响：默认不发送；`--send-alert` 只发送精简运行状态摘要。
数据修复与交易边界：不允许自动修复、不允许人工改数、不允许自动回补、不允许自动交易。
"""

from __future__ import annotations

import argparse
from typing import Callable

from app.alerting.status_text import (
    alert_send_status_label,
    final_delivery_status_label,
    gateway_status_label,
)
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings, load_settings
from app.core.logger import configure_logging, get_logger
from app.monitoring.runtime_status import collect_runtime_status
from app.monitoring.runtime_status_rendering import render_runtime_status_console, send_runtime_status_alert
from app.monitoring.runtime_status_types import RuntimeStatusReport
from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository
from app.storage.mysql.session import session_scope

StatusCollector = Callable[..., RuntimeStatusReport]
AlertSender = Callable[..., AlertSendResult]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读检查 Hermes BTC Agent 当前运行状态。")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="查询最近运行事件、告警记录和质量检查的小时数，默认 24。",
    )
    parser.add_argument(
        "--send-alert",
        action="store_true",
        help="通过现有 Hermes 告警链路发送一条中文运行状态摘要。",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    status_collector: StatusCollector = collect_runtime_status,
    alert_sender: AlertSender = send_runtime_status_alert,
    settings: AppSettings | None = None,
) -> int:
    """执行人工运行状态检查。

    参数：`argv` 是 CLI 参数；`status_collector` 和 `alert_sender` 供测试注入；
    `settings` 可注入配置。
    返回值：进程退出码，0 表示检查脚本完成；`--send-alert` 提交失败返回 1。
    失败场景：依赖读取异常由 app 层汇总进报告；真实 Hermes 提交失败体现在返回码。
    外部服务：默认不访问 Hermes；显式 `--send-alert` 才访问 Hermes。
    数据影响：默认只读；显式发送时只允许写 `alert_message` 发送记录。
    本函数不触发采集、回补、复核、修复、DeepSeek 或交易执行。
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    active_settings = settings or load_settings()
    configure_logging(active_settings, enable_file=False)
    logger = get_logger("scripts.check_runtime_status")

    report = status_collector(settings=active_settings, lookback_hours=max(1, args.lookback_hours))
    print(render_runtime_status_console(report))

    if not args.send_alert:
        return 0

    result = _send_runtime_status_summary(
        report=report,
        settings=active_settings,
        alert_sender=alert_sender,
        logger=logger,
    )
    _print_send_alert_result(result, report.trace_id)
    return 0 if result.status is AlertSendStatus.SUBMITTED_TO_HERMES else 1


def _send_runtime_status_summary(
    *,
    report: RuntimeStatusReport,
    settings: AppSettings,
    alert_sender: AlertSender,
    logger: object,
) -> AlertSendResult:
    """发送运行状态摘要，并优先记录到 `alert_message`。"""

    if alert_sender is not send_runtime_status_alert:
        return alert_sender(report, settings=settings)

    result: AlertSendResult | None = None
    try:
        with session_scope(settings=settings, commit_on_success=False) as db_session:
            result = alert_sender(
                report,
                settings=settings,
                alert_repository=AlertMessageRepository(),
                db_session=db_session,
            )
            if hasattr(db_session, "commit"):
                db_session.commit()
            return result
    except Exception as exc:  # noqa: BLE001 - MySQL 不可用时仍允许测试 Hermes 提交通道。
        if result is not None:
            if hasattr(logger, "warning"):
                logger.warning("运行状态摘要已提交，alert_message 提交事务失败：%s", exc)
            return result
        if hasattr(logger, "warning"):
            logger.warning("运行状态摘要记录 alert_message 失败，改为仅提交 Hermes：%s", exc)
        return alert_sender(report, settings=settings)


def _print_send_alert_result(result: AlertSendResult, trace_id: str) -> None:
    if result.status is AlertSendStatus.SUBMITTED_TO_HERMES:
        print("运行状态摘要已提交 Hermes。")
    else:
        print(f"运行状态摘要提交结果：{alert_send_status_label(result.status)}。")
    print(f"网关状态：{gateway_status_label(result.gateway_status)}。")
    print(f"最终微信送达状态：{final_delivery_status_label(result.final_delivery_status)}。")
    print(f"追踪ID：{trace_id}")


if __name__ == "__main__":
    raise SystemExit(main())
