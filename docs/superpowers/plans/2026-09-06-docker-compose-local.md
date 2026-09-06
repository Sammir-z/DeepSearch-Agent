# 本地 Docker Compose 一键启动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让前端、后端、MySQL 和 Redis 通过一次 Docker Compose 命令构建并启动，并保留现有账号、线程、长期记忆和文件数据。

**Architecture:** 使用四个 Compose 服务。前端采用 Node 多阶段构建后由 Nginx 提供静态文件，并将 `/api`、`/ws` 反向代理到 `backend:8000`；后端使用 Python 3.12 和 `uv.lock` 安装依赖，通过 Compose DNS 连接 `mysql:3306` 和 `redis:6379`。MySQL/Redis 使用现有 named volume，后端 output/updated 使用项目目录挂载。

**Tech Stack:** Docker Compose, Python 3.12, uv, FastAPI/Uvicorn, React/Vite, pnpm, Nginx, MySQL 8.4, Redis 8.

**Spec:** `docs/superpowers/specs/2026-09-06-docker-compose-local-design.md`

## Global Constraints

- 本阶段只覆盖本地开发/演示环境，不包含 HTTPS、域名、生产反向代理集群、自动扩缩容或云部署。
- 宿主机端口默认是 `FRONTEND_PORT=5173`、`BACKEND_PORT=8000`、`MYSQL_PORT=3307`、`REDIS_PORT=6380`。
- 容器间连接必须使用 `MYSQL_HOST=mysql`、`MYSQL_PORT=3306`、`REDIS_URL=redis://redis:6379/0`。
- 不删除或重建 `deepsearch_mysql_data`、`deepsearch_redis_data`，日常停止不得使用 `docker compose down -v`。
- Redis checkpoint 不可用时不能降级到 `InMemorySaver`。
- 真实 LLM、Tavily、RAGFlow 密钥只能保存在本地未提交的 `.env`，`.env.example` 只使用占位符。
- 所有代码和配置修改完成后必须运行前端 TypeScript/Vite 构建、后端测试以及 Compose 配置渲染检查。

---

### Task 1: 添加后端存活检查接口

**Files:**
- Modify: `app/api/server.py`（在 `app = FastAPI(...)` 后增加健康路由）
- Create: `tests/api/test_health_routes.py`

**Interfaces:**
- Produces: `GET /health/live`，成功时返回 `{"status": "ok"}`，不依赖 MySQL、Redis 或登录态。

- [ ] **Step 1: Write the failing test**

```python
import unittest


class HealthRouteTests(unittest.TestCase):
    def test_live_health_route_returns_ok_without_dependencies(self):
        from app.api import server

        response = server.health_live()
        self.assertEqual(response, {"status": "ok"})

    def test_live_health_route_is_registered(self):
        from app.api import server

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in server.app.routes}
        self.assertIn(("/health/live", ("GET",)), routes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .venv\Scripts\python.exe -m pytest tests/api/test_health_routes.py -q`

Expected: FAIL because `server.health_live` and `/health/live` are not defined.

- [ ] **Step 3: Write minimal implementation**

Immediately after `app = FastAPI(...)` in `app/api/server.py`, add:

```python
@app.get("/health/live")
def health_live():
    """Return a dependency-free liveness response for container health checks."""

    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .venv\Scripts\python.exe -m pytest tests/api/test_health_routes.py -q`

Expected: PASS with 2 tests.

### Task 2: Build the backend image and isolate Docker context

**Files:**
- Create: `.dockerignore`
- Create: `docker/backend.Dockerfile`

**Interfaces:**
- Produces: image `deepsearch-agents-backend` exposing container port 8000 and starting `app.api.server:app`.

- [ ] **Step 1: Add Docker context exclusions**

Create `.dockerignore` with these entries so credentials, caches, generated files and frontend dependencies never enter build context:

```text
.env
.env.*
!.env.example
.git
.venv
.uv-cache
.pnpm-store
node_modules
frontend/node_modules
frontend/dist
app/output
updated
__pycache__
*.py[cod]
```

