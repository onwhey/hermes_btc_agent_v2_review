"""Helpers for interpreting 20C model-review worker flags in the 25A pipeline.

This file belongs to `app/strategy_pipeline`. It only derives pipeline summary
booleans from existing 20C worker results. It does not call models, read model
outputs, send Hermes, access Binance/account state, or perform trading.
"""

from __future__ import annotations

from typing import Any, Iterable

_REAL_PROVIDER_HINTS = ("deepseek", "openai", "claude")
_NON_REAL_PROVIDER_HINTS = ("mock", "fake", "stub", "test")


def infer_real_model_called_from_worker_result(worker_result: Any) -> bool:
    """Return true only when this worker tick actually invoked a real provider.

    Parameters: an existing `ModelReviewChainWorkerResult`-like object.
    Return value: `True` means the current pipeline tick reached stage 19 with a
    real provider model key. Reused results, dry-run, mock review, and missing
    model keys return `False`.
    Failure scenarios: malformed result objects are treated conservatively as
    no real external model call.
    External effects: none.
    """

    if not bool(getattr(worker_result, "model_review_invoked", False)):
        return False
    if bool(getattr(worker_result, "model_review_reused", False)):
        return False

    invocation_mode = str(getattr(worker_result, "model_review_invocation_mode", "") or "").lower()
    if invocation_mode in {"", "none", "dry_run", "reused", "mock"}:
        return False

    keys = tuple(_iter_model_key_hints(worker_result))
    if not keys:
        return False
    for key in keys:
        normalized = key.lower()
        if any(marker in normalized for marker in _NON_REAL_PROVIDER_HINTS):
            continue
        if any(marker in normalized for marker in _REAL_PROVIDER_HINTS):
            return True
    return False


def _iter_model_key_hints(worker_result: Any) -> Iterable[str]:
    raw_keys = getattr(worker_result, "invoked_model_keys_json", ()) or ()
    if isinstance(raw_keys, str):
        yield raw_keys
    else:
        for value in raw_keys:
            if value:
                yield str(value)

    details = getattr(worker_result, "details", {}) or {}
    if not isinstance(details, dict):
        return
    for key in ("provider", "model_provider", "model_key"):
        value = details.get(key)
        if value:
            yield str(value)
    raw_detail_keys = details.get("invoked_model_keys_json") or details.get("invoked_model_keys")
    if isinstance(raw_detail_keys, str):
        yield raw_detail_keys
    elif isinstance(raw_detail_keys, (list, tuple)):
        for value in raw_detail_keys:
            if value:
                yield str(value)


__all__ = ["infer_real_model_called_from_worker_result"]
