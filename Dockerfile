# Legacy/local validation image only. This is not an official release or registry artifact.

# ========== Stage 1: Build the desktop Web payload ==========
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build
# vite emits /src/tianshu/web/static from this work directory.

# ========== Stage 2: Build the same self-contained Python wheel ==========
FROM python:3.12-slim-bookworm AS wheel-builder
WORKDIR /build

COPY pyproject.toml MANIFEST.in README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY build_backend/ ./build_backend/
COPY src/ ./src/
COPY --from=frontend-builder /src/tianshu/web/static ./src/tianshu/web/static/

# The in-tree backend rejects a wheel without the Web payload.
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /dist .

# ========== Stage 3: Minimal non-root runtime ==========
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/MJ-CJM/tianshu" \
      org.opencontainers.image.licenses="MIT" \
      io.tianshu.distribution-status="legacy-local-validation"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/tianshu \
    TIANSHU_DB_PATH=/data/tianshu.db \
    TIANSHU_ARTIFACT_DIR=/data/artifacts \
    TIANSHU_MEMORY_DIR=/data/memory \
    TIANSHU_RUNTIME_PERSONAS_DIR=/data/personas \
    TIANSHU_PLUGINS_DIR=/data/plugins \
    TIANSHU_LOG_DIR=/data/logs \
    TIANSHU_WORKSPACE_STAGING_ROOT=/data/workspaces \
    TIANSHU_WORKSPACE_DIR=/workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tianshu \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/tianshu tianshu \
    && install -d -o 10001 -g 10001 /app /data /workspace

WORKDIR /app
COPY --from=wheel-builder /dist/tianshu_agent_os-*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/tianshu_agent_os-*.whl \
    && python -c "from pathlib import Path; [path.unlink() for path in Path('/tmp').glob('tianshu_agent_os-*.whl')]"

VOLUME ["/data", "/workspace"]
EXPOSE 8000
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/live', timeout=3).read()"]

# scripts/docker.sh supplies the validated trusted-local container boundary
# variables (or a complete secure-remote profile) before starting this runtime.
CMD ["uvicorn", "tianshu.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
