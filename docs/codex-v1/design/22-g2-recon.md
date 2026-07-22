# G2 Durable Governance & Evidence — Execution Preflight

**Status:** `BLOCKED — plan amendments and G1 handoff freeze required before Increment 1 RED`

**Repository snapshot:** 2026-07-12, branch `feat_codex_phase_1`, committed HEAD
`302cba3742d2b12bef75722a23e320934be197ee`. The worktree was concurrently dirty with
G1.4b3 governed-apply work while this report was prepared. No runtime code, test code,
migration, commit, or build was produced by this reconnaissance.

This is an execution preflight for
`docs/superpowers/plans/2026-07-11-phase-2-durable-governance-evidence.md`, not a replacement
implementation plan. The G2 architecture is directionally sound, but the checked-in plan
cannot be executed literally against the current repository. Its migration premise is already
false, two of its required G1 contracts have not landed, and several durability claims lack the
transactional/fencing protocol needed to make their tests honest.

## 1. Executive verdict

Do **not** start G2 Increment 1 yet. Freeze and review the final G1 handoff, then amend the G2
plan in one pass. Starting the current plan would force at least one of these unsafe outcomes:

1. rewriting already-applied migration history;
2. creating a second governed-apply decision authority beside G1's existing authority;
3. acknowledging durable consumers after non-idempotent effects without an atomic business
   transition/consumer marker;
4. allowing stale attempt owners to write RunState or receipts despite a reassigned lease;
5. claiming restart-safe Agent continuation while omitting existing multi-tool-call state;
6. claiming a complete Evidence Bundle whose frozen model cannot contain required plan,
   side-effect, audit, and notification evidence;
7. testing delivery-worker readiness before the delivery worker exists.

The minimum release decision is:

- finish and commit G1.4b3;
- land G1.5 readiness/resources and G1.6 system audit/MCP security in their own next
  migrations;
- freeze the complete migration ledger names and checksums;
- select one canonical governed-apply decision model;
- amend G2's schema versions, UoW protocols, continuation schema, Evidence Bundle schema,
  lifecycle ordering, and failure tests;
- only then write Increment 1 RED tests.

## 2. Hard blockers and required plan amendments

