# Phase 2 Durable Governance & Evidence Implementation Plan

> **Codex v1 amendment:** Do not execute this snapshot alone. Read
> [the G2 recon](../design/22-g2-recon.md) first. In particular, generic
> `DecisionRequest`/`Resolution` is the sole governance authority for governed
> apply; the G1 token-bound apply authorization is only its immutable one-way
> projection. Migration versions in this snapshot are stale and must be allocated
> from the final G1 ledger.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver G2 as a single-node, SQLite-backed durable governance boundary: every Edict enters through one idempotent transaction, governance decisions and resumable state survive restart, supported managed side effects have a durable intent/receipt ledger, and every closed run can export an immutable Evidence Bundle with durable audit and notification records.

**Architecture:** Keep SQLite as the system of record and split the work into three automatic slices. G2-A establishes the transactional application boundary and at-least-once outbox. G2-B adds persistent decisions, versioned continuations, leased attempts, side-effect receipts, and planner evidence. G2-C closes the evidence, system-audit, observability, readiness, and notification loops. Gateway/CLI/Bot/MCP adapters call application services; services coordinate domain models and storage repositories; every event that carries a durability claim is persisted before EventBus dispatch, while explicitly local UI events remain best-effort. No Python coroutine stack is treated as durable state.

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Pydantic v2, SQLite/WAL, asyncio, OpenTelemetry, JSON Schema 2020-12, pytest/pytest-asyncio, import-linter, Ruff, mypy, uv, React/TypeScript regression gates.

## Global Constraints

- The user waived intermediate approval pauses on 2026-07-11. Execute continuously through G2-A, G2-B, and G2-C. Each slice still has an automatic Gate: if a command fails, stay inside that slice, diagnose, add or correct the failing test, rerun the Gate, and only then advance. Ask for user approval only after the complete G0-G5 programme is ready for final review, unless a genuinely external credential or authority boundary blocks progress.
- Use strict RED-GREEN-REFACTOR for every behavior. A failing test must demonstrate the missing behavior before production code is added. Never weaken an assertion to obtain GREEN.
- Run all Python commands through `uv run --frozen`; dependency changes require an intentional lockfile update followed by `uv sync --frozen --extra all --extra dev`.
- Do not edit or renumber G0 migration `0001_adopt_v042_baseline`. G1 owns version `2` and must consolidate all of its additive schema into `0002_g1_public_safe_foundation` before G2 starts. G2 exclusively owns versions `3` through `7` as specified below.
- A pre-G2 schema check must reject duplicate migration versions, changed applied checksums, missing G1 tables, and any G2 table already created outside the ledger. If an in-flight G1 branch has used versions `3+`, reconcile it back into version `2` before writing G2 code; do not silently choose new numbers.
- Migration callbacks use the existing `MigrationConnection`; they must not call `BEGIN`, `COMMIT`, `ROLLBACK`, or `executescript`. The migration ledger owns the transaction and checksum verification.
- Preserve the current single `sqlite3.Connection`, including `:memory:` tests. A Unit of Work must not open a second connection. Extract connection-level insert/update primitives and let public repository methods retain their existing transaction ownership.
- Event delivery is **at-least-once**. Consumer idempotency and compare-and-swap can prove one effective business result for tested consumers; they do not make transport delivery exactly-once.
- A no-duplicate-effective-side-effect claim is limited to explicitly listed managed boundaries that expose provider idempotency or receipt lookup and pass the fault matrix. Untracked external effects become `uncertain` and stop for a decision. Opaque contained CLIs expose only coarse process start/result evidence and are excluded from internal side-effect guarantees.
- Native may be described as managed only if the G1 ExecutionGateway and WorkspaceService compatibility Gates passed. Claude Code/Codex headless through Keqing remain `contained + experimental`; G2 must not upgrade their maturity label.
- Canonical JSON is UTF-8 JSON with sorted keys, `ensure_ascii=False`, separators `(',', ':')`, `allow_nan=False`, and explicit nulls. Hashes are lowercase SHA-256 hex over those bytes. Timestamps are UTC RFC 3339 strings.
- Never store raw secrets, authorization headers, full environment values, or unredacted notification/tool payloads in outbox, journal, audit, evidence, logs, or spans.
- Closed Evidence Bundles and system audit rows are application-append-only. SQLite triggers catch ordinary application mistakes, but the threat model explicitly does not defend against a host administrator or database administrator who can replace files, disable triggers, or rewrite the process.
- Every increment ends with a focused Gate and one intentional commit. Do not combine unrelated cleanup. Keep pre-existing user changes intact.

---

## Slice Map and Automatic Gates

| Slice | Increments | Durable result | Automatic Gate before continuing |
|---|---:|---|---|
| G2-A · Transactional ingress | 1–2 | Atomic Edict + initial Memorial + idempotency + outbox; all ingress adapters use one application service; outbox redelivery is safe | Fresh and upgraded schema through v3, submission conflict tests, crash-after-commit recovery, duplicate consumer delivery, direct-write architecture test, lint/type/import boundaries |
| G2-B · Durable governance runtime | 3–8 | Persistent decisions and RunState, restartable continuations, leased attempts, supported side-effect receipts, planner/replan evidence | Schema through v6 (v6 lands atomically before its G2-C services), decision CAS/expiry/restart, lease fencing/DLQ, side-effect fault matrix, L0–L3/pause continuation recovery, planner evidence, executor/scheduler/orchestrator regressions |
| G2-C · Verifiable closure | 9–12 | ArtifactStore, immutable Evidence Bundle v1, append-only system audit, correlated traces/readiness, durable notification delivery, G2 Gate report | Schema through v7, bundle schema/hash/immutability/replay, audit/readiness/trace flush, notification modes/deadline recovery, full backend/Web regressions and clean restart/export demonstration |

No Gate is an approval meeting. Passing evidence is recorded automatically; failures return to the current RED-GREEN-REFACTOR loop.

---

## Fixed Domain Contracts

These names and semantics are part of G2. If G1 landed an equivalent helper under a different file, move or adapt it once and retain these public G2 interfaces; do not carry two competing contracts.

### G1 handoff preflight

Before the first G2 RED test, assert these consumed contracts instead of guessing around them:

- `AuthContext` exposes immutable `principal`, `source_ip`, `correlation_id`, roles/scopes, and authentication source. HTTP middleware stores it at `request.state.auth_context`; Bot/MCP adapters construct it through the same G1 identity service, not by accepting an actor string.
- `RequestedGovernanceContractV1` and `EffectiveGovernanceContractV1` are strict, frozen, canonical-hashable models. An Edict stores the requested contract; each Memorial/run stores its effective contract and executor manifest hash.
- `ExecutionGateway` and `WorkspaceService` have passed their G1 negative Gates before Native is considered managed. Their request objects accept AuthContext/correlation/contract/workspace context rather than ambient globals.
- G1 migration v2 contains its complete schema, including a base `system_audit_events` table with `id`, `event_type`, `actor_principal_id`, `actor_display_name`, `reason`, `payload_json_redacted`, and `created_at`. G2 v6 adds context/hash columns but does not rename these base columns.
- `/health/live` exists and is public. G2 adds readiness without turning liveness into a dependency probe.

Add `tests/integration/test_g1_g2_handoff.py` in Increment 1. If any assertion fails, correct the G1 implementation or one-way compatibility adapter and rerun automatically before adding v3; do not fork a second identity, contract, audit, execution, or workspace concept.

### Canonical serialization

Create `src/tianshu/models/canonical.py` if G1 did not already provide the exact behavior:

```python
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

class RedactedError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    message: str
    retryable: bool
    details_hash: str | None

def canonical_json_bytes(value: BaseModel | Mapping[str, object]) -> bytes: ...
def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str: ...
```

`canonical_json_bytes()` must call `model_dump(mode="json", exclude_none=False)` for Pydantic values, reject NaN/Infinity and non-string mapping keys, and must not accept arbitrary `default=str` coercion. `canonical_sha256()` hashes only the canonical bytes.

### Submission application service

Create in `src/tianshu/application/edicts.py`:

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

class IdempotencyConflict(RuntimeError):
    principal_id: str
    idempotency_key: str
    existing_edict_id: str

class EdictApplicationService:
    def submit(
        self,
        command: SubmitEdictCommand,
        *,
        auth: AuthContext,
        producer: str,
        correlation_id: str,
    ) -> SubmitEdictResult: ...
```

The request hash covers the canonical normalized command excluding generated IDs/timestamps and the idempotency key; it includes the requested Governance Contract hash. An idempotency key is an opaque 1–200 character string with no control characters and is compared byte-for-byte without case folding or trimming. Idempotency is namespaced by `(auth.principal.id, idempotency_key)`, never by an untrusted `submitter` body field.

### Unit of Work and outbox

Create in `src/tianshu/storage/unit_of_work.py` and `src/tianshu/storage/outbox_repo.py`:

```python
class SqliteUnitOfWork:
    def __enter__(self) -> Self: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __exit__(self, exc_type, exc, traceback) -> bool: ...

class OutboxRepository:
    def add(self, conn: sqlite3.Connection, event: EventEnvelope) -> None: ...
    def claim_batch(self, *, owner_id: str, now: datetime, limit: int, lease_seconds: int) -> list[OutboxRecord]: ...
    def mark_published(self, *, event_id: str, owner_id: str, expected_version: int, now: datetime) -> bool: ...
    def mark_failed(self, *, event_id: str, owner_id: str, expected_version: int, error: RedactedError, available_at: datetime) -> bool: ...
    def record_consumption(self, *, event_id: str, consumer_name: str, result_hash: str | None) -> bool: ...

class OutboxDispatcher:
    async def drain_once(self, *, limit: int = 50) -> int: ...
    async def run(self) -> None: ...
    async def stop(self) -> None: ...
```

`SqliteUnitOfWork` acquires the Storage `RLock`, executes `BEGIN IMMEDIATE`, and yields the existing connection. It calls only connection-level primitives such as `_insert_edict(conn, edict)` and `_insert_memorial(conn, memorial)`; it must not call public methods that enter another connection transaction.

`EventBus.on()` requires a stable `consumer_name`. `EventBus.dispatch(event, skip_consumers=...) -> DispatchReport` invokes registered handlers without persisting and reports each success/failure instead of swallowing durable-dispatch errors. `OutboxDispatcher` skips already-recorded `(event_id, consumer_name)` successes, records new successes, and retries only failed/unseen consumers. Each durable consumer must either put its business mutation and consumption marker in one Unit of Work or protect the mutation with a tested unique key/CAS; a consumption row written after an external effect is not sufficient by itself.

Create `src/tianshu/application/event_history.py` with stable consumer `event_history.v1`. For envelopes with an Edict ID, it inserts the envelope’s `event_id` and timestamp into the legacy `events` table using `INSERT ... ON CONFLICT(id) DO NOTHING`, preserving current timeline APIs without making EventBus depend on Storage. System-wide envelopes without an Edict ID are recorded in SystemAuditLog rather than forced into the legacy table whose `edict_id` is non-null. `emit()` remains a compatibility path for explicitly non-durable local events and uses the same registered consumer; all events needed for restart/evidence enter through outbox rows. Remove the `EventBus -> Storage` dependency and its import-linter exemption.

### Persistent decisions

Create `src/tianshu/models/decision.py`:

```python
class DecisionKind(StrEnum):
    TOOL = "tool"
    OUTER_LOOP = "outer_loop"
    PLAN_REVIEW = "plan_review"
    GOVERNED_APPLY = "governed_apply"

class DecisionStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class DecisionRequestV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_request_id: str
    schema_version: Literal[1] = 1
    kind: DecisionKind
    edict_id: str
    memorial_id: str
    request_key: str
    payload: dict[str, JsonValue]
    payload_hash: str
    requested_by: str
    expires_at: datetime
    status: DecisionStatus
    version: int
    created_at: datetime
    updated_at: datetime

class DecisionResolutionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_request_id: str
    action: str
    reason: str
    payload: dict[str, JsonValue]
    actor_principal_id: str
    actor_display_name: str
    resolved_at: datetime

class RequestDecisionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: DecisionKind
    edict_id: str
    memorial_id: str
    request_key: str
    payload: dict[str, JsonValue]
    expires_at: datetime

class ResolveDecisionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: str
    reason: str
    payload: dict[str, JsonValue]
    expected_version: int

class DecisionRecordV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request: DecisionRequestV1
    resolution: DecisionResolutionV1 | None
