import os
import unittest
from unittest.mock import patch


class FrontendUrlLoggingTests(unittest.TestCase):
    def test_frontend_url_uses_configured_public_url(self):
        from app.api import server

        with patch.dict(
            os.environ,
            {"FRONTEND_URL": "http://localhost:5174", "FRONTEND_PORT": "5199"},
            clear=False,
        ):
            self.assertEqual(server.get_frontend_url(), "http://localhost:5174")

    def test_frontend_startup_log_prints_public_url_from_port(self):
        from app.api import server

        with patch.dict(os.environ, {"FRONTEND_URL": "", "FRONTEND_PORT": "5174"}, clear=False):
            with self.assertLogs("uvicorn.error", level="INFO") as captured:
                server.log_frontend_url()

        self.assertTrue(
            any("前端访问地址: http://localhost:5174" in message for message in captured.output)
        )


if __name__ == "__main__":
    unittest.main()