| ID | Severity | Current evidence | Why the written G2 plan is not executable | Required amendment before RED |
|---|---|---|---|---|
| B01 | Blocker | Committed `src/tianshu/storage/migrations.py::MIGRATIONS` already ends at v4. The active G1.4b3 worktree appends v5 `0005_governed_apply_bindings`. G1.5 owns another later migration; G1.6 explicitly requires `N+1` system audit and `N+2` MCP security. | G2 says G1 is consolidated into v2, owns v3-v7, and tests versions exactly `1,2,3`. That would overwrite immutable ledger history. | Replace every fixed G2 version/name with `G1_FINAL_N+1` through `G1_FINAL_N+5`, then freeze concrete names/checksums only after G1 final. Update schema tests, upgrade fixtures, SQL comments, Gate commands, and exit criteria together. Never edit an applied migration. |
| B02 | Blocker | `system_audit_events`, `storage/system_audit_repo.py`, and the final SystemAuditLog are absent. G1.6 reconnaissance confirms they are not implemented. | G2 Increment 3 already requires durable audit denials; Increment 10 assumes the G1 audit table exists and extends it. | Make completed G1.6 audit a strict precondition of G2 Increment 1. Add the exact G1 audit model/repository/UoW append symbols to the handoff test. |
| B03 | Blocker | `/health/live` exists at `src/tianshu/app.py::create_app`; `/health/ready` is public-listed by `SecurityBoundaryMiddleware._public_route()` but has no route. G1.5 readiness is not implemented. | G2's lifecycle and Increment 10 build on a readiness contract that has not landed. | Make final G1.5 readiness model/route a handoff assertion; specify which checks G2 extends rather than creating a second readiness model. |
| B04 | Blocker | G1 has `models/workspace.py::ApplyDecision`, `ApplyReceipt`, `executor/workspace_service.py::WorkspaceService.issue_apply_decision/apply`, and `storage/workspace_repo.py::claim_apply_decision/save_apply_receipt`. | G2 adds `DecisionKind.GOVERNED_APPLY` and a generic DecisionRequest/Resolution/table without mapping the existing token-bound decision and receipt. Two authorities can disagree about approval, expiry, consumption, actor binding, and evidence. | **Frozen resolution:** generic `DecisionRequest`/`Resolution` is the sole governance authority. A workspace apply authorization is an immutable, one-way projection bound to the resolved generic request, and `WorkspaceService` consumes that binding atomically. Once G2 is enabled, no public path may independently issue a second approval authority. Preserve consumed historical receipts as evidence; fail closed on unbound pending legacy authorizations rather than inventing approval. Add migration/backfill, single-authority architecture, restart, and API compatibility tests. |
| B05 | Blocker | Current Agent history records assistant `tool_calls` and `reasoning_content`, and one turn can contain multiple tool calls. | `PersistedChatMessageV1` omits both fields; `AgentContinuationV1.pending_tool` is singular. It cannot resume the current provider conversation exactly and would require a new LLM call or lose tool ordering. | Version the continuation around the actual provider-neutral assistant message and an ordered tuple of pending tool proposals/results. Preserve tool-call IDs, normalized arguments, reasoning field policy, cursor/index, and provider/model compatibility. RED-test a two-tool assistant turn. |
| B06 | Blocker | `run_states` and `side_effect_journal` as specified do not carry attempt owner/fence identity, while the plan promises every write is fenced. | RunState CAS alone does not reject a previous owner after lease reassignment when the RunState version has not changed. A stale worker can still write a journal receipt or state transition. | Define one repository/UoW fenced-write primitive that verifies `(attempt_id, owner_id, expected_attempt_version, lease_not_expired)` and mutates attempt + RunState/journal in one transaction. All dispatcher/executor writes must carry this token. |
| B07 | Blocker | EventBus currently persists before local handler dispatch, swallows handler failures, and registrations have no stable names. Consumers include scheduler, planner, executor, auditor, notifier, cost, memory, universe, personas, bot approvals, and outbound channels. | Increment 2 records consumption after handler return but does not define idempotency/atomic marker semantics for the real consumers. Stable names alone do not prevent duplicate scheduling, execution, audit mutation, or external sends. Nested `emit()` calls also remain locally durable only. | Add a consumer-by-consumer idempotency matrix and a transaction-aware handler protocol. Convert every durability-carrying nested transition to outbox/UoW, or label it explicitly local and remove the durable claim. External notification consumers must enqueue delivery rows before they join durable dispatch. |
| B08 | Blocker | The plan globally forbids secret persistence from Increment 1, but stronger allowlisting/redaction is scheduled in Increment 10. Current `security/redact.py` is pattern-based and not a durable payload policy. | Outbox, idempotency response, decision payload, continuation, journal, and audit records can persist sensitive values for nine increments before the proposed control exists. | Move canonical durable-payload allowlists, recursive secret-key/value rejection, maximum sizes, and sentinel tests into Increment 1. Reuse the same codec for all later durable stores. |
| B09 | Blocker | The frozen `EvidenceBundleV1` fields do not include plan revisions, side-effect intents/receipts/uncertainties, system audit entries, or notification delivery evidence. | Increment 9 text and G2 exit criteria require those facts, but the model cannot serialize them. Draft creation and content-hash exclusion semantics are also underspecified. | Revise and freeze EvidenceBundle v1 before v6-equivalent migration: add explicit evidence sections/references, define draft body/version, define canonical hash input excluding `content_hash`, and define missing/mandatory evidence closure failures. |
| B10 | Blocker | Increment 10 readiness tests require a stale delivery-worker heartbeat; `DeliveryWorker` and its schema are created in Increment 11. | The expected RED/GREEN boundary is impossible: Increment 10 cannot turn that check green without implementing Increment 11 early. | Move delivery-worker readiness registration/test to Increment 11, or move notification worker/schema ahead of Increment 10. Keep Increment 10 readiness extensible by registered mandatory components. |
| B11 | High | `AuthContext` has immutable principal, source/client kind, correlation ID, and `remote_addr`; it does not have the plan's `source_ip` attribute. Principal owns scopes. | The audit/decision contract names fields that do not match the actual identity boundary, inviting adapter-local identity reconstruction. | Map exact final G1 fields in the handoff contract. Either audit `remote_addr` or add one canonical normalization property in G1; never accept actor/source address from command bodies. Require service and AuthContext correlation IDs to match or remove the duplicate parameter. |
| B12 | High | `pyproject.toml`'s mypy package list does not cover most proposed G2 packages (`application`, `governance`, `evidence`, `auditor`, `executor`, `scheduler`, `notifier`). | A passing `uv run --frozen mypy` would not prove the new durable boundary is typed. | Expand the mypy package list or make every Gate pass explicit G2 paths. Record the exact command/output in the Gate report. |

## 3. Migration ownership and upgrade law

### 3.1 Live ledger state

At the captured snapshot:

- committed HEAD contains:
  - v1 `0001_adopt_v042_baseline`;
  - v2 `0002_auth_tokens`;
  - v3 `0003_governance_contracts`;
  - v4 `0004_workspace_foundation`;
- the active G1.4b3 worktree adds v5 `0005_governed_apply_bindings` as an additive
  migration rather than reopening v4;
- G1.5 still owns a default-persona/resource-era migration after that tail;
- G1.6 still owns two ordered migrations: system audit (`N+1`) and MCP ciphertext/grants
  (`N+2`).

Therefore G2's first version cannot be known until G1 final freezes. With the currently planned
G1 work it will be later than v5 and likely later than v8, but the executor must calculate it
from final `MIGRATIONS[-1]`, not encode that estimate.

### 3.2 Required renumbering rule

Amend the plan to use symbolic ownership while G1 is active:

| Written G2 name | Temporary plan name | Purpose |
|---|---|---|
| `0003_submission_outbox` | `G1_FINAL_N+1_submission_outbox` | submission/UoW/outbox |
| `0004_decisions_run_state` | `G1_FINAL_N+2_decisions_run_state` | decisions and RunState |
| `0005_attempts_side_effects` | `G1_FINAL_N+3_attempts_side_effects` | attempt leases and journal |
| `0006_plans_artifacts_evidence_audit` | `G1_FINAL_N+4_plans_artifacts_evidence_audit` | revisions, artifacts, evidence, G1 audit extensions |
| `0007_notification_deliveries` | `G1_FINAL_N+5_notification_deliveries` | notification delivery |

