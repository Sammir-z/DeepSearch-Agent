# DeepSearch Agents

一个面向多用户的对话式多智能体深度研搜系统。用户可以通过自然语言发起研究任务，系统按需调用网络搜索、MySQL 数据查询、RAGFlow 知识库和文件分析能力，并通过 WebSocket 实时展示执行过程。

## 项目能力

- 多用户注册、登录、登出和会话过期管理
- 用户级任务、thread、上传文件和生成文件隔离
- 主智能体调度网络搜索、数据库查询和 RAGFlow 助手
- Redis 持久化 Agent checkpoint 和用户长期记忆
- MySQL 保存用户、认证会话、thread 归属和审计事件
- 显式记住、查询和删除用户偏好
- 用户重新登录后恢复自己的 thread 列表和聊天历史
- Markdown/PDF 报告生成与文件下载
- WebSocket 推送工具调用、助手调用、结果和异常事件
- Redis/MySQL 不可用时返回明确错误，不静默降级

## 系统结构

```text
浏览器
  │
  ▼
前端容器（Nginx，5173）
  │  /api、/ws 反向代理
  ▼
后端容器（FastAPI/Uvicorn，8000）
  ├── DeepAgents 主智能体与专家助手
  ├── MySQL：用户、认证、thread 归属、审计
  └── Redis：checkpoint、长期记忆、会话数据
```

Docker Compose 服务间使用容器网络通信：后端连接 `mysql:3306` 和 `redis:6379`。宿主机端口仅用于浏览器或本地调试。

## Docker 一键启动

### 环境要求

- Docker Desktop（包含 Docker Compose）
- 可用的 OpenAI 兼容模型 API Key
- Tavily API Key
- 如果使用私有知识库，还需要 RAGFlow 服务和 API Key

### 配置环境变量

在项目根目录复制环境变量模板：

PowerShell：

```powershell
Copy-Item .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写模型和 Tavily 配置：

```dotenv
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=你的模型_API_KEY
LLM_QWEN_MAX=deepseek-v4-flash
TAVILY_API_KEY=你的_TAVILY_API_KEY
```

默认宿主机端口如下：

| 配置项 | 默认值 | 用途 |
| --- | ---: | --- |
| `FRONTEND_PORT` | `5173` | 前端网站 |
| `BACKEND_PORT` | `8000` | 后端 API |
| `MYSQL_PORT` | `3307` | 宿主机访问 MySQL |
| `REDIS_PORT` | `6380` | 宿主机访问 Redis |

### 启动

前台运行并查看全部日志：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up --build
```

后台运行：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up --build -d
```

启动后访问：

```text
前端：http://localhost:5173
后端接口文档：http://localhost:8000/docs
后端健康检查：http://localhost:8000/health/live
```

后端启动日志会打印可访问的前端地址：

```text
INFO:     前端访问地址: http://localhost:5173
```

查看后端日志：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml logs -f backend
```

停止服务但保留数据卷：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml down
```

不要使用 `docker compose down -v`，否则会删除 MySQL 和 Redis 的命名数据卷。

> MySQL 初始化脚本只在数据卷首次创建时执行。修改 `.env` 中的 `MYSQL_PASSWORD` 不会自动修改已有数据卷中的 root 密码，需要通过 SQL 或数据库管理工具单独修改。

## 使用流程

1. 打开 `http://localhost:5173`，注册并登录账号。
2. 点击“新建研搜”，创建当前用户的 thread。
3. 输入任务并提交，可选上传附件。
4. 前端通过 WebSocket 接收任务进度、工具调用和最终回答。
5. 生成的 Markdown/PDF 文件保存在当前用户的会话目录中。
6. 重新登录后，前端会从后端加载该用户的 thread 列表和聊天历史。
7. 需要保存偏好时，使用显式记住功能；删除后后续任务不会再召回该记忆。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health/live` | 容器存活检查 |
| `POST` | `/api/auth/register` | 注册用户 |
| `POST` | `/api/auth/login` | 登录并设置会话 Cookie |
| `POST` | `/api/auth/logout` | 注销当前会话 |
| `GET` | `/api/auth/me` | 获取当前用户 |
| `GET` | `/api/threads` | 获取当前用户的 thread 列表 |
| `GET` | `/api/threads/{thread_id}/history` | 获取用户问题和助手最终回答 |
| `POST` | `/api/task` | 启动后台 Agent 任务 |
| `POST` | `/api/task/{thread_id}/cancel` | 取消任务 |
| `POST` | `/api/upload` | 上传当前 thread 的文件 |
| `GET` | `/api/files` | 列出当前用户可访问的生成文件 |
| `GET` | `/api/download` | 下载生成文件 |
| `POST` | `/api/memory` | 显式保存长期记忆 |
| `GET` | `/api/memory` | 查询当前用户的长期记忆 |
| `DELETE` | `/api/memory/{memory_key}` | 删除一条长期记忆 |
| `WebSocket` | `/ws/{thread_id}` | 接收任务实时事件 |

所有认证接口使用服务端会话 Cookie。前端不自行生成或提交 `user_id`，thread、文件和记忆均由后端按当前登录用户校验归属。

## 手动开发启动

Docker 方式适合完整联调；如果只修改代码，也可以在本机分别启动前后端。

### 后端

```bash
uv sync
uv run uvicorn app.api.server:app --host 0.0.0.0 --port 8000 --reload
```

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

本机独立运行前端时，可在 `frontend/.env.local` 指定：

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

Docker 模式下前端默认使用同源的 `/api` 和 `/ws`，无需填写这两个变量。

## 测试与构建

运行后端测试：

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

构建前端：

```bash
cd frontend
pnpm build
```

检查 Compose 配置：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml config
```

## 项目目录

```text
deepsearch-agents/
├── app/
│   ├── agent/          # 主智能体、子智能体和 Agent 执行入口
│   ├── api/            # FastAPI、WebSocket 和事件监控
│   ├── memory/         # MySQL 认证、Redis 持久化、长期记忆和聊天历史
│   ├── tools/          # 网络搜索、数据库、RAGFlow、文件和报告工具
│   ├── output/         # 运行时生成的用户会话产物
│   └── updated/        # 运行时上传文件暂存目录
├── frontend/           # React + Vite 前端
├── docker/
│   ├── docker-compose.yaml
│   ├── backend.Dockerfile
│   ├── frontend.Dockerfile
│   ├── nginx.conf
│   └── mysql/mysql.sql
├── docs/               # 设计方案和项目文档
├── tests/              # 后端测试
├── .env.example
└── pyproject.toml
```

## 代码来源与致谢

本项目是在上游 DeepSearch Agents 项目基础上进行的二次开发，参考了其多智能体编排、工具组织和基础前后端结构；本项目进一步增加了多用户认证、用户归属校验、Redis 长期记忆、聊天历史恢复和完整 Docker 化部署。

上游代码参考：[didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents)
