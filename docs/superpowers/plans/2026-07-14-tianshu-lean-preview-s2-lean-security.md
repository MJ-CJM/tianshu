# Tianshu Lean Preview S2 Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Lean public-security floor: tamper-evident SystemAudit with scoped export, atomic security-event writes, encrypted MCP override secrets with complete key rotation, and fail-closed defaults for remote and unapproved stdio MCP.

**Architecture:** Append two migrations after the live ledger tail. The first adds an immutable hash-chained audit authority and composes it into auth/estop/MCP state transactions. The second rebuilds MCP override secret columns from plaintext JSON to encrypted canonical mappings. This Lean plan deliberately does not implement full SSRF/DNS connect pinning or persistent stdio grants; instead those paths remain disabled until P2-A1/P2-A2.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite/WAL/triggers, Fernet, FastAPI, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Require the G1.5 report status `passed` before Task 1.
- Compute migration tail at runtime. At design time it is v6; likely S2 audit/cipher migrations are v7/v8, but the implementation must use actual `N+1` and `N+2` and must not rewrite v1–v6.
- Audit metadata is an action-specific allowlist. Never store raw principal IDs, display names, IPs, URLs, paths/queries, tokens, cookies, headers, env values, request/response bodies, stdout/stderr, or exception strings.
- Security state mutation and audit append share one SQLite lock/transaction; audit failure rolls back the state mutation.
- Missing/wrong master key or corrupt MCP ciphertext fails startup/config loading explicitly. No plaintext or empty-config fallback is allowed.
- A failed plaintext-to-ciphertext migration may retain one mode-0600 pre-migration recovery backup marked `legacy-sensitive`; active DB/WAL and all later backups must not retain the sentinel.
- New DB-created MCP servers default `enabled=false`. In `secure-remote`, every `streamable_http` server is denied with `trusted_egress_unavailable`. An enabled stdio server with empty approved `tools.include` registers no tools.
- Do not implement `remote_policy.py`, DNS pinning, redirect/proxy controls, `mcp_stdio_grants`, executable drift binding, official containers, SBOM, signing, or publication in this phase.
- Baseline technical source: `docs/codex-v1/design/21-g1.6-recon.md`; where it requests full S2.4/S2.5, this plan's D8 exclusions take precedence.

---

### Task 1: Freeze the S2 handoff and live migration tail

**Files:**
- Create: `tests/integration/test_s1_s2_handoff.py`
- Test: `tests/storage/test_migration_ledger.py`
- Read: `src/tianshu/storage/migrations.py`

**Interfaces:**
- Consumes: G1.5 report, `Storage`, `MIGRATIONS`, AuthContext/scopes, Doctor/readiness.
- Produces: one failing prerequisite test if the handoff is incomplete; frozen prefix digest used by later tests.

- [ ] **Step 1: Write the handoff test**

```python
def test_s1_s2_handoff_has_green_gate_and_live_tail() -> None:
    report = Path("docs/cc-fable-v1/reports/g1.5-report.md").read_text()
    assert "status: passed" in report
    assert MIGRATIONS[-1].name == "0006_seed_default_personas"
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
```

During execution, replace the exact tail-name assertion only if an already-approved migration landed after this plan; do not weaken contiguity or the prior callback fingerprint test.

- [ ] **Step 2: Run the handoff test**

Run:

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_s1_s2_handoff.py tests/storage/test_migration_ledger.py tests/storage/test_migration_callback_freeze.py -q
```

Expected: pass. A missing G1.5 report or changed frozen prefix blocks S2.

- [ ] **Step 3: Commit the handoff guard**

```bash
git add tests/integration/test_s1_s2_handoff.py
git commit -m "test: freeze the S1 to S2 security handoff"
```

### Task 2: Add immutable SystemAudit storage

**Files:**
- Create: `src/tianshu/models/system_audit.py`
- Create: `src/tianshu/storage/system_audit_repo.py`
- Create: `tests/storage/test_system_audit.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/facade.py`

**Interfaces:**
- Consumes: current SQLite connection and lock, canonical JSON/hash helpers.
- Produces:

```python
class SystemAuditEventV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    id: str
    sequence: int
    correlation_id: str
    actor_digest: str
    action: str
    outcome: Literal["allowed", "denied", "succeeded", "failed"]
    reason_code: str
    subject_kind: str
    subject_digest: str
    metadata: dict[str, str | int | bool | None]
    previous_hash: str
    event_hash: str
    created_at: datetime

class SystemAuditMixin:
    def append_system_audit(self, request: AppendSystemAuditRequest) -> SystemAuditEventV1: ...
    def list_system_audit(self, *, after: int = 0, limit: int = 100) -> list[SystemAuditEventV1]: ...
    def export_system_audit(self) -> SystemAuditExportV1: ...
    def verify_system_audit(self) -> SystemAuditVerificationV1: ...