Once G1 final is committed, replace the symbolic names with contiguous concrete versions in a
single G2 plan amendment. The associated tests must assert the **entire final immutable prefix**,
not versions `1,2,3`. Upgrade tests need fixtures at every real prior prefix, especially the
post-G1.4, post-G1.5, and post-G1.6 prefixes.

### 3.3 Reusable migration machinery

Keep and reuse:

- `src/tianshu/storage/migration_ledger.py::apply_migrations` for one
  `BEGIN IMMEDIATE` transaction per migration, ledger prefix/checksum verification, and callback
  transaction ownership;
- `src/tianshu/storage/migration_ledger.py::MigrationConnection`, which intentionally prevents
  callbacks from committing, rolling back, beginning, or using `executescript`;
- `src/tianshu/storage/_base.py::_StorageBase.init_db` for serialized backup/migrate/integrity
  flow and the same-connection `:memory:` behavior;
- additive statement tuples and callbacks in `src/tianshu/storage/migrations.py`.

Do not copy migration logic into repositories, application services, startup workers, or tests.
Do not edit a migration after any database can have recorded its checksum.

## 4. Exact package and symbol map

The table maps the proposed G2 ownership onto live code. “Amend” means the G2 plan must name the
symbol/file explicitly before execution; it does not authorize an edit during this preflight.

| Boundary | Existing symbol(s) to reuse or adapt | Planned owner | Required mapping/amendment |
|---|---|---|---|
| SQLite connection/lock | `storage/_base.py::_StorageBase`, `_lock`, `_conn`, `transaction` | `storage/uow.py` plus repositories | UoW must use the same re-entrant-safe connection/lock discipline; do not create a second connection behind repository methods during one transaction. |
| Edict insert | `storage/edict_repo.py::EdictRepository.save_edict`, `_save_requested_governance_contract_unlocked` | `storage/uow.py::SubmissionUnitOfWork` | Extract a connection-taking insert that includes both Edict and requested governance-contract projection; otherwise the new path silently loses G1 governance facts. |
| Memorial insert | `storage/memorial_repo.py::MemorialRepository.save_memorial`, effective-contract projection | submission UoW | Preserve the effective governance-contract side write in the same transaction. |
| Event creation | `storage/event_repo.py::EventRepository.append_event`; `models/events.py::EventEnvelope` | `storage/outbox_repo.py`, `application/outbox.py` | Freeze one event ID/timestamp/payload codec and decide whether heartbeat mutation belongs to the same UoW. Existing mutable `dict[str, Any]` payload is not yet a safe durable codec. |
| Legacy submit | `core/edict_ops.py::submit_new_edict` | `application/edict_service.py::EdictApplicationService.submit` | Replace the three-step save/save/fire authority; keep only a compatibility adapter. |
| HTTP ingress | `gateway/edicts_api.py::create_edict`, `_idempotency_request_hash` | application submit service | Header/body key policy, trusted principal binding, request hash fields, replay response, and conflict response must be centralized. |
| CLI ingress | `cli/commands/edict.py`; `cli/client.py::api_post` | HTTP application route | Add idempotency header support; CLI may not calculate a competing canonical request hash. |
| Web ingress | `web/src/api/edicts.ts::createEdict`; `web/src/pages/EdictCreatePage.tsx` | HTTP application route | Generate/retain one key per user submission retry, not per network attempt. |
| MCP ingress | `gateway/mcp_server.py` submit tool | application submit service | Derive AuthContext from MCP boundary and pass one stable key; no direct repository write. |
| Bot/chat ingress | `core/edict_bridge.py` direct saves | application submit service | One adapter command per accepted user message; direct writes become architecture-test failures. |
| Tool/schedule ingress | `tools/submit_edict.py`, `tools/schedule_edict.py` | application submit service | Route through the same UoW; define a stable producer request key. |
| Event routing | `bus/event_bus.py::EventBus.emit/fire/on`; `bootstrap/wiring_scheduler.py` registrations | `EventBus.dispatch`, `OutboxDispatcher` | Split durable dispatch from local UI dispatch, add stable names, reports, and transaction-aware consumer completion. |
| Approvals | `governance/approvals.py::ApprovalManager` in-memory maps/events | `governance/decision_service.py` | Compatibility facade only after persistence commits; no parked `asyncio.Event` as authority. |
| Workspace apply | `models/workspace.py::ApplyDecision/ApplyReceipt`; `WorkspaceService.issue_apply_decision/apply`; workspace repository claim/receipt | generic decisions plus workspace projection | Resolve duplicate authority before schema. Preserve immutable change-set/restore-point/repository binding from G1. |
| Identity | `models/principal.py::Principal`, `AuthContext`; `gateway/auth.py::SecurityBoundaryMiddleware` | all command services | Actor is derived only from AuthContext. Align `remote_addr`/scopes/correlation fields exactly. |
| Agent continuation | `orchestrator/agent.py` LoopState/message/tool-call loop | `models/run_state.py::AgentContinuationV1` | Persist ordered tool calls, reasoning policy, results/cursor, token/cost state, provider/model binding, and no-extra-LLM-call resume. |
| Outer loop | `orchestrator/state.py::OrchestratorState`; `orchestrator/loop.py` checkpoint/pause; `storage/orchestrator_repo.py` | `RunStateV1`, continuation adapter | One-way import old checkpoint, then RunState is authority. End the attempt on suspension/pause; do not poll a durable pause in a live coroutine. |
| Scheduler ownership | `scheduler/scheduler.py` orphan sweep/review tasks | attempt dispatcher/reconciler | Replace heuristic orphan resume as execution authority while retaining scheduling-time behavior. Reconcile only expired leases and fence every write. |
| Execution receipts | `executor/gateway.py::ExecutionGateway`; process receipts; workspace `ApplyReceipt` | side-effect journal/provider adapters | Reuse supported receipts, but encode Native/Keqing truth. Process completion is not target-system outcome; opaque CLI remains process-level evidence only. |
| Capability truth | `executor/capabilities.py` | side-effect semantics | Do not promote durable resume/receipt/apply claims until named fault tests pass. Re-read after G1.4b3 final because this file is concurrently modified. |
| Planner | `planner/planner.py::Planner`; `models/dag.py`; `storage/dag_repo.py` | plan revision/diff/quality repositories | Existing DAG `plan_json` is an execution projection, not immutable revision truth. Persist revision first, then safely project. |
| Auditor | `auditor/auditor.py::Auditor` and current mutating handler | `application/evidence.py` plus pure conclusion adapter | Reuse rule/LLM conclusion computation, but separate it from Memorial/Edict mutation and event emission. Mandatory evidence failures close fail-closed. |
| Artifact refs | `models/common.py::ArtifactRef` path/url | `evidence/artifact_store.py` | Existing ref is not content-addressed or quota-reserved. Define storage root, hash, size, media type, atomic rename, and reservation protocol. |
| System audit | final G1.6 model/repository/log (not landed) | G2 audit extensions | G2 must extend, not recreate. Add connection-taking append so DB mutation and outcome audit share a UoW. Define legacy-to-hash-chain start semantics. |
| OTel | `observability.py::init_telemetry`, span helpers | expanded observability/lifespan | Retain provider for force-flush/shutdown; allowlist attributes; propagate correlation/Edict/Memorial/attempt IDs. |
| Liveness/readiness | `app.py` `/health/live`; pending G1.5 readiness | `application/readiness.py`, `gateway/readiness_api.py` | Extend one readiness registry. Keep liveness dependency-free. Move delivery heartbeat check to Increment 11. |
| Notifications | `notifier/notifier.py`; `storage/notify_repo.py`; `notifier/channel_registry.py`; `channels/base.py` | delivery repo/worker/API | Reuse channel registry and legacy rows as migration source. Replace lazy flush/delete-on-failure with a Clock-driven queue. Enqueue before external dispatch. |
| Local lifecycle | `app.py::lifespan`; `scripts/local.sh`; `tests/scripts/test_local_sh.py` | fixed process order | Split channel/bot construction from start so all stable consumers are registered before durable workers/producers start. Add scripts/tests to Increment 12 file list. |
| Import gates | `pyproject.toml` import-linter contracts and mypy package list | G2 package boundaries | Remove only the obsolete EventBus→Storage exemption after adapters are migrated; cover every new package in type checks. |

