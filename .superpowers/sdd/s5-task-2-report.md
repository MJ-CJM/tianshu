# S5 Task 2 Report — Five Adapters and Candidate Staging

## Status

DONE

Implemented the five `memory` / `skill` / `policy` / `persona` / `code` source adapters and the common `CandidateService.propose()` / `CandidateService.stage()` path. The implementation reuses the Task 1 `EvolutionCandidateV1` envelope, lifecycle graph, evolution contract, and `EvolutionRepository`, plus the S3 `ArtifactRefV1`, `ArtifactStore`, canonical JSON/digest helpers, and same-connection SQLite unit of work.

No GateEvaluator, PromotionService, live routing, champion/allocation writes, Web/API changes, or real activation/rollback wiring were added.

## TDD Evidence

### Initial five-domain RED

Command:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_candidate_adapters.py -q
```

Result: exit 2, 1 collection error. Expected failure: `ModuleNotFoundError: No module named 'tianshu.evolution'` before production implementation existed.

### First GREEN

The initial 15-case parameterized matrix reached:

```text
15 passed, 4 warnings in 0.88s
```

### Fail-closed RED / GREEN

After deleting the provisional untested activation/rollback bodies, the new five-domain fail-closed matrix produced the intended RED:

```text
5 failed, 15 passed, 4 warnings in 1.44s
```

All five failures were `AttributeError` for the absent `activate` interface. Restoring the minimal explicit fail-closed interface produced final focused GREEN:

```text
20 passed, 4 warnings in 1.15s
```

The warnings are pre-existing third-party deprecations from `lark_oapi` / `websockets`, not new task warnings.

## Five-domain Validation Differences

- `memory`: validates through the existing `MemoryEntry` model and `memory.safety.validate_content()` before materialization.
- `skill`: validates the canonical skill name/content with the existing `SkillValidator`, including guard/frontmatter checks, and binds the declared frontmatter name to the source name.
- `policy`: validates `WorkspacePolicyV1` + `RecoveryPolicyV1` through the existing `validate_workspace_policy()` matrix.
- `persona`: validates through the existing `AgentPersona` model; it does not touch runtime persona directories or SQLite persona rows.
- `code`: validates through the existing `CanonicalChangeSet`, including safe path/Git identity rules; it never auto-promotes or applies a change set.

All adapters inherit one materialization path that uses S3 canonical JSON and content-addressed artifacts. None defines a parallel candidate, lifecycle, contract, hash, champion, allocation, or routing schema.

## Binding and Ownership

- `CandidateService` alone derives the deterministic candidate ID from the complete canonical proposal, constructs provenance, binds evidence bundle IDs, constructs the Task 1 envelope/rollback spec, persists through `EvolutionRepository`, and transitions `PROPOSED -> STAGED`.
- Source, base, candidate, diff, and evolution-contract bindings all use the shared canonical digest implementation.
- Wrong adapter selection raises `AdapterKindMismatch` before validation or materialization.
- `activate()` and `rollback()` exist only as explicit later-service protocol boundaries and raise `AdapterOperationUnavailable`; there is no hidden promotion or live effect.

## Idempotency and No-live-mutation Evidence

The five-domain restart test creates a sentinel live resource, proposes and stages with one service instance, creates a new service instance over the same durable storage/artifact root, and repeats both stage and propose. For every kind it asserts:

- identical staged receipt after restart;
- the repeated proposal resolves to the already-staged durable candidate;
- exactly one candidate row and two lifecycle journal rows (`PROPOSED`, `STAGED`);
- unchanged live-resource sentinel content;
- `routing is None` and automatic promotion remains false.

Stage artifact insertion and lifecycle CAS use the same SQLite connection/UoW via `ArtifactStore.put_bytes_current()`, so a stage receipt and state transition commit together.

## Files and Interfaces

Created:

- `src/tianshu/evolution/__init__.py`
- `src/tianshu/evolution/candidate_service.py`
- `src/tianshu/evolution/adapters/__init__.py`
- `src/tianshu/evolution/adapters/base.py`
- `src/tianshu/evolution/adapters/memory.py`
- `src/tianshu/evolution/adapters/skill.py`
- `src/tianshu/evolution/adapters/policy.py`
- `src/tianshu/evolution/adapters/persona.py`
- `src/tianshu/evolution/adapters/code.py`
- `tests/evolution/test_candidate_adapters.py`
- `.superpowers/sdd/s5-task-2-report.md`

Public interfaces:

- `CandidateProposalV1`, `CandidateSourceV1`, `ProvenanceInputV1`
- `CandidateService.propose(proposal)`
- `CandidateService.stage(candidate_id)`
- `CandidateAdapter.validate_source/build_diff/stage/activate/rollback`
- `StagedCandidateV1`, `ActivationReceiptV1`, `RollbackReceiptV1`

## Verification

Brief command:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/evolution/test_candidate_adapters.py tests/universe tests/skills -q
498 passed, 4 warnings in 38.02s
```

Task 1 / v18 regression:

```text
155 passed, 4 warnings in 4.24s
```

Quality gates:

```text
ruff check: All checks passed
ruff format --check: 10 files already formatted
mypy src/tianshu/evolution: Success, no issues in 9 source files
lint-imports: 2 kept, 0 broken
git diff --check: clean
```

## Commit

Commit subject: `feat: stage five evolution candidate kinds`. The immutable commit hash is listed in the final task handoff.

