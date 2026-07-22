# S5 Task 6 — Routing Distribution and Rollback Safety Report

## Scope and authority

- Baseline: `ed8115d`, branch `feat_cc_fable_v1`.
- This increment reuses `PromotionService`, `evolution_promotion_journal`, routing allocation CAS, lifecycle journal, outbox, SystemAudit, and Task 5 immutable assignments as the only authorities.
- `EvolutionRollbackReconciler` only reconstructs and replays the original durable `RollbackCommand` through `PromotionService`; it does not write lifecycle, routing, receipts, audit, or outbox directly.
- The journal now records the original idempotency key. The field is backward compatible for decoding. A legacy pending row without that identity remains allocation-zero and degraded, but is not automatically replayed.

## Measured local routing evidence

- Stable input: 10,000 keys named `distribution-run-00000` through `distribution-run-09999`.
- Allocation: 1,000 basis points (10%), seed `distribution-v1`, fixed local test secret.
- Result: 1,002 challenger assignments (10.02%), inside the required 9%–11% interval.
- Every challenger had `selected_ref != champion_ref`; its persisted overlay artifact digest matched `selected_ref` and differed from the champion digest.
- A restarted router with a rotated secret loaded the first 100 existing assignments byte-for-byte unchanged.

This is measured local routing evidence only. It is not ROI, production traffic statistics, a cost calibration, or a Task 7 Lean Core Gate result.

## Rollback invariant and fault matrix

The first rollback UoW CASes allocation to zero and commits `rollback_pending` with lifecycle journal, promotion journal, outbox, and audit before restore begins. After that commit, the candidate is no longer routable, so every new assignment is champion/legacy. Existing Task 5 assignments remain immutable and may continue their originally selected overlay; they are not rerouted.

| Boundary | Verified outcome |
| --- | --- |
| Failure before allocation-zero commit | Original canary lifecycle/allocation remain; no rollback journal or restore effect |
| Commit succeeds, failure before/at restore | `rollback_pending`, allocation zero, readiness degraded; new traffic is legacy/champion |
| Restore throws/unavailable adapter | Pending state remains; stable failure is contained and champion dispatch continues |
| Real Skill restore succeeds, crash before applied receipt | Live base verification reconstructs the receipt; actual restore is not repeated |
| Applied receipt exists, crash before completed/final UoW | Replay reuses the applied receipt and does not repeat the effect |
| Completed append/outbox/final UoW fails | Atomic UoW rolls back finalization; retry finalizes once from applied receipt |
| Restart in pending/applied/completed states | Durable journal reconstruction resumes pending/applied and completed replay is idempotent |
| Legacy journal missing original command identity | Decode remains compatible; reconciliation fails closed without restore |
| Adapter has neither live verification nor declared idempotency | Automatic replay is refused; pending/degraded state remains |
| Corrupt/missing/cross-bound journal or receipt | Canonical hash, row binding, candidate/version/routing binding, and receipt binding reject it before finalization |
| Multiple pending candidates | Global authority is validated first; deterministic order is used; one failed restore does not starve a healthy later candidate, including with success limit 1 |
| Repeated reconciliation | No duplicate effective restore, completed journal, outbox, or audit |

Skill is the golden real-effect domain: it verifies the exact governed live tree before synthesizing a missing applied receipt. Memory, policy, persona, and code retain the existing truthful `UnavailablePromotionAdapter`; they remain pending/degraded rather than pretending restore completion.

### Review fix: linearizable Skill rollback

The Skill adapter now exposes a rollback guard that holds the same subject promotion lock across the complete authority boundary. The observed order in both controlled concurrency tests is:

1. acquire `.promotion.lock`;
2. restore or verify the governed base and produce the effect receipt;
3. commit the durable `applied` journal entry when it is missing;
4. commit the durable `completed` journal entry and `rolled_back` lifecycle;
5. release the lock;
6. allow the waiting competing writer to acquire the lock, where the durable rollback-authority marker rejects that stale activation.