## 5. UoW and outbox protocol that must be frozen

### 5.1 Submission transaction

The Increment 1 UoW must own exactly one SQLite transaction and produce, in order:

1. canonical request identity lookup/claim;
2. Edict row plus requested governance-contract projection;
3. initial Memorial row plus effective governance-contract projection;
4. one frozen `edict.submitted` envelope/outbox row;
5. the canonical replay response in `submission_idempotency`;
6. commit once.

The request hash contract is presently incomplete. Freeze the included command fields and exclude
server-generated IDs/timestamps. The key must be scoped to trusted producer/principal identity,
not merely an arbitrary body `submitter`. Define how `extra_payload`, governance request,
correlation ID, producer/source, and omitted/default fields normalize. AuthContext correlation ID
must either be the only correlation ID or equal the explicit service parameter.

The durable JSON codec must be introduced here, not in Increment 10. It must reject or redact
secret-shaped keys/values, forbid non-JSON values, canonicalize mappings/sequences, bound nesting
and byte size, and be the only encoder for outbox/idempotency/decisions/RunState/journal/audit/
evidence/notification payloads.

### 5.2 Consumer completion protocol

`(event_id, consumer_name)` success rows are necessary but insufficient. Freeze one of these
two protocols per consumer:

- **Database consumer:** handler receives a UoW/context and commits its idempotent business
  transition plus the consumer-success row atomically; or
- **Externally idempotent consumer:** persist intent/key first, perform the effect with a provider
  that supports that key or receipt lookup, persist receipt/uncertainty, then mark consumption.

Writing success after an arbitrary handler returns cannot make an external send once-only. The
plan needs an inventory for every registered consumer and must fail startup if a durable event is
bound to an unnamed or unclassified consumer.

### 5.3 Nested event transitions

The live flow is scheduler → planner → executor → auditor/notifier and currently uses nested
`EventBus.emit()`. If only ingress is placed in the outbox, a crash can still lose downstream
durable work. Worse, swallowed nested handler errors may let the parent event be marked consumed.
For every event type, mark it as one of:

- durable transition: insert its outbox row in the same UoW as the state mutation;
- local live projection: dispatch best-effort and exclude it from durability/readiness claims;
- external delivery intent: enqueue notification/side-effect intent before I/O.

Do not leave this classification implicit in call sites.

## 6. Decision, RunState, attempt, and fencing corrections

