"""Coroutine-local identity context used by Agent memory tools."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional, TypedDict


_user_id_ctx: ContextVar[Optional[str]] = ContextVar("memory_user_id", default=None)


class AgentRuntimeContext(TypedDict):
    """Immutable context schema passed to each DeepAgent run."""

    user_id: str | None


def set_user_id_context(user_id: str | None) -> Token[Optional[str]]:
    """Bind the authenticated user to the current Agent execution context."""

    return _user_id_ctx.set(user_id)


def get_user_id_context() -> str | None:
    """Return the authenticated user bound to the current coroutine, if any."""

    return _user_id_ctx.get()


def reset_user_id_context(token: Token[Optional[str]]) -> None:
    """Restore the previous user identity after an Agent task finishes."""

    _user_id_ctx.reset(token)
