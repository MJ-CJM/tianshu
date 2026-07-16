# S3 Task 4C — managed effects and continuation recovery report

## Outcome

Task 4C was implemented from approved base `1184704`; the production-path remediation was
completed from review base `2dbb2bc` on `feat_cc_fable_v1`. The real managed runner now carries
immutable attempt/root/fencing authority into `ToolRegistry`. Explicitly supported tool effects
persist intent before invocation and receipt before attempt acknowledgement. A side-effect tool
without supported semantics is not invoked: it becomes `uncertain`, creates one generic durable
`Decision`, and suspends the fenced attempt.

The production outer-loop human-Decision entry point is L3-only. At L3 the pending Decision,
waiting `RunState`, suspended attempt, and `decision.requested` outbox row commit in one UoW;
the claimed coroutine then unwinds as `SUSPENDED` instead of polling. L0–L3 coverage is limited
to compatible persisted snapshot reconstruction and the generic recovery consumer. It does not
claim production L0–L2 human-decision suspension. No Python coroutine or live provider/client
object is serialized.

The guarantee is deliberately limited to the managed boundary. This slice does not claim
exactly-once external execution. Opaque CLI/Keqing effects remain unsupported and require a
human Decision.

## Commit map

| Commit | Review boundary |
| --- | --- |
| `f125d68` | strict side-effect models, canonical identities, appended v15 schema, journal repository, model/storage RED→GREEN |
| `faa214a` | intent→invoke/lookup→receipt lifecycle, provider idempotency, unsupported-effect Decision suspension, lifecycle fault matrix |
| `72b00b1` | atomic Decision resume/cancel, suspended-attempt CAS, L0–L3 reconstruction, single consumer wiring, orchestrator RunState-first restart |

Review remediation from `2dbb2bc`:

| Commit | Review boundary |
| --- | --- |
| `c0f8b8b` | pre-invocation exact authority/root replay rejection and bounded metadata contracts |
| `84d9ce4` | production runner→registry managed-effect wiring, stable provider key, receipt ordering, opaque fail-closed suspension |
| `7c198a7` | production L3 atomic Decision/RunState/attempt/outbox suspension, unwind, restart/fence/fault matrix |

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

Production-path remediation added direct `ProductionRunRunner`→`ToolRegistry` coverage. It
proves that `submit_edict` is the explicitly adapted provider-idempotent operation, that the
handler receives the stable journal key, and that a crash after provider return cannot produce
runner success before a receipt exists. Generic side-effect tools under managed authority are
treated as opaque and suspend with zero handler calls. Explicit managed effects outside attempt
authority fail closed. A two-root replay attack conflicts before lookup/invocation, records zero
provider calls, and leaves the original journal row unchanged. Request/receipt metadata is a
narrow allowlist with secret, byte, text, key-count, item-count, and nesting-depth limits.

### Durable continuation recovery

RED collection failed because `tianshu.application.continuation_recovery` did not exist.
GREEN continuation/orchestrator matrix: `43 passed`. It proves:

- compatible synthetic snapshots reconstruct after process close/reopen at L0, L1, L2, and L3;
- the real production L3 entry commits Decision, waiting RunState, attempt suspension, and outbox
  together, then unwinds without a live poll;
- complete recovery of the durable cursor, level, iteration, streak/counters, summarized
  history, steer/advice, usage evidence, and total cost;
- exactly one deterministic `execution.resume.requested` outbox record;
- repeated resolved events do not increment versions or duplicate outbox work;
- suspended→claimable reuses the attempt row without consuming failure retry budget;
- the next claim increments the fencing token and the stale runner cannot complete;
- abort/reject atomically terminalizes RunState, root Memorial, and attempt, and replay never
  resurrects the root;
- injected failure after RunState CAS or before resume outbox rolls the whole UoW back.

The remediation-focused model/storage/governance/effect/continuation/runner/tool/scheduler
matrix completed with `217 passed`. The focused production L3 restart/fault and 4B2B recovery
combination completed with `53 passed`.

## Verification

| Gate | Result |
| --- | --- |
| Focused models/storage/governance/effects/continuation/runner/tools/scheduler | `217 passed` |
| Production L3 + 4B2B continuation + C1 combination | `53 passed` |
| Migration callback/ledger/preservation/v9-v15/secret upgrade paths | `179 passed` |
| Ruff | all checks passed |
| Ruff format check | 799 files already formatted |
| mypy | success, 122 source files |
| import-linter | 2 contracts kept; 447 files / 1515 dependencies |
| `git diff --check` | passed |
| forbidden-file check | no `uv.lock` or migration-file diff in remediation |

### Repository-wide non-slow observation

The remediation run of `pytest -m "not slow" -q` completed in 821.59 seconds with
`3619 passed, 2 skipped, 24 deselected, 7 failed`. One failure was the old direct
`submit_edict` test bypassing the new managed authority; that test was migrated to a real
managed attempt/runtime and passed in isolation. The remaining six are outside the remediation
diff. A subsequent `--lf` run confirms the current residual set exactly:
`6 failed, 43 deselected` in 3.56 seconds. Four were already recorded at review base `2dbb2bc`:

1. `tests/architecture/test_decision_api_boundaries.py` expects one `DecisionService`
   construction path, while base `WorkspaceService` also constructs a fallback instance.
2. `tests/compat/test_executor_capability_gate.py` calls the base `retry_dag` API without its
   required `idempotency_key` (and the fixture has no managed ingress).
3. `tests/integration/test_outbox_recovery.py` uses a fixed 2026-07-15 dispatcher clock that is
   now earlier than the real submission timestamp, so it claims zero events.

4. `tests/test_executor.py` expects the legacy root-Memorial message before the already-present
   managed-ingress fail-closed check.

Two additional unchanged base tests failed in the complete run:

5. `tests/test_gateway_extended.py` expects 404 for an invalid literal Edict ID, while request
   validation returns 422 before lookup.
6. `tests/test_integration_flow.py` wires the legacy event chain without the already-required
   managed run ingress, so execution intentionally fails closed after `plan.completed`.

The failing behavior/assertion lines for these six cases are unchanged by this remediation.

## Remaining limitations

- Provider idempotency and receipt lookup only protect adapters that explicitly implement and
  pass the managed-boundary contract.
- An `uncertain` journal row remains immutable evidence even after its Decision is resolved;
  resolution is represented by the Decision and RunState cursor, not by rewriting history.
- Historical outer-loop records preserve summaries and the final best output, not every full
  actor body. Reconstruction uses only those durable fields and never invents a coroutine.
- Production outer-loop Decision suspension is L3-only; L0–L3 remains a schema/reconstruction
  compatibility guarantee.

No Task 4C correctness concern remains in the focused and migration matrices.
