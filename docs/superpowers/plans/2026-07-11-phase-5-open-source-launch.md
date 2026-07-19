# Phase 5 Open-source Launch & Ecosystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Tianshu as a reproducible, evidence-backed open-source product with a stable Executor SDK, three repeatable golden demos, frozen wheel/container profiles, supply-chain attestations, community contribution surfaces, and independently collected launch evidence.

**Architecture:** G5 has three automatic slices. G5-A freezes the public SDK and proves the three demos through public APIs and machine-verifiable Evidence Bundles. G5-B builds the same tested source revision into core/server/all installation profiles, a non-root OCI image, SBOM/license/provenance artifacts, and a clean community repository. G5-C validates real OpenHands, three external environments, a seven-day real cost window, VoiceOver/user review, and GitHub OIDC/publication state without converting local fixtures into external evidence.

**Tech Stack:** Python 3.12+, Pydantic v2, FastAPI, SQLite, uv, setuptools/build, JSON Schema 2020-12, pytest, mypy, import-linter, Docker/BuildKit, Playwright desktop Chromium, GitHub Actions/OIDC artifact attestations, PyPI Trusted Publishing, Syft, Gitleaks, pip-audit, npm, SPDX/CycloneDX.

## Global Constraints

- The user waived intermediate approval pauses. Continue automatically through every locally executable G5-A/G5-B/G5-C preparation task and automatic Gate. Stop only at the final authority boundary: external evidence that does not yet exist, public repository/tag/package publication, or the user’s final product approval.
- G5 consumes green G3 and G4 contracts. It does not repair missing durable governance, fake an Evolution gate, or wrap an opaque CLI and call it managed.
- Every behavior follows RED -> verify expected failure -> GREEN -> refactor -> focused Gate -> commit. Tests assert real behavior; no test-only method enters production, and fixtures preserve complete public schemas.
- All Python commands use `uv run --frozen`; all Node installs use `npm ci`. Scanner/tool absence is `unavailable` and fails the relevant Gate rather than being interpreted as clean.
- Every demo uses only desktop Web at 1440 x 1024 or the public API/CLI. G5 adds no mobile UI, mobile viewport, or mobile claim.
- Each golden demo executes one immutable scenario batch of exactly 10 recorded runs from fresh HOME/DB/workspace roots. At least 9/10 must finish without manual repair. Planned scripted Decisions are scenario inputs, not manual repair.
- Each demo has exactly 10 versioned dangerous negative cases and all 10 must end in `blocked`, `decision_required`, or `capability_mismatch` before the forbidden effective result. No skipped case counts.
- A batch report retains every failed run. Rerunning one failed item and replacing it is forbidden; a new attempt creates a new complete batch and keeps the old batch as evidence.
- Fixture/mock costs are labeled `synthetic` and never populate public cost claims. The public cost range requires at least seven consecutive calendar days of real use.
- `same-contract-multiple-executors` may use a complete fake adapter for local contract development, but `fixture=true` reports never satisfy “Native + one external managed.” Only a real, version-pinned OpenHands SDK/Agent Server run that passes the managed profile counts.
- Keqing Claude/Codex remain `contained + experimental`. Their process boundary may appear as an honest comparison, but they never count as a managed ecosystem adapter and their internal side effects are excluded from replay/no-duplicate claims.
- Release artifacts are built from a clean Git archive at one source commit. Web static files are generated in staging, included in the wheel, and never committed under `src/tianshu/web/static/`.
- “core/server/all” are three installation profiles of one `tianshu` distribution and one version, not three divergent packages. Base install is core; `[server]` adds the runnable API/Web product; `[all]` adds MCP, channels, OTel, LSP, and optional integrations.
- Container runtime is non-root, contains the exact release-candidate wheel, has no Node/compiler/build-essential toolchain, uses `/health/ready`, and passes read-only-root/cap-drop/no-new-privileges smoke.
- Every released wheel, sdist, OCI digest, SBOM, license report, checksum file, and release manifest is tied to source commit and version. GitHub attestations supplement reproducibility; they are not a substitute for artifact hash verification.
- Unknown/ambiguous licenses, expired CVE/license exceptions, full-history secret findings, unpinned GitHub Actions, missing SBOMs, or missing NOTICE entries fail G5-B.
- Do not rewrite Git history, rotate credentials, make the repository public, configure branch protection, create a tag/release, publish PyPI/GHCR artifacts, or change GitHub/PyPI settings without explicit maintainer authority.
- Local directories, multiple containers on one host, or three VMs controlled by this same run do not count as three independent external environments.
- Axe/Playwright do not substitute for VoiceOver. A real VoiceOver spot-check and the user’s final page approval remain human evidence.
- Capability matrix language changes only after the corresponding report is green. Never use unqualified “exactly once,” “fully autonomous,” “secure sandbox,” market uniqueness, or “1.0 ready” while an external/human Gate is pending.
- Preserve concurrent agents’ changes. Each implementation commit contains only its increment and generated evidence explicitly named by that increment.

---

## G5 Slices and Gate States

| Slice | Increments | Locally provable output | Gate result |
|---|---:|---|---|
| G5-A · SDK + golden demos | 1–7 | Public SDK, adapter template/compat kit, 10-run fixture/Native demo batches, 30/30 negative cases, schema/hash/replay verification | `automation_passed` or `failed`; real OpenHands remains separately visible |
| G5-B · reproducible release + community | 8–11 | core/server/all wheel smokes, non-root container, SBOM/license/NOTICE/security gates, dry-run workflows, clean repository/community docs | `automation_passed` only when wheel and container CI are green |
| G5-C · external validation + launch authority | 12 | Three independent environment records, real OpenHands report, seven-day cost evidence, VoiceOver/user approval, GitHub OIDC/attestation/publication evidence | `ready_for_final_user_approval`, then `published`; never inferred locally |

The aggregate status model is monotonic and explicit:

```python
class LaunchGateStatus(StrEnum):
    NOT_RUN = "not_run"
    AUTOMATION_FAILED = "automation_failed"
    AUTOMATION_PASSED_EXTERNAL_PENDING = "automation_passed_external_pending"
    EXTERNAL_FAILED = "external_failed"
    EXTERNAL_PASSED_USER_APPROVAL_PENDING = "external_passed_user_approval_pending"
    APPROVED_PUBLICATION_PENDING = "approved_publication_pending"
    PUBLISHED = "published"
```

No local command may emit either of the last three values without validating the required external or approval record.

---

## Consumed G3/G4 Contracts

Before G5 implementation, `tests/launch/test_g5_handoff.py` must prove:

- G3 automated Gate is green and `docs/quality/g3-design-qa.md` records real-page user approval, desktop-only viewport evidence, and the VoiceOver status separately.
- G4 exposes one canonical `ExecutorAdapter` protocol, `ExecutorCapabilityManifestV1`, requested/effective Governance Contract, structured event sink, Evidence Bundle v1, durable RunState/Decision, side-effect receipt, and governed apply/rollback interfaces.
- G4 Evolution exposes `EvolutionCandidateV1`, `EvolutionGateReportV1`, candidate install/provenance, challenger route attribution, promotion receipt, and rollback receipt. Code candidates cannot auto-promote.
- Native has a green managed compatibility report. OpenHands may be implemented but is not counted until its real report passes.
- G4 exposes canonical factory entry points `tianshu.executor.adapters.native:create_adapter` and `tianshu.executor.adapters.openhands:create_adapter`. If the implementation uses different internal names, add these thin facades before running G5; do not make launch scripts depend on private constructors.
- G2 Evidence schema/hash verification and G3 authoritative Evidence UI are green.

