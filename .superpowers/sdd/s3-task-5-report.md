# S3 Task 5 — Plan Revision Evidence Report

## Outcome

Implemented the Lean `PlanRevisionV1` evidence boundary and connected it to durable
planning, approval replay, restart recovery, and DAG activation.

The implementation deliberately stops at the Task 5 boundary:

- no estimator, quality score, self-grading, evaluation, or benchmark framework;
- no ArtifactStore implementation (the revision exposes the raw lowercase SHA-256
  digest that Task 6 can use as its content address);
- no database migration and no dependency or lockfile changes.

## Reviewable commits

1. `dde6055` — `feat: add immutable plan revision lineage`
2. `085f52b` — `feat: persist plan revision evidence`

This report is committed separately from both production changesets.

## Contract and invariants

`PlanRevisionV1` is a strict, frozen, extra-forbid Pydantic model with exactly the
Task 5 evidence fields:

- `revision_id`
- `parent_revision_id`
- `plan_hash`
- `reason_code`
- `reason_summary`
- `artifact_digest`
- `created_at`

Canonical plan bytes use the existing repository-wide canonical JSON boundary:
UTF-8, sorted keys, explicit nulls, `ensure_ascii=False`, compact separators, and
`allow_nan=False`. Both `plan_hash` and `artifact_digest` are the lowercase SHA-256
of those exact bytes. Reason summaries must be nonblank and redacted; the builder
redacts before model construction.

Lineage validation rejects:

- duplicate revision IDs;
- disconnected or self-parented revisions;
- backwards timestamps;
- invalid revisions reconstructed from bypassed model copies.

`AgentContinuationV1` persists the complete immutable revision tuple, the current
revision ID, canonical plan hash, and current redacted plan snapshot. The repository
validates the full lineage before every write. Compare-and-swap updates must preserve
the prior lineage as an exact prefix, cannot truncate it, rewrite an ancestor, or add
more than one revision in a single CAS.

## Runtime integration

- The planner creates the initial revision and appends replans only while the run is
  in `PLANNING` or `PAUSED`.
- An append must name the exact durable head as its parent, preventing lost-update
  forks.
- A restarted planner reconstructs the current `Plan` from the durable snapshot and
  does not invoke planning again.
- Plan-review requests include revision identity and digest. Exact replay reuses the
  durable revision rather than creating conflicting evidence.
- Before any worker dispatch, the DAG scheduler verifies that the projected execution
  plan hashes to the durable lineage head and atomically activates the RunState as
  `EXECUTING`. A mismatch fails before worker-pool side effects.
- Existing fail-closed plan-review corruption handling was retained: a RunState whose
  stricter revision binding cannot decode is terminalized without attempting to trust
  the corrupt row.

## TDD evidence

The work followed red-green-refactor slices:

1. The new integration test initially failed because `plan_revision` did not exist.
2. Contract tests became green after the strict immutable model, canonical hash, and
   redaction builder were added.
3. Persistence/restart tests initially failed on the missing planner API and durable
   reuse path; implementation made the restarted planner avoid a second plan call.
4. Lineage mutation tests drove repository-level zero-write rejection for ancestor
   rewrite, truncation, and multi-append.
5. DAG binding tests first failed on the missing activation boundary; implementation
   added pre-dispatch verification and `EXECUTING` activation.
6. Broader regression tests exposed nondeterministic approval replay and stricter
   corrupt-state decode behavior. Deterministic revision reuse and fail-closed
   terminalization fixed both without weakening lineage validation.
7. The public model import test was changed first and failed with `ImportError`; the
   package export was then added.

## Verification evidence

Focused and related suites:

- `pytest tests/integration/test_replan_evidence.py tests/test_planner.py tests/test_dag.py tests/test_scheduler.py -q`
  — 49 passed.
- `pytest tests/integration/test_replan_evidence.py tests/test_planner.py tests/test_dag.py tests/test_scheduler.py tests/storage/test_run_state_repository.py tests/governance/test_plan_review_decision.py tests/governance/test_decision_service.py tests/application/test_plan_review_attempt_resume.py -q`
  — 174 passed.
