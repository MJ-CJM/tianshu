#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
RUNTIME_DIR="$PROJECT_ROOT/.tianshu"

UVICORN_PID_FILE="$RUNTIME_DIR/uvicorn.pid"
VITE_PID_FILE="$RUNTIME_DIR/vite.pid"
UVICORN_LOG="$RUNTIME_DIR/uvicorn.log"
VITE_LOG="$RUNTIME_DIR/vite.log"

UVICORN_PORT="${TIANSHU_PORT:-8000}"
VITE_PORT=3000

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  build          Install Python deps + build frontend
  start [--dev]  Start services (--dev: uvicorn + vite dev server)
  stop [--dev]   Stop all running services
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

# --- Process management helpers ---

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

# Kill a process and all its children by PID file
stop_by_pid() {
    local pid_file="$1" label="$2"
    if [[ ! -f "$pid_file" ]]; then
        return 1
    fi

    local pid
    pid=$(cat "$pid_file")
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        return 1
    fi

    echo "==> Stopping $label (PID: $pid)..."
    # Kill the process tree: first children, then parent
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true

    # Wait up to 3s for graceful exit
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < 30 )); do
        sleep 0.1
        (( waited++ ))
    done

    # Force kill if still alive
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$pid_file"
    return 0
}

# Kill any process occupying a given port (fallback cleanup)
kill_port() {
    local port="$1"
    local pids
    pids=$(lsof -ti:"$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "    Cleaning up port $port (PIDs: $(echo $pids | tr '\n' ' '))..."
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 0.5
        # Force kill survivors
        pids=$(lsof -ti:"$port" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
            sleep 0.5
        fi
    fi
}

wait_port_free() {
    local port="$1" max_wait="${2:-10}"
    local waited=0
    while lsof -ti:"$port" >/dev/null 2>&1 && (( waited < max_wait )); do
        sleep 0.5
        (( waited++ ))
    done
    if lsof -ti:"$port" >/dev/null 2>&1; then
        echo "    WARNING: port $port still occupied after ${max_wait}s"
        return 1
    fi
    return 0
}

# --- Commands ---

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

    # Truncate logs on fresh start
    : > "$UVICORN_LOG"

    if $dev_mode; then
        echo "==> Starting uvicorn (dev mode)..."
        nohup uvicorn tianshu.app:create_app --factory \
            --host "$host" --port "$UVICORN_PORT" \
            --reload --reload-dir src \
            >> "$UVICORN_LOG" 2>&1 &
        echo $! > "$UVICORN_PID_FILE"
        echo "    Uvicorn PID: $(cat "$UVICORN_PID_FILE"), log: $UVICORN_LOG"

        if [[ -d "$PROJECT_ROOT/web" ]]; then
            # Ensure port is free before starting
            kill_port "$VITE_PORT"
            wait_port_free "$VITE_PORT" 5

            : > "$VITE_LOG"
            echo "==> Starting vite dev server..."
            # Use exec so the PID file tracks the actual node process, not an intermediate shell
            nohup bash -c 'cd "$1" && exec ./node_modules/.bin/vite' _ "$PROJECT_ROOT/web" \
                >> "$VITE_LOG" 2>&1 &
            echo $! > "$VITE_PID_FILE"
            echo "    Vite PID: $(cat "$VITE_PID_FILE"), log: $VITE_LOG"
        fi
    else
        echo "==> Starting uvicorn (production mode)..."
        export TIANSHU_STATIC_DIR="${TIANSHU_STATIC_DIR:-$PROJECT_ROOT/src/tianshu/web/static}"
        nohup uvicorn tianshu.app:create_app --factory \
            --host "$host" --port "$UVICORN_PORT" \
            >> "$UVICORN_LOG" 2>&1 &
        echo $! > "$UVICORN_PID_FILE"
        echo "    Uvicorn PID: $(cat "$UVICORN_PID_FILE"), log: $UVICORN_LOG"
    fi

    echo "==> Services started."
}

cmd_stop() {
    local stopped=false

    # Stop by PID file first
    if stop_by_pid "$VITE_PID_FILE" "vite"; then
        stopped=true
    fi
    if stop_by_pid "$UVICORN_PID_FILE" "uvicorn"; then
        stopped=true
    fi

    # Port-based fallback: clean up orphans
    local vite_orphans uvicorn_orphans
    vite_orphans=$(lsof -ti:"$VITE_PORT" 2>/dev/null || true)
    uvicorn_orphans=$(lsof -ti:"$UVICORN_PORT" 2>/dev/null || true)

    if [[ -n "$vite_orphans" ]]; then
        echo "==> Cleaning up orphan processes on port $VITE_PORT..."
        kill_port "$VITE_PORT"
        stopped=true
    fi
    if [[ -n "$uvicorn_orphans" ]]; then
        echo "==> Cleaning up orphan processes on port $UVICORN_PORT..."
        kill_port "$UVICORN_PORT"
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
    sleep 1
    # shellcheck disable=SC2086
    cmd_start $args
}

cmd_status() {
    load_env

    echo "--- Service Status ---"

    if is_running "$UVICORN_PID_FILE"; then
        echo "Uvicorn:  RUNNING (PID: $(cat "$UVICORN_PID_FILE"), port: $UVICORN_PORT)"
    else
        echo "Uvicorn:  STOPPED"
    fi

    if is_running "$VITE_PID_FILE"; then
        echo "Vite:     RUNNING (PID: $(cat "$VITE_PID_FILE"), port: $VITE_PORT)"
    else
        echo "Vite:     STOPPED"
    fi

    echo ""
    echo "--- Health Check ---"
    if curl -sf "http://localhost:${UVICORN_PORT}/health" -o /dev/null 2>/dev/null; then
        echo "http://localhost:${UVICORN_PORT}/health  OK"
    else
        echo "http://localhost:${UVICORN_PORT}/health  UNREACHABLE"
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
