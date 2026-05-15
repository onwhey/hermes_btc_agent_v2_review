from __future__ import annotations

from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from types import SimpleNamespace

from app.alerting.service import format_alert_message
from app.alerting.types import AlertFinalDeliveryStatus, AlertGatewayStatus, AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.core.time_utils import UTC
from app.monitoring import runtime_status as runtime_status_module
from app.monitoring.runtime_status import collect_runtime_status
from app.monitoring.runtime_status_rendering import (
    build_runtime_status_alert_event,
    render_runtime_status_console,
)
from app.monitoring.runtime_status_types import RuntimeStatusLevel
from scripts import check_runtime_status

CURRENT_TIME = datetime(2026, 5, 15, 8, 10, tzinfo=UTC)
LATEST_CLOSED_4H = datetime(2026, 5, 15, 4, 0, tzinfo=UTC)


class FakeSystemdChecker:
    def __init__(self, status: str = "active") -> None:
        self.status = status

    def is_active(self, service_name: str) -> str:
        return self.status


class FakeRedis:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_scan: bool = False,
        ttl: int = 30,
        keys: list[str] | None = None,
    ) -> None:
        self.fail = fail
        self.fail_scan = fail_scan
        self.ttl_value = ttl
        self.keys_list = keys or ["bitcoin_price"]

    def ping(self) -> bool:
        if self.fail:
            raise RuntimeError("redis down")
        return True

    def exists(self, key: str) -> int:
        return 1 if key in self.keys_list else 0

    def ttl(self, key: str) -> int:
        return self.ttl_value

    def scan_iter(self, pattern: str):
        if self.fail_scan:
            raise RuntimeError("redis scan down")
        for key in self.keys_list:
            if fnmatch(key, pattern):
                yield key


class FakeMySqlReader:
    def __init__(
        self,
        *,
        latest_open_time: datetime | None = LATEST_CLOSED_4H,
        recent_count: int = 100,
        collector_status: str = "success",
        daily_quality_status: str = "healthy",
        alert_rows: list[SimpleNamespace] | None = None,
        fail: bool = False,
    ) -> None:
        self.latest_open_time = latest_open_time
        self.recent_count = recent_count
        self.collector_status = collector_status
        self.daily_quality_status = daily_quality_status
        self.alert_rows = alert_rows if alert_rows is not None else [_submitted_alert_row()]
        self.fail = fail

    def check_connection(self) -> None:
        if self.fail:
            raise RuntimeError("mysql down")

    def get_latest_kline(self, *, symbol: str, interval_value: str) -> SimpleNamespace | None:
        if self.latest_open_time is None:
            return None
        return SimpleNamespace(open_time_utc=self.latest_open_time)

    def list_recent_klines(self, *, symbol: str, interval_value: str, limit: int) -> list[object]:
        return [object()] * min(self.recent_count, limit)

    def get_latest_collector_event(self, *, symbol: str, interval_value: str, since_utc: datetime) -> SimpleNamespace:
        return SimpleNamespace(status=self.collector_status)

    def get_latest_daily_quality_check(self, *, symbol: str, interval_value: str, since_utc: datetime) -> SimpleNamespace:
        return SimpleNamespace(status=self.daily_quality_status)

    def list_recent_alert_messages(self, *, since_utc: datetime, limit: int) -> list[SimpleNamespace]:
        return self.alert_rows[:limit]


def _submitted_alert_row() -> SimpleNamespace:
    return SimpleNamespace(
        status=AlertSendStatus.SUBMITTED_TO_HERMES.value,
        gateway_status=AlertGatewayStatus.GATEWAY_ACCEPTED.value,
        final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN.value,
        trace_id="trace-ok",
    )


def _normal_report():
    return collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(),
        current_time_utc=CURRENT_TIME,
    )


