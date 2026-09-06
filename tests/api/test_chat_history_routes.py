import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ChatHistoryRouteRegistrationTests(unittest.TestCase):
    def test_history_route_is_registered_and_protected(self):
        import inspect

        from app.api import server

        routes = {
            (route.path, tuple(sorted(route.methods or [])))
            for route in server.app.routes
            if hasattr(route, "methods")
        }
        self.assertIn(("/api/threads/{thread_id}/history", ("GET",)), routes)
        self.assertIn("user", inspect.signature(server.get_thread_history).parameters)


class ChatHistoryRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_owner_thread_stops_before_reading_history(self):
        from fastapi import HTTPException

        from app.api import server
        from app.memory import AuthUser

        request = SimpleNamespace(state=SimpleNamespace())
        user = AuthUser(id="user-2", email="other@example.com", status="active")
        with (
            patch.object(
                server,
                "_require_existing_thread",
                side_effect=HTTPException(status_code=404, detail="会话不存在"),
            ) as require_owner,
            patch.object(server, "_ensure_redis_persistence", new=AsyncMock()) as ensure_redis,
            patch.object(server, "load_thread_history", new=AsyncMock()) as load_history,
        ):
            with self.assertRaises(HTTPException) as raised:
                await server.get_thread_history("thread-1", request, user=user)

        self.assertEqual(raised.exception.status_code, 404)
        require_owner.assert_called_once_with("thread-1", "user-2")
        ensure_redis.assert_not_awaited()
        load_history.assert_not_awaited()

    async def test_owned_thread_returns_history_without_user_id(self):
        from app.api import server
        from app.memory import AuthUser
        from app.memory.history import ChatHistoryTurn

        request = SimpleNamespace(state=SimpleNamespace())
        user = AuthUser(id="user-1", email="user@example.com", status="active")
        turns = [
            ChatHistoryTurn(
                id="turn-0",
                user_content="问题",
                assistant_content="回答",
                timestamp="2026-09-06T10:00:00",
            )
        ]
        with (
            patch.object(server, "_require_existing_thread") as require_owner,
            patch.object(server, "_ensure_redis_persistence", new=AsyncMock()),
            patch.object(server, "initialize_main_agent", new=AsyncMock(return_value=object())),
            patch.object(server, "load_thread_history", new=AsyncMock(return_value=turns)),
        ):
            response = await server.get_thread_history("thread-1", request, user=user)

        require_owner.assert_called_once_with("thread-1", "user-1")
        self.assertEqual(response["thread_id"], "thread-1")
        self.assertEqual(response["turns"][0]["user_content"], "问题")
        self.assertNotIn("user_id", response)

    async def test_storage_failure_returns_503(self):
        from fastapi import HTTPException

        from app.api import server
        from app.memory import AuthUser
        from app.memory.history import HistoryStorageUnavailableError

        request = SimpleNamespace(state=SimpleNamespace())
        user = AuthUser(id="user-1", email="user@example.com", status="active")
        with (
            patch.object(server, "_require_existing_thread"),
            patch.object(server, "_ensure_redis_persistence", new=AsyncMock()),
            patch.object(server, "initialize_main_agent", new=AsyncMock(return_value=object())),
            patch.object(
                server,
                "load_thread_history",
                new=AsyncMock(side_effect=HistoryStorageUnavailableError("history down")),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await server.get_thread_history("thread-1", request, user=user)

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