```

Create `src/tianshu/governance/decision_service.py`:

```python
class DecisionService:
    def request(self, command: RequestDecisionCommand, *, auth: AuthContext) -> DecisionRequestV1: ...
    def get(self, decision_request_id: str) -> DecisionRecordV1 | None: ...
    def list_pending(self, *, kind: DecisionKind | None = None) -> list[DecisionRequestV1]: ...
    def resolve(
        self,
        decision_request_id: str,
        command: ResolveDecisionCommand,
        *,
        auth: AuthContext,
    ) -> DecisionResolutionV1: ...
    def expire_due(self, *, now: datetime, limit: int = 100) -> int: ...
```

Neither command contains actor identity. `request_key` is stable for the governed fact (`tool_call_id`, `outer-loop:<level>:<iteration>`, plan revision ID, or approved change-set hash). `DecisionService.request()` derives `requested_by` from `AuthContext`; the same Memorial/kind/key with the same payload hash returns the existing request, while a different hash is a conflict. `DecisionService.resolve()` derives the resolver from `AuthContext`, requires a non-blank reason, uses `status='pending' AND version=? AND expires_at>?` CAS, inserts the immutable resolution, updates the request, and writes `decision.resolved` to the outbox in one transaction. Concurrent, expired, cancelled, and late attempts return structured conflicts and append a system audit denial; they do not overwrite the winning resolution.

Validate actions by kind: tool accepts `approve|reject|guide`; outer-loop accepts `continue|accept_as_is|abort|modify_acceptance`; plan review accepts `approve|reject|amend`; governed apply accepts `approve|reject`. Any action-specific payload is strict and versioned inside `payload`; unknown actions/fields return 422 before a state transition.

### Versioned RunState

Create `src/tianshu/models/run_state.py`:

```python
class RunPhase(StrEnum):
    SUBMITTED = "submitted"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_DECISION = "waiting_decision"
    PAUSED = "paused"
    AUDITING = "auditing"
    COMPLETED = "completed"
    FAILED = "failed"

class PersistedChatMessageV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[dict[str, JsonValue], ...]
    name: str | None
    tool_call_id: str | None

class ToolProposalV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    arguments_hash: str
    tool_tier: str
    policy_rule_id: str | None
    proposed_at: datetime

class IterationSummaryV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    iteration: int
    level: Literal["L0", "L1", "L2", "L3"]
    output_artifact_ref: str | None
    critic_verdict: str | None
    critic_issue_class: str | None
    feedback: str | None
    usage: UsageSummary
    completed_at: datetime

class AgentContinuationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["agent"] = "agent"
    messages: tuple[PersistedChatMessageV1, ...]
    pending_tool: ToolProposalV1 | None
    iteration: int
    usage: UsageSummary
    checkpoint_ref: str | None
    resolved_decision_id: str | None
    side_effect_cursor: int

class OuterLoopContinuationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["outer_loop"] = "outer_loop"
    level: Literal["L0", "L1", "L2", "L3"]
    iteration: int
    best_output: str | None
    feedback: str | None
    steer: str | None
    history: tuple[IterationSummaryV1, ...]
    same_issue_streak: int
    last_critic_issue_class: str | None
    l1_rounds_used: int
    l2_rounds_used: int
    consultation_advice: str | None
    usage: UsageSummary
    total_cost_cny: Decimal
    checkpoint_ref: str | None
    resolved_decision_id: str | None
    side_effect_cursor: int

class RunStateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    memorial_id: str
    edict_id: str
    schema_version: Literal[1] = 1
    phase: RunPhase
    continuation: AgentContinuationV1 | OuterLoopContinuationV1
    checkpoint_ref: str | None
    side_effect_cursor: int
    version: int
    created_at: datetime
    updated_at: datetime
```

`memorial_id` is the canonical run identifier; do not introduce a competing run ID table. `RunStateRepository.save(state, expected_version)` and `transition(...)` use version CAS. The mapper validates that `continuation_kind` equals the JSON discriminator and rejects unknown `schema_version`/kind before returning state. Persist data needed to reconstruct work, never an awaitable, task, stack frame, generator, open client, or closure.

Persisted messages/tool arguments may contain user content but must never contain resolved credential values. Tool arguments store G1 secret references/tokens and redacted display values; the governed boundary resolves a reference only immediately before execution. Restart tests include a sentinel secret and assert it is absent from `run_states`, logs, events, audit, journal, and Evidence JSON.

### Attempts, leases, and side-effect journal

Create `src/tianshu/storage/attempt_ledger.py`, `src/tianshu/storage/side_effect_journal.py`, `src/tianshu/application/dispatcher.py`, and `src/tianshu/executor/side_effects.py`:

```python
class AttemptStatus(StrEnum):
    CLAIMABLE = "claimable"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUSPENDED = "suspended"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"

@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    attempt_id: str
    memorial_id: str
    attempt_no: int
    status: AttemptStatus
    owner_id: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    available_at: datetime
    max_attempts: int
    failure: RedactedError | None
    version: int
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True, slots=True)
class ReconcileResult:
    requeued_attempt_ids: tuple[str, ...]
    dead_letter_attempt_ids: tuple[str, ...]

class DurableDispatcher:
    def claim(self, *, owner_id: str, now: datetime, limit: int) -> list[ExecutionAttempt]: ...
    def heartbeat(self, *, attempt_id: str, owner_id: str, expected_version: int, now: datetime) -> ExecutionAttempt: ...
    def complete(self, *, attempt_id: str, owner_id: str, expected_version: int, outcome: AttemptOutcome) -> ExecutionAttempt: ...
    def reconcile_expired(self, *, now: datetime, limit: int = 100) -> ReconcileResult: ...

class SideEffectSemantics(StrEnum):
    PROVIDER_IDEMPOTENT = "provider_idempotent"
    RECEIPT_LOOKUP = "receipt_lookup"
    WORKSPACE_ONLY = "workspace_only"
    UNTRACKED_EXTERNAL = "untracked_external"
    OPAQUE_CLI = "opaque_cli"

class SideEffectIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    effect_id: str
    memorial_id: str
    attempt_id: str
    sequence_no: int
    boundary: str
    semantics: SideEffectSemantics
    operation: str
    request_redacted: dict[str, JsonValue]
    intent_hash: str
    idempotency_key: str | None

class SideEffectReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    effect_id: str
    provider_receipt: dict[str, JsonValue]
    result_hash: str
    effective_at: datetime

class SideEffectBoundary(Protocol):
    name: str
    semantics: SideEffectSemantics
    async def execute(self, intent: SideEffectIntent) -> SideEffectReceipt: ...
    async def lookup_receipt(self, idempotency_key: str) -> SideEffectReceipt | None: ...
```

Every supported managed effect follows `intent persisted -> effect attempted -> receipt persisted -> RunState cursor advanced -> attempt/outbox ack`. A stale lease owner cannot persist a receipt or terminal attempt because every write includes `owner_id` and `version`. `UNTRACKED_EXTERNAL` after a possible send becomes `uncertain`, creates a DecisionRequest, and is not automatically retried. `OPAQUE_CLI` records process-level evidence only.

### Planner evidence

Create `src/tianshu/evals/planner_quality.py` and `src/tianshu/storage/plan_repo.py`:

```python
class PlanQualityEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    estimated_tokens: int | None
    estimated_seconds: float | None
    estimated_cost_cny: Decimal | None
    actual_tokens: int | None
    actual_seconds: float | None
    actual_cost_cny: Decimal | None
    failure_class: str | None
    acceptance_result: str | None

class PlanDiffEntryV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    operation: Literal["add", "remove", "replace"]
    before_hash: str | None
    after_hash: str | None

class PlanRevisionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_revision_id: str
    edict_id: str
    memorial_id: str
    revision: int
    source: Literal["initial", "amend", "replan"]
    reason: str
    previous_hash: str | None
    plan: dict[str, JsonValue]
    plan_hash: str
    diff: tuple[PlanDiffEntryV1, ...]
    quality: PlanQualityEvidenceV1
    status: Literal["draft", "closed"]
    version: int
    created_at: datetime
    closed_at: datetime | None
```

Record initial plans, amendments, and replans. A replan may occur before dispatch or at an explicit safe outer-loop boundary; never mutate an actively executing DAG graph. G2 stores quality evidence and diffs but does not build a dynamic DAG runtime.

### Artifact and Evidence Bundle v1

Create `src/tianshu/evidence/models.py`, `src/tianshu/evidence/artifact_store.py`, and `src/tianshu/evidence/service.py`:

```python
class ArtifactRecordV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    artifact_id: str
    digest: str
    size_bytes: int
    mime_type: str
    uri: str
    producer: str
    produced_at: datetime
    environment_fingerprint: str
    retention_policy: str
    delete_policy: str
    redaction_policy: str

class ExecutorEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    executor_id: str
    adapter_version: str
    isolation_mode: Literal["managed", "contained", "unmanaged"]
    capability_manifest: ExecutorCapabilityManifestV1
    capability_manifest_hash: str
    attempt_ids: tuple[str, ...]
    declared_limitations: tuple[str, ...]

class ArtifactEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    artifact_id: str
    role: str
    digest: str
    size_bytes: int
    mime_type: str
    uri: str

class ChangeEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    operation: Literal["add", "modify", "delete", "rename", "mode"]
    before_hash: str | None
    after_hash: str | None
    previous_path: str | None
    mode_before: str | None
    mode_after: str | None
    binary: bool

class CheckEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    check_id: str
    name: str
    status: Literal["passed", "failed", "unavailable", "skipped"]
    command_fingerprint: str | None
    exit_code: int | None
    output_artifact_digest: str | None
    started_at: datetime
    completed_at: datetime

class PolicyDecisionEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_request_id: str
    kind: DecisionKind
    action: str
    actor_principal_id: str
    reason: str
    payload_hash: str
    resolved_at: datetime

class CostEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    currency: Literal["CNY"] = "CNY"
    requested_budget: Decimal | None
    effective_budget: Decimal | None
    actual_cost: Decimal
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int

class EnvironmentEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tianshu_version: str
    python_version: str
    platform: str
    architecture: str
    dependency_lock_hash: str
    workspace_base_revision: str | None
    environment_fingerprint: str

class AuditorConclusionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    auditor_id: str
    verdict: Literal["pass", "fail"]
    reason: str
    required_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    evaluated_at: datetime

class ReproductionCommandV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    label: str
    argv: tuple[str, ...]
    cwd_ref: str
    environment_keys: tuple[str, ...]
    expected_result_hash: str | None

class EvidenceBundleV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str
    edict_id: str
    memorial_id: str
    run_state_version: int
    requested_contract: RequestedGovernanceContractV1
    requested_contract_hash: str
    effective_contract: EffectiveGovernanceContractV1
    effective_contract_hash: str
    executor: ExecutorEvidenceV1
    artifacts: tuple[ArtifactEvidenceV1, ...]
    changes: tuple[ChangeEvidenceV1, ...]
    checks: tuple[CheckEvidenceV1, ...]
    policy_decisions: tuple[PolicyDecisionEvidenceV1, ...]
    cost: CostEvidenceV1
    environment: EnvironmentEvidenceV1
    auditor_conclusion: AuditorConclusionV1
    reproduction_commands: tuple[ReproductionCommandV1, ...]
    created_at: datetime
    closed_at: datetime
    content_hash: str

class ArtifactStore:
    def put(self, stream: BinaryIO, metadata: ArtifactMetadata) -> ArtifactRecordV1: ...
    def open(self, digest: str) -> BinaryIO: ...
    def verify(self, digest: str) -> bool: ...

class EvidenceService:
    def create_draft(self, *, edict_id: str, memorial_id: str) -> str: ...
    def close(self, bundle_id: str, *, expected_version: int) -> EvidenceBundleV1: ...
    def export_json(self, bundle_id: str) -> bytes: ...

class EvidenceApplicationService:
    def close_run(self, *, memorial_id: str, auth: AuthContext) -> EvidenceBundleV1: ...
    def replay(
        self,
        bundle_id: str,
        *,
        auth: AuthContext,
        idempotency_key: str,
    ) -> SubmitEdictResult: ...