```

- [ ] **Step 1: Write RED tests for chain, triggers, and metadata allowlists**

Tests must assert genesis `"0" * 64`, contiguous sequence, exact canonical hash, page predecessor validation, UPDATE/DELETE rejection, unknown metadata-key rejection, secret sentinel absence, and tamper detection.

Run:

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/storage/test_system_audit.py -q
```

Expected RED: model/repository/table do not exist.

- [ ] **Step 2: Append the live `N+1` migration**

Add `system_audit_events` plus `(correlation_id, sequence)` and `(action, sequence)` indexes and unconditional UPDATE/DELETE rejection triggers. Hash every persisted field except `event_hash`; use lowercase SHA-256 over canonical UTF-8 JSON.

- [ ] **Step 3: Implement the model and repository minimally**

Use one low-level helper for callers that already own the transaction:

```python
def _append_system_audit_unlocked(
    conn: sqlite3.Connection,
    request: AppendSystemAuditRequest,
) -> SystemAuditEventV1:
    """Append exactly one verified event without opening a transaction."""
```

Public append owns `self._lock, self._conn`; pagination verifies the predecessor anchor and each returned hash; export verifies the entire chain before returning data.

- [ ] **Step 4: Run storage and migration regressions**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/storage/test_system_audit.py tests/storage/test_migration_ledger.py tests/storage/test_migration_callback_freeze.py tests/storage/test_migration_preserves_data.py -q
.venv/bin/ruff check src/tianshu/models/system_audit.py src/tianshu/storage/system_audit_repo.py tests/storage/test_system_audit.py
```

Expected GREEN: all pass, prior migration fingerprints unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/models/system_audit.py src/tianshu/storage/system_audit_repo.py src/tianshu/storage/migrations.py src/tianshu/storage/facade.py tests/storage/test_system_audit.py tests/storage/test_migration_ledger.py
git commit -m "feat: add immutable system audit storage"
```

### Task 3: Make security mutations and admin reads use SystemAudit

**Files:**
- Create: `src/tianshu/gateway/system_audit_api.py`
- Create: `tests/gateway/test_system_audit_api.py`
- Create: `tests/security/test_system_audit_transactions.py`
- Modify: `src/tianshu/storage/auth_repo.py`
- Modify: `src/tianshu/storage/security_repo.py`
- Modify: `src/tianshu/gateway/auth.py`
- Modify: `src/tianshu/gateway/auth_api.py`
- Modify: `src/tianshu/security/estop.py`
- Modify: `src/tianshu/gateway/estop_api.py`
- Modify: `src/tianshu/app.py`

**Interfaces:**
- Consumes: `_append_system_audit_unlocked`, request AuthContext, `request.state.correlation_id`.
- Produces: `GET /api/audit/system` and `/api/audit/system/export`, both admin-only.

- [ ] **Step 1: Write RED API and rollback tests**

Cover PAT issue/rotate/revoke, session rotate/revoke/denial, estop engage/resume, injected audit failure rollback, admin success, `api`-only 403, anonymous 401, corrupt-chain stable error, and secret-sentinel absence.

- [ ] **Step 2: Add compound repository methods**

Use explicit methods rather than calling two public transactions:

```python
def save_auth_token_with_audit(
    self,
    record: dict[str, object],
    audit: AppendSystemAuditRequest,
) -> None:
    with self._lock, self._conn:
        self._conn.execute(_INSERT_AUTH_TOKEN, _auth_token_values(record))
        _append_system_audit_unlocked(self._conn, audit)
```

Add equivalent atomic methods for rotate/revoke/session-family and estop transitions. Update in-memory estop state only after the DB transaction commits.

- [ ] **Step 3: Add the scoped read/export router**

The router obtains the authenticated context from request state; it never accepts actor identity from query/body. Bound `limit` to 1–500 and return no partial export when verification fails.

- [ ] **Step 4: Run focused regressions**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/gateway/test_system_audit_api.py tests/security/test_system_audit_transactions.py tests/gateway/test_auth.py tests/gateway/test_estop_api.py tests/security/test_estop.py -q
```

Expected GREEN: state and audit are atomic; scope matrix and redaction pass.

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/gateway/system_audit_api.py src/tianshu/storage/auth_repo.py src/tianshu/storage/security_repo.py src/tianshu/gateway/auth.py src/tianshu/gateway/auth_api.py src/tianshu/security/estop.py src/tianshu/gateway/estop_api.py src/tianshu/app.py tests/gateway/test_system_audit_api.py tests/security/test_system_audit_transactions.py
git commit -m "feat: bind security mutations to system audit"
```

