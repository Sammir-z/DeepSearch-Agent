import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.memory.auth_service import (
    AuthService,
    AuthStorageUnavailableError,
    InvalidCredentialsError,
    LoginRateLimitedError,
    hash_session_token,
    normalize_email,
)
from app.memory.auth_store import StoredSession, StoredUser


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True

    def delete(self, key):
        self.values.pop(key, None)
        return 1


class AuthServiceTests(unittest.TestCase):
    def test_normalize_email_is_case_insensitive(self):
        self.assertEqual(normalize_email("  User@Example.com "), "user@example.com")

    def test_session_token_is_stored_as_sha256(self):
        self.assertEqual(
            hash_session_token("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_login_creates_opaque_session_and_authenticate_reads_user(self):
        store = Mock()
        redis = FakeRedis()
        password_hash = AuthService(store=store, redis_client=redis).password_hash
        record = StoredUser("user-1", "user@example.com", password_hash.hash("correctpass"), "active")
        store.get_user_by_email.return_value = record
        store.get_user_by_id.return_value = record

        service = AuthService(store=store, redis_client=redis)
        token, user = service.login("USER@example.com", "correctpass", "127.0.0.1")
        store.create_session.assert_called_once()
        token_hash = store.create_session.call_args.args[0]
        self.assertNotEqual(token, token_hash)
        self.assertEqual(user.id, "user-1")

        store.get_active_session.return_value = StoredSession(
            token_hash=token_hash,
            user_id="user-1",
            expires_at=datetime.now(timezone.utc),
        )
        authenticated = service.authenticate(token)
        self.assertEqual(authenticated.id, "user-1")

    def test_failed_logins_are_limited_by_redis_counter(self):
        store = Mock()
        store.get_user_by_email.return_value = None
        redis = FakeRedis()
        service = AuthService(store=store, redis_client=redis)

        for _ in range(service.login_failure_limit):
            with self.assertRaises(InvalidCredentialsError):
                service.login("user@example.com", "wrongpass", "127.0.0.1")

        with self.assertRaises(LoginRateLimitedError):
            service.login("user@example.com", "wrongpass", "127.0.0.1")

    def test_login_success_clears_failed_login_counter(self):
        store = Mock()
        redis = FakeRedis()
        password_hash = AuthService(store=store, redis_client=redis).password_hash
        record = StoredUser("user-1", "user@example.com", password_hash.hash("correctpass"), "active")
        store.get_user_by_email.return_value = record
        service = AuthService(store=store, redis_client=redis)
        rate_key = service._rate_key("user@example.com", "127.0.0.1")
        redis.values[rate_key] = 2

        service.login("user@example.com", "correctpass", "127.0.0.1")
        self.assertNotIn(rate_key, redis.values)

    def test_database_configuration_failure_is_reported_as_storage_unavailable(self):
        store = Mock()
        store.get_user_by_id.side_effect = ValueError("missing MYSQL_HOST")
        service = AuthService(store=store, redis_client=FakeRedis())

        with self.assertRaises(AuthStorageUnavailableError):
            service.authenticate("opaque-token")


if __name__ == "__main__":
    unittest.main()