```

`EvidenceService` lives in `tianshu.evidence` and never imports `tianshu.application` or `tianshu.auditor`. `EvidenceApplicationService` lives in `src/tianshu/application/evidence.py`; it coordinates the independent auditor and EvidenceService, and it is the only replay orchestrator. `content_hash` is computed over the canonical closed model with `content_hash` omitted. `EvidenceService.close()` fails closed if any referenced plan revision is still draft or if mandatory contract, manifest, artifact, change/check, decision, cost/environment, or auditor evidence required by the effective contract is absent. A successful close stores one immutable canonical snapshot. Replay parses evidence into a new `SubmitEdictCommand` and calls `EdictApplicationService`; it never executes a stored shell string.

Artifact bytes live at `<artifact_dir>/<digest[0:2]>/<digest>`. Paths are derived only from validated lowercase SHA-256 digests, resolved beneath the configured root, and opened without following symlinks where the platform supports it. Write to a same-directory temporary file, fsync the file, atomically rename, fsync the parent directory, then insert metadata. Identical digests deduplicate. Serialize quota reservation under the storage lock and check streamed bytes against per-object and total quotas; large tool results place a summary and `artifact://sha256/<digest>` URI in prompts rather than embedding the payload.

### System audit, readiness, and notification delivery

G1 creates `SystemAuditLog` and the narrow repository. G2 extends their interfaces:

```python
class SystemAuditEventV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    audit_event_id: str
    event_type: str
    actor_principal_id: str
    actor_display_name: str
    source_ip: str | None
    correlation_id: str
    edict_id: str | None
    memorial_id: str | None
    reason: str
    payload_redacted: dict[str, JsonValue]
    payload_hash: str
    previous_hash: str | None
    row_hash: str
    created_at: datetime

@dataclass(frozen=True, slots=True)
class SystemAuditQuery:
    event_type: str | None = None
    edict_id: str | None = None
    correlation_id: str | None = None
    limit: int = 100
    offset: int = 0

class SystemAuditLog:
    def append(self, event: SystemAuditEventV1) -> None: ...
    def list(self, query: SystemAuditQuery) -> list[SystemAuditEventV1]: ...

class ReadinessService:
    def check(self, *, now: datetime) -> ReadinessReport: ...

class ReadinessCheckV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    status: Literal["ready", "unready"]
    reason_code: str | None
    observed: int | float | str | None
    threshold: int | float | str | None

class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["ready", "unready"]
    checks: tuple[ReadinessCheckV1, ...]
    checked_at: datetime

class DeliveryMode(StrEnum):
    PROVIDER_IDEMPOTENT = "provider_idempotent"
    AT_LEAST_ONCE = "at_least_once"
    AT_MOST_ONCE = "at_most_once"

class DeliveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    delivery_id: str
    idempotency_key: str
    event_id: str
    edict_id: str | None
    memorial_id: str | None
    payload_redacted: dict[str, JsonValue]
    rendered_text_redacted: str

class DeliveryReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    delivery_id: str
    provider_message_id: str | None
    accepted_at: datetime
    receipt_metadata: dict[str, JsonValue]

class ChannelAdapter(Protocol):
    name: str
    delivery_mode: DeliveryMode
    async def send(self, request: DeliveryRequest) -> DeliveryReceipt: ...
    async def lookup(self, idempotency_key: str) -> DeliveryReceipt | None: ...

class DeliveryWorker:
    async def drain_once(self, *, now: datetime, limit: int = 50) -> int: ...
    async def run(self) -> None: ...
    async def stop(self) -> None: ...
```

System audit records actor principal, source IP, correlation ID, Edict/Memorial IDs, event type, reason, redacted payload hash, previous row hash, row hash, and timestamp. Under the Storage lock, `previous_hash` is the latest committed row hash and `row_hash` is SHA-256 of the canonical event with `row_hash` omitted; insert and governed DB mutation share one transaction when both are local SQLite writes. The hash chain is tamper-evident only under the documented host/DB-admin threat limitation.

`provider_idempotent` retries with the same key and may look up a receipt. `at_least_once` retries and reports “可能重复”. `at_most_once` does not retry after a send was attempted and reports “可能丢失”. A successful send followed by a crash is resolved according to that declared mode, never relabeled as exactly-once.

### HTTP contracts consumed by G3

All responses use the existing `ApiResponse` envelope and G1 authentication/correlation middleware.

| Method and path | Request | Success | Deterministic errors |
|---|---|---|---|
| `POST /api/edicts` | Existing Edict body plus mandatory `Idempotency-Key`; any legacy body key must match | 202 new or 200 deduplicated; metadata includes key, request hash, event ID, and Memorial ID | 409 key/hash conflict; 422 missing/mismatched key or invalid contract |
| `GET /api/decisions?status=pending&kind=tool` | Optional status/kind/Edict/Memorial filters and bounded pagination | Immutable DecisionRequest summaries | 422 invalid filter |
| `GET /api/decisions/{id}` | None | Request plus resolution when present | 404 unknown/not visible |
| `POST /api/decisions/{id}/resolve` | `{action, reason, expected_version, payload}` | Winning immutable DecisionResolution | 409 stale/resolved/expired/cancelled; 422 blank reason/action invalid |
| `GET /api/edicts/{edict_id}/evidence` | None | Ordered bundle summaries and close status | 404 unknown Edict |
| `GET /api/evidence/{bundle_id}/download` | None | `application/json` canonical closed bytes and `ETag` equal to content hash | 404 unknown; 409 draft |
| `POST /api/evidence/{bundle_id}/replay` | `{idempotency_key}` | New/deduplicated `SubmitEdictResult` through governed submission | 409 draft/conflicting key; 422 invalid snapshot |
| `GET /api/notifications/deliveries` | Status/channel/Edict filters and bounded pagination | Rows including attempts, declared mode, receipt/uncertainty, and next retry | 422 invalid filter |
| `POST /api/notifications/deliveries/{id}/retry` | `{reason, expected_version}` | New pending/retry-wait state | 409 delivered/in-flight/stale/not manually retryable; 422 blank reason |
| `GET /health/live` | None | 200 `{"status":"ok"}` | None from dependency state |
| `GET /health/ready` | None | 200 `{"status":"ready","checks":...}` | 503 `{"status":"unready","checks":...}` with no secrets |

G3 may render these APIs but must not infer promotion/readiness/evidence truth from WebSocket presence or page-local state.

### Process lifecycle order

Startup order is fixed: open and migrate Storage -> construct repositories/services -> register stable EventBus consumers -> start outbox dispatcher -> start attempt dispatcher/reconciler -> start delivery worker -> start scheduler/Bots/MCP -> mark readiness eligible. A component is not ready merely because its task object was created; it must publish an initial heartbeat after its first successful storage probe.

Shutdown order is fixed: mark readiness unready -> stop request producers (scheduler/Bots/MCP admission) -> stop new outbox/attempt/delivery claims -> allow a configured bounded drain -> cancel remaining in-process work and leave durable leases for reconciliation -> close channel/provider clients -> force-flush and shut down OTel -> close artifact handles -> close SQLite. Every `stop()` is idempotent and has a timeout; startup failure unwinds only components that actually started. Add a local-script smoke proving `./scripts/local.sh start --dev`, readiness polling, and `./scripts/local.sh stop` all return without hanging.

### G2 settings added to `TianshuSettings`

| Setting | Default | Validation/use |
|---|---:|---|
| `outbox_poll_interval_seconds` | `0.25` | Positive; idle poll only, wake event may shorten it |
| `outbox_lease_seconds` | `30` | Positive and greater than two poll intervals |
| `outbox_max_attempts` | `20` | Positive; exponential backoff capped below |
| `attempt_poll_interval_seconds` | `0.25` | Positive |
| `attempt_lease_seconds` | `60` | Greater than three heartbeat intervals |
| `attempt_heartbeat_seconds` | `15` | Positive and less than lease/3 |
| `attempt_max_attempts` | `3` | Positive; suspensions do not consume it |
| `delivery_poll_interval_seconds` | `0.5` | Positive; worker also wakes for next stored deadline |
| `delivery_lease_seconds` | `30` | Positive |
| `delivery_max_attempts` | `8` | Positive; applies only when mode permits retry |
| `durable_retry_base_seconds` | `1` | Positive; jittered exponential backoff |
| `durable_retry_max_seconds` | `300` | At least base |
| `artifact_dir` | `~/.tianshu/artifacts` | Expanded absolute root, mode 0700 where supported |
| `artifact_max_bytes` | `104857600` | Positive per-object limit (100 MiB) |
| `artifact_quota_bytes` | `5368709120` | At least per-object limit (5 GiB) |
| `artifact_orphan_grace_seconds` | `3600` | Positive safety age before sweep |
| `notify_timezone` | `Asia/Shanghai` | Valid IANA `ZoneInfo` name; quiet deadlines stored in UTC |
| `readiness_outbox_max_pending` | `1000` | Non-negative backlog threshold |
| `readiness_outbox_max_age_seconds` | `60` | Positive oldest-pending threshold |
| `readiness_worker_max_stale_seconds` | `45` | Greater than active heartbeat intervals |
| `readiness_expired_lease_grace_seconds` | `30` | Positive reconciliation grace |
| `shutdown_drain_seconds` | `15` | Non-negative bounded shutdown wait |

Retryable outbox/attempt/delivery failures use full jitter: `delay = random.uniform(0, min(max_seconds, base_seconds * 2 ** (attempt_count - 1)))`. Inject Clock and random source in tests; tests use zero-delay wake signals and never sleep for production defaults.

---

## Exact Migration Ownership and Tables

Each migration defines a tuple of normalized SQL statements, computes a fixed SHA-256 checksum from the migration name plus normalized statements, and is appended to `MIGRATIONS` in ascending order. Add fresh-install, v2-upgrade, checksum-tamper, rollback, and concurrent-start coverage to the existing migration test suite.

### Version 3 · `0003_submission_outbox`

Create these tables and indexes:

```sql
CREATE TABLE submission_idempotency (
    principal_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE RESTRICT,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE RESTRICT,
    event_id TEXT NOT NULL REFERENCES outbox_events(event_id) ON DELETE RESTRICT,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (principal_id, idempotency_key),
    UNIQUE (event_id)
);

CREATE TABLE outbox_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    edict_id TEXT REFERENCES edicts(id) ON DELETE CASCADE,
    memorial_id TEXT REFERENCES memorials(id) ON DELETE CASCADE,
    producer TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','claimed','published','retry_wait','dead_letter')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 20 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_json TEXT,
    published_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);

CREATE TABLE outbox_consumptions (
    event_id TEXT NOT NULL REFERENCES outbox_events(event_id) ON DELETE CASCADE,
    consumer_name TEXT NOT NULL,
    result_hash TEXT,
    consumed_at TEXT NOT NULL,
    PRIMARY KEY (event_id, consumer_name)
);

CREATE INDEX idx_outbox_claim
ON outbox_events(status, available_at, lease_expires_at);
CREATE INDEX idx_outbox_edict
ON outbox_events(edict_id, occurred_at);
```

The v3 transaction inserts Edict, initial Memorial, `edict.submitted` outbox row, and `submission_idempotency` before one commit. The legacy `edicts.idempotency_key` remains readable for compatibility but is not the G2 uniqueness authority.

### Version 4 · `0004_decisions_run_state`

```sql
CREATE TABLE decision_requests (
    decision_request_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    kind TEXT NOT NULL CHECK (kind IN ('tool','outer_loop','plan_review','governed_apply')),
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
    request_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    requested_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','resolved','expired','cancelled')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (memorial_id, kind, request_key)
);

CREATE TABLE decision_resolutions (
    decision_request_id TEXT PRIMARY KEY
        REFERENCES decision_requests(decision_request_id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    payload_json TEXT NOT NULL,
    actor_principal_id TEXT NOT NULL,
    actor_display_name TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);

CREATE TABLE run_states (
    memorial_id TEXT PRIMARY KEY REFERENCES memorials(id) ON DELETE CASCADE,
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    phase TEXT NOT NULL CHECK (phase IN (
        'submitted','planning','executing','waiting_decision','paused','auditing','completed','failed'
    )),
    continuation_kind TEXT NOT NULL CHECK (continuation_kind IN ('agent','outer_loop')),
    continuation_json TEXT NOT NULL,
    checkpoint_ref TEXT,
    side_effect_cursor INTEGER NOT NULL DEFAULT 0 CHECK (side_effect_cursor >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_decisions_pending
ON decision_requests(status, kind, expires_at);
CREATE INDEX idx_decisions_memorial
ON decision_requests(memorial_id, created_at);
CREATE INDEX idx_run_states_edict
ON run_states(edict_id, updated_at);
```

Resolution and `decision.resolved` outbox insertion share one transaction. Expiry uses the same CAS and emits `decision.expired`. A late resolution attempt does not create a second resolution row.

### Version 5 · `0005_attempts_side_effects`

