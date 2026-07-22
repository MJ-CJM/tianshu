# Tianshu Lean Preview S3 Core Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the SQLite single-node durable-governance core: atomic Edict ingress, persistent decisions and continuations, leased/fenced attempts, supported side-effect receipts, immutable artifacts/Evidence Bundles, internal durable notifications, and a restart/fault matrix.

**Architecture:** Gateway/Bot/MCP/Tool adapters call one application service. That service coordinates repositories through one existing SQLite connection and durable outbox. Governance decisions and run continuations are persisted domain state rather than Python await stacks. Supported effects use intent→receipt→ack; Evidence closes over stable IDs/hashes. S2 SystemAudit is reused, not duplicated.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite/WAL, asyncio, JSON Schema 2020-12, pytest/pytest-asyncio, import-linter, Ruff, mypy.

## Global Constraints

- Require `docs/cc-fable-v1/reports/s2-lean-security-report.md` with zero unresolved Critical/Important findings.
- Normative detailed source is [`docs/codex-v1/plans/12-g2-durable-governance-evidence.md`](../../codex-v1/plans/12-g2-durable-governance-evidence.md), but its fixed v3–v7 migration numbers and full planner/OTel/external-notification scope are superseded here.
- Allocate every migration from the live tail after S2. Never rename or edit prior migrations.
- Generic `DecisionRequestV1`/`DecisionResolutionV1` is the sole governance authority. G1 governed-apply authorization is an immutable one-way projection of an already resolved decision, never a second approval authority.
- Preserve the existing single SQLite connection, including `:memory:` tests. `SqliteUnitOfWork` must not open a second connection or call public repository methods that nest transactions.
- Delivery is at-least-once. Claims of one effective result require consumer idempotency/CAS. Do not say transport is exactly-once.
- Side-effect guarantees apply only to explicitly managed/tested boundaries. Opaque contained CLI effects remain `uncertain` and stop for a decision.
- Canonical JSON is UTF-8, sorted keys, `ensure_ascii=False`, separators `(',', ':')`, `allow_nan=False`, explicit nulls; SHA-256 is lowercase hex over those bytes.
- S3.9 is narrowed to plan hash, revision reason, parent reference, and artifact link. Do not build the full planner quality/evaluation system.
- S3.12 keeps internal outbox/audit/correlation/readiness. Full OTel exporters/SLO and durable external notification channels are deferred to P2-B2.
- Do not add PostgreSQL, Kubernetes, multi-replica, vector retrieval, OpenHands, or Web page code in this phase.

---

### Task 1: Rebase the durable plan onto the live S2 handoff

**Files:**
- Create: `tests/integration/test_s2_s3_handoff.py`
- Create: `src/tianshu/models/canonical.py` if the exact canonical contract is absent
- Create: `tests/models/test_canonical.py` if the exact canonical contract is absent
- Test: `tests/storage/test_migration_ledger.py`

**Interfaces:**
- Consumes: S2 SystemAudit, AuthContext, Governance Contract, ExecutionGateway, WorkspaceService, `/health/live` and `/health/ready`.
- Produces:

```python
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

def canonical_json_bytes(value: BaseModel | Mapping[str, object]) -> bytes: ...
def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str: ...
```

- [ ] **Step 1: Write the handoff RED/guard test**

Assert S2 report is green, live migration versions are contiguous, `SystemAuditEventV1` imports, remote MCP remains denied in secure-remote, Governance Contract models are frozen/canonical, and G1 governed apply exposes only a projection binding.

