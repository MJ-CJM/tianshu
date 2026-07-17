# S5 Task 4 Report: centralized governed promotion and rollback

## Outcome

Implemented `PromotionService` as the only write authority for canary allocation,
promotion, and rollback.

- `start_canary`, `promote`, and `rollback` require strict commands with a reason,
  expected candidate version, and idempotency key.
- Every mutation requires an authenticated API/admin scope and is authorized against
  `candidate.provenance.actor_principal_id`. Only the candidate owner or an admin may
  mutate it. Authorization occurs before idempotent receipt lookup, so another
  principal cannot recover a receipt by replaying the same key.
- Canary start binds exactly one current green Gate report to the candidate digest,
  pre-transition version, Gate snapshot version/hash, routing allocation, and journal
  receipt in one transaction.
- Promotion revalidates the original immutable green report and its currently closed
  Evidence/artifacts; it does not mint, copy, or replace Gate evidence. Code promotion
  additionally requires a current resolved high-risk Decision bound to the exact
  candidate/version/digest/action.
- Promotion and rollback use durable `intended`/`rollback_pending`, `applied`, and
  `completed` journal states. A durable applied receipt prevents the external effect
  from running again if the final audit/outbox transaction fails.
- Rollback CASes the allocation to zero and moves the candidate to
  `ROLLBACK_PENDING` before invoking the restore adapter. A failed restore therefore
  remains traffic-safe and retryable.
- Candidate, routing, journal, system audit, and outbox changes share SQLite units of
  work. Failure injection proves rollback of the whole transaction.
- The real Skill adapter reads canonical content-addressed packages, validates the
  package/subject binding, serializes filesystem replacement with a lock, atomically
  replaces the live tree, and treats an already exact tree as an idempotent no-op.
  It also restores an exact base package, including the explicit absent-base case.
- `create_app` wires the real Gate evaluator, Skill adapter, and promotion service. A
  golden lifespan test proposes and stages through `SkillInstallService`, constructs
  and closes an eight-gate Evidence bundle, runs the actual `GateEvaluator`, promotes
  the exact skill package, and rolls it back to absent.
- Legacy universe switch, rollback, code-promotion API, manager, Evolver auto-promote,
  and CLI-reachable mutation paths no longer write champion/live state. They either
  recommend promotion or return a stable governed-promotion refusal.

## Adapter availability

Only `CandidateKind.SKILL` has a production live-effect adapter in Task 4.
`MEMORY`, `POLICY`, `PERSONA`, and `CODE` resolve to an explicit unavailable adapter
and fail closed before any live effect. Code's Decision validation is implemented and
tested with a valid resolved Decision, but code deployment itself remains unavailable.

## TDD evidence

The implementation was developed through observed RED/GREEN cycles:

1. The first authority test run failed during collection because
   `tianshu.evolution.promotion` did not exist.
2. After introducing strict contracts, the focused suite had one passing schema test
   and nine behavior failures. Canary CAS, immutable Gate binding, journal receipts,
   routing, rollback ordering, audit/outbox, and stable failures were added
   incrementally.
3. The real Skill adapter test first failed on the missing adapter import, then on an
   incorrect subject/package binding. The canonical package validation and exact-tree
   apply/restore path made it green.
4. Crash-window tests injected final outbox failures after an applied receipt. The
   first attempts exposed repeated adapter effects; the durable `applied` journal state
   made retries complete without rerunning activate/restore.
5. The first application golden path used a test-created Gate snapshot and was
   rejected during self-review. It was replaced with the real
   `SkillInstallService -> EvidenceService -> GateEvaluator -> PromotionService` path.
6. The BOLA tests were added after review. RED showed that a different API principal
   could mutate another principal's candidate. GREEN moved candidate owner/admin
   authorization ahead of all mutation and receipt lookup. The final test asserts no
   change to candidate, routing, journal, audit, outbox, or adapter call counts for all
   three operations, including same-key receipt replay; admin positive paths pass.

## Verification

- Exact Task 4 command plus the new authority coverage: `42 passed, 4 warnings in
  4.68s`.
- Adjacent Gate/candidate/adapter/skill-install/gateway/universe regression suite:
  `199 passed, 4 warnings in 13.25s`.
- BOLA RED: `2 failed`; BOLA GREEN: `2 passed, 11 deselected, 4 warnings in 2.77s`.
- Ruff: all checks passed for all 14 changed source/test files.
- Ruff format: all 14 changed source/test files already formatted.
- Mypy: `Success: no issues found in 130 source files`.
- Import-linter: 477 files / 1711 dependencies; 2 contracts kept, 0 broken.
- `git diff --check`: clean.

The four pytest warnings are existing third-party deprecations from `lark_oapi` and
`websockets`; Task 4 introduced no new warning.

## Self-review and known limits

- The mutation authority is centralized and the architecture test rejects direct
  routing/champion writes outside `PromotionService` and migrations.
- Authorization is candidate ownership, not Decision review identity. A reviewer who
  approved a Decision does not thereby become an authorized mutation caller.
- Gate reports remain immutable and candidate-bound. Promotion only revalidates the
  report persisted by Task 3 and its current Evidence/artifact bytes.
- External effects are idempotent and journal-recoverable, but Task 4 does not add a
  background startup reconciler. A caller must retry the same authenticated command
  after a crash between durable `applied` and `completed`.
