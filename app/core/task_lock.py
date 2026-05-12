"""Redis-backed task lock helpers.

This file belongs to `app/core`.
It provides a small owner-checked lock for phase-08 Kline write tasks. The lock
uses Redis `SET key value NX EX ttl` and releases only when the stored owner
matches the current task owner.
It does not request Binance, read or write MySQL, send Hermes, call DeepSeek,
cache prices, repair Klines, schedule jobs, or execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import RedisError

_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


@dataclass(frozen=True)
class TaskLockIdentity:
    """Identity of one held or attempted task lock."""

    key: str
    owner: str
    ttl_seconds: int


class RedisTaskLock:
    """Owner-checked Redis task lock.

    Parameters: `redis_client` is an injected Redis-compatible client. If omitted,
    the default Redis client is created lazily from `app/storage/redis/client.py`.
    Return value: lock helper instance.
    Failure scenarios: Redis client creation or Redis command failures raise
    `RedisError` so callers can refuse formal Kline writes.
    External service access: only the explicit `acquire_lock` and `release_lock`
    methods talk to Redis.
    Data impact: writes/deletes only caller-supplied task-lock keys; it never writes
    `bitcoin_price` or any market data.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis_client = redis_client

    def _client(self) -> Any:
        if self._redis_client is None:
            from app.storage.redis.client import create_redis_client

            self._redis_client = create_redis_client()
        return self._redis_client

    def acquire_lock(self, *, key: str, owner: str, ttl_seconds: int) -> bool:
        """Try to acquire a Redis lock with owner and TTL.

        Return value: `True` when acquired, `False` when another owner already
        holds the lock. Redis failures raise `RedisError`.
        """

        if ttl_seconds <= 0:
            raise RedisError("task lock ttl_seconds must be greater than 0")
        try:
            result = self._client().set(key, owner, nx=True, ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver errors.
            raise RedisError(f"failed to acquire task lock key={key}") from exc
        return bool(result)

    def release_lock(self, *, key: str, owner: str) -> bool:
        """Release a Redis lock only when the stored owner matches.

        Return value: `True` when the lock was deleted, `False` when the key is
        absent or owned by another task. Redis failures raise `RedisError`.
        """

        try:
            # Owner check and delete must be atomic; GET followed by DELETE can
            # remove a lock reacquired by a different task between commands.
            result = self._client().eval(_RELEASE_LOCK_SCRIPT, 1, key, owner)
            return bool(int(result or 0))
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver errors.
            raise RedisError(f"failed to release task lock key={key}") from exc


def build_kline_write_lock_key(*, symbol: str, interval_value: str) -> str:
    """Build the shared formal Kline write-lock key for one symbol and interval."""

    return f"kline_write:{symbol.strip().upper()}:{interval_value.strip()}"
