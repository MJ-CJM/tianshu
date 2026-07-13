# Tianshu Lean Preview S5 Core Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make self-evolution real and governed for the Lean Candidate: immutable candidates, five domain adapters, one skill-install boundary, evidence-bound fail-closed gates, one promotion authority, persistent challenger assignments, effective overlays, and safe rollback.

**Architecture:** `EvolutionCandidateV1` is the sole cross-domain candidate envelope; adapters validate/materialize domain differences. `GateEvaluator` derives blocking gates only from current evidence. `PromotionService` is the only write authority for canary/promote/rollback. Before dispatch, a deterministic router persists `RunAssignmentV1` and `EffectiveEvolutionOverlayV1` in the same UoW as the run/outbox, so challenger means changed behavior, not a label. Rollback first sets allocation to zero with CAS, then restores artifacts idempotently.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite/WAL, FastAPI, S3 UoW/outbox/Evidence/SystemAudit, pytest, Ruff, mypy, import-linter, React/TypeScript regression tests.

## Global Constraints

- Require S3 Core Gate passed and S4 automation `automation_passed` or an explicitly recorded S4 backend-only handoff. Do not depend on user visual approval to implement S5 backend.
- Normative detailed source is [`docs/codex-v1/plans/14-g4-governed-evolution-executors.md`](../../codex-v1/plans/14-g4-governed-evolution-executors.md). Execute its Increments 1–7 only, plus the Lean Core Gate below. Increments 8–15 are deferred.
- Do not add OpenHands dependencies/adapter, executor SDK/compat suite, FTS ROI benchmark, 100+ cost calibration, full budget-mode Gate, or complete G4-A/B/C report.
- `EvolutionCandidateV1` is the only cross-domain candidate envelope. Memory/skill/policy/persona/code adapters must not create parallel lifecycle/gate/promotion schemas.
- `PromotionService` is the only service that changes champion, allocation, routing version, or lifecycle. Architecture tests must reject direct `UniverseManager.switch()`, `promote_code_variant()`, API, Evolver, CLI, reviewer, or curator writes.
- Code candidates never auto-promote. Any code promote requires an explicit high-risk DecisionRequest and remains outside the golden Lean Demo.
- Challenger allocation must change the real resolved overlay. Persist assignment before dispatch; the same run remains stable across restart/retry.
- Rollback first CASes allocation to zero. Restore verification failure remains `rollback_pending/degraded`; it never reopens challenger traffic.
- All lifecycle/state changes append S3 outbox, S2 SystemAudit, and S3 Evidence references in the same transaction where applicable.
- Lean Core Gate proves candidate/gate/promotion/routing/rollback only. It must state OpenHands/ROI/cost/full G4 are `external_pending` or deferred.

---

### Task 1: Freeze S5 handoff, domain schema, and migrations

**Files:**
- Create: `tests/evolution/test_s4_s5_handoff.py`
- Create: `src/tianshu/models/evolution_candidate.py`
- Create: `src/tianshu/storage/evolution_repo.py`
- Create: `tests/evolution/test_candidate_schema.py`
- Modify: `src/tianshu/storage/migrations.py`

**Interfaces:**

```python
class CandidateKind(StrEnum):
    MEMORY = "memory"
    SKILL = "skill"
    POLICY = "policy"
    PERSONA = "persona"
    CODE = "code"

class CandidateLifecycle(StrEnum):
    PROPOSED = "proposed"
    STAGED = "staged"
    EVALUATING = "evaluating"
    BLOCKED = "blocked"
    READY = "ready"
    CANARY = "canary"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    ARCHIVED = "archived"

class EvolutionCandidateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    candidate_id: str
    kind: CandidateKind
    subject_key: str
    provenance: EvolutionProvenanceV1
    base: CandidateVersionRefV1
    candidate: CandidateVersionRefV1
    diff_artifact_digest: str
    evolution_contract: EvolutionContractV1
    evolution_contract_hash: str
    gate_snapshot_version: int
    evidence_bundle_ids: tuple[str, ...]
    routing: RoutingPolicyV1 | None
    rollback: RollbackSpecV1
    lifecycle: CandidateLifecycle
    version: int
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 1: Write RED handoff/schema/lifecycle tests**

Import S3 Evidence/UoW/Decision/RunState/assignment prerequisites and S4 evolution read contract. Assert strict schema, canonical hash, legal lifecycle graph, automatic promotion literal false, code-promote decision requirement, stale version CAS, and immutable provenance.

- [ ] **Step 2: Append the live candidate migration**

Allocate from actual migration tail; add candidates, gate snapshots, lifecycle journal, promotion journal, routing allocation, and future assignment tables exactly as required by Increments 1/5/6. Do not copy fixed migration numbers.

- [ ] **Step 3: Implement repository CAS and run tests**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_s4_s5_handoff.py tests/evolution/test_candidate_schema.py tests/storage/test_migration_ledger.py tests/storage/test_migration_callback_freeze.py -q
git add src/tianshu/models/evolution_candidate.py src/tianshu/storage/evolution_repo.py src/tianshu/storage/migrations.py tests/evolution/test_s4_s5_handoff.py tests/evolution/test_candidate_schema.py
git commit -m "feat: add the governed evolution candidate domain"
```

