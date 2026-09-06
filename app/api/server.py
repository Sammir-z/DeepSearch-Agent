"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    status,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, Field

from app.agent.main_agent import (
    initialize_main_agent,
    redis_persistence,
    run_deep_agent,
    user_memory_service,
)
from app.api.monitor import manager
from app.memory import (
    AuthService,
    AuthStorageUnavailableError,
    AuthUser,
    InvalidCredentialsError,
    LoginRateLimitedError,
    MemoryStorageUnavailableError,
    MemoryValidationError,
    RedisPersistenceUnavailableError,
    UserAlreadyExistsError,
    clear_session_cookie,
    extract_session_token,
    extract_websocket_session_token,
    set_session_cookie,
)
from app.memory.auth_store import StoredThread, ThreadOwnershipError
from app.memory.history import (
    HistoryStorageUnavailableError,
    load_thread_history,
)
from app.memory.observability import log_event


auth_service = AuthService(redis_client=redis_persistence.client)


def get_frontend_url() -> str:
    """Resolve the browser URL shown in the backend startup log."""

    configured_url = os.getenv("FRONTEND_URL", "").strip()
    if configured_url:
        return configured_url.rstrip("/")

    frontend_port = os.getenv("FRONTEND_PORT", "5173").strip() or "5173"
    return f"http://localhost:{frontend_port}"


def log_frontend_url() -> None:
    """Print the local frontend address once when the API starts."""

    logging.getLogger("uvicorn.error").info("前端访问地址: %s", get_frontend_url())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    服务生命周期入口。

    启动时绑定当前事件循环到 WebSocket 管理器，确保后台 Agent 任务可以把
    monitor 事件投递回 FastAPI 所在的 loop。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    log_frontend_url()
    try:
        try:
            await redis_persistence.setup()
            if redis_persistence._setup_complete:
                await initialize_main_agent()
        except RedisPersistenceUnavailableError as error:
            # Keep the API alive so task endpoints can return an explicit 503
            # while Redis is booting or recovering. Never use an in-memory saver.
            log_event(
                "redis_persistence_unavailable",
                error_type=type(error).__name__,
                level=logging.WARNING,
            )
        try:
            auth_service.setup()
        except AuthStorageUnavailableError as error:
            # Authentication and ownership routes perform their own MySQL
            # checks and will return 503 until the database is reachable.
            log_event(
                "mysql_auth_storage_unavailable",
                error_type=type(error).__name__,
                level=logging.WARNING,
            )
        yield
    finally:
        await redis_persistence.close()


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(title="DeepAgents API", lifespan=lifespan)


