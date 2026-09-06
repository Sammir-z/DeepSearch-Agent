"""Async checkpoint access for authenticated chat-history requests."""

from __future__ import annotations

from typing import Any

from app.memory.history.extractor import ChatHistoryTurn, extract_chat_history


class HistoryStorageUnavailableError(RuntimeError):
    """Raised when the Redis checkpoint cannot be read."""


async def load_thread_history(
    agent: Any,
    thread_id: str,
    fallback_timestamp: str | None = None,
) -> list[ChatHistoryTurn]:
    """Read one thread's latest state through the compiled async Agent graph."""

    try:
        state = await agent.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
    except Exception as error:
        raise HistoryStorageUnavailableError("聊天记录暂时无法加载") from error

    return extract_chat_history(state, fallback_timestamp=fallback_timestamp)