```sql
CREATE TABLE execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    status TEXT NOT NULL CHECK (status IN (
        'claimable','claimed','running','suspended','succeeded','failed','dead_letter'
    )),
    owner_id TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    available_at TEXT NOT NULL,
    max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
    failure_json TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (memorial_id, attempt_no)
);

CREATE TABLE side_effect_journal (
    effect_id TEXT PRIMARY KEY,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL REFERENCES execution_attempts(attempt_id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    boundary TEXT NOT NULL,
    semantics TEXT NOT NULL CHECK (semantics IN (
        'provider_idempotent','receipt_lookup','workspace_only','untracked_external','opaque_cli'
    )),
    operation TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64),
    request_json_redacted TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'intended','executing','receipted','uncertain','failed'
    )),
    idempotency_key TEXT,
    provider_receipt_json TEXT,
    result_hash TEXT,
    error_json TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (memorial_id, sequence_no),
    UNIQUE (boundary, idempotency_key)
);

CREATE INDEX idx_attempts_claim
ON execution_attempts(status, available_at, lease_expires_at);
CREATE INDEX idx_attempts_memorial
ON execution_attempts(memorial_id, attempt_no);
CREATE INDEX idx_effects_attempt
ON side_effect_journal(attempt_id, sequence_no);
CREATE INDEX idx_effects_uncertain
ON side_effect_journal(status, updated_at);
```

The partial guarantee requires a non-null idempotency key for `provider_idempotent` and `receipt_lookup`. `workspace_only` relies on the G1 staging/apply boundary. `untracked_external` and `opaque_cli` cannot be auto-promoted to a stronger semantic.

### Version 6 · `0006_plans_artifacts_evidence_audit`

```sql
CREATE TABLE plan_revisions (
    plan_revision_id TEXT PRIMARY KEY,
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    source TEXT NOT NULL CHECK (source IN ('initial','amend','replan')),
    reason TEXT NOT NULL,
    previous_hash TEXT,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (length(plan_hash) = 64),
    diff_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','closed')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    CHECK (
        (status = 'draft' AND closed_at IS NULL)
        OR
        (status = 'closed' AND closed_at IS NOT NULL)
    ),
    UNIQUE (edict_id, revision)
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE CHECK (length(digest) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    mime_type TEXT NOT NULL,
    uri TEXT NOT NULL UNIQUE,
    producer TEXT NOT NULL,
    produced_at TEXT NOT NULL,
    environment_fingerprint TEXT NOT NULL,
    retention_policy TEXT NOT NULL,
    delete_policy TEXT NOT NULL,
    redaction_policy TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'available' CHECK (state IN ('available','tombstoned')),
    tombstoned_at TEXT
);

CREATE TABLE evidence_bundles (
    bundle_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL CHECK (schema_version = '1.0'),
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE RESTRICT,
    memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE RESTRICT,
    run_state_version INTEGER NOT NULL CHECK (run_state_version > 0),
    status TEXT NOT NULL CHECK (status IN ('draft','closed')),
    body_json TEXT NOT NULL,
    content_hash TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    CHECK (
        (status = 'draft' AND content_hash IS NULL AND closed_at IS NULL)
        OR
        (status = 'closed' AND length(content_hash) = 64 AND closed_at IS NOT NULL)
    )
);

CREATE TABLE evidence_bundle_artifacts (
    bundle_id TEXT NOT NULL REFERENCES evidence_bundles(bundle_id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (bundle_id, role, ordinal),
    UNIQUE (bundle_id, artifact_id, role)
);

CREATE INDEX idx_plan_revisions_memorial
ON plan_revisions(memorial_id, revision);
CREATE INDEX idx_evidence_edict
ON evidence_bundles(edict_id, created_at);
CREATE INDEX idx_evidence_memorial
ON evidence_bundles(memorial_id, created_at);
CREATE INDEX idx_system_audit_event_type_created
ON system_audit_events(event_type, created_at);
CREATE INDEX idx_system_audit_edict_created
ON system_audit_events(edict_id, created_at);
CREATE INDEX idx_system_audit_correlation_created
ON system_audit_events(correlation_id, created_at);

CREATE TRIGGER plan_revision_closed_no_update
BEFORE UPDATE ON plan_revisions
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed plan revision is immutable');
END;

CREATE TRIGGER plan_revision_closed_no_delete
BEFORE DELETE ON plan_revisions
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed plan revision is immutable');
END;

CREATE TRIGGER evidence_closed_no_update
BEFORE UPDATE ON evidence_bundles
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed evidence bundle is immutable');
END;

CREATE TRIGGER evidence_closed_no_delete
BEFORE DELETE ON evidence_bundles
WHEN OLD.status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed evidence bundle is immutable');
END;

CREATE TRIGGER system_audit_no_update
BEFORE UPDATE ON system_audit_events
BEGIN
    SELECT RAISE(ABORT, 'system audit events are append-only');
END;

CREATE TRIGGER system_audit_no_delete
BEFORE DELETE ON system_audit_events
BEGIN
    SELECT RAISE(ABORT, 'system audit events are append-only');
END;
```

Before the static statements, the v6 callback checks `PRAGMA table_info(system_audit_events)`. It requires the G1 base columns and conditionally adds exactly these nullable context columns when absent: `correlation_id`, `edict_id`, `memorial_id`, `source_ip`, `payload_hash`, `previous_hash`, and `row_hash`. It then executes the listed indexes/triggers. The migration checksum includes the ordered column specification and helper algorithm version so conditional execution cannot change without a checksum change.

### Version 7 · `0007_notification_deliveries`

```sql
CREATE TABLE notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    edict_id TEXT REFERENCES edicts(id) ON DELETE SET NULL,
    memorial_id TEXT REFERENCES memorials(id) ON DELETE SET NULL,
    channel_name TEXT NOT NULL,
    payload_json_redacted TEXT NOT NULL,
    rendered_text_redacted TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    delivery_mode TEXT NOT NULL CHECK (delivery_mode IN (
        'provider_idempotent','at_least_once','at_most_once'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'pending','claimed','delivered','retry_wait','uncertain','dead_letter'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    provider_receipt_json TEXT,
    last_error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    UNIQUE (event_id, channel_name),
    UNIQUE (channel_name, idempotency_key)
);

CREATE INDEX idx_notification_claim
ON notification_deliveries(status, available_at, lease_expires_at);
CREATE INDEX idx_notification_edict
ON notification_deliveries(edict_id, created_at);
```

The existing `pending_notifications` table becomes a read-only legacy migration source. On v7 upgrade, decode each row’s message/channel JSON and create one delivery per channel. The Python migration callback computes `delivery_id = 'legacy-' + sha256((legacy_id + ':' + channel_name).encode()).hexdigest()` and `idempotency_key = sha256(('legacy-notification:' + legacy_id + ':' + channel_name).encode()).hexdigest()`, then sets `event_id='legacy-notification-' + legacy_id`, `delivery_mode='at_least_once'`, `status='pending'`, and `available_at=created_at`. Malformed JSON raises `SchemaCompatibilityError` and rolls back v7; it is never silently discarded. Leave the old table intact until a later compatibility-removal migration.

---

## Import and Write-Boundary Contract

Update `pyproject.toml` in Increment 1. Replace the broad layer contract with this explicit G2 order and preserve the existing Telegram/Feishu extras contract:

```toml
[[tool.importlinter.contracts]]
name = "G2 durable governance boundaries"
type = "layers"
layers = [
    "tianshu.gateway : tianshu.cli : tianshu.bootstrap",
    "tianshu.application : tianshu.scheduler : tianshu.notifier",
    "tianshu.executor",
    "tianshu.governance : tianshu.evidence : tianshu.auditor",
    "tianshu.storage",
    "tianshu.models : tianshu.kernel : tianshu.config : tianshu.bus",
]
ignore_imports = [
    "tianshu.kernel.ambient -> tianshu.persona.model",
]
```

Remove `tianshu.bus.event_bus -> tianshu.storage`. Storage repositories return rows, primitives, or storage record dataclasses and never import application/governance/evidence services. Services map storage records into immutable public models.

Add `tests/architecture/test_durable_write_boundaries.py`, using the existing AST-test conventions, with these rules:

- `gateway/edicts_api.py`, `gateway/mcp_server.py`, `gateway/core/edict_bridge.py`, `tools/submit_edict.py`, `tools/schedule_edict.py`, `executor/approvals.py`, and CLI submission code may not call `Storage.save_edict`, `save_memorial`, `append_event`, or write `_conn` directly for top-level submission/amend flows.
- Decision HTTP/Bot adapters may not call decision/run-state repositories directly; they call `DecisionService`.
- `tianshu.evidence` may not import `tianshu.application` or `tianshu.auditor`. `EvidenceApplicationService.replay()` may not call a shell, `ExecutionGateway.run`, or a channel adapter directly; it calls `EdictApplicationService.submit()`.
- Gateway modules may not reach into `storage._conn` for the new G2 tables.
- `EventBus` may not import `tianshu.storage` or call any persistence method.

Run after every boundary-changing increment:

```bash
uv run --frozen lint-imports
uv run --frozen pytest tests/architecture/test_durable_write_boundaries.py -q
```

---

## Increment 1: Atomic submission Unit of Work and v3 outbox

**Slice:** G2-A

**Files:**

- Create: `src/tianshu/application/__init__.py`
- Create: `src/tianshu/application/edicts.py`
- Create: `src/tianshu/models/canonical.py` only if G1 lacks the exact canonical helper
- Create: `src/tianshu/storage/unit_of_work.py`
- Create: `src/tianshu/storage/outbox_repo.py`
- Modify: `src/tianshu/storage/_base.py`
- Modify: `src/tianshu/storage/edict_repo.py`
- Modify: `src/tianshu/storage/memorial_repo.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `pyproject.toml`
- Test: `tests/storage/test_durable_schema_v3.py`
- Test: `tests/application/test_edict_application_service.py`
- Test: `tests/integration/test_g1_g2_handoff.py`
- Test: `tests/integration/test_edict_idempotency.py`
- Test: `tests/architecture/test_durable_write_boundaries.py`

### 1.1 RED · Lock migration ownership and transaction semantics

- [ ] Add the G1 handoff contract test for AuthContext, Governance Contract, ExecutionGateway/WorkspaceService maturity, liveness, v2 ownership, and base system-audit columns.
- [ ] Add schema tests asserting versions are exactly `1, 2, 3`, names/checksums are stable, fresh install creates all v3 objects, v2 upgrade preserves rows, a forced exception at each insert rolls back all four records, and `:memory:` uses the same connection.
- [ ] Add application tests for first submit, same principal/key/same hash deduplication, same principal/key/different hash conflict, different principals with the same key, and canonical request hashing.
- [ ] Add a regression test proving the current sequence can leave an Edict without its initial Memorial/outbox when the second write fails.

Run:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v3.py \
  tests/application/test_edict_application_service.py \
  tests/integration/test_g1_g2_handoff.py \
  tests/integration/test_edict_idempotency.py -q
```

Expected RED: v3 tables, application service, atomic rollback, and hash conflict behavior do not exist.

### 1.2 GREEN · Implement connection primitives and one commit

- [ ] Change `_StorageBase._lock` to `threading.RLock`; add a private transaction context that executes `BEGIN IMMEDIATE` on the existing connection and has exactly one commit/rollback owner.
- [ ] Extract `_insert_edict(conn, edict)` and `_insert_memorial(conn, memorial)` connection-level primitives. Existing `save_*` methods call those primitives inside their current lock/transaction behavior; `SqliteUnitOfWork` calls the primitives directly.
- [ ] Implement v3, repository record mapping, and `EdictApplicationService.submit()` with one transaction for Edict, Memorial, idempotency response, and outbox event.
- [ ] Convert uniqueness races into either a deterministic deduplicated result or `IdempotencyConflict`; never use “check then insert” as the only guard.
- [ ] Store the exact serialized response needed to return the original Edict/Memorial/event IDs on retry.

### 1.3 REFACTOR · Prove the boundary

- [ ] Remove the legacy `EventBus -> Storage` import and import-linter exemption; do not migrate callers yet.
- [ ] Run focused tests, Ruff on touched paths, mypy on touched packages, import-linter, and `git diff --check`.

Run:

```bash
uv run --frozen pytest tests/storage/test_durable_schema_v3.py tests/application tests/integration/test_g1_g2_handoff.py tests/integration/test_edict_idempotency.py -q
uv run --frozen ruff check src/tianshu/application src/tianshu/storage tests/application tests/storage/test_durable_schema_v3.py tests/integration/test_edict_idempotency.py
uv run --frozen mypy src/tianshu/application src/tianshu/storage/unit_of_work.py src/tianshu/storage/outbox_repo.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: add durable submission unit of work`

---

## Increment 2: Unified ingress and recoverable outbox dispatch

