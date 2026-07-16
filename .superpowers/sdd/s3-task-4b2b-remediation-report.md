# S3 Task 4B2B remediation report

## Status

`DONE`

All I1-I6, M1-M4, and the dispatcher projection-buffer finding are closed with
focused tests. The required runtime regressions, configured mypy gate, Ruff,
formatting, import boundaries, diff hygiene, and forbidden-file checks pass.

Task 4C remains intentionally out of scope: external tool and network effects
are not yet exactly once.

## Finding map

| Finding | Remediation | Focused evidence | Commit |
|---|---|---|---|
| I1 | Added one atomic `ManagedRunIngress`; API, bridge, legacy plan/resume, and DAG retry adapters no longer own root tasks. | `test_managed_run_ingress.py`, `test_production_run_execution.py`, managed follow-up gateway tests | `e33664d` |
| I2 | Plan-review reconciliation accepts exact unprojected and already-projected Decision state and fails closed on invalid identity/binding. | resolution-before-suspension, suspension-before-resolution, duplicate, expiry, approve/reject, wrong/missing identity tests in `test_plan_review_attempt_resume.py` | `a79122d` |
| I3 | Managed scheduler resume restores the durable cursor and starts only `_managed_job_loop` for once/cron/interval. | pause/resume/restart and two-instance cases in `test_managed_scheduler.py` | `e33664d` |
| I4 | Run-now requires a stable caller key and replays the first canonical fire envelope across clock drift without moving the periodic cursor. | `test_scheduled_run_preparer.py`, `test_schedule_edict.py` | `e33664d` |
| I5 | Fenced completion persists final output, strict usage, reasoning, failure reason, summary/result/error, and preserves artifact/timeline references. | terminal evidence, redacted failure, and production projection cases in `test_fenced_run_completion.py` and `test_production_run_execution.py` | `a79122d` |
| I6 | Startup now records each background stop before start and cleans every started/partially-started component in strict reverse order on scheduler, MCP, tracing, telemetry, or outbox failure. | failure injection in `test_outbox_app_lifecycle.py` | `fix: harden managed run lifecycle` |
| M1 | Legacy `plan.completed` is adopted only with a durable canonical Decision/root/Edict/plan binding; otherwise its consumer raises an operator-visible retained conflict. | legacy missing-binding fail-closed tests in `test_production_run_execution.py` and `test_executor.py` | `e33664d`, `fix: harden managed run lifecycle` |
| M2 | Retryable managed failures produce `RETRY` and the next ledger attempt; exhausted retries produce one DLQ plus one fenced failed root/outbox projection, never a retry Memorial/task. | retry classification and retry-budget parameter cases in `test_production_run_execution.py` and `test_fenced_run_completion.py` | `fix: harden managed run lifecycle` |
| M3 | Terminal root projection uses nonterminal CAS; cancellation atomically revokes claimed, claimable, or suspended attempt authority. | stale terminal overwrite, current cancellation, and pre-running cancellation cases in `test_fenced_run_completion.py` | `a79122d`, `fix: harden managed run lifecycle` |
| M4 | Resume and Planner reuse recompute canonical plan hash and verify plan ref plus Decision/root/Edict identity. | canonical tamper and wrong/missing binding tests in `test_plan_review_attempt_resume.py` | `a79122d` |
| Minor | Dispatcher invokes authority cleanup in `finally`, covering success, failure, heartbeat loss, cancellation, and fence loss. | `test_every_dispatch_exit_clears_authority_projection_buffer` | `fix: harden managed run lifecycle` |

## RED to GREEN record

- Ingress/scheduler/run-now slice: 7 expected failures became `79 passed`.
- Plan-review/evidence slice: 7 expected failures became `29 passed` after
  adding the event-order and binding attacks.
- Lifecycle/retry/DLQ/buffer slice: 6 expected failures became `51 passed`.
- A legacy workspace retry test then exposed an obsolete unmanaged `retry_dag`
  expectation by hanging on a lease that the new ingress boundary correctly
  never creates. Full-trace localized it to
  `test_pre_running_cancellation_persists_terminal_and_closes_lease[retry]`.
  The test was migrated to assert early fail-closed behavior and managed
  adoption; the replacement group passed `7 passed`.

