# Redis Persistence Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the process-local LangGraph `InMemorySaver` with a Redis-backed checkpointer and prepare a shared Redis Store without implementing authentication or memory extraction.

**Architecture:** `app/memory/redis_persistence.py` owns Redis URL resolution, client creation, `RedisSaver`/`RedisStore` construction, startup setup, and shutdown. The main agent imports this isolated factory, while FastAPI initializes the shared persistence during lifespan before serving requests.

**Tech Stack:** Python 3.12, LangGraph 1.1.10, DeepAgents 0.5.7, `langgraph-checkpoint-redis` 0.3.2, Redis 8 Alpine, standard-library `unittest`.

**Spec:** `长期记忆实施方案.md`

## Global Constraints

- Keep all new memory integration code under `app/memory`.
- Keep `thread_id` as the checkpoint key; do not add `user_id` authentication in this phase.
- Use the existing Compose service `redis` at container address `redis:6379` and local fallback `127.0.0.1:${REDIS_PORT}`.
- Do not silently fall back to `InMemorySaver` when Redis setup fails.
- Preserve the existing Agent tools, subagents, streaming events, and file behavior.

---

### Task 1: Add Redis persistence dependencies and configuration contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `长期记忆实施方案.md` only if the final configuration contract changes
- Test: `tests/memory/test_redis_persistence.py`

- [ ] **Step 1: Write a failing import/configuration test**

```python
def test_resolve_redis_url_prefers_explicit_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    assert resolve_redis_url() == "redis://redis:6379/0"
```

- [ ] **Step 2: Run the focused test and confirm it fails because `app.memory` is absent**

Run: `python -m unittest tests.memory.test_redis_persistence -v`

- [ ] **Step 3: Add `langgraph-checkpoint-redis==0.3.2` to the project dependencies and lock the environment**

Run: `$env:UV_CACHE_DIR='.uv-cache'; uv lock`

- [ ] **Step 4: Re-run the focused test after the factory is implemented in Task 2**

Expected: the URL test passes and no Redis package import error remains.

### Task 2: Create the isolated Redis persistence factory

**Files:**
- Create: `app/memory/__init__.py`
- Create: `app/memory/redis_persistence.py`
- Test: `tests/memory/test_redis_persistence.py`

**Interfaces:**
- Produces `resolve_redis_url() -> str`.
- Produces `RedisPersistence.from_env() -> RedisPersistence`.
- Produces `RedisPersistence.setup() -> None` and `RedisPersistence.close() -> None`.
- Exposes `RedisPersistence.checkpointer` and `RedisPersistence.store` for graph compilation.

- [ ] **Step 1: Add failing tests for URL fallback and lifecycle delegation**

```python
def test_resolve_redis_url_uses_host_and_port_fallback(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", "6380")
    assert resolve_redis_url() == "redis://127.0.0.1:6380/0"


def test_setup_initializes_both_redis_components():
    persistence = RedisPersistence(client=client, checkpointer=checkpointer, store=store)
    persistence.setup()
    checkpointer.setup.assert_called_once_with()
    store.setup.assert_called_once_with()
```

- [ ] **Step 2: Run the tests and verify the missing factory failure**

Run: `python -m unittest tests.memory.test_redis_persistence -v`

- [ ] **Step 3: Implement the minimal factory**

Use `redis.Redis.from_url(url, decode_responses=False)`, construct `RedisSaver(redis_client=client)` and `RedisStore(client)`, call both `setup()` methods, and close only the owned Redis client in `close()`.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `python -m unittest tests.memory.test_redis_persistence -v`

Expected: all focused tests pass.

### Task 3: Wire Redis persistence into the Agent and FastAPI lifecycle

**Files:**
- Modify: `app/agent/main_agent.py`
- Modify: `app/api/server.py`
- Modify: `tests/memory/test_redis_persistence.py` or add `tests/integration/test_agent_persistence_wiring.py`

- [ ] **Step 1: Add a wiring assertion that the graph receives the Redis checkpointer and store**

Patch the graph construction boundary in the test and assert `create_deep_agent(..., checkpointer=..., store=...)` receives the factory-owned objects.

- [ ] **Step 2: Run the wiring test and verify the current `InMemorySaver` wiring fails the assertion**

Run: `python -m unittest tests.integration.test_agent_persistence_wiring -v`

- [ ] **Step 3: Replace `InMemorySaver()` with the shared factory objects and call `setup()`/`close()` from FastAPI lifespan**

Construct the persistence object once, compile the agent with its checkpointer and store, initialize it before `yield`, and close the client in the `finally` block after `yield`.

- [ ] **Step 4: Run the focused and import checks**

Run: `python -m unittest discover -s tests -v`  
Run: `python -c "from app.agent.main_agent import main_agent; print(type(main_agent).__name__)"`

Expected: tests pass and the Agent module imports without using `InMemorySaver`.

### Task 4: Verify the real Redis service

**Files:**
- No source changes.

- [ ] **Step 1: Validate Compose configuration**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml config --quiet`

- [ ] **Step 2: Verify Redis health and persistence connectivity**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml ps redis` and `docker exec deepsearch-redis redis-cli ping`

Expected: Redis is `healthy` and returns `PONG`.