**Slice:** G2-A

**Files:**

- Modify: `src/tianshu/edict_ops.py`
- Modify: `src/tianshu/bus/event_bus.py`
- Create: `src/tianshu/application/event_history.py`
- Modify: `src/tianshu/bootstrap/wiring_storage.py`
- Modify: `src/tianshu/bootstrap/wiring_scheduler.py`
- Modify: `src/tianshu/app.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/gateway/edicts_api.py`
- Modify: `src/tianshu/gateway/mcp_server.py`
- Modify: `src/tianshu/gateway/core/edict_bridge.py`
- Modify: `src/tianshu/gateway/core/outbound.py`
- Modify: `src/tianshu/gateway/feishu/approval_card.py`
- Modify: `src/tianshu/gateway/telegram/approval_kb.py`
- Modify: `src/tianshu/gateway/personas_api.py`
- Modify: `src/tianshu/tools/submit_edict.py`
- Modify: `src/tianshu/tools/schedule_edict.py`
- Modify: `src/tianshu/executor/approvals.py` amend path
- Modify: `src/tianshu/storage/event_repo.py`
- Modify: `src/tianshu/cli/client.py`
- Modify: `src/tianshu/cli/commands/edict.py`
- Modify: `web/src/api/edicts.ts`
- Modify: `web/src/pages/EdictCreatePage.tsx`
- Test: `web/src/api/edicts.test.ts`
- Test: `web/src/pages/EdictCreatePage.test.tsx`
- Test: `tests/integration/test_outbox_recovery.py`
- Test: `tests/integration/test_outbox_consumer_idempotency.py`
- Modify: `tests/test_event_bus.py`
- Test: `tests/integration/test_event_history_consumer.py`
- Test: `tests/gateway/test_edict_idempotency_contract.py`
- Test: existing gateway/tool/bot/MCP/CLI suites

### 2.1 RED · Specify ingress and redelivery behavior

- [ ] Parameterize API, CLI, Web, MCP, Feishu/Telegram bridge, `submit_edict`, scheduled submission, and amend/retry adapters. Assert each creates one `SubmitEdictCommand` and calls `EdictApplicationService.submit()` once.
- [ ] Require a stable idempotency key at every adapter: HTTP `Idempotency-Key`, CLI/Web generated client key, MCP call key, Bot source message/update ID, tool invocation ID, scheduler job/fire ID, and amend decision ID. Reject HTTP header/body mismatch with 422.
- [ ] Assert HTTP returns 202 for a new submit, 200 plus `metadata.deduplicated=true` for same-hash retry, and 409 `idempotency_conflict` for different hash.
- [ ] Add crash tests for commit-before-dispatch and handler-effect-before-consumption-ack. Restart a new dispatcher over the same file DB and assert eventual handling with one effective business transition.
- [ ] Assert duplicate dispatch stores one legacy timeline row with the original envelope event ID/time and skips only consumers already recorded as successful.
- [ ] Assert poisoned events back off, retain redacted failure details, and reach outbox DLQ only after `max_attempts`.

Run:

```bash
uv run --frozen pytest \
  tests/integration/test_outbox_recovery.py \
  tests/integration/test_outbox_consumer_idempotency.py \
  tests/integration/test_event_history_consumer.py \
  tests/test_event_bus.py \
  tests/gateway/test_edict_idempotency_contract.py \
  tests/architecture/test_durable_write_boundaries.py -q
(cd web && npm run test -- --run src/api/edicts.test.ts src/pages/EdictCreatePage.test.tsx)
```

Expected RED: ingress still writes directly and `EventBus.fire()` is process-local.

### 2.2 GREEN · Dispatch persisted envelopes only

- [ ] Add stable consumer registration plus `EventBus.dispatch()` reports. Make `OutboxDispatcher` claim with a lease, skip recorded successes, dispatch unseen/failed consumers, record each named consumer’s completion, then mark published only when all current consumers succeeded. A restart reclaims expired rows.
- [ ] Register `EventHistoryConsumer` and preserve the envelope event ID/timestamp in the existing `events` table idempotently so current timeline and approval-card lookups keep working.
- [ ] Give scheduler/executor/notifier handler registrations stable consumer names. A duplicate `event_id + consumer_name` returns the saved consumption result instead of repeating its business transition.
- [ ] Wire one outbox worker in lifespan startup; stop it before storage closes. A worker exception affects readiness and retry state but does not kill the app loop silently.
- [ ] Replace all top-level ingress writes with the application service. Keep `submit_new_edict()` as a deprecated compatibility wrapper that delegates to the service; it no longer owns persistence.
- [ ] Make CLI expose `--idempotency-key` with one generated ULID per command when omitted. Make `EdictCreatePage` retain one `crypto.randomUUID()` for the current unchanged form submission and pass it to `createEdict(body, key)` as the header; a transport retry reuses it, while editing the form after a terminal response creates a new key. Bot and scheduler derive keys from their durable source IDs rather than random values.

### 2.3 REFACTOR · Close G2-A architecture and regression Gate

- [ ] Remove superseded pre-insert idempotency checks and direct `EventBus.fire()` from submission paths.
- [ ] Run all submission adapters, outbox faults, migrations 1–3, architecture, gateway, and type/lint checks.

Run the **G2-A automatic Gate**:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v3.py \
  tests/application \
  tests/integration/test_g1_g2_handoff.py \
  tests/integration/test_edict_idempotency.py \
  tests/integration/test_outbox_recovery.py \
  tests/integration/test_outbox_consumer_idempotency.py \
  tests/integration/test_event_history_consumer.py \
  tests/test_event_bus.py \
  tests/gateway/test_edict_idempotency_contract.py \
  tests/architecture/test_durable_write_boundaries.py \
  tests/gateway/test_edicts_api.py \
  tests/gateway/feishu/test_edict_bridge.py \
  tests/gateway/telegram/test_edict_bridge_channel.py \
  tests/tools/test_submit_edict.py \
  tests/tools/test_schedule_edict.py \
  tests/test_cli_edict.py -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
(cd web && npm run typecheck && npm run test -- --run src/api/edicts.test.ts src/pages/EdictCreatePage.test.tsx)
git diff --check
```

Commit after G2-A Gate: `feat: unify edict ingress through durable outbox`

---

## Increment 3: Persistent DecisionRequest and versioned RunState

**Slice:** G2-B

**Files:**

- Create: `src/tianshu/governance/__init__.py`
- Create: `src/tianshu/governance/decision_service.py`
- Create: `src/tianshu/models/decision.py`
- Create: `src/tianshu/models/run_state.py`
- Create: `src/tianshu/storage/decision_repo.py`
- Create: `src/tianshu/storage/run_state_repo.py`
- Create: `src/tianshu/gateway/decisions_api.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/app.py`
- Test: `tests/storage/test_durable_schema_v4.py`
- Test: `tests/governance/test_decision_models.py`
- Test: `tests/governance/test_decision_cas.py`
- Test: `tests/governance/test_run_state_cas.py`
- Test: `tests/gateway/test_decisions_api.py`

### 3.1 RED · Freeze decision, identity, expiry, and CAS semantics

- [ ] Test strict immutable models, payload hash validation, non-blank reason, UTC expiry, and exhaustive enums.
- [ ] Test request persistence, one winning concurrent resolution, stale version conflict, expiry-vs-resolution race, cancelled request, late result rejection, and resolution/outbox atomicity.
- [ ] Assert the actor in a malicious request body is ignored/rejected and the stored actor comes from G1 `AuthContext`.
- [ ] Test RunState create/read/CAS transition, unknown schema rejection, and round-trip of every Agent/OuterLoop continuation field.
- [ ] Add API contracts: `GET /api/decisions`, `GET /api/decisions/{id}`, and `POST /api/decisions/{id}/resolve`; map not found to 404 and version/expiry/status conflicts to 409.

Run:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v4.py \
  tests/governance/test_decision_models.py \
  tests/governance/test_decision_cas.py \
  tests/governance/test_run_state_cas.py \
  tests/gateway/test_decisions_api.py -q
```

Expected RED: v4 schema, models, services, CAS, and routes do not exist.

### 3.2 GREEN · Persist decisions and state transitions

- [ ] Implement v4 and repositories without exposing raw connection access to gateway code.
- [ ] Implement request/resolve/expire transactions and outbox events; use injected Clock for deterministic boundary tests.
- [ ] Implement RunState discriminated-union serialization and expected-version writes.
- [ ] Register `decisions_router`; use the G1 route auth dependency and correlation ID. Do not accept actor, principal, or source IP from JSON.

### 3.3 REFACTOR · Verify migration and boundary quality

Run:

```bash
uv run --frozen pytest tests/storage/test_durable_schema_v4.py tests/governance tests/gateway/test_decisions_api.py -q
uv run --frozen ruff check src/tianshu/governance src/tianshu/models/decision.py src/tianshu/models/run_state.py src/tianshu/storage/decision_repo.py src/tianshu/storage/run_state_repo.py src/tianshu/gateway/decisions_api.py tests/governance
uv run --frozen mypy src/tianshu/governance src/tianshu/models/decision.py src/tianshu/models/run_state.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: persist governance decisions and run state`

---

## Increment 4: Route all decision adapters through durable governance

**Slice:** G2-B

**Files:**

- Modify: `src/tianshu/executor/approvals.py`
- Modify: `src/tianshu/executor/policy_hook.py`
- Modify: `src/tianshu/gateway/execution_api.py`
- Modify: `src/tianshu/gateway/edicts_api.py`
- Modify: `src/tianshu/gateway/core/approval.py`
- Modify: `src/tianshu/gateway/feishu/approval_card.py`
- Modify: `src/tianshu/gateway/telegram/approval_kb.py`
- Modify: `src/tianshu/bootstrap/wiring_executor.py`
- Test: `tests/integration/test_decision_restart_recovery.py`
- Test: `tests/integration/test_decision_adapter_compatibility.py`
- Test: `tests/gateway/telegram/test_approval_kb.py`
- Test: existing approval/Bot/policy tests

### 4.1 RED · Kill the process while a decision is pending

- [ ] Start a governed request in a subprocess, wait until `decision_requests.status='pending'` and RunState is `waiting_decision`, kill it, restart app services, list the decision, resolve it, and assert one `decision.resolved` outbox event.
- [ ] Test tool, outer-loop, plan review, and governed apply kinds through the same service.
- [ ] Assert legacy `/approvals/*`, `/decrees`, outer-loop endpoints, Feishu cards, Telegram buttons, and text commands are compatibility adapters over `decision_request_id`; they no longer depend on an in-memory `asyncio.Event` being alive.
- [ ] Test two Bot/API resolvers racing and confirm only one receives success.

Run:

```bash
uv run --frozen pytest \
  tests/integration/test_decision_restart_recovery.py \
  tests/integration/test_decision_adapter_compatibility.py \
  tests/gateway/feishu/test_approval_card.py \
  tests/gateway/telegram/test_approval_kb.py \
  tests/gateway/feishu/test_approval_commands.py -q
```

Expected RED: pending decisions disappear with the in-memory waiter.

### 4.2 GREEN · Make old surfaces durable adapters

- [ ] Replace ApprovalManager’s pending/result dictionaries with DecisionService queries. Preserve public method names temporarily, but return/accept `decision_request_id` and delegate persistence.
- [ ] Persist the display payload before notifying cards/WS. Cards use the decision ID, not a Memorial ID alias.
- [ ] Convert legacy Decree actions into DecisionResolution commands and derive actor from request/Bot auth context. Retain Decree rows only as compatibility projections after the durable resolution commits.
- [ ] On startup, `list_pending()` discovers unresolved decisions; no waiter recreation is required.
- [ ] Keep restart recovery at “decision persists and emits resume work.” Actual Agent/OuterLoop continuation execution lands in Increment 7.

### 4.3 REFACTOR · Remove false in-memory authority

- [ ] Delete `_pending`, `_results`, `_outer_loop_pending`, and `_outer_loop_results` after compatibility tests are GREEN.
- [ ] Update API docstrings and responses so they no longer claim in-memory authority.

Run:

```bash
uv run --frozen pytest tests/integration/test_decision_restart_recovery.py tests/integration/test_decision_adapter_compatibility.py tests/gateway/feishu tests/gateway/telegram tests/test_pause_resume_api.py -q
uv run --frozen ruff check src/tianshu/executor/approvals.py src/tianshu/executor/policy_hook.py src/tianshu/gateway tests/integration/test_decision_restart_recovery.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: route all decisions through durable governance`

---

## Increment 5: Leased execution attempts, fencing, reconciliation, and DLQ

**Slice:** G2-B

