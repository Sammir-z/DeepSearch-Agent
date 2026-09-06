"""Shared Redis persistence for LangGraph checkpoints and stores.

This module owns the Redis client lifecycle so the Agent assembly and FastAPI
lifespan do not need to know how Redis is configured or initialized.
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass

from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.store.redis import RedisStore
from redis import Redis
from redis.asyncio import Redis as AsyncRedis


class RedisPersistenceUnavailableError(RuntimeError):
    """Raised when Redis-backed Agent persistence cannot be used."""


class _DeferredRedisStore(RedisStore):
    """Construct a store without probing Redis during module import.

    ``RedisStore`` sends ``CLIENT SETINFO`` from its constructor. Suppressing
    that best-effort monitoring call keeps the API importable while Redis is
    down; the real connectivity and index setup still happen in ``setup``.
    """

    def set_client_info(self) -> None:
        return None


def resolve_redis_url() -> str:
    """Resolve the Redis URL, preferring an explicit URL over host/port."""

    explicit_url = os.getenv("REDIS_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = os.getenv("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/0"


@dataclass
class RedisPersistence:
    """Own one Redis client shared by the LangGraph saver and store."""

    client: Redis
    checkpointer: AsyncRedisSaver | None
    store: RedisStore
    async_client: AsyncRedis | None = None
    _closed: bool = False
    _setup_complete: bool = False
    _setup_lock: asyncio.Lock | None = None

    @classmethod
    def from_env(cls) -> "RedisPersistence":
        """Build persistence components from the current environment."""

        client = Redis.from_url(resolve_redis_url(), decode_responses=False)
        return cls(
            client=client,
            # AsyncRedisSaver must be constructed inside a running event loop.
            # Defer its construction until setup() is awaited by FastAPI.
            checkpointer=None,
            store=_DeferredRedisStore(client),
        )

    async def setup(self) -> None:
        """Create Redis indices for the async saver and sync store."""

        if self._setup_lock is None:
            self._setup_lock = asyncio.Lock()

        async with self._setup_lock:
            if self._setup_complete:
                return

            try:
                if self.async_client is None:
                    self.async_client = AsyncRedis.from_url(
                        resolve_redis_url(), decode_responses=False
                    )
                if self.checkpointer is None:
                    self.checkpointer = AsyncRedisSaver(
                        redis_client=self.async_client
                    )

                await self.async_client.ping()
                await self.checkpointer.asetup()
                # RedisStore exposes async wrappers, but setup itself is sync.
                await asyncio.to_thread(self.store.setup)
            except Exception as error:
                self._setup_complete = False
                raise RedisPersistenceUnavailableError(
                    "Redis 持久化服务不可用"
                ) from error
            self._setup_complete = True

    async def ensure_available(self) -> None:
        """Verify Redis connectivity before accepting a task.

        A startup race can leave the API process alive while Redis is still
        booting. Retry index setup on the first request after that race rather
        than silently running without a checkpointer.
        """

        try:
            if self.async_client is None or self.checkpointer is None:
                await self.setup()
            else:
                await self.async_client.ping()
                if not self._setup_complete:
                    await self.setup()
        except RedisPersistenceUnavailableError:
            raise
        except Exception as error:
            raise RedisPersistenceUnavailableError("Redis 持久化服务不可用") from error

    async def close(self) -> None:
        """Close both Redis clients once during application shutdown."""

        if not self._closed:
            if self.async_client is not None:
                await self.async_client.aclose()
            self.client.close()
            self._closed = True
