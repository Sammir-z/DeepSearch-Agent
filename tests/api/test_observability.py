import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class ObservabilityTests(unittest.TestCase):
    def test_log_event_contains_identity_metrics_but_no_secret_values(self):
        from app.memory.observability import log_event, logger

        with patch.object(logger, "log") as emit:
            log_event(
                "http_request",
                user_id="user-1",
                thread_id="thread-1",
                duration_ms=12.3456,
                error_type="HTTP_503",
                status_code=503,
                method="POST",
                path="/api/task",
            )

        payload = json.loads(emit.call_args.args[2])
        self.assertEqual(payload["user_id"], "user-1")
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertEqual(payload["status_code"], 503)
        self.assertEqual(payload["duration_ms"], 12.35)
        self.assertNotIn("password", emit.call_args.args[2].lower())
        self.assertNotIn("token", emit.call_args.args[2].lower())

    def test_monitor_telemetry_redacts_tool_arguments(self):
        from app.memory.observability import safe_event_data

        result = safe_event_data(
            {
                "content": "a complete memory that must not be logged",
                "api_key": "secret-value",
                "table_name": "drugs",
            }
        )

        self.assertEqual(result["content"], "[REDACTED]")
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertEqual(result["table_name"], "drugs")


class MiddlewareObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_middleware_logs_status_duration_identity_and_thread(self):
        from app.api.server import observability_middleware

        request = SimpleNamespace(
            state=SimpleNamespace(user_id="user-1", thread_id="thread-1"),
            method="POST",
            url=SimpleNamespace(path="/api/task"),
        )
        response = SimpleNamespace(status_code=503)

        async def call_next(_request):
            return response

        with patch("app.api.server.log_event") as emit:
            result = await observability_middleware(request, call_next)

        self.assertIs(result, response)
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "user-1")
        self.assertEqual(kwargs["thread_id"], "thread-1")
        self.assertEqual(kwargs["status_code"], 503)
        self.assertEqual(kwargs["error_type"], "HTTP_503")
        self.assertGreaterEqual(kwargs["duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()
