"""Redis lock helpers for stage-21C strategy advice scheduler orchestration.

This file belongs to `app/strategy_advice`. It builds and owns only temporary
Redis locks that prevent concurrent handling of the same MRAG by 21C.

Called by `app/strategy_advice/scheduler_service.py`. External services:
Redis only through `app/core/task_lock.py`. MySQL: none. Hermes: none. Model
providers: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.task_lock import RedisTaskLock


@dataclass(frozen=True)
class StrategyAdviceSchedulerLock:
    """One acquired or attempted stage-21C Redis lock identity."""

    key: str
    owner: str
    ttl_seconds: int


class StrategyAdviceSchedulerLockManager:
    """Acquire and release owner-checked Redis locks for stage 21C.

    Failure scenarios: Redis driver errors propagate through `RedisTaskLock`
    as `RedisError`, allowing the service to log a lock_skipped result instead
    of crashing. This manager never touches MySQL or Hermes.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._lock = RedisTaskLock(redis_client=redis_client)

    def acquire_strategy_advice_lock(self, *, lock: StrategyAdviceSchedulerLock) -> bool:
        """Return True when this worker acquired the MRAG lock."""

        return self._lock.acquire_lock(key=lock.key, owner=lock.owner, ttl_seconds=lock.ttl_seconds)

    def release_strategy_advice_lock(self, *, lock: StrategyAdviceSchedulerLock) -> bool:
        """Release the MRAG lock if it is still owned by this worker."""

        return self._lock.release_lock(key=lock.key, owner=lock.owner)


def build_strategy_advice_21c_lock_key(
    *,
    symbol: str,
    base_interval: str,
    higher_interval: str,
    review_aggregation_run_id: str,
) -> str:
    """Build the Redis key for one stage-21C MRAG orchestration lock."""

    return (
        "strategy_advice_21c:"
        f"{symbol.strip().upper()}:{base_interval.strip()}:{higher_interval.strip()}:{review_aggregation_run_id.strip()}"
    )


__all__ = [
    "StrategyAdviceSchedulerLock",
    "StrategyAdviceSchedulerLockManager",
    "build_strategy_advice_21c_lock_key",
]
