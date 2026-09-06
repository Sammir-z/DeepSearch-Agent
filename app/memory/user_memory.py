"""Per-user long-term memory backed by the shared LangGraph Redis Store.

The first memory version intentionally keeps records structured and small.  It
does not require an embedding model: listing and filtering are performed inside
the user's Redis Store namespace after the records are loaded.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from langgraph.store.base import BaseStore


ALLOWED_MEMORY_TYPES = frozenset({"preference", "fact", "constraint"})
MEMORY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# These patterns deliberately target credential-like assignments and common
# secret formats.  Ordinary memories such as "I prefer private workspaces"
# are allowed, while values that look like credentials are never persisted.
_SENSITIVE_PATTERNS = (
    re.compile(
        r"(?i)(?:password|passwd|pwd|secret|api[ _-]?key|access[ _-]?token|"
        r"refresh[ _-]?token|bearer|authorization)\b\s*(?:is|为|是|:|=)"
    ),
    re.compile(r"(?:密码|口令|私钥|身份证|银行卡|信用卡)\s*(?:是|为|:|：|=)"),
    re.compile(r"(?i)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
)
_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_MAX_SCAN_RESULTS = 1000
_T = TypeVar("_T")


class MemoryValidationError(ValueError):
    """Raised when a memory violates the V1 storage policy."""


class MemoryStorageUnavailableError(RuntimeError):
    """Raised when Redis Store cannot complete a memory operation."""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _luhn_checksum(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def contains_sensitive_content(content: str) -> bool:
    """Return whether text resembles a credential or high-risk identifier."""

    if any(pattern.search(content) for pattern in _SENSITIVE_PATTERNS):
        return True

    for match in _CARD_PATTERN.finditer(content):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_checksum(digits):
            return True

    # Mainland Chinese resident identity numbers are 18 digits with a final
    # checksum character.  They are rejected regardless of checksum validity.
    return bool(re.search(r"(?<!\d)\d{17}[\dXx](?!\d)", content))


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Return the only namespace in which a user's memories may be stored."""

    if not isinstance(user_id, str) or not USER_ID_PATTERN.fullmatch(user_id):
        raise MemoryValidationError("用户身份无效")
    return ("memories", user_id)


