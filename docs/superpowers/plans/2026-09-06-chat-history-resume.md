# Chat History Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a user signs in again, load that user's persisted conversation turns and continue the selected thread in the frontend.

**Architecture:** Use the existing Redis `AsyncRedisSaver` checkpoint as the source of truth for conversation messages; do not duplicate chat content in browser `localStorage` or add a second message database in the first version. The authenticated backend verifies MySQL thread ownership, reads the latest checkpoint through the compiled Agent's async state API, normalizes only user prompts and final assistant text, and returns a history read model for the React session hook.

**Tech Stack:** Python 3.12, FastAPI, DeepAgents/LangGraph async checkpoint API, Redis, MySQL ownership records, React 19, TypeScript, Ant Design, browser Fetch/WebSocket APIs.

**Spec:** Current user request: restore a user's previous chat after re-login, without implementing guest mode.

## Global Constraints

- Guest mode is out of scope; every history request remains authenticated through the existing HttpOnly session cookie.
- `agent_threads.user_id` is the ownership boundary. A user must never receive another user's history, even when the thread ID is known.
- Redis checkpoint data is canonical for the first version; the frontend only caches the selected thread ID and transient rendering state.
- Use the existing async Redis checkpointer; do not reintroduce `RedisSaver`, `InMemorySaver`, or synchronous checkpoint calls in async request paths.
- Restore user prompts and final assistant text first. Live tool/assistant event telemetry is not durable history and is intentionally not reconstructed in this version.
- Internal runtime instructions appended to a prompt (for example `【工作环境指令】`) must not be shown as the user's original message.
- Redis/MySQL failures must preserve the current explicit behavior: return 503 where the history cannot be read, and never leak data from a previous user while handling the error.

---

### Task 1: Define and test the checkpoint history read model

**Files:**
- Create: `app/memory/chat_history.py`
- Modify: `app/memory/__init__.py`
- Test: `tests/memory/test_chat_history.py`

**Interfaces:**
- `ChatHistoryTurn` dataclass with `id: str`, `user_content: str`, `assistant_content: str`, and optional `timestamp: str | None`.
- `extract_chat_history(state: object, fallback_timestamp: str | None = None) -> list[ChatHistoryTurn]`.

- [ ] **Step 1: Write failing pure tests for message extraction**

  Cover a human message followed by an assistant text message, assistant tool-call messages with empty content, multiple turns, an incomplete user turn, structured/list content, and removal of the runtime `【工作环境指令】` suffix.

- [ ] **Step 2: Run the focused test and verify the extractor is missing**

  Run: `\.venv\Scripts\python -m unittest tests.memory.test_chat_history -v`

  Expected: import or attribute failure for the new history module.

- [ ] **Step 3: Implement the minimal normalizer**

  Read only `state.values["messages"]` (or the equivalent mapping), identify human and assistant messages by their LangChain `type`, convert text and text blocks to strings, ignore tool/system messages, strip content after `【工作环境指令】`, and generate deterministic fallback IDs from the thread-local message index when a message has no ID. Pair each user prompt with the next non-empty assistant text; retain a final user-only turn when no assistant response exists yet.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `\.venv\Scripts\python -m unittest tests.memory.test_chat_history -v`

- [ ] **Step 5: Export the read model from `app.memory`**

  Export `ChatHistoryTurn` and `extract_chat_history` without changing existing memory service imports.

### Task 2: Add an authenticated backend history endpoint

**Files:**
- Modify: `app/api/server.py`
- Modify: `app/memory/observability.py` only if the new route needs a safe history-read event
- Test: `tests/api/test_chat_history_routes.py`

**Interfaces:**
- `GET /api/threads/{thread_id}/history`
- Response: `{ "thread_id": str, "session_path": str | null, "turns": [{ "id": str, "user_content": str, "assistant_content": str, "timestamp": str | null }] }`.

- [ ] **Step 1: Write failing route tests**

  Test that an unauthenticated request returns `401`, a different user's thread returns `404`, a user-owned thread calls the async Agent state API and returns normalized turns, an empty checkpoint returns an empty list, Redis unavailability returns `503`, and the response never contains `user_id`.

- [ ] **Step 2: Run the route tests and verify the endpoint is absent**

  Run: `\.venv\Scripts\python -m unittest tests.api.test_chat_history_routes -v`

- [ ] **Step 3: Add typed response models and the route**

  Reuse `get_current_user`, `_require_existing_thread`, `_mark_request_context`, `_ensure_redis_persistence`, and `initialize_main_agent`. Build the async state config with the requested `thread_id`, call `await agent.aget_state(config)`, pass the state to `extract_chat_history`, and compute `session_path` only after ownership succeeds. Convert Redis/checkpointer failures to the existing 503 response style.