### 6.1 Decision service

Before Increment 3, add to the fixed contract:

- `cancel()` semantics because `DecisionStatus.CANCELLED` exists;
- a started/stopped expiry worker or deterministic expiry-on-read/resolve rule;
- structured conflict codes/actions for resolved, expired, cancelled, stale-version, hash conflict,
  authorization failure, and late resolution;
- one transactional SystemAuditLog append protocol for denial and success;
- exact compatibility semantics for ApprovalManager, Decree, tool approvals, outer-loop approvals,
  plan replan, uncertainty resolution, and G1 governed apply;
- idempotent resume-attempt creation in the same transaction that consumes
  `decision.resolved`, or a tested unique-key consumer transition.

Increment 4's restart test should initially prove that a pending decision and waiting RunState
survive and that resolution produces one durable resume event. It must not claim the Agent has
continued until Increment 7 actually supplies the continuation dispatcher.

### 6.2 RunState schema

The versioned state needs explicit invariants:

- one canonical checkpoint/cursor location; remove or validate duplicate top-level and
  continuation fields;
- continuation discriminator equals stored payload kind;
- provider/model/tool schema version compatibility is checked before resume;
- an ordered multi-tool continuation with call ID, name, canonical arguments, decision binding,
  result/error, and next index;
- safe policy for `reasoning_content` (persist a redacted/allowed representation or prove it is
  not needed for provider continuation);
- exact outer-loop messages/history, steer/pause intent, level/iteration, cost and budget units;
- terminal/suspended/paused transitions and whether a new attempt is created;
- one-way legacy checkpoint import marker to prevent repeated imports.

### 6.3 Attempt repository and dispatcher

The attempt API in the plan is missing transitions it names. Freeze methods for:

- enqueue/claim;
- claimed → running;
- heartbeat/extend lease;
- running → suspended without consuming retry budget;
- running → succeeded/failed/retry_wait/dead_letter;
- pause/release at a safe point;
- expired-lease reconciliation;
- idempotent continuation-attempt creation by stable source key.

Define `attempt_no`, `max_attempts`, failure budget, suspension count, and backoff independently.
A pause/decision suspension is not a failure retry. Claim and reconciliation tests need two
independent Storage connections and workers, not two calls sharing one object lock.

### 6.4 Fenced write token

Every executor-owned write must accept one immutable token, for example:

```text
AttemptFence(attempt_id, owner_id, expected_attempt_version, lease_expires_at)
```

Repository SQL must verify the current attempt row still has that owner/version and an active
lease in the same transaction as RunState/journal/receipt mutation. The write then advances both
the relevant record version and attempt version. This applies to:

- RunState save/transition;
- journal intent creation/state transition;
- provider receipt/uncertainty append;
- side-effect cursor advance;
- suspension/terminal outcome;
- outbox/consumer acknowledgement produced by the attempt.

Without that join/CAS, the plan's stale-owner guarantee is false.

## 7. Side-effect journal and capability truth

The five crash barriers in Increment 6 are useful, but the plan must define repository/service
interfaces before tests:

1. persist redacted canonical intent and stable key;
2. select provider semantics from a frozen capability declaration;
3. attempt or look up the effect;
4. persist receipt or uncertainty with the active AttemptFence;
5. advance RunState cursor with the same fence;
6. finish/ack the attempt and durable event.

Supported mappings should remain intentionally narrow:

| Effect | Reusable current evidence | Honest G2 claim |
|---|---|---|
| Managed workspace apply | G1 `ApplyDecision`/`ApplyReceipt`, canonical change set and restore point bindings | Can become governed and receipt-backed after the decision-authority mapping and crash matrix pass. |
| Native managed tool/process | `ExecutionGateway` command/process receipt and containment evidence | Process-level attempt/exit evidence; target-system outcome only where a provider-specific idempotency/lookup adapter exists. |
| Keqing/opaque CLI | Existing contained subprocess evidence | `OPAQUE_CLI`: process-level evidence, no automatic claim about downstream effect outcome. |
| Untracked external effect | No provider lookup/idempotency authority | Persist `uncertain`, create one DecisionRequest, never auto-retry after possible execution. |

Capabilities must be promoted only after their named tests pass. Re-read
`executor/capabilities.py` after G1.4b3 is committed because the active worktree is changing its
workspace-apply truth.

## 8. Planner, ArtifactStore, Evidence Bundle, and audit corrections

### 8.1 Planner revisions

`Planner` currently returns/emits a plan and the DAG repository stores `plan_json`; neither is an
immutable revision history. The revised flow should be:

1. persist immutable PlanRevision and its canonical content hash;
2. persist diff/quality facts against an explicit parent revision;
3. authorize replan through DecisionService when required;
4. reject graph mutation once any DAG node is active;
5. project the accepted revision into the existing DAG execution representation;
6. bind RunState and later evidence closure to the exact revision ID/hash.

The full plans/artifacts/evidence/audit-extension migration still needs to land atomically before
Increment 9 uses it, but only after its schema/model contradictions are resolved.

### 8.2 ArtifactStore quota and atomicity

The plan says quota reservation occurs under a storage lock but defines no reservation row. It
must choose and test one protocol:

- reserve bytes durably in SQLite, release/finalize after streaming to a temporary file, then
  atomically rename by content hash; or
- hold the process lock across the entire bounded stream, accepting the explicit contention
  tradeoff.

