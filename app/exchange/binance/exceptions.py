"""Binance public REST exception types.

This file belongs to `app/exchange/binance`.
It defines explicit exceptions for public Binance REST configuration, request,
timeout, HTTP status, and response parsing failures.
It is called by `app/exchange/binance/rest_client.py` and tests.
It does not send HTTP requests by itself.
It does not read or write MySQL.
It does not read or write Redis.
It does not send Hermes alerts.
It does not call DeepSeek or any large language model.
It does not implement account, signing, private stream, or trading execution features.
"""

from __future__ import annotations

from app.core.exceptions import ExternalServiceError, ValidationError


class BinanceError(ExternalServiceError):
    """Base exception for Binance public REST failures.

    Parameters: inherited exception message and optional cause.
    Return value: none.
    Failure scenarios: public REST request, timeout, HTTP, or response failures.
    External service access: this class does not access external services.
    Data impact: this class does not read/write MySQL or Redis and does not send Hermes.
    This class does not implement private endpoints or trading execution.
    """


class BinanceRequestError(BinanceError):
    """Raised when a public Binance REST request cannot complete safely."""


class BinanceTimeoutError(BinanceRequestError):
    """Raised when a public Binance REST request exceeds the configured timeout."""


class BinanceHTTPError(BinanceRequestError):
    """Raised when Binance returns a non-success HTTP status.

    Parameters: `message` is safe for logs; `status_code`, `path`, and
    `binance_code` identify the public endpoint failure without printing secrets.
    Return value: none.
    Failure scenarios: 4xx or 5xx public REST responses.
    External service access: this class does not access external services.
    Data impact: this class does not read/write MySQL or Redis and does not send Hermes.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str = "",
        binance_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.binance_code = binance_code


class BinanceRateLimitError(BinanceHTTPError):
    """Raised when Binance returns a rate-limit status for a public endpoint."""


class BinanceResponseError(BinanceError):
    """Raised when a public Binance REST response cannot be parsed or validated."""


class BinanceValidationError(ValidationError):
    """Raised when local Binance public REST input validation fails."""