- [ ] **Step 2: Run the handoff test**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_s2_s3_handoff.py tests/governance/test_legacy_edict_mapping.py tests/storage/test_migration_ledger.py tests/storage/test_migration_callback_freeze.py -q
```

Expected: pass. A missing consumed contract blocks S3; do not create a duplicate.

- [ ] **Step 3: Add or normalize canonical helpers with RED/GREEN tests**

Reject NaN/Infinity, non-string mapping keys, and `default=str` coercion; include explicit nulls. Run `tests/models/test_canonical.py` and commit.

```bash
git add src/tianshu/models/canonical.py tests/models/test_canonical.py tests/integration/test_s2_s3_handoff.py
git commit -m "test: freeze the S2 to durable governance handoff"
```

### Task 2: Build atomic Edict ingress and durable outbox

**Files:**
- Create: `src/tianshu/application/__init__.py`
- Create: `src/tianshu/application/edicts.py`
- Create: `src/tianshu/application/event_history.py`
- Create: `src/tianshu/storage/unit_of_work.py`
- Create: `src/tianshu/storage/outbox_repo.py`
- Create: `tests/integration/test_edict_idempotency.py`
- Create: `tests/integration/test_outbox_recovery.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/edict_ops.py`
- Modify: `src/tianshu/bus/event_bus.py`
- Modify: all ingress files named in the baseline plan Increment 2

**Interfaces:**
- Produces the exact contracts:

```python
@dataclass(frozen=True, slots=True)
class SubmitEdictCommand:
    edict: Edict
    idempotency_key: str
    requested_contract: RequestedGovernanceContractV1
    extra_payload: Mapping[str, JsonValue]

@dataclass(frozen=True, slots=True)
class SubmitEdictResult:
    edict: Edict
    memorial: Memorial
    event_id: str
    request_hash: str
    deduplicated: bool

class EdictApplicationService:
    def submit(self, command: SubmitEdictCommand, *, auth: AuthContext, producer: str, correlation_id: str) -> SubmitEdictResult: ...

class OutboxDispatcher:
    async def drain_once(self, *, limit: int = 50) -> int: ...
    async def run(self) -> None: ...
    async def stop(self) -> None: ...
```

- [ ] **Step 1: Execute baseline Increment 1 RED tests with dynamic migration allocation**

Write same-key/same-hash dedupe, same-key/different-hash 409, crash-after-commit, outbox lease, consumer duplicate, and nested-transaction rejection tests. Allocate the outbox/idempotency migration at live `N+1`.

- [ ] **Step 2: Implement UnitOfWork and submission service minimally**

`SqliteUnitOfWork` uses the existing `Storage._lock` and `_conn`, `BEGIN IMMEDIATE`, explicit commit/rollback, and connection-level primitives. Request hash excludes generated IDs/timestamps and includes contract hash.

- [ ] **Step 3: Execute baseline Increment 2 ingress convergence**

Route `gateway/edicts_api.py`, `gateway/mcp_server.py`, `gateway/core/edict_bridge.py`, `tools/submit_edict.py`, `tools/schedule_edict.py`, and governed amend entry through `EdictApplicationService.submit()`. Architecture tests reject direct Edict+Memorial writes outside application/storage modules.

- [ ] **Step 4: Run and commit the slice**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_edict_idempotency.py tests/integration/test_outbox_recovery.py tests/gateway/test_edicts_api.py tests/tools/test_submit_edict.py tests/tools/test_schedule_edict.py -q
.venv/bin/lint-imports
git add src/tianshu/application src/tianshu/storage/unit_of_work.py src/tianshu/storage/outbox_repo.py src/tianshu/storage/migrations.py src/tianshu/edict_ops.py src/tianshu/bus/event_bus.py src/tianshu/gateway/edicts_api.py src/tianshu/gateway/mcp_server.py src/tianshu/gateway/core/edict_bridge.py src/tianshu/tools/submit_edict.py src/tianshu/tools/schedule_edict.py tests/integration
git commit -m "feat: add atomic Edict ingress and durable outbox"
```

### Task 3: Persist decisions and versioned RunState

**Files:**
- Create: `src/tianshu/models/decision.py`
- Create: `src/tianshu/models/run_state.py`
- Create: `src/tianshu/governance/decision_service.py`
- Create: `src/tianshu/storage/decision_repo.py`
- Create: `src/tianshu/storage/run_state_repo.py`
- Create: `tests/integration/test_decision_restart_recovery.py`
- Create: `tests/integration/test_outer_loop_restart_recovery.py`
- Create: `tests/governance/test_decision_cas.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/executor/approvals.py`
- Modify: `src/tianshu/executor/orchestrator/human_decision.py`

**Interfaces:**

