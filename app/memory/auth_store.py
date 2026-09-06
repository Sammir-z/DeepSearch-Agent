"""MySQL persistence for users, sessions, thread ownership, and audit events."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import find_dotenv, load_dotenv
from mysql.connector import Error, IntegrityError, connect

load_dotenv(find_dotenv())


SCHEMA_PATH = Path(__file__).with_name("auth_schema.sql")


def get_auth_db_config() -> dict[str, Any]:
    """Read the same MySQL environment contract used by the database tools."""

    config: dict[str, Any] = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }
    config = {key: value for key, value in config.items() if value is not None}

    required_keys = ("user", "password", "database")
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    return config


@dataclass(frozen=True)
class StoredUser:
    """User fields safe to return to API callers."""

    id: str
    email: str
    password_hash: str
    status: str


@dataclass(frozen=True)
class StoredSession:
    """An active, unexpired database session."""

    token_hash: str
    user_id: str
    expires_at: Any


@dataclass(frozen=True)
class StoredThread:
    """Thread summary owned by one authenticated user."""

    thread_id: str
    title: str | None
    status: str
    created_at: Any
    last_used_at: Any


class ThreadOwnershipError(Exception):
    """Raised when a thread belongs to another user."""


class DuplicateUserError(Exception):
    """Raised when a user email violates the unique constraint."""


class AuthStore:
    """Small per-operation MySQL repository used by the authentication service."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        schema_path: Path = SCHEMA_PATH,
    ) -> None:
        self.config = config or get_auth_db_config()
        self.schema_path = schema_path

    def _connect(self):
        return connect(**self.config)

    def setup(self) -> None:
        """Create authentication tables idempotently."""

        schema = self.schema_path.read_text(encoding="utf-8")
        statements = [
            statement.strip()
            for statement in schema.split(";")
            if statement.strip()
        ]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            connection.commit()

    def create_user(self, email: str, password_hash: str) -> StoredUser:
        user = StoredUser(
            id=str(uuid4()),
            email=email,
            password_hash=password_hash,
            status="active",
        )
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO users (id, email, password_hash, status)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (user.id, user.email, user.password_hash, user.status),
                    )
                connection.commit()
        except IntegrityError as error:
            if getattr(error, "errno", None) == 1062:
                raise DuplicateUserError("邮箱已注册") from error
            raise
        return user

    def get_user_by_email(self, email: str) -> StoredUser | None:
        with self._connect() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    """
                    SELECT id, email, password_hash, status
                    FROM users
                    WHERE email = %s
                    LIMIT 1
                    """,
                    (email,),
                )
                row = cursor.fetchone()
        return StoredUser(**row) if row else None

    def get_user_by_id(self, user_id: str) -> StoredUser | None:
        with self._connect() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    """
                    SELECT id, email, password_hash, status
                    FROM users
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
        return StoredUser(**row) if row else None

    def create_session(
        self, token_hash: str, user_id: str, expires_at: Any
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO auth_sessions (token_hash, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (token_hash, user_id, expires_at),
                )
            connection.commit()

    def get_active_session(self, token_hash: str) -> StoredSession | None:
        with self._connect() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    """
                    SELECT token_hash, user_id, expires_at
                    FROM auth_sessions
                    WHERE token_hash = %s
                      AND revoked_at IS NULL
                      AND expires_at > UTC_TIMESTAMP(6)
                    LIMIT 1
                    """,
                    (token_hash,),
                )
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        """
                        UPDATE auth_sessions
                        SET last_seen_at = UTC_TIMESTAMP(6)
                        WHERE token_hash = %s
                        """,
                        (token_hash,),
                    )
            connection.commit()
        return StoredSession(**row) if row else None

    def revoke_session(self, token_hash: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = UTC_TIMESTAMP(6)
                    WHERE token_hash = %s AND revoked_at IS NULL
                    """,
                    (token_hash,),
                )
            connection.commit()

    def get_thread_owner(self, thread_id: str) -> str | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM agent_threads WHERE thread_id = %s",
                    (thread_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def ensure_thread_owner(self, thread_id: str, user_id: str) -> None:
        """Create a thread record or require that it already belongs to user_id."""

        owner = self.get_thread_owner(thread_id)
        if owner and owner != user_id:
            raise ThreadOwnershipError("会话不属于当前用户")
        if owner:
            self.touch_thread(thread_id)
            return

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_threads (thread_id, user_id)
                        VALUES (%s, %s)
                        """,
                        (thread_id, user_id),
                    )
                connection.commit()
        except IntegrityError as error:
            if getattr(error, "errno", None) != 1062:
                raise
            owner = self.get_thread_owner(thread_id)
            if owner != user_id:
                raise ThreadOwnershipError("会话不属于当前用户") from error

    def touch_thread(self, thread_id: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE agent_threads
                    SET last_used_at = UTC_TIMESTAMP(6)
                    WHERE thread_id = %s
                    """,
                    (thread_id,),
                )
            connection.commit()

    def list_threads(self, user_id: str) -> list[StoredThread]:
        """Return recent threads belonging only to ``user_id``."""

        with self._connect() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    """
                    SELECT thread_id, title, status, created_at, last_used_at
                    FROM agent_threads
                    WHERE user_id = %s
                    ORDER BY last_used_at DESC, created_at DESC
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [StoredThread(**row) for row in rows]

    def record_audit(
        self,
        user_id: str | None,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_events (user_id, event_type, metadata)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, event_type, metadata_json),
                )
            connection.commit()


__all__ = [
    "AuthStore",
    "StoredSession",
    "StoredThread",
    "StoredUser",
    "ThreadOwnershipError",
    "DuplicateUserError",
    "get_auth_db_config",
]