- [ ] **Step 4: Run the focused route tests**

  Run: `\.venv\Scripts\python -m unittest tests.api.test_chat_history_routes -v`

- [ ] **Step 5: Run the existing backend suite**

  Run: `\.venv\Scripts\python -m unittest discover -s tests -v`

### Task 3: Add the frontend history API contract and session state

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/hooks/useDeepAgentSession.ts`

**Interfaces:**
- `ChatHistoryTurn`, `ThreadHistoryResponse`, and `getThreadHistory(threadId: string)`.
- `useDeepAgentSession` exposes `history`, `isLoadingHistory`, and `historyThreadId` in addition to the existing live state.

- [ ] **Step 1: Add TypeScript contract tests or compile-time fixtures**

  Define a fixture matching the backend response and assert that `getThreadHistory` uses `credentials: "include"`, URL-encodes the thread ID, and preserves `ApiError` status handling. If no frontend test runner is configured, keep the fixture in the API module and use the production build as the compile gate.

- [ ] **Step 2: Implement `getThreadHistory` and the hook state**

  Add a guarded `loadThreadHistory(threadId)` callback. Clear old history before loading, discard stale responses when the user or selected thread changes, call `onAuthExpired()` on 401, and expose a non-authenticated read error without retaining the previous user's turns.

- [ ] **Step 3: Load history during initial thread selection and explicit switching**

  After `/api/threads` selects the most recently used thread, load its history. `selectThread` must clear the current workspace, set the selected ID, and load the new thread's history. When no thread exists, expose an empty history for the newly generated local ID.

- [ ] **Step 4: Compile the frontend**

  Run from `frontend`: `pnpm build`.

### Task 4: Render restored turns without disrupting live streaming

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/ConversationThread.tsx` only if the shared turn type needs to move
- Create or modify: `frontend/src/lib/chatHistory.ts` for a pure history-to-view-model mapper

**Interfaces:**
- `mapHistoryToChatTurns(history: ChatHistoryTurn[], fallbackTimestamp: string) -> ChatTurn[]`.

- [ ] **Step 1: Write mapper tests or deterministic fixtures**

  Verify that restored turns render as completed turns with empty historical events/files, preserve order, show a user-only turn as an unfinished response, and use a safe fallback timestamp when the backend timestamp is null.

- [ ] **Step 2: Implement the mapper and wire it into `App`**

  When `historyThreadId === session.threadId`, replace the cleared `turns` state with the restored turns. Keep the existing live WebSocket effect updating only the current turn after a new task starts; do not append restored turns again when the history request completes.

- [ ] **Step 3: Restore the selected thread's artifact directory**

  If `session_path` is present, set the hook's session path and call the existing file-list API. Treat file-list failure as an inline warning, not as a reason to discard chat history.

- [ ] **Step 4: Compile the frontend again**

  Run from `frontend`: `pnpm build`.

### Task 5: Harden re-login, switching, and failure boundaries

**Files:**
- Modify: `frontend/src/hooks/useDeepAgentSession.ts`
- Modify: `frontend/src/App.tsx`
- Test: `tests/api/test_chat_history_routes.py` and frontend build output

- [ ] Ensure logout/user change clears restored turns, files, WebSocket, and the stored thread ID before another user's thread list can populate the UI.
- [ ] Ensure a delayed history response for user A cannot overwrite user B's workspace after logout/login or a fast thread switch.
- [ ] Ensure a 401 during history or file loading returns to the login screen through the existing `expireSession` callback.
- [ ] Ensure a 503 history response leaves the selected thread visible but displays an explicit “聊天记录暂时无法加载” warning instead of showing stale data.
- [ ] Ensure re-login selects the backend's most recently used thread and that the user can explicitly select any older thread from the list.

### Task 6: Verify the end-to-end acceptance flow

**Files:**
- Test: backend unit/integration tests, `frontend` production build, and a manual browser smoke checklist

- [ ] Run backend tests: `\.venv\Scripts\python -m unittest discover -s tests -v`.
- [ ] Run frontend build: `cd frontend; pnpm build`.
- [ ] Register and log in as user A, run at least two tasks, log out, log in again, and verify the latest thread displays the prior user prompts and assistant answers.
- [ ] Select an older thread and verify only that thread's turns are shown.
- [ ] Log in as user B and verify user A's thread history returns 404 and is not rendered.
- [ ] Restart the backend while retaining the Redis named volume, log in again, and verify the same thread history is still available.
- [ ] Stop Redis and verify the history endpoint returns 503 without showing stale history from another thread or user.