- Task 4 persists governed allocation policy; request-time challenger routing remains
  outside this task.
- No dependency or lockfile was changed, and no unrelated cleanup was performed.

## Review remediation

The Changes-required review identified two Important and two Minor gaps. All four were
reproduced before production changes and remediated without adding Task 5 behavior.

### Durable CODE Decision preflight

The root cause was ordering: the repository validated the Decision only during the
final `CANARY -> PROMOTED` save, after the intended journal and adapter effect. The
repository now exposes its existing exact connection-level validation as
`require_code_promotion_decision()`. `PromotionService` invokes it in the first unit of
work after candidate/Gate/routing validation and before an intended journal row or any
adapter call. The final repository save still runs the same validation a second time.

The preflight requires a real Decision row that is resolved with `approve`, is a
`GOVERNED_APPLY`, and is bound to the exact current candidate ID, version, candidate
digest, Gate snapshot version, `promote` action, and `high` risk. Nonexistent, pending,
rejected, wrong-version, wrong-digest, and wrong-snapshot Decisions all return the
stable `promotion_decision_required`; the candidate remains `CANARY`, activate is not
called, and no promote intended/applied journal exists.

### Crash-atomic Skill replacement

The root cause was the filesystem state sequence: after `target -> backup`, a failed
`stage -> target` fell through a `finally` that deleted the only staged new tree and
never restored the old target.

Replacement now uses one deterministic backup per skill and one deterministic stage
per skill/candidate digest under the existing process lock. Before every apply,
including after constructing a new adapter after a simulated restart, reconciliation
validates every canonical target/backup/stage against the exact artifact-backed old or
new package. It then performs only a deterministic finish, restore, or cleanup:

- a failed second rename immediately attempts `backup -> target`;
- if compensation also fails, the exact backup and exact stage are both retained and
  the target stays fail-closed until the next locked reconciliation;
- cleanup failure can leave a deterministic remnant, but the canonical target remains
  an exact old or new tree;
- retry/reopen clears the remnant without rewriting an already exact target, preserving
  the idempotent inode contract;
- unknown, corrupt, mismatched, or symlink recovery state is never installed or
  silently deleted.

### Journal and receipt binding

Promotion journal entries now carry command key, candidate ID, Decision ID, candidate
digest, and base digest. Reads select the durable journal ID, command key, candidate
ID/version, Gate snapshot, action, status, Decision ID, JSON, and hash together. Decode
requires canonical JSON and exact equality between every relational column and entry,
including deterministic journal ID and valid action/status/receipt combinations.

Command replay also binds the entry to the current candidate, exact request hash,
expected pre-transition version, action, Decision, and both digests. Applied receipts
must match the action-specific candidate/effect digest. Completed receipts additionally
must match deterministic journal ID, candidate, action, idempotency key, expected final
version/lifecycle, Gate/routing values, and effect digest. Valid-hash receipt tampering
and relational cross-binding now fail with the stable `promotion_journal_conflict`.

### Lifecycle authority architecture gate

The architecture suite now uses AST analysis rather than string presence. It follows
local aliases for governed lifecycle values and update dictionaries, detects direct or
aliased `model_copy(update=...)`, constructor lifecycle writes, and direct attribute
writes, and scans raw candidate lifecycle SQL persistence. A synthetic aliased
`model_copy + save_candidate` bypass proves the scanner catches the pattern. Production
write sites are limited to the three `PromotionService` methods; the only raw durable
primitive remains `EvolutionRepository` (migrations are excluded from application
authority).

### Remediation RED/GREEN evidence

- CODE Decision RED:
  `pytest ... -k durably_preflighted` -> `6 failed, 13 deselected`; invalid Decisions
  either reached activate or produced a foreign-key journal conflict.
- CODE Decision GREEN: `pytest ... -k code_` -> `8 passed, 20 deselected`.
- Skill fault RED:
  `pytest ... -k 'skill_second_rename or skill_failed_restore or skill_cleanup_failure'`
  -> `2 failed, 1 passed, 19 deselected`; the old target disappeared and the recovery
  stage was deleted.
- Skill fault GREEN: real adapter, rename failure, compensation failure, cleanup
  failure, reopen/retry, absent restore idempotency, and golden app tests -> `6 passed,
  23 deselected`.
- Journal RED: receipt tamper and row cross-binding command ->
  `6 failed, 22 deselected`; all corruptions were accepted.
- Journal/service GREEN: the final promotion service suite passed `29 passed, 4
  warnings`.
- Architecture alias RED: `1 failed, 5 deselected`; the old direct-only scanner returned
  no site for an aliased bypass.
- Architecture GREEN: `7 passed, 4 warnings`.
- Final promotion plus architecture rerun after formatting: `36 passed, 4 warnings in
  4.84s`.

### Remediation verification

- Required promotion/architecture plus affected Gate and universe suites:
  `106 passed, 4 warnings in 13.03s`.
- Adjacent candidate adapter, skill-install, evolution gateway, and Evolver suites:
  `153 passed, 4 warnings in 6.06s`.
- Ruff: all checks passed for the four remediation files.
- Ruff format: all four remediation files already formatted.
- Mypy: `Success: no issues found in 130 source files`.
- Import-linter: 477 files / 1711 dependencies; 2 contracts kept, 0 broken.
- `git diff --check`: clean.
- `uv.lock`: zero diff from the pre-remediation Task 4 commit.
