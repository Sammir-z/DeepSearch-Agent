import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cancelTask,
  listSessionFiles,
  listThreads,
  startTask,
  uploadSessionFiles
} from "../lib/api";
import { WS_BASE_URL } from "../lib/config";
import { getThreadHistory } from "../history";
import type { ChatHistoryTurn } from "../history";
import {
  clearStoredThreadId,
  createThreadId,
  storeThreadId
} from "../lib/thread";
import type {
  AuthUser,
  ConnectionState,
  MonitorMessage,
  OutputFile,
  SocketMessage,
  ThreadSummary,
  UploadedItem
} from "../types";

const MAX_EVENTS = 120;
const MAX_RECONNECT_DELAY_MS = 15000;

function extractString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function useDeepAgentSession(
  user: AuthUser | null,
  onAuthExpired: () => void
) {
  const userId = user?.id ?? null;
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  const reconnectAttemptsRef = useRef(0);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  const [threadId, setThreadId] = useState<string | null>(null);
  const [readyUserId, setReadyUserId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [history, setHistory] = useState<ChatHistoryTurn[]>([]);
  const [historyThreadId, setHistoryThreadId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("closed");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [sessionPath, setSessionPath] = useState("");
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastWarning, setLastWarning] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);
  const historyRequestRef = useRef(0);

  const clearSocketTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = undefined;
    }
  }, []);

  const clearWorkspace = useCallback(() => {
    historyRequestRef.current += 1;
    setHistory([]);
    setHistoryThreadId(null);
    setIsLoadingHistory(false);
    setEvents([]);
    setFiles([]);
    setSessionPath("");
    setResult("");
    setLastError("");
    setLastWarning("");
    setLastPongAt("");
    setUploadedItems([]);
    uploadedNameSetRef.current.clear();
    setIsRunning(false);
    setIsCancelling(false);
    setIsUploading(false);
  }, []);

  const refreshThreads = useCallback(async () => {
    if (!userId) {
      setThreads([]);
      return [];
    }

    try {
      const response = await listThreads();
      setThreads(response.threads);
      return response.threads;
    } catch (error) {
      if (isUnauthorized(error)) {
        onAuthExpired();
      }
      throw error;
    }
  }, [onAuthExpired, userId]);

  const loadThreadHistory = useCallback(
    async (targetThreadId: string) => {
      if (!userId) {
        return;
      }

      const requestId = ++historyRequestRef.current;
      setIsLoadingHistory(true);
      setHistory([]);
      setHistoryThreadId(null);
      setSessionPath("");

      try {
        const response = await getThreadHistory(targetThreadId);
        if (requestId !== historyRequestRef.current) {
          return;
        }
        setHistory(response.turns);
        setHistoryThreadId(targetThreadId);
        setSessionPath(response.session_path || "");
      } catch (error) {
        if (requestId !== historyRequestRef.current) {
          return;
        }
        if (isUnauthorized(error)) {
          onAuthExpired();
          return;
        }
        setHistory([]);
        setHistoryThreadId(targetThreadId);
        setSessionPath("");
        setLastWarning("聊天记录暂时无法加载");
      } finally {
        if (requestId === historyRequestRef.current) {
          setIsLoadingHistory(false);
        }
      }
    },
    [onAuthExpired, userId],
  );

  // A local thread id must never survive an identity change.  The next id is
  // selected exclusively from the authenticated user's backend thread list.
  useEffect(() => {
    let disposed = false;
    clearStoredThreadId();
    historyRequestRef.current += 1;
    setThreadId(null);
    setReadyUserId(null);
    setThreads([]);
    clearWorkspace();
    clearSocketTimers();
    socketRef.current?.close();
    socketRef.current = null;
    reconnectAttemptsRef.current = 0;

    if (!userId) {
      setIsLoadingThreads(false);
      setConnectionState("closed");
      return () => {
        disposed = true;
      };
    }

    setIsLoadingThreads(true);
    setConnectionState("connecting");
    refreshThreads()
      .then((loadedThreads) => {
        if (disposed) {
          return;
        }
        const nextThreadId = loadedThreads[0]?.thread_id || createThreadId();
        storeThreadId(nextThreadId);
        setThreadId(nextThreadId);
        setReadyUserId(userId);
        if (loadedThreads[0]) {
          void loadThreadHistory(nextThreadId);
        } else {
          setHistory([]);
          setHistoryThreadId(nextThreadId);
          setIsLoadingHistory(false);
        }
      })
      .catch((error: unknown) => {
        if (disposed || isUnauthorized(error)) {
          return;
        }
        setLastError(error instanceof Error ? error.message : "线程列表加载失败");
        // Keep the workspace usable for a transient list-read failure.  The
        // backend still performs the definitive ownership check on submit.
        const fallbackThreadId = createThreadId();
        storeThreadId(fallbackThreadId);
        setThreadId(fallbackThreadId);
        setReadyUserId(userId);
        setHistory([]);
        setHistoryThreadId(fallbackThreadId);
        setIsLoadingHistory(false);
      })
      .finally(() => {
        if (!disposed) {
          setIsLoadingThreads(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, [clearSocketTimers, clearWorkspace, loadThreadHistory, refreshThreads, userId]);

  useEffect(() => {
    let disposed = false;

    if (!userId || !threadId || readyUserId !== userId) {
      setConnectionState(userId ? "connecting" : "closed");
      return () => {
        disposed = true;
        clearSocketTimers();
      };
    }

    function scheduleReconnect(connect: () => void) {
      if (disposed) {
        return;
      }
      const attempt = reconnectAttemptsRef.current;
      reconnectAttemptsRef.current += 1;
      const delay = Math.min(1000 * 2 ** Math.min(attempt, 4), MAX_RECONNECT_DELAY_MS);
      setConnectionState("reconnecting");
      reconnectTimerRef.current = window.setTimeout(connect, delay);
    }

    function connect() {
      if (disposed || !userId || !threadId) {
        return;
      }
      clearSocketTimers();
      socketRef.current?.close();
      setConnectionState(reconnectAttemptsRef.current > 0 ? "reconnecting" : "connecting");

      // The backend session is an HttpOnly cookie.  Browsers attach it to this
      // backend-origin WebSocket handshake; no token or user_id is put in URL.
      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(threadId)}`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        reconnectAttemptsRef.current = 0;
        setConnectionState("connected");
        setLastError("");
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 25000);
      };

      socket.onmessage = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        try {
          const payload = JSON.parse(event.data) as SocketMessage;
          if (payload.type === "pong") {
            setLastPongAt(new Date().toISOString());
            return;
          }

          if (payload.type !== "monitor_event") {
            return;
          }

          setEvents((previous) => [...previous, payload].slice(-MAX_EVENTS));

          if (payload.event === "session_created") {
            const path = extractString(payload.data, "path");
            if (path) {
              setSessionPath(path);
            }
          }

          if (payload.event === "task_result") {
            const finalResult = extractString(payload.data, "result");
            setResult(finalResult || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "task_cancelled") {
            setResult((previous) => previous || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "error") {
            setLastError(payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (
            payload.event === "memory_unavailable" ||
            payload.event === "memory_save_failed" ||
            payload.event === "memory_delete_failed"
          ) {
            setLastWarning(payload.message);
          }
        } catch (error) {
          setLastError(error instanceof Error ? error.message : "WebSocket 消息解析失败");
        }
      };

      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) {
          setLastError("WebSocket 连接异常，正在准备重连");
        }
      };

      socket.onclose = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        socketRef.current = null;
        clearSocketTimers();
        if (disposed) {
          setConnectionState("closed");
          return;
        }
        if (event.code === 1008) {
          setConnectionState("closed");
          setLastError("登录状态已过期，请重新登录");
          onAuthExpired();
          return;
        }
        if (event.code === 1013) {
          setLastError("认证服务暂时不可用，正在重连");
        }
        scheduleReconnect(connect);
      };
    }

    connect();

    return () => {
      disposed = true;
      clearSocketTimers();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [clearSocketTimers, onAuthExpired, readyUserId, threadId, userId]);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    try {
      const response = await listSessionFiles(sessionPath);
      if (response.error) {
        throw new Error(response.error);
      }
      setFiles(response.files || []);
    } catch (error) {
      if (isUnauthorized(error)) {
        onAuthExpired();
      }
      throw error;
    }
  }, [onAuthExpired, sessionPath]);

  useEffect(() => {
    if (!sessionPath || !userId) {
      return;
    }

    refreshFiles().catch((error: unknown) => {
      setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
    });

    const timer = window.setInterval(() => {
      refreshFiles().catch((error: unknown) => {
        setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
      });
    }, isRunning ? 2500 : 6000);

    return () => window.clearInterval(timer);
  }, [isRunning, refreshFiles, sessionPath, userId]);

  const resetSession = useCallback(() => {
    const nextThreadId = createThreadId();
    storeThreadId(nextThreadId);
    setThreadId(nextThreadId);
    clearWorkspace();
    setHistoryThreadId(nextThreadId);
  }, [clearWorkspace]);

  const selectThread = useCallback(
    (nextThreadId: string) => {
      if (!threads.some((thread) => thread.thread_id === nextThreadId)) {
        return;
      }
      storeThreadId(nextThreadId);
      setThreadId(nextThreadId);
      clearWorkspace();
      void loadThreadHistory(nextThreadId);
    },
    [clearWorkspace, loadThreadHistory, threads]
  );

  const submitTask = useCallback(
    async (query: string) => {
      const cleanQuery = query.trim();
      if (!cleanQuery) {
        throw new Error("请输入研搜任务");
      }
      if (!threadId) {
        throw new Error("会话正在加载，请稍后再试");
      }

      historyRequestRef.current += 1;
      setIsLoadingHistory(false);
      setIsRunning(true);
      setIsCancelling(false);
      setEvents([]);
      setResult("");
      setLastError("");
      setLastWarning("");
      try {
        const response = await startTask(cleanQuery, threadId);
        if (response.thread_id && response.thread_id !== threadId) {
          storeThreadId(response.thread_id);
          setThreadId(response.thread_id);
        }
        // /api/task creates/updates ownership before returning, so refresh the
        // sidebar without making thread IDs part of the request identity.
        void refreshThreads().catch((error: unknown) => {
          if (!isUnauthorized(error)) {
            setLastError(error instanceof Error ? error.message : "线程列表刷新失败");
          }
        });
        return response;
      } catch (error) {
        if (isUnauthorized(error)) {
          onAuthExpired();
        }
        setIsRunning(false);
        setIsCancelling(false);
        throw error;
      }
    }, [onAuthExpired, refreshThreads, threadId]
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!isRunning || !threadId) {
      throw new Error("当前没有正在执行的任务");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelTask(threadId);
      if (response.status === "cancelled") {
        setIsRunning(false);
        setIsCancelling(false);
        setResult((previous) => previous || "任务已取消");
      }
      return response;
    } catch (error) {
      if (isUnauthorized(error)) {
        onAuthExpired();
      }
      setIsCancelling(false);
      throw error;
    }
  }, [isRunning, onAuthExpired, threadId]);

  const uploadFiles = useCallback(
    async (items: UploadedItem[]) => {
      if (items.length === 0) {
        throw new Error("请选择要上传的文件");
      }
      if (!threadId) {
        throw new Error("会话正在加载，请稍后再试");
      }

      const nextItems = items.filter((item) => !uploadedNameSetRef.current.has(item.name));
      if (nextItems.length === 0) {
        return {
          status: "uploaded",
          files: Array.from(uploadedNameSetRef.current)
        };
      }

      setIsUploading(true);
      setLastError("");
      try {
        const response = await uploadSessionFiles(
          nextItems.map((item) => item.raw),
          threadId
        );
        setUploadedItems((previous) => {
          const names = new Set(previous.map((item) => item.name));
          const next = [...previous];
          nextItems.forEach((item) => {
            if (!names.has(item.name)) {
              names.add(item.name);
              uploadedNameSetRef.current.add(item.name);
              next.push(item);
            }
          });
          return next;
        });
        return response;
      } catch (error) {
        if (isUnauthorized(error)) {
          onAuthExpired();
        }
        throw error;
      } finally {
        setIsUploading(false);
      }
    },
    [onAuthExpired, threadId]
  );

  const stats = useMemo(() => {
    const toolEvents = events.filter((event) => event.event === "tool_start").length;
    const assistantEvents = events.filter((event) => event.event === "assistant_call").length;
    const errorEvents = events.filter((event) => event.event === "error").length;

    return {
      toolEvents,
      assistantEvents,
      errorEvents,
      fileCount: files.length
    };
  }, [events, files.length]);

  return {
    connectionState,
    events,
    files,
    history,
    historyThreadId,
    isCancelling,
    isLoadingHistory,
    isLoadingThreads,
    isRunning,
    isUploading,
    lastError,
    lastWarning,
    lastPongAt,
    refreshFiles,
    refreshThreads,
    resetSession,
    result,
    selectThread,
    sessionPath,
    stats,
    cancelCurrentTask,
    submitTask,
    threadId,
    threads,
    uploadFiles,
    uploadedItems
  };
}
