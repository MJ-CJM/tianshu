"""Live v11 durable Decision and RunState schema contracts."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from tianshu.storage import Storage
from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    MigrationExecutionError,
    MigrationStateError,
    apply_migrations,
    pending_migrations,
)
from tianshu.storage.migrations import MIGRATIONS

_FROZEN_V1_TO_V10_DEFINITIONS = (
    (
        1,
        "0001_adopt_v042_baseline",
        "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6",
    ),
    (2, "0002_auth_tokens", "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"),
    (
        3,
        "0003_governance_contracts",
        "07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8",
    ),
    (
        4,
        "0004_workspace_foundation",
        "1c0a028e0ea16475b9de5eb0c843f81aa275ddf62c0aca3c067bf8408dd9bee5",
    ),
    (
        5,
        "0005_governed_apply_bindings",
        "c73294984096ea15e32d6ce80294f82323408cda12e82efea645ad8f35c5abc6",
    ),
    (
        6,
        "0006_seed_default_personas",
        "596e672919bbe16b111fe3793e183b17666c7c5cad588d5532d7b2875501fca1",
    ),
    (
        7,
        "0007_system_audit_events",
        "b24d3152f2b5aaa2d7dbf5776a5c865d336e025e861f8ca110e8be0c6a42e10b",
    ),
    (
        8,
        "0008_encrypt_mcp_secret_mappings",
        "f03ad9148472267b754f6e4f1f03cefc947795c2a6717e0b89206b38244706ad",
    ),
    (
        9,
        "0009_durable_edict_ingress",
        "114d0d4daab66202b32a4f9e4eb4290e2e06602ecf9465ce4d5beae03aac0a98",
    ),
    (
        10,
        "0010_telegram_seen_instance_identity",
        "d0587f036178e5f36e25277df16528925823905cd35d8bba30e7a3a8ab680f67",
    ),
)


def _insert_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "preserve", "2026-07-15T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", "2026-07-15T00:00:00+00:00"),
    )
    connection.commit()


def _objects(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger')"
        )
    }


def test_live_migration_tail_is_v11_without_drifting_v1_to_v10() -> None:
    assert tuple((item.version, item.name, item.checksum) for item in MIGRATIONS[:10]) == (
        _FROZEN_V1_TO_V10_DEFINITIONS
    )
    assert tuple(item.version for item in MIGRATIONS) == tuple(range(1, 12))
    assert (MIGRATIONS[-1].version, MIGRATIONS[-1].name) == (
        11,
        "0011_decisions_run_state",
    )


def test_fresh_v11_schema_has_durable_objects_and_valid_foreign_keys() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        expected = {
            "decision_requests",
            "decision_resolutions",
            "run_states",
            "idx_decisions_pending",
            "idx_decisions_memorial",
            "idx_run_states_edict",
            "decision_resolutions_no_update",
            "decision_resolutions_no_delete",
        }
        assert expected <= _objects(storage._conn)  # noqa: SLF001 - schema contract
        assert storage._conn.execute("PRAGMA foreign_key_check").fetchall() == []  # noqa: SLF001
    finally:
        storage.close()


def test_v10_to_v11_upgrade_preserves_existing_data() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:10]) == tuple(range(1, 11))
        _insert_parent_rows(connection)
        connection.execute(
            "INSERT INTO telegram_seen_messages (instance_id, update_id, seen_at) VALUES (?, ?, ?)",
            ("telegram-one", "stable", "2026-07-15T00:00:00+00:00"),
        )
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS) == (11,)
        assert connection.execute("SELECT goal FROM edicts WHERE id = 'edict-1'").fetchone() == (
            "preserve",
        )
        assert connection.execute(
            "SELECT instance_id, update_id FROM telegram_seen_messages"
        ).fetchall() == [("telegram-one", "stable")]
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (11, "0011_decisions_run_state")
    finally:
        connection.close()


def test_v11_failure_rolls_back_schema_and_ledger() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:10]) == tuple(range(1, 11))
        _insert_parent_rows(connection)
        migration = MIGRATIONS[10]

        def fail_after_upgrade(active: MigrationConnection) -> None:
            migration.upgrade(active)
            raise RuntimeError("stop after v11")

        failing = Migration(
            version=migration.version,
            name=migration.name,
            checksum=migration.checksum,
            upgrade=fail_after_upgrade,
        )
        with pytest.raises(MigrationExecutionError, match=migration.name):
            apply_migrations(connection, (*MIGRATIONS[:10], failing))

        assert not {"decision_requests", "decision_resolutions", "run_states"} & _objects(
            connection
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (10,)
        assert connection.execute("SELECT goal FROM edicts WHERE id = 'edict-1'").fetchone() == (
            "preserve",
        )
    finally:
        connection.close()


def test_applied_v11_checksum_drift_is_rejected_without_writes() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert apply_migrations(connection, MIGRATIONS) == tuple(range(1, 12))
        drifted = (*MIGRATIONS[:-1], replace(MIGRATIONS[-1], checksum="f" * 64))
        before = connection.total_changes
        with pytest.raises(MigrationStateError, match="checksum drift"):
            pending_migrations(connection, drifted)
        assert connection.total_changes == before
    finally:
        connection.close()


def test_concurrent_v11_upgrade_executes_callback_once(tmp_path: Path) -> None:
    database = tmp_path / "v11-concurrent.sqlite3"
    setup = sqlite3.connect(database)
    try:
        assert apply_migrations(setup, MIGRATIONS[:10]) == tuple(range(1, 11))
    finally:
        setup.close()

    connections = (
        sqlite3.connect(database, timeout=5, check_same_thread=False),
        sqlite3.connect(database, timeout=5, check_same_thread=False),
    )
    migration = MIGRATIONS[10]
    callback_entered = Event()
    release_callback = Event()
    calls_lock = Lock()
    callback_calls = 0
    results: list[tuple[int, ...]] = []
    errors: list[BaseException] = []

    def controlled_upgrade(active: MigrationConnection) -> None:
        nonlocal callback_calls
        with calls_lock:
            callback_calls += 1
        callback_entered.set()
        if not release_callback.wait(timeout=5):
            raise TimeoutError("test did not release v11 migration")
        migration.upgrade(active)

    controlled = replace(migration, upgrade=controlled_upgrade)

    def run(connection: sqlite3.Connection) -> None:
        try:
            results.append(apply_migrations(connection, (*MIGRATIONS[:10], controlled)))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=run, args=(connection,)) for connection in connections]
    try:
        threads[0].start()
        assert callback_entered.wait(timeout=5)
        threads[1].start()
        release_callback.set()
        for thread in threads:
            thread.join(timeout=5)

        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(results) == [(), (11,)]
        assert callback_calls == 1
        assert connections[0].execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 11"
        ).fetchone() == (1,)
    finally:
        release_callback.set()
        for thread in threads:
            thread.join(timeout=5)
        for connection in connections:
            connection.close()
