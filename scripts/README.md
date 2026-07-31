# Scripts

Tianshu 启动脚本，提供本地开发和 Docker 容器两种运行模式。

## 本地模式 (`local.sh`)

直接以系统进程运行，适合日常开发和调试。

```bash
# 1. 构建（安装依赖 + 编译前端）
bash scripts/local.sh build

# 2a. 生产模式启动（uvicorn 服务静态文件）
bash scripts/local.sh start

# 2b. 开发模式启动（uvicorn --reload + vite dev server :3000）
bash scripts/local.sh start --dev

# 3. 查看状态
bash scripts/local.sh status

# 4. 查看日志
bash scripts/local.sh logs

# 5. 重启（自动继承 --dev 参数）
bash scripts/local.sh restart

# 6. 停止所有服务
bash scripts/local.sh stop
```

运行时文件存放在 `.tianshu/` 目录：

| 文件 | 用途 |
|------|------|
| `uvicorn.pid` | uvicorn 进程 PID |
| `vite.pid` | vite dev server 进程 PID |
| `uvicorn.log` | uvicorn 日志 |
| `vite.log` | vite 日志 |

## Legacy Docker 本地验证 (`docker.sh`)

容器化运行仅用于本地环境一致性验证。该镜像是 `legacy/experimental` 开发资产，不是
官方安装路径、registry 制品或对外部署承诺；正式本地路径仍是源码 checkout 与 exact
Wheel。

```bash
# 1. 构建镜像（多阶段：前端编译 + Python 安装）
bash scripts/docker.sh build

# 2. 启动容器
bash scripts/docker.sh start

# 3. 查看状态
bash scripts/docker.sh status

# 4. 查看日志
bash scripts/docker.sh logs

# 5. 重启
bash scripts/docker.sh restart

# 6. 停止并删除容器
bash scripts/docker.sh stop
```

容器配置：

| 项目 | 值 |
|------|-----|
| 镜像名 | `tianshu` |
| 容器名 | `tianshu` |
| 端口映射 | `127.0.0.1:$TIANSHU_PORT:8000`（默认仅宿主回环） |
| 数据卷 | `tianshu-data:/data` |
| 工作区挂载 | 项目根目录 → `/workspace` |

## 环境配置

两种模式都从项目根目录的 `.env` 文件加载配置（参考 `.env.example`）：

```bash
cp .env.example .env
# 编辑 .env 填入实际配置
```

关键变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TIANSHU_HOST` | `127.0.0.1` | 本地脚本默认监听地址；Docker 容器内由 helper 安全覆盖 |
| `TIANSHU_PORT` | `8000` | 服务端口 |
| `TIANSHU_DOCKER_BIND_HOST` | `127.0.0.1` | Docker 宿主发布地址；公开前必须启用 secure-remote |
| `TIANSHU_SECURITY_MODE` | `trusted-local` | 本地或远程安全运行模式 |
| `TIANSHU_LLM_API_KEY` | - | LLM API 密钥 |
| `TIANSHU_DB_PATH` | `.tianshu/tianshu.db` | 数据库路径 |
