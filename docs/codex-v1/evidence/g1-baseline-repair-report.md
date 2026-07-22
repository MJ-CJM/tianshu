# G1 current-branch baseline repair report

## Scope

Repair the seven full-suite failures recorded after G1.3b3 without changing any
frozen migration version, name, checksum, or ordering, and without weakening the
requested-governance-contract boundary.

## Root-cause classification

1. `test_concurrent_storage_startup_creates_one_true_pre_migration_backup` and
   `test_migration_upgrades_old_session_tables` were stale assertions. They
   hard-coded ledger versions 1-2 even though the frozen append-only sequence now
   contains version 3 (`0003_governance_contracts`). The assertions now derive the
   complete expected `(version, name)` ledger from `MIGRATIONS`, so later append-only
   migrations cannot silently leave these tests stale.
2. The two extended Auditor failures and the full integration-chain failure exposed
   one production contract bug. `Auditor` still passed a mutable `Edict` object to
   `Storage.update_edict`, whose current API accepts an ID plus editable fields.
   Auto-close now uses the dedicated lifecycle operation
   `update_edict_status(edict.id, EdictStatus.COMPLETED.value)`. No compatibility
   overload was added.
3. The Gateway 409 was not an asynchronous status race. Requested governance
   contracts freeze `goal` and `context` at creation, and the dedicated
   `governance_contract_frozen` test independently proves that 409 contract. The
   stale CRUD test now deterministically seeds an OPEN edict, updates only its
   editable title, and proves the frozen goal is preserved.
4. The public Docker truth test required obsolete local-only wording. It now checks
   the current default loopback `trusted-local` boundary and the explicit
   `secure-remote` requirements (HTTPS public URL, exact Host/Origin, trusted proxy
   CIDR, and anonymous REST/WebSocket/MCP denial).

## TDD evidence

- Initial exact reproduction: `7 failed, 2 warnings`.
- After updating the desired test contracts and before the production fix: the
  Auditor unit nodes and integration chain remained RED with the same
  `sqlite3.ProgrammingError`, while the migration/Gateway contract nodes were GREEN.
- Minimal production change: one Auditor call site moved to the existing dedicated
  status API.
- Exact final seven-node rerun: `7 passed, 1 third-party warning`.

## Related regression evidence

- Storage backup/migration/governance suites: `100 passed`.
- Auditor + integration + notifier suites: `20 passed`.
- Gateway extended + all `tests/gateway`: `483 passed`.
- Public documentation truth suite: `10 passed`.

## Quality evidence

- `uv run ruff check .`: all checks passed.
- `uv run ruff format --check .`: 647 files already formatted.
- `uv run mypy`: success for 106 source files.
- `uv run lint-imports`: 391 files / 1160 dependencies / 2 contracts kept /
  0 broken.
- Stable whole-repository rerun after G1.3b3 review fixes landed: `2239 passed,
  145 warnings` in 109.46 seconds. The warnings are the known third-party
  deprecations and existing un-awaited mocked LLM coroutine warnings; there are
  no test failures.

## Concurrent-work note

The first whole-repository rerun overlapped uncommitted G1.3b3 review hardening in
`scripts/sync_persona_templates.py` and its AST/Git/Gateway security tests. It saw a
mid-edit snapshot (`6 failed, 2216 passed`) unrelated to this baseline repair. Those
files are owned by the concurrent G1.3b3 implementer and were not modified or staged
by this task. That work was committed separately as `6100eb1`; the fresh stable result
above supersedes the mid-edit run.