### Task 4: Migrate MCP env/header mappings to ciphertext

**Files:**
- Create: `tests/secrets/test_mcp_secret_migration.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/secrets/vault.py`
- Modify: `src/tianshu/storage/config_repo.py`
- Modify: `src/tianshu/storage/_base.py`
- Modify: `src/tianshu/storage/sqlite_backup.py`

**Interfaces:**
- Consumes: `SecretVault`, `mcp_server_overrides` nullable override semantics.
- Produces:

```python
def encrypt_canonical_mapping(vault: SecretVault, value: Mapping[str, str]) -> bytes: ...
def decrypt_canonical_mapping(vault: SecretVault, ciphertext: bytes) -> dict[str, str]: ...
```

and MCP columns `env_ciphertext`, `env_keys_json`, `headers_ciphertext`, `header_keys_json`.

- [ ] **Step 1: Write RED upgrade tests**

Cover NULL, `{}`, env-only, headers-only, DB-only server, YAML override, missing/wrong/malformed key, corrupt ciphertext, malformed legacy JSON, source DB unchanged on failure, 0600 legacy-sensitive backup, active DB/WAL sentinel absence, and prior-prefix upgrade.

- [ ] **Step 2: Implement canonical mapping codec**

```python
def encrypt_canonical_mapping(vault: SecretVault, value: Mapping[str, str]) -> bytes:
    payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return vault.encrypt(payload)

def decrypt_canonical_mapping(vault: SecretVault, ciphertext: bytes) -> dict[str, str]:
    value = json.loads(vault.decrypt(ciphertext))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("MCP secret mapping is invalid")
    return value
```

- [ ] **Step 3: Append live `N+1` migration after the audit migration**

Before any schema mutation, parse, validate, encrypt, decrypt, and compare every legacy mapping in memory. Then rebuild the table in the migration transaction, preserve all non-secret columns and NULL-vs-empty semantics, set `PRAGMA secure_delete=ON`, and remove plaintext columns.

- [ ] **Step 4: Make ConfigMixin fail closed**

`list_mcp_overrides()` and `upsert_mcp_override()` use the low-level vault. Missing/wrong key with non-null ciphertext raises a stable redacted exception; `MCPManager._load_overrides_from_storage()` must not catch it and continue with an empty list.

- [ ] **Step 5: Run migration and config regressions**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/secrets/test_mcp_secret_migration.py tests/tools/mcp/test_config.py tests/storage/test_backup_restore.py tests/storage/test_migration_ledger.py tests/storage/test_migration_callback_freeze.py -q
```

Expected GREEN: active data round-trips with no plaintext fallback; legacy recovery exception is explicit.

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/storage/migrations.py src/tianshu/secrets/vault.py src/tianshu/storage/config_repo.py src/tianshu/storage/_base.py src/tianshu/storage/sqlite_backup.py tests/secrets/test_mcp_secret_migration.py tests/tools/mcp/test_config.py tests/storage/test_backup_restore.py
git commit -m "feat: encrypt persisted MCP secret mappings"
```

### Task 5: Rotate network/channel/MCP ciphertext atomically

**Files:**
- Modify: `src/tianshu/cli/commands/secrets.py`
- Modify: `tests/cli/test_secrets_rotate.py`
- Modify: `tests/storage/test_backup_restore.py`

**Interfaces:**
- Consumes: old/new Fernet keys, all non-null ciphertext families.
- Produces: one dry-run plan, one online backup, one transaction, all-family rotation.

- [ ] **Step 1: Write RED all-family and rollback tests**

Include one network credential, one channel secret, MCP env and MCP headers. Wrong old key or one corrupt row must leave every family unchanged and create no misleading success result.

- [ ] **Step 2: Replace per-row public updates with one transaction**

Create a private rotation plan containing decrypted plaintext only in memory. After full dry-run succeeds and backup completes, re-encrypt every family under `storage._lock, storage._conn` and append one redacted `secrets.master_key.rotated` audit event. Zero-family rotation remains a valid no-op with no backup.

