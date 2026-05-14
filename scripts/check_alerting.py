"""Hermes 报警模块人工检查入口。

触发方式：用户手动执行 `python -m scripts.check_alerting --dry-run`。
是否允许用户手动执行：允许。
是否允许 scheduler 调用：本阶段未提供 scheduler job，也不应被 scheduler 配置引用。
必须参数：无；默认 dry-run。真实发送必须显式传入 `--send-real-alert` 或 `--send-test`。
调用 service：`app/alerting/service.py::send_test_alert`。
不负责业务逻辑：不请求 Binance，不采集 K 线，不写正式 K 线表，不读写 Redis。
数据库影响：默认不写 MySQL；本脚本不创建业务表，不执行 migration。
Hermes 影响：默认不发送 Hermes；真实发送只在用户显式传参且配置允许时发生。
数据修复与交易边界：不允许自动修复数据，不允许自动交易。
"""

from __future__ import annotations

import argparse

from app.alerting.hermes_client import HermesClient
from app.alerting.service import format_alert_message, send_test_alert
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, load_settings
from app.core.logger import configure_logging, get_logger

REQUIRED_TEMPLATE_TYPES = (
    AlertType.SYSTEM_CHECK,
    AlertType.INFRA_ERROR,
    AlertType.DATA_QUALITY_ERROR,
    AlertType.COLLECTOR_ERROR,
    AlertType.PRICE_MONITOR_ERROR,
)


def _check_required_templates() -> list[str]:
    errors: list[str] = []
    for alert_type in REQUIRED_TEMPLATE_TYPES:
        try:
            message = format_alert_message(
                AlertEvent(
                    alert_type=alert_type,
                    severity=AlertSeverity.WARNING,
                    title=f"{alert_type.value} dry-run",
                    summary="固定模板渲染检查。",
                    details={"check": "template"},
                    source="scripts.check_alerting",
                )
            )
            if "不是交易建议" not in message:
                errors.append(f"{alert_type.value} 模板缺少非交易建议声明")
        except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误后统一返回。
            errors.append(f"{alert_type.value} 模板渲染失败：{exc}")
    return errors


def _validate_real_send_settings(settings: AppSettings) -> list[str]:
    errors: list[str] = []
    if not settings.hermes_enabled:
        errors.append("真实发送被拒绝：HERMES_ENABLED 必须为 true")
    if settings.hermes_dry_run:
        errors.append("真实发送被拒绝：HERMES_DRY_RUN 必须为 false")
    if not settings.hermes_webhook_url:
        errors.append("真实发送被拒绝：HERMES_WEBHOOK_URL 未配置")
    return errors


def collect_alerting_errors(
    *,
    settings: AppSettings | None = None,
    send_real_alert: bool = False,
    client: HermesClient | None = None,
) -> list[str]:
    """收集报警模块检查错误。

    参数：`settings` 是可注入配置；`send_real_alert` 显式允许真实 Hermes；
    `client` 可注入 mock，默认由 service 构造。
    返回值：错误字符串列表，空列表表示通过。
    失败场景：模板渲染失败、配置不允许真实发送或 Hermes 返回失败。
    外部服务：默认不访问外部服务；只有显式真实发送且配置允许才访问 Hermes。
    数据影响：不写 MySQL，不读写 Redis，不执行 migration，不发送 DeepSeek。
    本函数不涉及 scheduler、正式 K 线写入或自动交易。
    """

    active_settings = settings or load_settings()
    configure_logging(active_settings, enable_file=False)
    logger = get_logger("scripts.check_alerting")

    errors = _check_required_templates()
    if errors:
        return errors

    if send_real_alert:
        errors.extend(_validate_real_send_settings(active_settings))
        if errors:
            return errors
        logger.warning("用户手动显式触发真实 Hermes 测试报警。")

    result = send_test_alert(
        settings=active_settings,
        client=client,
        send_real_alert=send_real_alert,
    )

    if send_real_alert and result.status != AlertSendStatus.SUBMITTED_TO_HERMES:
        errors.append(f"真实 Hermes 测试报警失败：{result.error_message or result.message}")
    if not send_real_alert and result.attempted_real_send:
        errors.append("dry-run 模式不应尝试真实 Hermes 发送")
    return errors


def build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    参数：无。
    返回值：`argparse.ArgumentParser`。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责执行检查或自动交易。
    """

    parser = argparse.ArgumentParser(description="检查 Hermes 固定模板报警模块。")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="默认模式，只渲染模板并验证发送边界，不真实发送 Hermes。",
    )
    mode_group.add_argument(
        "--send-real-alert",
        "--send-test",
        dest="send_real_alert",
        action="store_true",
        help="用户手动显式触发真实 Hermes 测试报警。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    参数：`argv` 是可选命令行参数列表。
    返回值：检查通过返回 0，否则返回 1。
    失败场景：配置、模板或 Hermes 真实发送检查失败时返回 1。
    外部服务：默认不访问外部服务；只有显式真实发送且配置允许才访问 Hermes。
    数据影响：不写 MySQL，不读写 Redis，不发送 DeepSeek。
    本入口不允许 scheduler 调用，不执行 migration，不自动交易。
    """

    args = build_arg_parser().parse_args(argv)
    errors = collect_alerting_errors(send_real_alert=args.send_real_alert)

    if errors:
        print("Hermes 报警模块检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    mode = "real-send" if args.send_real_alert else "dry-run"
    print(f"Hermes 报警模块检查通过（{mode}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
