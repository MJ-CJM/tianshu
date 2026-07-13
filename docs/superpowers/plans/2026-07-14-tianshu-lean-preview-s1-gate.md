# Tianshu Lean Preview S1 G1.5 Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the already-implemented S1.1–S1.5 work with one reproducible full backend Gate, explicit slow packaging black boxes, a G1.5 report, and a clean evidence handoff.

**Architecture:** This phase adds no planned product behavior. It serially validates the existing implementation from commits `48a285d` through `498b1e4`, records the exact Wheel/environment evidence, and stops for a defect-specific TDD amendment if any required command fails.

**Tech Stack:** Python 3.12, pytest, uv build, npm/Vite, exact Wheel/sdist, SQLite, FastAPI.

## Global Constraints

- Start from a descendant of implementation commit `498b1e4` and design commit `5ef4790`.
- Preserve the uncommitted `docs/cc-fable-v1/PROGRESS.md`; only append verified Gate results.
- Run the full non-slow suite and slow packaging suites serially; previous concurrent heavy runs showed build-lock contention.
- Do not “fix forward” from a red Gate without first adding a failing regression test and an exact plan amendment.
- Wheel SHA is evidence for that build only, not a cross-build invariant.
- Linux/Windows, Python 3.13/3.14, containers, registry publication, and OIDC remain `external_pending`.

---

### Task 1: Freeze the Gate entry

**Files:**
- Read: `docs/cc-fable-v1/PROGRESS.md`
- Read: `tests/resources/test_wheel_manifest.py`
- Read: `tests/packaging/test_fresh_wheel_demo.py`
- Read: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: S1 commits `48a285d`, `9a23f9f`, `73b2ca7`, `a215cef`, `3f65e2a`, `498b1e4`.
- Produces: immutable entry facts for the G1.5 report.

- [ ] **Step 1: Verify ancestry and dirty-tree boundary**

Run:

```bash
git merge-base --is-ancestor 498b1e4 HEAD
git merge-base --is-ancestor 5ef4790 HEAD
git status --short
```

Expected: both ancestry commands exit 0; only the pre-existing ` M docs/cc-fable-v1/PROGRESS.md` is present before Gate-generated evidence.

- [ ] **Step 2: Verify the slow suites are still explicitly marked**

Run:

```bash
rg -n "pytestmark = pytest.mark.slow" tests/resources/test_wheel_manifest.py tests/packaging/test_fresh_wheel_demo.py
```

Expected: one marker in each file; neither suite is silently included in `-m "not slow"`.

- [ ] **Step 3: Record entry commit without changing source**

Run:

```bash
git rev-parse HEAD
git diff --check
```

Expected: one full SHA; diff check exits 0.

### Task 2: Run the single non-slow backend Gate

**Files:**
- Test: `tests/`
- Evidence later: `docs/cc-fable-v1/reports/g1.5-report.md`

**Interfaces:**
- Consumes: all backend behavior except tests marked `slow`.
- Produces: one exact pass/fail count and warning summary.

- [ ] **Step 1: Run the complete non-slow suite once**

Run:

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
```

Expected: exit 0, zero failed/error. Record passed/skipped/deselected/warning counts exactly. If red, stop this plan and create a defect-specific RED test; do not continue to Task 3.

- [ ] **Step 2: Run static quality gates**

Run:

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/lint-imports
```

Expected: all commands exit 0. Repository-wide noise outside `src tests` is reported separately and is not silently folded into this source-quality claim.

### Task 3: Run the exact packaging and fresh-HOME Gate

**Files:**
- Test: `tests/resources/test_wheel_manifest.py`
- Test: `tests/packaging/test_fresh_wheel_demo.py`
- Build: `scripts/build_release.sh`

**Interfaces:**
- Consumes: built Web payload, in-tree build backend, Wheel/sdist resources, demo profile.
- Produces: exact Wheel name/SHA, source commit, OS/arch/Python facts, governed demo evidence.

- [ ] **Step 1: Build the current Web payload**

Run:

```bash
cd web
npm ci
npm run build
cd ..
```

Expected: Vite build exits 0 and `src/tianshu/web/static/index.html` exists.

- [ ] **Step 2: Run Wheel manifest and sdist round-trip tests**

Run:

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py -q
```

Expected: exit 0; no skip/deselection. The suite proves direct Wheel and sdist→Wheel manifests and exact brand bytes.

- [ ] **Step 3: Run the repo-external fresh-HOME black box**

Run:

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/packaging/test_fresh_wheel_demo.py -q -s
```

Expected: exit 0; no skip/deselection. Capture the printed JSON from `test_record_exact_wheel_and_environment_evidence` without hand-editing its values.

### Task 4: Write and commit the G1.5 report

**Files:**
- Create: `docs/cc-fable-v1/reports/g1.5-report.md`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: exact outputs from Tasks 1–3.
- Produces: S1 Gate status and S2 entry authorization.

- [ ] **Step 1: Write the report from observed facts**

Use this exact field structure. For every value, paste the observed output from Tasks 1–3; do not leave labels or estimated counts in the committed report:

```markdown
# G1.5 Wheel, Offline Demo, and Doctor Gate Report

status: passed
entry_commit: exact output of `git rev-parse HEAD`
gate_date: UTC RFC 3339 timestamp at Gate completion

## Commands
- `env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q` — exact observed counts
- `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py -q` — exact observed counts
- `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/packaging/test_fresh_wheel_demo.py -q -s` — exact observed counts

## Artifact Evidence
- wheel: exact filename printed by the black box
- sha256: exact 64-character lowercase digest printed by the black box
- os/arch/python: exact values printed by the black box
- brand_sha256: `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`

## Proven
- exact-wheel and sdist-to-wheel packaging
- repo-external fresh HOME and zero external Python networking
- governed demo completion, workspace evidence, Doctor/readiness, clean shutdown

## Not Proven
- Linux/Windows and Python 3.13/3.14
- container runtime, public registry, OIDC, signing, publication
```

- [ ] **Step 2: Append the S1 Gate result to the existing progress ledger**

Append one block; do not rewrite earlier text:

```text
=== S1 / G1.5 Gate ===
S1 Gate: passed (include the three exact command results and counts from the report)
Report: docs/cc-fable-v1/reports/g1.5-report.md
Next: S2 Lean Security
```

- [ ] **Step 3: Validate and commit only evidence files**

Run:

```bash
git diff --check
rg -n "status: passed|external_pending|wheel:|sha256:" docs/cc-fable-v1/reports/g1.5-report.md
git add docs/cc-fable-v1/reports/g1.5-report.md docs/cc-fable-v1/PROGRESS.md
git diff --cached --check
git commit -m "docs: close the G1.5 packaging and offline Gate"
```

Expected: one docs-only commit. S2 must not start unless this report truthfully says `passed`.