### Task 2: Implement five adapters and candidate staging

**Files:**
- Create: `src/tianshu/evolution/__init__.py`
- Create: `src/tianshu/evolution/candidate_service.py`
- Create: `src/tianshu/evolution/adapters/base.py`
- Create: `src/tianshu/evolution/adapters/memory.py`
- Create: `src/tianshu/evolution/adapters/skill.py`
- Create: `src/tianshu/evolution/adapters/policy.py`
- Create: `src/tianshu/evolution/adapters/persona.py`
- Create: `src/tianshu/evolution/adapters/code.py`
- Create: `tests/evolution/test_candidate_adapters.py`

**Interfaces:**

```python
class CandidateAdapter(Protocol):
    kind: CandidateKind
    def validate_source(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1: ...
    def build_diff(self, proposal: CandidateProposalV1) -> ArtifactRefV1: ...
    def stage(self, candidate: EvolutionCandidateV1) -> StagedCandidateV1: ...
    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1: ...
    def rollback(self, candidate: EvolutionCandidateV1) -> RollbackReceiptV1: ...
```

- [ ] **Step 1: Execute baseline Increment 2 RED adapter matrix**

Parameterize all five kinds; assert source digest/provenance, base/candidate canonical refs, diff artifact, contract binding, wrong-adapter rejection, stage idempotency, and no live mutation during propose/stage/evaluate.

- [ ] **Step 2: Implement adapters behind CandidateService**

Adapters retain domain-specific validation/materialization only. CandidateService owns common IDs, provenance, lifecycle transitions and Evidence references. No adapter writes champion/allocation.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_candidate_adapters.py tests/universe tests/skills -q
git add src/tianshu/evolution tests/evolution/test_candidate_adapters.py
git commit -m "feat: stage five evolution candidate kinds"
```

### Task 3: Add fail-closed GateEvaluator and unify skill installation

**Files:**
- Create: `src/tianshu/evolution/gates.py`
- Create: `src/tianshu/skills/install_service.py`
- Create: `tests/evolution/test_gate_evaluator.py`
- Create: `tests/skills/test_install_service_security.py`
- Modify: `src/tianshu/gateway/skills_api.py`
- Modify: `src/tianshu/skills/installer.py`
- Modify: all agent/reviewer/curator/zip/CLI skill write entry points discovered by architecture test
- Modify: `src/tianshu/gateway/evolution_api.py`

**Interfaces:**

```python
class GateEvaluator:
    def evaluate(self, candidate_id: str, *, expected_version: int) -> EvolutionGateReportV1: ...

class SkillInstallService:
    def propose(self, command: ProposeSkillCommand, *, auth: AuthContext) -> EvolutionCandidateV1: ...
    def stage(self, candidate_id: str, *, auth: AuthContext) -> StagedCandidateV1: ...
```

- [ ] **Step 1: Write RED Gate tests**

Required gates are schema, security, regression, sample, evidence, budget, rollback, human veto. Missing/stale/mismatched/corrupt Evidence blocks. Evaluator never trusts a caller-supplied passed boolean and never mutates champion.

- [ ] **Step 2: Write RED skill-entry security tests**

Every API/agent/reviewer/curator/zip/CLI path goes through one `SkillInstallService`; malicious path traversal, symlink, oversized content, invalid SKILL metadata, provenance omission, and direct live write are rejected.

- [ ] **Step 3: Implement minimally and expose read/evaluate API**

Gate API returns blocking gate names/reasons and evidence hashes. Writes require authenticated authority and append audit/outbox. Do not expose promote in this task.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_gate_evaluator.py tests/skills/test_install_service_security.py tests/skills -q
git add src/tianshu/evolution/gates.py src/tianshu/skills/install_service.py src/tianshu/gateway/skills_api.py src/tianshu/skills src/tianshu/gateway/evolution_api.py tests/evolution/test_gate_evaluator.py tests/skills/test_install_service_security.py
git commit -m "feat: gate candidates and unify skill installation"
```

