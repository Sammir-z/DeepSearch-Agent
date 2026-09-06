# 本地一键 Docker 化设计方案

## 1. 目标与范围

为 DeepSearch Agents 提供本地一键启动能力：前端、后端、MySQL 和 Redis 全部由 Docker Compose 管理。用户执行一次 Compose 命令后，可以通过浏览器完成注册、登录、任务提交、WebSocket 实时推送、线程历史恢复和文件查看。

本次只覆盖本地开发/演示环境，不包含 HTTPS、域名、生产反向代理集群、自动扩缩容或云部署。

## 2. 服务拓扑

Compose 项目名保持 `deepsearch-agents`，包含四个服务：

| 服务 | 容器职责 | 容器端口 | 宿主机端口 |
| --- | --- | ---: | ---: |
| `frontend` | React 构建产物和 Nginx 静态服务 | 80 | `FRONTEND_PORT`，默认 5173 |
| `backend` | FastAPI/Uvicorn Agent API | 8000 | `BACKEND_PORT`，默认 8000 |
| `mysql` | 账号、线程归属和业务示例数据库 | 3306 | `MYSQL_PORT`，默认 3307 |
| `redis` | LangGraph checkpoint、长期记忆和限流 | 6379 | `REDIS_PORT`，默认 6380 |

浏览器只访问 `http://localhost:${FRONTEND_PORT}`。Nginx 将 `/api/*` 和 `/ws/*` 转发到 Compose 内部的 `backend:8000`，静态页面使用同源 API 和 WebSocket 地址。

后端容器通过 Compose DNS 访问依赖：

```text
MYSQL_HOST=mysql
MYSQL_PORT=3306
REDIS_URL=redis://redis:6379/0
```

宿主机端口只用于本地调试，不作为容器间连接地址。

## 3. 构建与运行时设计

### 3.1 后端镜像

- 新增 `docker/backend.Dockerfile`；
- 基于 Python 3.12 镜像；
- 复制 `pyproject.toml` 和 `uv.lock`，使用 `uv sync --frozen` 安装锁定依赖；
- 再复制 `app` 代码；
- 使用非开发模式启动 `uvicorn app.api.server:app --host 0.0.0.0 --port 8000`；
- 设置非缓冲日志，便于 `docker compose logs` 实时查看。

### 3.2 前端镜像

- 新增 `docker/frontend.Dockerfile`；
- 使用 Node.js 和 pnpm 安装 `frontend/pnpm-lock.yaml` 中的依赖；
- 执行 TypeScript 检查和 Vite production build；
- 将 `dist` 复制到 Nginx 镜像；
- 新增 Nginx 配置，支持 React fallback、`/api` 代理和 WebSocket Upgrade 头。

前端配置优先使用同源路径，这样开发代理和 Nginx 代理都能工作；不把 Compose 内部服务名暴露给浏览器。

### 3.3 Compose 配置

在现有 `docker/docker-compose.yaml` 中增加 `backend` 和 `frontend` 服务：

- `backend` 依赖 MySQL、Redis 的 `service_healthy` 状态；
- `frontend` 依赖 backend 健康状态；
- 所有端口支持 `.env` 覆盖；
- backend 使用根目录 `.env` 作为密钥和业务配置来源，同时覆盖容器内部的 MySQL/Redis 地址；
- backend 和 frontend 的容器名称分别使用 `deepsearch-agents-backend`、`deepsearch-agents-frontend`；
- 保留 `deepsearch-mysql`、`deepsearch-redis` 名称，避免破坏现有本地操作习惯。

## 4. 数据与文件持久化

保留现有 named volume：

- `deepsearch_mysql_data`：用户、会话、线程归属、审计和业务示例数据；
- `deepsearch_redis_data`：AOF、checkpoint、长期记忆和登录限流状态。

后端挂载项目目录中的运行时文件：

```text
../app/output  -> /app/app/output
../updated     -> /app/app/updated
```

这样重建后端镜像不会丢失已生成的 Markdown/PDF 和上传附件。日常停止使用普通 `docker compose down`，不执行 `docker compose down -v`。

## 5. 健康检查与故障行为

新增后端存活检查接口（例如 `/health/live`），backend 容器使用该接口作为健康检查。MySQL 和 Redis 保留已有健康检查。

- MySQL 或 Redis 未 healthy 时，backend 不应被认为已就绪；
- Redis checkpoint 不可用时，任务接口继续返回明确的 503，不降级到 `InMemorySaver`；
- MySQL 不可用时，认证和线程归属接口返回 503；
- frontend 只在 backend 健康后启动，避免页面启动后立即产生大量失败请求。

## 6. 环境变量约定

根目录 `.env.example` 增加或明确以下变量：

```dotenv
FRONTEND_PORT=5173
BACKEND_PORT=8000
MYSQL_PORT=3307
REDIS_PORT=6380
```

容器内部地址由 Compose 的 `environment` 覆盖，不要求用户把 `MYSQL_HOST` 改成容器名。`.env.example` 只保留占位符，真实 LLM、Tavily、RAGFlow 密钥只允许出现在本地未提交的 `.env` 中；已有暴露或疑似真实密钥应先撤销并重新生成。

## 7. 验收标准

1. 执行 `docker compose --env-file .env -f docker/docker-compose.yaml up --build` 能构建并启动四个服务。
2. `docker compose ps` 显示 MySQL、Redis、backend、frontend 正常运行，backend 健康检查通过。
3. 浏览器访问 `http://localhost:5173`，可以注册、登录并提交任务。
4. `/api` 和 `/ws` 请求经 Nginx 正确转发，Cookie 和 WebSocket 实时事件正常工作。
5. backend 日志显示使用 `mysql:3306` 和 `redis:6379`，而不是宿主机端口。
6. 普通 `docker compose down` 后再次启动，账号、线程、长期记忆和文件仍存在。
7. Redis 或 MySQL 未就绪时，接口返回明确错误，不静默使用进程内存储。
8. 前端执行 `tsc -b` 和 `vite build` 成功；后端测试套件保持通过。

## 8. 非目标

- 不在本阶段引入 Qdrant；
- 不实现生产 HTTPS、域名、网关集群或自动扩缩容；
- 不改动 Agent 任务队列和跨进程 Worker 设计；
- 不删除或重建现有 MySQL/Redis 数据卷；
- 不把真实密钥复制进镜像或提交到仓库。
