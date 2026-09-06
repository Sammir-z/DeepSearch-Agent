# MySQL Authentication Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-user registration, login sessions, login-failure limiting, and ownership checks for Agent tasks, files, and WebSocket connections.

**Architecture:** Keep the MySQL schema, repository, password hashing, opaque session tokens, and Redis-backed login limiter under `app/memory`. `app/api/server.py` adds thin FastAPI routes and ownership guards; existing Agent and file behavior remains unchanged after authorization succeeds.

**Tech Stack:** Python 3.12, FastAPI, MySQL 8.4 via `mysql-connector-python`, `pwdlib[argon2]`, Redis client already provided by phase 1, HttpOnly cookie sessions.

**Spec:** `长期记忆实施方案.md`

## Global Constraints

- Implement all authentication and ownership logic under `app/memory`.
- Use opaque random session tokens stored only as SHA-256 hashes in MySQL.
- Use Argon2id password hashing through `pwdlib`; never store plaintext passwords.
- Use Redis counters for failed-login limiting; do not use an in-process limiter.
- Derive `user_id` from the server-side session; never trust a client-supplied user identity.
- Preserve existing task streaming, file generation, and WebSocket event behavior for authorized users.

---

### Task 1: Add schema, database repository, and dependencies

**Files:**
- Create: `app/memory/auth_schema.sql`
- Create: `app/memory/auth_store.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

Implement idempotent creation of `users`, `auth_sessions`, `agent_threads`, and `audit_events`, plus repository methods for users, sessions, thread ownership, and audit records.

### Task 2: Add authentication and rate-limit service

**Files:**
- Create: `app/memory/auth_service.py`
- Modify: `app/memory/__init__.py`

Implement registration, Argon2id verification, opaque session creation/expiry/revocation, Redis failed-login counters, and helpers for extracting and setting the session cookie.

### Task 3: Add routes and ownership guards

**Files:**
- Modify: `app/api/server.py`

Add `/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, and `/api/auth/me`. Initialize the schema in FastAPI lifespan. Require an authenticated user for task, cancel, upload, file list/download, and WebSocket routes; verify every thread belongs to that user before accessing it.

### Task 4: Verify behavior after implementation

**Files:**
- Create: `tests/memory/test_auth_service.py`
- Create: `tests/api/test_auth_routes.py`

Run unit tests, application import/compile checks, MySQL schema checks, and live registration/login/expiry/ownership smoke tests against the Docker services.

