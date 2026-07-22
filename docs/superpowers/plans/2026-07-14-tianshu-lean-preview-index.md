# Tianshu Lean Developer Preview Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the D8-A Lean Agent OS Developer Preview Candidate from the current S1 Gate through durable governance, three real desktop pages, governed evolution, and one evidence-backed golden demo.

**Architecture:** This is a plan suite rather than one oversized plan. S1 closes the already-implemented packaging Gate; S2 establishes audit, encrypted MCP persistence, and fail-closed public defaults; S3 builds the durable governance and Evidence core; S4 consumes those APIs in the approved desktop shell; S5 makes candidate routing and rollback real; Closure composes one reproducible demo and the minimum open-source candidate documentation. Full container distribution, OpenHands, ROI calibration, fourteen-page convergence, and formal supply-chain publication remain in the D8 deferred backlog.

**Tech Stack:** Python 3.12, FastAPI/Starlette, Pydantic v2, SQLite/WAL, asyncio, pytest/pytest-asyncio, Ruff, mypy, import-linter, React 18, TypeScript 5.6, Ant Design 5, TanStack Query, Vitest, Playwright, axe, Vite 6, uv, npm.

## Global Constraints

- Product position is exact: “天枢是一个可治理、可验证、持续成长的自进化 Agent OS。”
- Execution worktree is `/Users/chenjiamin/tiangong/tianshu-worktree/tianshu`, branch `feat_cc_fable_v1`.
- Design baseline commit is `5ef4790772e1acafed09b5f4a68a158c74a51260`; require `git merge-base --is-ancestor 5ef4790 HEAD` before execution.
- Preserve the pre-existing uncommitted `docs/cc-fable-v1/PROGRESS.md`; append to it only when a task explicitly records verified evidence. Never overwrite or discard its current content.
- Current migration tail is v6 at design time. Every phase must compute `N = MIGRATIONS[-1].version` immediately before its first migration and append `N+1`; never copy the stale v3–v7 numbers from older plans.
- Existing migration SQL, callbacks, names, checksums, and callback fingerprints are immutable. New migrations are additive or use an explicitly tested table rebuild in a newly appended migration.
- Every behavior follows RED → verify expected failure → minimal GREEN → focused regression → static checks → review → commit. Never weaken a test to obtain GREEN.
- Use `env -u VIRTUAL_ENV .venv/bin/python -m pytest` for Python tests in this worktree. The verified environment contains `.venv/bin/ruff`, `.venv/bin/mypy`, and `.venv/bin/lint-imports`; use those exact executables. Do not invoke `uv run` in a way that rewrites `uv.lock`.
- Node commands run from `web/` with `npm ci`, `npm run lint`, `npm run typecheck`, `npm test -- --run`, and `npm run build`.
- Backend source soft limit is 800 changed production lines per commit and total diff soft limit is 1,500 lines. Split before implementation when a task exceeds either limit.
- At most one writer may modify `src/tianshu/storage/migrations.py`, `src/tianshu/app.py`, `web/src/App.tsx`, the CLI registry, or a public domain contract at a time.
- SQLite single-node restart semantics are in scope. PostgreSQL, Kubernetes, and multi-replica claims are out of scope.
- Remote MCP and newly created unapproved stdio MCP configurations remain disabled/fail-closed. Full SSRF/DNS pinning and persistent stdio grants are deferred to `docs/cc-fable-v1/06-deferred-work-backlog.md`.
- Preserve `web/public/brand.png` byte-for-byte with SHA-256 `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`.
- Preserve the header quote “成功只有一个——按照自己的方式，去度过人生。”, the right-side “彩蛋 / 通用 / English / 实时 / 通政”, all four groups/fourteen departments, light-mode control, and sidebar collapse control.
- Governance copy uses `裁决 / 自动裁决 / 待裁决 / 等待裁决 / 查看并裁决`; it must not reintroduce `批红 / 朱批 / 司礼监代批`.
- Desktop Web only. No mobile implementation, mobile viewport, or mobile claim.
- Missing external evidence is `external_pending`, not passed. Lean Core Gate is not the complete G4-A/B/C Gate.
- Do not make the repository public, create/push a tag, publish PyPI/GHCR, configure OIDC, or make external marketing claims without a new explicit user authorization.

---

## Plan Suite and Order

| Order | Plan | Independently testable result | Required before next |
|---:|---|---|---|
| 1 | [S1 G1.5 Gate](./2026-07-14-tianshu-lean-preview-s1-gate.md) | Current Wheel/sdist/demo/Doctor implementation has one recorded full Gate | G1.5 report status `passed` |
| 2 | [S2 Lean Security](./2026-07-14-tianshu-lean-preview-s2-lean-security.md) | Tamper-evident audit, encrypted MCP persistence, safe disabled defaults | S2 Lean report has zero Critical/Important findings |
| 3 | [S3 Core Governance](./2026-07-14-tianshu-lean-preview-s3-core-governance.md) | Restart-safe decision/run/effect/evidence core | Core fault matrix and Evidence verifier pass |
| 4 | [S4 Core Web](./2026-07-14-tianshu-lean-preview-s4-core-web.md) | Approved shell plus Control/Edict/Evolution real pages | Automation passes; visual status may remain `user_approval_pending` |
| 5 | [S5 Core Evolution](./2026-07-14-tianshu-lean-preview-s5-core-evolution.md) | Candidate/gate/promotion/assignment/rollback changes real execution | Lean Core Gate passes without claiming full G4 |
| 6 | [Lean Closure](./2026-07-14-tianshu-lean-preview-closure.md) | One offline golden demo, candidate artifact/report, truth docs | Ready for final user approval |

