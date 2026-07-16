# S3 Task 7 remediation report

## Result

The three P1 delivery findings are remediated on top of `04e863d`. The implementation is
committed separately as `57b0bea` (`fix(notifier): fence durable delivery recovery`). No push,
merge, tag, dependency change, lockfile change, or production migration change was performed.
Provider/channel acceptance remains best-effort; this work does not expand the durable guarantee
beyond the internal notification handler.

## Correctness changes

1. Stable Notifier handoff replay
   - Internal delivery identity now uses the stable event/delivery ID plus immutable event type,
     correlation, edict, and memorial identity.
   - Recomputed availability, deadline, and retry policy no longer conflict with a retained row.
   - Replay returns the original row without rewriting its schedule, deadline, creation time, or
     version, allowing the source consumer acknowledgement to finish after restart.
2. Fresh-clock lease and deadline fencing
   - The worker reads its clock again after every awaited handler completion or failure.
   - Success and failure CAS operations require claimed status, matching owner/version, an
     unexpired lease, and an unexpired deadline at that fresh time.
   - A stale owner cannot commit. An unreclaimed expired claim stays available for a new owner.
     A handler that crosses its deadline is atomically retained in the DLQ with the deadline audit.
   - Failure backoff starts from handler completion rather than claim time.
3. Legacy pending recovery
   - Channel dispatch reports a boolean result for every intended channel, including false,
     exceptions, and missing registrations.
   - A legacy row is deleted only after every intended channel reports success. Failed rows remain
     durable, a successful retry deletes once, and restart retry works after internal outbox binding.
   - Application startup drains existing legacy rows after binding the internal outbox.

## TDD evidence

The regressions failed for the intended reasons before production changes:

- Actual Notifier replay after an acknowledgement-loss restart placed the source event in
  `retry_wait` because a recomputed schedule was treated as an identity conflict.
- A handler finishing after lease expiry incorrectly changed the row to `delivered` instead of
  leaving the stale claim fenced.
- The legacy restart test failed because no drain API existed; the pre-existing flush path also
  deleted rows without checking channel results.
- The broader migration run exposed five Task 7-introduced historical-fixture failures at the v16
  exact-schema check. Reviewer isolation showed those five passed on `ce47cd7` and failed after the
  Task 7 v16/v17 objects were added.

All corresponding regressions are green after the fixes. The historical fixture fix removes all
v16/v17-owned indexes, triggers, and tables from the pre-v8 fixture in foreign-key-safe order before
replay. Production `migrations.py`, `migration_ledger.py`, and the v16 exact-schema check are
unchanged.

## Verification

| Gate | Result |
| --- | --- |
| Notifier focused + correlation + outbox lifecycle | 49 passed |
| Exact five historical fixture regressions | 5 passed |
| Related migration suites | 109 passed |
| Application suite | 170 passed |
| Storage suite | 402 passed |
| Gateway suite, including health | 593 passed |
| Ruff check | passed |
| Ruff format | 827 files already formatted |
| Configured mypy | success in 125 source files |
| import-linter | 455 files / 1571 dependencies; 2 kept, 0 broken |
| `git diff --check` | passed |
| Forbidden paths | no dependency, lockfile, or production migration paths changed |

Pytest emitted only the four existing third-party deprecation warnings from `lark_oapi` and
`websockets`.

## Failure classification correction

The earlier broad Task 7 run had 14 failures. Reviewer comparison against `ce47cd7` established the
correct classification:

- Five were introduced by Task 7 historical-fixture drift and are fixed here: the two historical
  core replay cases, the combined core/session/supervision replay case, and both legacy supervision
  parameterizations.
- Nine are inherited failures that also reproduce on the pre-Task-7 comparison base. They remain
  outside this remediation and are not relabelled as fixed.

Every suite required by the remediation brief is green after correcting the five introduced
fixture failures.