- [ ] **Step 2: Add the backend Dockerfile**

Create `docker/backend.Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -f docker/backend.Dockerfile -t deepsearch-agents-backend .`

Expected: exit code 0 and an image tagged `deepsearch-agents-backend`.

### Task 3: Make the frontend same-origin and add Nginx image

**Files:**
- Modify: `frontend/src/lib/config.ts`
- Create: `docker/frontend.Dockerfile`
- Create: `docker/nginx.conf`

**Interfaces:**
- Consumes: browser path `/api` and `/ws`.
- Produces: image `deepsearch-agents-frontend` serving port 80 with React fallback and proxy routes.

- [ ] **Step 1: Update the frontend default API base**

Change `frontend/src/lib/config.ts` so the default API base is same-origin:

```ts
const DEFAULT_API_BASE_URL = "";
```

Keep the existing `VITE_API_BASE_URL` and `VITE_WS_BASE_URL` overrides. With an empty API base, `fetch` uses `/api/...`, and `deriveWsBaseUrl` uses the browser host for WebSocket connections.

- [ ] **Step 2: Add the Nginx configuration**

Create `docker/nginx.conf`:

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 3: Add the frontend multi-stage Dockerfile**

Create `docker/frontend.Dockerfile`:

```dockerfile
FROM node:22-alpine AS build

WORKDIR /frontend

RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend ./
RUN pnpm build

FROM nginx:1.27-alpine

COPY --from=build /frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
```

- [ ] **Step 4: Run frontend checks**

Run from `frontend`: `..\frontend\node_modules\.bin\tsc.CMD -b` and then `..\frontend\node_modules\.bin\vite.CMD build`.

Expected: both commands exit 0.

- [ ] **Step 5: Build the image**

Run: `docker build -f docker/frontend.Dockerfile -t deepsearch-agents-frontend .`

Expected: exit code 0 and an image tagged `deepsearch-agents-frontend`.

### Task 4: Extend Compose with backend/frontend services and internal configuration

**Files:**
- Modify: `docker/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `frontend/.env.example`

**Interfaces:**
- Consumes: root `.env` values for credentials and host port overrides.
- Produces: four services available through `docker compose --env-file .env -f docker/docker-compose.yaml up --build`.

- [ ] **Step 1: Add host port variables to `.env.example`**

Add or update:

```dotenv
FRONTEND_PORT=5173
BACKEND_PORT=8000
MYSQL_PORT=3307
REDIS_PORT=6380
```

Replace the current-looking `OPENAI_API_KEY` and `TAVILY_API_KEY` values with placeholders such as `your-openai-api-key` and `your-tavily-api-key`; do not modify the user’s local `.env`.

- [ ] **Step 2: Set frontend development defaults**

Update `frontend/.env.example` to document same-origin defaults:

```dotenv
VITE_API_BASE_URL=
VITE_WS_BASE_URL=
```

Keep explicit `http://localhost:8000` overrides documented as an optional value for manually running Vite against a host-started backend.

- [ ] **Step 3: Add the backend Compose service**

Add after the Redis service in `docker/docker-compose.yaml`:

```yaml
  backend:
    build:
      context: ..
      dockerfile: docker/backend.Dockerfile
    container_name: deepsearch-agents-backend
    restart: unless-stopped
    env_file:
      - ../.env
    environment:
      MYSQL_HOST: mysql
      MYSQL_PORT: 3306
      REDIS_URL: redis://redis:6379/0
      CORS_ALLOW_ORIGINS: http://localhost:${FRONTEND_PORT:-5173},http://127.0.0.1:${FRONTEND_PORT:-5173}
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - ../app/output:/app/app/output
      - ../updated:/app/app/updated
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
```

The `updated` mount must match `run_deep_agent`’s project-root layout. If the current host directory is absent, create `updated` before starting Compose.

- [ ] **Step 4: Add the frontend Compose service**

Add:

```yaml
  frontend:
    build:
      context: ..
      dockerfile: docker/frontend.Dockerfile
    container_name: deepsearch-agents-frontend
    restart: unless-stopped
    ports:
      - "${FRONTEND_PORT:-5173}:80"
    depends_on:
      backend:
        condition: service_healthy
```

