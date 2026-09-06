import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  LeftOutlined,
  LogoutOutlined,
  RightOutlined,
  ToolOutlined,
  UserOutlined
} from "@ant-design/icons";
import { Alert, App as AntApp, Button, Spin } from "antd";
import { useEffect, useRef, useState } from "react";
import { AuthScreen } from "./components/AuthScreen";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationThread } from "./components/ConversationThread";
import type { ChatTurn } from "./components/ConversationThread";
import { mapHistoryToChatTurns } from "./history";
import { API_BASE_URL, WS_BASE_URL } from "./lib/config";
import { useAuth } from "./hooks/useAuth";
import { useDeepAgentSession } from "./hooks/useDeepAgentSession";
import type { ConnectionState, UploadedItem } from "./types";

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

function createTurn(content: string): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    content,
    events: [],
    files: [],
    isRunning: true,
    result: "",
    timestamp: new Date().toISOString()
  };
}

export default function App() {
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [isStatusCollapsed, setIsStatusCollapsed] = useState(false);
  const streamRef = useRef<HTMLElement | null>(null);
  const liveTurnIdRef = useRef<string | null>(null);
  const auth = useAuth();
  const session = useDeepAgentSession(auth.user, auth.expireSession);

  useEffect(() => {
    liveTurnIdRef.current = null;
    setTurns([]);
    setQuery("");
    setStagedItems([]);
  }, [auth.user?.id]);

  useEffect(() => {
    if (session.historyThreadId !== session.threadId) {
      return;
    }

    setTurns(mapHistoryToChatTurns(session.history, new Date().toISOString()));
    liveTurnIdRef.current = null;
  }, [session.history, session.historyThreadId, session.threadId]);

  useEffect(() => {
    setTurns((previous) => {
      const liveTurnId = liveTurnIdRef.current;
      if (!liveTurnId) {
        return previous;
      }

      const liveTurnIndex = previous.findIndex((turn) => turn.id === liveTurnId);
      if (liveTurnIndex < 0) {
        return previous;
      }

      const liveTurn = previous[liveTurnIndex];
      const nextLiveTurn = {
        ...liveTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result
      };

      return previous.map((turn, index) => (index === liveTurnIndex ? nextLiveTurn : turn));
    });
  }, [session.events, session.files, session.isRunning, session.result]);

  useEffect(() => {
    const streamNode = streamRef.current;
    if (!streamNode) {
      return;
    }

    window.requestAnimationFrame(() => {
      streamNode.scrollTo({
        top: streamNode.scrollHeight,
        behavior: "smooth"
      });
    });
  }, [turns]);

  async function handleSubmit() {
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入研搜任务");
      return;
    }
    if (!session.threadId || session.isLoadingThreads || session.isLoadingHistory) {
      message.info(
        session.isLoadingHistory ? "聊天记录加载中，请稍后再试" : "线程列表加载中，请稍后再试",
      );
      return;
    }

    const nextTurn = createTurn(cleanQuery);
    liveTurnIdRef.current = nextTurn.id;
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.submitTask(cleanQuery);
      message.success("任务已启动，执行过程会显示在对话中");
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === nextTurn.id
            ? {
                ...turn,
                isRunning: false,
                result: error instanceof Error ? error.message : "任务启动失败"
              }
            : turn
        )
      );
      message.error(error instanceof Error ? error.message : "任务启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  async function handleLogout() {
    liveTurnIdRef.current = null;
    try {
      await auth.logout();
      message.success("已退出登录");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "退出登录失败");
    }
  }

  function handleNewSession() {
    liveTurnIdRef.current = null;
    session.resetSession();
    setTurns([]);
    setQuery("");
    setStagedItems([]);
  }

  const online = session.connectionState === "connected";

  if (auth.isLoading) {
    return (
      <main className="auth-loading" aria-live="polite">
        <Spin size="large" />
        <span>正在确认登录状态...</span>
      </main>
    );
  }

  if (!auth.user) {
    return (
      <AuthScreen
        error={auth.error}
        isSubmitting={auth.isSubmitting}
        onLogin={auth.login}
        onRegister={auth.register}
      />
    );
  }

  return (
    <div
      className={`chat-app-shell min-h-dvh ${isStatusCollapsed ? "chat-app-shell--status-collapsed" : ""}`}
    >
      <aside className="chat-sidebar" aria-label="会话信息">
        <div className="sidebar-brand">
          <span className="panel-kicker">DEEPSEARCH</span>
          <h1>深度研搜</h1>
          <p>对话式多智能体研究台</p>
        </div>

        <Button className="new-chat-button" block onClick={handleNewSession}>
          新建研搜
        </Button>

        <div className="sidebar-user-card">
          <div className="sidebar-user-identity">
            <UserOutlined aria-hidden />
            <span title={auth.user.email}>{auth.user.email}</span>
          </div>
          <Button
            className="sidebar-logout-button"
            icon={<LogoutOutlined />}
            onClick={handleLogout}
            size="small"
          >
            退出登录
          </Button>
        </div>

        <div className="sidebar-section">
          <span className="sidebar-label">THREAD</span>
          <strong className="thread-id" title={session.threadId || ""}>
            {session.threadId ? session.threadId.slice(0, 8) : "加载中"}
          </strong>
        </div>

        <div className="sidebar-section sidebar-thread-section">
          <div className="sidebar-section-heading">
            <span className="sidebar-label">THREADS</span>
            <span className="sidebar-thread-count">{session.threads.length}</span>
          </div>
          {session.isLoadingThreads ? (
            <div className="sidebar-thread-empty">加载线程中...</div>
          ) : session.threads.length === 0 ? (
            <div className="sidebar-thread-empty">完成一次任务后会出现在这里</div>
          ) : (
            <div className="sidebar-thread-list" aria-label="历史线程">
              {session.threads.map((thread) => (
                <button
                  className={
                    thread.thread_id === session.threadId
                      ? "sidebar-thread sidebar-thread--active"
                      : "sidebar-thread"
                  }
                  key={thread.thread_id}
                  onClick={() => {
                    liveTurnIdRef.current = null;
                    session.selectThread(thread.thread_id);
                    setTurns([]);
                    setQuery("");
                    setStagedItems([]);
                  }}
                  type="button"
                >
                  <strong>{thread.title || `会话 ${thread.thread_id.slice(0, 8)}`}</strong>
                  <span>{new Date(thread.last_used_at).toLocaleString("zh-CN", { hour12: false })}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="sidebar-section">
          <span className="sidebar-label">AGENTS</span>
          <ul className="agent-mini-list">
            <li>
              <CloudServerOutlined aria-hidden />
              网络搜索助手
            </li>
            <li>
              <DatabaseOutlined aria-hidden />
              数据库查询助手
            </li>
            <li>
              <FileSearchOutlined aria-hidden />
              RAGFlow 助手
            </li>
          </ul>
        </div>

        <div className="sidebar-section sidebar-endpoints">
          <span className="sidebar-label">ENDPOINTS</span>
          <code>{API_BASE_URL}</code>
          <code>{WS_BASE_URL}</code>
        </div>
      </aside>

      <main className="chat-main">
        <header className="chat-topbar">
          <div>
            <span className="panel-kicker">CHAT WORKSPACE</span>
            <h2>深度研搜对话</h2>
          </div>
          <div className={`run-indicator ${session.isRunning ? "run-indicator--live" : ""}`}>
            {session.isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
            {session.isRunning ? "研搜中" : "待命"}
          </div>
          <div className="chat-user-control">
            <span title={auth.user.email}>{auth.user.email}</span>
            <Button
              aria-label="退出登录"
              className="chat-user-logout"
              icon={<LogoutOutlined />}
              onClick={handleLogout}
              size="small"
            />
          </div>
        </header>

        {session.lastError ? (
          <Alert
            className="chat-alert"
            message={session.lastError}
            showIcon
            type="error"
          />
        ) : null}

        {session.lastWarning ? (
          <Alert
            className="chat-alert"
            message={session.lastWarning}
            showIcon
            type="warning"
          />
        ) : null}

        <section className="chat-stream-panel" ref={streamRef}>
          {session.isLoadingHistory ? (
            <div className="conversation-empty" aria-live="polite">
              正在加载历史聊天记录...
            </div>
          ) : (
            <ConversationThread
              onUseExample={setQuery}
              turns={turns}
            />
          )}
        </section>

        <ChatComposer
          isCancelling={session.isCancelling}
          isRunning={session.isRunning}
          isUploading={session.isUploading}
          onCancel={handleCancel}
          onNewSession={handleNewSession}
          onQueryChange={setQuery}
          onStagedItemsChange={setStagedItems}
          onSubmit={handleSubmit}
          onUpload={handleUpload}
          query={query}
          stagedItems={stagedItems}
          uploadedItems={session.uploadedItems}
        />
      </main>

      <aside
        className={`chat-status-sidebar ${isStatusCollapsed ? "chat-status-sidebar--collapsed" : ""}`}
        aria-label="运行状态"
      >
        <div className="status-sidebar-heading">
          {isStatusCollapsed ? <ApiOutlined aria-hidden /> : <span className="sidebar-label">STATUS</span>}
          <button
            aria-expanded={!isStatusCollapsed}
            aria-label={isStatusCollapsed ? "展开状态栏" : "折叠状态栏"}
            className="status-sidebar-toggle"
            onClick={() => setIsStatusCollapsed((collapsed) => !collapsed)}
            title={isStatusCollapsed ? "展开状态栏" : "折叠状态栏"}
            type="button"
          >
            {isStatusCollapsed ? <LeftOutlined aria-hidden /> : <RightOutlined aria-hidden />}
          </button>
        </div>
        {isStatusCollapsed ? (
          <div
            aria-label={`WebSocket：${connectionLabel(session.connectionState)}`}
            className={`status-sidebar-collapsed-state ${online ? "status-sidebar-collapsed-state--online" : ""}`}
            title={`WebSocket：${connectionLabel(session.connectionState)}`}
          >
            <span aria-hidden />
          </div>
        ) : (
          <div className="sidebar-status-list">
            <div className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}>
              <ApiOutlined aria-hidden />
              <span>WebSocket</span>
              <strong>{connectionLabel(session.connectionState)}</strong>
            </div>
            <div className="sidebar-status">
              <BranchesOutlined aria-hidden />
              <span>助手调度</span>
              <strong>{session.stats.assistantEvents}</strong>
            </div>
            <div className="sidebar-status">
              <ToolOutlined aria-hidden />
              <span>工具调用</span>
              <strong>{session.stats.toolEvents}</strong>
            </div>
            <div className={session.stats.errorEvents > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"}>
              <CloseCircleOutlined aria-hidden />
              <span>异常</span>
              <strong>{session.stats.errorEvents}</strong>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