def test_runtime_status_default_report_is_chinese_and_read_only(capsys) -> None:
    called_alert = False

    def fake_collector(**_: object):
        return _normal_report()

    def fake_alert_sender(*_: object, **__: object) -> AlertSendResult:
        nonlocal called_alert
        called_alert = True
        raise AssertionError("默认模式不应发送 Hermes")

    exit_code = check_runtime_status.main(
        [],
        status_collector=fake_collector,
        alert_sender=fake_alert_sender,
        settings=AppSettings(),
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert called_alert is False
    assert "【Hermes BTC 运行状态检查】" in output
    assert "总体结论：正常" in output
    assert "最新 BTCUSDT 4h K线" in output
    assert "最近 100 根 K线：已读取 100 根，连续性以每日 K线复核为准" in output
    assert "最近 100 根 K线：数量正常" not in output
    assert "本检查只读，不修复、不回补、不写正式 K线表，也不执行自动交易" in output
    assert "Binance" not in output
    assert "DeepSeek" not in output


def test_runtime_status_send_alert_uses_compact_chinese_summary(capsys) -> None:
    report = _normal_report()
    captured: dict[str, str] = {}

    def fake_collector(**_: object):
        return report

    def fake_alert_sender(sent_report, **_: object) -> AlertSendResult:
        event = build_runtime_status_alert_event(sent_report)
        captured["message"] = format_alert_message(event)
        return AlertSendResult(
            status=AlertSendStatus.SUBMITTED_TO_HERMES,
            gateway_status=AlertGatewayStatus.GATEWAY_ACCEPTED,
            final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN,
            attempted_real_send=True,
        )

    exit_code = check_runtime_status.main(
        ["--send-alert"],
        status_collector=fake_collector,
        alert_sender=fake_alert_sender,
        settings=AppSettings(),
    )
    output = capsys.readouterr().out
    message = captured["message"]

    assert exit_code == 0
    assert "运行状态摘要已提交 Hermes" in output
    assert "Hermes 网关已接收" in output
    assert "BTC Agent 无法确认微信最终送达" in output
    assert "微信发送成功" not in output
    assert "微信已送达" not in output
    assert "Hermes BTC 运行状态检查" in message
    assert "总体结论：正常" in message
    assert "最新 BTCUSDT 4h K线" in message
    assert "本次状态摘要已提交 Hermes" not in message
    assert "本摘要将通过 Hermes 通道提交" in message
    assert "最终微信送达状态由 Hermes/微信通道决定，BTC Agent 不直接确认" in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "delivered" not in message
    assert "weixin_success" not in message
    assert "scheduler:running:" not in message
    assert "SELECT" not in message
    assert "channel_response" not in message
    assert "{" not in message


def test_redis_status_reports_scheduler_key_overview_and_legacy_notice() -> None:
    report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(keys=["bitcoin_price", "scheduler:completed:1", "scheduler:job:old"]),
        mysql_reader=FakeMySqlReader(),
        current_time_utc=CURRENT_TIME,
    )
    console = render_runtime_status_console(report)

    assert report.redis.connection_ok is True
    assert report.redis.scheduler_completed_count == 1
    assert report.redis.scheduler_job_legacy_count == 1
    assert report.overall_level == RuntimeStatusLevel.NOTICE
    assert "历史残留" in console


def test_redis_client_create_error_marks_runtime_status_error_and_report_continues(monkeypatch) -> None:
    def fail_create_client(settings: AppSettings) -> object:
        raise RuntimeError("redis config missing")

    monkeypatch.setattr(runtime_status_module, "create_redis_client", fail_create_client)

    report = collect_runtime_status(
        settings=AppSettings(),
        systemd_checker=FakeSystemdChecker(),
        mysql_reader=FakeMySqlReader(),
        current_time_utc=CURRENT_TIME,
    )
    console = render_runtime_status_console(report)

    assert report.redis.connection_ok is False
    assert report.redis.level == RuntimeStatusLevel.ERROR
    assert "redis config missing" in str(report.redis.error_message)
    assert any("Redis 无法初始化或连接失败" in issue.message for issue in report.issues)
    assert "服务状态：" in console
    assert "数据状态：" in console
    assert "告警状态：" in console


def test_redis_ping_error_marks_runtime_status_error() -> None:
    report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(fail=True),
        mysql_reader=FakeMySqlReader(),
        current_time_utc=CURRENT_TIME,
    )

    assert report.redis.connection_ok is False
    assert report.redis.level == RuntimeStatusLevel.ERROR
    assert report.overall_level == RuntimeStatusLevel.ERROR
    assert "Redis：" in render_runtime_status_console(report)