```python
class DecisionKind(StrEnum):
    TOOL = "tool"
    OUTER_LOOP = "outer_loop"
    PLAN_REVIEW = "plan_review"
    GOVERNED_APPLY = "governed_apply"

class DecisionService:
    def request(self, command: RequestDecisionCommand, *, auth: AuthContext) -> DecisionRequestV1: ...
    def get(self, decision_request_id: str) -> DecisionRecordV1 | None: ...
    def list_pending(self, *, kind: DecisionKind | None = None) -> list[DecisionRequestV1]: ...
    def resolve(self, decision_request_id: str, command: ResolveDecisionCommand, *, auth: AuthContext) -> DecisionResolutionV1: ...
    def expire_due(self, *, now: datetime, limit: int = 100) -> int: ...

class RunStateRepository:
    def load(self, memorial_id: str) -> RunStateV1 | None: ...
    def compare_and_swap(self, state: RunStateV1, *, expected_version: int) -> RunStateV1: ...
```

- [ ] **Step 1: Execute baseline Increment 3 RED tests**

Cover same request key/same payload dedupe, different payload conflict, concurrent resolve CAS, expiry/late resolution, restart recovery, strict action payloads, and RunState version conflict. Append the live decision/run-state migration.

- [ ] **Step 2: Implement DecisionService and RunState**

Actor identity comes only from AuthContext. Resolution and `decision.resolved` outbox append share one UoW. RunState persists messages, proposal, iteration, continuation cursor, decision ID, effect cursor, best output, feedback and plan reference needed to reconstruct work.

- [ ] **Step 3: Execute baseline Increment 4 adapter convergence**

Replace in-memory `_pending: dict[str, asyncio.Event]` authority in tool/outer-loop/plan/apply paths. Process-local waiters may wake a current process but cannot own the truth. All HTTP/Bot adapters resolve through one DecisionService.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_decision_restart_recovery.py tests/integration/test_outer_loop_restart_recovery.py tests/governance/test_decision_cas.py tests/executor -q
git add src/tianshu/models/decision.py src/tianshu/models/run_state.py src/tianshu/governance src/tianshu/storage/decision_repo.py src/tianshu/storage/run_state_repo.py src/tianshu/storage/migrations.py src/tianshu/executor/approvals.py src/tianshu/executor/orchestrator/human_decision.py tests/integration tests/governance
git commit -m "feat: persist governance decisions and run state"
```

### Task 4: Add leased attempts, fencing, side-effect receipts, and continuation resume

**Files:**
- Create: `src/tianshu/storage/attempt_ledger.py`
- Create: `src/tianshu/storage/side_effect_journal.py`
- Create: `src/tianshu/application/run_dispatcher.py`
- Create: `src/tianshu/application/run_reconciler.py`
- Create: `tests/integration/test_claim_lease_recovery.py`
- Create: `tests/integration/test_side_effect_idempotency.py`
- Create: `tests/integration/test_continuation_recovery.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/executor/worker.py`
- Modify: `src/tianshu/scheduler/scheduler.py`
- Modify: `src/tianshu/executor/orchestrator/loop.py`
- Modify: `src/tianshu/executor/execution_gateway/`

**Interfaces:**

```python
class AttemptLeaseRepository:
    def claim(self, *, memorial_id: str, owner_id: str, now: datetime, lease_seconds: int) -> AttemptLeaseV1 | None: ...
    def heartbeat(self, *, attempt_id: str, owner_id: str, fencing_token: int, now: datetime) -> bool: ...
    def complete(self, *, attempt_id: str, owner_id: str, fencing_token: int, outcome: AttemptOutcomeV1) -> bool: ...

class SideEffectJournal:
    def begin_intent(self, intent: SideEffectIntentV1) -> SideEffectIntentV1: ...
    def record_receipt(self, receipt: SideEffectReceiptV1, *, expected_version: int) -> SideEffectReceiptV1: ...
    def mark_uncertain(self, intent_id: str, *, reason_code: str) -> None: ...
