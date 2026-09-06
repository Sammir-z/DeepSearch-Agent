import unittest
from unittest.mock import patch

from app.memory.context import reset_user_id_context, set_user_id_context
from app.memory.tools import (
    configure_user_memory_service,
    remember_user_memory,
    search_user_memories,
)
from app.memory.user_memory import UserMemoryService


class FailingStore:
    def get(self, namespace, key):
        raise OSError("redis unavailable")

    def put(self, namespace, key, value, index=None):
        raise OSError("redis unavailable")

    def search(self, namespace, query=None, limit=10):
        raise OSError("redis unavailable")


class MemoryToolFailureTests(unittest.TestCase):
    def setUp(self):
        configure_user_memory_service(UserMemoryService(FailingStore()))
        self.user_token = set_user_id_context("user-1")

    def tearDown(self):
        reset_user_id_context(self.user_token)

    def test_write_failure_reports_save_failed_and_never_claims_success(self):
        with patch("app.memory.tools.monitor.report_memory_save_failed") as report:
            result = remember_user_memory.invoke(
                {
                    "memory_type": "preference",
                    "content": "喜欢简洁报告",
                }
            )

        self.assertIn("保存失败", result)
        self.assertNotIn("已保存", result)
        report.assert_called_once_with("write", "MemoryStorageUnavailableError")

    def test_read_failure_reports_memory_unavailable(self):
        with patch("app.memory.tools.monitor.report_memory_unavailable") as report:
            result = search_user_memories.invoke({"query": "", "limit": 10})

        self.assertIn("未加载长期记忆", result)
        report.assert_called_once_with("read", "MemoryStorageUnavailableError")


if __name__ == "__main__":
    unittest.main()