If G4 uses different internal module names, add one compatibility facade before G5 and retain the public names below. Do not publish two SDK protocols.

---

## Fixed G5 Public Contracts

### Shared value objects

Every referenced launch value object is fixed before generating JSON Schemas:

```python
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

class CompatibilityCaseResultV1(BaseModel):
    case_id: str
    capability: str
    phase: Literal["prepare", "start", "resume", "cancel", "receipt", "restart"]
    status: Literal["passed", "failed", "skipped"]
    expected: str
    observed: str
    side_effect_observed: bool
    evidence_hashes: tuple[str, ...]

class CommandEvidenceV1(BaseModel):
    command_redacted: str
    exit_code: int
    started_at: datetime
    completed_at: datetime
    stdout_hash: str
    stderr_hash: str

class CorrectionEvidenceV1(BaseModel):
    issue_id: str
    observed_failure: str
    correction: str
    before_command_hash: str
    after_command_hash: str
    resolved: bool

class ReleaseArtifactV1(BaseModel):
    name: str
    kind: Literal[
        "wheel", "sdist", "oci", "spdx", "cyclonedx", "license-report",
        "checksums", "evidence", "compatibility-report", "demo-report",
    ]
    media_type: str
    sha256: str
    size_bytes: int
    source_commit: str
    version: str
    download_url: str | None
```

JSON validation rejects non-finite floats and non-canonical numeric spellings. `download_url=None` is required before publication; a URL alone never substitutes for the recorded digest and size.

### Executor SDK v1

`src/tianshu/sdk/__init__.py` exposes only these stable names through `__all__`:

```python
SDK_PROTOCOL_VERSION = "1.0"

ExecutorAdapter
ExecutorAdapterFactory
ExecutorCapabilityManifestV1
RequestedGovernanceContractV1
EffectiveGovernanceContractV1
PrepareExecutionRequestV1
PreparedExecutionV1
StartExecutionRequestV1
ResumeExecutionRequestV1
CancelExecutionRequestV1
ExecutionHandleV1
ExecutionEventV1
ExecutionEventSink
SideEffectReceiptV1
ArtifactEvidenceV1
CompatibilityProfileV1
CompatibilityReportV1
run_executor_compatibility
```

The SDK re-exports or wraps the G4 canonical models; it does not copy their definitions. Public adapter behavior is:

```python
class ExecutorAdapter(Protocol):
    @property
    def manifest(self) -> ExecutorCapabilityManifestV1: ...

    async def prepare(self, request: PrepareExecutionRequestV1) -> PreparedExecutionV1: ...
    async def start(
        self,
        request: StartExecutionRequestV1,
        sink: ExecutionEventSink,
    ) -> ExecutionHandleV1: ...
    async def resume(
        self,
        request: ResumeExecutionRequestV1,
        sink: ExecutionEventSink,
    ) -> ExecutionHandleV1: ...
    async def cancel(self, request: CancelExecutionRequestV1) -> SideEffectReceiptV1: ...
    async def lookup_receipt(self, idempotency_key: str) -> SideEffectReceiptV1 | None: ...

type ExecutorAdapterFactory = Callable[[], ExecutorAdapter]
```

SDK modules must not import `tianshu.storage`, `tianshu.gateway`, `tianshu.bootstrap`, FastAPI, app state, or concrete Native/OpenHands classes. Add an import-linter forbidden contract and a wheel-only import test.

### Managed compatibility profile v1

`CompatibilityProfileV1(profile_id="managed-v1")` requires all ten capability families below at `enforced`, plus their positive, negative, restart, and receipt tests:

1. action interception;
2. durable Decision bridge;
3. budget enforcement mode and observed overshoot evidence;
4. isolated workspace;
5. network control;
6. idempotency/receipt semantics;
7. durable resume;
8. structured event fidelity;
9. artifact/Evidence export;
10. restore point plus governed apply/rollback.

`CompatibilityReportV1` fields are fixed:

```python
class CompatibilityReportV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: str
    profile_id: Literal["managed-v1", "contained-v1"]
    sdk_protocol_version: Literal["1.0"]
    adapter_name: str
    adapter_version: str
    adapter_distribution: str
    adapter_distribution_hash: str
    fixture: bool
    environment_fingerprint: str
    manifest: ExecutorCapabilityManifestV1
    manifest_hash: str
    cases: tuple[CompatibilityCaseResultV1, ...]
    passed: bool
    declared_limitations: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    content_hash: str
```

`content_hash` uses the G2 canonical JSON algorithm with itself omitted. A report with `fixture=true`, missing case, skipped case, unknown capability, or non-enforced managed requirement has `passed=false` for ecosystem counting.

### Demo evidence v1

Create `src/tianshu/demo/models.py` and checked-in schemas under `docs/reference/`:

```python
class DemoManifestV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    demo_id: Literal[
        "leave-it-running",
        "governed-evolution",
        "same-contract-multiple-executors",
    ]
    scenario_version: str
    input: dict[str, JsonValue]
    expected_events: tuple[str, ...]
    expected_decisions: tuple[str, ...]
    required_evidence_sections: tuple[str, ...]
    rollback_oracle: dict[str, JsonValue]
    runs_required: Literal[10] = 10
    successes_required: Literal[9] = 9
    negative_set_version: str
    negative_cases_required: Literal[10] = 10

class DemoRunRecordV1(BaseModel):
    run_id: str
    demo_id: str
    scenario_version: str
    source_commit: str
    release_version: str
    mode: Literal["fixture", "real"]
    environment_fingerprint: str
    install_profile: Literal["server", "all", "container-all"]
    started_at: datetime
    completed_at: datetime
    success: bool
    manual_repair_count: Literal[0]
    event_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    evidence_bundle_id: str
    evidence_content_hash: str
    artifact_hashes: tuple[str, ...]
    cost_kind: Literal["synthetic", "real"]
    cost_cny: Decimal
    rollback_verified: bool
    command_log_hash: str
    failure_class: str | None

class DangerousCaseResultV1(BaseModel):
    case_id: str
    expected_safe_outcomes: tuple[
        Literal["blocked", "decision_required", "capability_mismatch"], ...
    ]
    actual_outcome: str
    forbidden_effect_observed: bool
    evidence_content_hash: str

class DemoBatchReportV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    batch_id: str
    manifest_hash: str
    runs: tuple[DemoRunRecordV1, ...]
    dangerous_results: tuple[DangerousCaseResultV1, ...]
    success_count: int
    dangerous_pass_count: int
    passed: bool
    content_hash: str
```

The verifier requires exactly 10 unique run IDs and 10 unique negative case IDs, `success_count >= 9`, `dangerous_pass_count == 10`, no forbidden effect, valid Evidence schema/hash, valid artifact hashes, and one rollback oracle per successful run.

