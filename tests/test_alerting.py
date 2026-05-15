from __future__ import annotations

import json
from pathlib import Path

from app.alerting.hermes_client import HermesClient, HermesTransportResponse, build_hermes_headers
from app.alerting.sanitizer import sanitize_mapping
from app.alerting.service import format_alert_message, send_alert
from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY, supported_alert_type_values
from app.alerting.types import (
    AlertEvent,
    AlertFinalDeliveryStatus,
    AlertGatewayStatus,
    AlertSendResult,
    AlertSendStatus,
    AlertSeverity,
    AlertType,
)
from app.core.config import AppSettings, load_settings
from app.core.time_utils import now_utc
from app.storage.mysql.models.alert_message import AlertMessage
from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository
from scripts.check_alerting import collect_alerting_errors

ROOT = Path(__file__).resolve().parents[1]


def _iter_python_files(*roots: Path) -> list[Path]:
    return [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _build_event(alert_type: AlertType = AlertType.SYSTEM_CHECK) -> AlertEvent:
    return AlertEvent(
        alert_type=alert_type,
        severity=AlertSeverity.WARNING,
        title="alerting test",
        summary="fixed template test",
        details={"component": "test"},
        source="tests.test_alerting",
    )


def test_hermes_settings_are_loaded_and_typed() -> None:
    settings = load_settings(
        env_file=None,
        environ={
            "HERMES_ENABLED": "true",
            "HERMES_DRY_RUN": "false",
            "HERMES_TIMEOUT_SECONDS": "7.5",
            "HERMES_MAX_RETRIES": "3",
        },
    )

    assert settings.hermes_enabled is True
    assert settings.hermes_dry_run is False
    assert settings.hermes_timeout_seconds == 7.5
    assert settings.hermes_max_retries == 3


def test_required_fixed_templates_render_without_external_services() -> None:
    required = {
        AlertType.SYSTEM_CHECK.value,
        AlertType.INFRA_ERROR.value,
        AlertType.DATA_QUALITY_ERROR.value,
        AlertType.COLLECTOR_ERROR.value,
        AlertType.PRICE_MONITOR_ERROR.value,
    }

    assert required.issubset(set(supported_alert_type_values()))
    for alert_type_value in required:
        message = format_alert_message(_build_event(AlertType(alert_type_value)))
        assert "不是交易建议" in message
        assert "fixed template test" in message


def test_kline_related_templates_state_no_auto_repair_or_manual_data_change() -> None:
    for alert_type in (
        AlertType.DATA_QUALITY_ERROR,
        AlertType.COLLECTOR_ERROR,
        AlertType.KLINE_DATA_QUALITY_ERROR,
        AlertType.KLINE_INTEGRITY_CHECK_FAILED,
    ):
        message = format_alert_message(_build_event(alert_type))

        assert "没有自动修复数据" in message
        assert "没有人工改数" in message
        assert "没有自动回补" in message
        assert "没有执行自动交易" in message


def test_non_kline_visible_body_does_not_append_kline_boundary_statement() -> None:
    event = AlertEvent(
        alert_type=AlertType.SYSTEM_CHECK,
        severity=AlertSeverity.INFO,
        title="系统检查通过",
        summary="system ok",
        details={WECHAT_VISIBLE_BODY_DETAIL_KEY: "系统检查可见正文。"},
        source="tests.test_alerting",
    )

    message = format_alert_message(event)

    assert "系统检查可见正文" in message
    assert "本提醒不是交易建议" in message
    assert "没有自动修复" not in message
    assert "没有人工改数" not in message
    assert "没有自动回补" not in message
    assert "没有执行自动交易" not in message


def test_sanitizer_redacts_sensitive_mapping_values() -> None:
    sanitized = sanitize_mapping(
        {
            "Authorization": "Bearer raw-token",
            "nested": {
                "password": "mysql-secret",
                "body": "password=abc webhook=https://example.invalid/hook",
            },
            "items": ["secret=value", "safe"],
        },
        extra_sensitive_values=("https://example.invalid/hook",),
    )

    rendered = str(sanitized)
    assert "raw-token" not in rendered
    assert "mysql-secret" not in rendered
    assert "abc" not in rendered
    assert "https://example.invalid/hook" not in rendered
    assert "***REDACTED***" in rendered


def test_hermes_disabled_skips_without_transport_call() -> None:
    called = False

    def fake_post(*_: object) -> HermesTransportResponse:
        nonlocal called
        called = True
        return HermesTransportResponse(status_code=200)

    settings = AppSettings(
        hermes_enabled=False,
        hermes_dry_run=False,
        hermes_webhook_url="https://example.invalid/hook",
    )
    client = HermesClient(settings, http_post=fake_post)

    result = client.send_alert_message(_build_event(), "message", send_real_alert=True)

    assert result.status == AlertSendStatus.SKIPPED
    assert called is False


def test_hermes_dry_run_skips_without_transport_call() -> None:
    called = False

    def fake_post(*_: object) -> HermesTransportResponse:
        nonlocal called
        called = True
        return HermesTransportResponse(status_code=200)

    settings = AppSettings(
        hermes_enabled=True,
        hermes_dry_run=True,
        hermes_webhook_url="https://example.invalid/hook",
    )
    client = HermesClient(settings, http_post=fake_post)

    result = client.send_alert_message(_build_event(), "message", send_real_alert=True)

    assert result.status == AlertSendStatus.SKIPPED
    assert called is False


def test_hermes_client_success_is_mocked_and_channel_response_is_sanitized() -> None:
    settings = AppSettings(
        hermes_enabled=True,
        hermes_dry_run=False,
        hermes_webhook_url="https://example.invalid/hook",
        hermes_secret="hermes-secret",
        hermes_max_retries=0,
    )
    captured: dict[str, object] = {}

    def fake_post(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HermesTransportResponse:
        captured["url"] = url
        captured["payload"] = json.loads(body.decode("utf-8"))
        captured["headers"] = headers
        captured["timeout"] = timeout
        return HermesTransportResponse(
            status_code=200,
            body='{"ok":true,"secret":"hermes-secret","webhook":"https://example.invalid/hook"}',
            headers={"X-Webhook-Signature": "raw-signature"},
        )

    client = HermesClient(settings, http_post=fake_post)
    log_messages: list[str] = []

    class FakeLogger:
        def info(self, message: str, *args: object) -> None:
            log_messages.append(message % args)

        def warning(self, message: str, *args: object) -> None:
            log_messages.append(message % args)

    client._logger = FakeLogger()  # type: ignore[assignment]

    result = client.send_alert_message(_build_event(), "message", send_real_alert=True)

    assert result.status == AlertSendStatus.SUBMITTED_TO_HERMES
    assert result.gateway_status == AlertGatewayStatus.GATEWAY_ACCEPTED
    assert result.final_delivery_status == AlertFinalDeliveryStatus.UNKNOWN
    assert result.channel_response["gateway_status"] == AlertGatewayStatus.GATEWAY_ACCEPTED.value
    assert result.channel_response["final_delivery_status"] == AlertFinalDeliveryStatus.UNKNOWN.value
    assert result.attempted_real_send is True
    assert captured["url"] == settings.hermes_webhook_url
    assert captured["payload"]["not_trading_advice"] is True
    assert "X-Webhook-Signature" in captured["headers"]
    assert any("final_delivery_status=unknown" in message for message in log_messages)
    assert "微信发送成功" not in " ".join(log_messages)

    rendered_response = str(result.channel_response)
    assert "weixin_success" not in rendered_response
    assert "hermes-secret" not in rendered_response
    assert "https://example.invalid/hook" not in rendered_response
    assert "raw-signature" not in rendered_response
    assert "***REDACTED***" in rendered_response


def test_hermes_payload_redacts_sensitive_values_before_send() -> None:
    settings = AppSettings(
        hermes_enabled=True,
        hermes_dry_run=False,
        hermes_webhook_url="https://example.invalid/hook",
        hermes_secret="hermes-secret",
        hermes_max_retries=0,
    )
    captured: dict[str, object] = {}

    def fake_post(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HermesTransportResponse:
        captured["payload_text"] = body.decode("utf-8")
        captured["payload"] = json.loads(body.decode("utf-8"))
        return HermesTransportResponse(status_code=200, body="{}")

    event = AlertEvent(
        alert_type=AlertType.SYSTEM_CHECK,
        severity=AlertSeverity.WARNING,
        title=(
            "title hermes-secret https://example.invalid/hook "
            "password=abc webhook=https://example.invalid/hook"
        ),
        summary="summary",
        details={},
        source="tests.test_alerting token=xxx",
        trace_id="trace-hermes-secret",
    )
    client = HermesClient(settings, http_post=fake_post)

    result = client.send_alert_message(
        event,
        (
            "message hermes-secret https://example.invalid/hook "
            "password=abc webhook=https://example.invalid/hook token=xxx"
        ),
        send_real_alert=True,
    )

    payload_text = str(captured["payload_text"])

    assert result.status == AlertSendStatus.SUBMITTED_TO_HERMES
    assert result.final_delivery_status == AlertFinalDeliveryStatus.UNKNOWN
    assert "hermes-secret" not in payload_text
    assert "https://example.invalid/hook" not in payload_text
    assert "password=abc" not in payload_text
    assert "webhook=https://example.invalid/hook" not in payload_text
    assert "token=xxx" not in payload_text
    assert "***REDACTED***" in payload_text


def test_hermes_client_failure_returns_failed_without_real_network() -> None:
    settings = AppSettings(
        hermes_enabled=True,
        hermes_dry_run=False,
        hermes_webhook_url="https://example.invalid/hook",
        hermes_secret="hermes-secret",
        hermes_max_retries=0,
    )

    def fake_post(*_: object) -> HermesTransportResponse:
        return HermesTransportResponse(
            status_code=500,
            body="secret=hermes-secret webhook=https://example.invalid/hook failed",
            headers={"Authorization": "raw-auth"},
        )

    client = HermesClient(settings, http_post=fake_post)

    result = client.send_alert_message(_build_event(), "message", send_real_alert=True)

    assert result.status == AlertSendStatus.GATEWAY_REJECTED
    assert result.gateway_status == AlertGatewayStatus.GATEWAY_REJECTED
    assert result.final_delivery_status == AlertFinalDeliveryStatus.UNKNOWN
    assert result.http_status_code == 500
    assert "HTTP 500" in result.error_message
    rendered_result = str(result)
    assert "hermes-secret" not in rendered_result
    assert "https://example.invalid/hook" not in rendered_result
    assert "raw-auth" not in rendered_result


def test_hermes_client_connection_exception_returns_submit_failed_without_success() -> None:
    settings = AppSettings(
        hermes_enabled=True,
        hermes_dry_run=False,
        hermes_webhook_url="https://example.invalid/hook",
        hermes_secret="hermes-secret",
        hermes_max_retries=0,
    )

    def fake_post(*_: object) -> HermesTransportResponse:
        raise TimeoutError("timeout secret=hermes-secret webhook=https://example.invalid/hook")

    client = HermesClient(settings, http_post=fake_post)

    result = client.send_alert_message(_build_event(), "message", send_real_alert=True)

    assert result.status == AlertSendStatus.SUBMIT_FAILED
    assert result.gateway_status == AlertGatewayStatus.SUBMIT_FAILED
    assert result.final_delivery_status == AlertFinalDeliveryStatus.UNKNOWN
    assert result.http_status_code is None
    assert "hermes-secret" not in result.error_message
    assert "https://example.invalid/hook" not in result.error_message
    assert result.status != AlertSendStatus.SUBMITTED_TO_HERMES


def test_hmac_header_does_not_contain_plain_secret() -> None:
    settings = AppSettings(hermes_secret="hermes-secret")
    headers = build_hermes_headers(b'{"message":"hello"}', settings)

    assert "X-Webhook-Signature" in headers
    assert "hermes-secret" not in headers["X-Webhook-Signature"]


def test_service_can_use_mock_repository_without_real_mysql() -> None:
    class FakeClient:
        def send_alert_message(
            self,
            event: AlertEvent,
            message: str,
            *,
            send_real_alert: bool = False,
        ) -> AlertSendResult:
            assert "不是交易建议" in message
            assert send_real_alert is False
            return AlertSendResult(
                status=AlertSendStatus.SUBMITTED_TO_HERMES,
                gateway_status=AlertGatewayStatus.GATEWAY_ACCEPTED,
                final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN,
                message="mocked",
                submitted_at_utc=now_utc(),
            )

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.flush_count = 0

        def add(self, record: object) -> None:
            self.added.append(record)

        def flush(self) -> None:
            self.flush_count += 1

    fake_session = FakeSession()
    repository = AlertMessageRepository()

    result = send_alert(
        _build_event(),
        settings=AppSettings(),
        client=FakeClient(),  # type: ignore[arg-type]
        repository=repository,
        db_session=fake_session,
    )

    assert result.status == AlertSendStatus.SUBMITTED_TO_HERMES
    assert len(fake_session.added) == 1
    assert fake_session.flush_count == 2
    assert fake_session.added[0].status == AlertSendStatus.SUBMITTED_TO_HERMES.value
    assert fake_session.added[0].sent_at_utc == result.submitted_at_utc


def test_alert_message_model_and_migration_are_scoped_to_alert_table() -> None:
    assert AlertMessage is not None
    migration_text = (
        ROOT / "migrations" / "versions" / "20260511_04_create_alert_message.py"
    ).read_text(encoding="utf-8")

    assert '"alert_message"' in migration_text
    assert "market_kline_4h" not in migration_text
    assert "collector_event_log" not in migration_text
    assert "data_quality_check" not in migration_text


def test_check_alerting_dry_run_does_not_send_real_hermes() -> None:
    assert collect_alerting_errors(settings=AppSettings(), send_real_alert=False) == []


def test_check_alerting_rejects_real_send_when_config_is_not_explicit() -> None:
    class FakeClient:
        def send_alert_message(self, *_: object, **__: object) -> AlertSendResult:
            raise AssertionError("client must not be called when real send settings are invalid")

    errors = collect_alerting_errors(
        settings=AppSettings(hermes_enabled=False, hermes_dry_run=True),
        send_real_alert=True,
        client=FakeClient(),  # type: ignore[arg-type]
    )

    assert errors


def test_alerting_code_has_no_legacy_sent_or_failed_status_calls() -> None:
    forbidden_status_calls = [
        ("AlertSendStatus", "SENT"),
        ("AlertSendStatus", "FAILED"),
    ]

    for path in _iter_python_files(ROOT / "app", ROOT / "scripts", ROOT / "tests"):
        text = path.read_text(encoding="utf-8")
        for enum_name, member_name in forbidden_status_calls:
            assert f"{enum_name}.{member_name}" not in text, str(path.relative_to(ROOT))


def test_user_visible_alert_submission_text_uses_submission_language() -> None:
    forbidden_phrases = (
        "alert " + "delivery " + "failed",
        "delivery " + "failed",
        "alert" + "_delivery",
        "delivery" + "_failure",
    )

    for path in _iter_python_files(ROOT / "app", ROOT / "scripts"):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden_phrases:
            assert phrase not in text, str(path.relative_to(ROOT))
