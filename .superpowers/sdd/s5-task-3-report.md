# S5 Task 3 Report: fail-closed gates and governed skill installation

## Outcome

Implemented the Task 3 boundary without adding promotion, canary, rollback, champion,
routing, or allocation mutation.

- `GateEvaluator` re-derives all eight required gates from persisted, closed Evidence.
- Missing, open, corrupt, stale, artifact-invalid, or candidate-mismatched Evidence
  blocks evaluation. Freshness is measured from the pre-evaluation candidate timestamp,
  so Evidence closed after staging and before evaluation remains valid.
- Gate evaluation uses candidate CAS, persists an immutable hash-bound snapshot, moves
  only through `EVALUATING` to `BLOCKED`/`READY`, and appends audit plus outbox in the
  same unit of work.
- Authenticated candidate/gate read and gate-evaluate APIs are exposed. A caller field
  such as `passed=true` is ignored and no promote/canary/rollback route is exposed.
- `SkillInstallService` is the single authenticated proposal/stage authority. It derives
  provenance from `AuthContext`, validates client/source binding, rejects cross-principal
  staging, preserves idempotency, and writes candidate/artifact/audit/outbox atomically.
- API, tool, reviewer, curator, lifecycle, and legacy installer write paths no longer
  mutate live skills. The legacy `SkillInstaller.install()` immediately returns the
  stable `governed_skill_service_required` refusal without reading the source, creating
  the target, or staging inside the live tree.
- Skill creation uses an explicit absent base package and rejects any same-name loader
  hit before persistence. Updates snapshot the loader-selected raw `SKILL.md` plus all
  ordinary files and directories under `scripts`, `references`, `assets`, and
  `templates`; the candidate preserves those resources while replacing `SKILL.md`.
  Symlinks, non-ordinary files, illegal paths, unsafe loader identities, concurrent
  changes, and size/member limits fail closed before proposal persistence.
- Proposal contracts were placed in `models` and the skill service depends on a port
  protocol, preserving the repository's import-layer contract.
- The composition root wires candidate, gate, and skill-install services during the real
  application lifespan.

## TDD evidence

The implementation was developed through observed RED/GREEN cycles:

1. Gate model imports initially failed because `tianshu.evolution.gates` did not exist.
2. Gate durability tests then failed on missing evaluator behavior; CAS, snapshots,
   evidence blockers, audit/outbox rollback, and current-report integrity were added
   incrementally until green.
3. Skill security tests initially failed because `tianshu.skills.install_service` did
   not exist, then failed on direct write architecture and missing API staging routes.
4. The legacy installer architecture test failed with a detected `os.replace` call and
   its behavior test showed a live installation. The public entry was changed to stable
   refusal, after which the full legacy installer security suite passed.
5. The real `create_app` lifespan smoke first exposed assertion assumptions about the
   SQLite row factory and shutdown representation; the test was corrected to verify the
   actual contract: live query during lifespan and `storage._conn is None` after exit.
6. Import-linter then detected a new indirect `skills -> executor` dependency. Proposal
   DTOs were moved to `models` and a candidate authority port was introduced; both layer
   contracts now pass.
7. Remediation tests showed that Gate evaluation trusted artifact metadata without
   verifying bytes and compared freshness against the new `EVALUATING` timestamp. An
   injected artifact verification port plus a pre-transition `evidence_not_before`
   timestamp fixed metadata loss, byte tampering, stale Evidence, and the real
   stage-T0/close-T1/evaluate-T2 ordering.
8. Remediation tests showed that the retired installer still created its target and
   staged beneath it. Direct stable refusal made valid, invalid, and malicious sources
   leave both missing and pre-existing live trees byte-for-byte unchanged.
9. The API create-stage test exposed identical base/candidate digests. An explicit
   absent base state was added and the test now proves distinct digests, truthful
   rollback binding, and no live write.
10. The update API remediation test exposed that loader `content` excludes frontmatter,
    causing the current base to fail validation and omitting all resource members. The
    skills-layer package snapshot now safely rereads the loader's authoritative path,
    and the real API test proves the exact complete base digest and unchanged live tree.
11. The duplicate-create remediation test exposed that a same-name live skill entered
    proposal validation. The API now returns stable `skill_already_exists` before the
    service call; artifact, candidate, lifecycle, audit, and outbox counts remain
    unchanged. Snapshot errors similarly return stable
    `skill_package_snapshot_invalid` with no filesystem path disclosure or persistence.

## Verification

- Remediation focused tests: `339 passed, 4 warnings in 10.61s`.
- Exact brief command before the final layer-only refactor: `246 passed, 4 warnings in
  3.10s`; the larger 268-test run was repeated after that refactor.
- Real application lifespan smoke is included in the focused run and independently
  passed: services present during startup; clean watcher/scheduler/MCP shutdown and
  closed storage after exit.
- Ruff: all checks passed for the 10 remediation-touched source/test files.
- Ruff format check: 10 files already formatted.
- Mypy: no issues in the 6 remediation-touched source files.
- Import-linter: 476 files / 1694 dependencies; 2 contracts kept, 0 broken.
- `git diff --check`: clean.
- Authoritative-package remediation: `326 passed, 4 warnings in 9.03s` across the
  skills, installer, skill gate, candidate adapter, and real API suites; the final
  API/snapshot check passed `9 passed, 4 warnings in 4.12s`.
- Remediation static checks: Ruff passed; mypy reported no issues in the two modified
  source files; import-linter analyzed 476 files / 1696 dependencies with both
  contracts kept.
- `uv.lock` is unchanged from `HEAD`; validation uses the existing virtual environment
  directly so verification does not rewrite dependency metadata.

The four pytest warnings are third-party deprecations from `lark_oapi` and `websockets`;
no Task 3 warning was introduced. Per controller direction, no unrelated full-repository
slow regression was run during final verification.

## Self-review

- No Task 4 promotion or traffic-allocation behavior is present.
- No live skill tree is changed by propose, stage, API, agent, reviewer, curator, zip, or
  CLI-facing paths covered by the architecture test.
- Closed Evidence artifacts must match both immutable database metadata and actual
  content-addressed bytes; a missing verifier fails closed, and a prior green snapshot
  is rejected if its artifact bytes later change.
- Caller identity/provenance and gate decisions are server-derived.
- Audit/outbox failure injection rolls back candidate and artifact records.
- Changes outside the new services are limited to wiring, shared DTO/audit enums,
  evidence timestamp decoding, atomic persistence hooks, stable-refusal entry points,
  and their regression expectations.