The first is safer for concurrent writers and restart recovery, but requires a reservation table
or equivalent ledger in the migration. Do not claim quota atomicity from a pre-check followed by
an unlocked write. Deduplicate only after independently verifying size and hash. Reject paths
outside the configured root and never replay shell commands from an artifact.

### 8.3 EvidenceBundle v1 amendment

Before freezing schema/model/hash fixtures, add explicit, versioned sections or artifact
references for:

- accepted plan revision, ancestry/diffs, and quality metrics;
- attempt history including suspensions, retries, DLQ, and fences;
- side-effect intents, receipts, uncertainty, and resolution;
- policy/governance decision requests and immutable resolutions;
- system audit chain range/root and verification result;
- artifact manifest with hash/size/media type;
- notification delivery outcomes and declared semantics;
- final auditor conclusion and mandatory evidence rule results.

Define a draft model separately from closed `EvidenceBundleV1`. Define the exact canonical hash
input (closed content with `content_hash` absent), storage version CAS, immutable close trigger,
and behavior when a referenced row changes or is missing. Closure must read one consistent
SQLite snapshot and fail closed; it must not assemble facts across independently changing reads.

### 8.4 System audit chain

G2 must consume the final G1.6 SystemAuditLog rather than invent a sibling. The append API needs a
connection-taking low-level primitive for atomic DB mutation/outcome audit. When G2 adds nullable
chain/context columns, define how legacy G1 rows participate:

- whether legacy rows are backfilled in deterministic ID order;
- or whether the first G2 hashed row declares an explicit legacy segment/root.

The conditional migration/checksum algorithm must make either choice reproducible. Security and
governance mutations include workspace apply, so Increment 10's file list must also name
`executor/workspace_service.py` and its gateway service/API adapter, or document a lower-level
audit protocol that already covers it.

## 9. OTel, readiness, lifecycle, and notification ordering

### 9.1 OTel

Reuse the current initialization and call-site spans, but retain the tracer provider so shutdown
can force-flush and close it. Freeze an attribute allowlist: correlation, Edict, Memorial,
attempt, decision, plan revision, and evidence bundle identifiers are acceptable; request/tool/
notification bodies, headers, environment, credentials, and raw paths are not. Tests should use a
fake/in-memory exporter and assert both positive correlation and negative secret sentinels.

### 9.2 Readiness

Extend the one G1.5 readiness registry with component heartbeats and storage probes. Increment 10
can add outbox dispatcher, attempt dispatcher/reconciler, artifact read/write probe, backlog age,
and expired-lease grace. Delivery-worker heartbeat belongs to Increment 11. Liveness must remain
200 and dependency-free while readiness returns 503 with a bounded public summary.

### 9.3 Startup and shutdown

The fixed lifecycle is correct, but current channel/bot wiring constructs and starts producers too
early. Move the construction/start split into Increment 2, because stable consumer registration
must precede outbox dispatch and producers. The executable order is:

```text
open/migrate Storage
  -> construct repositories/services/providers
  -> register and validate every stable consumer
  -> start outbox dispatcher
  -> start attempt dispatcher and reconciler
  -> start delivery worker when introduced
  -> start scheduler, Bots, MCP, and request producers
  -> publish initial heartbeats
  -> become readiness-eligible
```

Shutdown reverses admission/claiming before bounded drain, provider/OTel/artifact/SQLite close.
Every `start` records success; failure unwinds only successfully started components. Every `stop`
is idempotent and time-bounded.

Increment 12 must explicitly modify/test `scripts/local.sh` because it currently polls liveness,
not readiness. Add `tests/scripts/test_local_sh.py` to its file and Gate lists.

### 9.4 Notification ordering

The current Notifier sends directly, flushes quiet rows only on later activity, and can delete a
pending row even when send fails. Retain ChannelRegistry and channel adapters, but make Notifier a
delivery-intent producer. Persist one redacted delivery per event/channel before any network I/O.

There is a dependency conflict with Increment 2: durable outbox redelivery must not call the
current non-idempotent external Notifier directly. Either:

1. introduce the minimal delivery-intent table/consumer in Increment 2 and add full worker/API/
   legacy conversion later; or
2. exclude external notifier consumers from durable dispatch until Increment 11 and explicitly
   state that gap.

Option 1 gives a coherent end-to-end durability claim and is preferred. Mode-specific worker
behavior remains Increment 11: exactly-once only when provider idempotency/lookup proves it;
otherwise at-least-once or uncertain. Quiet-hour scheduling uses an injected Clock and a computed
`available_at`, never “flush on next event”.

## 10. Dependency graph and execution order

The twelve increments should remain one serial mainline. Limited parallel work is safe only where
it does not share migrations, application lifespan, Storage facade, domain contracts, or the same
tests.

```text
G1.4b3 final
  -> G1.5 final readiness/resources
  -> G1.6 final system audit/MCP security
  -> freeze G1 migration prefix + G2 plan amendment
  -> I1 UoW/submission/secret-safe durable codec
  -> I2 unified ingress/outbox/consumer protocol/lifecycle split
  -> I3 decisions + corrected RunState
  -> I4 durable decision adapters
  -> I5 attempts + fencing + reconciliation
  -> I6 side-effect journal + capability truth
  -> I7 Agent/outer-loop resume
  -> I8 plan revisions + complete reserved schema
  -> I9 ArtifactStore + corrected Evidence Bundle
  -> I10 audit extensions + OTel + readiness (without delivery heartbeat)
  -> I11 durable notifications + delivery readiness
  -> I12 real fault matrix + docs + Gate evidence
```

