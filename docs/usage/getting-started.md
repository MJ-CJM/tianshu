# 编译、运行与部署

## 项目结构

```
tianshu/
├── src/tianshu/           # Python 后端 + CLI
│   └── web/static/        # 前端构建产物（gitignore）
├── web/                   # 前端源码（React + Vite + TypeScript）
├── pyproject.toml         # Python 依赖
└── Dockerfile             # 两阶段构建，单容器部署
```

## 前置条件

- Python >= 3.12
- Node.js >= 20
- Docker（仅部署需要）

---

## 1. 本地开发（前后端分离）

前端和后端各自启动，通过 vite proxy 串联。

### 后端

```bash
# 安装依赖（首次 / 依赖变更后）
pip install -e ".[cli]"

# 配置环境变量
cp .env.example .env
# 编辑 .env，填写 TIANSHU_LLM_API_KEY 等

# 启动（带热重载）
uvicorn tianshu.app:create_app --factory --reload --port 8000
```

后端监听 `http://localhost:8000`，提供 `/api/*` 和 `/health` 接口。

### 前端

```bash
cd web

# 安装依赖（首次 / 依赖变更后）
npm install

# 启动开发服务器
npm run dev
```

前端监听 `http://localhost:7999`，Vite 自动将 `/api` 和 `/health` 代理到后端 8000。

**开发时访问 `http://localhost:7999`。**

---

## 2. 本地一体化运行

先构建前端，后端直接 serve 静态文件，单端口访问。

```bash
# 构建前端
cd web && npm run build && cd ..

# 启动后端，指定静态文件目录
TIANSHU_STATIC_DIR=src/tianshu/web/static \
  uvicorn tianshu.app:create_app --factory --port 8000
```

访问 `http://localhost:8000` 同时提供 API 和 Web UI。

---

## 3. Docker 部署

### 构建镜像

```bash
docker build -t tianshu .
```

Dockerfile 采用两阶段构建，最终产出单容器：

1. **Stage 1（frontend-builder）**：Node 20 环境，`npm ci` + `npm run build`，编译前端为静态文件
2. **Stage 2（runtime）**：Python 3.12 环境，安装后端依赖，将 Stage 1 的构建产物复制到 `/app/static`

两阶段构建不会把 Node.js 和 node_modules 带入运行阶段；运行镜像仍包含 Python 应用源码、后端依赖，以及 `git`、`curl`、`jq`、编译工具等执行器所需的系统工具。
前端静态文件由 FastAPI 直接 serve，React 在用户浏览器中运行。

### 运行容器

```bash
docker run -d \
  --name tianshu \
  -p 127.0.0.1:8000:8000 \
  -v tianshu-data:/data \
  -v "$(pwd)/workspace:/workspace" \
  --env-file .env \
  tianshu
```

> ⚠️ v0.4.2 无统一鉴权，仅限可信本地。必须把宿主端口绑定到 `127.0.0.1`，不要映射到公网或不可信网段。

### 常用操作

```bash
docker logs -f tianshu         # 查看日志
docker stop tianshu            # 停止
docker rm tianshu              # 删除容器
docker build -t tianshu . && \
docker rm -f tianshu && \
  docker run -d --name tianshu -p 127.0.0.1:8000:8000 \
    -v tianshu-data:/data --env-file .env tianshu   # 重新构建并运行
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TIANSHU_LLM_MODEL` | `gpt-4o-mini` | LLM 模型 |
| `TIANSHU_LLM_API_KEY` | （必填） | API 密钥 |
| `TIANSHU_DB_PATH` | `~/.tianshu/tianshu.db` | SQLite 数据库路径 |
| `TIANSHU_HOST` | `127.0.0.1` | 默认仅监听回环；Docker helper 会在容器内单独覆盖 |
| `TIANSHU_PORT` | `8000` | 监听端口 |
| `TIANSHU_SECURITY_MODE` | `trusted-local` | 远程部署须显式设为 `secure-remote` 并补齐下列安全配置 |
| `TIANSHU_PUBLIC_BASE_URL` | 空 | secure-remote 的 HTTPS 公共地址 |
| `TIANSHU_ALLOWED_HOSTS` | 空 | secure-remote 精确 Host 列表，逗号分隔 |
| `TIANSHU_ALLOWED_ORIGINS` | 空 | secure-remote 精确 HTTPS Origin 列表 |
| `TIANSHU_TRUSTED_PROXY_CIDRS` | 空 | 可声明 HTTPS 的可信反代网段 |
| `TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH` | 空 | `sha256:<64 hex>`；服务端不保存明文 token |
| `TIANSHU_API_TOKEN` | 空 | CLI/MCP Bearer token，仅放客户端环境变量 |
| `TIANSHU_WORKSPACE_DIR` | `.` | Agent 工作目录 |
| `TIANSHU_STATIC_DIR` | `/app/static` | 前端静态文件目录 |
| `TIANSHU_AGENT_MAX_ITERATIONS` | `20` | Agent 最大迭代次数 |
| `TIANSHU_AGENT_TIMEOUT_SECONDS` | `300` | Agent 执行超时（秒） |

Docker 容器中 `TIANSHU_DB_PATH`、`TIANSHU_WORKSPACE_DIR`、`TIANSHU_STATIC_DIR` 已通过 Dockerfile ENV 预设，无需手动指定。

---

## CLI 使用

```bash
# 安装 CLI 依赖
pip install -e ".[cli]"

# 查看可用命令
tianshu --help
```