**Files:**

- Create: `src/tianshu/application/dispatcher.py`
- Create: `src/tianshu/storage/attempt_ledger.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/executor/worker.py`
- Modify: `src/tianshu/executor/executor.py`
- Modify: `src/tianshu/scheduler/scheduler.py`
- Modify: `src/tianshu/storage/scheduler_repo.py`
- Modify: `src/tianshu/bootstrap/wiring_scheduler.py`
- Test: `tests/storage/test_durable_schema_v5.py`
- Test: `tests/integration/test_claim_lease_recovery.py`
- Test: `tests/integration/test_scheduler_reconcile_dlq.py`
- Test: `tests/executor/test_attempt_fencing.py`

### 5.1 RED · Define claim and stale-owner races

- [ ] Test atomic multi-worker claims, lease heartbeat, owner/version fencing, lease expiry, bounded retry with incremented `attempt_no`, and DLQ transition at `max_attempts`.
- [ ] Pause worker A after expiry, let worker B reclaim, then resume A; assert A cannot persist state, receipt, or terminal outcome.
- [ ] Kill a claimed worker and prove a fresh dispatcher recovers after lease expiry without scheduler resume storms.
- [ ] Assert reconciliation ignores intentionally paused/waiting-decision runs and active DAG work owned by a live lease.

Run:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v5.py \
  tests/integration/test_claim_lease_recovery.py \
  tests/integration/test_scheduler_reconcile_dlq.py \
  tests/executor/test_attempt_fencing.py -q
```

Expected RED: current heartbeat/orphan sweep has no claim token or fencing version.

### 5.2 GREEN · Make attempts the execution authority

- [ ] Implement v5 attempt repository with claim via `BEGIN IMMEDIATE`, deterministic ordering by `available_at, created_at`, lease duration configuration, and version CAS.
- [ ] Create an initial claimable attempt from the `edict.submitted` consumer. Worker execution requires an owned attempt; it cannot infer authority from Memorial status alone.
- [ ] Replace orphan resume emission with `reconcile_expired()`. Preserve existing scheduler jobs, but make execution retry/DLQ state come from the attempt ledger.
- [ ] Heartbeat the attempt and RunState; shutdown stops new claims, waits bounded time for active work, and releases or lets leases expire safely.

### 5.3 REFACTOR · Separate scheduling time from execution ownership

- [ ] Keep scheduler responsible for “when to submit”; keep DurableDispatcher responsible for “who may execute.” Remove duplicated orphan state transitions.

Run:

```bash
uv run --frozen pytest tests/storage/test_durable_schema_v5.py tests/integration/test_claim_lease_recovery.py tests/integration/test_scheduler_reconcile_dlq.py tests/executor/test_attempt_fencing.py tests/test_scheduler.py tests/test_edict_lifecycle.py -q
uv run --frozen ruff check src/tianshu/application/dispatcher.py src/tianshu/storage/attempt_ledger.py src/tianshu/executor src/tianshu/scheduler tests/integration/test_claim_lease_recovery.py
uv run --frozen mypy src/tianshu/application/dispatcher.py src/tianshu/storage/attempt_ledger.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: add leased durable execution dispatch`

---

## Increment 6: Governed side-effect intent and receipt journal

**Slice:** G2-B

**Files:**

- Create: `src/tianshu/executor/side_effects.py`
- Create: `src/tianshu/storage/side_effect_journal.py`
- Modify: G1 `src/tianshu/executor/execution_gateway.py`
- Modify: `src/tianshu/executor/agent.py`
- Modify: `src/tianshu/executor/policy_hook.py`
- Modify: `src/tianshu/executor/worker.py`
- Modify: `src/tianshu/storage/facade.py`
- Test: `tests/executor/test_side_effect_contract.py`
- Test: `tests/integration/test_side_effect_idempotency.py`
- Test: `tests/integration/test_side_effect_uncertain.py`
- Test: `tests/compat/test_side_effect_boundaries.py`

### 6.1 RED · Inject crashes around intent, effect, and receipt

- [ ] Build fake `provider_idempotent`, `receipt_lookup`, `workspace_only`, `untracked_external`, and `opaque_cli` adapters.
- [ ] Inject failure before intent, after intent/before effect, after effect/before receipt, after receipt/before RunState cursor, and after cursor/before attempt ack.
- [ ] Assert supported provider boundaries retry with the same key or look up the receipt and obtain one effective provider result.
- [ ] Assert untracked external post-send ambiguity becomes `uncertain`, creates one decision, and never auto-retries.
- [ ] Assert opaque CLI records only process start/exit/output hash and makes no assertion about file/network/tool actions inside the process.

Run:

```bash
uv run --frozen pytest \
  tests/executor/test_side_effect_contract.py \
  tests/integration/test_side_effect_idempotency.py \
  tests/integration/test_side_effect_uncertain.py \
  tests/compat/test_side_effect_boundaries.py -q
```

Expected RED: side effects have no durable intent or receipt cursor.

### 6.2 GREEN · Journal only what the boundary can prove

- [ ] Implement intent insert before adapter invocation and fenced status transitions through `executing`, `receipted`, `uncertain`, or `failed`.
- [ ] Derive a stable effect idempotency key from Memorial ID, sequence, boundary, operation, and canonical intent hash; never from process-local object identity.
- [ ] Resume by reading the journal at `RunState.side_effect_cursor`: reuse a receipt, look it up, retry the same key, or stop for a decision according to semantics.
- [ ] Register verified Native managed operations in a compatibility matrix. Keep Keqing Claude/Codex as `opaque_cli` regardless of successful process exit.
- [ ] Redact request/error/receipt fields before persistence and include only allowlisted provider receipt metadata.

### 6.3 REFACTOR · Encode truth in capabilities

- [ ] Update G1 capability manifests so receipt/idempotency states reflect tested adapters. Mandatory receipt capability fails closed for unsupported executors.

Run:

```bash
uv run --frozen pytest tests/executor/test_side_effect_contract.py tests/integration/test_side_effect_idempotency.py tests/integration/test_side_effect_uncertain.py tests/compat -q
uv run --frozen ruff check src/tianshu/executor/side_effects.py src/tianshu/storage/side_effect_journal.py tests/executor/test_side_effect_contract.py tests/integration/test_side_effect_idempotency.py
uv run --frozen mypy src/tianshu/executor/side_effects.py src/tianshu/storage/side_effect_journal.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: journal governed side effects`

---

## Increment 7: Resume Agent and outer-loop work from durable continuations

**Slice:** G2-B

**Files:**

- Create: `src/tianshu/executor/continuation.py`
- Modify: `src/tianshu/executor/agent.py`
- Modify: `src/tianshu/executor/policy_hook.py`
- Modify: `src/tianshu/executor/orchestrator/loop.py`
- Modify: `src/tianshu/executor/orchestrator/state.py`
- Modify: `src/tianshu/executor/orchestrator/persistence.py`
- Modify: `src/tianshu/executor/worker.py`
- Modify: `src/tianshu/executor/checkpoint.py`
- Test: `tests/integration/test_agent_continuation_restart.py`
- Test: `tests/integration/test_outer_loop_restart_recovery.py`
- Test: `tests/integration/test_pending_tool_restart.py`
- Test: `tests/test_outer_loop_resume.py`

### 7.1 RED · Persist safe points rather than await stacks

- [ ] For a dangerous tool call, assert messages and the exact tool proposal are persisted before requesting a decision or side effect. Kill after persistence, restart, resolve, and assert the already-approved proposal executes without another LLM call.
- [ ] Inject restart at L0, L1, L2, L3, paused, waiting-decision, and post-decision/pre-resume-enqueue safe points.
- [ ] Assert outer-loop restoration includes history summaries, best output, critic feedback, steer, level counters, total usage/cost, checkpoint reference, decision ID, and side-effect cursor.
- [ ] Assert a completed/failed continuation cannot be resumed and stale RunState writers lose CAS.

Run:

```bash
uv run --frozen pytest \
  tests/integration/test_agent_continuation_restart.py \
  tests/integration/test_outer_loop_restart_recovery.py \
  tests/integration/test_pending_tool_restart.py \
  tests/test_outer_loop_resume.py -q
```

Expected RED: `_state_from_dict()` discards history and pending tool state, and approval depends on a live coroutine.

### 7.2 GREEN · Suspend, exit, and resume as new attempts

- [ ] Introduce a structured `RunSuspended` outcome. Waiting for a decision persists RunState and marks the current attempt `suspended`; it does not consume the failure retry budget and does not keep an application coroutine parked for durability.
- [ ] Consume `decision.resolved` to create a claimable continuation attempt in the same idempotent consumer transaction.
- [ ] On resume, load RunState, validate schema and decision binding, apply the resolution to the stored proposal, then continue from the safe point.
- [ ] Replace legacy outer-loop checkpoint JSON as the authority with RunState; keep a one-way legacy adapter that imports existing checkpoint data once.
- [ ] Keep pause durable: pause transitions RunState and ends/relinquishes work at the next safe point; resume creates one new attempt.

### 7.3 REFACTOR · Remove lossy restore paths

- [ ] Remove `history=()` restore behavior and any polling loop whose only purpose was keeping a process alive for approval.

Run:

```bash
uv run --frozen pytest tests/integration/test_agent_continuation_restart.py tests/integration/test_outer_loop_restart_recovery.py tests/integration/test_pending_tool_restart.py tests/test_outer_loop_resume.py tests/test_orchestrator_audit.py tests/test_orchestrator_lifecycle_routing.py -q
uv run --frozen ruff check src/tianshu/executor/continuation.py src/tianshu/executor/agent.py src/tianshu/executor/orchestrator tests/integration/test_agent_continuation_restart.py
uv run --frozen mypy src/tianshu/executor/continuation.py src/tianshu/models/run_state.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: resume governed runs from durable continuations`

---

## Increment 8: Planner revisions, quality metrics, and replan evidence

**Slice:** G2-B

**Files:**

- Create: `src/tianshu/evals/planner_quality.py`
- Create: `src/tianshu/storage/plan_repo.py`
- Modify: `src/tianshu/planner/planner.py`
- Modify: `src/tianshu/executor/dag_scheduler.py`
- Modify: `src/tianshu/executor/orchestrator/loop.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/storage/migrations.py`
- Test: `tests/storage/test_durable_schema_v6.py`
- Test: `tests/planner/test_plan_amend_metrics.py`
- Test: `tests/integration/test_replan_evidence.py`
- Test: `tests/planner/test_active_dag_replan_guard.py`

### 8.1 RED · Define revision and safe-replan evidence

- [ ] Test initial revision 1, amendment/replan monotonic revisions, canonical plan hash, previous hash, stable structural diff, reason/source, estimate/actual variance, failure class, final acceptance result, expected-version close, and closed-row immutability.
- [ ] Assert replan before dispatch and at an outer-loop safe point succeeds; replan of an actively executing DAG is rejected and records the blocked reason.
- [ ] Inject restart after plan revision persistence but before dispatch and assert the same revision is reused rather than duplicated.

Run:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v6.py \
  tests/planner/test_plan_amend_metrics.py \
  tests/integration/test_replan_evidence.py \
  tests/planner/test_active_dag_replan_guard.py -q
```

Expected RED: v6 objects and versioned revision/diff/quality records do not exist.

### 8.2 GREEN · Persist plan facts without a dynamic DAG runtime

- [ ] Add the complete immutable v6 migration exactly as specified in this plan, including plan, artifact, evidence, audit-context/index, and trigger objects; verify all objects in `tests/storage/test_durable_schema_v6.py`. Landing the whole schema here is intentional: a migration version/checksum is never reopened when the G2-C services begin using the reserved tables.
- [ ] Implement the plan repository with `(edict_id, revision)` uniqueness; G2-C services remain unwired until Increments 9–10.
- [ ] Persist each plan before dispatch. Amend/replan derives `previous_hash` and structural diff from the prior closed revision.
- [ ] Update actual usage/time/cost and failure/acceptance evidence at terminal safe points with expected-version CAS, then close the revision. Closed revisions reject update/delete; a superseded pre-dispatch plan closes with its replan reason and available actuals.
- [ ] Expose plan revisions to the later EvidenceService; do not add mutable in-flight DAG topology.

### 8.3 REFACTOR · Close G2-B Gate

