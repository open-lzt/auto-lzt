"""IdempotencyGuard — a fast, best-effort dedup over Redis SET NX.

F-2: this is NOT the source of truth for "did the side-effect happen" — the durable Postgres
RunStep row is. The guard only suppresses a *duplicate within the run window*; for a truly
idempotent node (bump is idempotent by item_id on the marketplace) re-running after the key expires
is harmless, and for non-idempotent nodes (Wave 4) dedup keys off the upstream event id, not this.
"""

from __future__ import annotations

from typing import Protocol

from redis.asyncio import Redis


class DedupGuard(Protocol):
    """The dedup contract a node depends on. A real Redis guard and an in-memory test double both
    satisfy it structurally (genuine two-impl seam)."""

    async def check_and_set(self, key: str, ttl_s: int = 3600) -> bool: ...


class DuplicateOperation(Exception):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"duplicate operation {idempotency_key}")
        self.idempotency_key = idempotency_key


class IdempotencyGuard:
    """Redis-backed dedup. ``check_and_set`` returns True the first time a key is seen and False on
    any repeat within the TTL."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check_and_set(self, key: str, ttl_s: int = 3600) -> bool:
        was_set = await self._redis.set(f"idem:{key}", "1", nx=True, ex=ttl_s)
        return bool(was_set)
