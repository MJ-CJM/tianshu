#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
RUNTIME_DIR="$PROJECT_ROOT/.tianshu"

UVICORN_PID_FILE="$RUNTIME_DIR/uvicorn.pid"
VITE_PID_FILE="$RUNTIME_DIR/vite.pid"
UVICORN_LOG="$RUNTIME_DIR/uvicorn.log"
VITE_LOG="$RUNTIME_DIR/vite.log"

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  build          Install Python deps + build frontend
  start [--dev]  Start services (--dev: uvicorn + vite dev server)
  stop           Stop all running services
  restart        Restart services (preserves original flags)
  status         Check service status + health endpoint
  logs           Tail service logs

EOF
}

ensure_runtime_dir() {
    mkdir -p "$RUNTIME_DIR"
}

load_env() {
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.env"
        set +a
    fi
}

is_running() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pid_file"
    fi
    return 1
}

cmd_build() {
    echo "==> Installing Python dependencies (editable)..."
    pip install -e ".[cli]"

    if [[ -d "$PROJECT_ROOT/web" ]]; then
        echo "==> Installing frontend dependencies..."
        (cd "$PROJECT_ROOT/web" && npm ci)
        echo "==> Building frontend..."
        (cd "$PROJECT_ROOT/web" && npm run build)
    else
        echo "==> No web/ directory found, skipping frontend build."
    fi

    echo "==> Build complete."
}

cmd_start() {
    local dev_mode=false
    if [[ "${1:-}" == "--dev" ]]; then
        dev_mode=true
    fi

    ensure_runtime_dir
    load_env

    local host="${TIANSHU_HOST:-0.0.0.0}"
    local port="${TIANSHU_PORT:-8000}"

    # Check if already running
    if is_running "$UVICORN_PID_FILE"; then
        echo "Uvicorn is already running (PID: $(cat "$UVICORN_PID_FILE"))."
        return 1
    fi

    # Save mode for restart
    if $dev_mode; then
        echo "--dev" > "$RUNTIME_DIR/start_args"
    else
        echo "" > "$RUNTIME_DIR/start_args"
    fi

    if $dev_mode; then
        echo "==> Starting uvicorn (dev mode)..."
        nohup uvicorn tianshu.app:create_app --factory \
            --host "$host" --port "$port" \
            --reload \
            >> "$UVICORN_LOG" 2>&1 &
        echo $! > "$UVICORN_PID_FILE"
        echo "    Uvicorn PID: $(cat "$UVICORN_PID_FILE"), log: $UVICORN_LOG"

        if [[ -d "$PROJECT_ROOT/web" ]]; then
            echo "==> Starting vite dev server..."
            nohup sh -c "cd '$PROJECT_ROOT/web' && npm run dev" \
                >> "$VITE_LOG" 2>&1 &
            echo $! > "$VITE_PID_FILE"
            echo "    Vite PID: $(cat "$VITE_PID_FILE"), log: $VITE_LOG"
        fi
    else
        echo "==> Starting uvicorn (production mode)..."
        export TIANSHU_STATIC_DIR="${TIANSHU_STATIC_DIR:-$PROJECT_ROOT/src/tianshu/web/static}"
        nohup uvicorn tianshu.app:create_app --factory \
            --host "$host" --port "$port" \
            >> "$UVICORN_LOG" 2>&1 &
        echo $! > "$UVICORN_PID_FILE"
        echo "    Uvicorn PID: $(cat "$UVICORN_PID_FILE"), log: $UVICORN_LOG"
    fi

    echo "==> Services started."
}

cmd_stop() {
    local stopped=false

    if is_running "$VITE_PID_FILE"; then
        local pid
        pid=$(cat "$VITE_PID_FILE")
        echo "==> Stopping vite (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        rm -f "$VITE_PID_FILE"
        stopped=true
    fi

    if is_running "$UVICORN_PID_FILE"; then
        local pid
        pid=$(cat "$UVICORN_PID_FILE")
        echo "==> Stopping uvicorn (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        rm -f "$UVICORN_PID_FILE"
        stopped=true
    fi

    if $stopped; then
        echo "==> All services stopped."
    else
        echo "==> No running services found."
    fi
}

cmd_restart() {
    local args=""
    if [[ -f "$RUNTIME_DIR/start_args" ]]; then
        args=$(cat "$RUNTIME_DIR/start_args")
    fi

    cmd_stop
    # shellcheck disable=SC2086
    cmd_start $args
}

cmd_status() {
    load_env
    local port="${TIANSHU_PORT:-8000}"

    echo "--- Service Status ---"

    if is_running "$UVICORN_PID_FILE"; then
        echo "Uvicorn:  RUNNING (PID: $(cat "$UVICORN_PID_FILE"))"
    else
        echo "Uvicorn:  STOPPED"
    fi

    if is_running "$VITE_PID_FILE"; then
        echo "Vite:     RUNNING (PID: $(cat "$VITE_PID_FILE"))"
    else
        echo "Vite:     STOPPED"
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
    local files=()
    [[ -f "$UVICORN_LOG" ]] && files+=("$UVICORN_LOG")
    [[ -f "$VITE_LOG" ]] && files+=("$VITE_LOG")

    if [[ ${#files[@]} -eq 0 ]]; then
        echo "No log files found in $RUNTIME_DIR"
        return 1
    fi

    tail -f "${files[@]}"
}

# --- Main dispatch ---
case "${1:-}" in
    build)   cmd_build ;;
    start)   cmd_start "${2:-}" ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    *)       usage; exit 1 ;;
esac