## Verification

Required regression coverage was split only to isolate the obsolete hanging
test, then rerun after its contract migration:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/executor -q
177 passed, 1 skipped, 4 warnings in 181.06s

env -u VIRTUAL_ENV .venv/bin/python -m pytest \
  tests/application/test_production_run_execution.py \
  tests/application/test_plan_review_attempt_resume.py \
  tests/application/test_managed_scheduler.py \
  tests/application/test_fenced_run_completion.py \
  tests/application/test_run_dispatcher_lifecycle.py \
  tests/integration/test_claim_lease_recovery.py \
  tests/governance/test_plan_review_decision.py \
  tests/integration/test_outbox_app_lifecycle.py \
  tests/test_scheduler.py tests/gateway -q
702 passed, 4 warnings in 45.62s
```

The brief names `tests/scheduler`; this checkout has no such directory, so the
repository's scheduler suite `tests/test_scheduler.py` was used. Combined
required regression result: `879 passed, 1 skipped, 0 failed`.

Fresh post-fix focused verification:

```text
76 passed, 4 warnings in 14.53s
```

Static gates:

```text
.venv/bin/ruff check <28 changed Python paths>              PASS
.venv/bin/ruff format --check <28 changed Python paths>     PASS
.venv/bin/mypy                                             PASS
  Success: no issues found in 120 source files
.venv/bin/lint-imports                                     PASS
  441 files, 1455 dependencies, 2 contracts kept
git diff --check                                           PASS
git diff --quiet 76b23d3 -- uv.lock src/tianshu/storage/migrations.py
                                                            PASS