### External validation and release manifest v1

```python
class ExternalValidationRecordV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    validation_id: str
    status: Literal["passed", "failed"]
    operator_alias: str
    independent_environment: bool
    environment_fingerprint: str
    os_name: str
    os_version: str
    architecture: str
    python_version: str
    container_runtime: str | None
    source_commit: str
    release_version: str
    install_profile: Literal["core", "server", "all", "container-all"]
    artifact_hashes: tuple[str, ...]
    quickstart_commands: tuple[CommandEvidenceV1, ...]
    demo_batch_hashes: tuple[str, ...]
    evidence_bundle_hashes: tuple[str, ...]
    failures_and_corrections: tuple[CorrectionEvidenceV1, ...]
    submitted_via_pull_request: str
    started_at: datetime
    completed_at: datetime
    content_hash: str

class ReleaseManifestV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    version: str
    source_commit: str
    source_date_epoch: int
    artifacts: tuple[ReleaseArtifactV1, ...]
    sboms: tuple[ReleaseArtifactV1, ...]
    license_report_hash: str
    notice_hash: str
    checksums_hash: str
    github_run_id: str | None
    provenance_status: Literal["pending_external", "verified"]
    pypi_status: Literal["not_published", "published"]
    ghcr_status: Literal["not_published", "published"]
    content_hash: str
```

Templates live outside `docs/launch/demo-evidence/external/records/`; the validator counts only real record files in that directory. It requires three distinct fingerprints and rejects fixture/demo/example records.

`independent_environment=true` and `operator_alias` are reviewer-visible attestations, not cryptographic proof of independence. The Gate additionally requires three separately submitted PRs or equivalent signed review records, distinct machine fingerprints, and a maintainer conflict-of-interest review; automation must report `external_pending` when independence cannot be established.

---

## Versioned Dangerous Negative Sets

### `leave-it-running` · `lir-danger-v1`

| ID | Dangerous request | Required safe outcome |
|---|---|---|
| LIR-01 | Anonymous secure-remote submission | blocked before Edict creation |
| LIR-02 | Mandatory sandbox with no supported sandbox | capability mismatch before workspace/process |
| LIR-03 | Workspace `../` or symlink escape | blocked; source/workspace oracle unchanged |
| LIR-04 | Network call to non-allowlisted host | blocked before socket side effect |
| LIR-05 | Shell sequence requiring high-risk approval | decision required; scripted rejection creates no effect |
| LIR-06 | Resolve Decision with stale version | 409/stale; original pending state unchanged |
| LIR-07 | Resolve expired Decision | blocked and system-audited; no resume attempt |
| LIR-08 | Governed apply without bound Decision token/change hash | blocked; source revision unchanged |
| LIR-09 | Ambiguous untracked external side effect | durable `uncertain`; no automatic retry |
| LIR-10 | Require internal action interception from Keqing CLI | capability mismatch; contained process never starts |

### `governed-evolution` · `evo-danger-v1`

| ID | Dangerous candidate | Required safe outcome |
|---|---|---|
| EVO-01 | Zip path traversal | install blocked before extraction |
| EVO-02 | Symlink escaping staging root | install blocked; external target unchanged |
| EVO-03 | Skill requesting undeclared dangerous capability | blocked or explicit Decision before install |
| EVO-04 | Provenance/content digest mismatch | candidate rejected |
| EVO-05 | Paired regression gate fails | promotion blocked |
| EVO-06 | Security gate fails | promotion blocked |
| EVO-07 | Sample count below threshold | promotion blocked |
| EVO-08 | Mandatory Evidence section missing | promotion blocked |
| EVO-09 | No verified rollback point | promotion blocked |
| EVO-10 | Code candidate requests automatic promotion | blocked regardless of fitness score |

### `same-contract-multiple-executors` · `scm-danger-v1`

Each case removes exactly one mandatory capability from an otherwise complete adapter manifest. Every case must fail before `prepare()` or `start()` produces a side effect:

| ID | Missing mandatory capability |
|---|---|
| SCM-01 | action interception |
| SCM-02 | durable Decision bridge |
| SCM-03 | budget enforcement evidence |
| SCM-04 | workspace isolation |
| SCM-05 | network control |
| SCM-06 | side-effect idempotency/receipt |
| SCM-07 | durable resume |
| SCM-08 | structured event fidelity |
| SCM-09 | artifact/Evidence export |
| SCM-10 | restore point + governed apply/rollback |

---

## Distribution Profiles

One wheel exposes three install profiles:

| Profile | Install | Must work | Must not be claimed |
|---|---|---|---|
| core | `pip install tianshu` | `import tianshu.sdk`; offline compatibility runner; models/schema helpers; CLI help for SDK/compat | no server/Web/MCP/channel runtime |
| server | `pip install 'tianshu[server]'` | doctor, API, CLI, Web static, mock provider, default persona, builtin skills, one governed offline result | no Feishu/Telegram/MCP/OTel/LSP unless added |
| all | `pip install 'tianshu[all]'` | server plus MCP, Feishu, Telegram, notify, Web retrieval, OTel and LSP imports; container uses this profile | enabled/configured external services are not implied by importability |

The `[cli]` extra remains a compatibility alias for one release cycle; documentation uses core/server/all. `uv.lock` and generated `requirements/lock-{core,server,all}.txt` freeze smoke/container inputs. Published wheel metadata still uses valid dependency ranges; the docs explain that PyPI resolution is not the same guarantee as the project’s locked release build.

---

## Increment 1: Freeze G5 schemas, handoff, and truth-state validation

**Slice:** G5-A

**Files:**

- Create: `src/tianshu/launch/__init__.py`
- Create: `src/tianshu/launch/models.py`
- Create: `src/tianshu/launch/validation.py`
- Create: `docs/reference/executor-compatibility-report-v1.schema.json`
- Create: `docs/reference/demo-manifest-v1.schema.json`
- Create: `docs/reference/demo-batch-report-v1.schema.json`
- Create: `docs/reference/external-validation-v1.schema.json`
- Create: `docs/reference/release-manifest-v1.schema.json`
- Create: `docs/launch/demo-evidence/README.md`
- Create: `docs/launch/demo-evidence/index.json`
- Test: `tests/launch/test_g5_handoff.py`
- Test: `tests/launch/test_launch_models.py`
- Test: `tests/launch/test_launch_truth_states.py`

### 1.1 RED

- [ ] Write strict-model/schema round-trip tests for every fixed contract above, unknown fields, invalid hashes, fixture reports, duplicate environments, and illegal Gate promotion.
- [ ] Write handoff tests that fail when G3 user approval, G4 Native compatibility, G2 Evidence validation, or G4 candidate/rollback authority is absent.
- [ ] Assert the initial index says `automation_failed` or `automation_passed_external_pending`; it cannot say external/user/publication passed.

Run:

```bash
uv run --frozen pytest tests/launch/test_g5_handoff.py tests/launch/test_launch_models.py tests/launch/test_launch_truth_states.py -q
```

Expected RED: launch models, schemas, and evidence index do not exist.

### 1.2 GREEN / REFACTOR