The second race begins with an existing `applied` receipt and verifies the same ordering from exact-base verification through final commit. In that state the guard never blindly restores: if live content drifted before lock acquisition, finalization fails closed with `rollback_restore_failed`, leaves the candidate `rollback_pending`, writes no completed journal entry, and preserves the observed live tree for diagnosis. A missing `applied` receipt may restore the governed base idempotently. Crash-created stage residue is removed while the same guard is held.

The generic adapter protocol remains limited to activation and rollback. Automatic reconciliation accepts either the explicit linearizable rollback guard or an explicit idempotency declaration; adapters with neither remain pending/degraded.

### Review fix: durable rollback marker writes

Rollback marker creation now loops until every payload byte is written and rejects zero- or negative-progress writes. The expected inode identity comes only from the open temporary descriptor before rename. After fsync, the temporary marker is checked as a regular non-symlink file with that identity and bytes exactly equal to the canonical payload. Rename on the same filesystem preserves the inode, so the final marker must open with that same identity, survive fsync and exact-byte read, and still have the same path identity after the read, all under the promotion lock before any restore effect or `applied` journal write.

Fault injection proves that recoverable short writes complete with the exact marker, while a zero-byte write or truncated final marker fails before the effect boundary. Review reproductions replace the final marker both before verification and inside the former cleanup `Path.unlink` boundary. Because no sequence of path checks can make pathname unlink atomic with identity verification, failure handling never unlinks the canonical final marker. It retains the observed marker and fails closed, including for future activation, leaving allocation zero, lifecycle `rollback_pending`, readiness degraded, and no `applied`/`completed` entry. A retry writes a fresh governed marker and atomically replaces the retained final, then completes rollback; the resulting marker rejects the rolled-back candidate while allowing a distinct later candidate.

Pre-rename failures retain one fixed, candidate-scoped `.rollback-quarantine-*` file instead of unlinking it. The quarantine is not an activation authority and the next attempt reuses the same path, so repeated failures do not create one residue per attempt. Successful rename consumes that quarantine. Automated cleanup of externally mutable residues is deferred until an atomic deletion mechanism can prove the path still names the owned inode.

## Production composition and readiness

- `app.state.evolution_reconciler` is wired beside the one production `PromotionService`.
- The existing `RunReconciler.before_scan` executes Evolution reconciliation before each run scan, including the startup one-shot.
- The control-plane callback runs in a worker thread, is serialized by the Evolution reconciler lock, and cannot overlap within one process.
- Stable per-candidate `PromotionConflict` outcomes are recorded as `last_error_code` and do not make the run reconciler fatal; database and programming failures still propagate.
- Readiness check `evolution.rollback` is `pass` with no pending rollback and `degraded` for pending work or probe exceptions. It is not a required/not-ready failure because allocation is already sealed.
- Shutdown remains owned by the existing `RunReconciler` lifecycle; no additional background task is created.

## Verification

- Task 6 brief set: 32 passed.
- Rollback marker I/O fault injection: 7 passed.
- Task 4 rollback/fail-closed authority set: 45 passed.
- Task 4/5 authority, dispatcher, evidence, readiness/health, and migration regression set: 361 passed.
- Evolution production composition plus readiness/health: 77 passed (3 composition tests plus 74 tests already included in the regression set).
- Total distinct tests exercised: 387 (522 executions including overlap).
- Ruff check: passed; Ruff format check: passed.
- mypy on changed production modules: passed.
- import-linter: 2 contracts kept, 0 broken.
- `git diff --check`: passed.
- `uv.lock`: zero diff.

The test process reports four pre-existing third-party deprecation warnings from `lark_oapi`/`websockets`; there are no Task 6 test failures or new dependency changes.

## Deferred / external pending

- OpenHands integration and executor compatibility suite.
- ROI/FTS benchmarks and production traffic statistics.
- 100+ sample cost calibration and full budget-mode gate.
- Full G4-A/B/C report and Task 7 automated Lean Core Gate/UI work.
