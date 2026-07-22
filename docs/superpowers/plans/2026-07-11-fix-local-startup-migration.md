# Local Startup Migration Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./scripts/local.sh start --dev` start against the real pre-ledger Tianshu database safely, and fail immediately with useful diagnostics when the backend exits during startup.

**Architecture:** Extend migration 1 with exact-signature adapters for the four confirmed historical SQLite differences, keeping unknown drift fail-closed and all writes inside the existing owned transaction. Add a process-aware health wait to `local.sh` so failed startup cleans up both child services and stale PID files instead of silently polling for 30 seconds.

**Tech Stack:** Python 3.14, SQLite, pytest, Bash, Vite/Uvicorn.

## Global Constraints

- Preserve the existing strict schema comparison; only the exact confirmed historical signatures may bypass the initial difference check.
- Preserve all valid business rows and verify copied payloads before dropping legacy tables.
- Historical `events` rows with no parent `edicts` row cannot satisfy the canonical foreign key; remove only those rows inside the migration transaction, with the existing pre-migration full backup as recovery evidence.
- Map historical pending notifications to a deterministic memorial; refuse and roll back if a row cannot be mapped.
- Do not edit or delete existing backup files.
- Do not change the normal ports: backend `8000`, Vite `7999`.
- Do not weaken migration integrity checks or write a ledger row before canonical verification succeeds.

---

### Task 1: Historical core-schema compatibility adapter

**Files:**
- Modify: `tests/storage/test_migration_preserves_data.py`
- Modify: `src/tianshu/storage/migrations.py`

**Interfaces:**
- Consumes: `MigrationConnection`, `_schema_differences()`, canonical schema signatures, and migration-ledger transaction ownership.
- Produces: exact legacy-shape detectors and `_migrate_historical_core_tables(conn: MigrationConnection) -> None` behavior used by `_baseline_upgrade()`.

- [ ] **Step 1: Write failing migration tests**

Create a helper that starts with `_build_canonical_preledger()`, rebuilds `pending_notifications`, `memorials`, and `events` into their confirmed historical definitions, and creates the historical redundant index:

```python
CREATE TABLE pending_notifications (
    id TEXT PRIMARY KEY,
    edict_id TEXT,
    message_json TEXT NOT NULL,
    rendered TEXT,
    channels_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE memorials (
    id TEXT PRIMARY KEY,
    edict_id TEXT NOT NULL REFERENCES edicts(id),
    status TEXT NOT NULL,
    summary TEXT,
    result TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    instruction TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    parent_memorial_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'not_required',
    audit_json TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    timeline_json TEXT NOT NULL DEFAULT '[]',
    dag_node_id TEXT,
    persona_id TEXT,
    runtime_override_json TEXT,
    acceptance_override_json TEXT,
    reasoning_content TEXT,
    final_output TEXT,
    universe_id TEXT,
    feedback_score INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at TEXT,
    failure_reason TEXT
);
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    edict_id TEXT NOT NULL,
    memorial_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_outer_loop_edict
    ON outer_loop_iterations(edict_id, iteration);
```

Add tests that require:

```python
def test_historical_preledger_core_shape_upgrades_to_canonical_without_valid_row_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical.sqlite3"
    _build_historical_core_preledger(path)
    storage = Storage(str(path))
    storage.init_db()
    assert storage._conn.execute(
        "SELECT memorial_id FROM pending_notifications WHERE id='pending-1'"
    ).fetchone()[0] == "memorial-1"
    assert storage._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert storage._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='idx_outer_loop_edict'"
    ).fetchone() is None
    assert [(row["version"], row["name"]) for row in _ledger_rows(storage._conn)] == [
        (1, _BASELINE_NAME)
    ]
    storage.close()


def test_historical_preledger_core_adapter_discards_only_orphan_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-orphan.sqlite3"
    _build_historical_core_preledger(path, include_orphan_event=True)
    storage = Storage(str(path))
    storage.init_db()
    assert storage._conn.execute("SELECT id FROM events ORDER BY id").fetchall() == [
        ("event-valid",)
    ]
    storage.close()


def test_unmappable_historical_pending_notification_rolls_back_entire_baseline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-unmappable.sqlite3"
    _build_historical_core_preledger(path, pending_edict_id="missing-edict")
    _assert_preledger_rejected_without_ledger(path)
    conn = _connect(path)
    assert conn.execute("SELECT rendered FROM pending_notifications").fetchone()[0] == "legacy"
    conn.close()


def test_near_miss_historical_core_shape_still_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "historical-near-miss.sqlite3"
    _build_historical_core_preledger(path)
    conn = _connect(path)
    conn.execute("ALTER TABLE pending_notifications ADD COLUMN unknown_payload TEXT")
    conn.commit()
    conn.close()
    _assert_preledger_rejected_without_ledger(path)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/storage/test_migration_preserves_data.py -k historical -q
```

Expected: the upgrade tests fail with `MigrationExecutionError` containing `unsupported pre-ledger schema`; rollback/fail-closed tests continue to pass.