```

The additional explicit non-gate broad scan
`python -m mypy src/tianshu` reported `199 errors in 52 files` across all 441
source files. Classification: `175` errors are in untouched files; `24` are in
three changed files (`executor.py`: 16, `planner.py`: 7, `edicts_api.py`: 1),
all on unchanged legacy dependency-typing lines. Task-attributable mapper,
plan-continuation, scheduler optional, and managed-ingress typing errors were
fixed before the configured gate was rerun.

## Remaining concerns

None within the Task 4B2B remediation boundary. Task 4C side-effect
intents/receipts and provider idempotency remain the explicitly frozen next-task
boundary, not a defect in this remediation.

## Second review fixes

### Status

`DONE`

The second-review Critical 1 / Important 7 findings are closed. Production
cancellation, follow-up, legacy adoption, DAG retry, terminal projection,
executor retry classification, failed-startup cleanup, and plan-review
convergence now share the required managed authority boundaries. No migration,
`uv.lock`, or Task 4C effect-receipt change was made.

### Finding and commit map

| Finding | Second-review fix | Focused evidence | Commit |
|---|---|---|---|
| C1 | All production root/DAG/Edict cancellation adapters call the shared fenced cancellation service; attempt revocation, root terminalization, RunState CAS, and outbox write are one existing-connection UoW. | cancellation delegation plus claimed/claimable/suspended, stale-race, and deterministic outbox attacks in `test_fenced_run_completion.py` and gateway/scheduler tests | `9a656d0` |
| I1 | Bootstrap and executor legacy callbacks delegate the complete durable event to `ManagedRunIngress.adopt_legacy`; no adapter creates attempts or defaults retry budgets. | legacy adoption ingress/adapter attacks in `test_managed_run_ingress.py` and `test_production_run_execution.py` | `9a656d0` |
| I2 | DAG retry requires a stable caller key and atomically creates/replays root, DAG reset claim, attempt, and outbox in one UoW. Every injected transaction boundary rolls back to zero writes. | four transaction-boundary faults, exact replay, conflict, delegation, and no-local-write attacks in `test_managed_run_ingress.py`, `test_production_run_execution.py`, and `test_executor_workspace_lifecycle.py` | `9a656d0`, `d87b214` |
| I3 | Exact replay is checked before active-root and parent selection inside ingress; HTTP and channel adapters no longer make those authority decisions. | duplicate-follow-up-after-new-active-root and stable channel-message-ID/busy-owner attacks | `9a656d0`, `d87b214` |
| I4 | Completion, exhaustion/dead-letter, cancellation, and plan-review convergence all require existing RunState and use version/state CAS before terminal root/outbox projection. | missing/stale RunState and all terminal-path attacks in `test_fenced_run_completion.py` and `test_plan_review_attempt_resume.py` | `9a656d0` |
| I5 | The real executor preserves provider timeout, connection, and transient failure classification in `ManagedExecutionProjection`; retry exhaustion alone produces the fenced failed root/outbox. | real `Executor` timeout through `ProductionRunRunner`/`ProductionAttemptCompleter`: first retry nonterminal, exhaustion one failed outbox | `1e6c02f` |
| I6 | The cleanup stack exists before the first background start, registers each successful start immediately, unwinds in strict reverse order, closes Storage last, and registers the tracing provider shutdown returned by `init_tracing`. | bot/watcher reverse cleanup, tracing-before-telemetry-failure, partial outbox, MCP, scheduler/reconciler, normal shutdown order, and bounded-outbox-stop tests | `8ba92f3` |
| I7 | Plan-review convergence reads the root Memorial in the same UoW and requires one Edict identity across Memorial, RunState, and DecisionRequest before any terminal projection. | cross-Edict root Memorial attack in `test_plan_review_attempt_resume.py` | `9a656d0` |

### RED to GREEN evidence

- Core authority attacks started with `12 failed`: legacy adoption, three initial
  DAG retry transaction boundaries, replay ordering, six cancellation/RunState
  terminal cases, and cross-Edict root binding. The fourth `after_outbox`
  transaction fault was added before GREEN. The selected core attacks then
  passed `13 passed`; the full three core application files passed `41 passed`.
- Adapter attacks started with `4 failed` for cancellation delegation, stable
  retry identity, channel follow-up ordering, and legacy callback ownership;
  the same selection passed `4 passed` after the production adapters were
  narrowed to ingress delegation.
- Real executor retry classification and lifecycle cleanup started with
  `3 failed` (I5: 1, I6: 2). The complete production execution file passed
  `13 passed`; the complete app lifecycle file passed `20 passed`.
- The first broad regression found five stale tests after the intentional
  ingress contract replacement: `858 passed, 5 failed, 1 skipped`. All five
  reproduced in isolation. Their tests were migrated without restoring local
  authority: stable channel IDs use real async ingress busy ownership, and
  executor retry tests assert stable key delegation plus zero local writes.
  The isolated set then passed `5 passed`; the three affected files passed
  `55 passed`.

### Formal verification

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest \
  tests/application/test_managed_run_ingress.py \
  tests/application/test_plan_review_attempt_resume.py \
  tests/application/test_fenced_run_completion.py \
  tests/application/test_production_run_execution.py \
  tests/integration/test_outbox_app_lifecycle.py \
  tests/gateway tests/executor tests/test_scheduler.py -q
863 passed, 1 skipped, 4 warnings in 255.48s

.venv/bin/ruff check <20 changed Python paths>             PASS
.venv/bin/ruff format --check <20 changed Python paths>    PASS
.venv/bin/mypy                                            PASS
  Success: no issues found in 120 source files
.venv/bin/lint-imports                                    PASS
  441 files, 1460 dependencies, 2 contracts kept
git diff --check 59488d5..HEAD                             PASS
git diff --quiet 59488d5..HEAD -- \
  uv.lock src/tianshu/storage/migrations.py                PASS
```

The brief's literal `tests/scheduler` path does not exist in this checkout and
returns `file or directory not found`; the repository scheduler suite is
`tests/test_scheduler.py`, which is included in the passing broad command.
The four warnings are pre-existing third-party deprecations from Lark/websockets.

### Remaining concerns

None within the second-review Task 4B2B boundary. Task 4C effect receipts and
provider idempotency remain intentionally out of scope.