- [ ] **Step 5: Preserve existing MySQL/Redis mappings and volumes**

Verify the existing entries remain exactly equivalent to:

```yaml
mysql:
  ports:
    - "${MYSQL_PORT:-3307}:3306"
redis:
  ports:
    - "127.0.0.1:${REDIS_PORT:-6380}:6379"
volumes:
  deepsearch_mysql_data:
  deepsearch_redis_data:
```

- [ ] **Step 6: Render the Compose configuration**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml config`

Expected: exit code 0; rendered services are `mysql`, `redis`, `backend`, `frontend`; backend environment shows `mysql:3306` and `redis:6379`; host mappings show 3307 and 6380 by default.

### Task 5: Run end-to-end Docker verification and document the workflow

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: documented one-command startup, health and safe shutdown workflow.

- [ ] **Step 1: Start the stack**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml up --build -d`

Expected: all four containers are created; `docker compose ps` eventually shows MySQL, Redis, backend and frontend running, with backend healthy.

- [ ] **Step 2: Verify service endpoints**

Run: `Invoke-WebRequest http://localhost:5173 -UseBasicParsing`, `Invoke-WebRequest http://localhost:8000/health/live -UseBasicParsing`, and `docker exec deepsearch-redis redis-cli ping`.

Expected: frontend returns HTTP 200, backend returns `{"status":"ok"}`, Redis returns `PONG`.

- [ ] **Step 3: Verify internal connectivity from backend**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml exec backend python -c "import os; print(os.environ['MYSQL_HOST'], os.environ['MYSQL_PORT'], os.environ['REDIS_URL'])"`.

Expected: output contains `mysql 3306 redis://redis:6379/0` and no `localhost:3307` or `127.0.0.1:6380`.

- [ ] **Step 4: Run the backend test suite**

Run: `& .venv\Scripts\python.exe -m pytest -q`

Expected: exit code 0 with all existing and new tests passing.

- [ ] **Step 5: Verify persistence across restart**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml restart redis backend`, then repeat the endpoint checks. Do not add `-v`.

Expected: Redis remains healthy, backend returns 200, and previously stored MySQL/Redis data and mounted files remain available.

- [ ] **Step 6: Update README startup instructions**

Add a Docker startup section before manual `uv`/`pnpm` instructions:

```text
docker compose --env-file .env -f docker/docker-compose.yaml up --build
```

Document `http://localhost:5173` as the browser entrypoint, `localhost:3307` and `localhost:6380` as host debug ports, and explicitly warn that `docker compose down -v` deletes the MySQL/Redis data volumes.

### Task 6: Final review and working-tree handoff

**Files:**
- Review: `docs/superpowers/specs/2026-09-06-docker-compose-local-design.md`
- Review: `docs/superpowers/plans/2026-09-06-docker-compose-local.md`
- Review: all files listed in Tasks 1–5

- [ ] **Step 1: Check the final Compose graph**

Run: `docker compose --env-file .env -f docker/docker-compose.yaml config --services`

Expected output includes exactly `mysql`, `redis`, `backend`, `frontend`.

- [ ] **Step 2: Check for secret leakage in tracked examples**

Run: `Select-String -Path .env.example,frontend/.env.example -Pattern 'sk-|tvly-|ragflow-'`

Expected: no real-looking credential values; only placeholders remain.

- [ ] **Step 3: Check generated files remain outside the image context**

Run: `docker build -f docker/backend.Dockerfile -t deepsearch-agents-backend .` and inspect the build context summary.

Expected: `.env`, `.venv`, caches, `app/output`, and `updated` are excluded by `.dockerignore`.

- [ ] **Step 4: Record the verification result**

Report the exact commands and exit codes for frontend build, backend tests, Compose config rendering, stack startup, health checks and persistence restart. If this directory still has no `.git`, do not claim a commit; hand off the working-tree changes and mention that no Git commit was possible.
