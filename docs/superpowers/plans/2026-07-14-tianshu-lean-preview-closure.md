# Tianshu Lean Developer Preview Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package one reproducible offline golden demo and a truthful Lean Developer Preview Candidate report from the completed S1–S5 Core capabilities, without performing any external publication.

**Architecture:** A public runner starts a fresh demo HOME/DB/workspace, invokes only public API/CLI surfaces, and records a fixed scenario from governed Edict through Decision, run, Evidence, skill candidate, Gate, canary assignment, and rollback. A verifier checks schema, hashes, expected states, forbidden effects, and source/artifact commit binding. Existing README/security/contribution/capability documents are aligned to the verified report; no stable SDK, container, registry, or multi-executor promise is added.

**Tech Stack:** Python 3.12, public Tianshu CLI/API, exact Wheel/sdist, JSON/Pydantic schemas, pytest, SQLite, Playwright desktop smoke, Markdown link/truth checks.

## Global Constraints

- Require passed reports for S1 G1.5, S2 Lean, S3 Core, S4 automation, and S5 Lean Core before Task 1.
- One golden demo only. Do not implement the other two G5 demos, stable Executor SDK, adapter kit, official container, SBOM/provenance, or external-environment validation.
- Demo is desktop Web/public API/CLI only; no private repository imports in the scenario runner after the exact Wheel is installed.
- Demo must be zero external network in demo profile. Loopback is permitted. Child-process network guarantees must match the actual ExecutionGateway contract and must not be overstated.
- Demo includes a planned Decision as scenario input; no ad-hoc manual repair may be hidden. A failed batch remains evidence and a rerun creates a new batch ID.
- Skill is the only candidate kind promoted/canaried in the golden demo. Code candidates may be shown blocked but never promoted.
- Every result is bound to source commit, exact Wheel SHA, manifest digest, environment facts, requested/effective contract, Evidence Bundle hash, candidate ID/gate hash, assignment ID, and rollback receipt.
- README and capability language must distinguish implemented, disabled, deferred, experimental, `external_pending`, and user-approval-pending.
- Remove tracked `.idea/` project files only as the separately approved D7 hygiene commit. Do not clean unrelated historical/temp files in that commit.
- Do not push, tag, publish, make public, configure OIDC, or announce 1.0.

---

### Task 1: Freeze candidate-report and demo-batch schemas

**Files:**
- Create: `src/tianshu/models/lean_preview.py`
- Create: `docs/reference/lean-preview-demo-report-v1.schema.json`
- Create: `docs/reference/lean-preview-candidate-report-v1.schema.json`
- Create: `tests/launch/test_lean_preview_schemas.py`

**Interfaces:**

```python
class LeanPreviewStepResultV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_id: str
    status: Literal["passed", "failed", "blocked", "decision_required"]
    started_at: datetime
    completed_at: datetime
    evidence_hashes: tuple[str, ...]
    observed_state_hash: str

class LeanPreviewDemoReportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    batch_id: str
    source_commit: str
    wheel_sha256: str
    environment_fingerprint: str
    fixture: bool
    steps: tuple[LeanPreviewStepResultV1, ...]
    evidence_bundle_id: str
    evidence_bundle_hash: str
    candidate_id: str
    gate_hash: str
    assignment_id: str
    rollback_receipt_hash: str
    external_pending: tuple[str, ...]
    content_hash: str

class LeanPreviewCandidateReportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    source_commit: str
    phase_report_hashes: dict[str, str]
    demo_report_hash: str
    wheel_sha256: str
    sdist_sha256: str
    capability_matrix_hash: str
    automation_status: Literal["passed", "failed"]
    visual_status: Literal["user_approval_pending", "user_approved"]
    publication_status: Literal["not_authorized"]
    deferred_work_ids: tuple[str, ...]
    content_hash: str
```

- [ ] **Step 1: Write RED strict-schema/hash tests**

Reject unknown fields, noncanonical hash, missing step, duplicate step IDs, fixture report counted as external evidence, missing phase hash, missing deferred IDs, `user_approved` without approval record, and publication status other than `not_authorized`.

- [ ] **Step 2: Implement models and generated schemas**