Do not execute plans out of order. The only allowed overlap is S4 shell/state-component work after S3 public read contracts are frozen; real S4 pages still wait for their named S3 APIs.

## File Ownership Map

| Area | Primary files | Plan owner |
|---|---|---|
| Gate evidence | `docs/cc-fable-v1/reports/`, `docs/cc-fable-v1/PROGRESS.md` | S1 and each phase closeout |
| Security audit | `models/system_audit.py`, `storage/system_audit_repo.py`, `gateway/system_audit_api.py` | S2 only, then consumed by S3 |
| MCP ciphertext/defaults | `storage/config_repo.py`, `secrets/vault.py`, `tools/mcp/`, `gateway/mcp_api.py` | S2 only |
| Migration ledger | `storage/migrations.py` | Current phase integration owner only |
| Durable ingress/runtime | `application/`, `governance/`, `models/run_state.py`, storage repos | S3 only |
| Evidence | `evidence/`, `storage/artifact_repo.py`, `gateway/evidence_api.py` | S3, consumed by S4/S5/Closure |
| Desktop routes/shell | `web/src/App.tsx`, layout, theme, state components | S4 only |
| Evolution | `models/evolution_candidate.py`, `evolution/`, `universe/router.py` | S5 only |
| Golden demo/docs | `examples/lean-governed-evolution/`, `scripts/run_lean_preview_demo.py`, launch docs | Closure only |

## Phase Handoff Contract

Every phase report must record:

```text
phase:
entry_commit:
exit_commit:
migration_prefix_sha256:
focused_commands:
full_gate_command:
test_counts:
artifact_hashes:
independent_review:
known_limits:
deferred_or_external_pending:
next_phase_ready:
```

If `next_phase_ready` is false, stop at that phase. Do not create compatibility shims in the next phase to hide a failed prerequisite.

## D8 Spec Coverage

| Approved requirement | Implemented by |
|---|---|
| S1 full non-slow + explicit slow Wheel/manifest/fresh-HOME Gate | S1 Tasks 1–4 |
| Append-only SystemAudit, scoped export, transactional security audit | S2 Tasks 2–3 |
| MCP ciphertext migration and all-family key rotation | S2 Tasks 4–5 |
| Remote MCP and unapproved stdio fail-closed without full S2.4/S2.5 | S2 Task 6 |
| Minimal threat model, capability truth, no container/publication claim | S2 Task 7 |
| Single application ingress, UoW and durable outbox | S3 Task 2 |
| Persistent Decision/RunState, lease/fencing/DLQ, effect journal, resume | S3 Tasks 3–4 |
| Lean plan lineage, ArtifactStore and Evidence Bundle | S3 Tasks 5–6 |
| Internal durable notification, correlation/readiness, fault matrix | S3 Tasks 7–8 |
| Frozen New Chinese shell, exact brand/copy/nav/theme/collapse | S4 Tasks 1–2 |
| First-run governed onboarding | S4 Task 3 |
| Real Control Center, Edict Detail, Evolution Center | S4 Tasks 4–6 |
| Seven states, axe, keyboard, 200% zoom, desktop visual matrix | S4 Task 7 |
| Immutable five-kind candidate and adapters | S5 Tasks 1–2 |
| One skill supply chain, fail-closed gate, sole promotion authority | S5 Tasks 3–4 |
| Real persistent challenger overlay, restart-stable assignment, rollback | S5 Tasks 5–6 |
| Lean Core Gate without full G4 claim | S5 Task 7 |
| One exact-Wheel golden demo and verifier | Closure Tasks 1–3 |
| Public truth docs, D7 hygiene, candidate report, no external action | Closure Tasks 4–6 |

## Cross-Phase Type Chain

| Producer | Public contract consumed later |
|---|---|
| S2 | `SystemAuditEventV1`, `AppendSystemAuditRequest`, `SystemAuditVerificationV1` |
| S3 ingress | `SubmitEdictCommand`, `SubmitEdictResult`, `EdictApplicationService`, `OutboxDispatcher` |
| S3 governance | `DecisionRequestV1`, `DecisionResolutionV1`, `DecisionRecordV1`, `RunStateV1` |
| S3 effects/evidence | `AttemptLeaseV1`, `SideEffectIntentV1`, `SideEffectReceiptV1`, `ArtifactRefV1`, `EvidenceBundleV1` |
| S4 read models | `ControlCenterSnapshotV1`, `EvolutionCenterSnapshotV1`, Web `ApiProblem`, Web `PageDataStatus` |
| S5 | `EvolutionCandidateV1`, `EvolutionGateReportV1`, `PromotionReceiptV1`, `RunAssignmentV1`, `EffectiveEvolutionOverlayV1`, `RollbackReceiptV1` |
| Closure | `LeanPreviewDemoReportV1`, `LeanPreviewCandidateReportV1` |

Later plans must import these names or add one thin compatibility facade. They must not copy the models into a second module with divergent fields.

## Execution Checkpoint Policy

- One intentional commit per task in the phase plan.
- Do not stage unrelated files or the whole repository. Stage only paths named by the task.
- If a Gate reveals an unplanned product defect, first add a reproducing test and amend the current phase plan with exact files/commands. Do not improvise a cross-phase refactor.
- After each commit, append verified facts to `docs/cc-fable-v1/PROGRESS.md` without rewriting historical entries.
- At phase exit, require `git diff --check`, an explicit `git status --short`, and no unknown dirty files.