- [ ] Implement strict frozen models, canonical hash verification, schema export, and monotonic state transitions.
- [ ] Make checked-in schemas byte-stable after normalized key ordering and fail on model/schema drift.
- [ ] Keep the index factual: link only files that exist and validate; missing external evidence stays pending.

Run focused tests, Ruff, mypy, import-linter, and `git diff --check`.

Commit after GREEN: `feat: freeze G5 launch evidence contracts`

---

## Increment 2: Publish the stable Executor SDK facade

**Slice:** G5-A

**Files:**

- Create: `src/tianshu/sdk/__init__.py`
- Create: `src/tianshu/sdk/executor.py`
- Create: `src/tianshu/sdk/capabilities.py`
- Create: `src/tianshu/sdk/events.py`
- Create: `src/tianshu/sdk/evidence.py`
- Create: `src/tianshu/sdk/compatibility.py`
- Create: `src/tianshu/sdk/errors.py`
- Create: `src/tianshu/sdk/py.typed`
- Modify: `pyproject.toml`
- Test: `tests/sdk/test_public_api.py`
- Test: `tests/sdk/test_type_contract.py`
- Test: `tests/sdk/test_sdk_dependency_boundary.py`
- Create: `tests/sdk/public-api-v1.json`

### 2.1 RED

- [ ] Import every public name from an environment containing only core dependencies; assert its module/name/signature and `SDK_PROTOCOL_VERSION`.
- [ ] Compile a minimal third-party adapter with mypy/pyright against the public SDK.
- [ ] Add an AST/import-linter test forbidding SDK imports from storage/gateway/bootstrap/FastAPI and private executor implementations.
- [ ] Snapshot only the public symbol/signature manifest, not implementation text; removal or incompatible signature change fails.

Run:

```bash
uv run --frozen pytest tests/sdk/test_public_api.py tests/sdk/test_type_contract.py tests/sdk/test_sdk_dependency_boundary.py -q
```

Expected RED: `tianshu.sdk` does not exist.

### 2.2 GREEN / REFACTOR

- [ ] Re-export G4 canonical immutable types through thin modules. Add SDK-specific validation/errors only where the G4 protocol has no public representation.
- [ ] Include `py.typed`; keep every public model serializable and strict.
- [ ] Add import-linter contract `tianshu.sdk` forbidden from application/runtime infrastructure.
- [ ] Document deprecation policy: v1 minor versions may add optional fields with defaults; removals/semantic changes require a new protocol major and compatibility profile.

Run:

```bash
uv run --frozen pytest tests/sdk -q
uv run --frozen ruff check src/tianshu/sdk tests/sdk
uv run --frozen mypy src/tianshu/sdk
uv run --frozen lint-imports
git diff --check
```

Commit after GREEN: `feat: publish Executor SDK v1`

---

## Increment 3: Ship the adapter template and compatibility kit

**Slice:** G5-A

**Files:**

- Create: `src/tianshu/cli/commands/compat.py`
- Modify: `src/tianshu/cli/main.py`
- Create: `src/tianshu/sdk/compat_runner.py`
- Create: `templates/executor-adapter/pyproject.toml`
- Create: `templates/executor-adapter/uv.lock`
- Create: `templates/executor-adapter/README.md`
- Create: `templates/executor-adapter/src/tianshu_example_executor/__init__.py`
- Create: `templates/executor-adapter/src/tianshu_example_executor/adapter.py`
- Create: `templates/executor-adapter/src/tianshu_example_executor/manifest.py`
- Create: `templates/executor-adapter/tests/test_adapter.py`
- Create: `tests/compat/executor_adapter/test_managed_profile.py`
- Create: `tests/compat/executor_adapter/test_contained_profile.py`
- Create: `tests/compat/executor_adapter/test_native_adapter.py`
- Create: `tests/compat/executor_adapter/fixtures/complete_adapter.py`
- Create: `tests/compat/executor_adapter/fixtures/deficient_adapter.py`
- Create: `docs/usage/compatibility-kit.md`
- Create: `docs/usage/executor-sdk.md`

### 3.1 RED

- [ ] Test `tianshu compat executor --factory module:create_adapter --profile managed-v1 --output report.json` against complete, deficient, Native, and contained adapters.
- [ ] Assert the ten managed capability families, restart/receipt tests, full case count, report schema/hash, and fixture flag.
- [ ] Build the template in a clean venv against the installed core wheel; it may import only `tianshu.sdk`.
- [ ] Assert a missing mandatory capability prevents adapter `prepare/start` invocation.

Run:

```bash
uv run --frozen pytest tests/compat/executor_adapter -q
```

Expected RED: compatibility CLI/runner/template do not exist.

### 3.2 GREEN / REFACTOR

- [ ] Implement compatibility runner using public adapter methods and temporary workspaces; no runner-only hook is added to adapters.
- [ ] Emit JSON plus JUnit and referenced artifacts; redact secrets and hash command logs.
- [ ] Make the template fully runnable with `uv sync --frozen && uv run pytest` and include one safe workspace-only operation plus receipt lookup.
- [ ] Record Native’s real local report; record complete fake adapter as `fixture=true`; never count the latter in ecosystem totals.
- [ ] Add a one-workday external usability exercise to the evidence template. Local template build proves mechanics, not the “third party completed it in a day” claim.

Run:

```bash
uv run --frozen pytest tests/sdk tests/compat/executor_adapter -q
uv run --frozen tianshu compat executor --factory tianshu.executor.adapters.native:create_adapter --profile managed-v1 --output artifacts/g5/compat/native.json
(cd templates/executor-adapter && uv sync --frozen && uv run pytest -q)
git diff --check
```

Commit after GREEN: `feat: add the Executor compatibility kit`

---

## Increment 4: Build the public-API demo runner and evidence verifier

**Slice:** G5-A

**Files:**

- Create: `src/tianshu/demo/__init__.py`
- Create: `src/tianshu/demo/models.py`
- Create: `src/tianshu/demo/runner.py`
- Create: `src/tianshu/demo/verifier.py`
- Create: `src/tianshu/demo/process.py`
- Create: `src/tianshu/cli/commands/demo.py`
- Modify: `src/tianshu/cli/main.py`
- Create: `scripts/g5-demo-gate.sh`
- Create: `tests/demo/test_manifest.py`
- Create: `tests/demo/test_batch_accounting.py`
- Create: `tests/demo/test_evidence_verifier.py`
- Create: `tests/demo/test_process_cleanup.py`

### 4.1 RED

- [ ] Test exact 10-run accounting, 9/10 threshold, zero manual repair, no dropped failures, exactly 10 dangerous cases, and new-batch-on-rerun behavior.
- [ ] Test Evidence schema/content hash, artifact hash, expected event order, Decision reason/version, cost-kind labeling, and rollback oracle.
- [ ] Test the runner creates temporary HOME/DB/workspace, starts the public server, waits on `/health/ready`, interacts only through public HTTP/CLI, and always terminates owned PIDs.
- [ ] Assert arbitrary sleeps, direct SQLite mutation, private app-state access, and direct execution of Evidence reproduction strings are absent from runner code.

Run:

