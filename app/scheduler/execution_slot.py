"""Redis execution-slot de-duplication for phase-12 scheduler jobs.

This file belongs to `app/scheduler`. It builds stable Redis keys for scheduler
time windows and atomically reserves a slot before a job runs. It does not run
jobs, call scripts, request Binance, read/write MySQL, send Hermes, call
DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.exceptions import RedisError
from app.storage.redis.client import get_redis_client

KLINE_4H_INCREMENTAL_JOB_NAME = "kline_4h_incremental"
DAILY_KLINE_INTEGRITY_JOB_NAME = "daily_kline_integrity"


def build_kline_4h_incremental_slot_key(slot_time_utc: datetime) -> str:
    """Build the Redis slot key for one 09 scheduled 4h collection window."""

    return f"scheduler:job:{KLINE_4H_INCREMENTAL_JOB_NAME}:{slot_time_utc.strftime('%Y-%m-%dT%H:%MZ')}"


def build_daily_kline_integrity_slot_key(slot_date_utc: date) -> str:
    """Build the Redis slot key for one 11 daily integrity review date."""

    return f"scheduler:job:{DAILY_KLINE_INTEGRITY_JOB_NAME}:{slot_date_utc.isoformat()}"


class SchedulerExecutionSlotStore:
    """Atomic Redis slot store used by the scheduler runner.

    Parameters: `redis_client` may be injected by tests; omitted value uses the
    shared Redis client factory.
    Return value: an object with `reserve_execution_slot`.
    Failure scenarios: Redis driver errors are wrapped as `RedisError`.
    External effects: writes short-lived Redis scheduler slot keys only.
    This class does not write Kline data, read price state, send Hermes, call
    DeepSeek, call scripts, or execute trades.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis_client = redis_client

    @property
    def redis_client(self) -> Any:
        if self._redis_client is None:
            self._redis_client = get_redis_client()
        return self._redis_client

    def reserve_execution_slot(
        self,
        *,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        """Reserve one job execution time window.

        Parameters: `key` identifies the job time window; `owner` is the runner
        trace; `ttl_seconds` controls Redis key lifetime.
        Return value: True when the slot is newly reserved, False when it
        already exists.
        Failure scenarios: invalid TTL or Redis write failure raises
        `RedisError`.
        """

        if ttl_seconds <= 0:
            raise RedisError("Scheduler 执行槽 TTL 必须大于 0")
        try:
            return bool(self.redis_client.set(key, owner, nx=True, ex=ttl_seconds))
        except Exception as exc:  # noqa: BLE001 - Redis driver errors must block execution safely.
            raise RedisError(f"Scheduler 执行槽写入失败：{exc}") from exc


__all__ = [
    "DAILY_KLINE_INTEGRITY_JOB_NAME",
    "KLINE_4H_INCREMENTAL_JOB_NAME",
    "SchedulerExecutionSlotStore",
    "build_daily_kline_integrity_slot_key",
    "build_kline_4h_incremental_slot_key",
]