Run the **G2-B automatic Gate**:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v4.py \
  tests/storage/test_durable_schema_v5.py \
  tests/storage/test_durable_schema_v6.py \
  tests/governance \
  tests/integration/test_decision_restart_recovery.py \
  tests/integration/test_decision_adapter_compatibility.py \
  tests/integration/test_claim_lease_recovery.py \
  tests/integration/test_scheduler_reconcile_dlq.py \
  tests/integration/test_side_effect_idempotency.py \
  tests/integration/test_side_effect_uncertain.py \
  tests/integration/test_agent_continuation_restart.py \
  tests/integration/test_outer_loop_restart_recovery.py \
  tests/integration/test_pending_tool_restart.py \
  tests/integration/test_replan_evidence.py \
  tests/planner \
  tests/executor \
  tests/test_scheduler.py \
  tests/test_outer_loop_resume.py -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
git diff --check
```

Commit after G2-B Gate: `feat: record planner revisions and replan evidence`

---

## Increment 9: Content-addressed ArtifactStore and immutable Evidence Bundle v1

**Slice:** G2-C

**Files:**

- Create: `src/tianshu/evidence/__init__.py`
- Create: `src/tianshu/evidence/models.py`
- Create: `src/tianshu/evidence/artifact_store.py`
- Create: `src/tianshu/evidence/service.py`
- Create: `src/tianshu/application/evidence.py`
- Create: `src/tianshu/storage/artifact_repo.py`
- Create: `src/tianshu/storage/evidence_repo.py`
- Create: `src/tianshu/gateway/evidence_api.py`
- Create: `docs/reference/evidence-bundle-v1.schema.json`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/auditor/auditor.py`
- Modify: `src/tianshu/app.py`
- Test: `tests/evidence/test_artifact_store.py`
- Test: `tests/evidence/test_bundle.py`
- Test: `tests/evidence/test_canonical_hash.py`
- Test: `tests/evidence/test_close_snapshot_immutable.py`
- Test: `tests/integration/test_independent_audit_evidence.py`
- Test: `tests/gateway/test_evidence_api.py`

### 9.1 RED · Define storage, schema, closure, and replay

- [ ] Stream identical and different artifacts; test digest layout, atomic rename, dedupe, quota rejection, MIME/size/producer/environment metadata, redaction policy, and orphan temporary-file cleanup.
- [ ] Validate a complete bundle against the checked-in JSON Schema; reject missing mandatory evidence and unknown fields.
- [ ] Assert canonical hash stability across mapping order/process restart and hash change for any meaningful field.
- [ ] Close once, retry close idempotently, then prove repository update/delete and direct SQL update/delete are rejected for the closed row.
- [ ] Assert independent auditor cannot pass when effective-contract mandatory checks/artifacts are absent.
- [ ] Assert download returns the exact canonical JSON bytes. Replay must create a new governed Edict via application service and must not directly execute the stored reproduction command.

Run:

```bash
uv run --frozen pytest \
  tests/evidence/test_artifact_store.py \
  tests/evidence/test_bundle.py \
  tests/evidence/test_canonical_hash.py \
  tests/evidence/test_close_snapshot_immutable.py \
  tests/integration/test_independent_audit_evidence.py \
  tests/gateway/test_evidence_api.py -q
```

Expected RED: the reserved v6 tables exist, but Evidence/Artifact services, schema document, closure behavior, and routes do not exist.

### 9.2 GREEN · Build and close one canonical snapshot

- [ ] Add `artifact_dir`, per-artifact maximum bytes, and total quota settings with positive validation and secure directory creation.
- [ ] Use the already-applied v6 artifact/evidence tables and immutable triggers; do not alter v6 SQL or checksum in this increment.
- [ ] Store bytes first via temporary file/fsync/rename, then metadata. An orphan sweeper removes stale temp files and unreferenced bytes only after a safety age; referenced artifacts are never deleted by the sweeper.
- [ ] Build bundle fields from authoritative G1/G2 repositories, not page payloads. Include requested/effective contract and hashes, executor manifest hash, plan revisions, side-effect receipts/uncertainties, changes, checks, decisions, cost, environment, and independent auditor conclusion. `EvidenceApplicationService.close_run()` coordinates the auditor and lower-level EvidenceService without creating sibling-package imports.
- [ ] `close()` validates required evidence, schema-validates the canonical model, computes hash excluding itself, and performs one version-CAS update from draft to closed.
- [ ] Register APIs: `GET /api/edicts/{edict_id}/evidence`, `GET /api/evidence/{bundle_id}/download`, and `POST /api/evidence/{bundle_id}/replay`; the replay route calls `EvidenceApplicationService`.

### 9.3 REFACTOR · Prove no upward storage dependency

Run:

```bash
uv run --frozen pytest tests/evidence tests/integration/test_independent_audit_evidence.py tests/gateway/test_evidence_api.py -q
uv run --frozen ruff check src/tianshu/evidence src/tianshu/application/evidence.py src/tianshu/storage/artifact_repo.py src/tianshu/storage/evidence_repo.py src/tianshu/gateway/evidence_api.py tests/evidence
uv run --frozen mypy src/tianshu/evidence src/tianshu/application/evidence.py src/tianshu/storage/artifact_repo.py src/tianshu/storage/evidence_repo.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: add canonical evidence bundles`

---

## Increment 10: Append-only system audit, correlated OTel, and fail-closed readiness

**Slice:** G2-C

**Files:**

- Modify: G1 `src/tianshu/auditor/system_log.py`
- Modify: G1 `src/tianshu/storage/system_audit_repo.py`
- Create: `src/tianshu/gateway/readiness_api.py`
- Create: `src/tianshu/application/readiness.py`
- Modify: `src/tianshu/observability.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/app.py`
- Modify: `src/tianshu/gateway/estop_api.py`
- Modify: `src/tianshu/gateway/audit_api.py`
- Modify: `src/tianshu/gateway/credentials_api.py`
- Modify: `src/tianshu/gateway/mcp_api.py`
- Modify: `src/tianshu/gateway/skills_api.py`
- Modify: `src/tianshu/gateway/universes_api.py`
- Modify: `src/tianshu/secrets/store.py`
- Modify: `src/tianshu/tools/policy_store.py`
- Modify: `src/tianshu/tools/mcp/config.py`
- Modify: `src/tianshu/tools/mcp/manager.py`
- Modify: `src/tianshu/skills/loader.py`
- Modify: `src/tianshu/skills/metrics.py`
- Modify: `src/tianshu/universe/manager.py`
- Modify: `src/tianshu/universe/evolver.py`
- Modify: `src/tianshu/universe/deployer.py`
- Modify: `src/tianshu/universe/unlock.py`
- Test: `tests/audit/test_system_audit_log.py`
- Test: `tests/audit/test_system_audit_append_only.py`
- Test: `tests/observability/test_trace_correlation.py`
- Test: `tests/observability/test_tracer_shutdown.py`
- Test: `tests/gateway/test_readiness.py`
- Test: `tests/integration/test_durable_lifecycle.py`

### 10.1 RED · Define audit, correlation, redaction, and readiness failure states

- [ ] Test append/list only, row-hash chain, update/delete trigger rejection, actor/IP/time/reason requirements, and payload redaction/hash.
- [ ] Test persistent audit events for estop engage/resume, decision request/resolve/deny, policy changes, skill changes, MCP changes, credential changes, and evolution mutation/promotion/rollback.
- [ ] Capture a fake exporter trace and assert correlation across Edict -> planner -> LLM -> tool -> policy -> check -> channel using correlation/Edict/Memorial/attempt/effect IDs without prompt, secret, raw args, or output content.
- [ ] Assert shutdown force-flushes and shuts down the provider before storage/process teardown.
- [ ] Assert startup failure unwinds started workers, repeated `stop()` is safe, shutdown rejects new claims, and the configured drain timeout leaves recoverable leases rather than hanging.
- [ ] Test `/health/live` stays a simple public liveness response. Test `/health/ready` returns 503 with structured component reasons for DB/migration error, outbox backlog/age, stale dispatcher heartbeat, unreconciled expired lease, inaccessible artifact/evidence store, or stale delivery worker.

Run:

```bash
uv run --frozen pytest \
  tests/audit/test_system_audit_log.py \
  tests/audit/test_system_audit_append_only.py \
  tests/observability/test_trace_correlation.py \
  tests/observability/test_tracer_shutdown.py \
  tests/gateway/test_readiness.py \
  tests/integration/test_durable_lifecycle.py -q
```

Expected RED: G1 audit is narrow, tracing lacks full correlation/flush, and readiness is only `{"status":"ok"}`.

### 10.2 GREEN · Make operational truth queryable

- [ ] Use the audit context columns/indexes/triggers already applied by the complete v6 migration; implement repository hash chaining and allowlisted event schemas without changing v6.
- [ ] Route mutation services through `SystemAuditLog`. Append a redacted intent before every governance/security mutation; if that append fails, do not mutate. Append a success/failure outcome afterward. Database-backed mutations put state change and outcome audit in one Unit of Work where possible; file/provider mutations retain the durable intent if the process dies after the external change and degrade readiness until reconciliation instead of claiming an unaudited success.
- [ ] Add span helper parameters for correlation, Memorial, attempt, decision, effect, and channel IDs. Centralize redaction and attribute allowlists.
- [ ] Store the tracer provider and implement `shutdown_tracing(timeout_millis)` that calls `force_flush` then `shutdown`; invoke it during lifespan teardown before storage closes.
- [ ] Add injected component heartbeats and readiness thresholds: maximum pending outbox rows/oldest age, dispatcher/reconciler/delivery heartbeat age, and artifact write/read probe. Return HTTP 503 when any mandatory check fails.
- [ ] Preserve `/health` as a compatibility alias for liveness while G1 `/health/live` remains canonical.

### 10.3 REFACTOR · Verify sensitive content cannot enter evidence paths

Run:

```bash
uv run --frozen pytest tests/audit tests/observability tests/gateway/test_readiness.py tests/integration/test_durable_lifecycle.py tests/gateway/test_auth.py tests/gateway/test_mcp_auth.py -q
uv run --frozen ruff check src/tianshu/auditor src/tianshu/application/readiness.py src/tianshu/gateway/readiness_api.py src/tianshu/observability.py tests/audit tests/observability
uv run --frozen mypy src/tianshu/auditor src/tianshu/application/readiness.py src/tianshu/observability.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: make governance audit and readiness durable`

---

## Increment 11: Durable notification delivery, explicit semantics, and deadlines

**Slice:** G2-C

**Files:**

- Create: `src/tianshu/notifier/channel_adapter.py`
- Create: `src/tianshu/notifier/delivery_outbox.py`
- Create: `src/tianshu/storage/notification_delivery_repo.py`
- Create: `src/tianshu/gateway/notifications_api.py`
- Modify: `src/tianshu/notifier/notifier.py`
- Modify: `src/tianshu/notifier/channel_registry.py`
- Modify: `src/tianshu/notifier/channels/base.py`
- Modify: `src/tianshu/gateway/bot_manager.py`
- Modify: `src/tianshu/bootstrap/wiring_channels.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/app.py`
- Test: `tests/storage/test_durable_schema_v7.py`
- Test: `tests/notifier/test_delivery_recovery.py`
- Test: `tests/notifier/test_delivery_semantics.py`
- Test: `tests/notifier/test_silent_deadline.py`
- Test: `tests/notifier/test_channel_plugin.py`
- Test: `tests/gateway/test_notifications_api.py`

### 11.1 RED · Crash before/after send under all delivery modes

- [ ] Test claim/lease/backoff/retry/DLQ and stale-owner fencing.
- [ ] For `provider_idempotent`, crash after provider success/before receipt save and prove lookup or same-key retry gives one effective provider result.
- [ ] For `at_least_once`, crash at the same point and assert retry plus `outcome_may_duplicate=true`.
- [ ] For `at_most_once`, mark attempted before send; crash after the attempt and assert `uncertain`, no automatic retry, and `outcome_may_be_lost=true`.
- [ ] Queue a normal notification during quiet hours, advance injected Clock past its computed `available_at` without producing any later event, and assert the worker sends it.
- [ ] Register a mock adapter via ChannelRegistry and assert no new conditional branch in BotManager.
- [ ] Test `GET /api/notifications/deliveries` and authorized `POST /api/notifications/deliveries/{id}/retry`; manual retry creates audit evidence and obeys delivery mode.

Run:

```bash
uv run --frozen pytest \
  tests/storage/test_durable_schema_v7.py \
  tests/notifier/test_delivery_recovery.py \
  tests/notifier/test_delivery_semantics.py \
  tests/notifier/test_silent_deadline.py \
  tests/notifier/test_channel_plugin.py \
  tests/gateway/test_notifications_api.py -q
```

Expected RED: current notifier logs failures, deletes pending rows regardless of success, and flushes quiet notifications only when later activity occurs.