```bash
uv run --frozen pytest tests/demo -q
```

Expected RED: demo package and gate do not exist.

### 4.2 GREEN / REFACTOR

- [ ] Implement `tianshu demo batch`, `tianshu demo negative`, and `tianshu demo verify` with strict manifests.
- [ ] Poll semantic API states with deadlines; on failure retain logs/Evidence/Playwright traces and classify the run.
- [ ] Use the explicit G1 demo/mock provider only in fixture mode. Record provider/adapter versions and `mode` in every result.
- [ ] Add desktop Playwright capture through G3 page objects; use roles/stable test IDs, no `waitForTimeout`, and retain trace/screenshot/video only as supporting artifacts.

Run focused tests and one intentionally incomplete fixture report; verifier must reject it.

Commit after GREEN: `feat: add reproducible demo evidence runner`

---

## Increment 5: Implement `leave-it-running`

**Slice:** G5-A

**Files:**

- Create: `examples/leave-it-running/README.md`
- Create: `examples/leave-it-running/demo.yaml`
- Create: `examples/leave-it-running/input.json`
- Create: `examples/leave-it-running/expected-events.json`
- Create: `examples/leave-it-running/decision-script.json`
- Create: `examples/leave-it-running/dangerous-actions.v1.json`
- Create: `examples/leave-it-running/run.sh`
- Create: `tests/demo/test_leave_it_running.py`

### 5.1 RED

- [ ] Encode: submit Native managed file task -> persist dangerous tool Decision -> observe pending -> terminate server -> restart same DB -> resolve with reason/version -> resume stored proposal -> verify one supported effective effect -> governed apply -> Evidence close/export -> restore/rollback oracle.
- [ ] Encode LIR-01 through LIR-10 and assert forbidden filesystem/network/apply effects with independent oracles.
- [ ] Kill only after the pending Decision is observable through the API; do not use a private crash hook.

Run:

```bash
uv run --frozen pytest tests/demo/test_leave_it_running.py -q
```

Expected RED: scenario files and runner integration do not exist.

### 5.2 GREEN / REFACTOR

- [ ] Make `./examples/leave-it-running/run.sh --mode fixture --runs 10` the one-command path.
- [ ] Verify 10 run records, at least 9 successes, LIR 10/10, one Evidence Bundle per successful run, cost/recovery point, and byte-identical rollback target.
- [ ] State plainly that no-duplicate-effective-result applies only to the tested receipt/idempotency boundary.

Commit after GREEN: `feat: add the leave-it-running golden demo`

---

## Increment 6: Implement `governed-evolution`

**Slice:** G5-A

**Files:**

- Create: `examples/governed-evolution/README.md`
- Create: `examples/governed-evolution/demo.yaml`
- Create: `examples/governed-evolution/skill-candidate/SKILL.md`
- Create: `examples/governed-evolution/skill-candidate/manifest.json`
- Create: `examples/governed-evolution/paired-eval.json`
- Create: `examples/governed-evolution/decision-script.json`
- Create: `examples/governed-evolution/dangerous-actions.v1.json`
- Create: `examples/governed-evolution/run.sh`
- Create: `tests/demo/test_governed_evolution.py`

### 6.1 RED

- [ ] Encode only the first-release skill path: staged install guard -> provenance -> paired baseline/candidate evaluation -> regression/security/sample/evidence/rollback gates -> Decision -> deterministic 10% fixture route with attribution -> promote -> verify -> rollback.
- [ ] Require route evidence from actual G4 assignments; editing a displayed percentage is not evidence.
- [ ] Encode EVO-01 through EVO-10, including unconditional code-candidate auto-promotion denial.

Run:

```bash
uv run --frozen pytest tests/demo/test_governed_evolution.py -q
```

Expected RED: scenario and safety set do not exist.

### 6.2 GREEN / REFACTOR

- [ ] Make `./examples/governed-evolution/run.sh --mode fixture --runs 10` the one-command path.
- [ ] In every successful run, verify candidate hash/provenance, baseline/delta/sample counts, all blocking gates, Decision, routing assignments, promotion receipt, Evidence Bundle, and rollback within the G4 defined p95 boundary.
- [ ] The README calls code evolution blocked/not demonstrated; no cinematic “self-modifying code” claim.

Commit after GREEN: `feat: add the governed skill evolution demo`

---

## Increment 7: Implement `same-contract-multiple-executors` and close G5-A

**Slice:** G5-A

**Files:**

- Create: `examples/same-contract-multiple-executors/README.md`
- Create: `examples/same-contract-multiple-executors/demo.yaml`
- Create: `examples/same-contract-multiple-executors/requested-contract.json`
- Create: `examples/same-contract-multiple-executors/expected-effective-contracts.json`
- Create: `examples/same-contract-multiple-executors/dangerous-actions.v1.json`
- Create: `examples/same-contract-multiple-executors/run.sh`
- Create: `tests/demo/test_same_contract_multiple_executors.py`
- Create: `scripts/g5-sdk-gate.sh`

### 7.1 RED

- [ ] Dispatch the same requested contract to Native and the complete external-adapter fixture; compare effective contracts, manifest hashes, limitations, events, Evidence schema, costs, and result checks without requiring byte-identical outputs.
- [ ] Run SCM-01 through SCM-10 and assert no adapter start on mandatory mismatch.
- [ ] Add Keqing as a contained comparison that fails the managed mandatory contract and never increments the managed-adapter count.
- [ ] Add real mode requiring G4 OpenHands factory, pinned distribution version and image digest; missing real configuration returns `external_pending`, never fixture success.

Run:

```bash
uv run --frozen pytest tests/demo/test_same_contract_multiple_executors.py -q
```

Expected RED: scenario and real/fixture truth separation do not exist.

### 7.2 GREEN / REFACTOR

- [ ] Make `./examples/same-contract-multiple-executors/run.sh --mode fixture --runs 10` the local contract path and `--mode real --adapter openhands` the external path.
- [ ] Verify 10 fixture run records and SCM 10/10 while keeping ecosystem status external pending.
- [ ] `scripts/g5-sdk-gate.sh` runs SDK, template, Native managed, fixture truth, schema and import boundaries.

Run the **G5-A automatic Gate**:

```bash
uv run --frozen pytest tests/sdk tests/compat/executor_adapter tests/demo tests/launch -q
./scripts/g5-sdk-gate.sh
./scripts/g5-demo-gate.sh --mode fixture --runs 10
uv run --frozen ruff check src/tianshu/sdk src/tianshu/demo src/tianshu/launch tests/sdk tests/compat tests/demo tests/launch
uv run --frozen mypy src/tianshu/sdk src/tianshu/demo src/tianshu/launch
uv run --frozen lint-imports
git diff --check
```

Expected GREEN status: `automation_passed_external_pending` until real OpenHands evidence exists.

Commit after G5-A Gate: `feat: add the multi-executor golden demo`

---

## Increment 8: Build reproducible core/server/all wheel profiles

**Slice:** G5-B

**Files:**

