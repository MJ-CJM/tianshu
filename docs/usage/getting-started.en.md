# Build, Run, and Deploy

[中文](getting-started.md)

## Project Structure

```
tianshu/
├── src/tianshu/           # Python backend + CLI
│   └── web/static/        # Frontend build output (gitignored)
├── web/                   # Frontend source (React + Vite + TypeScript)
├── pyproject.toml         # Python dependencies
└── Dockerfile             # legacy/experimental local container verification
```

## Prerequisites

- Python >= 3.12
- Node.js >= 20
- Docker (only needed for local container verification)

---

## 1. Local Development (Frontend and Backend Separated)

The frontend and backend start separately and are wired together through the Vite proxy.

### One-Command Script (Recommended)

`scripts/local.sh` wraps the manual steps below into managed background services
(PID files under `.tianshu/`, logs in `.tianshu/uvicorn.log` and `.tianshu/vite.log`):

```bash
# First time / after dependency changes: install Python deps (with the cli extra) + npm ci + frontend build
./scripts/local.sh build

# Dev mode: starts uvicorn (hot reload) and the Vite dev server together; visit http://localhost:7999
./scripts/local.sh start --dev

# Day-to-day operations
./scripts/local.sh status    # process status + health check
./scripts/local.sh logs      # tail the logs
./scripts/local.sh restart   # restart with the original flags
./scripts/local.sh stop      # graceful shutdown
```

Before starting, the script exports every variable in `.env` into the environment.
This is a real difference from running uvicorn by hand: settings declared on
`TianshuSettings` are read from `.env` either way, but runtime secrets read via
`os.getenv` (such as `TIANSHU_SECRET_MASTER_KEY`) are only visible when exported
as environment variables. Once you configure a master key, prefer starting
through the script.

The manual step-by-step flow below is for readers who want to see each moving part.

### Backend

```bash
# Install dependencies (first time / after dependency changes)
pip install -e .

# Configure environment variables
cp .env.example .env
# Edit .env and fill in TIANSHU_LLM_API_KEY, etc.

# Start (with hot reload)
tianshu serve --reload --port 8000
```

The backend listens on `http://localhost:8000`, serving the `/api/*` and `/health` endpoints.

### Frontend

```bash
cd web

# Install dependencies (first time / after dependency changes)
npm install

# Start the dev server
npm run dev
```

The frontend listens on `http://localhost:7999`; Vite automatically proxies `/api` and `/health` to the backend on port 8000.

**During development, visit `http://localhost:7999`.**

---

## 2. Local All-in-One Run

Build the frontend first, then let the backend serve the static files directly, all on a single port.

```bash
# Build the frontend
cd web && npm run build && cd ..

# Start the backend: static assets are located inside the package automatically,
# so TIANSHU_STATIC_DIR is not required
tianshu serve
```

Visit `http://localhost:8000` for both the API and the Web UI.

The managed equivalent is `./scripts/local.sh start` (without `--dev`); it expects
a prior `./scripts/local.sh build` and warns when the static directory is missing.

---

## 3. Legacy Docker Local Verification

The Dockerfile is a `legacy/experimental` development asset, used only to verify the local
container path; it is not an official installation path and is not published to any registry.
The official local path remains a source checkout, or the exact wheel built from that same
checkout.

```bash
bash scripts/docker.sh build
bash scripts/docker.sh start
bash scripts/docker.sh status
```

The Dockerfile uses a three-stage build:

1. Node 20 builds the Web static payload;
2. Python 3.12 uses the in-tree build backend to build a wheel containing the Web assets and
   license notices;
3. A non-root Python 3.12 runtime installs that wheel and reads the Web payload from the
   package.

Use `scripts/docker.sh` — it validates the trusted-local loopback publish address and passes
the exact container gateway to the authentication boundary. Do not bypass that boundary with a
bare `docker run`, and do not distribute this image as an official release artifact.

The image has been built and started locally to confirm that the runtime runs as the non-root
user `10001:10001` and that health/API/Web are all reachable. This only demonstrates that the
current local Docker path works; it does not make the image an official container, a registry
artifact, or a statement of cross-platform support.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TIANSHU_LLM_MODEL` | `gpt-4o-mini` | LLM model |
| `TIANSHU_LLM_API_KEY` | (required) | API key |
| `TIANSHU_DB_PATH` | `~/.tianshu/tianshu.db` | SQLite database path |
| `TIANSHU_HOST` | `127.0.0.1` | Loopback-only by default; the Docker helper overrides it inside the container |
| `TIANSHU_PORT` | `8000` | Listen port |
| `TIANSHU_SECURITY_MODE` | `trusted-local` | Remote deployments must explicitly set `secure-remote` and provide the security settings below |
| `TIANSHU_PUBLIC_BASE_URL` | empty | Public HTTPS address for secure-remote |
| `TIANSHU_ALLOWED_HOSTS` | empty | Exact Host list for secure-remote, comma-separated |
| `TIANSHU_ALLOWED_ORIGINS` | empty | Exact HTTPS Origin list for secure-remote |
| `TIANSHU_TRUSTED_PROXY_CIDRS` | empty | Trusted reverse-proxy CIDRs allowed to assert HTTPS |
| `TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH` | empty | `sha256:<64 hex>`; the server never stores the plaintext token |
| `TIANSHU_API_TOKEN` | empty | CLI/MCP Bearer token, kept only in client-side environment variables |
| `TIANSHU_WORKSPACE_DIR` | `.` | Agent working directory |
| `TIANSHU_STATIC_DIR` | empty (use the in-package Web payload) | Explicit override of the frontend static file directory |
| `TIANSHU_AGENT_MAX_ITERATIONS` | `20` | Maximum agent iterations |
| `TIANSHU_AGENT_TIMEOUT_SECONDS` | `300` | Agent execution timeout (seconds) |

In the Docker container, `TIANSHU_DB_PATH`, the persistence directories, and
`TIANSHU_WORKSPACE_DIR` are preset by the Dockerfile; Web static files come from the installed
wheel.

For `secure-remote`, the recommended CLI flow is to set `TIANSHU_API_URL` first and then run
`tianshu auth login`. During login the PAT is used only once, to exchange for a session; when
the access token later expires it is refreshed automatically once. The session file lives at
`~/.tianshu/credentials.json` and is forced to mode `0600`. Use `tianshu auth whoami` to
inspect the current principal, or `tianshu auth logout` to revoke and delete the local session.
`TIANSHU_API_TOKEN` still takes the highest precedence.

---

## Task Scheduling Boundary

Long-horizon tasks can run immediately or be scheduled to run once. The current single-node
runtime identity model cannot safely support cron / interval recurrence for long-horizon tasks,
so the Web UI, API, and scheduling tools all reject that combination; use a regular task when
you need periodic execution.

---

## 4. Local Wheel Builds and Distribution Status

Wheels and sdists build from the current source tree and pass the artifact manifest, license
notice, and Python dependency security checks. Tianshu is not yet published to PyPI or GHCR:
the supported installation path is a source checkout, or the exact wheel built from that same
checkout. For the full verification steps, see the
[Lean Developer Preview](lean-developer-preview.md).

---

## 5. CLI Usage

```bash
# The CLI ships with the base install
pip install -e .

# List available commands
tianshu --help
```
