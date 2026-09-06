"""
Agent 执行过程监控模块

负责把工具调用、子智能体调用、任务结果和会话目录等事件统一包装后推送给前端
在 Web 服务中优先通过 WebSocket 定向推送；在脚本调试场景中保留控制台输出
"""

import asyncio
import builtins
import datetime
import logging
from typing import Any, Optional

from fastapi import WebSocket

from app.api.context import get_thread_context
from app.memory.context import get_user_id_context
from app.memory.observability import log_event, safe_event_data


logger = logging.getLogger(__name__)


class ToolMonitor:
    """
    工具和助手调用的统一监控入口

    业务工具只需要导入全局 monitor，并调用 report_tool/report_assistant 等方法
    具体是通过 WebSocket 推送，还是输出到脚本运行时，由本类内部统一处理
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None
        return cls._instance

    def set_websocket_manager(self, manager: "ConnectionManager") -> None:
        """绑定 FastAPI WebSocket 连接管理器"""
        self.websocket_manager = manager

    def _emit(
        self,
        event_type: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        构造统一监控事件，并尝试推送到当前 thread_id 对应的前端连接

        :param event_type: 事件类型，例如 tool_start、assistant_call
        :param message: 面向前端展示的事件说明
        :param data: 附加结构化数据
        """
        event_data = data or {}
        # Tool arguments are telemetry only.  Redact them before they leave
        # the backend so a monitor stream cannot become a secret dump.
        if event_type == "tool_start":
            event_data = safe_event_data(event_data)

        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": event_data,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        thread_id = get_thread_context()
        user_id = get_user_id_context()
        event_level = logging.ERROR if event_type == "error" else logging.INFO
        if event_type in {"memory_unavailable", "memory_save_failed", "memory_delete_failed"}:
            event_level = logging.WARNING
        error_type = None
        if event_type in {"error", "memory_unavailable", "memory_save_failed", "memory_delete_failed"}:
            error_type = str(event_data.get("error_type") or event_type)
        log_event(
            "agent_event",
            user_id=user_id,
            thread_id=thread_id,
            error_type=error_type,
            level=event_level,
        )

        if self.websocket_manager:
            try:
                # thread_id 来自 ContextVar，确保事件只推给当前任务对应的前端连接
                manager_loop = self.websocket_manager.loop

                if manager_loop and thread_id:
                    self._send_to_websocket(payload, thread_id, manager_loop)
            except Exception as e:
                log_event(
                    "monitor_websocket_send_failed",
                    user_id=user_id,
                    thread_id=thread_id,
                    error_type=type(e).__name__,
                    level=logging.WARNING,
                )

        # DeepAgents 脚本调试时，如果运行时暴露了 stream_writer，也同步写入流式输出
        if hasattr(builtins, "runtime") and hasattr(builtins.runtime, "stream_writer"):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # Keep console output concise; the structured event above is the
        # authoritative record and contains no free-form message text.
        logger.debug("monitor event=%s", event_type)

    def _send_to_websocket(
        self,
        payload: dict[str, Any],
        thread_id: str,
        manager_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        将监控事件投递到 WebSocket 所在事件循环

        FastAPI 的 WebSocket 必须在创建它的事件循环中发送消息
        如果当前代码已经在同一个循环里，直接 create_task；否则使用线程安全投递
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        coroutine = self.websocket_manager.send_to_thread(payload, thread_id)
        # 后续也可以全部写成 asyncio.run_coroutine_threadsafe(), 此处是为了方便理解同loop和不同loop
        if current_loop and current_loop == manager_loop:
            # 已经在 WebSocket 所属事件循环中，直接创建异步任务即可
            current_loop.create_task(coroutine)
        else:
            # 不在同一个事件循环中时，线程安全地投递到 manager_loop
            asyncio.run_coroutine_threadsafe(coroutine, manager_loop)

    def report_tool(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告开始执行某个工具"""
        self._emit(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args},
        )

    def report_assistant(
        self,
        assistant_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告正在调用某个子智能体"""
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_result(self, result: str) -> None:
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_task_cancelled(self) -> None:
        """报告任务已被用户取消"""
        self._emit("task_cancelled", "任务已取消")

    def report_memory_unavailable(self, operation: str, error_type: str) -> None:
        """Warn that long-term memory could not be read for this task."""

        self._emit(
            "memory_unavailable",
            "本次未加载长期记忆，任务将继续。",
            {"operation": operation, "error_type": error_type},
        )

    def report_memory_save_failed(self, operation: str, error_type: str) -> None:
        """Tell the user that a requested memory write did not succeed."""

        self._emit(
            "memory_save_failed",
            "长期记忆保存失败，本次不会声称已记住。",
            {"operation": operation, "error_type": error_type},
        )

    def report_memory_delete_failed(self, operation: str, error_type: str) -> None:
        """Warn that an explicit memory deletion did not complete."""

        self._emit(
            "memory_delete_failed",
            "长期记忆删除失败。",
            {"operation": operation, "error_type": error_type},
        )

    def report_session_dir(self, path: str) -> None:
        """报告当前任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


monitor = ToolMonitor()


class ConnectionManager:
    """
    WebSocket 连接管理器

    active_connections 使用 thread_id 作为 key，保证监控事件只推送给对应任务的前端连接
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        # WebSocket 发送必须回到创建连接的事件循环，因此启动时需要显式绑定 loop
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定 FastAPI 主事件循环，并同步注册到 monitor"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        logger.debug("ConnectionManager bound to application event loop")

    async def connect(self, websocket: WebSocket, thread_id: str) -> None:
        """接受 WebSocket 连接，并按 thread_id 保存"""
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        log_event("websocket_connected", thread_id=thread_id)

    def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        """移除已经断开的 WebSocket 连接"""
        if self.active_connections.get(thread_id) is websocket:
            del self.active_connections[thread_id]
            log_event("websocket_disconnected", thread_id=thread_id)
        else:
            log_event(
                "websocket_stale_disconnect",
                thread_id=thread_id,
                error_type="stale_connection",
                level=logging.WARNING,
            )

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        """向指定 WebSocket 发送纯文本消息"""
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict[str, Any], thread_id: str) -> None:
        """向指定 thread_id 对应的前端连接发送 JSON 消息"""
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)


manager = ConnectionManager()