| Increment | Strict prerequisites | Necessarily serial work | Safe limited parallel work after contracts freeze |
|---|---|---|---|
| 1 | Final G1 prefix, canonical apply decision choice, durable JSON/hash contract | migration file, Storage/UoW extraction, application submit orchestration | RED fixtures for hash normalization and per-prefix upgrade can be authored separately; final integration remains serial. |
| 2 | I1 GREEN | EventBus protocol, all consumer registrations, app lifecycle, direct-write removal | Adapter-specific HTTP/CLI/Web/MCP/Bot RED tests may be divided if they do not edit shared service/wiring files. |
| 3 | I2 GREEN, G1 SystemAuditLog | migration, DecisionService/UoW, RunState schema/mappers | Decision model tests and continuation round-trip test design can proceed independently, then integrate serially. |
| 4 | I3 GREEN | ApprovalManager/Decree/API/Bot compatibility and resolution-resume consumer | Surface-specific adapter tests can be split; no parallel decision authority implementation. |
| 5 | I3/I4 stable | migration, attempt repository, dispatcher/reconciler, scheduler authority change | Race/fault harness can be prepared independently using two connections/processes. |
| 6 | I5 GREEN, G1 apply final | shared attempts/journal migration already frozen, fenced effect flow, capability updates | Provider-specific fake adapters/tests can be split after journal protocol is fixed. |
| 7 | I3-I6 GREEN | Agent and outer-loop continuation execution, pause/suspend ownership | L0-L3 fixture generation and legacy-checkpoint fixtures may be parallel; Agent/loop edits remain coordinated. |
| 8 | I7 semantics stable | complete reserved migration, plan revision service, DAG projection | Pure diff/quality metric tests may be split after PlanRevision schema freezes. |
| 9 | I8 migration applied | EvidenceApplicationService snapshot/close, routes, artifact/evidence integration | Artifact streaming/hash tests and JSON-schema validator tests can run in parallel if they avoid shared migration/model edits. |
| 10 | I9 GREEN, final G1 audit | audit extension/chain, app lifecycle, readiness/OTel | Exporter tests and redaction corpus may be split. Delivery readiness is excluded. |
| 11 | I2 consumer protocol, I10 readiness registry | notification migration/conversion, Notifier cutover, lifecycle | Channel-mode fake-provider tests can be split after Delivery contract freezes. |
| 12 | I1-I11 GREEN | fault fixes, release docs, Gate report | Failure cases can run in parallel in isolated temp directories/databases; merge no speculative fixes until each failure is reproduced. |

Do not assign parallel implementation agents to `storage/migrations.py`, `storage/__init__.py`,
`app.py`, EventBus/wiring, shared models, or the same test modules. Those are serial integration
surfaces. A single intentional commit per increment remains the safest review boundary.

## 11. Honest RED tests and process-boundary rules

### Increment 1

- First test the final G1→G2 handoff. A failed handoff is a blocker, not a G2 RED.
- RED should prove the legacy three-write submit can leave partial work under injected failure,
  the new UoW objects do not exist, and same-key/different-hash semantics are absent.
- Transaction rollback injection is honest here; it proves atomicity, not crash recovery.
- Include governance-contract projection rows in all-or-nothing assertions.

### Increment 2

- Use a file-backed DB and a new process to prove commit-before-dispatch recovery.
- Kill after commit and before claim; restart and observe the same envelope ID.
- Crash one named consumer and prove successful consumers are skipped while only failed/unseen
  consumers retry.
- Duplicate-deliver each real database consumer and assert one effective transition, not merely
  one consumer-success row.
- Architecture test every ingress path and nested durable event producer.

### Increment 3

- Resolve races through separate threads/connections; assert one CAS winner and structured loser.
- Verify actor spoof fields in bodies are rejected/ignored and audit uses AuthContext identity.
- Round-trip **all** continuation fields, including two tool calls and unknown schema/kind rejection.
- Drive expiry with injected Clock; do not sleep.

### Increment 4

- Use a subprocess for pending-decision restart.
- At this increment, assert one durable `decision.resolved`/resume intent only. Full Agent resume is
  Increment 7's responsibility.
- Test every legacy surface as an adapter and prove no in-memory pending dictionary is authority.

### Increment 5

- Use two Storage connections/workers to claim concurrently.
- Kill an owner process, advance Clock/lease, reassign, and prove the old AttemptFence loses every
  state/journal/terminal write.
- Prove suspension does not consume failure budget and final allowed failure produces one DLQ row.

### Increment 6

- Use provider fakes with an external oracle independent of SQLite so “effect happened, receipt
  missing” is observable after process death.
- Exercise all five barriers in new processes where post-crash durable state matters.
- Exactly-once is asserted only for providers with idempotency/lookup; untracked effect becomes
  uncertain; opaque CLI records process-only truth.
- Scan every durable store and captured log/span for secret sentinels.

### Increment 7

- Kill at L0/L1/L2/L3, pending multi-tool decision, pause, and after stored tool result.
- Restart into a new attempt and assert no extra LLM call for already-produced assistant tool calls.
- Compare full provider-visible message/tool sequence before and after resume.
- Reject terminal continuation and stale state/attempt writers.