## Concerns

No task blocker. Activation and rollback deliberately remain unavailable until the later PromotionService task supplies a safe live-resource boundary. The third-party deprecation warnings noted above remain pre-existing.

## Review Remediation — 2026-07-17

The Task 2 review returned four Important findings and one Minor finding. This follow-up closes all five without adding Task 3 gate, allocation, promotion, web, or live-mutation behavior.

- Proposal base/candidate/diff artifact rows and candidate insertion now share one SQLite unit of work. Stage receipt insertion, lifecycle CAS, and commit use the same transaction.
- Artifact writes return tracked ownership receipts. Rollback compensation removes only a file created by that failed transaction, only when its inode is unchanged and no durable metadata exists. Within the ArtifactStore writer-lock protocol, pre-existing or shared digest bytes are not claimed or deleted; uncooperative external filesystem writers are outside this guarantee.
- All five adapters materialize their strict normalized domain model. Extras fail closed with generic errors that do not echo source payloads; memory coercions, `CanonicalChangeSet` ordering, safe persona-relative paths, and canonical skill-member ordering therefore affect the persisted bytes and digest.
- Skill candidates now describe a complete package. Validate-only staging reuses installer member limits, traversal/root checks, symlink rejection, unique root `SKILL.md`, frontmatter/name validation, and guard scanning without installing into a live skill directory.
- Candidate identity binds command, kind, subject, normalized base/candidate versions and digests, evolution contract, provenance principal/source, evidence IDs, and restore point. Identical retries remain stable while each identity-bearing input changes the ID.
- Real temporary memory/skill/policy/persona/code resources remain byte-identical through propose/stage. Close/reopen storage tests and two independent SQLite connections cover restart and concurrency; injected envelope/insert/CAS/commit failures assert candidate, journal, artifact-row, and artifact-file outcomes.

TDD review-fix evidence:

```text
Initial reviewer-focused RED: 4 failed, 20 deselected
Artifact persistent-commit RED: 1 failed, 1 passed, 2 deselected
Artifact persistent-commit GREEN: 2 passed, 2 deselected
Candidate full file: 51 passed, 4 warnings in 2.49s
```

Fresh final verification:

```text
Task 2 brief (candidate + universe + skills): 529 passed, 4 warnings in 28.15s
Evidence plus evolution contract regression: 181 passed, 4 warnings in 11.96s
Task 1 brief regression: 118 passed, 4 warnings in 0.73s
Skills/installer regression: 208 passed, 4 warnings in 1.89s
ruff check: All checks passed
mypy: Success, no issues found in 11 source files
lint-imports: 2 kept, 0 broken
git diff --check: clean
```

The four warnings are unchanged third-party deprecations from `lark_oapi` and `websockets`. No review-remediation blocker remains.

## Second Review Remediation — 2026-07-17

The second review identified four remaining boundary gaps. The remediation stays inside Task 2:

- Artifact publish, metadata repair, and cleanup now share one sibling artifact-root lock using POSIX `fcntl.flock`, the Lean target supported by Ubuntu and macOS. The lock descriptor is released on normal and exceptional exits. A spawned process with an independent `Storage` and `ArtifactStore` proves a protocol-compliant same-digest publish cannot be removed by concurrent rollback cleanup. This is a cooperating-writer protocol guarantee, not protection from processes that bypass the lock.
- Persona source paths now require a canonical relative POSIX representation and reject Windows drives, absolute/root/UNC forms, backslashes, empty or dot segments, traversal, NUL, and control characters without echoing the input.
- Skill package members are canonicalized to POSIX targets before target-level duplicate checks. Empty/root aliases, slash and dot aliases, absolute/backslash/traversal paths, and file/directory target conflicts fail closed; only sorted canonical members enter materialized bytes.
- `CandidateLiveAuthorities` injects the memory root, skill install target, policy root, persona root, and code worktree into adapters. Skill validation uses the injected installer target rather than cwd. Five-domain tests use separate live memory/persona SQLite data plus real skill, policy, persona, and worktree roots, repeat after close/reopen, and verify both authority-tree and isolated-cwd digests remain unchanged.

Second-review TDD evidence:

```text
Initial focused RED: 8 failed, 4 warnings in 1.00s
Artifact TOCTOU RED: independent publish succeeded; final shared path was missing
Artifact TOCTOU GREEN: 1 passed, 4 warnings in 2.39s
Persona focused GREEN: 3 passed, 4 warnings in 0.29s
Skill canonical-path focused GREEN: 3 passed, 4 warnings in 0.29s
Live-authority focused GREEN: 6 passed, 52 deselected, 4 warnings in 0.60s
```

Fresh second-review verification:

```text
Candidate plus Evidence: 182 passed, 4 warnings in 16.26s
Skills/installer: 208 passed, 4 warnings in 1.93s
Task 2 brief: 536 passed, 4 warnings in 29.76s
Task 1 regression: 118 passed, 4 warnings in 0.72s
ruff check: All checks passed
ruff format --check: 13 files already formatted
mypy: Success, no issues found in 11 source files
lint-imports: 2 kept, 0 broken
git diff --check: clean
```

No Task 3 gate, allocation, promotion, web, live activation, or `uv.lock` behavior was added. The four warnings remain the pre-existing third-party `lark_oapi` and `websockets` deprecations.