Reuse S3 canonical JSON helpers; do not define a second hashing algorithm. `content_hash` omits only itself.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_lean_preview_schemas.py -q
git add src/tianshu/models/lean_preview.py docs/reference/lean-preview-demo-report-v1.schema.json docs/reference/lean-preview-candidate-report-v1.schema.json tests/launch/test_lean_preview_schemas.py
git commit -m "feat: freeze Lean Preview evidence schemas"
```

### Task 2: Build the public runner and evidence verifier

**Files:**
- Create: `scripts/run_lean_preview_demo.py`
- Create: `scripts/verify_lean_preview_evidence.py`
- Create: `tests/launch/test_lean_preview_runner.py`
- Create: `tests/launch/test_lean_preview_verifier.py`
- Create: `examples/lean-governed-evolution/scenario.json`
- Create: `examples/lean-governed-evolution/README.md`

**Interfaces:**
- Consumes: exact installed Wheel CLI/API, demo provider, public Decision/Evidence/Evolution endpoints.
- Produces:

```text
tianshu-lean-demo --base-url http://127.0.0.1:7998 --scenario examples/lean-governed-evolution/scenario.json --batch-id "$BATCH_ID" --output-root docs/cc-fable-v1/evidence/lean-preview
python scripts/verify_lean_preview_evidence.py --report "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/demo-report.json" --artifact-root "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/artifacts"
```

- [ ] **Step 1: Write RED runner/verifier tests against a fake HTTP boundary**

Assert exact step order, bounded polling, correlation propagation, no private imports, redacted commands, failed step retention, timeout failure, missing/corrupt Evidence rejection, wrong commit/Wheel rejection, champion-only fake assignment rejection, and rollback receipt verification.

- [ ] **Step 2: Implement the public runner**

Use HTTP/CLI only after installation. The fixed scenario steps are:

```text
doctor_ready
submit_governed_edict
observe_decision_required
resolve_decision_with_reason
observe_completed_run
verify_evidence_bundle
propose_skill_candidate
evaluate_candidate_gate
start_skill_canary
submit_canary_eligible_run
verify_real_candidate_overlay
rollback_candidate
verify_new_run_uses_champion
```

- [ ] **Step 3: Implement the verifier**

Recompute every artifact/report hash, validate schema, verify all phase report hashes, ensure no failed/missing step, verify candidate overlay differs from champion for the assigned run, verify allocation zero and champion for post-rollback run, and require all D8 deferred IDs.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_lean_preview_runner.py tests/launch/test_lean_preview_verifier.py -q
git add scripts/run_lean_preview_demo.py scripts/verify_lean_preview_evidence.py tests/launch/test_lean_preview_runner.py tests/launch/test_lean_preview_verifier.py examples/lean-governed-evolution
git commit -m "feat: add the Lean Preview golden demo runner"
```

### Task 3: Run the exact-Wheel golden demo from a fresh HOME

**Files:**
- Create when run: `docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/demo-report.json`
- Create when run: `docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/artifacts/`
- Create: `tests/launch/test_lean_preview_fresh_wheel.py`
- Modify: `scripts/build_release.sh` only if the public runner needs an intentional console-script entry
- Modify: `pyproject.toml` only for that console-script entry

**Interfaces:**
- Consumes: one exact Wheel built after Web build, fresh HOME/DB/workspace, zero-external-network demo profile.
- Produces: one retained batch and verified report.

- [ ] **Step 1: Write the slow black-box RED test**

Build one Wheel, install outside the repository into Python 3.12, clear PYTHONPATH/user-site, use a HOME containing spaces/non-ASCII, start the installed server, run the public demo, verify evidence, SIGTERM, SQLite quick_check, and package-resource digest unchanged.

- [ ] **Step 2: Run the release build and slow test**

```bash
BATCH_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD)"
export BATCH_ID
rm -rf dist/lean-preview
./scripts/build_release.sh dist/lean-preview
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_lean_preview_fresh_wheel.py -q -s
```

Expected: one exact Wheel, one complete report, verifier exit 0, clean shutdown. If a batch fails, retain it and create a new batch for the corrected run.

- [ ] **Step 3: Verify the report explicitly**

```bash
env -u VIRTUAL_ENV .venv/bin/python scripts/verify_lean_preview_evidence.py --report "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/demo-report.json" --artifact-root "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/artifacts"
```

Expected: exit 0. `BATCH_ID` is the immutable value exported in Step 2 and recorded inside the report.

- [ ] **Step 4: Commit runner changes and evidence separately**

First commit any test-discovered runner/package correction with its RED test. Then stage only the verified batch and commit:

```bash
git add "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID"
git commit -m "test: record the Lean Preview golden demo evidence"
```

### Task 4: Align public truth and candidate documentation

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `SECURITY.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/launch/README.md`
- Modify: `docs/launch/capability-matrix.md`
- Modify: `docs/launch/checklist.md`
- Modify: `docs/launch/demo-storyboards.md`
- Create: `docs/usage/lean-developer-preview.md`
- Modify: `tests/test_public_docs_truth.py`

**Interfaces:**
- Consumes: verified phase reports and golden demo report.
- Produces: public source/Wheel instructions, one demo command, capability truth, limitations, deferred roadmap links.

- [ ] **Step 1: Write RED documentation truth/link tests**

