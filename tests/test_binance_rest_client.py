from __future__ import annotations

import inspect
import urllib.error
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.core.config import AppSettings, load_settings
from app.exchange.binance import rest_client
from app.exchange.binance.constants import (
    ALLOWED_PUBLIC_REST_PATHS,
    FUTURES_EXCHANGE_INFO_PATH,
    FUTURES_KLINES_PATH,
    FUTURES_PING_PATH,
    FUTURES_SERVER_TIME_PATH,
)
from app.exchange.binance.exceptions import (
    BinanceHTTPError,
    BinanceRateLimitError,
    BinanceResponseError,
    BinanceTimeoutError,
    BinanceValidationError,
)
from app.exchange.binance.rest_client import BinanceRestClient, build_kline_params
from app.exchange.binance.types import BinanceHttpResponse
from scripts import check_binance_rest
from scripts.check_binance_rest import collect_binance_rest_errors


class FakeHttpGet:
    def __init__(self, responses: list[BinanceHttpResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, object], float]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, object],
        timeout_seconds: float,
    ) -> BinanceHttpResponse:
        self.calls.append((url, dict(params), timeout_seconds))
        if not self._responses:
            raise AssertionError("FakeHttpGet received an unexpected call")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def build_settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "app_env": "test",
        "binance_base_url": "https://fapi.binance.com",
        "binance_timeout_seconds": 1.5,
        "binance_max_retries": 0,
        "binance_retry_backoff_seconds": 0.0,
        "binance_default_symbol": "BTCUSDT",
        "binance_default_interval": "4h",
        "binance_kline_default_limit": 10,
        "binance_kline_max_limit": 1500,
    }
    values.update(overrides)
    return AppSettings(**values)


def test_binance_settings_are_loaded_from_core_config() -> None:
    settings = load_settings(
        env_file=None,
        environ={
            "APP_ENV": "test",
            "BINANCE_BASE_URL": "https://example.invalid",
            "BINANCE_TIMEOUT_SECONDS": "3.5",
            "BINANCE_MAX_RETRIES": "4",
            "BINANCE_RETRY_BACKOFF_SECONDS": "0.25",
            "BINANCE_DEFAULT_SYMBOL": "ethusdt",
            "BINANCE_DEFAULT_INTERVAL": "1h",
            "BINANCE_KLINE_DEFAULT_LIMIT": "12",
            "BINANCE_KLINE_MAX_LIMIT": "99",
        },
    )

    assert settings.binance_base_url == "https://example.invalid"
    assert settings.binance_timeout_seconds == 3.5
    assert settings.binance_max_retries == 4
    assert settings.binance_retry_backoff_seconds == 0.25
    assert settings.binance_default_symbol == "ethusdt"
    assert settings.binance_default_interval == "1h"
    assert settings.binance_kline_default_limit == 12
    assert settings.binance_kline_max_limit == 99


def test_ping_success_uses_configured_timeout_and_public_path() -> None:
    http_get = FakeHttpGet([BinanceHttpResponse(status_code=200, body="{}")])
    client = BinanceRestClient(settings=build_settings(binance_timeout_seconds=2.25), http_get=http_get)

    assert client.ping() is True

    url, params, timeout_seconds = http_get.calls[0]
    assert url == "https://fapi.binance.com/fapi/v1/ping"
    assert params == {}
    assert timeout_seconds == 2.25


def test_ping_failure_raises_http_error() -> None:
    http_get = FakeHttpGet([BinanceHttpResponse(status_code=500, body='{"code":-1000,"msg":"error"}')])
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    with pytest.raises(BinanceHTTPError):
        client.ping()


def test_server_time_is_parsed_as_utc_datetime() -> None:
    http_get = FakeHttpGet([BinanceHttpResponse(status_code=200, body='{"serverTime":1700000000000}')])
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    server_time = client.get_server_time()

    assert server_time.server_time_ms == 1700000000000
    assert server_time.server_time_utc.isoformat() == "2023-11-14T22:13:20+00:00"
    assert http_get.calls[0][0].endswith(FUTURES_SERVER_TIME_PATH)


def test_exchange_info_returns_public_metadata() -> None:
    http_get = FakeHttpGet(
        [BinanceHttpResponse(status_code=200, body='{"symbols":[{"symbol":"BTCUSDT"}]}')]
    )
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    assert client.get_exchange_info() == {"symbols": [{"symbol": "BTCUSDT"}]}
    assert http_get.calls[0][0].endswith(FUTURES_EXCHANGE_INFO_PATH)


def test_get_klines_builds_public_kline_params_without_parsing_or_writing() -> None:
    http_get = FakeHttpGet([BinanceHttpResponse(status_code=200, body="[[1700000000000,\"1\"]]")])
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    rows = client.get_klines(
        symbol="btcusdt",
        interval="4h",
        limit=2,
        start_time_ms=1700000000000,
        end_time_ms=1700014400000,
    )

    assert rows == [[1700000000000, "1"]]
    url, params, _timeout = http_get.calls[0]
    assert url.endswith(FUTURES_KLINES_PATH)
    assert params == {
        "symbol": "BTCUSDT",
        "interval": "4h",
        "limit": 2,
        "startTime": 1700000000000,
        "endTime": 1700014400000,
    }


def test_build_kline_params_rejects_invalid_limits_and_time_ranges() -> None:
    with pytest.raises(BinanceValidationError):
        build_kline_params(symbol="BTCUSDT", interval="4h", limit=1501, max_limit=1500)
    with pytest.raises(BinanceValidationError):
        build_kline_params(
            symbol="BTCUSDT",
            interval="4h",
            limit=1,
            max_limit=1500,
            start_time_ms=2,
            end_time_ms=1,
        )