@dataclass(frozen=True)
class UserMemory:
    """Serializable memory record returned by the service and API."""

    key: str
    type: str
    content: str
    source: str
    confidence: float
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_item(cls, key: str, value: Any) -> "UserMemory":
        if not isinstance(value, dict):
            raise MemoryValidationError("记忆记录格式无效")
        try:
            return cls(
                key=key,
                type=str(value["type"]),
                content=str(value["content"]),
                source=str(value.get("source", "unknown")),
                confidence=float(value.get("confidence", 1.0)),
                created_at=str(value["created_at"]),
                updated_at=str(value.get("updated_at", value["created_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryValidationError("记忆记录格式无效") from error


class UserMemoryService:
    """CRUD service that always scopes operations to ``("memories", user_id)``."""

    def __init__(self, store: BaseStore) -> None:
        self.store = store
        self.max_content_length = _env_int(
            "MEMORY_MAX_CONTENT_LENGTH", 2000, minimum=1, maximum=10000
        )
        self.max_results = _env_int(
            "MEMORY_MAX_RESULTS", 20, minimum=1, maximum=100
        )
        self.max_query_length = _env_int(
            "MEMORY_MAX_QUERY_LENGTH", 500, minimum=1, maximum=2000
        )

    def _storage(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except MemoryValidationError:
            raise
        except Exception as error:
            raise MemoryStorageUnavailableError("记忆存储暂不可用") from error

    def _validate_type(self, memory_type: str) -> str:
        if not isinstance(memory_type, str):
            raise MemoryValidationError("记忆类型无效")
        normalized = memory_type.strip().lower()
        if normalized not in ALLOWED_MEMORY_TYPES:
            allowed = ", ".join(sorted(ALLOWED_MEMORY_TYPES))
            raise MemoryValidationError(f"记忆类型必须是：{allowed}")
        return normalized

    def _validate_content(self, content: str) -> str:
        if not isinstance(content, str):
            raise MemoryValidationError("记忆内容必须是文本")
        normalized = content.strip()
        if not normalized:
            raise MemoryValidationError("记忆内容不能为空")
        if len(normalized) > self.max_content_length:
            raise MemoryValidationError(
                f"记忆内容不能超过 {self.max_content_length} 个字符"
            )
        if contains_sensitive_content(normalized):
            raise MemoryValidationError("记忆内容疑似包含敏感信息，不能保存")
        return normalized

    def _validate_source(self, source: str) -> str:
        if not isinstance(source, str):
            raise MemoryValidationError("记忆来源必须是文本")
        normalized = source.strip()
        if not normalized:
            raise MemoryValidationError("记忆来源不能为空")
        if len(normalized) > 128:
            raise MemoryValidationError("记忆来源不能超过 128 个字符")
        if contains_sensitive_content(normalized):
            raise MemoryValidationError("记忆来源疑似包含敏感信息，不能保存")
        return normalized

    def _validate_key(self, key: str) -> str:
        if not isinstance(key, str) or not MEMORY_KEY_PATTERN.fullmatch(key):
            raise MemoryValidationError("记忆 key 无效")
        return key

    def _validate_limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise MemoryValidationError("查询条数必须是整数")
        if limit < 1 or limit > self.max_results:
            raise MemoryValidationError(f"查询条数必须在 1-{self.max_results} 之间")
        return limit

    def _validate_query(self, query: str | None) -> str:
        if query is None:
            return ""
        if not isinstance(query, str):
            raise MemoryValidationError("查询内容必须是文本")
        normalized = query.strip()
        if len(normalized) > self.max_query_length:
            raise MemoryValidationError(
                f"查询内容不能超过 {self.max_query_length} 个字符"
            )
        return normalized.casefold()

    def remember(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        source: str = "explicit_user_request",
        key: str | None = None,
    ) -> UserMemory:
        namespace = memory_namespace(user_id)
        normalized_type = self._validate_type(memory_type)
        normalized_content = self._validate_content(content)
        normalized_source = self._validate_source(source)
        memory_key = self._validate_key(key) if key else uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        def write() -> UserMemory:
            existing = self.store.get(namespace, memory_key)
            created_at = now
            if existing is not None and isinstance(existing.value, dict):
                created_at = str(existing.value.get("created_at", now))
            value = {
                "type": normalized_type,
                "content": normalized_content,
                "source": normalized_source,
                "confidence": 1.0,
                "created_at": created_at,
                "updated_at": now,
            }
            # Structured records do not require an embedding model in V1.
            self.store.put(namespace, memory_key, value, index=False)
            return UserMemory.from_item(memory_key, value)

        return self._storage(write)

    def get(self, user_id: str, key: str) -> UserMemory | None:
        namespace = memory_namespace(user_id)
        memory_key = self._validate_key(key)

        def read() -> UserMemory | None:
            item = self.store.get(namespace, memory_key)
            if item is None:
                return None
            return UserMemory.from_item(memory_key, item.value)

        return self._storage(read)

    def search(
        self,
        user_id: str,
        query: str | None = None,
        limit: int = 20,
    ) -> list[UserMemory]:
        namespace = memory_namespace(user_id)
        normalized_query = self._validate_query(query)
        result_limit = self._validate_limit(limit)

        def read() -> list[UserMemory]:
            # query=None makes RedisStore enumerate the namespace.  Filtering
            # locally keeps this implementation independent of vector indexes.
            items = self.store.search(
                namespace,
                query=None,
                limit=max(_MAX_SCAN_RESULTS, result_limit),
            )
            records: list[UserMemory] = []
            for item in items:
                try:
                    record = UserMemory.from_item(item.key, item.value)
                except MemoryValidationError:
                    # Ignore malformed legacy records rather than exposing them
                    # through the API or crashing a user's complete list call.
                    continue
                if normalized_query:
                    haystack = " ".join(
                        (record.type, record.content, record.source)
                    ).casefold()
                    if normalized_query not in haystack:
                        continue
                records.append(record)

            records.sort(key=lambda record: record.updated_at, reverse=True)
            return records[:result_limit]

        return self._storage(read)

    def forget(self, user_id: str, key: str) -> bool:
        namespace = memory_namespace(user_id)
        memory_key = self._validate_key(key)

        def remove() -> bool:
            existed = self.store.get(namespace, memory_key) is not None
            self.store.delete(namespace, memory_key)
            return existed

        return self._storage(remove)