def test_redis_scan_error_marks_runtime_status_error_and_report_continues() -> None:
    report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(fail_scan=True),
        mysql_reader=FakeMySqlReader(),
        current_time_utc=CURRENT_TIME,
    )
    console = render_runtime_status_console(report)

    assert report.redis.connection_ok is False
    assert report.redis.level == RuntimeStatusLevel.ERROR
    assert "redis scan down" in str(report.redis.error_message)
    assert "告警状态：" in console


def test_mysql_latest_kline_and_recent_events_drive_error_levels() -> None:
    stale_report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(latest_open_time=datetime(2026, 5, 15, 0, 0, tzinfo=UTC)),
        current_time_utc=datetime(2026, 5, 15, 12, 10, tzinfo=UTC),
    )
    collector_failed = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(collector_status="failed"),
        current_time_utc=CURRENT_TIME,
    )
    daily_failed = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(daily_quality_status="failed"),
        current_time_utc=CURRENT_TIME,
    )

    assert stale_report.mysql.level == RuntimeStatusLevel.ERROR
    assert collector_failed.mysql.level == RuntimeStatusLevel.ERROR
    assert daily_failed.mysql.level == RuntimeStatusLevel.ERROR


def test_latest_kline_later_than_expected_closed_bar_marks_error() -> None:
    report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(latest_open_time=datetime(2026, 5, 15, 8, 0, tzinfo=UTC)),
        current_time_utc=CURRENT_TIME,
    )

    assert report.mysql.level == RuntimeStatusLevel.ERROR
    assert report.overall_level == RuntimeStatusLevel.ERROR
    assert any("未收盘 K线误写正式表" in issue.message for issue in report.issues)


def test_latest_closed_kline_at_expected_time_remains_normal() -> None:
    report = _normal_report()

    assert report.mysql.latest_kline_open_time_utc == LATEST_CLOSED_4H
    assert report.mysql.level == RuntimeStatusLevel.NORMAL
    assert report.overall_level == RuntimeStatusLevel.NORMAL


def test_alert_status_interprets_submission_semantics_and_flags_legacy_status() -> None:
    normal_report = _normal_report()
    legacy_report = collect_runtime_status(
        systemd_checker=FakeSystemdChecker(),
        redis_client=FakeRedis(),
        mysql_reader=FakeMySqlReader(
            alert_rows=[
                SimpleNamespace(
                    status="sent",
                    gateway_status=AlertGatewayStatus.GATEWAY_ACCEPTED.value,
                    final_delivery_status="weixin_success",
                    trace_id="trace-old",
                )
            ]
        ),
        current_time_utc=CURRENT_TIME,
    )
    console = render_runtime_status_console(normal_report)

    assert normal_report.alert.latest_status == AlertSendStatus.SUBMITTED_TO_HERMES.value
    assert normal_report.alert.latest_gateway_status == AlertGatewayStatus.GATEWAY_ACCEPTED.value
    assert normal_report.alert.latest_final_delivery_status == AlertFinalDeliveryStatus.UNKNOWN.value
    assert "已提交 Hermes" in console
    assert "Hermes 网关已接收" in console
    assert "最终微信送达状态：未知" in console
    assert legacy_report.alert.level == RuntimeStatusLevel.WARNING
    assert legacy_report.alert.legacy_status_count == 1


def test_runtime_status_does_not_import_binance_or_model_clients() -> None:
    source_files = (
        "scripts/check_runtime_status.py",
        "app/monitoring/runtime_status.py",
        "app/monitoring/runtime_status_readers.py",
        "app/monitoring/runtime_status_rendering.py",
    )

    for path in source_files:
        text = Path(path).read_text(encoding="utf-8")
        assert "app.exchange.binance" not in text
        assert "openai" not in text.lower()


def test_runtime_status_implementation_doc_has_clean_test_commands() -> None:
    text = Path("docs/implementation/13_runtime_observability_and_ops.md").read_text(encoding="utf-8")

    assert "S cripts" not in text
    assert "p ython.exe" not in text
    assert ".\\.venv\\Scripts\\python.exe -m pytest tests/test_alerting.py tests/test_runtime_status.py" in text
    assert "python -m pytest tests/test_alerting.py tests/test_runtime_status.py" in text
