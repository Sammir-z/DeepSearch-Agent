"""Authentication, opaque sessions, and Redis-backed login throttling."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request, Response, WebSocket
from mysql.connector import Error
from pwdlib import PasswordHash
from redis import Redis, RedisError

from app.memory.auth_store import (
    AuthStore,
    DuplicateUserError,
    StoredUser,
    StoredThread,
    ThreadOwnershipError,
)


SESSION_COOKIE_NAME = "deepsearch_session"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class AuthServiceError(Exception):
    """Base class for expected authentication failures."""


class UserAlreadyExistsError(AuthServiceError):
    """Raised when an email is already registered."""


class InvalidCredentialsError(AuthServiceError):
    """Raised without revealing whether the email or password was wrong."""


class LoginRateLimitedError(AuthServiceError):
    """Raised after too many failed login attempts in the configured window."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("登录失败次数过多，请稍后重试")
        self.retry_after = retry_after


class AuthStorageUnavailableError(AuthServiceError):
    """Raised when MySQL or Redis cannot serve an authentication operation."""


@dataclass(frozen=True)
class AuthUser:
    """Public user identity exposed to API handlers."""

    id: str
    email: str
    status: str

    @classmethod
    def from_record(cls, record: StoredUser) -> "AuthUser":
        return cls(id=record.id, email=record.email, status=record.status)

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "email": self.email, "status": self.status}


def normalize_email(email: str) -> str:
    """Normalize and minimally validate an email used as the login identifier."""

    normalized = email.strip().lower()
    if len(normalized) > 320 or not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("邮箱格式无效")
    return normalized


def validate_password(password: str) -> str:
    """Apply the V1 password length policy before hashing."""

    if len(password) < 8:
        raise ValueError("密码长度不能少于 8 位")
    if len(password) > 128:
        raise ValueError("密码长度不能超过 128 位")
    return password


def hash_session_token(token: str) -> str:
    """Store only a deterministic digest of the raw browser session token."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_session_token(request: Request) -> str | None:
    """Read a session token from HttpOnly cookie or an Authorization header."""

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    return request.cookies.get(SESSION_COOKIE_NAME)


def extract_websocket_session_token(websocket: WebSocket) -> str | None:
    """Read the same session token forms from a WebSocket handshake."""

    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    return websocket.cookies.get(SESSION_COOKIE_NAME)


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """Set a local-development-safe HttpOnly session cookie."""

    secure = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
    same_site = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
    if same_site not in {"lax", "strict", "none"}:
        same_site = "lax"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite=same_site,
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the browser session cookie on logout."""

    response.delete_cookie(key=SESSION_COOKIE_NAME)


class AuthService:
    """Coordinate MySQL identity records with Redis session controls."""

    def __init__(
        self,
        store: AuthStore | None = None,
        redis_client: Redis | None = None,
    ) -> None:
        self.store = store or AuthStore()
        self.redis = redis_client
        self.password_hash = PasswordHash.recommended()
        self._dummy_password_hash = self.password_hash.hash(
            "deepsearch-invalid-password"
        )
        self.session_ttl_seconds = int(
            os.getenv("AUTH_SESSION_TTL_SECONDS", str(7 * 24 * 60 * 60))
        )
        self.login_failure_limit = int(
            os.getenv("AUTH_LOGIN_FAILURE_LIMIT", "5")
        )
        self.login_failure_window_seconds = int(
            os.getenv("AUTH_LOGIN_FAILURE_WINDOW_SECONDS", "900")
        )

    def setup(self) -> None:
        """Create the authentication tables before the API accepts traffic."""

        try:
            self.store.setup()
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def register(self, email: str, password: str) -> AuthUser:
        normalized_email = normalize_email(email)
        password = validate_password(password)
        password_hash = self.password_hash.hash(password)
        try:
            record = self.store.create_user(normalized_email, password_hash)
            self.store.record_audit(record.id, "register")
        except DuplicateUserError as error:
            raise UserAlreadyExistsError from error
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error
        return AuthUser.from_record(record)

    def login(
        self,
        email: str,
        password: str,
        client_ip: str | None = None,
    ) -> tuple[str, AuthUser]:
        normalized_email = normalize_email(email)
        password = validate_password(password)
        rate_key = self._rate_key(normalized_email, client_ip or "unknown")
        self._check_login_rate(rate_key)

        try:
            record = self.store.get_user_by_email(normalized_email)
            password_matches = self.password_hash.verify(
                password,
                record.password_hash if record else self._dummy_password_hash,
            )
            if not record or record.status != "active" or not password_matches:
                self._record_login_failure(rate_key)
                raise InvalidCredentialsError("邮箱或密码错误")

            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                seconds=self.session_ttl_seconds
            )
            self.store.create_session(hash_session_token(token), record.id, expires_at)
            self.store.record_audit(record.id, "login")
            self._clear_login_failures(rate_key)
        except InvalidCredentialsError:
            raise
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

        return token, AuthUser.from_record(record)

    def authenticate(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        try:
            session = self.store.get_active_session(hash_session_token(token))
            if not session:
                return None
            record = self.store.get_user_by_id(session.user_id)
            if not record or record.status != "active":
                return None
            return AuthUser.from_record(record)
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hash_session_token(token)
        try:
            session = self.store.get_active_session(token_hash)
            self.store.revoke_session(token_hash)
            if session:
                self.store.record_audit(session.user_id, "logout")
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def ensure_thread_owner(self, thread_id: str, user_id: str) -> None:
        if not THREAD_ID_PATTERN.fullmatch(thread_id):
            raise ValueError("thread_id 格式无效")
        try:
            self.store.ensure_thread_owner(thread_id, user_id)
        except ThreadOwnershipError:
            raise
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def thread_belongs_to_user(self, thread_id: str, user_id: str) -> bool:
        try:
            return self.store.get_thread_owner(thread_id) == user_id
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def list_threads(self, user_id: str) -> list[StoredThread]:
        """List the authenticated user's recent Agent threads."""

        try:
            return self.store.list_threads(user_id)
        except (Error, OSError, ValueError) as error:
            raise AuthStorageUnavailableError("认证数据库不可用") from error

    def _rate_key(self, email: str, client_ip: str) -> str:
        digest = hashlib.sha256(f"{email}\0{client_ip}".encode("utf-8")).hexdigest()
        return f"auth:login-fail:{digest}"

    def _check_login_rate(self, rate_key: str) -> None:
        if not self.redis:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用")
        try:
            count = int(self.redis.get(rate_key) or 0)
        except RedisError as error:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用") from error
        if count >= self.login_failure_limit:
            raise LoginRateLimitedError(self.login_failure_window_seconds)

    def _record_login_failure(self, rate_key: str) -> None:
        if not self.redis:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用")
        try:
            count = self.redis.incr(rate_key)
            if count == 1:
                self.redis.expire(rate_key, self.login_failure_window_seconds)
        except RedisError as error:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用") from error

    def _clear_login_failures(self, rate_key: str) -> None:
        if not self.redis:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用")
        try:
            self.redis.delete(rate_key)
        except RedisError as error:
            raise AuthStorageUnavailableError("登录限流 Redis 不可用") from error


__all__ = [
    "AuthService",
    "AuthServiceError",
    "AuthStorageUnavailableError",
    "AuthUser",
    "InvalidCredentialsError",
    "LoginRateLimitedError",
    "SESSION_COOKIE_NAME",
    "UserAlreadyExistsError",
    "clear_session_cookie",
    "extract_session_token",
    "extract_websocket_session_token",
    "hash_session_token",
    "normalize_email",
    "set_session_cookie",
    "validate_password",
]
