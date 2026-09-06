import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class AuthRouteTests(unittest.TestCase):
    def test_protected_routes_require_current_user_dependency(self):
        from app.api import server

        for endpoint in (
            server.run_task,
            server.cancel_task,
            server.upload_files,
            server.download_file,
            server.list_files,
            server.list_threads,
        ):
            self.assertIn("user", inspect.signature(endpoint).parameters)

    def test_auth_routes_are_registered(self):
        from app.api.server import app

        routes = {
            (route.path, tuple(sorted(route.methods or [])))
            for route in app.routes
            if hasattr(route, "methods")
        }
        self.assertIn(("/api/auth/register", ("POST",)), routes)
        self.assertIn(("/api/auth/login", ("POST",)), routes)
        self.assertIn(("/api/auth/logout", ("POST",)), routes)
        self.assertIn(("/api/auth/me", ("GET",)), routes)

    def test_thread_list_route_is_registered(self):
        from app.api.server import app

        routes = {
            (route.path, tuple(sorted(route.methods or [])))
            for route in app.routes
            if hasattr(route, "methods")
        }
        self.assertIn(("/api/threads", ("GET",)), routes)

    def test_memory_routes_are_registered(self):
        from app.api.server import app

        routes = {
            (route.path, tuple(sorted(route.methods or [])))
            for route in app.routes
            if hasattr(route, "methods")
        }
        self.assertIn(("/api/memory", ("POST",)), routes)
        self.assertIn(("/api/memory", ("GET",)), routes)
        self.assertIn(("/api/memory/{memory_key}", ("DELETE",)), routes)


class PersistenceFailureRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_returns_503_when_redis_persistence_is_unavailable(self):
        from fastapi import HTTPException

        from app.api import server
        from app.memory import AuthUser, RedisPersistenceUnavailableError

        http_request = SimpleNamespace(state=SimpleNamespace())
        user = AuthUser(id="user-1", email="user@example.com", status="active")
        with patch.object(
            server.redis_persistence,
            "ensure_available",
            new=AsyncMock(
                side_effect=RedisPersistenceUnavailableError("redis down")
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await server.run_task(
                    server.TaskRequest(query="run"),
                    http_request,
                    user=user,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("任务未启动", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