@app.get("/health/live")
def health_live():
    """Return a dependency-free liveness response for container health checks."""

    return {"status": "ok"}


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Record bounded request metrics without logging request bodies or cookies."""

    started_at = time.perf_counter()
    status_code: int | None = None
    error_type: str | None = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        if status_code >= 400:
            error_type = f"HTTP_{status_code}"
        return response
    except Exception as error:
        error_type = type(error).__name__
        raise
    finally:
        thread_id = getattr(request.state, "thread_id", None)
        log_event(
            "http_request",
            user_id=getattr(request.state, "user_id", None),
            thread_id=thread_id,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            error_type=error_type,
            status_code=status_code,
            method=request.method,
            path=request.url.path,
            level=logging.WARNING if error_type else logging.INFO,
        )

# 保存 thread_id -> 后台 Agent 任务，用于同一会话任务替换和主动取消
active_tasks: dict[str, asyncio.Task] = {}

# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件，run_deep_agent 启动时会复制到对应 output/session_xxx
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 前端使用 Cookie 会话时必须返回具体 Origin，不能在 allow_credentials=True 时使用通配符。
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    """前端启动任务时提交的请求体。"""

    query: str
    thread_id: str | None = None


class AuthRequest(BaseModel):
    """Registration and login payload."""

    email: str
    password: str


class UserResponse(BaseModel):
    """Public user representation; password fields are never returned."""

    id: str
    email: str
    status: str


class MemoryCreateRequest(BaseModel):
    """Payload for an explicit user memory write."""

    type: str = Field(validation_alias=AliasChoices("type", "memory_type"))
    content: str
    source: str = "explicit_user_request"


class MemoryResponse(BaseModel):
    """Public representation of a validated user memory."""

    key: str
    type: str
    content: str
    source: str
    confidence: float
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    """Memory list response scoped to the current authenticated user."""

    memories: list[MemoryResponse]


class ThreadResponse(BaseModel):
    """Thread summary returned to its authenticated owner."""

    thread_id: str
    title: str | None
    status: str
    created_at: str
    last_used_at: str


class ThreadListResponse(BaseModel):
    """Thread list response scoped to the current authenticated user."""

    threads: list[ThreadResponse]


class HistoryTurnResponse(BaseModel):
    """A persisted user prompt and assistant answer."""

    id: str
    user_content: str
    assistant_content: str
    timestamp: str | None = None


class ThreadHistoryResponse(BaseModel):
    """Durable chat history returned only to the thread owner."""

    thread_id: str
    session_path: str | None = None
    turns: list[HistoryTurnResponse]


def _user_response(user: AuthUser) -> dict[str, str]:
    return user.as_dict()


def get_current_user(request: Request) -> AuthUser:
    """Resolve the authenticated user from the server-side session."""

    try:
        user = auth_service.authenticate(extract_session_token(request))
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user_id = user.id
    return user


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _mark_request_context(
    request: Request,
    user: AuthUser,
    thread_id: str | None = None,
) -> None:
    """Expose safe identity fields to the request observability middleware."""

    request.state.user_id = user.id
    if thread_id:
        request.state.thread_id = thread_id


async def _ensure_redis_persistence() -> None:
    """Reject task creation when the durable Redis checkpointer is unavailable."""

    try:
        await redis_persistence.ensure_available()
        await initialize_main_agent()
    except RedisPersistenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis 持久化服务不可用，任务未启动",
        ) from error


def _ensure_thread_owner(thread_id: str, user_id: str) -> None:
    try:
        auth_service.ensure_thread_owner(thread_id, user_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ThreadOwnershipError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


def _require_existing_thread(thread_id: str, user_id: str) -> None:
    try:
        owns_thread = auth_service.thread_belongs_to_user(thread_id, user_id)
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if not owns_thread:
        raise HTTPException(status_code=404, detail="会话不存在")


def _thread_id_from_output_path(path: Path) -> str | None:
    try:
        relative = path.relative_to(output_dir.resolve())
    except ValueError:
        return None
    if not relative.parts:
        return None
    first_part = relative.parts[0]
    if not first_part.startswith("session_"):
        return None
    return first_part.removeprefix("session_")


def _resolve_output_path(path: str, user: AuthUser, allow_root: bool = False) -> Path:
    try:
        absolute_path = Path(path).resolve()
        output_root = output_dir.resolve()
        if not absolute_path.is_relative_to(output_root):
            raise HTTPException(status_code=403, detail="只能访问输出目录下的文件")
        if absolute_path == output_root:
            if allow_root:
                return absolute_path
            raise HTTPException(status_code=403, detail="必须指定具体会话文件")
        thread_id = _thread_id_from_output_path(absolute_path)
        if not thread_id:
            raise HTTPException(status_code=403, detail="无效的会话路径")
        _require_existing_thread(thread_id, user.id)
        return absolute_path
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=400, detail="无效的路径参数") from error


@app.post(
    "/api/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: AuthRequest, request: Request):
    """Create a user account; registration does not expose a password or token."""

    try:
        user = auth_service.register(payload.email, payload.password)
    except UserAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="邮箱已注册") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    request.state.user_id = user.id
    return _user_response(user)


@app.post("/api/auth/login", response_model=UserResponse)
async def login(payload: AuthRequest, request: Request, response: Response):
    """Authenticate a user and issue a server-side opaque session cookie."""

    try:
        token, user = auth_service.login(
            payload.email,
            payload.password,
            client_ip=_client_ip(request),
        )
    except LoginRateLimitedError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    set_session_cookie(response, token, auth_service.session_ttl_seconds)
    request.state.user_id = user.id
    return _user_response(user)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Revoke the current session and remove its browser cookie."""

    try:
        auth_service.logout(extract_session_token(request))
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    clear_session_cookie(response)
    return {"status": "logged_out"}


@app.get("/api/auth/me", response_model=UserResponse)
async def current_user(user: AuthUser = Depends(get_current_user)):
    """Return the authenticated user's public identity."""

    return _user_response(user)


def _thread_response(thread: StoredThread) -> dict[str, str | None]:
    def serialize(value: object) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return {
        "thread_id": thread.thread_id,
        "title": thread.title,
        "status": thread.status,
        "created_at": serialize(thread.created_at),
        "last_used_at": serialize(thread.last_used_at),
    }


