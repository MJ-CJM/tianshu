# 编译、运行与部署

## 项目结构

```
tianshu/
├── src/tianshu/           # Python 后端 + CLI
│   └── web/static/        # 前端构建产物（gitignore）
├── web/                   # 前端源码（React + Vite + TypeScript）
├── pyproject.toml         # Python 依赖
└── Dockerfile             # legacy/experimental 本地容器验证
```

## 前置条件

- Python >= 3.12
- Node.js >= 20
- Docker（仅本地容器验证需要）

---

## 1. 本地开发（前后端分离）

前端和后端各自启动，通过 vite proxy 串联。

### 后端

```bash
# 安装依赖（首次 / 依赖变更后）
pip install -e .

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

## 3. Legacy Docker 本地验证

Dockerfile 是 `legacy/experimental` 开发资产，仅用于本地容器路径验证；它不是官方安装
路径，也没有发布到 registry。正式本地路径仍是源码 checkout 与同一 checkout 构建的
exact Wheel。

```bash
bash scripts/docker.sh build
bash scripts/docker.sh start
bash scripts/docker.sh status
```

Dockerfile 采用三阶段构建：

1. Node 20 构建 Web 静态载荷；
2. Python 3.12 用 in-tree build backend 构建包含 Web 与许可证通知的 Wheel；
3. 非 root 的 Python 3.12 runtime 安装该 Wheel，并从包内读取 Web 载荷。

请使用 `scripts/docker.sh`，它会验证 trusted-local 的回环发布地址并把准确的容器网关传给
鉴权边界。不要用裸 `docker run` 绕过该边界，也不要将此镜像作为官方发行物对外发布。

本轮开源前检查已经在本地真实构建并启动该镜像，确认 runtime 使用非 root
`10001:10001`，health/API/Web 均可访问。这只能证明当前本地 Docker 路径可运行，不会
把它提升为官方容器、registry 制品或跨平台支持。

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
| `TIANSHU_STATIC_DIR` | 空（使用包内 Web 载荷） | 显式覆盖前端静态文件目录 |
| `TIANSHU_AGENT_MAX_ITERATIONS` | `20` | Agent 最大迭代次数 |
| `TIANSHU_AGENT_TIMEOUT_SECONDS` | `300` | Agent 执行超时（秒） |

Docker 容器中 `TIANSHU_DB_PATH`、持久目录与 `TIANSHU_WORKSPACE_DIR` 已通过 Dockerfile
预设；Web 静态文件来自安装的 Wheel。

`secure-remote` 的 CLI 推荐先设置 `TIANSHU_API_URL`，再运行 `tianshu auth login`。
登录时 PAT 只用于换取一次会话；后续 access token 到期会自动刷新一次，会话文件位于
`~/.tianshu/credentials.json` 且强制为 `0600`。可用 `tianshu auth whoami` 查看当前主体，
或用 `tianshu auth logout` 撤销并删除本机会话。`TIANSHU_API_TOKEN` 仍保持最高优先级。

---

## 任务调度边界

长程任务可以立即执行或定时执行一次。当前单节点运行身份模型不能安全支持长程任务的
cron / interval 周期运行，因此 Web、API 与调度工具都会拒绝该组合；需要周期执行时请
改用普通任务。

---

## 4. 本地 Wheel 与发布边界

本轮已经从当前代码构建出 Wheel 和 sdist，并通过制品清单、许可证通知和 Python
依赖安全检查。**没有**执行用户明确排除的“Ubuntu 全新 HOME 安装 exact Wheel 并完成
核心黄金路径”，因此不能把本地构建成功写成该环境已通过，也不能据此发布 PyPI、
GHCR、tag 或 release。完整复验步骤见
[Lean Developer Preview](lean-developer-preview.md)。

---

## 5. CLI 使用

```bash
# CLI 是基础安装的一部分
pip install -e .

# 查看可用命令
tianshu --help
```