def test_timeout_error_is_explicit() -> None:
    timeout = urllib.error.URLError(TimeoutError("slow"))
    http_get = FakeHttpGet([timeout])
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    with pytest.raises(BinanceTimeoutError):
        client.get_server_time()


def test_rate_limit_error_is_explicit() -> None:
    http_get = FakeHttpGet([BinanceHttpResponse(status_code=429, body='{"code":-1003,"msg":"limited"}')])
    client = BinanceRestClient(settings=build_settings(), http_get=http_get)

    with pytest.raises(BinanceRateLimitError):
        client.ping()


def test_bounded_retries_are_used_for_server_errors() -> None:
    http_get = FakeHttpGet(
        [
            BinanceHttpResponse(status_code=500, body='{"code":-1000,"msg":"error"}'),
            BinanceHttpResponse(status_code=200, body="{}"),
        ]
    )
    client = BinanceRestClient(
        settings=build_settings(binance_max_retries=1, binance_retry_backoff_seconds=0),
        http_get=http_get,
    )

    assert client.ping() is True
    assert len(http_get.calls) == 2


def test_invalid_json_and_binance_error_body_are_rejected() -> None:
    invalid_json_client = BinanceRestClient(
        settings=build_settings(),
        http_get=FakeHttpGet([BinanceHttpResponse(status_code=200, body="not json")]),
    )
    with pytest.raises(BinanceResponseError):
        invalid_json_client.ping()

    error_body_client = BinanceRestClient(
        settings=build_settings(),
        http_get=FakeHttpGet([BinanceHttpResponse(status_code=200, body='{"code":-1000,"msg":"bad"}')]),
    )
    with pytest.raises(BinanceResponseError):
        error_body_client.ping()


def test_only_expected_public_rest_paths_are_allowed() -> None:
    assert ALLOWED_PUBLIC_REST_PATHS == {
        FUTURES_PING_PATH,
        FUTURES_SERVER_TIME_PATH,
        FUTURES_EXCHANGE_INFO_PATH,
        FUTURES_KLINES_PATH,
    }
    forbidden_text = " ".join(ALLOWED_PUBLIC_REST_PATHS)
    for forbidden in (
        "ticker/price",
        "order",
        "account",
        "position",
        "leverage",
        "margin",
        "listenKey",
    ):
        assert forbidden not in forbidden_text


def test_client_does_not_expose_private_or_rest_price_monitor_methods() -> None:
    client = BinanceRestClient(settings=build_settings(), http_get=FakeHttpGet([]))

    for method_name in (
        "get_latest_price",
        "poll_price",
        "create_order",
        "get_account",
        "get_position",
        "set_leverage",
        "create_listen_key",
    ):
        assert not hasattr(client, method_name)

    source = inspect.getsource(rest_client)
    assert "app.storage" not in source
    assert "app.alerting" not in source
    assert "api_key" not in source.lower()
    assert "signature" not in source.lower()


def test_check_script_dry_run_does_not_call_real_binance() -> None:
    errors = collect_binance_rest_errors(settings=build_settings(), request_real_binance=False)

    assert errors == []


def test_check_script_dry_run_validates_invalid_base_url() -> None:
    errors = collect_binance_rest_errors(
        settings=build_settings(binance_base_url="not-a-url"),
        request_real_binance=False,
    )

    assert errors
    assert "BINANCE_BASE_URL" in errors[0]


def test_check_script_dry_run_validates_timeout() -> None:
    errors = collect_binance_rest_errors(
        settings=build_settings(binance_timeout_seconds=0),
        request_real_binance=False,
    )

    assert errors
    assert "BINANCE_TIMEOUT_SECONDS" in errors[0]


def test_check_script_dry_run_validates_max_retries() -> None:
    errors = collect_binance_rest_errors(
        settings=build_settings(binance_max_retries=-1),
        request_real_binance=False,
    )

    assert errors
    assert "BINANCE_MAX_RETRIES" in errors[0]


def test_check_script_dry_run_validates_retry_backoff() -> None:
    errors = collect_binance_rest_errors(
        settings=build_settings(binance_retry_backoff_seconds=-0.1),
        request_real_binance=False,
    )

    assert errors
    assert "BINANCE_RETRY_BACKOFF_SECONDS" in errors[0]


def test_check_script_dry_run_uses_fake_http_get_without_calling_it(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    original_client = check_binance_rest.BinanceRestClient

    class SpyClient(original_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["http_get"] = kwargs.get("http_get")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(check_binance_rest, "BinanceRestClient", SpyClient)

    errors = check_binance_rest.collect_binance_rest_errors(
        settings=build_settings(),
        request_real_binance=False,
    )

    assert errors == []
    assert captured["http_get"] is not None
    with pytest.raises(AssertionError):
        captured["http_get"]("https://fapi.binance.com/fapi/v1/ping", {}, 1.0)


def test_check_script_real_request_path_can_be_mocked() -> None:
    class FakeCheckClient:
        def ping(self) -> bool:
            return True

        def get_server_time(self) -> SimpleNamespace:
            return SimpleNamespace(server_time_ms=1)

        def get_exchange_info(self) -> dict[str, Any]:
            return {"symbols": [{"symbol": "BTCUSDT"}]}

        def get_klines(
            self,
            *,
            symbol: str | None = None,
            interval: str | None = None,
            limit: int | None = None,
            start_time_ms: int | None = None,
            end_time_ms: int | None = None,
        ) -> list[list[Any]]:
            assert symbol == "BTCUSDT"
            assert interval == "4h"
            assert limit == 10
            assert start_time_ms is None
            assert end_time_ms is None
            return [[1700000000000, "1"]]

    errors = collect_binance_rest_errors(
        settings=build_settings(),
        request_real_binance=True,
        client=FakeCheckClient(),
    )

    assert errors == []
