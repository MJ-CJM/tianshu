# Phase 4 Governed Evolution & Executor Neutrality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 G1 的安全执行边界、G2 的持久治理与证据、G3 的真实桌面 Web 之上，建立跨 memory/skill/policy/persona/code 的统一演化控制面、会真实改变运行行为的 10% challenger、首个经真实兼容套件证明的外部 managed OpenHands adapter、可重复的 memory/profile 配对收益证据，以及不夸大能力的成本预测与预算治理。

**Architecture:** 控制面以 `EvolutionCandidateV1`、权威 `GateEvaluator` 和唯一写入口 `PromotionService` 统一候选、晋升与回滚；数据面以持久 `RunAssignmentV1` 和不可变 `EffectiveEvolutionOverlayV1` 在派发前固化真实候选行为。所有技能写入口先进入同一安全供应链和候选仓，不直接污染 live skill。Native 与 OpenHands 复用 G1 executor adapter protocol、Governance Contract、G2 Decision/RunState/SideEffect/Evidence schema；mock 只验证客户端契约，只有连接真实、固定版本的 OpenHands SDK/Agent Server 并通过完整能力套件后才允许标记 managed。评测面用固定配对数据集、显式 prompt layer token budget、可校准成本区间和按真实能力区分的 managed/contained/observed enforcement evidence，最终由自动 G4-A/G4-B/G4-C Gate 汇总。

**Tech Stack:** Python 3.12+、Pydantic v2、FastAPI、SQLite/WAL、asyncio、OpenHands SDK/Tools/Workspace/Agent Server 1.32.0、DockerWorkspace/APIRemoteWorkspace、pytest/pytest-asyncio、SciPy-free paired bootstrap、Ruff、mypy、import-linter、uv、React 18、TypeScript、Vitest、Playwright。

## Global Constraints

- 本计划消费且不复制 G1 的 `RequestedGovernanceContractV1`、`EffectiveGovernanceContractV1`、`ExecutorCapabilityManifestV1`、`ExecutionGateway`、`WorkspaceService`，G2 的 `DecisionRequestV1`、`RunStateV1`、`SqliteUnitOfWork`、outbox、side-effect journal、`EvidenceBundleV1`、`ArtifactStore`，以及 G3 已审批的桌面 Web shell、统一错误协议和演化/成本页面契约。
- 开始 Increment 1 前必须运行 G1/G2/G3 handoff tests。任一 consumed contract 不存在或 Gate 未通过时先修正前序阶段，不能在 G4 创建第二套身份、合同、裁决、运行状态、证据、工作区或页面真值。
- `EvolutionCandidateV1` 是 memory、skill、policy、persona、code 唯一的跨对象候选 envelope。各领域通过 adapter 保留校验、物化和回滚差异，不得各自定义另一套 gate、promotion 或 lifecycle。
- `PromotionService` 是改变 live champion、routing allocation 和 candidate lifecycle 的唯一 application service。`UniverseManager.switch()`、`promote_code_variant()`、旧 API、Evolver、CLI、agent tool、reviewer 和 curator 不得直接晋升或写 live。
- 首个公开演化 Demo 只使用 skill candidate。code candidate 可以 propose、隔离评测、显示阻断原因和人工裁决，但永远不能自动晋升；任何“自动修复后自动替换运行代码”的路径必须被 architecture test 阻断。
- configured 10% challenger 必须产生真实 candidate overlay，并在 G2 Unit of Work 内于 dispatch/outbox 之前持久化 assignment。只写 `universe_id`、flag 或 UI 标签而仍加载 champion 的实现不算通过。
- rollback 的安全优先级高于可用性：第一步以 CAS 将新流量 allocation 置零；即使随后进程崩溃，新 run 也必须落 champion。恢复 live artifact 失败时状态为 `rollback_pending/degraded`，不能重新开放 challenger。
- OpenHands mock/fake server 只通过 adapter contract tests，manifest 仍为 `candidate + unverified`。G4 managed 必须使用真实、固定版本的 OpenHands SDK/Agent Server 与隔离 workspace；缺少服务、Docker、镜像 digest、真实 provider 或故障注入证据时 G4-C 失败，不能以 `skip`、`xfail`、录制 JSON 或本地 stub 标绿。
- Keqing 的 Claude Code/Codex headless CLI 始终保持 `contained + experimental`，只声明 G1 已验证的进程外围能力。ACP、CLI wrapper、tmux 或同名 adapter 不得把 action interception、durable decision、receipt 或 hard budget 位升级为 `enforced`。
- OpenHands 依赖使用一组匹配版本：`openhands-sdk==1.32.0`、`openhands-tools==1.32.0`、`openhands-workspace==1.32.0`、`openhands-agent-server==1.32.0`；镜像必须用 `@sha256:` digest。升级版本是独立变更，必须重跑全部 compatibility suite 并更新计划/证据，不能使用 `latest`。
- Prompt 的 persona/profile/peer/memory/skills 层必须有逐层 token budget、tokenizer 方法、裁剪记录和内容 digest。char count 仅作旧配置迁移输入，不能在 UI 或 Evidence 中冒充 token。
- memory/profile ROI 必须是同一任务、模型、provider、seed、contract、executor 和环境的 paired baseline/treatment。mock provider 只能证明 runner 正确，单次主观评价、非配对历史数据或更换模型的对比不能证明收益。
- 成本统一用 `Decimal` 与 CNY；forecast 显示 p10/p50/p90、样本窗口和 calibration 状态，Evidence 同时记录 actual、attribution 和 overrun。终态计量或 iteration hook 不得宣传为 pre-action hard cap。
- managed/contained/observed budget mode 由 effective contract 与 capability manifest 共同决定：mandatory hard enforcement 只有 `budget_enforcement=enforced` 的 managed adapter 可接受；contained 只能 soft-stop 新边界，observed 只能事后记录。
- 每个 Increment 严格执行 RED → GREEN → focused regression → stage regression → commit。测试失败、缺少 external evidence、`skip`、只改 snapshot、只改文案或手工编辑 Gate 报告都不能进入下一阶段。
- 不修改 `web/public/brand.png`、G3 冻结的天枢 header/sidebar/主题/侧栏行为，也不开发手机端。Phase 4 Web 只把真实候选、路由、成本与执行器证据接入已审批桌面页面。
- Phase 4 新增/修改的中文治理动作继续只用 `裁决 / 自动裁决 / 待裁决 / 等待裁决 / 查看并裁决`；不得重新出现 `批红 / 朱批 / 司礼监代批`。

## Source-of-truth and version note