### Task 4: Make PromotionService the sole canary/promote/rollback authority

**Files:**
- Create: `src/tianshu/evolution/promotion.py`
- Create: `tests/evolution/test_promotion_fail_closed.py`
- Create: `tests/architecture/test_promotion_authority.py`
- Modify: `src/tianshu/universe/manager.py`
- Modify: `src/tianshu/universe/evolver.py`
- Modify: `src/tianshu/gateway/universes_api.py`
- Modify: `src/tianshu/gateway/evolution_api.py`

**Interfaces:**

```python
class PromotionService:
    def start_canary(self, candidate_id: str, command: StartCanaryCommand, *, auth: AuthContext) -> PromotionReceiptV1: ...
    def promote(self, candidate_id: str, command: PromoteCommand, *, auth: AuthContext) -> PromotionReceiptV1: ...
    def rollback(self, candidate_id: str, command: RollbackCommand, *, auth: AuthContext) -> RollbackReceiptV1: ...
```

- [ ] **Step 1: Execute baseline Increment 5 RED authority tests**

Reject direct manager/API/Evolver/CLI switch, stale Gate snapshot, blocked candidate, missing reason/Decision, allocation over contract max, code auto-promote, duplicate promote, and stale version. Assert receipt/audit/outbox atomicity.

- [ ] **Step 2: Implement PromotionService**

Start canary and promote require current Gate report plus explicit Decision where contract/risk requires it. Rollback first CASes allocation to zero before adapter restore; every operation is idempotent by command key.

- [ ] **Step 3: Make old APIs delegate or reject**

Keep compatibility reads. Any old mutation endpoint calls PromotionService with AuthContext or returns a stable deprecation/conflict; it never writes the universe store directly.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_promotion_fail_closed.py tests/architecture/test_promotion_authority.py tests/universe/test_promote_code_api.py tests/universe/test_manager.py -q
git add src/tianshu/evolution/promotion.py src/tianshu/universe/manager.py src/tianshu/universe/evolver.py src/tianshu/gateway/universes_api.py src/tianshu/gateway/evolution_api.py tests/evolution/test_promotion_fail_closed.py tests/architecture/test_promotion_authority.py
git commit -m "feat: centralize governed promotion and rollback"
```

### Task 5: Persist real challenger assignment and effective overlays

**Files:**
- Create: `src/tianshu/models/run_assignment.py`
- Create: `src/tianshu/universe/router.py`
- Create: `tests/universe/test_challenger_routing.py`
- Modify: `src/tianshu/storage/evolution_repo.py`
- Modify: `src/tianshu/application/edicts.py`
- Modify: `src/tianshu/application/run_dispatcher.py`
- Modify: prompt/skill/policy/persona/memory resolution paths selected by the five adapters
- Modify: Memorial/Evidence attribution models and APIs

**Interfaces:**

```python
class RunAssignmentV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    assignment_id: str
    memorial_id: str
    candidate_id: str | None
    champion_ref: CandidateVersionRefV1
    selected_ref: CandidateVersionRefV1
    routing_version: int
    bucket: int
    created_at: datetime

class EffectiveEvolutionOverlayV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    assignment_id: str
    kind: CandidateKind | None
    subject_key: str | None
    artifact_digest: str
    canonical_digest: str
```

- [ ] **Step 1: Execute baseline Increment 6 RED tests**

Assert deterministic bucket assignment, allocation boundary, zero-allocation champion, real selected artifact change, same assignment after restart/retry, routing-version attribution, dispatch/outbox/assignment atomicity, and Evidence inclusion.

- [ ] **Step 2: Implement router and bind overlays before dispatch**

Hash stable run key + allocation seed into 0–9999. Persist assignment in the same UoW as run/outbox before any worker sees it. Resolve all candidate-domain behavior through the stored overlay; never re-route a resumed run.

- [ ] **Step 3: Replace `route_for_memorial()` champion-only behavior**

Preserve a compatibility facade if callers depend on the symbol, but have it load/persist the authoritative assignment and return actual selected overlay. Tests assert output behavior/digest differs for challenger, not only IDs.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/universe/test_challenger_routing.py tests/universe/test_routing.py tests/integration/test_continuation_recovery.py tests/evidence -q
git add src/tianshu/models/run_assignment.py src/tianshu/universe/router.py src/tianshu/storage/evolution_repo.py src/tianshu/application src/tianshu/universe src/tianshu/models tests/universe/test_challenger_routing.py
git commit -m "feat: route real challenger overlays before dispatch"
```

