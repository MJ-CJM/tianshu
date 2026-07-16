# S3 Task 4C — managed effects and continuation recovery report

## Outcome

Task 4C is implemented from approved base `1184704` on `feat_cc_fable_v1`.
Managed/tested effects now persist strict intent evidence before invocation and receipts before
attempt acknowledgement. Unsupported opaque effects are never invoked blindly: they become
`uncertain`, create one generic durable `Decision`, and suspend the fenced attempt. Resolved
outer-loop and managed-effect Decisions converge through one replay-idempotent consumer. A
fresh execution reconstructs L0–L3 outer-loop state from `RunState` and the resolved Decision;
no Python coroutine or live provider/client object is serialized.

The guarantee is deliberately limited to the managed boundary. This slice does not claim
exactly-once external execution. Opaque CLI/Keqing effects remain unsupported and require a
human Decision.

## Commit map

| Commit | Review boundary |
| --- | --- |
| `f125d68` | strict side-effect models, canonical identities, appended v15 schema, journal repository, model/storage RED→GREEN |
| `faa214a` | intent→invoke/lookup→receipt lifecycle, provider idempotency, unsupported-effect Decision suspension, lifecycle fault matrix |
| `72b00b1` | atomic Decision resume/cancel, suspended-attempt CAS, L0–L3 reconstruction, single consumer wiring, orchestrator RunState-first restart |

No commit was pushed, merged, or tagged.

## Migration tail proof

- Approved base live tail: v14, `0014_execution_attempt_ledger`.
- This slice appends exactly one migration: v15, `0015_side_effect_journal`.
- No v1–v14 callback, ordering, or checksum was edited.
- v15 callback freeze fingerprint:
  `db69496a047a2af8a248b04db84052aa38f48cd9e407bb430384ded15fd52b1b`.
- Final migration-upgrade matrix: `146 passed`.
- `uv.lock` is unchanged.

## RED → GREEN evidence

### Contract and journal

RED collection failed because `tianshu.models.side_effect` did not exist. GREEN covers strict
schema/version rejection, secret rejection, canonical hash stability, concurrent duplicate
begin, mismatched replay conflicts, immutable identity, fenced receipt writes, lookup, and
uncertainty binding. The migration-preservation fixtures were extended for the appended live
tail only; historical callbacks were not modified.

### Managed effect lifecycle

RED collection failed because `tianshu.executor.side_effects` did not exist. GREEN focused
matrix: `61 passed`. It proves:

- crash after intent and before provider leaves a replayable intent;
- crash after effect and before receipt reconciles through receipt lookup;
- provider-idempotent retries reuse the stable intent identity and produce one effective result;
- receipt commits before the enclosing attempt acknowledgement;
- opaque effects create one `uncertain` Decision, one suspension, and zero invocations;
- Decision/outbox injection rolls back Decision, RunState, journal uncertainty, outbox, and
  attempt suspension in the caller-owned UoW;
- approve advances the side-effect cursor and resumes the same attempt row; reject cancels it;
- exact resolution replay is a no-op.

### Durable continuation recovery

RED collection failed because `tianshu.application.continuation_recovery` did not exist.
GREEN continuation/orchestrator matrix: `43 passed`. It proves:

- process close/reopen, resolve, resume, and reconstruction at L0, L1, L2, and L3;
- complete recovery of the durable cursor, level, iteration, streak/counters, summarized
  history, steer/advice, usage evidence, and total cost;
- exactly one deterministic `execution.resume.requested` outbox record;
- repeated resolved events do not increment versions or duplicate outbox work;
- suspended→claimable reuses the attempt row without consuming failure retry budget;
- the next claim increments the fencing token and the stale runner cannot complete;
- abort/reject atomically terminalizes RunState, root Memorial, and attempt, and replay never
  resurrects the root;
- injected failure after RunState CAS or before resume outbox rolls the whole UoW back.

Final combined Task 4C governance/effect/continuation command: `107 passed`.

## Verification

| Gate | Result |
| --- | --- |
| Focused models/storage/governance/effects/continuation/orchestrator | `107 passed` |
| Migration callback/ledger/preservation/v14/v15/secret upgrade paths | `146 passed` |
| Ruff | all checks passed |
| Ruff format check | 793 files already formatted |
| mypy | success, 122 source files |
| import-linter | 2 contracts kept; 446 files / 1493 dependencies |
| `git diff --check` | passed |
| forbidden-file check | no `uv.lock` diff; only v15 appended to migrations |

### Repository-wide non-slow observation

`pytest -m "not slow" -q` was safely interrupted after 640 seconds when it continued to make
slow dot-by-dot progress rather than deadlocking: `1690 passed, 2 skipped, 24 deselected` at
the interruption point. The Task 4C tests had passed. Three failures reproduce in isolation
and are byte-for-byte present at approved base `1184704`, so this slice did not broaden into
unrelated fixes:

1. `tests/architecture/test_decision_api_boundaries.py` expects one `DecisionService`
   construction path, while base `WorkspaceService` also constructs a fallback instance.
2. `tests/compat/test_executor_capability_gate.py` calls the base `retry_dag` API without its
   required `idempotency_key` (and the fixture has no managed ingress).
3. `tests/integration/test_outbox_recovery.py` uses a fixed 2026-07-15 dispatcher clock that is
   now earlier than the real submission timestamp, so it claims zero events.

An additional workspace Git failure printed during Ctrl-C was an interruption artifact; its
isolated rerun passed. A smaller combined run also confirmed a base `tests/test_executor.py`
message expectation predating the managed-ingress fail-closed check. These are repository
gate drift, not Task 4C regressions.

## Remaining limitations

- Provider idempotency and receipt lookup only protect adapters that explicitly implement and
  pass the managed-boundary contract.
- An `uncertain` journal row remains immutable evidence even after its Decision is resolved;
  resolution is represented by the Decision and RunState cursor, not by rewriting history.
- Historical outer-loop records preserve summaries and the final best output, not every full
  actor body. Reconstruction uses only those durable fields and never invents a coroutine.

No Task 4C correctness concern remains in the focused and migration matrices.
