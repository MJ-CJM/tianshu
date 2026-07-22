# G1 Authentication Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six security and usability blockers found in the independent review of commit `9fe77cc` without weakening production defaults.

**Architecture:** Keep the parent pure-ASGI boundary canonical. Restrict provider ingress to a dedicated namespace, refuse mutable Universe apps in secure mode, and make local trust depend on a loopback listener or an explicit Docker loopback-publish proof. Put browser and CLI authentication lifecycle behind shared clients, and derive every governance actor from an authenticated context or a channel-verified sender.

**Tech Stack:** FastAPI/Starlette, Pydantic v2, SQLite, Typer/httpx, React/TypeScript, Vitest, pytest.

## Global Constraints

- Use TDD for every behavior change and preserve `trusted-local` loopback ergonomics.
- `secure-remote` remains fail-closed for HTTP, WebSocket, MCP, Webhook, and Universe variants.
- Browser credentials are HttpOnly cookies only; never localStorage, sessionStorage, or URL query tokens.
- CLI environment PAT takes precedence over the `0600` credential file.
- Do not stage unrelated Phase 2/3/4 planning documents.

---

### Task 1: Provider webhook admission

**Files:**
- Modify: `src/tianshu/gateway/bot_manager.py`
- Modify: `src/tianshu/gateway/feishu/settings.py`
- Test: `tests/gateway/test_bot_manager.py`
- Test: `tests/gateway/test_auth.py`

**Interfaces:**
- Consumes: `app.state.public_webhook_paths: set[str]`
- Produces: validated exact paths under `/channels/{provider}/...`

- [x] Add failing tests proving `/api/*`, existing route conflicts, duplicate webhook paths, and empty secure-remote Feishu verifier are rejected.
- [x] Run the new tests and confirm the failures occur before route registration.
- [x] Validate provider namespace and inspect `app.routes` before `attach_webhook_router()`; reserve the path atomically in the manager.
- [x] Require `encrypt_key` or `verification_token` for secure-remote Feishu webhook mode; retain Telegram's existing secret requirement.
- [x] Re-run focused webhook and gateway tests.

### Task 2: Immutable secure runtime and local trust

**Files:**
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/universe/launcher.py`
- Modify: `scripts/docker.sh`
- Test: `tests/gateway/test_auth.py`
- Test: `tests/universe/test_launcher.py`
- Test: `tests/scripts/test_docker_sh.py`

**Interfaces:**
- Produces: `resolve_boot_plan()` refusal for every secure-remote worktree
- Produces: trusted-local loopback validation and explicit container loopback-publish proof

- [x] Add failing tests for secure worktree refusal, trusted-local non-loopback bind refusal, and public Docker publish refusal.
- [x] Add a launcher security smoke proving anonymous protected requests return 401 and a valid token reaches the handler.
- [x] Reject all secure-remote worktree records until G4 provides an immutable parent boundary.
- [x] Require loopback host for normal trusted-local; allow container wildcard only with the explicit container flag and loopback host publication enforced by the helper.
- [x] Re-run launcher, script, and auth tests.

### Task 3: MCP capability scopes

**Files:**
- Modify: `src/tianshu/gateway/auth.py`
- Modify: `src/tianshu/gateway/mcp_server.py`
- Test: `tests/gateway/test_mcp_auth.py`

**Interfaces:**
- Produces: MCP transport admission for either `mcp:read` or `mcp:submit`
- Produces: per-tool `_require_scope()` checks

- [x] Add a failing submit-only PAT test and a read-token submit denial test.
- [x] Change the parent MCP admission rule to accept either MCP scope.
- [x] Require `mcp:read` in every read tool and `mcp:submit` in the submit tool.
- [x] Re-run MCP auth and legacy MCP tests.

### Task 4: Canonical governance actors

**Files:**
- Modify: `src/tianshu/gateway/edicts_api.py`
- Modify: channel approval/plan-review handlers found by source inventory
- Test: gateway REST and channel approval tests

**Interfaces:**
- Consumes: `get_auth_context(request).principal.id`
- Consumes: provider-verified sender identity
- Produces: persisted decree/decision actor that cannot be supplied by request bodies

- [x] Add failing body-spoof tests for approve, reject, plan review, and tool decisions.
- [x] Add failing channel tests proving actor comes from the verified Feishu/Telegram sender.
- [x] Replace `human`/`emperor` constants at governance mutation points with canonical identities.
- [x] Re-run REST and channel gateway tests.

### Task 5: Web and CLI authentication lifecycle

**Files:**
- Create: `web/src/auth/AuthProvider.tsx`
- Create: `web/src/auth/LoginGate.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/api/client.ts`
- Modify: `src/tianshu/cli/client.py`
- Create: `src/tianshu/cli/commands/auth.py`
- Modify: `src/tianshu/cli/main.py`
- Test: Web Vitest auth tests
- Test: `tests/cli/test_auth_client.py`

**Interfaces:**
- Web consumes: `GET /api/auth/mode`, `POST /api/auth/session`, `GET /api/auth/me`, `DELETE /api/auth/session`
- CLI produces: `auth login`, `auth logout`, `auth whoami`
- CLI storage: owner-only JSON file containing an opaque session credential; `TIANSHU_API_TOKEN` remains first priority

- [x] Add failing Web tests for secure login gate, PAT exchange, logout, and absence of browser token persistence/query usage.
- [x] Implement AuthProvider state discovery and secure-mode login gate using credentialed cookie requests only.
- [x] Add failing CLI tests for `0600` writes, env precedence, logout, whoami, and one refresh/retry after 401.
- [x] Implement the auth command group and credential-aware client without printing stored secrets.
- [x] Run Web typecheck/tests and CLI tests.

### Task 6: Verification and commit

**Files:** all files above plus this plan.

- [x] Run focused security tests.
- [x] Run `pytest tests/gateway -q`.
- [x] Run Web typecheck and tests.
- [x] Run Ruff, mypy, import-linter, and `git diff --check`.
- [x] Stage only this G1 review hardening increment.
- [x] Commit with `fix: harden identity boundaries after security review`.
