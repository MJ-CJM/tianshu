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
