# Phase 1 Public-safe Foundation Implementation Plan

> **For Codex:** Execute continuously with test-driven development. The user waived intermediate approval pauses on 2026-07-11; every increment still requires its automated gate before the next increment begins.

**Goal:** Deliver G1 as a public-safe Agent OS foundation: authenticated remote access, a versioned governance contract, honest executor capabilities, one mandatory process boundary, isolated workspaces, reproducible fresh install, and fail-closed MCP/release security.

**Architecture:** Preserve `trusted-local` as the low-friction loopback mode and make `secure-remote` explicit and fail-closed. Freeze requested governance at submission and resolve an effective contract against an executor manifest and host probe before any side effect. Route arbitrary process execution through one async gateway, bind every governed run to an isolated Git staging lease, and keep apply as a separate decision-bound operation. Ship the same package resources and wheel in local, CI, and container paths.

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Pydantic v2, SQLite, cryptography/Fernet, asyncio, Git worktrees, React/TypeScript, Vitest, pytest, uv, Docker, GitHub Actions.

**Decisions:**

- G1 includes the narrow append-only `system_audit_events` foundation so MCP/auth denials are persistent; G2 extends the same schema.
- Secure-remote remote MCP remains disabled unless a trusted egress boundary is configured; DNS preflight alone is not advertised as rebinding protection.
- G1 support claims Linux and macOS only. Windows is not claimed while the migration lock depends on `fcntl`.
- Native becomes `managed` only after the gateway and workspace compatibility gates pass. Claude Code/Codex headless remain `contained + experimental`.
- G1 proves synchronous failure/cancel isolation and governed apply. Crash-point exactly-once remains false until the G2 side-effect journal exists.

---

## Increment 1: Runtime modes, identity, and route protection

**Files:**