- [ ] **Step 3: Implement exact legacy detectors**

In `migrations.py`, define exact table signatures for the three historical table definitions and an exact named-index signature for:

```sql
CREATE INDEX idx_outer_loop_edict
ON outer_loop_iterations(edict_id, iteration)
```

Return a set of individually detected known differences. Do not treat a table or index as historical unless its full signature matches.

- [ ] **Step 4: Implement transactional rebuilds**

For each detected historical table:

```python
# 1. Snapshot the source payload in deterministic primary-key order.
# 2. Create a reserved temporary table with the canonical definition.
# 3. Copy mapped/valid rows.
# 4. Read back and compare the copied payload.
# 5. Drop the source table, rename the temporary table, recreate canonical indexes.
```

For pending notifications, resolve `memorial_id` from the same `edict_id`, choosing the latest memorial at or before notification time and using `id` as a deterministic tie-breaker. Raise `SchemaCompatibilityError` if any row cannot be mapped.

For events, copy rows only when their `edict_id` exists; the pre-migration full backup retains any rejected orphan records.

- [ ] **Step 5: Integrate with baseline validation**

Add the exact detected tables/index to the initial ignored sets, perform all known adapters, then run the existing unmodified final `_schema_differences(conn)` check. Add all new temporary table names to `_RESERVED_TEMP_TABLES`.

- [ ] **Step 6: Run migration tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/storage/test_migration_preserves_data.py tests/storage/test_migration_ledger.py tests/storage/test_backup_restore.py tests/test_storage_instance_migration.py -q
```

Expected: all tests pass; only the existing third-party deprecation warning may remain.

### Task 2: Fail-fast local startup and cleanup

**Files:**
- Create: `tests/scripts/test_local_sh.py`
- Modify: `scripts/local.sh`

**Interfaces:**
- Consumes: existing PID files, `stop_by_pid()`, Uvicorn log, and `/health` polling.
- Produces: process-aware `wait_healthy()` and a single failed-start cleanup path.

- [ ] **Step 1: Write a failing isolated-script test**

The pytest fixture creates a temporary project containing the real `local.sh`, a fake Uvicorn that writes `simulated startup failure` and exits, a fake long-lived Vite, and fake `curl`/`lsof`/`sleep` commands. Run `start --dev` and assert:

```python
assert result.returncode != 0
assert "uvicorn exited before becoming healthy" in result.stdout.lower()
assert "simulated startup failure" in result.stdout
assert not (runtime / "uvicorn.pid").exists()
assert not (runtime / "vite.pid").exists()
assert not process_is_alive(vite_pid)
```

The test must clean up the fake Vite in `finally` so the RED run cannot leak a process.

- [ ] **Step 2: Run the script test and verify RED**

Run:

```bash
.venv/bin/pytest tests/scripts/test_local_sh.py -q
```

Expected: failure because the current script emits only the generic 30-second health error and leaves PID/process state behind.

- [ ] **Step 3: Implement minimal fail-fast behavior**

Update `wait_healthy()` to read the Uvicorn PID and check `kill -0` on every iteration. When it exits, print a clear error and a bounded tail of the Uvicorn log, then return nonzero.

Wrap the call in `cmd_start()`:

```bash
if ! wait_healthy; then
    stop_by_pid "$VITE_PID_FILE" "vite" || true
    stop_by_pid "$UVICORN_PID_FILE" "uvicorn" || true
    rm -f "$VITE_PID_FILE" "$UVICORN_PID_FILE"
    return 1
fi
```

The same cleanup path also handles a live backend that never becomes healthy within 30 seconds.

- [ ] **Step 4: Verify GREEN and shell syntax**

Run:

```bash
.venv/bin/pytest tests/scripts/test_local_sh.py -q
bash -n scripts/local.sh
```

Expected: all tests pass and Bash syntax exits zero.

### Task 3: Real database-copy and startup verification

**Files:**
- No source-file changes expected.

**Interfaces:**
- Consumes: `~/.tianshu/tianshu.db` as read-only source and the completed migration/startup fixes.
- Produces: verification evidence without risking the source database before tests pass.

- [ ] **Step 1: Verify a copy of the real database**

Create a temporary copy using SQLite online backup, initialize `Storage` against the copy, and assert:

```python
assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
assert conn.execute("SELECT count(*) FROM memorials").fetchone()[0] == 169
assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 7775
assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
```

- [ ] **Step 2: Run relevant and full verification**

Run:

```bash
.venv/bin/pytest tests/storage tests/scripts/test_local_sh.py -q
.venv/bin/ruff check src/tianshu/storage tests/storage tests/scripts/test_local_sh.py
bash -n scripts/local.sh
```

- [ ] **Step 3: Run actual local startup**

Run:

```bash
./scripts/local.sh start --dev
./scripts/local.sh status
```

Expected: backend healthy on `8000`, frontend running on `7999`, and no migration error. Leave the requested development services running.
