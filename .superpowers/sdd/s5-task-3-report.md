# S5 Task 3 Report: fail-closed gates and governed skill installation

## Outcome

Implemented the Task 3 boundary without adding promotion, canary, rollback, champion,
routing, or allocation mutation.

- `GateEvaluator` re-derives all eight required gates from persisted, closed Evidence.
- Missing, open, corrupt, stale, or candidate-mismatched Evidence blocks evaluation.
- Gate evaluation uses candidate CAS, persists an immutable hash-bound snapshot, moves
  only through `EVALUATING` to `BLOCKED`/`READY`, and appends audit plus outbox in the
  same unit of work.
- Authenticated candidate/gate read and gate-evaluate APIs are exposed. A caller field
  such as `passed=true` is ignored and no promote/canary/rollback route is exposed.
- `SkillInstallService` is the single authenticated proposal/stage authority. It derives
  provenance from `AuthContext`, validates client/source binding, rejects cross-principal
  staging, preserves idempotency, and writes candidate/artifact/audit/outbox atomically.
- API, tool, reviewer, curator, lifecycle, and legacy installer write paths no longer
  mutate live skills. The legacy `SkillInstaller.install()` validates input but returns
  the stable `governed_skill_service_required` refusal before any live materialization.
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

## Verification

- Brief plus extended focused tests: `268 passed, 4 warnings in 5.33s`.
- Exact brief command before the final layer-only refactor: `246 passed, 4 warnings in
  3.10s`; the larger 268-test run was repeated after that refactor.
- Real application lifespan smoke is included in the 268-test run and independently
  passed: services present during startup; clean watcher/scheduler/MCP shutdown and
  closed storage after exit.
- Ruff: all checks passed for the 23 Task 3 touched source/test files.
- Ruff format check: 23 files already formatted.
- Mypy: no issues in the 9 Task 3 core source files.
- Import-linter: 476 files / 1694 dependencies; 2 contracts kept, 0 broken.
- `git diff --check`: clean.

The four pytest warnings are third-party deprecations from `lark_oapi` and `websockets`;
no Task 3 warning was introduced. Per controller direction, no unrelated full-repository
slow regression was run during final verification.

## Self-review

- No Task 4 promotion or traffic-allocation behavior is present.
- No live skill tree is changed by propose, stage, API, agent, reviewer, curator, zip, or
  CLI-facing paths covered by the architecture test.
- Caller identity/provenance and gate decisions are server-derived.
- Audit/outbox failure injection rolls back candidate and artifact records.
- Changes outside the new services are limited to wiring, shared DTO/audit enums,
  evidence timestamp decoding, atomic persistence hooks, stable-refusal entry points,
  and their regression expectations.
