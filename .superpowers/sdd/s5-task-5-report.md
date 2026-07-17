# S5 Task 5 — Durable Challenger Assignment Report

## Scope and result

Task 5 now persists one immutable `RunAssignmentV1` per Memorial before dispatch,
binds the stored effective overlay into worker execution, resolves the public Skill path
through a task-local overlay, and adds owner-scoped assignment and Evidence attribution
reads. No migration was added: the v18 `run_evolution_assignments` and
`evolution_routing_allocations` tables already contain the required immutable identity,
routing version, bucket, refs, overlay digest, assignment JSON/hash, and update/delete
guards. `uv.lock` was not modified.

## Assumptions and boundaries

- One CANARY candidate is the Lean-preview routing authority. Ambiguous CANARY rows or
  routing-row/candidate-envelope drift fail closed.
- Existing assignment wins before current routing configuration is read. Retry, restart,
  allocation-secret rotation, and routing rotation therefore cannot rebucket a Memorial.
- `memorial.universe_id` remains only the legacy persona/code Universe projection. The
  compatibility facade must read an already persisted assignment and never creates one.
- The five distinct candidate adapters share one strict `resolve_effective_payload()`
  boundary. Skill is the Task 5 public live behavior sentinel; memory, policy, persona,
  and code retain their existing truthful live-activation limits.
- Task 6 statistical distribution and rollback fault-matrix proof is intentionally not
  implemented here.

## TDD evidence

The initial focused RED run failed during collection because
`tianshu.models.run_assignment` did not exist. Production code was then added in small
steps and the focused test was repeatedly rerun.

The final focused suite covers:

- the exact HMAC-SHA256 first-eight-byte bucket algorithm;
- bucket 999/1000 and allocation 0/10,000 boundaries;
- strict frozen assignment/overlay contracts;
- byte-stable retry/restart behavior and one-row concurrency;
- active candidate and routing-version attribution for both routing arms;
- real task-local Skill loader behavior with a challenger-only sentinel;
- the shared five-adapter resolution boundary, including Code and Skill;
- assignment/outbox/Memorial/Edict atomic rollback;
- fail-closed missing or invalid selected overlay before submission side effects;
- owner-scoped, disclosure-safe assignment API reads;
- read-only Evidence attribution: legacy runs remain assignment-free, while assigned
  runs include canonical `application/vnd.tianshu.evolution.assignment.v1+json`;
- compatibility facade failure when router/assignment authority is absent, proving it
  cannot silently return champion or lazy-write an assignment.

## Transaction and runtime ordering

Authoritative submission ordering is:

1. create Edict and Memorial in the caller Unit of Work;
2. read existing assignment or validate routing and the selected immutable artifact;
3. insert assignment and overlay digest;
4. append dispatch outbox / create attempt state;
5. commit once.

The same router instance is wired into HTTP Edict submission, managed run ingress,
RunDispatcher, and Universe compatibility reads. RunDispatcher only reads the durable
assignment, re-verifies a governed selected artifact for every attempt, and binds a
ContextVar-scoped runtime view around the runner task. It never mutates process-global
Skill caches or live resources.

Evidence close calls only `EvolutionRepository.get_assignment()`. A missing assignment
is legacy-compatible and causes no routing, rebucketing, or assignment write.

## Verification

Required Task 5 suite:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest \
  tests/universe/test_challenger_routing.py \
  tests/universe/test_routing.py \
  tests/integration/test_continuation_recovery.py \
  tests/evidence -q

148 passed, 4 warnings
```

Affected application, ingress, dispatcher, lease recovery, evolution adapter/schema/
promotion, gateway, Skill loader, and migration adjacency suite:

```text
434 passed, 1 deselected, 4 warnings
```

The one deselected test was exactly:

```text
tests/gateway/test_evolution_gate_api.py::
test_task_three_api_exposes_no_promote_canary_or_rollback_route
```

The adjacency command used `-k
'not test_task_three_api_exposes_no_promote_canary_or_rollback_route'`. This is an old
Task 3 assertion that no canary/promote/rollback routes exist, while the `c059151`
baseline already contains the Task 4 `/canary`, `/promote`, and `/rollback` routes.
Task 5 only adds `/runs/{memorial_id}/assignment`; it does not change the old test or
those Task 4 paths.

This was reproduced independently in a detached `c059151` worktree with:

```text
env -u VIRTUAL_ENV \
  /Users/chenjiamin/tiangong/tianshu-worktree/tianshu/.venv/bin/python \
  -m pytest \
  tests/gateway/test_evolution_gate_api.py::test_task_three_api_exposes_no_promote_canary_or_rollback_route \
  -q

