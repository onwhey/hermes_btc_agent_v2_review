"""Redis lock helpers for the stage-25A manual strategy pipeline.

This file belongs to `app/strategy_pipeline`. It builds the pipeline-level
Redis lock key and delegates locking to `app/core/task_lock.py`.

Called by `app/strategy_pipeline/service.py`. External services: Redis only
when acquiring/releasing locks. MySQL: none. Hermes: none. Large models: none.
Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.task_lock import RedisTaskLock
from app.core.time_utils import UTC, ensure_utc_aware


@dataclass(frozen=True)
class StrategyPipelineLock:
    """One Redis lock identity for a symbol/interval/base-Kline slot."""

    key: str
    owner: str
    ttl_seconds: int
    acquired: bool = False


def build_strategy_pipeline_lock_key(
    *,
    symbol: str,
    base_interval: str,
    higher_interval: str,
    kline_slot_utc: datetime,
) -> str:
    """Build the 25A lock key required by the plan.

    `kline_slot_utc` is the base Kline open time in UTC. Naive MySQL datetimes
    are treated as UTC through the shared time helper.
    """

    slot = ensure_utc_aware(kline_slot_utc)
    if slot is None:
        raise ValueError("kline_slot_utc is required for strategy pipeline lock key")
    slot_text = slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"strategy_pipeline:{symbol}:{base_interval}:{higher_interval}:{slot_text}"


class StrategyPipelineLockManager:
    """Acquire and release the stage-25A Redis lock through the shared helper."""

    def __init__(self, *, task_lock: RedisTaskLock | None = None) -> None:
        self._task_lock = task_lock or RedisTaskLock()

    def acquire_strategy_pipeline_lock(self, *, lock: StrategyPipelineLock) -> StrategyPipelineLock:
        """Try to acquire the Redis lock without writing MySQL."""

        acquired = self._task_lock.acquire_lock(lock.key, owner=lock.owner, ttl_seconds=lock.ttl_seconds)
        return StrategyPipelineLock(
            key=lock.key,
            owner=lock.owner,
            ttl_seconds=lock.ttl_seconds,
            acquired=bool(acquired),
        )

    def release_strategy_pipeline_lock(self, *, lock: StrategyPipelineLock) -> None:
        """Release the Redis lock if this owner still holds it."""

        self._task_lock.release_lock(lock.key, owner=lock.owner)


__all__ = [
    "StrategyPipelineLock",
    "StrategyPipelineLockManager",
    "build_strategy_pipeline_lock_key",
]

