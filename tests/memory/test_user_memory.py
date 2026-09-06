import unittest
from types import SimpleNamespace

from app.memory.context import (
    get_user_id_context,
    reset_user_id_context,
    set_user_id_context,
)
from app.memory.user_memory import (
    MemoryValidationError,
    UserMemoryService,
    memory_namespace,
)


class FakeStore:
    def __init__(self):
        self.values = {}

    def put(self, namespace, key, value, index=None):
        self.values[(namespace, key)] = dict(value)

    def get(self, namespace, key):
        value = self.values.get((namespace, key))
        if value is None:
            return None
        return SimpleNamespace(key=key, value=dict(value))

    def delete(self, namespace, key):
        self.values.pop((namespace, key), None)

    def search(self, namespace, query=None, limit=10):
        items = [
            SimpleNamespace(key=key, value=dict(value))
            for (stored_namespace, key), value in self.values.items()
            if stored_namespace == namespace
        ]
        return items[:limit]


class UserMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.service = UserMemoryService(self.store)

    def test_memories_are_isolated_by_user_namespace(self):
        first = self.service.remember("user-1", "preference", "喜欢简洁的报告")
        self.service.remember("user-2", "preference", "喜欢详细的报告")

        self.assertEqual(memory_namespace("user-1"), ("memories", "user-1"))
        self.assertEqual([item.key for item in self.service.search("user-1")], [first.key])
        self.assertEqual(len(self.service.search("user-2")), 1)

    def test_query_and_forget_are_scoped_to_one_user(self):
        record = self.service.remember("user-1", "fact", "所在地区是上海")
        self.service.remember("user-1", "constraint", "回答使用中文")

        results = self.service.search("user-1", query="上海")
        self.assertEqual([item.key for item in results], [record.key])
        self.assertTrue(self.service.forget("user-1", record.key))
        self.assertIsNone(self.service.get("user-1", record.key))
        self.assertFalse(self.service.forget("user-1", record.key))

    def test_type_length_and_sensitive_content_are_rejected(self):
        with self.assertRaises(MemoryValidationError):
            self.service.remember("user-1", "other", "内容")
        with self.assertRaises(MemoryValidationError):
            self.service.remember("user-1", "fact", "password=do-not-store")
        with self.assertRaises(MemoryValidationError):
            self.service.remember("user-1", "fact", " ")

    def test_context_round_trip(self):
        token = set_user_id_context("user-1")
        try:
            self.assertEqual(get_user_id_context(), "user-1")
        finally:
            reset_user_id_context(token)
        self.assertIsNone(get_user_id_context())


if __name__ == "__main__":
    unittest.main()