本计划以包内 [Master Roadmap](./00-master-roadmap.md) 与
[G2-G5 recon](../design/24-g2-g5-gap-analysis.md) 为产品/验收参考，并受
[rebaselined execution](./01-rebaselined-execution.md) 的顺序和 Gate 约束。外部 adapter 设计以官方 [OpenHands Remote Agent Server overview](https://docs.openhands.dev/sdk/guides/agent-server/overview)、[Security & Action Confirmation](https://docs.openhands.dev/sdk/guides/security)、[Conversation API](https://docs.openhands.dev/sdk/api-reference/openhands.sdk.conversation)、[Persistence guide](https://docs.openhands.dev/sdk/guides/convo-persistence) 和 [Hooks guide](https://docs.openhands.dev/sdk/guides/hooks) 为协议依据；依赖版本由 PyPI 的 [openhands-sdk 1.32.0](https://pypi.org/project/openhands-sdk/1.32.0/)、[openhands-tools 1.32.0](https://pypi.org/project/openhands-tools/1.32.0/)、[openhands-workspace 1.32.0](https://pypi.org/project/openhands-workspace/1.32.0/) 与 [openhands-agent-server 1.32.0](https://pypi.org/project/openhands-agent-server/1.32.0/) 记录固定。官方文档明确说明 confirmation mode 第一次 `run()` 产生待确认 action、第二次 `run()` 隐式批准执行，拒绝走 `reject_pending_actions()`；也明确指出直接 `execute_tool()` 绕开 conversation confirmation，因此 adapter 禁止使用该旁路。版本存在变化时，固定 1.32.0 的兼容结果优先于 `main/latest` 文档描述。

---

## Fixed G4 Domain Contracts

这些名称和语义在 Increment 1 冻结。若前序阶段已经生成等价类型，做一次兼容迁移并保留下列公开接口；不得长期保留两套候选、路由或成本 schema。

### Evolution candidate, provenance, lifecycle and gates

Create `src/tianshu/models/evolution_candidate.py`:

```python
class CandidateKind(StrEnum):
    MEMORY = "memory"
    SKILL = "skill"
    POLICY = "policy"
    PERSONA = "persona"
    CODE = "code"

class CandidateSourceChannel(StrEnum):
    API = "api"
    CLI = "cli"
    AGENT = "agent"
    REVIEWER = "reviewer"
    CURATOR = "curator"
    ZIP = "zip"
    SYSTEM = "system"

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

class GateName(StrEnum):
    SCHEMA = "schema"
    SECURITY = "security"
    REGRESSION = "regression"
    SAMPLE = "sample"
    EVIDENCE = "evidence"
    BUDGET = "budget"
    ROLLBACK = "rollback"
    HUMAN_VETO = "human_veto"

class GateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"

class EvolutionProvenanceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    source_channel: CandidateSourceChannel
    source_uri_redacted: str | None
    source_digest: str
    actor_principal_id: str
    actor_display_name: str
    originating_edict_id: str | None
    originating_memorial_id: str | None
    producer_name: str
    producer_version: str
    received_at: datetime

class CandidateVersionRefV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str
    artifact_digest: str
    canonical_digest: str

class RoutingPolicyV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allocation_basis_points: int = Field(ge=0, le=10_000)
    allocation_seed_id: str
    routing_version: int

class RollbackSpecV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    champion_ref: CandidateVersionRefV1
    restore_point_ref: str
    adapter_name: str
    max_seconds: int = Field(gt=0, le=60)

class EvolutionContractV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    kind: CandidateKind
    subject_key: str
    governance_contract_hash: str
    required_gates: tuple[GateName, ...]
    regression_policy_artifact_digest: str
    sample_policy_artifact_digest: str
    budget_policy_artifact_digest: str
    minimum_canary_samples: int = Field(gt=0)
    max_canary_allocation_basis_points: int = Field(gt=0, le=1_000)
    rollback_slo_seconds: int = Field(gt=0, le=60)
    automatic_promotion_allowed: Literal[False] = False

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

The legal lifecycle graph is:

```text
proposed -> staged -> evaluating -> blocked | ready
blocked -> evaluating | rejected
ready -> canary | rejected
canary -> ready | promoted | rejected | rollback_pending
promoted -> rollback_pending | archived
rollback_pending -> rolled_back
rolled_back -> archived
rejected -> archived
```

No transition may skip `staged/evaluating`; `code` may reach `ready/canary` for isolated evaluation but `promoted` always requires an explicit high-risk DecisionRequest and can never be triggered by Evolver or automatic policy.

Create `src/tianshu/evolution/gates.py`:

```python
class GateEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    candidate_id: str
    candidate_version: int
    candidate_digest: str
    gate: GateName
    status: GateStatus
    required: bool
    overrideable: bool
    summary: str
    threshold: JsonValue | None
    observed: JsonValue | None
    evidence_artifact_digests: tuple[str, ...]
    evaluated_at: datetime

class GateDecisionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    candidate_id: str
    candidate_version: int
    candidate_digest: str
    gate_snapshot_version: int
    promotion_allowed: bool
    blocking_gates: tuple[GateName, ...]
    overrideable_blocking_gates: tuple[GateName, ...]
    evaluated_at: datetime

class GateEvaluator:
    def evaluate(self, candidate_id: str, *, auth: AuthContext) -> GateDecisionV1: ...
```

Gate membership, thresholds, sample minimum, canary ceiling and rollback SLO are read from the candidate's immutable canonical `EvolutionContractV1`, never current global config or a browser request. Required `PENDING`, `FAILED`, `ERROR`, missing evidence, stale candidate version and stale artifact digest all block. `SCHEMA`, `SECURITY`, `EVIDENCE`, `ROLLBACK` and the prohibition on automatic promotion are non-overrideable invariants. An `override` DecisionRequest may waive only explicitly `overrideable=True` regression/sample/budget thresholds, with reason and immutable evidence; it does not rewrite the original failed result.

### Candidate adapters and authoritative services

Create `src/tianshu/evolution/adapters/protocol.py`:

```python
@dataclass(frozen=True, slots=True)
class StageCandidateCommand:
    kind: CandidateKind
    subject_key: str
    source_artifact_digest: str
    base_version: str
    evolution_contract: EvolutionContractV1
    evolution_contract_hash: str
    provenance: EvolutionProvenanceV1

class EffectiveEvolutionOverlayV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    candidate_id: str
    candidate_kind: CandidateKind
    subject_key: str
    version: str
    artifact_digest: str
    overlay_digest: str
    resource_bindings: dict[str, str]

class AppliedCandidateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    candidate_id: str
    applied_version: str
    live_digest: str
    restore_point_ref: str

class EvolutionAdapter(Protocol):
    kind: CandidateKind
    name: str
    def stage(self, command: StageCandidateCommand) -> EvolutionCandidateV1: ...
    def materialize_overlay(self, candidate: EvolutionCandidateV1) -> EffectiveEvolutionOverlayV1: ...
    def apply(self, candidate: EvolutionCandidateV1, *, expected_live_digest: str) -> AppliedCandidateV1: ...
    def rollback(self, candidate: EvolutionCandidateV1, *, restore_point_ref: str) -> AppliedCandidateV1: ...
    def verify_live(self, subject_key: str, expected_digest: str) -> bool: ...
```

Create `src/tianshu/evolution/candidate_service.py` and `src/tianshu/evolution/promotion.py`:

```python
class CandidateService:
    def propose(self, command: StageCandidateCommand, *, auth: AuthContext) -> EvolutionCandidateV1: ...
    def transition(
        self,
        candidate_id: str,
        target: CandidateLifecycle,
        *,
        expected_version: int,
        auth: AuthContext,
    ) -> EvolutionCandidateV1: ...

class PromotionAction(StrEnum):
    REJECT = "reject"
    OBSERVE = "observe"
    START_CANARY = "start_canary"
    PROMOTE = "promote"
    OVERRIDE_AND_PROMOTE = "override_and_promote"
    ROLLBACK = "rollback"

class PromotionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: PromotionAction
    reason: str
    expected_version: int
    expected_gate_snapshot_version: int
    decision_request_id: str | None
    allocation_basis_points: int | None

class PromotionService:
    def decide(
        self,
        candidate_id: str,
        command: PromotionCommand,
        *,
        auth: AuthContext,
    ) -> EvolutionCandidateV1: ...
    def reconcile_rollbacks(self, *, limit: int = 100) -> tuple[str, ...]: ...
```

`PromotionService.decide()` reloads candidate, gate decision and resolved G2 DecisionRequest inside one Unit of Work; validates version/digests/decision actor and reason; changes routing/live state; writes candidate/promotion journal/system audit/outbox atomically where possible. Filesystem/artifact application uses the G2 side-effect journal. A stale version, missing resolution, expired decision, gate error or adapter verification failure produces no promotion.

### Persistent assignment and actual overlay

Create `src/tianshu/universe/router.py`:

```python
class AssignmentArm(StrEnum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"

class RunAssignmentV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    memorial_id: str
    candidate_id: str | None
    arm: AssignmentArm
    allocation_basis_points: int
    allocation_seed_id: str
    routing_version: int
    bucket: int = Field(ge=0, lt=10_000)
    effective_overlay: EffectiveEvolutionOverlayV1 | None
    effective_overlay_digest: str
    evolution_contract_hash: str
    assigned_at: datetime

class ChallengerRouter:
    def assign(
        self,
        *,
        memorial_id: str,
        subject_key: str,
        evolution_contract_hash: str,
        uow: SqliteUnitOfWork,
    ) -> RunAssignmentV1: ...
    def get(self, memorial_id: str) -> RunAssignmentV1 | None: ...
```

Bucket calculation is exactly `int.from_bytes(HMAC-SHA256(seed_secret, f"{allocation_seed_id}:{memorial_id}".encode()).digest()[:8], "big") % 10_000`; challenger is selected when `bucket < allocation_basis_points`. Store only a key identifier, never the HMAC secret. A retry/restart reads the existing assignment and never rebuckets. Assignment plus overlay digest is persisted before the execution attempt and dispatch outbox row. `EffectiveEvolutionOverlayV1` must be bound into G1 `WorkspaceContext`/executor context and consumed by the skill/persona/policy/memory/code resolver; evidence records the same digest.

### Prompt budget, paired ROI, cost forecast and enforcement

Create `src/tianshu/persona/prompt_budget.py`:

```python
class PromptLayer(StrEnum):
    BASE = "base"
    PERSONA = "persona"
    PROFILE = "profile"
    PEER = "peer"
    MEMORY = "memory"
    SKILLS = "skills"
    TASK_RESERVE = "task_reserve"

class PromptBudgetV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    total_tokens: int = 26_624
    base_tokens: int = 2_048
    persona_tokens: int = 4_096
    profile_tokens: int = 1_024
    peer_tokens: int = 1_024
    memory_tokens: int = 6_144
    skills_tokens: int = 8_192
    task_reserve_tokens: int = 4_096

class PromptLayerUsageV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    layer: PromptLayer
    budget_tokens: int
    used_tokens: int
    tokenizer_id: str
    tokenizer_exact: bool
    source_digests: tuple[str, ...]
    dropped_source_digests: tuple[str, ...]
    truncated: bool

class PromptAssemblyEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    budget: PromptBudgetV1
    layers: tuple[PromptLayerUsageV1, ...]
    total_used_tokens: int
    prompt_digest: str
```

The seven numeric fields must sum exactly to `total_tokens`. Model/provider tokenization is used when available; a conservative fallback is marked `tokenizer_exact=False` and makes hard prompt-budget claims unavailable. The trim order is peer details → recent/low-priority memory → optional skill bodies while retaining the skill index → profile examples. Base identity and persona identity are never silently truncated: an oversize required identity fails prompt assembly with structured evidence.

Create `src/tianshu/cost/forecast.py` and extend `src/tianshu/cost/models.py`:

```python
class ForecastStatus(StrEnum):
    CALIBRATED = "calibrated"
    UNCALIBRATED = "uncalibrated"
    INSUFFICIENT_DATA = "insufficient_data"

class BudgetEnforcementMode(StrEnum):
    MANAGED = "managed"
    CONTAINED = "contained"
    OBSERVED = "observed"

class CostForecastV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    forecast_id: str
    memorial_id: str
    currency: Literal["CNY"] = "CNY"
    p10: Decimal
    p50: Decimal
    p90: Decimal
    status: ForecastStatus
    sample_count: int
    sample_window_start: datetime | None
    sample_window_end: datetime | None
    model: str
    executor_id: str
    candidate_id: str | None
    attribution_key: str
    created_at: datetime

class BudgetEnforcementEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    memorial_id: str
    forecast_id: str
    mode: BudgetEnforcementMode
    boundary: str
    requested_budget: Decimal
    effective_budget: Decimal
    remaining_before: Decimal
    forecast_p90_before: Decimal
    allowed: bool
    denial_reason: str | None
    actual_cost: Decimal | None
    overrun: Decimal | None
    capability_manifest_hash: str
    recorded_at: datetime
```

`overrun = max(Decimal("0"), actual_cost - effective_budget)`. Managed evaluates before every governed provider/tool side-effect and may deny before the action; contained may stop only before a new process/iteration boundary and cannot claim control over an opaque CLI already running; observed never claims a preventive stop. The mode is persisted in RunState/Evidence and displayed verbatim by the API/Web.

---

## Delivery and Gate Map

| Gate | Increments | Exit evidence |
| --- | --- | --- |
| G4-A · Unified candidate and authoritative promotion | 1–5 | Frozen schema/migrations; all five adapters; every skill write entry goes through one guard; direct promotion/writes blocked; fail-closed gate/CAS/rollback journal tests |
| G4-B · Real challenger and reversible traffic | 6–7 | Persist-before-dispatch assignments; actual overlay behavior; restart-stable 10% distribution; fault recovery; 100-cycle measured rollback p95 under 60s in recorded environment |
| G4-C · Executor neutrality, ROI and cost truth | 8–14 | Native/OpenHands same suite; real pinned Agent Server evidence; Keqing remains contained; FTS rebuild; fixed paired ROI evidence; calibrated forecasts; three honest budget modes; real Web readout |
| Automated G4 Gate | 15 | Machine-readable evidence index, signed-by-hash report, no missing/skip external checks, all G1–G4 regressions green |

---

## Increment 1: Freeze G4 handoff, domain schema and migrations

**Files:**

- Create: `src/tianshu/models/evolution_candidate.py`
- Create: `src/tianshu/evolution/__init__.py`
- Create: `src/tianshu/evolution/adapters/__init__.py`
- Create: `src/tianshu/evolution/adapters/protocol.py`
- Create: `src/tianshu/storage/evolution_repo.py`
- Modify: `src/tianshu/models/__init__.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/facade.py`
- Modify: `src/tianshu/storage/mappers.py`
- Test: `tests/integration/test_g3_g4_handoff.py`
- Test: `tests/evolution/test_candidate_schema.py`
- Test: `tests/storage/test_evolution_migrations.py`
- Test: `tests/storage/test_migration_preserves_data.py`

**Migration ownership:** G2 ends at v7. G4 adds append-only v8 `evolution_candidates`, `evolution_gate_evaluations`, `evolution_promotion_journal`, `evolution_routing_configs`, and `run_evolution_assignments`; v9 adds `skill_versions` and `skill_install_provenance`; v10 adds `memory_rebuild_jobs`, `memory_index_provenance`, and `prompt_layer_usage`; v11 adds `cost_forecasts`, `forecast_calibrations`, and `budget_enforcement_evidence`. This Increment implements v8 and reserves the later version numbers in tests/documentation without adding empty migrations.

### 1.1 RED — prove the handoff and strict schema

- [ ] Add `test_g3_g4_handoff.py` that imports the exact G1/G2 contracts named in Global Constraints, checks G1/G2 Gate reports are `passed`, checks G3 has immutable `automation_passed` evidence plus Gate status `user_approval_pending` or `passed`, and checks the `/api/evolution/candidates/{id}/gate` Web contract exists. The test must fail with a named prerequisite rather than silently adapting missing contracts.
- [ ] Add schema tests for every enum/model above: `extra="forbid"`, frozen mutation failure, canonical JSON/hash stability, Decimal/datetime JSON round-trip, invalid digest/version/allocation rejection, contract kind/subject mismatch, canary ceiling above 1,000 rejection, `automatic_promotion_allowed=True` rejection, and large payloads represented only by ArtifactStore digests.
- [ ] Parameterize all legal/illegal lifecycle edges. Explicitly reject `proposed -> promoted`, `blocked -> canary`, `code evaluating -> promoted` without decision, and every transition from archived.
- [ ] Add migration tests upgrading a real v7 fixture with existing universes/memorials/evidence, verifying v8 foreign keys, unique `(candidate_id, gate_snapshot_version, gate)`, unique memorial assignment, CAS version fields and downgrade refusal. Run migration twice and assert idempotency/checksum truth.

Run:

```bash
uv run --frozen pytest tests/integration/test_g3_g4_handoff.py tests/evolution/test_candidate_schema.py tests/storage/test_evolution_migrations.py -q
```

Expected RED: imports/tables/repository methods do not exist; no test is skipped.

### 1.2 GREEN — implement the smallest immutable domain and repository

- [ ] Implement the fixed models and adapter protocol without business behavior, convenience setters or domain-specific promotion fields.
- [ ] Add v8 tables and narrow `EvolutionRepository` methods: `insert_candidate`, `get_candidate`, `save_candidate(expected_version)`, `append_gate_evaluation`, `read_gate_snapshot`, `append_promotion_journal`, `get_routing_config`, `save_routing_config(expected_version)`, `insert_assignment`, `get_assignment`.
- [ ] Repository methods accept a connection/Unit of Work for multi-record operations; do not open nested transactions. Canonical JSON and digests use G2 helpers.
- [ ] Store ArtifactStore URIs/digests only. Add foreign keys to G2 Evidence/Decision/Run identifiers where lifecycle permits; preserve records when source business objects are archived.

### 1.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/integration/test_g3_g4_handoff.py tests/evolution/test_candidate_schema.py tests/storage/test_evolution_migrations.py tests/storage/test_migration_preserves_data.py -q
uv run --frozen ruff check src/tianshu/models/evolution_candidate.py src/tianshu/evolution src/tianshu/storage/evolution_repo.py tests/evolution tests/storage/test_evolution_migrations.py
uv run --frozen mypy src/tianshu/models src/tianshu/evolution src/tianshu/storage
```

Expected GREEN: strict candidate round-trip and v7→v8 migration pass; no existing row changes digest.

Commit:

```bash
git add src/tianshu/models/evolution_candidate.py src/tianshu/models/__init__.py src/tianshu/evolution src/tianshu/storage/evolution_repo.py src/tianshu/storage/migrations.py src/tianshu/storage/facade.py src/tianshu/storage/mappers.py tests/integration/test_g3_g4_handoff.py tests/evolution/test_candidate_schema.py tests/storage/test_evolution_migrations.py tests/storage/test_migration_preserves_data.py
git commit -m "feat: add unified evolution candidate domain"
```

---

## Increment 2: Implement five domain adapters and candidate staging

**Files:**

- Create: `src/tianshu/evolution/candidate_service.py`
- Create: `src/tianshu/evolution/overlay.py`
- Create: `src/tianshu/evolution/adapters/memory.py`
- Create: `src/tianshu/evolution/adapters/skill.py`
- Create: `src/tianshu/evolution/adapters/policy.py`
- Create: `src/tianshu/evolution/adapters/persona.py`
- Create: `src/tianshu/evolution/adapters/code.py`
- Modify: `src/tianshu/evolution/adapters/__init__.py`
- Modify: `src/tianshu/bootstrap/wiring_universe.py`
- Modify: `src/tianshu/universe/evolver.py`
- Modify: `src/tianshu/universe/model.py`
- Test: `tests/evolution/test_candidate_service.py`
- Test: `tests/evolution/test_candidate_adapters.py`
- Test: `tests/evolution/test_overlay_materialization.py`
- Modify tests: `tests/universe/test_evolver.py`, `tests/universe/test_evolver_code.py`

**Adapter boundaries:** memory stages a versioned memory entry/set snapshot; skill stages a complete guarded skill package; policy stages a versioned policy document; persona stages the referenced SOUL/ROLE/profile set; code stages a G1 isolated workspace revision/diff. Every adapter emits the same envelope/digest semantics but may reject commands that are invalid for its domain.

### 2.1 RED — adapters cannot mutate live while staging

- [ ] Parameterize the five adapters over the same `StageCandidateCommand`. Assert base and candidate artifacts exist in G2 ArtifactStore, canonical/diff digests verify, provenance is immutable, lifecycle becomes `staged`, and the corresponding live memory/skill/policy/persona/code digest is byte-identical before and after propose.
- [ ] For each kind, corrupt the source artifact after `ArtifactStore.put()` and assert digest verification fails closed before a candidate row is inserted.
- [ ] Assert `materialize_overlay()` is deterministic: same candidate gives byte-identical overlay/canonical digest; a one-byte candidate change changes both candidate and overlay digest; champion content never appears under a challenger digest.
- [ ] Assert adapter/type mismatch, missing base version, stale base digest, absent restore point, wrong subject key and a candidate equal to base all fail with structured errors and no row/event.
- [ ] Reproduce `UniverseEvolver.run()` calling `manager.switch()` under `universe_auto_promote`. Replace the old success expectation with an assertion that Evolver may propose/evaluate/recommend only and never invokes switch/promotion/live writes for behavior or code.
- [ ] Add a code-specific test proving automatic context cannot transition a code candidate beyond `ready/canary`; even all green gates do not create `promoted` without a separate resolved high-risk decision.

Run:

```bash
uv run --frozen pytest tests/evolution/test_candidate_service.py tests/evolution/test_candidate_adapters.py tests/evolution/test_overlay_materialization.py tests/universe/test_evolver.py tests/universe/test_evolver_code.py -q
```

Expected RED: candidate service/adapters are absent and existing Evolver still has a direct auto-promotion branch.

### 2.2 GREEN — stage immutable candidate artifacts

- [ ] Implement an adapter registry keyed by `CandidateKind`; duplicate/missing registrations fail at bootstrap. `CandidateService.propose()` derives actor from AuthContext and refuses caller-supplied principal fields.
- [ ] Read source bytes once, validate through the domain adapter, write candidate/diff/restore-point artifacts, verify all digests, then insert the `staged` candidate and `evolution.candidate_staged` outbox event in one Unit of Work. If DB insertion fails, unreferenced content-addressed bytes may be garbage-collected later but no candidate becomes visible.
- [ ] Implement deterministic overlays whose `resource_bindings` contain only artifact URIs, version IDs and safe logical names. No overlay contains host absolute paths, raw secrets or unbounded inline content.
- [ ] Map legacy Universe rows to `CandidateKind.PERSONA` or `CODE` through a one-way compatibility reference; do not rewrite existing Universe history. Evolver hands mutations to CandidateService and returns candidate ID plus recommendation.
- [ ] Preserve old `universe_auto_promote` configuration only as a deprecated no-op warning. Remove the live switch call; add docs/test truth that auto generation is not auto promotion.

### 2.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/evolution tests/universe/test_evolver.py tests/universe/test_evolver_code.py -q
uv run --frozen ruff check src/tianshu/evolution src/tianshu/universe/evolver.py tests/evolution
uv run --frozen mypy src/tianshu/evolution
```

Expected GREEN: all five kinds stage/diff/materialize through one contract; zero live digest changes occur during propose.

Commit:

```bash
git add src/tianshu/evolution src/tianshu/bootstrap/wiring_universe.py src/tianshu/universe/evolver.py src/tianshu/universe/model.py tests/evolution tests/universe/test_evolver.py tests/universe/test_evolver_code.py
git commit -m "feat: stage cross-domain evolution candidates"
```

---

## Increment 3: Add the authoritative fail-closed GateEvaluator and gate API

**Files:**

- Create: `src/tianshu/evolution/gates.py`
- Create: `src/tianshu/application/evolution.py`
- Create: `src/tianshu/gateway/evolution_api.py`
- Modify: `src/tianshu/gateway/api.py`
- Modify: `src/tianshu/universe/gate.py`
- Modify: `src/tianshu/universe/eval_harness.py`
- Modify: `src/tianshu/evidence/service.py`
- Test: `tests/evolution/test_gate_evaluator.py`
- Test: `tests/evolution/test_gate_evidence_binding.py`
- Test: `tests/gateway/test_evolution_gate_api.py`
- Modify tests: `tests/universe/test_gate.py`, `tests/universe/test_eval_harness.py`

**API contract:** `GET /api/evolution/candidates/{candidate_id}/gate` returns candidate/version/digest, lifecycle, immutable individual results, `promotion_allowed`, `blocking_gates`, sample/threshold/baseline/delta, evidence links, rollback readiness and routing truth. 404 is absent candidate; a missing evaluator/evidence dependency is 503, never an empty green gate.

### 3.1 RED — every missing or failed required gate blocks

- [ ] Parameterize `SCHEMA`, `SECURITY`, `REGRESSION`, `SAMPLE`, `EVIDENCE`, `BUDGET`, `ROLLBACK`, `HUMAN_VETO` over `pending/failed/error/missing/passed`. Assert any contract-required non-passed result appears once in `blocking_gates` and `promotion_allowed=False`; changing global config or API threshold after staging cannot change the frozen candidate decision.
- [ ] Inject evaluator exceptions/timeouts, corrupt referenced artifact, absent Evidence Bundle, open Evidence draft, failed auditor conclusion, insufficient sample, stale candidate version and mismatched candidate digest. Assert the evaluator appends an `ERROR`/failed result and does not reuse a prior green snapshot.
- [ ] Prove an old green snapshot becomes stale after candidate artifact/version changes; PromotionService will later be required to compare both candidate version and gate snapshot version.
- [ ] Prove an override request cannot waive schema, security, evidence integrity, rollback or code-auto invariant. It may only identify explicitly overrideable regression/sample/budget blockers and leaves original evaluations immutable.
- [ ] Bind gate evidence to G2 `EvidenceBundleV1`: check run assignment/candidate/overlay digest, requested/effective contract hashes, executor manifest, cost evidence, tests/artifacts and independent auditor. A UI-provided score or legacy `fitness` field is not authoritative evidence.
- [ ] Add API tests for AuthContext/scopes, correlation IDs, 404/409/503 mapping and exact G3 response fields. Assert the browser cannot post `promotion_allowed` or actor identity.

Run:

```bash
uv run --frozen pytest tests/evolution/test_gate_evaluator.py tests/evolution/test_gate_evidence_binding.py tests/gateway/test_evolution_gate_api.py -q
```

Expected RED: no unified evaluator/API exists and legacy Universe gate cannot bind all G2 evidence.

### 3.2 GREEN — evaluate immutable snapshots, never infer success

- [ ] Implement pure gate evaluators plus an orchestrating `GateEvaluator`. Each evaluator returns one typed result; orchestration catches exceptions only to persist `ERROR`, never to return pass.
- [ ] Append all results and update the candidate's `gate_snapshot_version/lifecycle` with CAS in one Unit of Work. Concurrent evaluations may both calculate, but only one snapshot becomes current; the loser reloads and cannot overwrite.
- [ ] Make legacy Universe evaluation feed regression/sample observations into the unified evaluator. Remove any direct “fitness above threshold means promoted” decision.
- [ ] Implement the read application service/API and register it once. Return immutable evidence URLs/digests rather than embedding arbitrary logs.
- [ ] Ensure `promotion_allowed=True` requires all required gates passed against the current candidate version/digest and no unresolved human veto.

### 3.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/evolution/test_gate_evaluator.py tests/evolution/test_gate_evidence_binding.py tests/gateway/test_evolution_gate_api.py tests/universe/test_gate.py tests/universe/test_eval_harness.py -q
uv run --frozen ruff check src/tianshu/evolution/gates.py src/tianshu/application/evolution.py src/tianshu/gateway/evolution_api.py tests/evolution tests/gateway/test_evolution_gate_api.py
uv run --frozen mypy src/tianshu/evolution src/tianshu/application/evolution.py
```

Expected GREEN: the only green decision is a current, complete, evidence-bound gate snapshot; every exception blocks.

Commit:

```bash
git add src/tianshu/evolution/gates.py src/tianshu/application/evolution.py src/tianshu/gateway/evolution_api.py src/tianshu/gateway/api.py src/tianshu/universe/gate.py src/tianshu/universe/eval_harness.py src/tianshu/evidence tests/evolution tests/gateway/test_evolution_gate_api.py tests/universe/test_gate.py tests/universe/test_eval_harness.py
git commit -m "feat: enforce evidence-bound evolution gates"
```

---

## Increment 4: Unify the complete skill supply chain behind SkillInstallService

**Files:**

- Create: `src/tianshu/skills/install_service.py`
- Create: `src/tianshu/skills/repository.py`
- Create: `src/tianshu/cli/commands/skills.py`
- Modify: `src/tianshu/skills/installer.py`
- Modify: `src/tianshu/skills/loader.py`
- Modify: `src/tianshu/skills/guard.py`
- Modify: `src/tianshu/skills/reviewer.py`
- Modify: `src/tianshu/skills/curator.py`
- Modify: `src/tianshu/tools/skill_tools.py`
- Modify: `src/tianshu/gateway/skills_api.py`
- Modify: `src/tianshu/cli/client.py`
- Modify: `src/tianshu/cli/main.py`
- Modify: `src/tianshu/bootstrap/wiring_skills.py`
- Modify: `src/tianshu/storage/migrations.py`
- Test: `tests/skills/test_install_service_security.py`
- Test: `tests/skills/test_install_service_channels.py`
- Test: `tests/skills/test_skill_version_rollback.py`
- Test: `tests/architecture/test_no_direct_skill_writes.py`
- Modify tests: `tests/skills/test_curator.py`, `tests/skills/test_iteration.py`, `tests/skills/test_loader_multifile.py`
- Test: `tests/gateway/test_skills_candidate_api.py`
- Test: `tests/cli/test_skills_command.py`

**Command contract:**

```python
class SkillMutation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    INSTALL = "install"
    ARCHIVE = "archive"
    DELETE = "delete"

class SkillSourceType(StrEnum):
    INLINE = "inline"
    ZIP = "zip"

class InstallSkillCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mutation: SkillMutation
    name: str
    source_type: SkillSourceType
    source_artifact_digest: str
    expected_base_version: str | None
    originating_edict_id: str | None
    originating_memorial_id: str | None

class SkillInstallService:
    def propose(self, command: InstallSkillCommand, *, auth: AuthContext) -> EvolutionCandidateV1: ...
```

HTTP/CLI upload bytes or inline content; they never send a server-local filesystem path. Read-only `GET /skills` and `GET /skills/{name}` remain live views. `POST /skills`, `PUT /skills/{name}`, archive/delete, agent `skill_manage`, reviewer and curator return/provide candidate IDs and do not make content immediately visible.

### 4.1 RED — prove no channel can bypass package security or provenance

- [ ] Build one malicious corpus and run it through API inline, API zip, CLI upload, agent tool, reviewer and curator: `../`/absolute/drive paths, backslash traversal, Unicode-normalization filename collision, duplicate ZIP member, symlink, nested archive, entry-count/expanded-size/compression-ratio bomb, oversized file, invalid UTF-8 SKILL.md, missing frontmatter/name mismatch, forbidden executable/binary, secret-bearing content and existing Guard deny patterns.
- [ ] Assert every channel returns the same stable error code, creates no candidate/version/live file, and appends a redacted security audit without storing malicious bytes outside ArtifactStore quarantine.
- [ ] Add positive channel tests proving identical bytes produce identical candidate digest but distinct provenance actor/channel/origin. API/CLI actor is derived from AuthContext/token; agent/reviewer/curator carry the originating run but cannot invent principal identity.
- [ ] Prove propose does not change `SkillsLoader.list_all_metadata()`, prompt output or live directory. Only a later PromotionService apply may expose a staged version.
- [ ] Reproduce direct `SkillsLoader.create/save/patch/delete/archive` calls in `skills_api.py`, `skill_tools.py`, `reviewer.py` and `curator.py`; architecture test scans these modules and fails until only `SkillInstallService` is used.
- [ ] Add v9 migration/version tests: immutable `(skill_name, version)` artifact/provenance rows, one current live pointer, base-version CAS, builtin mutation denial, and exact rollback to prior digest.

Run:

```bash
uv run --frozen pytest tests/skills/test_install_service_security.py tests/skills/test_install_service_channels.py tests/skills/test_skill_version_rollback.py tests/architecture/test_no_direct_skill_writes.py tests/gateway/test_skills_candidate_api.py tests/cli/test_skills_command.py -q
```

Expected RED: current API/tools/reviewer/curator write live through `SkillsLoader`; zip guard is not a single enforced entry.

### 4.2 GREEN — validate once, stage once, expose only after promotion

- [ ] Split `SkillRepository` into the only low-level filesystem/version pointer implementation and make public `SkillsLoader` mutation methods private compatibility shims callable only by the service during the migration. The architecture allowlist names exact repository/service files, not the whole skills directory.
- [ ] Make Installer validate the complete package tree, not only SKILL.md. Stream ZIP extraction into a bounded quarantine directory under G1 WorkspaceService, reject symlinks/path escapes before writes, and content-address the accepted package.
- [ ] `SkillInstallService.propose()` verifies base version, runs package Guard, writes ArtifactStore/provenance/v9 version row, and delegates to CandidateService. It never updates the live pointer.
- [ ] Convert all mutation channels. Existing API responses become HTTP 202 `{candidate_id, lifecycle:"staged", live_changed:false}`; CLI prints the same truth. Curator `dry_run=true` stays preview-only, while `dry_run=false` proposes candidates.
- [ ] Bind agent-originated proposals to the active Governance Contract and Decision policy. An agent may propose but cannot resolve its own promotion decision through the tool.
- [ ] Keep live pointer updates atomic (`temp symlink/manifest -> fsync -> rename -> parent fsync`) inside the skill adapter's `apply/rollback`, with digest verification before and after.

### 4.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/skills tests/gateway/test_skills_candidate_api.py tests/cli/test_skills_command.py tests/architecture/test_no_direct_skill_writes.py -q
uv run --frozen ruff check src/tianshu/skills src/tianshu/gateway/skills_api.py src/tianshu/tools/skill_tools.py src/tianshu/cli/commands/skills.py tests/skills tests/architecture/test_no_direct_skill_writes.py
uv run --frozen mypy src/tianshu/skills
```

Expected GREEN: all six channels share identical security result/digest semantics, no proposal is live, and the architecture test finds no bypass.

Commit:

```bash
git add src/tianshu/skills src/tianshu/gateway/skills_api.py src/tianshu/tools/skill_tools.py src/tianshu/cli/commands/skills.py src/tianshu/cli/client.py src/tianshu/cli/main.py src/tianshu/bootstrap/wiring_skills.py src/tianshu/storage/migrations.py tests/skills tests/architecture/test_no_direct_skill_writes.py tests/gateway/test_skills_candidate_api.py tests/cli/test_skills_command.py
git commit -m "feat: govern every skill mutation through candidates"
```

---

## Increment 5: Make PromotionService the sole switch/promote/rollback authority

**Files:**

- Create: `src/tianshu/evolution/promotion.py`
- Create: `src/tianshu/evolution/rollback_reconciler.py`
- Modify: `src/tianshu/application/evolution.py`
- Modify: `src/tianshu/gateway/evolution_api.py`
- Modify: `src/tianshu/gateway/universes_api.py`
- Modify: `src/tianshu/universe/manager.py`
- Modify: `src/tianshu/universe/evolver.py`
- Modify: `src/tianshu/storage/evolution_repo.py`
- Modify: `src/tianshu/bootstrap/wiring_universe.py`
- Test: `tests/evolution/test_promotion_fail_closed.py`
- Test: `tests/evolution/test_promotion_cas.py`
- Test: `tests/evolution/test_promotion_rollback.py`
- Test: `tests/evolution/test_code_never_auto_promotes.py`
- Test: `tests/architecture/test_promotion_authority.py`
- Modify tests: `tests/universe/test_universe_api.py`, `tests/universe/test_promote_code_api.py`, `tests/universe/test_manager.py`, `tests/universe/test_manager_code.py`

**Decision API semantics:** a candidate reaching `ready` has a pending G2 `DecisionRequestV1(kind=GOVERNED_APPLY)` whose payload contains candidate/version/digest/gate snapshot/action set. `POST /api/evolution/candidates/{id}/decision` accepts `{action, reason, expected_version}` only; the application service derives actor, creates a distinct request for `override` when required, resolves through `DecisionService`, re-runs GateEvaluator so `HUMAN_VETO` is evaluated against that immutable resolution, then drives PromotionService idempotently from the resolved decision/outbox. The candidate endpoint never updates DB/files directly.

### 5.1 RED — direct, stale and partially evidenced promotion is impossible

- [ ] Parameterize promotion over missing/failed/error/stale gates, missing Evidence Bundle, failed auditor, missing/expired/unresolved decision, blank reason, stale candidate/version/gate snapshot/live digest, wrong actor scope and adapter verification failure. Assert zero live/routing/lifecycle change and one redacted denial audit.
- [ ] Race two valid promote requests with the same expected version. Exactly one wins; the other receives 409 and cannot apply files, append a second effective promotion or demote the new champion.
- [ ] Inject crashes at: after decision resolution before outbox; after promotion intent before adapter apply; after apply before receipt; after receipt before candidate CAS; after candidate CAS before outbox ack. On restart, side-effect receipt/reconciler produces one effective result or a safe `rollback_pending`, never a silent split brain.
- [ ] For rollback, prove allocation becomes zero before artifact restoration. Crash after zeroing leaves all newly assigned runs on champion and the candidate `rollback_pending`; restart completes exact digest restoration and one journal entry.
- [ ] Prove code cannot auto-promote through Evolver, `manager.switch`, `promote_code_variant`, feature flag, API or agent tool. A manual code promotion needs current non-overrideable gates plus its own resolved high-risk decision.
- [ ] Make `test_promotion_authority.py` parse imports/AST and permit live mutation/routing allocation changes only in PromotionService, adapter low-level apply/rollback and repository primitives. No directory-wide exemptions.
- [ ] Update legacy `/universes/{id}/switch` and `/promote-code` tests: they resolve a mapped candidate through PromotionService or return structured 409 `promotion_preconditions_not_met`; they no longer directly switch.

Run:

```bash
uv run --frozen pytest tests/evolution/test_promotion_fail_closed.py tests/evolution/test_promotion_cas.py tests/evolution/test_promotion_rollback.py tests/evolution/test_code_never_auto_promotes.py tests/architecture/test_promotion_authority.py tests/universe/test_universe_api.py tests/universe/test_promote_code_api.py -q
```

Expected RED: current manager/API/Evolver have direct mutation paths and no durable promotion journal/reconciler.

### 5.2 GREEN — one decision path and recoverable apply/rollback

- [ ] Implement PromotionService exactly as the fixed contract. Validate current candidate/gate/decision in a Unit of Work; persist a side-effect intent before adapter apply; persist receipt/live digest before final lifecycle/outbox state.
- [ ] `START_CANARY` only changes routing config after rollback readiness and current gates pass. `PROMOTE` first closes challenger traffic, then atomically applies candidate live version, verifies digest, updates champion and marks promoted. `ROLLBACK` first CAS-zeroes routing, then restores and verifies champion digest.
- [ ] Implement idempotent recovery keyed by `(candidate_id, action, expected_version, decision_request_id)`. Replaying the same resolved decision returns the existing outcome; a different payload with the same key conflicts.
- [ ] Route all new and compatibility APIs through the application service. Responses include candidate version, gate snapshot, decision ID, promotion journal ID and routing truth.
- [ ] Turn `UniverseManager.switch/promote_code_variant` into private adapter primitives or remove them after migrating callers; remove Evolver's direct call. Keep read/branch/archive compatibility unrelated to promotion.
- [ ] Emit durable `evolution.canary_started`, `evolution.promoted`, `evolution.rollback_started/completed/failed` events through G2 outbox and bind them to system audit/Evidence.

### 5.3 Verify G4-A and commit

Run:

```bash
uv run --frozen pytest tests/evolution tests/skills tests/architecture/test_no_direct_skill_writes.py tests/architecture/test_promotion_authority.py tests/universe -q
uv run --frozen ruff check src/tianshu/evolution src/tianshu/skills src/tianshu/universe src/tianshu/gateway/evolution_api.py tests/evolution tests/skills
uv run --frozen mypy src/tianshu/evolution src/tianshu/skills
uv run --frozen lint-imports
```

Expected GREEN: G4-A candidate, skill supply chain and authoritative promotion tests pass; all direct bypass fixtures fail closed.

Commit:

```bash
git add src/tianshu/evolution src/tianshu/application/evolution.py src/tianshu/gateway/evolution_api.py src/tianshu/gateway/universes_api.py src/tianshu/universe/manager.py src/tianshu/universe/evolver.py src/tianshu/storage/evolution_repo.py src/tianshu/bootstrap/wiring_universe.py tests/evolution tests/architecture/test_promotion_authority.py tests/universe
git commit -m "feat: centralize promotion and rollback authority"
```

---

## Increment 6: Persist real challenger assignments and bind actual overlays before dispatch

**Files:**

- Create: `src/tianshu/universe/router.py`
- Create: `src/tianshu/evolution/runtime_context.py`
- Create: `src/tianshu/models/evolution_evidence.py`
- Modify: `src/tianshu/evolution/overlay.py`
- Modify: `src/tianshu/application/edicts.py`
- Modify: `src/tianshu/application/dispatcher.py`
- Modify: `src/tianshu/storage/evolution_repo.py`
- Modify: `src/tianshu/executor/workspace_context.py`
- Modify: `src/tianshu/executor/executor.py`
- Modify: `src/tianshu/skills/loader.py`
- Modify: `src/tianshu/persona/prompt_builder.py`
- Modify: `src/tianshu/executor/policy_hook.py`
- Modify: `src/tianshu/memory/manager.py`
- Modify: `src/tianshu/universe/launcher.py`
- Modify: `src/tianshu/models/memorial.py`
- Modify: `src/tianshu/storage/memorial_repo.py`
- Modify: `src/tianshu/application/evidence.py`
- Modify: `src/tianshu/gateway/evolution_api.py`
- Modify: `src/tianshu/bootstrap/wiring_universe.py`
- Test: `tests/universe/test_challenger_routing.py`
- Test: `tests/universe/test_assignment_durability.py`
- Test: `tests/evolution/test_runtime_overlay_resolution.py`
- Test: `tests/integration/test_assignment_before_dispatch.py`
- Test: `tests/integration/test_assignment_evidence.py`
- Modify tests: `tests/universe/test_routing.py`, `tests/universe/test_memorial_universe.py`, `tests/executor/test_workspace_staging.py`

**Evidence contract:** create `EvolutionRunEvidenceV1` containing the exact `RunAssignmentV1`, champion/candidate version refs, effective overlay digest, routing config digest and overlay verification result. Store its canonical JSON in ArtifactStore and include it in G2 `EvidenceBundleV1.artifacts` with role `evolution.assignment.v1`; do not mutate G2's frozen 1.0 envelope or hide attribution in free text.

### 6.1 RED — assignment is durable before any attempt and changes resources

- [ ] With a fixed HMAC test key, seed ID and 1,000 basis points, assert the specified bucket algorithm exactly. Boundary buckets 999 select challenger and 1000 champion; 0/10,000 allocations select none/all.
- [ ] Call assign twice and after a full Storage/router restart for the same memorial. Assert byte-identical assignment, bucket, routing version and overlay digest even after seed/config rotation.
- [ ] Inject failure while inserting assignment. Assert no execution attempt, dispatch outbox, provider request, process or workspace lease exists. Then retry the idempotent submission and get one assignment plus one effective attempt.
- [ ] Inject overlay materialization/digest failure after a challenger bucket is chosen. The run must fail before side effects with `candidate_overlay_unavailable`; it must not silently execute champion under challenger attribution or rebucket.
- [ ] Parameterize one visible sentinel change for every kind: memory recall set, skill prompt/tool availability, policy decision, persona prompt, code workspace revision. Assert challenger run consumes the candidate sentinel/digest and champion run does not.
- [ ] For the public skill path, install a candidate whose deterministic mock output contains a unique behavior marker. Assert only challenger assignments yield that marker and the Evidence artifact has the same candidate/overlay digest.
- [ ] Assert retries/follow-ups inherit or explicitly create a new assignment according to the evolution contract; a retry of one memorial never changes arm, while a new memorial is independently bucketed.
- [ ] Assert `memorial.universe_id` remains a compatibility projection for legacy persona/code Universes only. It cannot be used to infer skill/memory/policy assignment or overwrite `RunAssignmentV1`.

Run:

```bash
uv run --frozen pytest tests/universe/test_challenger_routing.py tests/universe/test_assignment_durability.py tests/evolution/test_runtime_overlay_resolution.py tests/integration/test_assignment_before_dispatch.py tests/integration/test_assignment_evidence.py -q
```

Expected RED: `route_for_memorial()` always returns champion, no assignment row exists, and executor loads only live resources.

### 6.2 GREEN — route once, persist once, resolve the selected resources

- [ ] Implement ChallengerRouter with the exact HMAC bucket function and routing config CAS. It receives the G1 secret by reference at runtime and records only the allocation seed ID.
- [ ] Move routing from `Executor.execute()` into the G2 submission/dispatch Unit of Work: create Memorial → assign/materialize immutable overlay → persist assignment → create RunState/attempt → append dispatch outbox. Existing assignment wins on idempotent retry.
- [ ] Bind `EvolutionRuntimeContext(assignment, overlay)` alongside G1 WorkspaceContext. Resource loaders resolve candidate artifact first only when the binding's kind/subject matches; otherwise they resolve the champion live version. They verify the artifact and overlay digest on every new attempt.
- [ ] For code overlays, bind the candidate revision to an isolated WorkspaceService lease; never execute a code candidate in the source checkout. For skill/persona/policy/memory overlays, use immutable per-run resource views rather than changing the process-global loader cache.
- [ ] Remove `UniverseManager.route_for_memorial()` as an authority; retain a deprecated read adapter only if existing callers require it. Delete the “always champion” test and replace it with assignment truth.
- [ ] Add `GET /api/evolution/runs/{memorial_id}/assignment` with AuthContext/scopes and G3 error semantics. Return logical refs/digests, never the HMAC key or host paths.
- [ ] During Evidence close, canonicalize and store `EvolutionRunEvidenceV1`; fail close if assignment and effective runtime overlay differ.

### 6.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/universe/test_challenger_routing.py tests/universe/test_assignment_durability.py tests/evolution/test_runtime_overlay_resolution.py tests/integration/test_assignment_before_dispatch.py tests/integration/test_assignment_evidence.py tests/universe/test_memorial_universe.py tests/executor/test_workspace_staging.py -q
uv run --frozen ruff check src/tianshu/universe/router.py src/tianshu/evolution/runtime_context.py src/tianshu/models/evolution_evidence.py tests/universe/test_challenger_routing.py tests/integration/test_assignment_before_dispatch.py
uv run --frozen mypy src/tianshu/universe/router.py src/tianshu/evolution src/tianshu/models/evolution_evidence.py
```

Expected GREEN: assignment is durable before dispatch and candidate selection demonstrably changes effective resources/output with matching evidence.

Commit:

```bash
git add src/tianshu/universe/router.py src/tianshu/evolution src/tianshu/models/evolution_evidence.py src/tianshu/application/edicts.py src/tianshu/application/dispatcher.py src/tianshu/application/evidence.py src/tianshu/storage/evolution_repo.py src/tianshu/executor/workspace_context.py src/tianshu/executor/executor.py src/tianshu/executor/policy_hook.py src/tianshu/skills/loader.py src/tianshu/persona/prompt_builder.py src/tianshu/memory/manager.py src/tianshu/universe/launcher.py src/tianshu/models/memorial.py src/tianshu/storage/memorial_repo.py src/tianshu/gateway/evolution_api.py src/tianshu/bootstrap/wiring_universe.py tests/universe tests/evolution/test_runtime_overlay_resolution.py tests/integration/test_assignment_before_dispatch.py tests/integration/test_assignment_evidence.py
git commit -m "feat: route durable challenger overlays"
```

---

## Increment 7: Prove 10% statistics, restart safety and measured rollback latency

**Files:**

- Create: `tests/universe/test_challenger_routing_statistics.py`
- Create: `tests/integration/test_challenger_routing_faults.py`
- Create: `tests/integration/test_challenger_rollback_faults.py`
- Create: `scripts/benchmarks/g4_rollback_latency.py`
- Create: `tests/benchmarks/test_g4_rollback_harness.py`
- Create: `docs/launch/benchmarks/.gitkeep`
- Modify: `pyproject.toml`
- Modify: `src/tianshu/evolution/promotion.py`
- Modify: `src/tianshu/evolution/rollback_reconciler.py`
- Modify: `src/tianshu/universe/router.py`
- Modify: `src/tianshu/storage/evolution_repo.py`

**Defined local benchmark environment:** SQLite WAL on a local filesystem; one process; 10,000 existing assignment rows; one complete skill candidate package no larger than 1 MiB; 100 rollback cycles; monotonic timing from accepted rollback command to both routing version with allocation zero and verified champion live digest. The artifact records OS, architecture, Python, Tianshu/dependency lock hash, CPU model, memory, filesystem, DB size, candidate size, sample count, warmup count and every latency. The acceptance applies only to the recorded environment.

### 7.1 RED — statistical and failure injection proof

- [ ] Route exactly `mem-00000` through `mem-09999` with fixed key/seed at 1,000 basis points. Assert observed proportion is in `[0.09, 0.11]` and the Wilson 95% interval contains `0.10`; persist raw bucket counts in the test artifact.
- [ ] For every selected challenger, assert assignment, runtime overlay and Evidence candidate ID/digest match. A distribution-only test without behavior/attribution assertions is insufficient.
- [ ] Test allocation 0, 1, 9,999 and 10,000 basis points plus multiple active subject keys. There is at most one selected candidate per subject, and routing config version/digest determines the exact candidate set.
- [ ] Kill/recreate router/storage after 37%, 63% and 100% of assignments. Existing rows remain identical and aggregate proportion remains in bounds; no seed rotation changes persisted rows.
- [ ] Inject rollback crashes before zero-allocation CAS, after zero CAS, after restore intent, after live apply, after receipt and before final lifecycle CAS. Only the pre-CAS case may still route according to the old valid config; every post-CAS case routes new runs to champion and reconciles without duplicate apply.
- [ ] Add a concurrency test racing assignment with rollback CAS. A run sees exactly one routing version: if committed before zero it may be challenger and retains attribution; if after zero it is champion. No assignment may combine the old arm with the new routing version.
- [ ] Make the benchmark harness test reject fewer than 100 measured cycles, missing environment fields, wall-clock rather than monotonic timing, omitted raw samples, any failed digest check, or a hand-authored p95.

Run:

```bash
uv run --frozen pytest tests/universe/test_challenger_routing_statistics.py tests/integration/test_challenger_routing_faults.py tests/integration/test_challenger_rollback_faults.py tests/benchmarks/test_g4_rollback_harness.py -q
```

Expected RED: restart-safe assignment/rollback fault handling and evidence-producing benchmark do not exist.

### 7.2 GREEN — reconcile safely and measure the actual system

- [ ] Use routing-config version CAS and the same SqliteUnitOfWork as assignment insert, so rollback/assignment races serialize to one version.
- [ ] Make in-flight challenger runs keep their immutable assignment/evidence; they are not relabeled champion. At the next governed side-effect safe point, effective contract decides continue/pause/cancel. New runs stop immediately after allocation zero.
- [ ] Implement rollback reconciler idempotently from promotion journal plus side-effect receipts. Verification failure remains `rollback_pending` and readiness reports degraded; it never reopens traffic.
- [ ] Register pytest marker `benchmark` in `pyproject.toml`; normal focused tests may exclude it, but G4-B invokes the real harness explicitly and cannot replace it with a skipped test.
- [ ] Implement the benchmark script to construct the defined real local state, warm up 5 cycles not counted, run 100 measured cycles, verify each digest/config, calculate nearest-rank p50/p95/p99 from raw samples and write canonical JSON under `docs/launch/benchmarks/g4-rollback-<environment_fingerprint>.json`.
- [ ] Exit nonzero when p95 is `>= 60.0` seconds or any sample fails. Do not round before comparison. The generated artifact is immutable input to the final G4 Gate.

### 7.3 Verify G4-B and commit

Run:

```bash
uv run --frozen pytest tests/universe/test_challenger_routing.py tests/universe/test_challenger_routing_statistics.py tests/integration/test_challenger_routing_faults.py tests/integration/test_challenger_rollback_faults.py tests/benchmarks/test_g4_rollback_harness.py -q
uv run --frozen python scripts/benchmarks/g4_rollback_latency.py --cycles 100 --assignments 10000 --max-candidate-bytes 1048576 --output docs/launch/benchmarks
uv run --frozen ruff check src/tianshu/universe/router.py src/tianshu/evolution/promotion.py src/tianshu/evolution/rollback_reconciler.py scripts/benchmarks/g4_rollback_latency.py tests/universe/test_challenger_routing_statistics.py tests/integration/test_challenger_rollback_faults.py
```

Expected GREEN: 10,000-run distribution/attribution passes; all fault points recover safely; generated benchmark reports 100 verified samples and p95 `< 60.0s` in the recorded environment.

Commit:

```bash
git add src/tianshu/universe/router.py src/tianshu/evolution/promotion.py src/tianshu/evolution/rollback_reconciler.py src/tianshu/storage/evolution_repo.py scripts/benchmarks/g4_rollback_latency.py tests/universe/test_challenger_routing_statistics.py tests/integration/test_challenger_routing_faults.py tests/integration/test_challenger_rollback_faults.py tests/benchmarks/test_g4_rollback_harness.py docs/launch/benchmarks pyproject.toml
git commit -m "test: prove challenger routing and rollback latency"
```

---

## Increment 8: Freeze the executor-neutral compatibility suite and truth model

**Files:**

- Create: `src/tianshu/executor/adapters/compatibility.py`
- Create: `src/tianshu/models/executor_compatibility.py`
- Modify: `src/tianshu/executor/adapters/protocol.py`
- Modify: `src/tianshu/executor/capabilities.py`
- Create: `src/tianshu/executor/adapters/native.py`
- Modify: `src/tianshu/executor/keqing/adapter.py`
- Create: `tests/compat/executor_adapter/__init__.py`
- Create: `tests/compat/executor_adapter/conftest.py`
- Create: `tests/compat/executor_adapter/suite.py`
- Create: `tests/compat/executor_adapter/test_native.py`
- Create: `tests/compat/executor_adapter/test_negative_dispatch.py`
- Create: `tests/compat/executor_adapter/test_keqing_truth.py`
- Create: `docs/usage/executor-adapter.md`
- Modify: `docs/launch/capability-matrix.md`

**Compatibility output:**

```python
class CompatibilityStatus(StrEnum):
    CANDIDATE = "candidate"
    CONTRACT_VERIFIED = "contract_verified"
    EXTERNAL_VERIFIED = "external_verified"
    FAILED = "failed"

class CapabilityProbeV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    capability: str
    requested_state: CapabilityState
    observed_state: CapabilityState
    passed: bool
    evidence_artifact_digests: tuple[str, ...]
    limitation: str | None

class ExecutorCompatibilityReportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    executor_id: str
    adapter_version: str
    deployment_fingerprint: str
    status: CompatibilityStatus
    requested_contract_hash: str
    effective_contract_hash: str
    probes: tuple[CapabilityProbeV1, ...]
    failed_mandatory_capabilities: tuple[str, ...]
    environment_artifact_digest: str
    created_at: datetime
```

The suite covers G1 capability names exactly: action interception, workspace/network/secret control, budget enforcement, decision bridge, pause, durable resume, event fidelity, artifact export, receipts, restore point and governed apply. A capability is derived from probe evidence, never from adapter self-description.

### 8.1 RED — the same suite must catch honest negative adapters

- [ ] Build reusable black-box probes that accept only the G1 adapter protocol, requested contract, temporary workspace and fault controller. Tests must not inspect concrete adapter classes to decide pass/fail.
- [ ] For each capability, provide a deliberately broken adapter fixture that advertises `enforced` but violates the behavior. Assert the probe fails and dispatcher rejects mandatory mismatch before process/workspace/provider side effects.
- [ ] Run the suite against G1 Native and prove every `enforced` bit has an artifact/receipt/fault test. If Native's current manifest overstates a bit, downgrade it or fix the G1 implementation; never relax the probe.
- [ ] Add cross-executor schema tests: Native and a generic adapter receive byte-identical requested contract, emit typed events accepted by the same schema, persist effective contract/manifest hashes, and close a G2 Evidence Bundle through the same service.
- [ ] Prove unknown event type, event loss/reorder beyond protocol rules, missing terminal receipt, duplicate side effect, secret sentinel leakage, stale restore point and resume from an uncommitted RunState fail their corresponding probe.
- [ ] Freeze Keqing truth: Claude Code and Codex headless have `contained + experimental`; opaque internal action/receipt/durable bits are `observed/unsupported`, not `enforced`. Wrapping them in ACP, tmux or a class implementing the protocol leaves the same maturity.
- [ ] Assert `CompatibilityStatus.CONTRACT_VERIFIED` cannot update deployment maturity to managed; only a successful external report with no failed mandatory probes can.

Run:

```bash
uv run --frozen pytest tests/compat/executor_adapter/test_native.py tests/compat/executor_adapter/test_negative_dispatch.py tests/compat/executor_adapter/test_keqing_truth.py -q
```

Expected RED: no evidence-derived shared suite/report exists and Keqing classification can be inferred from adapter names rather than probes.

### 8.2 GREEN — implement one protocol runner and fail-closed dispatcher binding

- [ ] Add only the protocol hooks required by all adapters: prepare, start/resume, pause/cancel, event stream, pending-action resolution, collect receipts/artifacts and close. Do not add OpenHands-specific types to the shared protocol. `src/tianshu/executor/adapters/native.py` is the thin protocol facade over the G1 `src/tianshu/executor/executor.py`; it does not duplicate the Native loop.
- [ ] Implement `ExecutorCompatibilityRunner` that runs probes, writes raw outputs/environment to ArtifactStore, canonicalizes the report and recomputes the effective capability manifest from passed evidence.
- [ ] Dispatcher compares requested mandatory capabilities with the evidence-backed manifest and rejects before side effects. A missing/stale compatibility report is `unsupported`, not a reason to trust the adapter declaration.
- [ ] Keep compatibility reports deployment-specific: adapter version, server/package/image digest, workspace backend and configuration are part of `deployment_fingerprint`.
- [ ] Document protocol lifecycle, event/evidence rules, capability probes, versioning and the explicit rule that local mock success is not managed acceptance. Leave capability matrix OpenHands row as `candidate / external evidence missing`.

### 8.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/compat/executor_adapter tests/compat/test_executor_capabilities.py tests/integration/test_g3_g4_handoff.py -q
uv run --frozen ruff check src/tianshu/executor/adapters src/tianshu/models/executor_compatibility.py tests/compat/executor_adapter
uv run --frozen mypy src/tianshu/executor/adapters src/tianshu/models/executor_compatibility.py
```

Expected GREEN: Native passes only its evidenced bits, broken fixtures fail each probe, and Keqing stays contained/experimental.

Commit:

```bash
git add src/tianshu/executor/adapters src/tianshu/executor/capabilities.py src/tianshu/executor/keqing/adapter.py src/tianshu/models/executor_compatibility.py tests/compat/executor_adapter docs/usage/executor-adapter.md docs/launch/capability-matrix.md
git commit -m "feat: add evidence-based executor compatibility suite"
```

---

## Increment 9: Implement the OpenHands adapter against a contract fake without upgrading maturity

**Files:**

- Create: `src/tianshu/executor/adapters/openhands.py`
- Create: `src/tianshu/executor/adapters/openhands_bridge.py`
- Create: `src/tianshu/executor/adapters/openhands_events.py`
- Create: `src/tianshu/executor/adapters/openhands_budget.py`
- Create: `src/tianshu/executor/adapters/openhands_workspace.py`
- Modify: `src/tianshu/executor/adapters/__init__.py`
- Modify: `src/tianshu/executor/capabilities.py`
- Modify: `src/tianshu/governance/decision_service.py`
- Modify: `src/tianshu/models/run_state.py`
- Modify: `src/tianshu/executor/side_effects.py`
- Modify: `src/tianshu/bootstrap/wiring_executor.py`
- Modify: `src/tianshu/config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/compat/executor_adapter/fake_openhands_server.py`
- Create: `tests/compat/executor_adapter/test_openhands_contract.py`
- Create: `tests/compat/executor_adapter/test_openhands_decision_bridge.py`
- Create: `tests/compat/executor_adapter/test_openhands_events.py`
- Create: `tests/compat/executor_adapter/test_openhands_faults.py`
- Create: `tests/compat/executor_adapter/test_openhands_maturity.py`

**Pinned optional extra:** add an `openhands` extra containing the four exact `==1.32.0` packages. Do not add them to core dependencies. `uv.lock` must resolve the matched versions; test `importlib.metadata.version()` and record wheel/package metadata in compatibility evidence.

**Confirmation bridge:** configure OpenHands `AlwaysConfirm`. The first `conversation.run()` may generate actions and stop at `ConversationExecutionStatus.WAITING_FOR_CONFIRMATION`; adapter calls `ConversationState.get_unmatched_actions()`, canonicalizes pending actions, persists a G2 DecisionRequest plus vendor conversation ID/event cursor/action digests in RunState, then returns control. Approval is not a vendor method: only after durable `decision.resolved(action="approve")` does the adapter call `conversation.run()` a second time, which is OpenHands' documented implicit confirmation. Rejection calls `conversation.reject_pending_actions(reason)` and continues. The adapter never calls `conversation.execute_tool()`, because the official API documents that it bypasses confirmation/analyzer.

### 9.1 RED — bridge every pending action through durable G2 state

- [ ] Fake the actual HTTP/WebSocket/event shapes used by the pinned SDK. Verify adapter constructs a remote conversation/workspace, registers typed callback, sets `AlwaysConfirm`, sends one message and preserves the vendor conversation ID.
- [ ] Produce a pending file-write action. Assert no workspace mutation/observation occurs before a G2 DecisionRequest is committed; RunState is suspended with exact event cursor/action digest; duplicate pending events create one decision.
- [ ] Approve, reject, expire and race two resolutions. Approval calls the second run once and records the matching observation/receipt; rejection calls `reject_pending_actions(reason)` and creates no side effect; expiry/cancel stays fail closed.
- [ ] Crash at pending event before DecisionRequest, after DecisionRequest before suspended RunState, after resolution before resume outbox, during second run, after observation before receipt and after receipt before cursor advance. Recovery uses G2 outbox/RunState/journal and never executes one action twice.
- [ ] Send unknown/malformed/out-of-order/duplicate OpenHands events. Unknown types are retained as bounded redacted artifacts and mark event-fidelity probe failed; malformed action/observation never becomes a receipt.
- [ ] Try to invoke direct `execute_tool`, workspace command helper, local `Conversation(workspace=".")`, LocalWorkspace and an ACP/CLI subprocess. Architecture/runtime guards reject these paths for the managed candidate profile.
- [ ] Assert a fully passing fake report ends `CONTRACT_VERIFIED`, while deployment maturity and manifest registry remain `candidate/unverified`; any code that marks managed from the fake fails.

Run:

```bash
uv sync --frozen --extra dev --extra openhands
uv run --frozen pytest tests/compat/executor_adapter/test_openhands_contract.py tests/compat/executor_adapter/test_openhands_decision_bridge.py tests/compat/executor_adapter/test_openhands_events.py tests/compat/executor_adapter/test_openhands_faults.py tests/compat/executor_adapter/test_openhands_maturity.py -q
```

Expected RED: pinned packages/adapter/bridge are absent and no durable mapping exists.

### 9.2 GREEN — implement the thin vendor boundary and honest fake status

- [ ] Keep all vendor imports inside the OpenHands adapter modules so core installs and Native remain importable without the optional extra. Missing extra produces structured adapter-unavailable truth.
- [ ] Use the pinned SDK `Conversation` with a remote `DockerWorkspace`/`APIRemoteWorkspace` abstraction; `openhands_workspace.py` validates image digest, mount roots, network policy, persistence volume and secret-reference injection before connection, while the contract fake implements the same narrow seam. Reject local host workspace for the managed profile.
- [ ] Map vendor execution status/events into G1 typed executor events with stable vendor event ID as dedupe key. Store full bounded/redacted vendor payload in ArtifactStore and emit only canonical summaries over the bus.
- [ ] Implement the confirmation bridge exactly as frozen. Persist state before returning; resume only from the G2 resolution outbox consumer, not an in-memory future or WebSocket callback.
- [ ] Convert observations to G2 SideEffectReceipt and bind workspace changes/artifacts/cost to the same Evidence Bundle schema used by Native.
- [ ] Implement budget reservation before each `conversation.run()`: known model price + counted input + configured maximum output tokens produce a conservative upper bound. If price/token bound is unavailable, observed state is `best_effort/unsupported`; never label hard enforcement.
- [ ] Register OpenHands as `candidate`, with deployment-specific effective manifest derived from compatibility evidence. The fake can populate contract probe artifacts but cannot set `external_verified`.

### 9.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/compat/executor_adapter -q -m "not external"
uv run --frozen ruff check src/tianshu/executor/adapters/openhands.py src/tianshu/executor/adapters/openhands_bridge.py src/tianshu/executor/adapters/openhands_events.py src/tianshu/executor/adapters/openhands_budget.py src/tianshu/executor/adapters/openhands_workspace.py tests/compat/executor_adapter
uv run --frozen mypy src/tianshu/executor/adapters
uv run --frozen python -c 'import importlib.metadata as m; assert all(m.version(p)=="1.32.0" for p in ("openhands-sdk","openhands-tools","openhands-workspace","openhands-agent-server"))'
```

Expected GREEN: all fake contract/fault tests pass, the exact packages are installed, and maturity remains candidate/unverified.

Commit:

```bash
git add src/tianshu/executor/adapters/openhands.py src/tianshu/executor/adapters/openhands_bridge.py src/tianshu/executor/adapters/openhands_events.py src/tianshu/executor/adapters/openhands_budget.py src/tianshu/executor/adapters/openhands_workspace.py src/tianshu/executor/adapters/__init__.py src/tianshu/executor/capabilities.py src/tianshu/governance/decision_service.py src/tianshu/models/run_state.py src/tianshu/executor/side_effects.py src/tianshu/bootstrap/wiring_executor.py src/tianshu/config.py pyproject.toml uv.lock tests/compat/executor_adapter
git commit -m "feat: bridge OpenHands into durable governance"
```

---

## Increment 10: Run the real pinned OpenHands Agent Server managed acceptance

**Files:**

- Create: `tests/external/test_openhands_managed_compatibility.py`
- Create: `tests/external/test_openhands_restart_and_decision.py`
- Create: `tests/external/test_openhands_network_workspace_secrets.py`
- Create: `tests/external/test_openhands_budget_receipts.py`
- Create: `tests/external/conftest.py`
- Create: `scripts/external/run_openhands_compatibility.py`
- Create: `tests/compat/executor_adapter/test_external_report_validation.py`
- Modify: `pyproject.toml`
- Modify: `src/tianshu/executor/adapters/openhands.py`
- Modify: `src/tianshu/executor/adapters/openhands_budget.py`
- Modify: `src/tianshu/executor/adapters/openhands_workspace.py`
- Modify: `src/tianshu/executor/capabilities.py`
- Modify: `docs/usage/executor-adapter.md`
- Modify: `docs/launch/capability-matrix.md`
- Create: `docs/launch/executor-compat/.gitkeep`

**Required external inputs:** `OPENHANDS_AGENT_SERVER_IMAGE` must contain `@sha256:` and must not contain `:latest`; either Docker is available for `DockerWorkspace` or a real `OPENHANDS_RUNTIME_API_URL`/secret reference is supplied for `APIRemoteWorkspace`; a real model/provider credential is obtained through G1 SecretRegistry; `TIANSHU_OPENHANDS_EXTERNAL=1`. Tests call `pytest.fail("external_not_configured: ...")` when these are absent. The normal unit job may deselect `external`, but `scripts/g4-gate.sh` must run and count these tests explicitly; deselection/skip cannot satisfy G4-C.

### 10.1 RED — a fake, host workspace or incomplete capability can never pass

- [ ] Validate the external runner rejects `latest`, tag-only image names, fake/local server URLs, LocalWorkspace, ACP process wrappers, package-version mismatch, reused stale report and report/environment fingerprint mismatch.
- [ ] Real action interception: request a sentinel file write under `AlwaysConfirm`; verify the file and observation do not exist while OpenHands waits and G2 durable DecisionRequest exists. Approve and verify exactly one write/receipt. Repeat with rejection and verify zero write.
- [ ] Durable restart: stop Tianshu after pending decision, reconstruct adapter solely from RunState/vendor conversation ID/persisted event cursor, approve and finish once. Then stop/restart the real Agent Server/workspace at a documented safe point and prove documented persistence behavior or fail the durable-resume bit.
- [ ] Network/workspace: in deny-all contract, attempts to reach a test egress endpoint and escape the mounted workspace fail before data leaves; allowed in-workspace file changes remain contained. Verify source checkout is byte-identical until G1 governed apply and restore point reverses it.
- [ ] Secrets: inject a sentinel provider/tool secret by reference, complete/fail/restart a run, then scan Tianshu DB/logs/artifacts/Evidence and exported OpenHands persistence/events/workspace. Any plaintext sentinel fails secret-control and managed acceptance.
- [ ] Budget: use a priced model with explicit max output bound. Show reservation denial occurs before the next `conversation.run()`/action when worst-case exceeds remaining, then reconcile actual cost/overrun. Unknown price or unbounded output must downgrade/fail hard-budget probe, not pass.
- [ ] Event/receipt: interrupt WebSocket, duplicate/reorder reconnect events and kill after remote observation. Resume cursor/dedupe must yield one typed observation, one side-effect receipt, complete artifact/change evidence and no lost terminal state.
- [ ] Run Native and real OpenHands with the same requested contract and task. Assert both produce the same contract/event/Evidence schemas while preserving different effective contract/manifests/deployment fingerprints.

Run:

```bash
TIANSHU_OPENHANDS_EXTERNAL=1 uv run --frozen pytest tests/external/test_openhands_managed_compatibility.py tests/external/test_openhands_restart_and_decision.py tests/external/test_openhands_network_workspace_secrets.py tests/external/test_openhands_budget_receipts.py -q -m external
```

Expected RED: before real deployment/configuration and complete implementation, tests fail explicitly; none skip.

### 10.2 GREEN — accept only the real deployment's observed capabilities

- [ ] Implement the minimal fixes revealed by real tests without weakening shared probes. Keep vendor/version-specific work in OpenHands modules.
- [ ] Register pytest marker `external` in `pyproject.toml`. An explicitly selected external test fails when required environment is absent; it never converts missing infrastructure to a skip.
- [ ] `run_openhands_compatibility.py` executes all probes and faults, records package versions/wheel metadata, image digest, workspace backend/config digest, model/provider identifiers without secrets, timestamps, raw test artifacts and canonical report under `docs/launch/executor-compat/openhands-<deployment_fingerprint>.json`.
- [ ] Validate the report has a real external marker, all required artifacts verify in ArtifactStore, no test skipped/deselected, and `failed_mandatory_capabilities=()` before setting `status=EXTERNAL_VERIFIED`.
- [ ] Only after validation update that exact deployment's registry/capability matrix to `managed`. A different image/config remains candidate until its own run.
- [ ] If the pinned real service cannot enforce any mandatory capability, leave the report failed, keep OpenHands candidate/contained as observed, leave G4-C red and document the blocking capability. Do not synthesize a passing state.

### 10.3 Verify external managed evidence and commit

Run:

```bash
uv run --frozen python scripts/external/run_openhands_compatibility.py --output docs/launch/executor-compat
uv run --frozen pytest tests/compat/executor_adapter/test_external_report_validation.py tests/compat/executor_adapter/test_keqing_truth.py -q
uv run --frozen ruff check src/tianshu/executor/adapters/openhands.py src/tianshu/executor/adapters/openhands_budget.py scripts/external/run_openhands_compatibility.py tests/external
```

Expected GREEN: a real, digest-pinned deployment has one complete `EXTERNAL_VERIFIED` report with no failed mandatory capability; Keqing labels are unchanged. If not, this Increment and G4-C remain incomplete.

Commit only when Expected GREEN is true:

```bash
git add src/tianshu/executor/adapters/openhands.py src/tianshu/executor/adapters/openhands_budget.py src/tianshu/executor/adapters/openhands_workspace.py src/tianshu/executor/capabilities.py scripts/external/run_openhands_compatibility.py tests/external tests/compat/executor_adapter/test_external_report_validation.py pyproject.toml docs/usage/executor-adapter.md docs/launch/capability-matrix.md docs/launch/executor-compat
git commit -m "feat: verify a real managed OpenHands adapter"
```

---

## Increment 11: Make FTS rebuild observable and enforce explicit prompt-layer token budgets

**Files:**

- Create: `src/tianshu/memory/rebuild.py`
- Create: `src/tianshu/persona/prompt_budget.py`
- Modify: `src/tianshu/memory/models.py`
- Modify: `src/tianshu/memory/fts.py`
- Modify: `src/tianshu/memory/manager.py`
- Modify: `src/tianshu/memory/markdown_backend.py`
- Modify: `src/tianshu/memory/backends/sqlite_backend.py`
- Modify: `src/tianshu/persona/prompt_builder.py`
- Modify: `src/tianshu/persona/profile_synthesizer.py`
- Modify: `src/tianshu/tools/memory_tools.py`
- Modify: `src/tianshu/gateway/memory_api.py`
- Modify: `src/tianshu/config.py`
- Modify: `src/tianshu/config_manager.py`
- Modify: `src/tianshu/models/api.py`
- Modify: `src/tianshu/storage/migrations.py`
- Test: `tests/memory/test_fts_rebuild_service.py`
- Test: `tests/memory/test_fts_rebuild_faults.py`
- Test: `tests/memory/test_fts_rebuild_provenance.py`
- Modify tests: `tests/memory/test_fulltext_recall.py`
- Create: `tests/persona/test_prompt_layer_budgets.py`
- Create: `tests/persona/test_prompt_budget_evidence.py`
- Test: `tests/gateway/test_memory_rebuild_api.py`

**Rebuild status:**

```python
class FtsIndexHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    REBUILDING = "rebuilding"
    FAILED = "failed"

class FtsRebuildJobV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    job_id: str
    scope_persona_id: str | None
    status: Literal["requested", "running", "succeeded", "failed"]
    health_before: FtsIndexHealth
    health_after: FtsIndexHealth
    source_count: int
    indexed_count: int
    tombstone_count: int
    source_manifest_digest: str | None
    index_digest: str | None
    schema_version_before: int
    schema_version_after: int
    error: RedactedError | None
    version: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
```

`POST /api/memory/rebuild` creates a durable job and returns 202; `GET /api/memory/rebuild/{job_id}` and `GET /api/memory/index-status` expose structured truth. Existing `/api/memory/sync` becomes a compatibility alias that creates a job; it no longer clears and rebuilds synchronously behind a count-only response.

### 11.1 RED — corruption is not an empty search and rebuild never clears first

- [ ] Corrupt/drop FTS, break a trigger and force a SQLite operational error. `fts_search()` must raise a typed index error, manager/API/tool return `degraded/service-unavailable`, and no path converts it to `[]`/“no memory found.” A valid zero-hit query still returns an explicit healthy empty result.
- [ ] Test an absent memory root, a present-but-empty authoritative root, malformed Markdown, unreadable source, duplicate IDs and partial source enumeration. Missing/partial/malformed input fails before DB mutation; a fully enumerated empty root may produce a valid empty index with manifest digest.
- [ ] Rebuild empty, normal, 10,000-entry, corrupt-index and schema-upgrade fixtures. Parse/canonicalize all source documents and compute manifest before entering the replacement transaction.
- [ ] Inject failures before job claim, during parse, after `BEGIN IMMEDIATE`, after delete, during insert, during FTS special `rebuild`, before validation and before commit. The old committed index remains queryable after rollback; restart marks stale running job failed/retryable without losing entries.
- [ ] Preserve provenance `(entry_id, source_path_redacted, source_digest, source_offset, imported_at)` and deletion semantics. If a complete new manifest omits an existing source entry, write a tombstone and exclude it; if enumeration was incomplete, fail rather than mass-delete.
- [ ] Add v10 migration/schema tests for rebuild jobs, provenance/tombstones and prompt usage; migrate current healthy/corrupt fixtures without pretending corrupt is healthy.
- [ ] Build oversized content in every PromptLayer. Assert per-layer and total limits, exact token counting metadata, deterministic trim order, source/drop digests, and structured failure when required base/persona identity exceeds budget.
- [ ] Assert no prompt receives raw candidate/live content outside its `EffectiveEvolutionOverlayV1`; baseline and treatment prompt digests differ only in the intended layer.

Run:

```bash
uv run --frozen pytest tests/memory/test_fts_rebuild_service.py tests/memory/test_fts_rebuild_faults.py tests/memory/test_fts_rebuild_provenance.py tests/memory/test_fulltext_recall.py tests/persona/test_prompt_layer_budgets.py tests/persona/test_prompt_budget_evidence.py tests/gateway/test_memory_rebuild_api.py -q
```

Expected RED: current FTS exceptions return `[]`, sync deletes before import, and PromptBuilder uses char/hardcoded budgets without evidence.

### 11.2 GREEN — transactional rebuild and evidence-producing prompt assembly

- [ ] Implement FtsRebuildService with durable CAS job claim. Enumerate/parse/canonicalize outside the write transaction; inside one `BEGIN IMMEDIATE`, replace the selected derived rows/provenance/tombstones, execute FTS5 `rebuild`, validate row count/sample queries/index digest, then commit. No intermediate delete is visible.
- [ ] Reconciler marks expired running leases failed and may create an explicit retry job; it never marks success from a stale heartbeat.
- [ ] Remove the broad exception in `fts_search`. Set index health degraded on operational/corruption errors and include correlation/job IDs in API responses without leaking SQL/source content.
- [ ] Implement the fixed `PromptBudgetV1`, provider/model token counter and conservative fallback. Default sum is exactly 26,624; reject any config whose layer sum differs or exceeds effective model input context after task/output reserve.
- [ ] Change PromptBuilder to return a `PromptBuildResult(prompt, evidence)`. Apply deterministic per-layer document-boundary trimming and persist `PromptAssemblyEvidenceV1`/usage rows linked to memorial/candidate/overlay.
- [ ] Migrate old default `skills_char_budget` to the new default `skills_tokens=8192`; convert an explicit non-default user override conservatively with `ceil(chars/3)`, record `legacy_char_conversion`, validate against total budget and deprecate the API field without silently ignoring it.
- [ ] Make ProfileSynthesizer consume the profile budget and propose profile changes through CandidateService; it must not grow the live profile beyond budget or write a candidate directly live.

### 11.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/memory tests/persona/test_prompt_layer_budgets.py tests/persona/test_prompt_budget_evidence.py tests/gateway/test_memory_rebuild_api.py -q
uv run --frozen ruff check src/tianshu/memory src/tianshu/persona/prompt_budget.py src/tianshu/persona/prompt_builder.py src/tianshu/persona/profile_synthesizer.py tests/memory tests/persona/test_prompt_layer_budgets.py
uv run --frozen mypy src/tianshu/memory src/tianshu/persona
```

Expected GREEN: rebuild is atomic/recoverable/observable, corrupt search is not false empty, and every prompt layer has verified token evidence.

Commit:

```bash
git add src/tianshu/memory src/tianshu/persona/prompt_budget.py src/tianshu/persona/prompt_builder.py src/tianshu/persona/profile_synthesizer.py src/tianshu/tools/memory_tools.py src/tianshu/gateway/memory_api.py src/tianshu/config.py src/tianshu/config_manager.py src/tianshu/models/api.py src/tianshu/storage/migrations.py tests/memory tests/persona/test_prompt_layer_budgets.py tests/persona/test_prompt_budget_evidence.py tests/gateway/test_memory_rebuild_api.py
git commit -m "feat: make memory rebuild and prompt budgets verifiable"
```

---

## Increment 12: Build fixed paired memory/profile ROI benchmarks and statistical gates

**Files:**

- Create: `src/tianshu/evals/memory_profile_roi.py`
- Create: `src/tianshu/evals/paired_statistics.py`
- Create: `tests/evals/memory_recall/data/manifest.v1.json`
- Create: `tests/evals/memory_recall/data/cases.v1.jsonl`
- Create: `tests/evals/memory_recall/test_dataset_contract.py`
- Create: `tests/evals/memory_recall/test_paired_runner.py`
- Create: `tests/evals/memory_recall/test_paired_statistics.py`
- Create: `tests/evals/memory_recall/test_candidate_gate_binding.py`
- Create: `tests/evals/memory_recall/test_no_data_leakage.py`
- Create: `tests/external/test_memory_profile_roi.py`
- Create: `scripts/evals/run_memory_profile_roi.py`
- Create: `tests/evals/test_memory_profile_roi_cli.py`
- Modify: `src/tianshu/evolution/gates.py`
- Modify: `src/tianshu/persona/profile_synthesizer.py`
- Create: `docs/launch/evals/.gitkeep`

**Dataset contract:** 60 immutable paired cases: 20 labeled memory recall/grounding tasks, 20 user-profile preference tasks and 20 distractor/negative-personalization tasks. Each JSONL row has stable ID, stratum, prompt, setup artifact digests, relevant memory IDs, allowed profile facts, forbidden facts, deterministic acceptance checks and severity. The manifest contains dataset digest, license/source notes, schema, creation rule and thresholds. Production run writes no answers back into the source dataset.

**Paired acceptance:** use the same requested/effective contract, executor, model/provider/version, pricing snapshot, temperature/seed where supported, prompt budgets and workspace. Deterministically randomize whether baseline or treatment runs first per pair. A valid report needs at least 50/60 complete pairs and at least 15 per stratum. Compute paired deltas and 10,000-resample percentile bootstrap 95% intervals with a fixed documented bootstrap seed.

- Memory candidate: lower 95% bound of task-success delta `> 0`; lower bound of recall@5 delta `> 0`; precision@5 lower bound `>= -0.02`.
- Profile candidate: lower 95% bound of task-success delta `> 0`; negative-personalization/severe privacy regressions exactly `0`.
- Both: treatment p90 prompt tokens within PromptBudgetV1; upper bound of paired mean token increase `<= 15%` of baseline mean and cost increase `<= 10%`; no severity-high regression; dataset/environment/artifact digests complete.

If baseline mean token/cost is zero, evaluate the absolute configured token/CNY threshold from the manifest instead of dividing by zero. Threshold changes create dataset/manifest v2 and cannot rewrite old evidence.

### 12.1 RED — prevent unpaired, leaky or statistically weak claims

- [ ] Validate schema, unique case IDs, exact 20/20/20 strata, deterministic dataset digest and no secret/personal production content. Any manifest/data edit without version/digest update fails.
- [ ] Runner test asserts baseline/treatment differ in exactly one target layer: memory absent/present or profile absent/present. All other contract/prompt layer/model/executor/environment digests match.
- [ ] Simulate provider drift, missing member, retry only one arm, changed seed, different prompt budget, treatment-first bias and acceptance evaluator leakage. Mark pair invalid and exclude with explicit reason; never impute success.
- [ ] Verify precision@5, recall@5, task success, tokens, cost and latency from hand-calculated fixtures. Bootstrap output is deterministic, paired by case ID and sensitive to direction; it must never resample arms independently.
- [ ] Add datasets with one clear benefit, no effect, quality benefit but excessive tokens/cost, privacy regression, insufficient pairs and wide uncertainty. Only the fully compliant benefit is eligible.
- [ ] Prove deterministic fake provider can pass runner/statistics unit tests but report status is `synthetic_validation` and cannot satisfy the candidate EVIDENCE gate.
- [ ] External real-provider test fails `external_not_configured` rather than skips when model/provider/credential is absent. Record seed support; lack of seed control is a limitation in evidence, not silently “deterministic.”
- [ ] Candidate gate accepts only a canonical real paired report whose candidate/base/overlay/dataset/environment digests match the current candidate. A manually edited summary or prior candidate report blocks.

Run:

```bash
uv run --frozen pytest tests/evals/memory_recall tests/evals/test_memory_profile_roi_cli.py -q
```

Expected RED: fixed data, paired runner/statistics and gate binding do not exist.

### 12.2 GREEN — produce reproducible paired evidence, not a marketing score

- [ ] Implement a provider-neutral runner that creates isolated baseline/treatment Memorials through the normal governed execution path, records RunState/Evidence/PromptAssembly/assignment and applies timeouts/retries symmetrically.
- [ ] Implement metrics and 10,000-resample paired bootstrap without adding a heavy numerical runtime dependency. Serialize raw per-pair metrics, exclusion reasons, intervals and threshold verdicts.
- [ ] Write canonical report/artifacts to ArtifactStore and `docs/launch/evals/memory-profile-roi-<dataset>-<environment_fingerprint>.json`; include model/provider version, pricing, seed behavior, order sequence, contract/prompt digests and real/synthetic status.
- [ ] `run_memory_profile_roi.py` exits nonzero on insufficient/ineligible/incomplete evidence. It accepts explicit provider/model/secret reference and output path, never raw secret CLI values.
- [ ] Bind an eligible report to memory/profile candidate gates. No eligible report means no promotion; it does not delete or hide the candidate.
- [ ] Keep vector retrieval out of implementation. A future vector proposal may be staged only after a separate paired report beats the FTS baseline under the same thresholds.

### 12.3 Verify real ROI evidence and commit

Run unit evidence first:

```bash
uv run --frozen pytest tests/evals/memory_recall tests/evals/test_memory_profile_roi_cli.py -q
uv run --frozen ruff check src/tianshu/evals/memory_profile_roi.py src/tianshu/evals/paired_statistics.py scripts/evals/run_memory_profile_roi.py tests/evals/memory_recall
```

Then run the required real-provider benchmark:

```bash
TIANSHU_ROI_EXTERNAL=1 uv run --frozen pytest tests/external/test_memory_profile_roi.py -q -m external
uv run --frozen python scripts/evals/run_memory_profile_roi.py --dataset tests/evals/memory_recall/data/manifest.v1.json --pairs 60 --bootstrap-samples 10000 --output docs/launch/evals
```

Expected GREEN: one real paired report satisfies all quality, safety, sample, token and cost thresholds. If not, memory/profile promotion remains blocked and G4-C is incomplete; do not commit a claim of proven ROI.

Commit only when the evidence is valid:

```bash
git add src/tianshu/evals/memory_profile_roi.py src/tianshu/evals/paired_statistics.py src/tianshu/evolution/gates.py src/tianshu/persona/profile_synthesizer.py tests/evals/memory_recall tests/evals/test_memory_profile_roi_cli.py tests/external/test_memory_profile_roi.py scripts/evals/run_memory_profile_roi.py docs/launch/evals
git commit -m "test: prove paired memory and profile ROI"
```

---

## Increment 13: Add calibrated cost intervals, immutable outcomes and attribution

**Files:**

- Create: `src/tianshu/cost/forecast.py`
- Create: `src/tianshu/cost/calibration.py`
- Modify: `src/tianshu/cost/models.py`
- Modify: `src/tianshu/cost/manager.py`
- Modify: `src/tianshu/cost/tracker.py`
- Modify: `src/tianshu/storage/cost_repo.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/gateway/cost_api.py`
- Create: `tests/cost/test_forecast_model.py`
- Create: `tests/cost/test_forecast_calibration.py`
- Create: `tests/cost/test_cost_attribution.py`
- Create: `tests/cost/test_cost_outcomes.py`
- Create: `tests/gateway/test_cost_forecast_api.py`
- Create: `scripts/evals/run_cost_calibration.py`
- Create: `tests/evals/test_cost_calibration_cli.py`
- Create: `docs/launch/cost/.gitkeep`

**Immutable outcome types:**

```python
class CostAttributionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    edict_id: str
    memorial_id: str
    attempt_ids: tuple[str, ...]
    provider_name: str
    model: str
    executor_id: str
    candidate_id: str | None
    assignment_arm: AssignmentArm
    overlay_digest: str
    contract_hash: str

class CostOutcomeV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    forecast_id: str
    attribution: CostAttributionV1
    actual_cost: Decimal
    overrun: Decimal
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    pricing_snapshot_digest: str
    closed_at: datetime

class ForecastCalibrationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    cohort_key: str
    sample_count: int
    backtest_count: int
    interval_nominal_coverage: Decimal
    interval_observed_coverage: Decimal
    p50_mdape: Decimal
    status: ForecastStatus
    source_manifest_digest: str
    environment_fingerprint: str
    artifact_digests: tuple[str, ...]
    evaluated_at: datetime
```

Comparable cohort key is canonical `(provider_name, model, executor_id, task_class, candidate_kind-or-none)`. Exact cohort is preferred; documented fallback drops candidate kind, then task class, but never mixes currency/provider price snapshots without normalization. Forecast remains immutable; completion creates a separate outcome joined by `forecast_id`.

### 13.1 RED — intervals must be backtested, ordered and honestly uncalibrated

- [ ] Assert `0 <= p10 <= p50 <= p90`, Decimal-only arithmetic, CNY currency, nonempty attribution key, price snapshot digest and no NaN/Infinity/float round-trip.
- [ ] With fewer than 100 comparable closed outcomes, status is `INSUFFICIENT_DATA`; the system may return a deterministic price/max-token estimate labeled uncalibrated but cannot display “calibrated” or use it as proof of a hard cap.
- [ ] For at least 100 records, use rolling-origin backtest: after a minimum 60-record history, predict each subsequent outcome from only earlier records. Assert nominal p10–p90 coverage `0.80`, observed coverage must be within `[0.70, 0.90]`, and p50 MdAPE `<= 0.35` to be calibrated.
- [ ] Test under-dispersed, over-wide, biased median, time-leaking and mixed-price datasets. They fail the appropriate coverage/MdAPE/source checks rather than receiving a wider hand-tuned interval after seeing holdout outcomes.
- [ ] Test drift: a new pricing snapshot or recent distribution shift invalidates the prior calibration for that cohort until re-evaluated; old reports remain immutable.
- [ ] Attribute champion/challenger runs, retries, cache tokens and OpenHands/Native separately. Sum of per-attempt costs equals the closed outcome; no record is double-counted between EventBus retry/final events.
- [ ] Compute actual overrun exactly as fixed. A terminal hook may record nonzero overrun; it cannot rewrite the forecast or claim the action was prevented.
- [ ] API tests for `GET /api/cost/forecast?memorial_id=...`, `GET /api/cost/outcomes/{memorial_id}` and calibration view: permission/error states, exact Decimal strings, sample window/count, status, attribution and no false zero/empty on storage failure.
- [ ] CLI test rejects a calibration report with synthetic-only records, fewer than 100 actual closed Evidence costs, future-data leakage, missing environment/pricing digest or manually edited aggregate without raw artifact.

Run:

```bash
uv run --frozen pytest tests/cost/test_forecast_model.py tests/cost/test_forecast_calibration.py tests/cost/test_cost_attribution.py tests/cost/test_cost_outcomes.py tests/gateway/test_cost_forecast_api.py tests/evals/test_cost_calibration_cli.py -q
```

Expected RED: current cost model has point/final values only and uses floats without calibration/outcome attribution.

### 13.2 GREEN — forecast from prior evidence and close a separate actual outcome

- [ ] Implement an empirical quantile forecaster over prior comparable outcomes, with deterministic interpolation documented/tested. If exact cohort is insufficient, expose fallback cohort and uncalibrated status rather than pretending equal precision.
- [ ] Implement rolling-origin calibration and immutable report/artifacts. Do not add an ML framework; the small empirical implementation is sufficient and auditable.
- [ ] Create v11 tables for forecasts/calibrations/enforcement evidence and narrow repository methods with Unit of Work support. Store Decimals as canonical strings/minor units, not binary floats.
- [ ] Create forecast before dispatch from requested/effective contract and historical evidence; bind `forecast_id` to RunState/assignment. On every terminal outcome, close one `CostOutcomeV1` idempotently from G2 usage/receipts.
- [ ] Extend Cost API with joined `CostGovernanceViewV1` containing immutable forecast, optional outcome and calibration. Return `INSUFFICIENT_DATA` explicitly.
- [ ] `run_cost_calibration.py` reads only closed real Evidence/Cost outcomes, emits raw cohort/backtest rows and canonical reports under `docs/launch/cost/calibration-<cohort>-<environment_fingerprint>.json`, and exits nonzero unless at least one launch cohort meets the fixed calibration thresholds.

### 13.3 Verify and commit

Run:

```bash
uv run --frozen pytest tests/cost tests/gateway/test_cost_forecast_api.py tests/evals/test_cost_calibration_cli.py -q
uv run --frozen python scripts/evals/run_cost_calibration.py --min-samples 100 --nominal-coverage 0.80 --coverage-min 0.70 --coverage-max 0.90 --max-mdape 0.35 --output docs/launch/cost
uv run --frozen ruff check src/tianshu/cost src/tianshu/storage/cost_repo.py src/tianshu/gateway/cost_api.py scripts/evals/run_cost_calibration.py tests/cost
uv run --frozen mypy src/tianshu/cost
```

Expected GREEN: at least one real launch cohort has an immutable calibrated report with 100+ source outcomes; other cohorts remain explicitly insufficient/uncalibrated.

Commit:

```bash
git add src/tianshu/cost src/tianshu/storage/cost_repo.py src/tianshu/storage/migrations.py src/tianshu/gateway/cost_api.py tests/cost tests/gateway/test_cost_forecast_api.py scripts/evals/run_cost_calibration.py tests/evals/test_cost_calibration_cli.py docs/launch/cost
git commit -m "feat: add calibrated cost forecasts and outcomes"
```

---

## Increment 14: Enforce and display honest managed/contained/observed budget modes

**Files:**

- Create: `src/tianshu/cost/enforcement.py`
- Modify: `src/tianshu/cost/budget.py`
- Modify: `src/tianshu/cost/manager.py`
- Modify: `src/tianshu/cost/models.py`
- Modify: `src/tianshu/executor/execution_gateway.py`
- Modify: `src/tianshu/executor/adapters/native.py`
- Modify: `src/tianshu/executor/adapters/openhands_budget.py`
- Modify: `src/tianshu/executor/keqing/executor.py`
- Modify: `src/tianshu/application/evidence.py`
- Modify: `src/tianshu/gateway/cost_api.py`
- Modify: `src/tianshu/gateway/evolution_api.py`
- Modify: `web/src/api/cost.ts`
- Modify: `web/src/hooks/useCost.ts`
- Modify: `web/src/pages/CostDashboardPage.tsx`
- Modify: `web/src/pages/UniversePage.tsx`
- Create: `web/src/components/cost/EnforcementModeTag.tsx`
- Create: `web/src/components/cost/ForecastInterval.tsx`
- Create: `web/src/components/cost/CostAttributionTable.tsx`
- Create: `web/src/components/cost/EnforcementModeTag.test.tsx`
- Create: `web/src/pages/CostDashboardPage.test.tsx`
- Modify: `web/src/pages/UniversePage.test.tsx`
- Create: `web/e2e/g4-evolution-cost.spec.ts`
- Test: `tests/integration/test_budget_enforcement_modes.py`
- Test: `tests/integration/test_budget_enforcement_faults.py`
- Test: `tests/cost/test_enforcement_evidence.py`
- Test: `tests/gateway/test_cost_governance_api.py`

**Mode semantics:**

- `MANAGED`: only when effective manifest has `budget_enforcement=enforced`, a known price ceiling and bounded input/output/action units. Reserve worst-case before every governed provider/tool side effect; deny before action if reservation exceeds remaining; reconcile reservation to actual.
- `CONTAINED`: for Keqing/opaque subprocess or an adapter with only boundary control. Check before starting a process/new iteration and stop future boundaries, but a running opaque process may exceed. UI says `进程边界软限制`, not hard cap.
- `OBSERVED`: collect actual after the fact; never block and never claim prevention. UI says `仅观测`, shows actual overrun.

### 14.1 RED — capability truth determines behavior and wording

- [ ] Request mandatory managed budget with Native/OpenHands passing manifest, contained Keqing and observed fake. Managed accepts only with hard prerequisites; contained/observed are rejected before dispatch for mandatory hard contract and show exact unsupported capability.
- [ ] Managed test reserves before a sentinel provider/tool action, injects insufficient remaining and proves zero provider/process/workspace side effect. Crash after reserve but before action releases/reconciles lease on timeout; crash after action uses receipt/actual exactly once.
- [ ] Test worst-case bound missing price, tokenizer, max output, action limit or manifest evidence. Mode downgrades/dispatch rejects; p90 forecast alone is not accepted as a hard upper bound.
- [ ] Contained test starts a bounded opaque process under budget, makes actual exceed during the process, records overrun, and prevents only the next launch. Assert no log/API/UI calls this “pre-call hard cap.”
- [ ] Observed test never blocks even when already over budget; it writes post-action evidence and high-severity signal. A hook return mistakenly blocking in observed mode fails.
- [ ] Fault/concurrency tests race reservations across two attempts sharing one budget. Serialized reservation prevents managed oversubscription; stale owner cannot spend/release another attempt's reservation.
- [ ] Evidence close fails if cost forecast/outcome/enforcement mode, capability manifest hash and attribution disagree. Store `cost.governance.v1` artifact in the G2 bundle.
- [ ] Web tests render p10–p90 and actual separately, calibration status/sample count, candidate/executor attribution, overrun and the exact three Chinese mode labels. 503/error is not rendered as ¥0; contained/observed never get the managed shield/icon/tooltip.
- [ ] Universe page uses backend `promotion_allowed/blocking_gates`; it displays cost gate and real routing truth but never derives promotion from a green cost card.
- [ ] Add one desktop-only real-stack Playwright journey at `1440 × 1024`: seed a fixed 10% routing config plus precomputed champion/challenger memorial IDs, open the real Universe page, prove assignment/overlay/evidence truth differs, open Cost Dashboard, prove OpenHands/Keqing rows display their backend managed/contained modes, and exercise rollback until new routing reads champion. Do not intercept API calls or use Phase 3 mock fixtures for this spec.

Run:

```bash
uv run --frozen pytest tests/integration/test_budget_enforcement_modes.py tests/integration/test_budget_enforcement_faults.py tests/cost/test_enforcement_evidence.py tests/gateway/test_cost_governance_api.py -q
npm --prefix web test -- --run src/components/cost/EnforcementModeTag.test.tsx src/pages/CostDashboardPage.test.tsx src/pages/UniversePage.test.tsx
npm --prefix web run e2e:real -- g4-evolution-cost.spec.ts
```

Expected RED: current iteration hook conflates modes, Evidence lacks enforcement proof and Web cannot distinguish interval/actual/overrun/mode.

### 14.2 GREEN — reserve only where control is real and expose the same truth

- [ ] Implement `BudgetEnforcementService.authorize(intent, effective_contract, manifest, forecast)` and `reconcile(receipt/outcome)` with durable reservations/CAS. It derives mode; callers cannot pass a trusted mode string.
- [ ] Integrate at Native/OpenHands governed boundaries and ExecutionGateway process launch. Keqing calls the contained pre-launch check only; existing `on_before_iteration` delegates and no longer claims a universal hard breaker.
- [ ] Persist each allow/deny/reconcile record and append system audit/outbox. Bind the final canonical cost governance artifact to Evidence.
- [ ] Add gateway aggregate endpoints using the fixed error contract. Do not calculate intervals/mode in React.
- [ ] Update only the desktop G3 pages/components. Keep existing TianShu logo, header quote, top-right `彩蛋 / 通用 / English / 实时 / 通政`, fourteen-department sidebar, theme and collapse controls unchanged.
- [ ] Use G3 “墨为骨、朱为睛、纸为气” tokens: interval uses restrained ink band/line, overrun uses cinnabar only when positive, no neon/gradient/large gold/new mobile layout.

### 14.3 Verify G4-C cost truth and commit

Run:

```bash
uv run --frozen pytest tests/cost tests/integration/test_budget_enforcement_modes.py tests/integration/test_budget_enforcement_faults.py tests/gateway/test_cost_governance_api.py tests/compat/executor_adapter/test_keqing_truth.py -q
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run e2e:real -- g4-evolution-cost.spec.ts
uv run --frozen ruff check src/tianshu/cost src/tianshu/executor tests/cost tests/integration/test_budget_enforcement_modes.py
```

Expected GREEN: preventive behavior, Evidence and Web wording match each executor's observed capability; managed denies before action, contained/observed remain honest.

Commit:

```bash
git add src/tianshu/cost src/tianshu/executor/execution_gateway.py src/tianshu/executor/adapters src/tianshu/executor/keqing/executor.py src/tianshu/application/evidence.py src/tianshu/gateway/cost_api.py src/tianshu/gateway/evolution_api.py web/src/api/cost.ts web/src/hooks/useCost.ts web/src/pages/CostDashboardPage.tsx web/src/pages/UniversePage.tsx web/src/components/cost tests/integration/test_budget_enforcement_modes.py tests/integration/test_budget_enforcement_faults.py tests/cost/test_enforcement_evidence.py tests/gateway/test_cost_governance_api.py web/src/pages/CostDashboardPage.test.tsx web/src/pages/UniversePage.test.tsx web/e2e/g4-evolution-cost.spec.ts
git commit -m "feat: expose truthful budget enforcement modes"
```

---

## Increment 15: Automate the complete G4 Gate and truth handoff

**Files:**

- Create: `scripts/g4-gate.sh`
- Create: `scripts/check_g4_evidence.py`
- Create: `tests/gates/test_g4_gate_contract.py`
- Create: `docs/launch/gates/g4-governed-evolution-executors.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/usage/executor-adapter.md`
- Modify: `README.md`
- Modify: `docs/usage/getting-started.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Gate status model:** `failed`, `passed`. There is no “passed with missing external evidence.” Subcheck status is `passed/failed/not_run`; `not_run` makes the full Gate failed. Generated report records commands, exit codes, start/end, commit/dirty state, environment fingerprints, artifact digests and zero skip/deselection counts.

### 15.1 RED — make every claim machine-checkable before writing the runner

- [ ] `test_g4_gate_contract.py` requires exact G4-A/B/C sections, raw JUnit/report/benchmark/eval/calibration/external artifact digests, no `skip/xfail/not_run`, and capability matrix rows derived from the same external report.
- [ ] Feed checker missing/corrupt/stale/wrong-commit artifacts, a fake OpenHands report, tag-only image, synthetic ROI report, 99-sample calibration, 9%-11% distribution without overlay evidence, rollback p95 exactly 60, one skipped external test and Keqing marked managed. Every fixture fails a stable reason.
- [ ] Feed a complete canonical fixture and assert checker recomputes hashes/statistics rather than trusting summary booleans.
- [ ] Add docs truth tests: no “自动进化已完成” without G4 Gate link; code auto-promotion is denied; OpenHands managed claim names exact deployment fingerprint; Keqing remains contained/experimental; cost wording distinguishes managed/contained/observed.
- [ ] Add CI test proving the default PR job runs G4-A/B and non-external G4-C contracts, while a protected/manual `g4-external` job runs real OpenHands + real ROI + calibration and cannot publish a pass artifact from a skipped job.

Run:

```bash
uv run --frozen pytest tests/gates/test_g4_gate_contract.py tests/test_public_docs_truth.py -q
```

Expected RED: Gate script/checker/report and complete evidence index do not exist.

### 15.2 GREEN — implement G4-A, G4-B and G4-C as one non-bypassable runner

- [ ] Verify the existing pytest markers `external` and `benchmark` retain their frozen semantics. External tests fail when explicitly selected without configuration; they never call `pytest.skip` for missing required evidence.
- [ ] `scripts/g4-gate.sh` begins with `set -euo pipefail`, rejects dirty generated evidence from another commit, runs `uv sync --frozen --extra dev --extra openhands`, validates exact package versions and required digest-pinned OpenHands image.
- [ ] G4-A runs candidate schema/migrations/adapters, all skill-channel security, Gate evaluator, PromotionService CAS/faults, code-never-auto and both architecture guards.
- [ ] G4-B runs 10,000-route statistics/actual overlay/evidence, restart/concurrency/fault suites and the real 100-cycle rollback benchmark; checker recomputes observed rate, Wilson interval and nearest-rank p95 from raw values.
- [ ] G4-C runs Native/OpenHands shared suite, the four real OpenHands external files, real memory/profile paired benchmark, real cost calibration, three budget modes, FTS rebuild/prompt budgets and desktop Web cost/evolution tests/build.
- [ ] Write JUnit XML per group and parse it. G4-C requires at least five selected external tests (four OpenHands files plus ROI), `skipped=0`, `errors=0`, `failures=0`; collection/deselection mismatch fails.
- [ ] Run full backend regressions, Ruff, mypy packages, import-linter, Web tests/typecheck/build after focused groups. Do not hide pre-existing failures; report exact blockers.
- [ ] `check_g4_evidence.py` verifies ArtifactStore/file hashes, commit/environment linkage, current gate snapshots, real deployment marker, candidate/overlay attribution, ROI thresholds, calibration thresholds, budget-mode evidence and capability matrix consistency; then writes the report from computed results.
- [ ] Update README/getting-started/capability matrix only from passed evidence. Until the external job passes, wording stays “G4 candidate / external evidence pending,” not “managed OpenHands available.”

### 15.3 Run the complete Gate

Required environment is supplied by secret references and digest-pinned image; no raw secrets appear in the command or report:

```bash
TIANSHU_OPENHANDS_EXTERNAL=1 TIANSHU_ROI_EXTERNAL=1 ./scripts/g4-gate.sh
```

The script must execute equivalent commands to:

```bash
uv run --frozen pytest tests/evolution tests/skills tests/architecture/test_no_direct_skill_writes.py tests/architecture/test_promotion_authority.py -q --junitxml=.tianshu/g4/g4-a.xml
uv run --frozen pytest tests/universe/test_challenger_routing.py tests/universe/test_challenger_routing_statistics.py tests/integration/test_challenger_routing_faults.py tests/integration/test_challenger_rollback_faults.py -q --junitxml=.tianshu/g4/g4-b.xml
uv run --frozen python scripts/benchmarks/g4_rollback_latency.py --cycles 100 --assignments 10000 --max-candidate-bytes 1048576 --output docs/launch/benchmarks
uv run --frozen pytest tests/compat/executor_adapter tests/external/test_openhands_managed_compatibility.py tests/external/test_openhands_restart_and_decision.py tests/external/test_openhands_network_workspace_secrets.py tests/external/test_openhands_budget_receipts.py tests/external/test_memory_profile_roi.py tests/memory/test_fts_rebuild_service.py tests/persona/test_prompt_layer_budgets.py tests/cost tests/integration/test_budget_enforcement_modes.py -q --junitxml=.tianshu/g4/g4-c.xml
uv run --frozen python scripts/evals/run_memory_profile_roi.py --dataset tests/evals/memory_recall/data/manifest.v1.json --pairs 60 --bootstrap-samples 10000 --output docs/launch/evals
uv run --frozen python scripts/evals/run_cost_calibration.py --min-samples 100 --nominal-coverage 0.80 --coverage-min 0.70 --coverage-max 0.90 --max-mdape 0.35 --output docs/launch/cost
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src/tianshu/models src/tianshu/evolution src/tianshu/executor src/tianshu/memory src/tianshu/persona src/tianshu/cost
uv run --frozen lint-imports
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run e2e:real -- g4-evolution-cost.spec.ts
uv run --frozen python scripts/check_g4_evidence.py --root . --report docs/launch/gates/g4-governed-evolution-executors.md
```

Expected GREEN:

- G4-A proves one candidate schema, one guarded skill supply chain and one authoritative promotion/rollback path.
- G4-B proves configured 10% is real behavior with correct durable attribution and recorded local rollback p95 `<60s`.
- G4-C proves Native plus one exact real OpenHands deployment pass the same mandatory suite; Keqing stays contained; real paired ROI/calibration and three truthful cost modes pass.
- Full backend/Web quality gates pass; report/evidence digests match the current commit; no external test is skipped/not run.

If any bullet is false, report status is `failed`, retain honest capability labels and continue the failing Increment; do not commit a passed Gate.

### 15.4 Final commit after a genuinely green Gate

```bash
git add scripts/g4-gate.sh scripts/check_g4_evidence.py tests/gates/test_g4_gate_contract.py docs/launch/gates/g4-governed-evolution-executors.md docs/launch/capability-matrix.md docs/usage/executor-adapter.md README.md docs/usage/getting-started.md .github/workflows/ci.yml pyproject.toml web/package.json web/package-lock.json docs/launch/benchmarks docs/launch/executor-compat docs/launch/evals docs/launch/cost
git commit -m "test: enforce the governed evolution G4 gate"
```

---

## Final G4 Acceptance Checklist

- [ ] memory/skill/policy/persona/code all serialize as `EvolutionCandidateV1` and use one lifecycle/gate/promotion service.
- [ ] API/CLI/agent/reviewer/curator/zip skill writes pass one full-package guard, provenance/version and rollback flow; no proposal is immediately live.
- [ ] PromotionService is the only live/routing authority; all missing/error/stale gates and CAS races produce zero promotion.
- [ ] Code candidates never auto-promote; public evolution demo uses a skill candidate only.
- [ ] 10% challenger changes actual resources/output, assignment is persisted before dispatch, restart-stable, and Evidence matches overlay/candidate digests.
- [ ] Rollback first stops new traffic, recovers from every injected fault, and measured p95 is below 60 seconds in a fully recorded defined environment.
- [ ] Native and a real digest-pinned OpenHands Agent Server pass the same requested contract, capability, event and Evidence compatibility suite.
- [ ] OpenHands fake/mock does not upgrade maturity; absence/failure of the real service leaves G4-C failed.
- [ ] Keqing Claude Code/Codex headless remain contained + experimental; ACP/CLI wrapper does not upgrade them.
- [ ] FTS corruption is observable, rebuild is transactional/recoverable, provenance/tombstones survive, and empty results are distinguished from index failure.
- [ ] persona/profile/peer/memory/skills have explicit token budgets, exact/fallback tokenizer evidence and deterministic trimming.
- [ ] A real fixed paired benchmark proves current memory/profile candidate ROI under quality, safety, token and cost thresholds; synthetic validation alone is rejected.
- [ ] Cost view shows calibrated p10/p50/p90, actual, attribution and overrun; insufficient cohorts remain labeled insufficient/uncalibrated.
- [ ] Managed/contained/observed budget behavior, Evidence and Web wording agree with the effective capability manifest.
- [ ] Desktop Web preserves all G3-approved TianShu branding/shell behavior and derives no gate/capability truth locally.
- [ ] Automated G4-A/G4-B/G4-C plus full regressions pass with zero required skips/not-run and a canonical current-commit report.

Passing G4 authorizes Phase 5 to build the three public demos and Executor SDK on top of a genuinely governed evolution/executor substrate. It does not prove every future OpenHands version/config, multi-node routing linearizability, all providers' exact monetary bounds, production latency on other hardware, or safe automatic code promotion; those claims remain explicitly out of scope.
