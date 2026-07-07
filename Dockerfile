# ========== Stage 1: Build frontend ==========
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci --prefer-offline
COPY web/ ./
RUN npm run build
# Output: /build/src/tianshu/web/static/

# ========== Stage 2: Python backend + static files ==========
FROM ubuntu:24.04
WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip \
        git curl wget jq zip unzip \
        build-essential ca-certificates openssh-client \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --break-system-packages ".[cli]"

# Copy frontend build output from Stage 1
# vite outDir = "../src/tianshu/web/static" relative to /build → /src/tianshu/web/static
COPY --from=frontend-builder /src/tianshu/web/static /app/static

ENV TIANSHU_DB_PATH="/data/tianshu.db"
ENV TIANSHU_WORKSPACE_DIR="/workspace"
ENV TIANSHU_STATIC_DIR="/app/static"
VOLUME ["/data", "/workspace"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 经 launcher 引导：读 deploy 指针决定从主仓或某代码变体 worktree 启动（支持代码变体晋升后重启生效）
CMD ["python", "-m", "tianshu.universe.launcher"]
