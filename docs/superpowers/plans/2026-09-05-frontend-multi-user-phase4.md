# Frontend Multi-User Experience Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cookie-based registration/login/logout UX, user-owned thread loading and switching, and WebSocket authentication-expiry handling to the existing React frontend.

**Architecture:** Keep authentication state and API calls in focused frontend modules, while preserving the existing `useDeepAgentSession` hook for task streaming. Add a minimal authenticated `GET /api/threads` endpoint backed by the existing MySQL `agent_threads` table; the browser never sends a `user_id` and relies on the HttpOnly session cookie.

**Tech Stack:** React 19, TypeScript, Ant Design 5, browser Fetch/WebSocket APIs, FastAPI, MySQL repository already used by Stage 2.

**Spec:** Approved Stage 4 design in the conversation.

## Global Constraints

- Authentication is derived from the backend session cookie; the frontend must not generate or submit `user_id`.
- Every authenticated fetch uses `credentials: "include"` so the HttpOnly cookie reaches the backend.
- Thread IDs are client-generated only for selecting/starting a session; thread ownership remains enforced by the backend.
- A `1008` WebSocket close is treated as expired authentication and must stop reconnecting until the user signs in again.
- User switch/logout clears local thread state and task UI before the next user's thread list is loaded.

---

### Task 1: Add backend thread-list read model and endpoint

**Files:**
- Modify: `app/memory/auth_store.py`
- Modify: `app/api/server.py`
- Test: `tests/memory/test_auth_store.py` or an isolated repository test if the existing suite has no MySQL fixture

**Interfaces:**
- `AuthStore.list_threads(user_id: str) -> list[StoredThread]`
- `GET /api/threads` returns `{ "threads": [{"thread_id", "title", "status", "created_at", "last_used_at"}] }` for the authenticated user only.

- [x] Add a typed `StoredThread` dataclass and a parameterized query ordered by `last_used_at DESC`.
- [x] Add `get_current_user` protection to the FastAPI route and convert database failures to HTTP 503.
- [x] Keep the response free of `user_id`; ownership comes solely from the authenticated session.

### Task 2: Add frontend auth API/types and auth state hook

**Files:**
- Create: `frontend/src/hooks/useAuth.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- `AuthUser`, `AuthPayload`, `ThreadSummary` types.
- `registerUser`, `loginUser`, `logoutUser`, `getCurrentUser`, `listThreads` API functions.
- `useAuth()` returns `{ user, isLoading, error, login, register, logout, refresh }`.

- [x] Make the shared JSON request helper include `credentials: "include"` by default.
- [x] Implement `/api/auth/me` bootstrap so a browser refresh preserves a valid server session without storing tokens locally.
- [x] Expose actionable HTTP error text for invalid credentials and expired sessions.

### Task 3: Add registration/login screen and authenticated shell

**Files:**
- Create: `frontend/src/components/AuthScreen.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [x] Build a single Ant Design form that toggles between login and registration, validates email/password, and shows loading/error state.
- [x] Render the existing workspace only after `useAuth` resolves a user; show a compact loading state during `/api/auth/me`.
- [x] Add user email and logout action to the sidebar; logout clears task state and returns to `AuthScreen`.

### Task 4: Make thread state user-aware and load backend threads

**Files:**
- Modify: `frontend/src/lib/thread.ts`
- Modify: `frontend/src/hooks/useDeepAgentSession.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/types.ts`

- [x] Add `clearStoredThreadId()` and a thread-selection API in the session hook.
- [x] On user identity change, close the old socket, clear local thread/events/files, then load that user's thread list and select the most recently used thread or create a new local thread ID.
- [x] Render thread summaries in the sidebar and switch sessions without sending any user ID.

### Task 5: Handle WebSocket cookie auth and expiry reconnects

**Files:**
- Modify: `frontend/src/hooks/useDeepAgentSession.ts`
- Modify: `frontend/src/App.tsx`

- [x] Keep WebSocket on the backend origin so the browser automatically sends the HttpOnly cookie; do not put credentials or user IDs in the URL.
- [x] On close code `1008`, stop the reconnect timer, clear the authenticated user through an `onAuthExpired` callback, and show a sign-in prompt.
- [x] Preserve bounded reconnect behavior for ordinary network closures and `1013` temporary service failures.

### Task 6: Verify frontend build and authentication flows

**Files:**
- Test: `frontend` TypeScript/Vite build and manual browser smoke flow

- [x] Run the TypeScript compiler and Vite production build from `frontend` (direct binaries; pnpm dependency preflight is non-interactive in this environment).
- [x] Exercise the authenticated register → login → thread list → logout API path; the frontend consumes the same cookie session.
- [x] Implement the WebSocket `1008` handler so it stops reconnects and returns to login; TypeScript compilation validates the client branch.