```

- [ ] **Step 1: Execute baseline Increments 5–7 RED matrices**

Cover claim race, expired lease, stale fencing token, max-attempt DLQ, crash before effect, crash after effect/before receipt, receipt lookup, unsupported opaque effect→uncertain decision, pause/restart/resolve/resume at L0–L3, and cancelled continuation.

- [ ] **Step 2: Append attempt/journal/continuation migrations from the live tail**

Use separate migrations only where a reviewer can independently reject the slice; keep each callback under the frozen migration discipline.

- [ ] **Step 3: Implement dispatcher/reconciler and managed effect integration**

Persist assignment/attempt before dispatch. Every completion write checks owner+fencing token. Managed side effects require provider idempotency key or receipt lookup; otherwise stop with a persistent decision instead of retrying blindly.

- [ ] **Step 4: Implement continuation reconstruction**

Resume from persisted RunState and Decision resolution; never serialize or pretend to restore a coroutine stack. Repeated resume events are idempotent.

- [ ] **Step 5: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_claim_lease_recovery.py tests/integration/test_side_effect_idempotency.py tests/integration/test_continuation_recovery.py tests/executor tests/scheduler -q
git add src/tianshu/application/run_dispatcher.py src/tianshu/application/run_reconciler.py src/tianshu/storage/attempt_ledger.py src/tianshu/storage/side_effect_journal.py src/tianshu/storage/migrations.py src/tianshu/executor src/tianshu/scheduler tests/integration tests/executor tests/scheduler
git commit -m "feat: add fenced execution and durable continuation"
```

### Task 5: Add the Lean planner revision evidence

**Files:**
- Create: `src/tianshu/models/plan_revision.py`
- Create: `tests/integration/test_replan_evidence.py`
- Modify: `src/tianshu/planner/planner.py`
- Modify: `src/tianshu/executor/dag_scheduler.py`
- Modify: RunState persistence/API files from Task 3

**Interfaces:**

```python
class PlanRevisionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision_id: str
    parent_revision_id: str | None
    plan_hash: str
    reason_code: str
    reason_summary: str
    artifact_digest: str
    created_at: datetime
```

- [ ] **Step 1: Write RED lineage and restart tests**

Assert canonical plan hash, immutable parent chain, non-blank redacted reason, ArtifactStore-ready digest, RunState reference, and restart retention.

- [ ] **Step 2: Implement only the Lean contract**

Persist plan/replan lineage and evidence references. Do not add estimator scoring, LLM self-grading, benchmark frameworks, or complete quality metrics from the baseline Increment 8.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_replan_evidence.py tests/test_planner.py tests/test_dag.py tests/test_scheduler.py -q
git add src/tianshu/models/plan_revision.py src/tianshu/planner/planner.py src/tianshu/executor/dag_scheduler.py tests/integration/test_replan_evidence.py
git commit -m "feat: persist plan revision evidence"
```

### Task 6: Add content-addressed artifacts and immutable Evidence Bundle v1

**Files:**
- Create: `src/tianshu/evidence/__init__.py`
- Create: `src/tianshu/evidence/models.py`
- Create: `src/tianshu/evidence/service.py`
- Create: `src/tianshu/storage/artifact_repo.py`
- Create: `src/tianshu/gateway/evidence_api.py`
- Create: `docs/reference/evidence-bundle-v1.schema.json`
- Create: `tests/evidence/test_bundle.py`
- Create: `tests/evidence/test_canonical_hash.py`
- Create: `tests/evidence/test_close_snapshot_immutable.py`
- Create: `tests/integration/test_independent_audit_evidence.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/app.py`

**Interfaces:**

```python
class ArtifactStore:
    def put_bytes(self, data: bytes, *, media_type: str, redaction: str) -> ArtifactRefV1: ...
    def get_bytes(self, digest: str) -> bytes: ...
    def verify(self, digest: str) -> bool: ...

class EvidenceService:
    def build_open(self, memorial_id: str) -> EvidenceBundleV1: ...
    def close(self, memorial_id: str, *, expected_version: int) -> ClosedEvidenceBundleV1: ...
    def verify(self, bundle_id: str) -> EvidenceVerificationV1: ...
    def export(self, bundle_id: str) -> bytes: ...
```

- [ ] **Step 1: Execute baseline Increment 9 RED tests**

Cover content dedupe, digest mismatch, path traversal, schema round-trip, canonical hash, closed immutability, missing required decision/effect/check, export/import verification, and redacted reproduction command.

- [ ] **Step 2: Append artifact/evidence migration and implement services**

Large payloads live in ArtifactStore; DB stores metadata/digests. Closed bundles are canonical immutable snapshots referencing requested/effective contract, plan revision, artifacts, checks, decisions, costs, environment, auditor, effect receipts, and reproduction command.

- [ ] **Step 3: Add scoped APIs and run tests**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evidence tests/integration/test_independent_audit_evidence.py tests/gateway/test_evidence_api.py -q
git add src/tianshu/evidence src/tianshu/storage/artifact_repo.py src/tianshu/gateway/evidence_api.py src/tianshu/storage/migrations.py src/tianshu/app.py docs/reference/evidence-bundle-v1.schema.json tests/evidence tests/integration/test_independent_audit_evidence.py tests/gateway/test_evidence_api.py
git commit -m "feat: add immutable Evidence Bundle v1"
```