### Increment 8

- Kill after revision persist and before DAG projection; retry must reuse the same revision.
- Reject replan once any DAG node is active, using the live DAG scheduler state.
- Validate diffs/metrics against independently computed fixtures, not the same helper used by
  production.

### Increment 9

- Validate bundle JSON against an independent checked-in schema and recompute its canonical hash
  independently.
- Direct SQL update/delete tests must prove immutable triggers, including after reopen.
- Quota tests need concurrent writers and crash recovery of reservations/temp files.
- Replay uses mocked safe services and asserts no shell/process/network call is reconstructed from
  evidence.

### Increment 10

- Use an in-memory/fake OTel exporter; assert correlation IDs and shutdown flush.
- Inject audit append failure and prove governed/security mutation fails closed.
- Verify audit chain across the legacy/G2 boundary with an independent verifier.
- Test each current mandatory heartbeat/probe individually. Do **not** expect delivery heartbeat
  until Increment 11.

### Increment 11

- Use injected Clock and provider oracle; no real sleeps.
- Crash before send, after provider acceptance, after receipt, and before ack for each declared
  delivery mode.
- Restart with no later event and prove quiet delivery occurs at `available_at`.
- Prove manual retry authorization, reason, CAS, audit evidence, and uncertainty rules.
- Now add delivery heartbeat to readiness and test stale/failing/recovered states.

### Increment 12

- F01/F02 may use transaction failure injection because their claim is rollback.
- F03-F25 cases that claim restart, lease recovery, duplicate external behavior, lifecycle, or
  flush must use new processes and file-backed durable state.
- Each case owns a temp HOME/database/artifact root/ports and records seed, fault barrier, process
  exit, restart command, and asserted durable rows.
- Generate the Gate report only from actual command output. Missing credentials/providers remain
  `external_pending`/`unverified`; never convert them to pass by simulation.
- Run `./scripts/local.sh start --dev`, poll `/health/ready`, stop, assert all PIDs exit, then start
  again over the same DB.

## 12. Import and architecture boundary recommendation

The intended dependency direction should be frozen as:

```text
gateway / cli / bot / mcp adapters
              |
              v
application orchestration  ---> governance / evidence / planner services
              |                         |
              v                         v
executor / scheduler / notifier domain protocols and models
              |
              v
storage repositories / ArtifactStore / provider adapters
```

Two traps need explicit import-linter tests:

1. `executor` must not import the higher `application/dispatcher.py`. The application dispatcher
   orchestrates executor work through a lower-level protocol/context; the AttemptFence belongs in
   a lower shared model/storage contract.
2. `governance` must not import a higher/sibling concrete auditor implementation merely to append
   audit. Inject a narrow SystemAudit protocol/UoW primitive.

Storage may import domain data models, but domain/application/gateway code may not reach raw
SQLite connections or repository internals. EventBus's current Storage import exemption should be
removed only after outbox persistence has moved behind the application/storage adapter and all
call sites pass.

## 13. G1 handoff test must assert these exact facts

`tests/integration/test_g1_g2_handoff.py` should be a blocker test, not an implementation shim. It
must assert:

- final immutable migration prefix, names, checksums, and no pending G1 migrations;
- one canonical Principal/AuthContext, including correlation and remote-address normalization;
- one canonical governance-contract model/repository projection;
- final SystemAuditEvent/SystemAuditLog table and connection-taking append contract;
- `/health/live` and one canonical `/health/ready` response/registry;
- G1 WorkspaceService canonical change-set, restore-point, governed-apply authorization, receipt,
  and capability truth;
- final ExecutionGateway managed/opaque effect declarations;
- no duplicate decision/audit/workspace/execution identity introduced by G2 adapters;
- G1.5 packaged resources and G1.6 security migrations remain readable after the first G2
  migration.

If this test fails, fix the G1 implementation or an explicitly one-way compatibility adapter.
Do not create a parallel G2 concept to make the test pass.

## 14. Ready-to-execute checklist

G2 may move from `BLOCKED` to `READY` only when all boxes are true:

- [ ] G1.4b3, G1.5, and G1.6 are committed, reviewed, and green.
- [ ] Worktree is clean or G2 uses an isolated worktree based on that final commit.
- [ ] Final migration prefix/names/checksums are frozen and G2 versions are renumbered.
- [ ] G1→G2 handoff test passes without introducing duplicate domain authorities.
- [ ] One governed-apply decision authority and compatibility/backfill protocol are documented.
- [ ] Durable JSON/hash/redaction contract is moved to Increment 1.
- [ ] Every durable EventBus consumer has an idempotency/atomic marker classification.
- [ ] Multi-tool AgentContinuation and complete RunState invariants are frozen.
- [ ] AttemptFence and transactional repository signatures are frozen.
- [ ] EvidenceBundle v1 contains every required evidence class and has exact draft/hash rules.
- [ ] Delivery readiness is moved to Increment 11 (or notifications are reordered earlier).
- [ ] Increment 12 includes local script readiness/start-stop files and tests.
- [ ] Mypy/import-linter commands cover every new G2 package.
- [ ] Failure-matrix harness distinguishes exception rollback from real process crash/restart.

Until then, the only safe G2 work is plan correction and isolated test-fixture design that does
not modify shared runtime or migration files.