@app.get("/api/threads", response_model=ThreadListResponse)
async def list_threads(user: AuthUser = Depends(get_current_user)):
    """Return only the current user's recent Agent threads."""

    try:
        threads = auth_service.list_threads(user.id)
    except AuthStorageUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return {"threads": [_thread_response(thread) for thread in threads]}


@app.get(
    "/api/threads/{thread_id}/history",
    response_model=ThreadHistoryResponse,
)
async def get_thread_history(
    thread_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """Return user prompts and final assistant answers from the latest checkpoint."""

    _mark_request_context(request, user, thread_id)
    _require_existing_thread(thread_id, user.id)
    await _ensure_redis_persistence()

    try:
        agent = await initialize_main_agent()
        turns = await load_thread_history(agent, thread_id)
    except HistoryStorageUnavailableError as error:
        log_event(
            "chat_history_read_failed",
            user_id=user.id,
            thread_id=thread_id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天记录暂时无法加载",
        ) from error

    session_dir = output_dir / f"session_{thread_id}"
    session_path = str(session_dir) if session_dir.exists() else None
    return {
        "thread_id": thread_id,
        "session_path": session_path,
        "turns": [
            {
                "id": turn.id,
                "user_content": turn.user_content,
                "assistant_content": turn.assistant_content,
                "timestamp": turn.timestamp,
            }
            for turn in turns
        ],
    }


@app.post(
    "/api/memory",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def remember_memory(
    payload: MemoryCreateRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Explicitly save one validated memory for the authenticated user."""

    try:
        record = user_memory_service.remember(
            user_id=user.id,
            memory_type=payload.type,
            content=payload.content,
            source=payload.source,
        )
    except MemoryValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MemoryStorageUnavailableError as error:
        log_event(
            "memory_store_write_failed",
            user_id=user.id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return record.as_dict()


@app.get("/api/memory", response_model=MemoryListResponse)
async def list_memories(
    query: str | None = None,
    limit: int = 20,
    user: AuthUser = Depends(get_current_user),
):
    """List or text-filter memories in the current user's namespace."""

    try:
        records = user_memory_service.search(user.id, query=query, limit=limit)
    except MemoryValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MemoryStorageUnavailableError as error:
        log_event(
            "memory_store_read_failed",
            user_id=user.id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return {"memories": [record.as_dict() for record in records]}


@app.delete("/api/memory/{memory_key}")
async def forget_memory(
    memory_key: str,
    user: AuthUser = Depends(get_current_user),
):
    """Delete one memory, limited to the authenticated user's namespace."""

    try:
        deleted = user_memory_service.forget(user.id, memory_key)
    except MemoryValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MemoryStorageUnavailableError as error:
        log_event(
            "memory_store_delete_failed",
            user_id=user.id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="长期记忆不存在")
    return {"status": "deleted", "key": memory_key}


def _forget_task(thread_id: str, task: asyncio.Task) -> None:
    """
    清理已结束任务的登记关系。

    done_callback 触发时，active_tasks 中可能已经被新任务替换；只有仍是同一个
    task 时才删除，避免误清理同 thread_id 下刚启动的新任务。
    """
    if active_tasks.get(thread_id) is task:
        active_tasks.pop(thread_id, None)


@app.post("/api/task")
async def run_task(
    request: TaskRequest,
    http_request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """
    启动一次 DeepAgents 后台任务。

    HTTP 请求只负责创建后台协程并立即返回，后续执行轨迹、子智能体调用和最终
    答案都会由 monitor 通过 `/ws/{thread_id}` 推送给同一会话的前端。
    """
    await _ensure_redis_persistence()
    thread_id = request.thread_id or str(uuid.uuid4())
    _mark_request_context(http_request, user, thread_id)
    _ensure_thread_owner(thread_id, user.id)

    # 同一个 thread_id 只保留一个活跃任务，新任务会先取消旧任务，避免并发写同一会话目录
    old_task = active_tasks.get(thread_id)
    if old_task and not old_task.done():
        old_task.cancel()

    # create_task 把长耗时 Agent 执行交给事件循环，接口本身不用等待最终结果
    task = asyncio.create_task(run_deep_agent(request.query, thread_id, user.id))
    active_tasks[thread_id] = task
    task.add_done_callback(lambda finished_task: _forget_task(thread_id, finished_task))

    return {"status": "started", "thread_id": thread_id}


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(
    thread_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """
    取消指定 thread_id 对应的后台 Agent 任务。

    注意：取消会向 asyncio.Task 注入 CancelledError。若底层第三方工具正在执行不可中断
    的同步阻塞调用，任务可能需要等该调用返回后才会真正结束。
    """
    _mark_request_context(request, user, thread_id)
    _require_existing_thread(thread_id, user.id)
    task = active_tasks.get(thread_id)
    if not task or task.done():
        active_tasks.pop(thread_id, None)
        raise HTTPException(status_code=404, detail="任务不存在或已结束")

    # 先发出取消信号，再短暂等待协程响应；若底层阻塞中，则返回 cancelling 给前端继续展示状态
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id}
    except asyncio.TimeoutError:
        return {"status": "cancelling", "thread_id": thread_id}
    except Exception as e:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id, "message": str(e)}

    _forget_task(thread_id, task)
    return {"status": "cancelled", "thread_id": thread_id}


@app.post("/api/upload")
async def upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    thread_id: str = Form(...),
    user: AuthUser = Depends(get_current_user),
):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    _mark_request_context(request, user, thread_id)
    _ensure_thread_owner(thread_id, user.id)
    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file in files:
        filename = Path(file.filename or "").name
        if not filename or filename in {".", ".."}:
            raise HTTPException(status_code=400, detail="文件名无效")
        file_path = target_dir / filename
        # 直接复制文件流，避免大文件一次性读入内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(
    path: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    abs_path = _resolve_output_path(path, user)
    thread_id = _thread_id_from_output_path(abs_path)
    if thread_id:
        _mark_request_context(request, user, thread_id)

    if not abs_path.exists():
        return {"error": "文件不存在"}

    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(
    path: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、修改时间、下载所需路径）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    abs_path = _resolve_output_path(path, user, allow_root=True)
    thread_id = _thread_id_from_output_path(abs_path)
    if thread_id:
        _mark_request_context(request, user, thread_id)

    if not abs_path.exists():
        return {"error": "目录不存在"}

    output_abs = output_dir.resolve()
    if abs_path == output_abs:
        visible_roots = []
        for child in abs_path.iterdir():
            thread_id = _thread_id_from_output_path(child)
            if not thread_id:
                continue
            try:
                if auth_service.thread_belongs_to_user(thread_id, user.id):
                    visible_roots.append(child)
            except AuthStorageUnavailableError as error:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(error),
                ) from error
    else:
        visible_roots = [abs_path]

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for visible_root in visible_roots:
            for file_path in visible_root.rglob("*"):
                if file_path.is_file():
                    stat = file_path.stat()
                    # mtime 修改时间，用于排序
                    files.append(
                        {
                            "name": file_path.name,
                            "type": "file",
                            "path": str(file_path),
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        }
                    )

    except Exception as error:
        log_event(
            "file_listing_failed",
            user_id=user.id,
            thread_id=thread_id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        return {"error": "文件列表暂时不可用"}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。
    """
    started_at = time.perf_counter()
    user: AuthUser | None = None
    try:
        user = auth_service.authenticate(extract_websocket_session_token(websocket))
        if not user:
            log_event(
                "websocket_rejected",
                thread_id=thread_id,
                error_type="Unauthenticated",
                level=logging.WARNING,
            )
            await websocket.close(code=1008, reason="请先登录")
            return
        _ensure_thread_owner(thread_id, user.id)
    except HTTPException:
        log_event(
            "websocket_rejected",
            user_id=user.id if user else None,
            thread_id=thread_id,
            error_type="ThreadOwnershipError",
            level=logging.WARNING,
        )
        await websocket.close(code=1008, reason="会话不存在")
        return
    except AuthStorageUnavailableError as error:
        log_event(
            "websocket_rejected",
            thread_id=thread_id,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        await websocket.close(code=1013, reason="认证服务暂不可用")
        return

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, thread_id)
    log_event("websocket_accepted", user_id=user.id, thread_id=thread_id)

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", 
                 "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, thread_id)
        log_event(
            "websocket_closed",
            user_id=user.id,
            thread_id=thread_id,
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )

    except Exception as error:
        log_event(
            "websocket_failed",
            user_id=user.id,
            thread_id=thread_id,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            error_type=type(error).__name__,
            level=logging.WARNING,
        )
        manager.disconnect(websocket, thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
