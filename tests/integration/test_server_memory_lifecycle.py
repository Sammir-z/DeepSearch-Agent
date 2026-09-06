import unittest
from unittest.mock import AsyncMock, patch


class ServerMemoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_sets_up_and_closes_redis_persistence(self):
        from app.api import server

        with patch.object(
            server.redis_persistence, "setup", new=AsyncMock()
        ) as setup, patch.object(
            server.redis_persistence, "close", new=AsyncMock()
        ) as close, patch.object(
            server.redis_persistence, "_setup_complete", True
        ), patch.object(
            server, "initialize_main_agent", new=AsyncMock()
        ) as initialize_agent, patch.object(
            server.auth_service, "setup"
        ) as auth_setup:
            async with server.lifespan(server.app):
                setup.assert_awaited_once_with()
                initialize_agent.assert_awaited_once_with()
                auth_setup.assert_called_once_with()
                close.assert_not_called()

        close.assert_awaited_once_with()

    async def test_lifespan_stays_up_when_redis_is_temporarily_unavailable(self):
        from app.api import server
        from app.memory import RedisPersistenceUnavailableError

        with patch.object(
            server.redis_persistence,
            "setup",
            new=AsyncMock(
                side_effect=RedisPersistenceUnavailableError("redis down")
            ),
        ), patch.object(
            server.redis_persistence, "close", new=AsyncMock()
        ) as close, patch.object(
            server.auth_service, "setup"
        ):
            async with server.lifespan(server.app):
                pass

        close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
