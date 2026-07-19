# S0.2/S0.3 Core Governed Apply Close Brief

## Scope

Close only the persistence/domain/anchored-filesystem/Git governed-apply
boundary already present in the dirty tree. Do not touch or stage REST, Auth,
CLI, app registration, or capability-surface files.

Read first:

- `../design/14-g1.4b3-governed-apply-brief.md`
- `../plans/01-rebaselined-execution.md`

Implementation lineage base is `1b51bcd9`. Before resuming, record the current
tracked docs-handoff HEAD as the S0 review base so the handoff bundle is excluded
from S0 diffs. Preserve all existing work. Use
`env -u VIRTUAL_ENV .venv/bin/python`. Do not run full pytest and do not run
`uv run`.

## Owned production files

- `src/tianshu/models/workspace.py`
- `src/tianshu/storage/migrations.py`
- `src/tianshu/storage/workspace_repo.py`
- `src/tianshu/executor/anchored_fs.py`
- `src/tianshu/executor/workspace_apply.py`
- `src/tianshu/executor/workspace_service.py`
- `src/tianshu/executor/git_backend.py`

## Owned tests

- `tests/services/test_workspace_governed_apply.py`
- `tests/executor/test_workspace_apply.py`
- `tests/executor/test_git_backend.py`
- `tests/executor/test_executor_workspace_lifecycle.py`
- `tests/executor/test_workspace_changes.py`
- `tests/executor/test_workspace_context.py`
- `tests/executor/test_workspace_runtime.py`
- `tests/executor/test_workspace_staging.py`
- `tests/executor/test_dag_workspace_lifecycle.py`
- `tests/security/test_workspace_runtime_binding.py`
- `tests/storage/test_workspace_foundation.py`
- `tests/storage/test_migration_preserves_data.py`
- `tests/storage/test_migration_ledger.py`
- `tests/storage/test_backup_restore.py`
- `tests/test_storage_instance_migration.py`
- `tests/test_workspace_wiring.py`
- `tests/tools/test_workspace_runtime_tools.py`

## Required checks

Verify additive V5/checksum preservation, immutable full authority binding,
one-time terminal decisions, safe receipts, source-level async/process locks,
root-anchored publication and rollback, symlink/TOCTOU rejection, pending-write
ownership, exact ref/object/index/status identities, POSIX mode preservation,
CAS, failure injection, and synchronous rollback truth.

Run all owned tests together. If any defect appears, reproduce it with a new
failing test, observe RED, implement the minimum fix, and observe GREEN. Then run
compileall, Ruff check/format on owned files, and `git diff --check`.

Stage and commit only the owned production files and tests with:

`feat: add governed workspace apply authority`

Do not stage any public-surface file. Write the complete report to
`.superpowers/sdd/s0-core-report.md`, including exact commands/results, TDD
evidence for fixes, self-review, commit SHA, remaining risks, and confirmation
that no full suite ran. Return only status, commit, test summary, concerns, and
report path.