### Task 7: Close internal audit/readiness/notification durability

**Files:**
- Create: `src/tianshu/notifier/delivery_outbox.py`
- Create: `tests/notifier/test_internal_delivery_recovery.py`
- Modify: `src/tianshu/diagnostics.py`
- Modify: `src/tianshu/app.py`
- Modify: `src/tianshu/notifier/notifier.py`
- Modify: S2 SystemAudit actions/allowlists

**Interfaces:**
- Consumes: durable outbox, correlation IDs, SystemAudit, Doctor/readiness.
- Produces: internal notification delivery record with attempt/backoff/DLQ; no external channel guarantee.

- [ ] **Step 1: Write RED readiness and internal-delivery tests**

Assert database/migration/outbox/dispatcher/decision/attempt/artifact dependencies affect readiness; liveness stays process-only. Internal notification retries survive restart, deadline expiry goes DLQ, and failures never delete pending data.

- [ ] **Step 2: Implement the narrowed baseline Increments 10–11**

Reuse S2 audit chain. Propagate correlation IDs into outbox/decision/run/effect/evidence. Add durable internal delivery only; keep Feishu/Telegram/email external delivery semantics truthfully outside this Gate. Do not add a complete OTel exporter/dashboard.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/gateway/test_health.py tests/diagnostics tests/notifier/test_internal_delivery_recovery.py tests/storage/test_system_audit.py -q
git add src/tianshu/notifier/delivery_outbox.py src/tianshu/notifier/notifier.py src/tianshu/diagnostics.py src/tianshu/app.py src/tianshu/models/system_audit.py tests/notifier/test_internal_delivery_recovery.py tests/gateway/test_health.py tests/diagnostics
git commit -m "feat: expose durable core readiness and internal delivery"
```

### Task 8: Run the S3 Core fault matrix and Gate

**Files:**
- Create: `scripts/check_s3_core_evidence.py`
- Create: `docs/cc-fable-v1/reports/s3-core-governance-report.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: all Task 1–7 reports/artifacts.
- Produces: machine-checked Core Gate; S4 API handoff.

- [ ] **Step 1: Add checker contract tests**

Create `tests/evidence/test_s3_core_gate.py`. Reject missing command/count/hash, wrong commit, dirty unknown file, skipped required fault, broken bundle, duplicate effective managed effect, stale fencing success, missing decision recovery, full-OTel claim, external-notification claim, or multi-replica claim.

- [ ] **Step 2: Run the focused fault matrix**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_edict_idempotency.py tests/integration/test_outbox_recovery.py tests/integration/test_decision_restart_recovery.py tests/integration/test_outer_loop_restart_recovery.py tests/integration/test_claim_lease_recovery.py tests/integration/test_side_effect_idempotency.py tests/integration/test_continuation_recovery.py tests/integration/test_replan_evidence.py tests/evidence tests/notifier/test_internal_delivery_recovery.py -q
```

Expected: no failures/skipped required cases.

- [ ] **Step 3: Run static and full non-slow Gates**

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/lint-imports
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
```

- [ ] **Step 4: Generate, verify, and commit the report**

```bash
env -u VIRTUAL_ENV .venv/bin/python scripts/check_s3_core_evidence.py --report docs/cc-fable-v1/reports/s3-core-governance-report.md
git add scripts/check_s3_core_evidence.py tests/evidence/test_s3_core_gate.py docs/cc-fable-v1/reports/s3-core-governance-report.md docs/launch/capability-matrix.md docs/cc-fable-v1/PROGRESS.md
git commit -m "docs: close the S3 durable governance Core Gate"
```

The report may claim SQLite single-node durable governance/Evidence only. S4 starts only if the checker exits 0.