### 11.2 GREEN · Persist every channel attempt before network I/O

- [ ] Implement v7 and legacy pending-row conversion. Persist one delivery per event/channel with redacted payload/rendering and a stable key.
- [ ] Add `notify_timezone` setting, validate it with `ZoneInfo`, and compute quiet-hour `available_at` in that zone before storing UTC. `start == end` remains “quiet hours disabled.”
- [ ] Make Notifier enqueue external deliveries; WebSocket remains best-effort live UI and is not misrepresented as durable notification delivery.
- [ ] Implement DeliveryWorker claim/send/receipt/backoff/DLQ and mode-specific ambiguous outcomes.
- [ ] Make ChannelRegistry register adapters by protocol/capability. Adapt existing Feishu/DingTalk/email channels with explicit declared modes; default unverified providers to `at_least_once`.
- [ ] Start/stop the worker in lifespan and feed its heartbeat/readiness.

### 11.3 REFACTOR · Remove lazy flush and core branching

- [ ] Remove `_flush_pending()` dependence on a later notification and stop deleting legacy pending rows on failed sends.
- [ ] Keep BotManager unaware of adapter types; registration is the only extension point.

Run:

```bash
uv run --frozen pytest tests/notifier tests/gateway/test_notifications_api.py tests/test_notifier.py tests/test_notifier_extended.py -q
uv run --frozen ruff check src/tianshu/notifier src/tianshu/storage/notification_delivery_repo.py src/tianshu/gateway/notifications_api.py tests/notifier
uv run --frozen mypy src/tianshu/notifier src/tianshu/storage/notification_delivery_repo.py
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: deliver notifications through a durable outbox`

---

## Increment 12: Fault-injection closure, truth documentation, and G2 Gate report

**Slice:** G2-C

**Files:**

- Create: `tests/fault_injection/harness.py`
- Create: `tests/fault_injection/test_g2_crash_matrix.py`
- Create: `tests/fault_injection/test_g2_process_restart.py`
- Create: `docs/launch/gates/g2-durable-governance-evidence.md`
- Create: `docs/reference/side-effect-compatibility.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/launch/checklist.md`
- Modify: `docs/ops/threat-model.md`
- Modify: `README.md` only for verified G2 claims and links
- Modify: `.env.example`
- Modify: `tests/test_public_docs_truth.py`
- Create: `tests/test_g2_gate_report.py`

### 12.1 RED · Run the complete named failure matrix

- [ ] Build a subprocess harness around a real temporary SQLite file, fake managed providers/channels, an injected Clock, and observable barriers. Use parent-process kill/restart to test process loss; do not simulate every crash with an exception inside one transaction.
- [ ] Encode F01–F25 from the matrix below as named parametrized cases with durable DB/provider/artifact oracles.
- [ ] Add a docs truth test that rejects unqualified “exactly once”, any zero-duplicate claim attached to `untracked_external`/`opaque_cli`, and any claim that Keqing CLI is managed.

Run:

```bash
uv run --frozen pytest tests/fault_injection tests/test_public_docs_truth.py tests/test_g2_gate_report.py -q
```

Expected RED: the integrated crash harness and complete evidence report do not yet exist, and any remaining race is now reproducible by its fault ID.

### 12.2 GREEN · Fix faults, then generate evidence from real Gate output

- [ ] Fix production behavior one fault at a time without weakening its oracle.
- [ ] Generate the G2 Gate report with commit SHA, Python/SQLite/uv versions, migration versions/checksums, each command and outcome, fault IDs passed, boundary compatibility table, Evidence Bundle digest, and known exclusions.
- [ ] Update capability matrix only after the corresponding automated evidence passes. Use “at-least-once delivery + one effective result on listed managed boundaries,” not an unconditional once-only claim.
- [ ] Document the host/DB-admin limitation, single-node SQLite scope, provider-real-world validation still required, and opaque CLI exclusion.

### 12.3 REFACTOR · Run G2-C and final G2 Gates

Run the **G2-C focused Gate**:

```bash
uv run --frozen pytest \
  tests/evidence \
  tests/audit \
  tests/observability \
  tests/notifier \
  tests/gateway/test_evidence_api.py \
  tests/gateway/test_readiness.py \
  tests/gateway/test_notifications_api.py \
  tests/integration/test_independent_audit_evidence.py \
  tests/integration/test_durable_lifecycle.py \
  tests/fault_injection \
  tests/test_public_docs_truth.py \
  tests/test_g2_gate_report.py -q
```

Then run the **G2 final automatic Gate** from a clean dependency state:

```bash
uv sync --frozen --extra all --extra dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen pytest -m 'not slow' -q
(cd web && npm run lint && npm run typecheck && npm run test -- --run && npm run build)
git diff --check
```

Also run these black-box proofs and paste their exact outputs/digests into the Gate report:

1. Create a fresh database, submit the same request twice with the same key, restart between commit and dispatch, and show one Edict/Memorial plus recovered outbox handling.
2. Start a Native managed tool request, wait for a persistent decision, kill the app, restart, resolve, continue from stored proposal, and show no duplicate effective result at the tested fake/provider-idempotent boundary.
3. Close and export Evidence Bundle v1, validate it with `docs/reference/evidence-bundle-v1.schema.json`, recompute its canonical digest independently, and verify every artifact digest.
4. Queue a quiet-hour notification, restart with no new event, advance to deadline, and show the mode-specific delivery receipt/outcome.
5. Query `/health/live` and `/health/ready` in healthy and injected-unhealthy states; confirm liveness remains 200 while readiness becomes 503 with the failing component.
6. Run `./scripts/local.sh start --dev`, wait for readiness, and run `./scripts/local.sh stop`; confirm both commands return, all worker PIDs terminate, and a second start over the same database reaches readiness.

Commit only after all applicable commands are GREEN: `test: close the G2 durability and evidence gate`

---

## Failure-Injection Matrix

| ID | Injection point | Required durable oracle | Guarantee boundary | Primary test |
|---|---|---|---|---|
| F01 | After idempotency reservation, before Edict insert | No idempotency/Edict/Memorial/outbox row remains | Atomic transaction | `test_g2_crash_matrix.py::test_f01` |
| F02 | After Edict and Memorial inserts, before outbox insert | Whole transaction rolls back | Atomic transaction | `test_f02` |
| F03 | After commit, before dispatch | Pending outbox row is reclaimed after restart | At-least-once delivery | `test_f03` |
| F04 | After handler business transition, before consumption ack | Redelivery occurs; handler CAS/idempotency yields one effective transition | Tested consumer only | `test_f04` |
| F05 | After DecisionRequest persist, before suspension response | Pending decision and waiting RunState are visible | Durable decision | `test_f05` |
| F06 | Process kill while decision pending | Restart lists and resolves the same decision ID | Durable decision | `test_f06` |
| F07 | Two concurrent resolvers | One CAS wins; loser gets 409; one resolution/outbox row | Decision CAS | `test_f07` |
| F08 | Resolution races expiry | Exactly one state transition wins; late attempt is denied/audited | Decision CAS, not message once-only | `test_f08` |
| F09 | Resolution commit, before resume enqueue dispatch | Resolution outbox recovers and creates one continuation attempt | Outbox + consumer idempotency | `test_f09` |
| F10 | Kill at L0/L1/L2/L3/pause safe point | Correct continuation fields and level resume | Versioned RunState | `test_f10` |
| F11 | Before tool proposal save | No effect and no resumable proposal | Safe-point ordering | `test_f11` |
| F12 | After proposal save, before decision | Waiting state; no effect | Safe-point ordering | `test_f12` |
| F13 | After decision, before intent | Resume creates one journal intent | Journal uniqueness | `test_f13` |
| F14 | After intent, before effect | Retry/restart reuses the same intent/key | Supported managed boundary | `test_f14` |
| F15 | After external effect, before receipt | Provider idempotency/lookup avoids duplicate effective result; untracked becomes uncertain; opaque CLI remains excluded | Mode-specific | `test_f15` |
| F16 | After receipt, before cursor/ack | Receipt is reused and effect is not repeated on supported boundary | Supported managed boundary | `test_f16` |
| F17 | Lease expires while old worker remains alive | Reclaimer wins; stale owner cannot commit | Lease fencing | `test_f17` |
| F18 | Final allowed attempt fails | Attempt enters DLQ once and readiness/evidence exposes it | Bounded retry | `test_f18` |
| F19 | Artifact bytes renamed, before metadata insert | No bundle references bytes; sweeper removes aged orphan safely | Artifact atomicity | `test_f19` |
| F20 | Bundle body persisted, before close | Draft remains valid and can close after restart | Evidence draft recovery | `test_f20` |
| F21 | Close committed, before HTTP response | Retry returns the same immutable bundle/hash | Evidence idempotent close | `test_f21` |
| F22 | Notification claimed, before send | Lease expiry retries according to declared mode | Delivery mode | `test_f22` |
| F23 | Notification send succeeds, before receipt update | Provider-idempotent dedupes; at-least-once may duplicate; at-most-once becomes uncertain | Delivery mode | `test_f23` |
| F24 | Quiet deadline reached with no later events | Delivery worker sends at stored `available_at` | Durable deadline | `test_f24` |
| F25 | Shutdown with spans queued | Exporter receives force-flushed spans before provider shutdown | OTel lifecycle | `test_f25` |

Every fault oracle must inspect durable state after a new process opens the database. In-memory counters alone are insufficient.

---

## Explicit Side-Effect Claim Matrix

| Boundary | Retry after ambiguous crash | Evidence required | Public claim allowed |
|---|---|---|---|
| Provider idempotency key | Retry with the same key | Intent, key hash, provider receipt/result hash, fault test | One effective provider result for the listed provider operation |
| Provider receipt lookup | Lookup first; retry only when absence is authoritative | Intent, lookup response, receipt/result hash, fault test | One effective provider result for the listed operation |
| G1 isolated workspace-only change | Reopen staging lease and compare change/restore hashes | Restore point, change-set hash, apply receipt | Source remains unchanged until separately governed apply; apply result limited to tested Git boundary |
| Untracked external effect | Do not auto-retry after possible execution | Intent, uncertainty reason, DecisionRequest | Outcome uncertain; human resolution required |
| Opaque contained CLI | Process may be restarted only under contained-executor policy; internal actions are not replayed/journaled individually | Process command fingerprint, start/exit/output hashes, workspace diff | No guarantee about internal duplicate file/network/tool side effects |

The compatibility document must name exact adapter + operation + version tested. “Native,” “HTTP,” or “provider” alone is too broad.

---

## G2 Exit Criteria

G2 is complete only when all of the following are true:

- Migrations 1–7 pass fresh install, v2 upgrade, checksum tamper, rollback, and concurrent startup tests.
- Every top-level Edict ingress uses `EdictApplicationService`; no architecture-test bypass exists.
- Submission is atomic and principal-scoped idempotency distinguishes same-hash replay from conflicting reuse.
- Outbox is recoverable and explicitly at-least-once; tested consumers produce one effective business transition under duplicate delivery.
- DecisionRequest/Resolution and versioned RunState survive real process restart; actor identity comes only from AuthContext; CAS/expiry/late semantics are deterministic.
- Attempts use claim/lease/heartbeat/fencing/DLQ; stale owners cannot commit.
- Supported managed boundaries pass intent/effect/receipt/cursor fault tests. Untracked and opaque outcomes remain honestly limited.
- L0/L1/L2/L3, pause, pending tool, decision resolution, and side-effect cursor continuations resume from serialized state without relying on a lost Python await stack.
- Planner revisions record reason, before/after diff, estimates, actuals, failure class, and acceptance evidence without adding a dynamic DAG runtime.
- Evidence Bundle v1 schema validation, canonical hash, independent audit, artifact digest verification, immutable close, export, and governed replay all pass.
- System audit is append-only at the application/trigger boundary, mutation events carry actor/IP/time/reason/correlation, traces correlate the full chain without sensitive payloads, and tracer shutdown flushes.
- Readiness fails closed for durability component failures while liveness remains simple.
- Notification delivery survives restart, respects declared provider/at-least-once/at-most-once semantics, wakes at quiet deadlines without later traffic, and supports registry-only channel extension.
- The G2 Gate report contains real command output and hashes. Capability matrix wording matches the tested scope and lists opaque CLI, real-provider, host-admin, DB-admin, and single-node limitations.

Passing G2 permits G3 to build authoritative Governance/Evidence UI over stable APIs. It does not by itself prove multi-node durability, protect against a hostile host/DB administrator, validate every real provider’s idempotency behavior, or make contained CLI internals replay-safe.