- Create: `src/tianshu/models/principal.py`
- Create: `src/tianshu/gateway/auth.py`
- Create: `src/tianshu/storage/auth_repo.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/app.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/gateway/mcp_server.py`
- Modify: `src/tianshu/universe/launcher.py`
- Modify: `src/tianshu/cli/client.py`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/hooks/useWebSocket.ts`
- Modify: `.env.example`
- Test: `tests/gateway/test_auth.py`
- Test: `tests/gateway/test_ws_auth.py`
- Test: `tests/gateway/test_mcp_auth.py`

### 1.1 Write failing configuration and route-matrix tests

- Assert default host is `127.0.0.1` and default mode is `trusted-local`.
- Assert secure-remote validation requires a bootstrap/admin token hash, explicit origins, allowed hosts, and trusted TLS proxy configuration.
- Assert `/health/live` is public; static assets and signed channel webhooks follow the explicit matrix; REST, `/api/ws`, and `/mcp` require identity in secure-remote.
- Assert invalid Host/Origin is rejected before route execution.

Run: `uv run pytest tests/gateway/test_auth.py tests/gateway/test_ws_auth.py tests/gateway/test_mcp_auth.py -q`
Expected: FAIL for missing models/middleware.

### 1.2 Implement principal, token storage, and middleware

- Define immutable `Principal`, `AuthContext`, roles/scopes, and authentication source.
- Store only token hashes and metadata (`id`, prefix, scopes, created/expires/revoked timestamps); return plaintext once at issue time.
- Use constant-time hash comparison and structured auth errors with correlation id.
- In trusted-local, synthesize a local operator only for loopback clients; forwarded/public clients do not inherit it.
- In secure-remote, accept bearer tokens for CLI/MCP and HttpOnly session cookies for Web/WS. Do not place browser credentials in a WebSocket URL.
- Apply Host/Origin/TLS-proxy rules at the parent ASGI boundary so mounted MCP cannot bypass them.

### 1.3 Wire clients and lifecycle commands

- Add issue/list/rotate/revoke endpoints and CLI configuration for bearer tokens.
- Make the Web client use credentialed same-origin requests and make WS authentication consume the secure cookie.
- Remove page-local authentication bypasses discovered by the auth inventory.

### 1.4 Verify and commit

Run the focused tests, then `uv run pytest tests/gateway -q`, Web typecheck/tests, and an anonymous negative smoke against secure-remote.

Commit: `feat: add secure runtime modes and identity`

---

## Increment 2: Governance Contract v1 and executor capability truth

**Files:**

- Create: `src/tianshu/models/governance_contract.py`
- Create: `src/tianshu/executor/capabilities.py`
- Create: `src/tianshu/executor/adapters/__init__.py`
- Create: `src/tianshu/executor/adapters/protocol.py`
- Modify: `src/tianshu/models/edict.py`
- Modify: `src/tianshu/models/api.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/edict_repo.py`
- Modify: `src/tianshu/storage/mappers.py`
- Modify: `src/tianshu/executor/keqing/adapter.py`
- Modify: `src/tianshu/executor/executor.py`
- Modify: `src/tianshu/gateway/edicts_api.py`
- Modify: `web/src/components/edict/EdictForm.tsx`
- Test: `tests/governance/test_contract_v1.py`
- Test: `tests/governance/test_legacy_edict_mapping.py`
- Test: `tests/compat/test_executor_capabilities.py`
- Test: gateway contract-preview tests

### 2.1 Write failing canonicalization and compatibility tests

- Freeze `RequestedGovernanceContractV1` and `EffectiveGovernanceContractV1` with `extra='forbid'`, schema version, stable canonical JSON/hash, positive budgets/deadlines, and immutable semantics.
- Cover objective, acceptance, executor, mandatory/advisory capabilities, permissions, network, workspace/base/apply, budgets, and recovery.
- Prove legacy Edict mapping preserves executor, policy profile, host grants, acceptance, and budgets.
- Prove new + legacy conflicts return 422 instead of silent precedence.
- Reproduce the current `EdictRuntimeRequest.executor/policy_profile` field-loss bug first.

### 2.2 Implement contract persistence and preview

- Add an immutable requested-contract record/reference in a new versioned migration; do not edit the G0 baseline migration checksum.
- Persist requested contract at submission. Store an effective contract per run/memorial, not globally on the Edict.
- Expose preview and structured capability mismatches before dispatch.

### 2.3 Implement capability manifests and resolver

- Use `enforced`, `best_effort`, `observed`, and `unsupported` states for action interception, workspace/network/secret control, budget enforcement, decision bridge, pause, durable resume, event fidelity, artifact export, receipts, restore point, and governed apply.
- Validate level consistency. Mandatory capabilities accept only `enforced`; advisory gaps remain visible.
- Register Native, Claude Code, and Codex manifests. Do not mark Native managed until Increments 3–4 pass.
- Reject mandatory mismatch before executor/process/workspace side effects.

### 2.4 Verify and commit

Run focused governance/compat/gateway tests, storage migration round-trip tests, then backend type/lint checks.

Commit: `feat: add versioned governance contracts and capabilities`

---

## Increment 3: Mandatory ExecutionGateway and process inventory gate

**Files:**

- Create: `src/tianshu/executor/execution_gateway.py`
- Create: `src/tianshu/executor/process_backend.py`
- Create: `src/tianshu/executor/git_backend.py`
- Modify: `src/tianshu/tools/builtins.py`
- Modify: `src/tianshu/tools/lark_cli.py`
- Modify: `src/tianshu/tools/grep.py`
- Modify: `src/tianshu/executor/orchestrator/checks.py`
- Modify: `src/tianshu/executor/keqing/executor.py`
- Modify: `src/tianshu/tools/mcp/transport.py`
- Modify: Universe, LSP, eval, and shadow-snapshot process callers
- Modify: bootstrap wiring modules
- Test: `tests/security/test_execution_gateway.py`
- Test: `tests/security/test_execution_gateway_processes.py`
- Test: `tests/security/test_execution_gateway_secrets.py`
- Test: `tests/security/test_execution_gateway_sandbox.py`
- Test: `tests/security/test_mcp_command_boundary.py`
- Test: `tests/architecture/test_no_direct_process_launch.py`

### 3.1 Write failing guard and lifecycle tests

- Require actor, contract, correlation id, purpose, exclusive argv/shell command, relative cwd, clean-env policy, network policy, sandbox requirement, timeout, and output bounds.
- Mandatory guard exception/timeout fails closed; advisory diagnostics emit a gap.
- Test stdout/stderr concurrent drain, truncation, secret redaction, timeout/cancel, and whole process-group cleanup.
- Secure-remote with required sandbox unavailable must reject rather than host-fallback.

### 3.2 Implement the gateway

- Provide async `run()` for bounded commands and `start()` handles for streaming/long-lived processes.
- Use explicit shell requests (`bash --noprofile --norc -c`) and run bash analysis before spawn.
- Serialize only env key names/secret references in audit records; redact output/errors before returning.
- Keep fixed Git operations behind named methods, not arbitrary argv.

### 3.3 Migrate high-risk callers

- Migrate acceptance checks, shell tool, Lark CLI, Keqing, MCP stdio, Universe gate/sandbox first.
- Migrate grep/LSP/eval and fixed Git calls next.
- Keep launcher/deployer `os.exec*` and MCP SDK spawn as narrowly documented low-level exceptions.

### 3.4 Add the architecture gate

- Detect subprocess aliases, asyncio spawns, `Popen`, `os.system/popen/spawn/exec`, and new files.
- Allow only exact low-level backend/lifecycle/maintenance locations; no directory-wide exemption.

### 3.5 Verify and commit

Run all security/process/architecture tests, existing Keqing/MCP/Universe suites, and import-linter.

Commit: `feat: enforce a unified external execution boundary`

---

## Increment 4: Isolated WorkspaceService and governed apply

**Files:**

- Create: `src/tianshu/executor/workspace_context.py`
- Create: `src/tianshu/executor/workspace_service.py`
- Modify: `src/tianshu/bootstrap/wiring_tools.py`
- Modify: `src/tianshu/bootstrap/wiring_executor.py`
- Modify: `src/tianshu/executor/policy_hook.py`
- Modify: `src/tianshu/executor/executor.py`
- Modify: `src/tianshu/executor/worker.py`
- Modify: `src/tianshu/executor/dag_scheduler.py`
- Modify: `src/tianshu/executor/keqing/executor.py`
- Modify: `src/tianshu/executor/shadow_snapshot.py`
- Test: `tests/executor/test_workspace_staging.py`
- Test: `tests/executor/test_workspace_changes.py`
- Test: `tests/integration/test_pre_run_rollback.py`
- Test: `tests/integration/test_governed_apply.py`
- Test: `tests/integration/test_workspace_concurrency.py`

### 4.1 Write failing lease/isolation tests

- Start with Git source workspaces and explicit base revision; scratch-only work uses `apply_mode=none`.
- Require a clean source for governed apply and a distinct staging lease per run/retry/follow-up.
- Prove restore point exists before the first process starts.
- Prove success, failure, timeout, cancel, and CLI crash leave source byte-identical.

### 4.2 Implement workspace binding

- Create `WorkspaceLease` states and ContextVar-based provider.
- Make tools and PolicyHook resolve the active staging root per call; governed mutation without a lease fails closed.
- Run Native, Keqing, DAG nodes, and acceptance checks in the bound staging lease. Serialize mutating calls for shared DAG staging in G1.

### 4.3 Implement canonical changes and apply

- Collect stable add/modify/delete/rename/mode/symlink/binary/untracked metadata and hash.
- Require a short-lived decision token bound to principal, scope, reason, base revision, restore point, and change-set hash.
- Recheck source drift, apply only the approved change set, rollback synchronous failures, and issue an apply receipt.
- Retain legacy shadow snapshot reads but mark new restore-point/change-set records canonical.

### 4.4 Promote honest Native capability and commit

- Run compatibility tests. Promote only the capability bits that are now enforced; keep `durable_resume` false until G2.

Commit: `feat: isolate governed runs and require approved apply`

---

## Increment 5: Self-contained wheel, default personas, mock provider, doctor, and readiness

**Files:**

- Create: `src/tianshu/resources/__init__.py`
- Move: root persona/template resources into `src/tianshu/resources/`
- Create: `src/tianshu/resources/default_personas.py`
- Create: `src/tianshu/providers/mock_provider.py`
- Create: `src/tianshu/diagnostics/`
- Create: `src/tianshu/gateway/health.py`
- Modify: `pyproject.toml`, config, LLM/provider wiring, persona/memory/resource loaders, Web mount, migrations, doctor command
- Test: resource, mock-provider, doctor, health/readiness, and fresh-install suites
- Create: `.github/workflows/release-smoke.yml`

### 5.1 Package-resource tests first

- Build a wheel and assert it contains six persona departments, persona templates and sources, builtin skills, Web assets/brand, executor templates, and license metadata.
- Install into a temporary venv with repo-external cwd, fresh HOME, cleared PYTHONPATH, and empty DB.

### 5.2 Move resource resolution and seed personas

- Expose stable resource locator functions; remove repo-root inference.
- Add a one-time versioned data migration that seeds six defaults only when the persona table is empty.

### 5.3 Add explicit demo profile and mock provider

- `demo` must be opt-in, deterministic, non-networked, zero-cost, and clearly marked in result/evidence.
- Implement both chat and streaming paths in the common LLM dispatch so direct `LLMClient` callers are covered.
- Never silently fall back from a live provider to mock.

### 5.4 Add structured doctor and health split

- Produce stable table/JSON `DoctorReport`; check mode/auth, provider, DB/schema, resources, workspace, port/readiness, sandbox, MCP, and dependencies without exposing secret prefixes.
- Add public liveness and readiness. DB/migration, scheduler, worker, or mandatory sandbox failures return 503 readiness; optional integrations do not.

### 5.5 Verify fresh-install black box and commit

- From the installed wheel, run doctor in demo mode, start Web/API, list personas/skills, and finish one governed mock Edict without network.

Commit: `feat: ship a self-contained offline-capable distribution`

---

## Increment 6: MCP secret/admission security, non-root container, CI, and threat model

**Files:**

- Create: `src/tianshu/security/mcp_remote_policy.py`
- Create: `src/tianshu/security/mcp_stdio_policy.py`
- Create: `src/tianshu/auditor/system_log.py`
- Create: `src/tianshu/storage/system_audit_repo.py`
- Modify: MCP persistence/config/manager/transport/API and migrations
- Modify: `Dockerfile`, `.dockerignore`, `SECURITY.md`, CI
- Create: `.github/workflows/security.yml`
- Create: `.github/workflows/release.yml`
- Create: `docs/ops/threat-model.md`
- Test: MCP migration/policy/audit/container/release-smoke suites

### 6.1 Write failing MCP persistence and negative-policy tests

- Migrate legacy env/header mappings to ciphertext and non-secret key metadata; active DB must not contain sentinels.
- Missing/invalid master key with non-empty legacy secret fails closed and preserves the source DB/backup.
- Reject HTTP/file/userinfo/private/link-local/metadata/reserved/mixed DNS URLs; disable redirects.
- In secure-remote, deny remote MCP without trusted egress.
- Default new stdio server to disabled/no tools; require exact executable realpath, argv fingerprint, workspace, env-key, network, actor/reason/expiry grant. Config changes revoke the grant.

### 6.2 Implement encrypted persistence and admission

- Reuse the low-level Fernet codec without making migrations depend on a high-level store.
- Record persistent allowlisted audit details only; never secret values, queries, or raw headers.
- Route stdio spawn through ExecutionGateway. Unknown discovered tools are not registered beyond the grant.

### 6.3 Build the release container

- Use frontend builder, frozen wheel/dependency builder, and minimal Python runtime stages.
- Run as fixed non-root user; install the tested wheel/profile; remove Node/compiler/build tools; use readiness healthcheck.
- Smoke with read-only root filesystem, fresh volumes, demo provider, Web/resources, and governed mock Edict.

### 6.4 Add security/release evidence

- Freeze CI sync/export, wheel/container smoke, dependency and code scanning, Python/Web SBOM, artifact hashes, and Developer Preview dry-run release.
- Scanners unavailable is a failing/unavailable state, not a clean pass.
- Document trusted-local, secure-remote, auth/TLS proxy, MCP, external executor, sandbox, workspace/apply, self-evolution, backup-secret, and single-node limitations.

### 6.5 Verify and commit

Run focused MCP/security tests, wheel/container smoke where a daemon exists, workflow syntax checks, and docs truth tests.

Commit: `feat: close G1 MCP and release security gates`

---

## G1 Final Gate

Run from a clean tree:

1. `uv sync --frozen --extra all --extra dev`
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run mypy`
5. `uv run lint-imports`
6. `uv run pytest -m 'not slow' -q`
7. Focused secure-remote, process-group, workspace, fresh-wheel, MCP-negative, and readiness tests.
8. `cd web && npm ci && npm run lint && npm run typecheck && npm test -- --run && npm run build`
9. Container non-root/read-only/demo smoke when Docker is available; otherwise record the daemon absence as unverified, never passed.

Gate output must include: capability matrix update, runtime route matrix, process inventory/exception list, wheel manifest, doctor JSON, threat model, security negative-test report, and a truthful list of external evidence still pending.