1 failed, 4 warnings
```

The temporary worktree was removed after reproduction.

Static gates:

- Ruff check: all changed production and test files passed.
- Ruff format check: all changed production and test files passed.
- Mypy: 11 new/core affected modules passed with no issues.
- Import Linter: 480 files and 1,731 dependencies analyzed; 2 contracts kept,
  0 broken.
- `git diff --check`: passed.
- `uv.lock`: unchanged.

Running mypy directly over the entire historical `skills/loader.py` reports seven
pre-existing `object` attribute errors outside this diff (metrics store and watcher
observer/event-loop members). They were not suppressed or opportunistically refactored;
the changed runtime overlay behavior is covered by focused runtime tests and Ruff.
The same seven errors were independently reproduced in a detached `c059151` worktree
with `.venv/bin/mypy src/tianshu/skills/loader.py` (line numbers differ only by this
Task 5 file's inserted runtime-overlay code).

The four warnings in test runs are existing third-party deprecations from Lark SDK and
websockets.

## Review remediation (2026-07-18)

The Task 5 review findings were remediated with a second RED/GREEN cycle. No Task 6
distribution or rollback work was added.

- Governed assignments now always carry a real candidate identity. Both champion and
  challenger arms persist the candidate kind and subject, verify the selected immutable
  artifact on every attempt, and consume only the normalized frozen payload. Champion
  execution has no live-resource fallback.
- Runs submitted without an active canary persist a strict, truthful
  `legacy_unmanaged` marker. The marker contains no fabricated candidate references or
  evidence and prevents a later canary from retroactively rerouting the Memorial.
- Artifact reads return and validate the complete `ArtifactRefV1` contract: digest,
  canonical digest, exact kind media type, redaction marker, and canonical bytes. The
  owning adapter is the single normalization boundary.
- Skill overlays affect metadata, index, always-load, list/view, `load_all()`, and
  `get()`. An absent overlay hides the Skill from every surface, and ContextVar exit
  restores the live view without mutating shared caches.
- All production Edict ingress paths receive the same application service and router
  from `app.state`; no production constructor creates a bare fallback service. The
  routing secret is explicitly injected from settings. A dispatcher without a router
  rejects before claim, while a post-claim binding failure is fenced to a stable,
  non-retryable failed outcome and performs cleanup exactly once.
- The Universe compatibility facade never reports the champion for a challenger arm.
  The SQL architecture scanner evaluates each concrete statement and exact write table.
  Assignment reads accept API or admin scope; API tokens are owner-scoped, admin tokens
  bypass ownership, and absent/non-owner reads are disclosure-safe 404 responses.
- Bucketing uses collision-free versioned canonical JSON input and reads an existing
  assignment before evaluating current routing configuration or secret material.

Fresh remediation verification:

```text
required Task 5 suite:                         154 passed, 4 warnings
gateway suite (known stale Task 3 deselected): 642 passed, 1 deselected, 4 warnings
application/claim/recovery/integration:         203 passed, 4 warnings
evolution/authority/migrations:                 258 passed, 4 warnings
skills plus submit/schedule tools:              278 passed, 4 warnings
```

Final static gates:

- Ruff check and format: 51 changed Python files passed.
- Mypy: 12 affected core modules passed; the changed Skill overlay passed separately
  while disabling only the seven documented pre-existing `attr-defined` errors in the
  watcher/metrics portions of that historical file.
- Import Linter: 480 files and 1,737 dependencies analyzed; 2 contracts kept, 0 broken.
- `git diff --check`: passed.
- `uv.lock`: unchanged.

## Review-two remediation (2026-07-18)

The second Task 5 review was handled as a separate, narrowly scoped RED/GREEN cycle.

- `BaseCandidateAdapter.resolve_effective_payload()` now invokes an adapter-owned
  `require_subject_binding()` hook after domain normalization. The default hook adds no
  semantics to memory, policy, persona, or code. The Skill adapter alone requires the
  canonical `skill:` prefix and exact equality between `overlay.subject_key` and the
  normalized package name. Present and absent packages both require a canonical Skill
  identifier. The loader remains only a consumer.
- Present and absent packages on both champion and challenger arms are covered. A
  claimed-name/package-name mismatch fails before assignment insertion and rolls back
  the Edict, Memorial, assignment, and outbox in the same submission Unit of Work.
  Later durable subject attribution drift also fails runtime binding.
- `ChallengerRouter.bind_runtime()` converts only
  `EvolutionRepositoryDecodeError` into the explicit
  `RunAssignmentUnavailable("run_assignment_unavailable")` subtype. RunDispatcher
  completes the claimed attempt once as non-retryable FAILED, never starts the runner,
  and performs cleanup once. Real bad-hash, conflicting-column, and candidate-attribution
  rows are covered. A real `sqlite3.OperationalError("database is busy")` remains
  unmodified and propagates to the infrastructure failure path.
- The prior Ruff count was corrected after counting commit `8805805` directly:
  51 changed Python files, plus the Markdown report.

Fresh review-two verification:

```text
focused review-two regressions:                 11 passed, 4 warnings
required Task 5 baseline:                      154 passed, 4 warnings
dispatcher/architecture/gateway relevant set: 126 passed, 4 warnings
```

Static gates for the review-two delta:

- Ruff check and format: all 6 changed Python files passed.
- Mypy: all 4 changed production modules passed.
- Import Linter: 480 files and 1,739 dependencies analyzed; 2 contracts kept, 0 broken.
- `git diff --check`: passed.
- `uv.lock`: unchanged.
