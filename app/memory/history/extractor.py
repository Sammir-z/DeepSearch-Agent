"""Convert LangGraph checkpoint messages into a safe chat-history read model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ChatHistoryTurn:
    """One user prompt and the final assistant text associated with it."""

    id: str
    user_content: str
    assistant_content: str
    timestamp: str | None = None


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def _message_role(message: Any) -> str | None:
    role = _message_value(message, "role")
    if role in {"user", "assistant"}:
        return role

    message_type = _message_value(message, "type")
    if message_type == "human":
        return "user"
    if message_type == "ai":
        return "assistant"
    return None


def _content_to_text(content: Any) -> str:
    """Extract text blocks while ignoring tool-call-only content."""

    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, (list, tuple)):
        return ""

    text_parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        block_text = block.get("text")
        if isinstance(block_text, str) and (
            block_type in {None, "text", "input_text", "output_text"}
        ):
            text_parts.append(block_text)
    return "".join(text_parts).strip()


def _clean_user_content(content: str) -> str:
    """Hide runtime-only path instructions appended by ``run_deep_agent``."""

    marker = "【工作环境指令】"
    if marker in content:
        content = content.split(marker, 1)[0]
    return content.strip()


def extract_chat_history(
    state: object,
    fallback_timestamp: str | None = None,
) -> list[ChatHistoryTurn]:
    """Extract durable user/assistant turns from a LangGraph state snapshot.

    Tool messages and assistant messages that contain only tool calls are not
    returned. If a checkpoint ends after a user message, that user message is
    retained with an empty assistant response so a later run can continue it.
    """

    values = _message_value(state, "values", state)
    messages = values.get("messages", []) if isinstance(values, Mapping) else []
    if not isinstance(messages, (list, tuple)):
        return []

    turns: list[ChatHistoryTurn] = []
    pending_user: tuple[int, str] | None = None
    pending_assistant = ""

    def flush_pending() -> None:
        nonlocal pending_user, pending_assistant
        if pending_user is None:
            return
        message_index, user_content = pending_user
        turns.append(
            ChatHistoryTurn(
                id=f"turn-{message_index}",
                user_content=user_content,
                assistant_content=pending_assistant,
                timestamp=fallback_timestamp,
            )
        )
        pending_user = None
        pending_assistant = ""

    for index, message in enumerate(messages):
        role = _message_role(message)
        if role == "user":
            flush_pending()
            user_content = _clean_user_content(
                _content_to_text(_message_value(message, "content", ""))
            )
            if user_content:
                pending_user = (index, user_content)
            continue

        if role == "assistant" and pending_user is not None:
            assistant_content = _content_to_text(
                _message_value(message, "content", "")
            )
            if assistant_content:
                # The final non-empty assistant message is the answer after
                # any intermediate tool-call messages.
                pending_assistant = assistant_content

    flush_pending()
    return turns
