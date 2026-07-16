# S3 Task 4C — managed effects and continuation recovery report

## Outcome

Task 4C was implemented from approved base `1184704`; the production-path remediation was
completed from review base `2dbb2bc` on `feat_cc_fable_v1`. The real managed runner now carries
immutable attempt/root/fencing authority into `ToolRegistry`. Explicitly supported tool effects
persist intent before invocation and receipt before attempt acknowledgement. A side-effect tool
without supported semantics is not invoked: it becomes `uncertain`, creates one generic durable
`Decision`, and suspends the fenced attempt.

The final production-recovery remediation was completed from base `65d6533`. A live reclaimed
origin attempt or repository-proven retry descendant may now reconcile the same stable intent
under a strictly higher fence. The intent's originating attempt/owner/fence remain immutable;
the receipt records the current reconciliation authority separately. Receipt-lookup tools have
an explicit provider lookup adapter, and managed side effects fail closed before their handler
when the registry has no managed executor.

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

Final production-recovery remediation from `65d6533`:

| Commit | Review boundary |
| --- | --- |
| `4d59f02` | immutable origin plus live higher-fence reconciliation authority, provider lookup adapter, and missing-adapter fail-closed registry path |
| `65d317a` | real production C1/C2 restart proofs and foreign/unrelated/stale/mismatched zero-provider rejection matrix |
| `3077c4d` | composition fix and regression for immutable fence 1, same-row reclaim fence 2, then retry-descendant fence 3 |

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

The earlier runner/registry substitute tests remain useful focused checks but are not counted as
the production proof. The final proof uses the actual `ProductionRunRunner`, `Planner`,
`Executor`, native `Agent`, and `ToolRegistry`: the provider applies the effect once, the process
dies before receipt persistence, `Storage` closes and reopens, a fresh runtime claims a retry
descendant, provider lookup returns the receipt without a second effect, and fenced completion
succeeds. The rejection matrix proves foreign root, same-root lineage gap, stale fence, and
mismatched canonical request all conflict with zero provider lookup/invocation and no journal
mutation. Request/receipt metadata remains a narrow allowlist with secret, byte, text, key-count,
item-count, and nesting-depth limits.

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

## Final production-recovery evidence

### RED to GREEN

- C1 RED: after the provider effect and before receipt persistence, a reopened runtime with a
  valid retry descendant failed at `side-effect replay identity conflict` before provider lookup.
  GREEN: both a reclaimed origin attempt and a retry descendant with a strictly higher current
  fence reconcile successfully while the origin columns remain unchanged and the receipt columns
  identify the current authority.
- I1 RED: the real production C1 test could not install a provider receipt lookup on
  `ToolRegistry`; the required API did not exist. GREEN: the actual
  `ProductionRunRunner -> Planner -> Executor -> native Agent -> ToolRegistry` test closes and
  reopens storage, uses a fresh runtime, observes two lookups (initial miss and recovery hit), one
  effective provider call, one receipt, and fenced success. The actual L3 path reaches durable
  suspension through `Executor._execute_outer_loop`, then resolves through
  `ContinuationRecoveryService`, `RunReconciler`, and `RunDispatcher`; both `max_attempts=1` and
  `max_attempts=3` reconstruct and complete under a fresh fence while stale completion is rejected.
- I2 RED: authority plus `side_effect=True`, undeclared semantics, and no managed adapter returned
  `SUCCEEDED` and invoked the handler. GREEN: it returns a failed projection with handler count
  zero. Read-only registry execution remains on its existing path.
- Compatibility RED caught during the combined matrix: exact replay of an already durable
  `UNCERTAIN` intent was rejected after its attempt suspended. GREEN limits live lineage checks to
  provider reconciliation or a different caller, preserving exact-origin terminal evidence replay
  without permitting any provider call.
- Final attack-probe RED: after an origin attempt at fence 1 was reclaimed in place at fence 2 and
  then expired into a contiguous retry descendant at fence 3, recovery failed at
  `side-effect reconciliation root or origin conflict`. The mutable ledger origin fence had been
  compared for equality with the immutable journal origin fence. GREEN bounds the ledger value as
  `journal origin fence <= ledger origin fence <= live caller fence`; current lease, same root,
  strictly higher caller fence, contiguous attempt numbers, and all-prior-failed checks remain
  mandatory. Receipt lookup hits the original provider receipt, effective effect count stays one,
  journal origin authority remains fence 1, and receipt authority records fence 3.

### Final gates

| Gate | Result |
| --- | --- |
| Actual production C1 plus C2 (`max_attempts=1,3`) | `3 passed` |
| Effects, registry, and production-recovery combination | `22 passed` |
| Task 4C/4B2B focused model/storage/governance/application/executor/tool/scheduler matrix | `253 passed` |
| All application/storage/executor/scheduler suites | `753 passed, 1 skipped` |
| All migration-named freeze/ledger/preservation/upgrade suites | `129 passed` |
| Ruff | all checks passed |
| Ruff format check | 796 files already formatted |
| mypy | success, 122 source files |
| import-linter | 2 contracts kept; 447 files / 1515 dependencies |
| `git diff --check` | passed |
| forbidden-file check from `65d6533` | no `uv.lock` or migration history diff |

The repository-wide non-slow observation and its exact six unchanged base failures remain the
separate baseline record above; none is in the final remediation diff. No substitute executor,
direct side-effect service, or direct L3 helper is counted as the final production proof.