Require exact product positioning; source/Wheel install; Ubuntu/Python 3.12 first official target; desktop-only; one demo command; SystemAudit/MCP ciphertext/durable governance/Evidence/three pages/Lean evolution implemented; remote MCP/open stdio/container/PyPI/GHCR/OpenHands/ROI/full G4/G5 deferred; publication not authorized; no “1.0 ready”, “exactly once”, “secure sandbox”, or unqualified market-unique claim.

- [ ] **Step 2: Update Chinese and English docs from evidence**

README leads with demonstrated differentiators and links to the golden demo verifier. SECURITY documents the single-node/host-admin boundary and disabled MCP paths. CONTRIBUTING points contributors to TDD, migration freeze, truth states and no-mock UI policy.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/test_public_docs_truth.py -q
git diff --check
git add README.md README.en.md SECURITY.md CONTRIBUTING.md docs/launch docs/usage/lean-developer-preview.md tests/test_public_docs_truth.py
git commit -m "docs: publish the Lean Preview truth and usage guide"
```

This is documentation inside the private working branch, not external publication.

### Task 5: Execute the approved D7 repository-hygiene slice

**Files:**
- Modify: `.gitignore`
- Remove from Git tracking: `.idea/`
- Create: `tests/launch/test_repository_hygiene.py`

**Interfaces:**
- Consumes: D7 approval.
- Produces: no tracked personal IDE project files; no unrelated cleanup.

- [ ] **Step 1: Write the hygiene RED test**

```python
def test_personal_ide_project_files_are_not_tracked() -> None:
    tracked = subprocess.check_output(["git", "ls-files", ".idea"], text=True).splitlines()
    assert tracked == []
```

- [ ] **Step 2: Stop tracking and ignore `.idea/`**

Run:

```bash
git rm --cached -r .idea
```

Add `/.idea/` to `.gitignore`. Do not delete the user's local IDE directory from disk and do not clean other tracked files in this commit.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_repository_hygiene.py -q
git add .gitignore tests/launch/test_repository_hygiene.py
git commit -m "chore: stop tracking personal IDE project files"
```

### Task 6: Generate the Lean Candidate report and final local Gate

**Files:**
- Create: `scripts/check_lean_preview_candidate.py`
- Create: `tests/launch/test_lean_preview_candidate_gate.py`
- Create: `docs/cc-fable-v1/reports/lean-developer-preview-candidate.md`
- Create: `docs/cc-fable-v1/evidence/lean-preview-candidate.json`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: all phase reports, verified demo, exact Wheel/sdist, docs/capability matrix, repository hygiene.
- Produces: `automation_status=passed`, visual status, `publication_status=not_authorized`, user final-approval checklist.

- [ ] **Step 1: Write RED candidate-checker fixtures**

Reject any missing/wrong-commit phase report, corrupt artifact, failed/skipped required test, stale screenshot, unverified demo, tracked `.idea`, capability/report mismatch, omitted deferred item, `external_pending` counted as pass, visual status upgraded without user record, or publication status changed.

- [ ] **Step 2: Run all final automated Gates**

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/lint-imports
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py tests/packaging/test_fresh_wheel_demo.py tests/launch/test_lean_preview_fresh_wheel.py -q -s
cd web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright test
cd ..
```

Expected: zero failures. Record exact counts; do not replace commands with summaries.

- [ ] **Step 3: Build sdist/Wheel candidate and compute hashes**

```bash
rm -rf dist/lean-preview-candidate
mkdir -p dist/lean-preview-candidate
cd web && npm ci && npm run build && cd ..
uv build --sdist --out-dir dist/lean-preview-candidate
uv build --wheel dist/lean-preview-candidate/*.tar.gz --out-dir dist/lean-preview-candidate/from-sdist
shasum -a 256 dist/lean-preview-candidate/* dist/lean-preview-candidate/from-sdist/*
```

- [ ] **Step 4: Generate and verify candidate evidence**

```bash
env -u VIRTUAL_ENV .venv/bin/python scripts/check_lean_preview_candidate.py --output docs/cc-fable-v1/evidence/lean-preview-candidate.json --report docs/cc-fable-v1/reports/lean-developer-preview-candidate.md
```

Expected: exit 0; report says `automation_status: passed`, `publication_status: not_authorized`, and either actual `user_approval_pending` or a separately verified user approval record.

- [ ] **Step 5: Append progress and commit**

```bash
git add scripts/check_lean_preview_candidate.py tests/launch/test_lean_preview_candidate_gate.py docs/cc-fable-v1/reports/lean-developer-preview-candidate.md docs/cc-fable-v1/evidence/lean-preview-candidate.json docs/cc-fable-v1/PROGRESS.md
git diff --cached --check
git commit -m "docs: assemble the Lean Developer Preview Candidate"
```

Stop after this commit and present the candidate to the user. Do not push/tag/publish or start a deferred P2/P3/P4 work package automatically.