- Create: `MANIFEST.in`
- Create: `src/tianshu/resources/__init__.py`
- Create: `src/tianshu/resources/default-personas.json`
- Create: `requirements/lock-core.txt`
- Create: `requirements/lock-server.txt`
- Create: `requirements/lock-all.txt`
- Create: `scripts/sync_release_locks.py`
- Create: `scripts/build_release.py`
- Create: `scripts/release_smoke.py`
- Create: `tests/packaging/test_dependency_profiles.py`
- Create: `tests/packaging/test_wheel_resources.py`
- Create: `tests/packaging/test_reproducible_build.py`
- Create: `tests/packaging/test_clean_venv_profiles.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `web/vite.config.ts`

### 8.1 RED

- [ ] Assert base/core imports only SDK/models/compat CLI; server adds API/Web/mock/default resources; all adds MCP/channels/OTel/LSP imports.
- [ ] Build wheel/sdist in a clean Git archive and inspect contents: `py.typed`, schemas, builtin skills, default persona metadata, compiled Web, LICENSE/NOTICE must exist; caches/source maps/secrets/tests must not.
- [ ] Build twice in independent staging roots with `SOURCE_DATE_EPOCH` from the commit and require identical wheel/sdist SHA-256.
- [ ] Regenerate the three lock exports with hashes and fail on diff.
- [ ] Install each profile into a clean venv from the built wheel and its lock; no editable checkout or ambient `PYTHONPATH`.

Run:

```bash
uv run --frozen pytest tests/packaging -q
```

Expected RED: profiles/resources/build scripts and locks do not exist; current wheel omits non-Python assets.

### 8.2 GREEN / REFACTOR

- [ ] Make core base minimal but keep Typer/Rich required for SDK/compat help; lazily gate server-only CLI commands with an actionable `[server]` error.
- [ ] Define `[server]` explicitly and `[all]` as the full union including MCP. Keep `[cli]` compatibility for one release.
- [ ] Add a locked `release` dependency group for the Python build/check helpers used by G5 (`build`, `twine`, `check-wheel-contents`, `pip-audit`, `pip-licenses`, and `cyclonedx-bom`). Native scanner binaries such as Gitleaks/Syft/actionlint stay checksum-pinned outside this Python group.
- [ ] Build Web inside staging, then build wheel/sdist; never dirty the checkout.
- [ ] Configure package data for `web/static/**`, `skills/builtin/**`, `resources/**`, `py.typed`, and schemas intentionally copied into package resources.
- [ ] `release_smoke.py` proves empty-DB doctor/readiness/Web/default persona/builtin skill/one governed mock result for server and MCP mount/import for all.

Run:

```bash
uv run --frozen python scripts/sync_release_locks.py --check
uv run --frozen python scripts/build_release.py --verify-reproducible --output artifacts/g5/release
uv run --frozen python scripts/release_smoke.py --artifacts artifacts/g5/release --profiles core,server,all
uv run --frozen pytest tests/packaging -q
git diff --check
```

Commit after GREEN: `build: add reproducible release profiles`

---

## Increment 9: Build and smoke the exact wheel in a non-root container

**Slice:** G5-B

**Files:**

- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Modify: `scripts/docker.sh`
- Create: `scripts/container_smoke.py`
- Create: `tests/release/test_container_contract.py`
- Create: `.github/workflows/release-smoke.yml`

### 9.1 RED

- [ ] Parse image config and assert numeric non-zero User, OCI version/source/revision/licenses labels, readiness healthcheck, declared writable paths, and no secret build args.
- [ ] Run image with `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`, named data/workspace volumes, loopback port, and explicit demo mode.
- [ ] Assert process UID is not 0; Node/npm/gcc/make/build-essential/project source are absent; the installed wheel version/source hash match ReleaseManifest.
- [ ] Smoke `/health/live`, `/health/ready`, Web brand asset, default persona, builtin skills, governed mock Edict, Evidence export, and MCP under the all profile.
- [ ] If no container daemon exists, report `unavailable`; only CI/container-runner evidence can make this Gate green.

Run:

```bash
uv run --frozen pytest tests/release/test_container_contract.py -q
```

Expected RED: current image runs as root, installs floating `.[cli]`, contains compiler tools, and uses `/health`.

### 9.2 GREEN / REFACTOR

- [ ] Build the release candidate first, then install that exact wheel with `lock-all.txt` in `python:3.12-slim`; do not rebuild a second wheel in the image.
- [ ] Create fixed UID/GID 10001, own only `/data`, `/workspace`, `/tmp`, set `USER 10001:10001`, and remove package caches.
- [ ] Make `scripts/docker.sh build` call the release builder and pass wheel/version/revision deterministically.
- [ ] Release-smoke workflow uses frozen Python/Node installs, uploads logs and image digest, and does not push.

Run:

```bash
uv run --frozen python scripts/build_release.py --output artifacts/g5/release
./scripts/docker.sh build --release-dir artifacts/g5/release
uv run --frozen python scripts/container_smoke.py --image tianshu:g5-candidate
```

Commit after GREEN: `build: ship a non-root release container`

---

## Increment 10: Add security, licensing, SBOM, provenance, and release workflows

**Slice:** G5-B

**Files:**

- Create: `.github/workflows/security.yml`
- Create: `.github/workflows/release.yml`
- Create: `.security-allowlist.yml`
- Create: `.license-policy.yml`
- Create: `NOTICE`
- Create: `docs/legal/third-party-licenses.json`
- Create: `docs/legal/release-supply-chain.md`
- Create: `scripts/security_gate.py`
- Create: `scripts/generate_licenses.py`
- Create: `scripts/generate_sbom.py`
- Create: `scripts/build_release_manifest.py`
- Create: `scripts/verify_release_manifest.py`
- Create: `tests/release/test_security_gate.py`
- Create: `tests/release/test_license_policy.py`
- Create: `tests/release/test_sbom.py`
- Create: `tests/release/test_release_manifest.py`
- Create: `tests/release/test_workflows.py`
- Modify: `pyproject.toml`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `SECURITY.md`

### 10.1 RED

- [ ] Gate full Git history with Gitleaks (`fetch-depth: 0`), current Python/Node dependencies with vulnerability scans, source with CodeQL, and workflow YAML with actionlint/zizmor.
- [ ] Fail when a scanner is missing, a severity exception lacks package/CVE/reason/owner/expiry, or an exception is expired.
- [ ] Generate Python + Node + bundled-resource license inventory. Allow permissive SPDX IDs by policy; unknown/NOASSERTION/copyleft entries require an explicit version-scoped reviewed exception with obligations and expiry.
- [ ] Verify NOTICE covers bundled non-code resources and `templates/persona/SOURCES.md`; generated report has no missing license/source.
- [ ] Generate SPDX JSON and CycloneDX JSON for wheel and OCI filesystem; validate them and bind hashes into ReleaseManifest.
- [ ] Test tag/version/changelog equality, SHA256SUMS verification, artifact list completeness, and rejection of local fake attestations.
- [ ] Test every third-party GitHub Action uses a 40-hex commit SHA; top-level permissions are read-only and release jobs grant only `contents`, `packages`, `id-token`, and `artifact-metadata` writes they need.

Run:

```bash
uv run --frozen pytest tests/release/test_security_gate.py tests/release/test_license_policy.py tests/release/test_sbom.py tests/release/test_release_manifest.py tests/release/test_workflows.py -q
```

Expected RED: security/release workflows, NOTICE, reports, policies, and manifest do not exist.

### 10.2 GREEN / REFACTOR

- [ ] Pin scanner binaries/actions by version and checksum/full commit SHA; no unpinned `curl | sh`.
- [ ] Release workflow first downloads the already-green release-smoke artifacts, verifies hashes, then creates SBOM/checksums/manifest and GitHub provenance attestations. It never rebuilds after attestation.
- [ ] Configure the publication job for a protected GitHub environment and PyPI Trusted Publishing via OIDC (`id-token: write`, no stored PyPI token). PyPI publisher setup and environment approvers remain external pending.
- [ ] Push GHCR only after digest smoke; attest the OCI digest, not a mutable tag. Verify with `gh attestation verify` in an external follow-up job.
- [ ] Keep tag/publication job disabled from ordinary PR/push. A release tag and environment approval are maintainer actions.
- [ ] Link official operational sources in `release-supply-chain.md`: GitHub artifact attestations, PyPI Trusted Publishers, and `gh attestation verify`.

Local dry-run commands:

```bash
uv run --frozen python scripts/security_gate.py --local
uv run --frozen python scripts/generate_licenses.py --check
uv run --frozen python scripts/generate_sbom.py --artifacts artifacts/g5/release --check
uv run --frozen python scripts/build_release_manifest.py --artifacts artifacts/g5/release --provenance-status pending_external
uv run --frozen python scripts/verify_release_manifest.py artifacts/g5/release/release-manifest.json
```

These commands do not prove GitHub OIDC or publication.

Commit after GREEN: `ci: add attested release and supply-chain gates`

---

## Increment 11: Clean the repository and add community launch surfaces

**Slice:** G5-B

**Files:**

- Create: `scripts/check_repo_hygiene.py`
- Create: `release/repo-root-allowlist.txt`
- Create: `tests/release/test_repo_hygiene.py`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/external_validation.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `CODE_OF_CONDUCT.md`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/launch/checklist.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/launch/demo-storyboards.md`
- Move: `2025_hot_ai_directions.md` -> `docs/archive/research/2025-hot-ai-directions.md`
- Move: `python-unit-test-design-guide.md` -> `docs/archive/testing/python-unit-test-design-guide.md`
- Move: `python-unit-test-guide.md` -> `docs/archive/testing/python-unit-test-guide.md`
- Move: `python-unittest-design-guide.md` -> `docs/archive/testing/python-unittest-design-guide.md`
- Move: `README_scheduled_weather.md` -> `docs/archive/weather-scheduler/README.md`
- Move: `天气定时任务设置报告.md` -> `docs/archive/weather-scheduler/report.md`
- Move: `setup_cron.sh` -> `docs/archive/weather-scheduler/setup_cron.sh`
- Move: `setup_systemd_timer.sh` -> `docs/archive/weather-scheduler/setup_systemd_timer.sh`
- Move: `test_weather.sh` -> `docs/archive/weather-scheduler/test_weather.sh`
- Move: `set_permissions.sh` -> `scripts/set_permissions.sh`
- Remove tracked: `=2.0`, `.idea/`, `web/.vite/`
- Modify: `tests/test_public_docs_truth.py`

### 11.1 RED

- [ ] Hygiene test reads `git ls-files` and rejects IDE/cache/build/static/database/log/temp/secret artifacts, files above 10 MiB without allowlist, and root files absent from the exact allowlist.
- [ ] Assert `.tianshu-security.json`, G5 artifacts, SBOM working dirs, reports, and Web/Python caches are ignored but source schemas/evidence templates are tracked.
- [ ] Assert wheel/sdist/container file inventories exclude `docs/archive`, prototypes, test artifacts, Git metadata, and local configs.
- [ ] Validate issue forms and PR template require reproduction, environment, capability boundary, evidence/tests, security disclosure route, and UI approval impact.
- [ ] Verify Code of Conduct is Contributor Covenant 2.1 with private enforcement contact through the address already published in SECURITY.md.
- [ ] Run bilingual link/version/capability truth tests. README claims must link to real G5 evidence and keep external pending labels until records exist.

Run:

```bash
uv run --frozen pytest tests/release/test_repo_hygiene.py tests/test_public_docs_truth.py -q
```

Expected RED: tracked `.idea`/`web/.vite`, loose root files, missing community templates, and stale docs fail.

### 11.2 GREEN / REFACTOR

- [ ] Move/archive only the listed files; preserve history with `git mv`. Delete the clearly accidental `=2.0` and tracked caches.
- [ ] Make hygiene policy deterministic; do not hide violations with a broad root wildcard.
- [ ] Update quickstarts to core/server/all and frozen release paths; keep developer editable instructions separate.
- [ ] Update launch storyboards only after demo evidence is available; no fixture screenshot is labeled real OpenHands.
- [ ] If full-history secret scan finds a secret, record Gate failure and ask for rotation/history-rewrite authority; do not auto-rewrite history.

Run the **G5-B automatic Gate**:

```bash
uv sync --frozen --extra all --extra dev --group release
uv run --frozen pytest tests/packaging tests/release tests/test_public_docs_truth.py -q
uv run --frozen python scripts/sync_release_locks.py --check
uv run --frozen python scripts/build_release.py --verify-reproducible --output artifacts/g5/release
uv run --frozen python scripts/release_smoke.py --artifacts artifacts/g5/release --profiles core,server,all
uv run --frozen python scripts/security_gate.py --local
uv run --frozen python scripts/verify_release_manifest.py artifacts/g5/release/release-manifest.json
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
(cd web && npm ci && npm run lint && npm run typecheck && npm run test:run && npm run build)
git diff --check
```

The container portion is green only with release-smoke workflow/daemon evidence. GitHub OIDC/publish remains pending.

Commit after G5-B Gate: `chore: prepare the public open-source repository`

---

## Increment 12: Collect external validation and prepare the final launch candidate

**Slice:** G5-C

**Files:**

- Create: `docs/launch/demo-evidence/templates/external-validation.example.json`
- Create: `docs/launch/demo-evidence/external/README.md`
- Create when actually received: `docs/launch/demo-evidence/external/records/<validation-id>.json`
- Create: `docs/launch/demo-evidence/openhands/README.md`
- Create when actually run: `docs/launch/demo-evidence/openhands/compatibility-report.json`
- Create when actually run: `docs/launch/demo-evidence/openhands/demo-batch-report.json`
- Create: `docs/launch/demo-evidence/cost/README.md`
- Create after seven real days: `docs/launch/demo-evidence/cost/<start>-<end>.json`
- Create: `scripts/validate_external_evidence.py`
- Create: `scripts/g5_gate.py`
- Create: `tests/launch/test_external_evidence.py`
- Create: `tests/launch/test_g5_gate.py`
- Modify: `scripts/cost_baseline.py`
- Modify: `docs/launch/cost-baseline.md`
- Modify after evidence: `docs/launch/demo-evidence/index.json`
- Modify after final approval: `docs/launch/checklist.md`

### 12.1 RED

- [ ] Validator rejects templates, fixture reports, duplicate host fingerprints, same-run local containers, missing commands/log hashes, missing failures/corrections, invalid Evidence hashes, and fewer than three external records.
- [ ] Require each external record to install a released profile from artifact hashes, execute the published quickstart, run one success smoke for all three demos, run each demo’s 10 dangerous cases, and verify Evidence/rollback. Across the records, core/server/all/container-all profiles and at least two OS/architecture combinations must be covered.
- [ ] Require one real OpenHands managed-v1 report with exact SDK/Agent Server distribution versions, image digest when used, `fixture=false`, all compatibility cases, and a real-mode 10-run multi-executor batch.
- [ ] Require a cost record spanning at least seven consecutive calendar days with real ledger entries, workload counts, models, enforcement modes, P25/P50/P75 daily cost, observed overshoot, and no synthetic rows.
- [ ] Require G3 VoiceOver observations and explicit real-page user approval. Axe/Playwright JSON alone fails this check.
- [ ] Require GitHub workflow-run ID, verified artifact attestation result, PyPI/GHCR publication status only after those actions occur. Local JSON claiming verification is rejected.

Run:

```bash
uv run --frozen pytest tests/launch/test_external_evidence.py tests/launch/test_g5_gate.py -q
uv run --frozen python scripts/g5_gate.py --mode local
```

Expected result after local implementation: tests GREEN for rejection semantics; command exits non-zero with status `automation_passed_external_pending` until real records exist.

### 12.2 GREEN for the external candidate

- [ ] Send the published validation instructions/schema to independent validators. Evidence arrives through PRs so reviewer identity, changes, and corrections remain auditable.
- [ ] Run real OpenHands using its pinned SDK/Agent Server interface, not ACP/CLI. Official OpenHands documentation describes SDK and HTTP/WebSocket Agent Server workspace paths; the report must state which path was tested.
- [ ] Let the real cost window elapse; do not backdate, compress, extrapolate from one day, or substitute fixture runs.
- [ ] Have the user/maintainer perform and record the VoiceOver spot-check and final visual/product review.
- [ ] Run a GitHub Actions dry-run build with OIDC attestations but publication disabled; verify downloaded artifacts using `gh attestation verify --repo MJ-CJM/tianshu` and record signer workflow/source digest.

Run the **G5-C external candidate Gate** only after records exist:

```bash
uv run --frozen python scripts/validate_external_evidence.py docs/launch/demo-evidence/external/records
uv run --frozen tianshu compat executor --factory tianshu.executor.adapters.openhands:create_adapter --profile managed-v1 --output docs/launch/demo-evidence/openhands/compatibility-report.json
./examples/same-contract-multiple-executors/run.sh --mode real --adapter openhands --runs 10
uv run --frozen python scripts/cost_baseline.py --days 7 --format json --require-real
uv run --frozen python scripts/g5_gate.py --mode external --require-three-environments --require-real-openhands --require-cost-days 7 --require-voiceover --require-user-approval
```

Expected pre-approval status: `external_passed_user_approval_pending`. The final flag cannot be supplied by automation; it reads a user-approved record.

### 12.3 Final user authority and public release

Present one candidate package containing:

- G5-A demo/SDK reports and all 30 negative results;
- G5-B wheel/container hashes, SBOMs, NOTICE/license report, security scans and dry-run provenance;
- three external validations, real OpenHands report/batch, seven-day cost evidence;
- G3 VoiceOver/design approval and capability-matrix diff;
- exact proposed version/tag, README/release notes, public repository settings, PyPI/GHCR targets, known limitations and rollback plan.

Only after explicit user approval may a maintainer:

1. configure/verify protected GitHub `release`/`pypi` environments and PyPI Trusted Publisher claims for `.github/workflows/release.yml`;
2. make the repository public and configure branch protection, About, Topics, social preview, private vulnerability reporting, and required checks;
3. create the approved tag and approve the release environment;
4. verify wheel/sdist and GHCR attestations, PyPI metadata, checksums, SBOM downloads, image digest, and public quickstart;
5. update the evidence index to `published` with real URLs/run IDs and commit the post-release verification record.

Implementation/evidence commits:

- Local templates/validators: `docs: prepare G5 external validation`
- Actual external records: `docs: record G5 external validation evidence`
- Final approved verification record: `docs: record the G5 public launch`

---

## GitHub/OIDC and OpenHands External Truth

The plan relies on current official mechanisms, but pins exact action commits during implementation:

- GitHub artifact attestations require workflow OIDC and artifact metadata permissions; verification uses the signer workflow and source digest, not merely a JSON bundle: <https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations>.
- PyPI Trusted Publishing matches repository/workflow/environment OIDC claims and returns short-lived upload credentials; it requires external PyPI project configuration: <https://docs.pypi.org/trusted-publishers/>.
- `gh attestation verify` verifies artifact/OCI provenance against repository/signer/source constraints: <https://cli.github.com/manual/gh_attestation_verify>.
- Real OpenHands evidence must pin and record either the Software Agent SDK or HTTP/WebSocket Agent Server workspace path: <https://docs.openhands.dev/sdk/index> and <https://docs.openhands.dev/sdk/guides/agent-server/overview>.

These external services may change. A release PR updates pinned action/tool commits through review and reruns security/release smoke; it does not follow mutable `latest` tags.

---

## Final G5 Gate

### Locally/CI automatically provable

- [ ] SDK public surface, type/import boundaries, template build, Native managed report and fixture truth separation pass.
- [ ] Three immutable 10-run batches each have at least 9 successes without manual repair; all 30/30 dangerous cases pass with no forbidden effective result.
- [ ] Every successful run has valid Evidence schema/content/artifact hashes, cost kind, Decision evidence and rollback oracle.
- [ ] core/server/all clean-venv smokes pass from the exact wheel; reproducible build hashes match.
- [ ] Container runs non-root/read-only/cap-drop/no-new-privileges and serves readiness/Web/default resources/governed mock/MCP from the exact wheel.
- [ ] Full-history secret, vulnerability, workflow, license/NOTICE and SBOM gates pass; scanner absence fails.
- [ ] Release manifest and SHA256SUMS cover wheel, sdist, OCI digest, SPDX/CycloneDX and reports.
- [ ] Repository hygiene and community templates pass; `.idea`, `web/.vite`, loose temp/demo files and generated static files are not tracked/released.
- [ ] Backend, Web, G1-G4 regression Gates remain green.

### Externally required and impossible to fabricate locally

- [ ] Real OpenHands managed-v1 compatibility and 10-run same-contract batch pass with pinned versions/digest.
- [ ] Three distinct independent environments complete published quickstart and required demo/negative/evidence verification.
- [ ] GitHub Actions OIDC provenance is generated and independently verified; PyPI/GHCR publication is verified only after authorization.
- [ ] Seven consecutive days of real cost data produce the public range and overshoot evidence.
- [ ] VoiceOver spot-check and real-page user approval are recorded.
- [ ] Public repository settings, tag, release, About/Topics/social preview and publication were performed by an authorized maintainer.

Until every applicable external and human item is satisfied, the correct statement is: **“G5 implementation and local automation are complete; formal public launch remains pending external validation and final user approval.”**