### Task 6: Prove distribution, restart safety, and rollback

**Files:**
- Create: `tests/evolution/test_routing_distribution.py`
- Create: `tests/evolution/test_rollback_fault_matrix.py`
- Create: `src/tianshu/evolution/reconciler.py`
- Modify: `src/tianshu/evolution/promotion.py`
- Modify: `src/tianshu/diagnostics.py`

**Interfaces:**
- Consumes: PromotionService, RunAssignment, effect receipts, candidate adapter restore.
- Produces: measured local routing/rollback evidence; not production ROI.

- [ ] **Step 1: Execute baseline Increment 7 RED tests**

Use 10,000 deterministic run keys for configured 10% allocation and assert 9%–11%, with every challenger assignment carrying a changed overlay digest. Fault at allocation-zero commit, restore start, restore complete, receipt append, and process restart. New runs after first rollback commit must always route champion.

- [ ] **Step 2: Implement idempotent rollback reconciler**

Reconstruct from promotion journal and effect receipts. Restore failure keeps `rollback_pending`, readiness degraded, allocation zero. Repeated reconciliation must not duplicate an effective restore.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_routing_distribution.py tests/evolution/test_rollback_fault_matrix.py tests/universe/test_challenger_routing.py -q
git add src/tianshu/evolution/reconciler.py src/tianshu/evolution/promotion.py src/tianshu/diagnostics.py tests/evolution/test_routing_distribution.py tests/evolution/test_rollback_fault_matrix.py
git commit -m "test: prove challenger routing and rollback safety"
```

### Task 7: Automate the Lean Core Evolution Gate

**Files:**
- Create: `scripts/check_s5_lean_evidence.py`
- Create: `tests/evolution/test_s5_lean_gate_contract.py`
- Create: `docs/cc-fable-v1/reports/s5-lean-evolution-report.md`
- Modify: `src/tianshu/gateway/evolution_api.py`
- Modify: `web/src/pages/EvolutionCenterPage.tsx`
- Modify: `web/src/pages/EvolutionCenterPage.test.tsx`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: candidate/gate/promotion/assignment/rollback artifacts.
- Produces: Lean Core Gate `passed`; complete G4 remains not passed.

- [ ] **Step 1: Write RED Gate-checker fixtures**

Reject missing/corrupt Evidence, direct promotion bypass, candidate label with champion overlay, wrong routing version, less than 9% or more than 11%, resumed-run reassignment, rollback reopening traffic, code auto-promotion, missing Decision, fake full-G4 claim, or OpenHands/ROI/cost marked passed.

- [ ] **Step 2: Wire S4 Evolution Center to real read data**

The existing `EvolutionCenterSnapshotV1` now reads candidates, gate blockers, allocation/assignment counts, last gate hash, and rollback state. Mutations call PromotionService and require reason/expected version. Retain all seven page states.

- [ ] **Step 3: Run focused and full Gates**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution tests/universe/test_challenger_routing.py tests/skills/test_install_service_security.py tests/architecture/test_promotion_authority.py -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/lint-imports
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
cd web
npm test -- --run src/pages/EvolutionCenterPage.test.tsx
npm run typecheck
npm run build
cd ..
```

- [ ] **Step 4: Generate report and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python scripts/check_s5_lean_evidence.py --report docs/cc-fable-v1/reports/s5-lean-evolution-report.md
git add scripts/check_s5_lean_evidence.py tests/evolution/test_s5_lean_gate_contract.py docs/cc-fable-v1/reports/s5-lean-evolution-report.md docs/launch/capability-matrix.md docs/cc-fable-v1/PROGRESS.md src/tianshu/gateway/evolution_api.py web/src/pages/EvolutionCenterPage.tsx web/src/pages/EvolutionCenterPage.test.tsx
git commit -m "docs: close the S5 Lean evolution Core Gate"
```

The report title and body must say “Lean Core Gate”, list OpenHands/compat/ROI/cost/full G4 as deferred or `external_pending`, and never emit `G4 passed`.
