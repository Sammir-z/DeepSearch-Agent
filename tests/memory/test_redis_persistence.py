import os
import unittest
from unittest.mock import AsyncMock, Mock, patch


class RedisPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_redis_url_prefers_explicit_url(self):
        from app.memory.redis_persistence import resolve_redis_url

        with patch.dict(
            os.environ,
            {"REDIS_URL": "redis://redis:6379/0", "REDIS_PORT": "6380"},
            clear=True,
        ):
            self.assertEqual(resolve_redis_url(), "redis://redis:6379/0")

    def test_resolve_redis_url_uses_host_and_port_fallback(self):
        from app.memory.redis_persistence import resolve_redis_url

        with patch.dict(
            os.environ,
            {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6380"},
            clear=True,
        ):
            self.assertEqual(resolve_redis_url(), "redis://127.0.0.1:6380/0")

    async def test_setup_initializes_async_checkpointer_and_close_closes_clients(self):
        from app.memory.redis_persistence import RedisPersistence

        client = Mock()
        async_client = AsyncMock()
        checkpointer = Mock()
        checkpointer.asetup = AsyncMock()
        store = Mock()
        persistence = RedisPersistence(client, None, store)

        with patch(
            "app.memory.redis_persistence.AsyncRedis.from_url",
            return_value=async_client,
        ), patch(
            "app.memory.redis_persistence.AsyncRedisSaver",
            return_value=checkpointer,
        ):
            await persistence.setup()
            await persistence.close()

        checkpointer.asetup.assert_awaited_once_with()
        store.setup.assert_called_once_with()
        async_client.aclose.assert_awaited_once_with()
        client.close.assert_called_once_with()

    async def test_setup_failure_is_explicit_and_never_falls_back(self):
        from app.memory.redis_persistence import (
            RedisPersistence,
            RedisPersistenceUnavailableError,
        )

        client = Mock()
        checkpointer = Mock()
        checkpointer.asetup = AsyncMock(side_effect=RuntimeError("redis down"))
        store = Mock()
        persistence = RedisPersistence(
            client,
            checkpointer,
            store,
            async_client=AsyncMock(),
        )

        with self.assertRaises(RedisPersistenceUnavailableError):
            await persistence.setup()

        self.assertFalse(persistence._setup_complete)
        store.setup.assert_not_called()

    async def test_ensure_available_rejects_unreachable_redis(self):
        from app.memory.redis_persistence import (
            RedisPersistence,
            RedisPersistenceUnavailableError,
        )

        client = Mock()
        async_client = AsyncMock()
        async_client.ping.side_effect = OSError("connection refused")
        persistence = RedisPersistence(
            client,
            Mock(),
            Mock(),
            async_client=async_client,
            _setup_complete=True,
        )

        with self.assertRaises(RedisPersistenceUnavailableError):
            await persistence.ensure_available()

    @patch("app.memory.redis_persistence._DeferredRedisStore")
    @patch("app.memory.redis_persistence.Redis")
    def test_from_env_defers_async_checkpointer_creation(self, redis_cls, store_cls):
        from app.memory.redis_persistence import RedisPersistence

        client = Mock()
        redis_cls.from_url.return_value = client
        with patch.dict(
            os.environ, {"REDIS_URL": "redis://redis:6379/0"}, clear=True
        ), patch("app.memory.redis_persistence.AsyncRedisSaver") as saver_cls:
            persistence = RedisPersistence.from_env()

        redis_cls.from_url.assert_called_once_with(
            "redis://redis:6379/0", decode_responses=False
        )
        saver_cls.assert_not_called()
        store_cls.assert_called_once_with(client)
        self.assertIs(persistence.client, client)
        self.assertIsNone(persistence.checkpointer)
        self.assertIs(persistence.store, store_cls.return_value)

    def test_store_constructor_does_not_probe_redis(self):
        from app.memory.redis_persistence import _DeferredRedisStore

        class Client:
            def client_setinfo(self, *_args, **_kwargs):
                raise AssertionError("store construction must not contact Redis")

        store = _DeferredRedisStore(Client())

        self.assertIsNotNone(store)

if __name__ == "__main__":
    unittest.main()