- [ ] **Step 3: Run tests and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/cli/test_secrets_rotate.py tests/storage/test_backup_restore.py tests/secrets/test_mcp_secret_migration.py -q
git add src/tianshu/cli/commands/secrets.py tests/cli/test_secrets_rotate.py tests/storage/test_backup_restore.py
git commit -m "feat: rotate every encrypted secret family atomically"
```

### Task 6: Enforce the Lean MCP disabled boundary and admin writes

**Files:**
- Create: `tests/security/test_mcp_lean_admission.py`
- Create: `tests/gateway/test_mcp_admin.py`
- Modify: `src/tianshu/tools/mcp/config.py`
- Modify: `src/tianshu/tools/mcp/manager.py`
- Modify: `src/tianshu/gateway/mcp_api.py`
- Modify: `src/tianshu/gateway/auth.py`

**Interfaces:**
- Consumes: MCPServerConfig, SystemAudit append, AuthContext/admin scope.
- Produces: `AdmissionDecision(allowed: bool, reason_code: str)` for pre-session checks.

- [ ] **Step 1: Write RED default and scope tests**

Assert new API-created stdio/remote servers default disabled; `secure-remote` rejects remote even when enabled; enabled stdio with empty `tools.include` exposes zero tools and never calls `ExecutionGateway`; PATCH/POST/DELETE/reload require admin; safe GET remains `api`; denial audit contains hashes/codes only.

- [ ] **Step 2: Add one explicit Lean admission method**

```python
@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    reason_code: str

def _admission_for(self, config: MCPServerConfig) -> AdmissionDecision:
    if not config.enabled:
        return AdmissionDecision(False, "disabled")
    if config.transport == "streamable_http" and self._security_mode == "secure-remote":
        return AdmissionDecision(False, "trusted_egress_unavailable")
    if config.transport == "stdio" and not config.tools.include:
        return AdmissionDecision(False, "approved_tools_required")
    if not self._admitted(config.name):
        return AdmissionDecision(False, "server_not_allowlisted")
    return AdmissionDecision(True, "admitted")
```

Use this method for readiness baseline, session creation, ExecutionGateway configuration, and tool registration. Do not implement network pinning or persistent grants here.

- [ ] **Step 3: Set API creation defaults and audit writes**

Change `_MCPServerCreate.enabled` to `False`. For DB-only creation, reject attempts to set `enabled=true` for `streamable_http` in `secure-remote`; stdio may be enabled only with non-empty `tools_include`. Persist configuration mutation and its safe audit event atomically.

- [ ] **Step 4: Run focused tests and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/security/test_mcp_lean_admission.py tests/gateway/test_mcp_admin.py tests/gateway/test_mcp_auth.py tests/tools/mcp tests/security/test_mcp_command_boundary.py tests/diagnostics/test_doctor_report.py -q
git add src/tianshu/tools/mcp/config.py src/tianshu/tools/mcp/manager.py src/tianshu/gateway/mcp_api.py src/tianshu/gateway/auth.py tests/security/test_mcp_lean_admission.py tests/gateway/test_mcp_admin.py
git commit -m "feat: keep unapproved MCP paths fail closed"
```

### Task 7: Close the S2 Lean Gate and truth documentation

**Files:**
- Create: `docs/security/lean-preview-threat-model.md`
- Create: `docs/cc-fable-v1/reports/s2-lean-security-report.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/ops/credentials.md`
- Modify: `docs/ops/mcp_servers.yaml.example`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: S2 audit/cipher/default behavior and focused test evidence.
- Produces: truthful public capability language and S3 entry report.

- [ ] **Step 1: Add documentation truth tests**

Extend `tests/test_public_docs_truth.py` to require: source/Wheel official install paths; remote MCP `disabled`/`deferred`; stdio exact grant `deferred`; SystemAudit and MCP ciphertext `implemented`; container/PyPI/GHCR/signing `deferred`; single-node SQLite limit; legacy-sensitive recovery-backup warning.

- [ ] **Step 2: Run the S2 focused security Gate**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/storage/test_system_audit.py tests/gateway/test_system_audit_api.py tests/security/test_system_audit_transactions.py tests/secrets/test_mcp_secret_migration.py tests/security/test_mcp_lean_admission.py tests/gateway/test_mcp_admin.py tests/cli/test_secrets_rotate.py tests/test_public_docs_truth.py -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/lint-imports
```

Expected: zero failures; no required security test skipped/deselected.

- [ ] **Step 3: Run the single full non-slow Gate**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
```

Expected: zero failures. Record exact counts and warnings.

- [ ] **Step 4: Write report and append progress**

Report must list migration versions/names, chain terminal hash, test commands/counts, secret sentinel proof, disabled-path matrix, independent security/spec review, known limits, and P2-A deferred links. Do not label full G1.6 or full MCP security passed.

- [ ] **Step 5: Commit docs and Gate evidence**

```bash
git add docs/security/lean-preview-threat-model.md docs/launch/capability-matrix.md docs/ops/credentials.md docs/ops/mcp_servers.yaml.example docs/cc-fable-v1/reports/s2-lean-security-report.md docs/cc-fable-v1/PROGRESS.md tests/test_public_docs_truth.py
git diff --cached --check
git commit -m "docs: close the S2 Lean security Gate"
```

S3 may begin only when the report has zero unresolved Critical/Important findings and names the actual live migration tail.
