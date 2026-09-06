"""Structured, redaction-safe observability helpers.

The API and Agent layers deliberately log only identifiers and measurements.
Request bodies, cookies, tokens, passwords, tool arguments, and memory text
must never be passed to this module.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any


LOGGER_NAME = "deepsearch.observability"
logger = logging.getLogger(LOGGER_NAME)


def _safe_identifier(value: str | None) -> str | None:
    """Keep log identifiers bounded without exposing arbitrary input."""

    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized[:128]


def _safe_duration(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, float(value)), 2)


def log_event(
    event: str,
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    duration_ms: float | int | None = None,
    error_type: str | None = None,
    status_code: int | None = None,
    method: str | None = None,
    path: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Write a small JSON event containing no free-form sensitive values."""

    payload: dict[str, Any] = {
        "event": _safe_identifier(event),
        "user_id": _safe_identifier(user_id),
        "thread_id": _safe_identifier(thread_id),
        "duration_ms": _safe_duration(duration_ms),
        "error_type": _safe_identifier(error_type),
        "status_code": status_code,
        "method": _safe_identifier(method),
        "path": _safe_identifier(path),
    }
    # Keep the schema stable and avoid noisy null fields in ordinary logs.
    payload = {key: value for key, value in payload.items() if value is not None}
    logger.log(level, "deepsearch_event %s", json.dumps(payload, ensure_ascii=False))


def safe_event_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return bounded monitor data suitable for tool-start telemetry.

    This helper is intentionally conservative.  It is used only for telemetry
    payloads, never for Agent tool inputs or task results.
    """

    if not data:
        return {}

    sensitive_names = {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "content",
        "query",
        "question",
        "instruction",
    }
    result: dict[str, Any] = {}
    for raw_key, raw_value in data.items():
        key = str(raw_key)[:64]
        key_normalized = key.lower().replace("-", "_").replace(" ", "_")
        if key_normalized in sensitive_names or any(
            marker in key_normalized
            for marker in ("content", "内容", "memory", "密码", "令牌", "token", "secret")
        ):
            result[key] = "[REDACTED]"
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            result[key] = str(raw_value)[:256] if isinstance(raw_value, str) else raw_value
        else:
            result[key] = f"<{type(raw_value).__name__}>"
    return result


__all__ = ["LOGGER_NAME", "log_event", "logger", "safe_event_data"]
