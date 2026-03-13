# ========== Stage 1: Build frontend ==========
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY tianshu-web/package.json tianshu-web/package-lock.json* ./
RUN npm ci --prefer-offline
COPY tianshu-web/ ./
RUN npm run build
# Output: /build/dist/

# ========== Stage 2: Python backend + static files ==========
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY tianshu/pyproject.toml ./
COPY tianshu/src/ ./src/
RUN pip install --no-cache-dir ".[cli]"

# Copy frontend build output from Stage 1
COPY --from=frontend-builder /build/dist /app/static

ENV TIANSHU_DB_PATH="/data/tianshu.db"
ENV TIANSHU_WORKSPACE_DIR="/workspace"
ENV TIANSHU_STATIC_DIR="/app/static"
VOLUME ["/data", "/workspace"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "tianshu.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
