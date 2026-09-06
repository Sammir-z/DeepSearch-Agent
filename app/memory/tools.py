"""Explicit Agent tools for managing the current user's long-term memories."""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool

from app.api.monitor import monitor
from app.memory.context import get_user_id_context
from app.memory.user_memory import (
    MemoryStorageUnavailableError,
    MemoryValidationError,
    UserMemoryService,
)


_memory_service: UserMemoryService | None = None


def configure_user_memory_service(service: UserMemoryService) -> None:
    """Configure the shared Redis Store service used by the Agent tools."""

    global _memory_service
    _memory_service = service


def _current_service() -> UserMemoryService:
    if _memory_service is None:
        raise MemoryStorageUnavailableError("记忆服务尚未初始化")
    return _memory_service


def _current_user_id() -> str | None:
    return get_user_id_context()


@tool
def remember_user_memory(
    memory_type: Annotated[
        str, "记忆类型，只能是 preference、fact 或 constraint"
    ],
    content: Annotated[str, "需要长期保存的用户偏好或事实，不要包含密码、令牌等敏感信息"],
    source: Annotated[str, "记忆来源，通常填写 explicit_user_request"] = "explicit_user_request",
) -> str:
    """Explicitly save a small, non-sensitive memory for the authenticated user."""

    user_id = _current_user_id()
    if not user_id:
        return "当前任务没有认证用户，无法保存长期记忆。"
    try:
        record = _current_service().remember(
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            source=source,
        )
    except MemoryValidationError as error:
        return f"长期记忆未保存：{error}"
    except MemoryStorageUnavailableError as error:
        monitor.report_memory_save_failed("write", type(error).__name__)
        return "长期记忆保存失败，本次不会声称已记住。"
    return f"已保存长期记忆，key={record.key}。"


@tool
def forget_user_memory(
    memory_key: Annotated[str, "remember_user_memory 返回的记忆 key"],
) -> str:
    """Explicitly delete one memory belonging to the authenticated user."""

    user_id = _current_user_id()
    if not user_id:
        return "当前任务没有认证用户，无法删除长期记忆。"
    try:
        deleted = _current_service().forget(user_id, memory_key)
    except MemoryValidationError as error:
        return f"长期记忆未删除：{error}"
    except MemoryStorageUnavailableError as error:
        monitor.report_memory_delete_failed("delete", type(error).__name__)
        return "长期记忆删除失败。"
    if not deleted:
        return "没有找到对应的长期记忆。"
    return "长期记忆已删除。"


@tool
def search_user_memories(
    query: Annotated[str, "要匹配的记忆内容、类型或来源；留空表示列出全部"] = "",
    limit: Annotated[int, "最多返回的记忆条数，范围 1-20"] = 10,
) -> str:
    """List or search memories belonging only to the authenticated user."""

    user_id = _current_user_id()
    if not user_id:
        return "当前任务没有认证用户，无法查询长期记忆。"
    try:
        records = _current_service().search(user_id, query=query, limit=limit)
    except MemoryValidationError as error:
        return f"长期记忆查询失败：{error}"
    except MemoryStorageUnavailableError as error:
        monitor.report_memory_unavailable("read", type(error).__name__)
        return "本次未加载长期记忆，任务将继续。"
    return json.dumps(
        [record.as_dict() for record in records], ensure_ascii=False
    )
