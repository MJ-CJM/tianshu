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

## Docker 模式 (`docker.sh`)

容器化运行，适合部署和环境一致性验证。

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
| 端口映射 | `$TIANSHU_PORT:8000`（默认 8000） |
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
| `TIANSHU_HOST` | `0.0.0.0` | 监听地址 |
| `TIANSHU_PORT` | `8000` | 服务端口 |
| `TIANSHU_LLM_API_KEY` | - | LLM API 密钥 |
| `TIANSHU_DB_PATH` | `.tianshu/tianshu.db` | 数据库路径 |
