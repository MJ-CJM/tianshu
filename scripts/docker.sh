#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

IMAGE_NAME="tianshu"
CONTAINER_NAME="tianshu"

usage() {
    cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  build    Build Docker image
  start    Start container
  stop     Stop and remove container
  restart  Restart container
  status   Check container status + health
  logs     Tail container logs

EOF
}

load_env() {
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.env"
        set +a
    fi
}

cmd_build() {
    echo "==> Building Docker image: $IMAGE_NAME ..."
    docker build -t "$IMAGE_NAME" "$PROJECT_ROOT"
    echo "==> Build complete."
}

cmd_start() {
    load_env
    local port="${TIANSHU_PORT:-8000}"
    local bind_host="${TIANSHU_DOCKER_BIND_HOST:-127.0.0.1}"
    local container_boundary="true"
    if [[ "${TIANSHU_SECURITY_MODE:-trusted-local}" == "secure-remote" ]]; then
        container_boundary="false"
    fi

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
            echo "Container '$CONTAINER_NAME' is already running."
            return 1
        fi
        echo "==> Removing stopped container '$CONTAINER_NAME'..."
        docker rm "$CONTAINER_NAME" > /dev/null
    fi

    echo "==> Starting container: $CONTAINER_NAME (port $bind_host:$port:8000)..."
    docker run -d \
        --name "$CONTAINER_NAME" \
        --env-file "$PROJECT_ROOT/.env" \
        -e TIANSHU_HOST=0.0.0.0 \
        -e TIANSHU_TRUSTED_LOCAL_CONTAINER_BOUNDARY="$container_boundary" \
        -p "${bind_host}:${port}:8000" \
        -v tianshu-data:/data \
        -v "$PROJECT_ROOT":/workspace \
        "$IMAGE_NAME"

    echo "==> Container started."
}

cmd_stop() {
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "==> Stopping container: $CONTAINER_NAME ..."
        docker stop "$CONTAINER_NAME" > /dev/null
    fi

    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "==> Removing container: $CONTAINER_NAME ..."
        docker rm "$CONTAINER_NAME" > /dev/null
    fi

    echo "==> Container stopped and removed."
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_status() {
    load_env
    local port="${TIANSHU_PORT:-8000}"

    echo "--- Container Status ---"

    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "Container:  RUNNING"
        docker ps --filter "name=$CONTAINER_NAME" --format "table {{.ID}}\t{{.Status}}\t{{.Ports}}"
    elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "Container:  STOPPED"
        docker ps -a --filter "name=$CONTAINER_NAME" --format "table {{.ID}}\t{{.Status}}"
    else
        echo "Container:  NOT FOUND"
    fi

    echo ""
    echo "--- Health Check ---"
    if curl -sf "http://localhost:${port}/health" -o /dev/null 2>/dev/null; then
        echo "http://localhost:${port}/health  OK"
    else
        echo "http://localhost:${port}/health  UNREACHABLE"
    fi
}

cmd_logs() {
    if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "Container '$CONTAINER_NAME' not found."
        return 1
    fi

    docker logs -f "$CONTAINER_NAME"
}

# --- Main dispatch ---
case "${1:-}" in
    build)   cmd_build ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    *)       usage; exit 1 ;;
esac