- `pytest tests/application tests/storage tests/test_planner.py tests/test_dag.py tests/test_scheduler.py -q`
  — 595 passed.

Static and architectural gates:

- `ruff check .` — passed.
- `ruff format --check .` — 802 files already formatted.
- `mypy` — no issues in 123 source files.
- `lint-imports` — both contracts kept, 0 broken.
- `git diff --check` — passed.

The test runs report four pre-existing third-party deprecation warnings from Lark and
websockets; the same warnings were present in the clean baseline. Final post-commit
verification repeats the requested focused, application/storage, static, diff, and
forbidden-change gates.

## Independent-review remediation

The follow-up review identified three P1 authorization gaps and one P2 retained-event
replay gap. They were corrected in separate reviewable commits:

1. `0423fd8` — `fix: bind DAG nodes before plan activation`
2. `197cd14` — `fix: fail closed on plan replay authority`
3. `b77f0fd` — `style: format plan revision remediation`

### Executed DAG authorization and activation order

One canonical node projection now binds every immutable dispatch input represented by
the plan: stable node ID, description, dependencies, required tools, and assignee.
The scheduler derives the authorized projection from the durable redacted plan snapshot
and compares it with the actual `DAGExecution.nodes`; the independently retained
`plan_json` must still hash to the revision head.

Structural validation is pure and runs before revision binding and before the RunState
CAS to `EXECUTING`. Unknown dependencies, cycles, and node-binding failures therefore
leave the exact prior RunState version, phase, lineage, and snapshot unchanged. A later
valid replan remains possible.

RED evidence:

- independent description, dependency, tool, and assignee mutations all reached
  activation without error;
- unknown dependency and cycle cases changed RunState to `EXECUTING` before raising.

GREEN evidence:

- all four node attacks fail at the projection boundary;
- unknown dependency and cycle attacks preserve the exact RunState and can append a
  valid child revision afterward;
- no worker-pool submission occurs in any rejected case.

### Durable plan-review authority

Before a durable snapshot can be reused, a review-required plan must resolve through
the exact referenced Decision. The planner validates Decision existence and decoding,
kind, root memorial, Edict, canonical plan snapshot/ref/hash, revision ID, artifact
digest, resolved status, and an `approve` outcome. Pending, missing, cross-root,
rejected, or unreferenced authority fails closed. The Decision-free fallback remains
only for Edicts that never require plan-review authority.

RED evidence: pending, missing, rejected, and unreferenced authority cases all continued
planning or returned the durable snapshot. GREEN evidence: those four cases plus an
explicit cross-root mismatch all fail before snapshot reuse; the existing valid approved
restart case remains green.

### Retained `edict.scheduled` replay

The initial durable continuation now binds the source scheduled event ID and canonical
envelope hash. An exact retained delivery validates and reuses the durable plan/revision
without invoking the planner, appending a revision, changing RunState, or re-emitting
`plan.completed`. Reuse of the same event identity with changed envelope content fails
before any state or event side effect. Existing unbound lineage also fails closed instead
of attempting a new `parent=None` revision.

RED evidence: exact replay invoked planning twice and raised a parent-lineage conflict;
conflicting payload replay reached the same late failure. GREEN evidence: exact replay
is a no-op over the durable result, while conflicting content raises the explicit event
identity conflict with unchanged RunState.

### Remediation gates

- Task 5 focused planner/DAG/scheduler suite: 57 passed.
- Approval, Decision, RunState, attempt-resume, and DAG-workspace suite: 144 passed.
- Application/storage/planner/DAG/scheduler regression: 595 passed.
- Post-format affected suite: 43 passed.
- `ruff check .`: passed.
- `ruff format --check .`: 802 files already formatted.
- `mypy`: no issues in 123 source files.
- `lint-imports`: 2 contracts kept, 0 broken.
- Diff and forbidden-scope checks: passed; no migration, dependency, lockfile,
  estimator, scoring, self-grading, benchmark, or quality-metric framework changes.

All test commands retain the same four third-party deprecation warnings documented
above; no new project warning was introduced.
