"""Live v10 Telegram ingress identity migration contracts."""

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

_FROZEN_V1_TO_V9_DEFINITIONS = (
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
)


def _primary_key(connection: sqlite3.Connection) -> tuple[tuple[int, str], ...]:
    return tuple(
        sorted(
            (int(row[5]), str(row[1]))
            for row in connection.execute("PRAGMA table_info(telegram_seen_messages)")
            if int(row[5]) > 0
        )
    )


def test_live_migration_prefix_through_v10_does_not_drift_v1_to_v9() -> None:
    assert (
        tuple(
            (migration.version, migration.name, migration.checksum) for migration in MIGRATIONS[:9]
        )
        == _FROZEN_V1_TO_V9_DEFINITIONS
    )
    assert tuple(migration.version for migration in MIGRATIONS[:10]) == tuple(range(1, 11))
    assert (MIGRATIONS[9].version, MIGRATIONS[9].name) == (
        10,
        "0010_telegram_seen_instance_identity",
    )


def test_fresh_schema_uses_instance_scoped_telegram_update_identity() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        assert _primary_key(storage._conn) == (  # noqa: SLF001 - schema contract
            (1, "instance_id"),
            (2, "update_id"),
        )

        storage.mark_telegram_update_seen("shared-update", instance_id="telegram-first")
        storage.mark_telegram_update_seen("shared-update", instance_id="telegram-second")
        storage.mark_telegram_update_seen("shared-update", instance_id="telegram-first")

        rows = storage._conn.execute(  # noqa: SLF001 - schema contract
            "SELECT instance_id, update_id FROM telegram_seen_messages ORDER BY instance_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("telegram-first", "shared-update"),
            ("telegram-second", "shared-update"),
        ]
    finally:
        storage.close()


def test_v9_to_v10_upgrade_preserves_instance_and_default_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:9]) == tuple(range(1, 10))
        connection.execute(
            """
            INSERT INTO telegram_seen_messages (update_id, instance_id, seen_at)
            VALUES (?, ?, ?)
            """,
            ("legacy-explicit", "telegram-alpha", "2026-07-15T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO telegram_seen_messages (update_id, seen_at) VALUES (?, ?)",
            ("legacy-default", "2026-07-15T00:00:01+00:00"),
        )
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS[:10]) == (10,)

        assert _primary_key(connection) == ((1, "instance_id"), (2, "update_id"))
        assert connection.execute(
            """
            SELECT instance_id, update_id, seen_at
            FROM telegram_seen_messages
            ORDER BY update_id
            """
        ).fetchall() == [
            ("telegram-default", "legacy-default", "2026-07-15T00:00:01+00:00"),
            ("telegram-alpha", "legacy-explicit", "2026-07-15T00:00:00+00:00"),
        ]
        connection.execute(
            """
            INSERT INTO telegram_seen_messages (instance_id, update_id, seen_at)
            VALUES (?, ?, ?)
            """,
            ("telegram-beta", "legacy-explicit", "2026-07-15T00:00:02+00:00"),
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM telegram_seen_messages WHERE update_id = 'legacy-explicit'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (10, "0010_telegram_seen_instance_identity")
    finally:
        connection.close()


def test_v10_failure_rolls_back_table_rebuild_and_ledger() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:9]) == tuple(range(1, 10))
        connection.execute(
            "INSERT INTO telegram_seen_messages (update_id, seen_at) VALUES (?, ?)",
            ("legacy-update", "2026-07-15T00:00:00+00:00"),
        )
        connection.commit()
        migration = MIGRATIONS[9]

        def fail_after_rebuild(active: MigrationConnection) -> None:
            migration.upgrade(active)
            raise RuntimeError("stop after v10 rebuild")

        failing = Migration(
            version=migration.version,
            name=migration.name,
            checksum=migration.checksum,
            upgrade=fail_after_rebuild,
        )

        with pytest.raises(MigrationExecutionError, match=migration.name):
            apply_migrations(connection, (*MIGRATIONS[:9], failing))

        assert _primary_key(connection) == ((1, "update_id"),)
        assert connection.execute(
            "SELECT update_id, instance_id, seen_at FROM telegram_seen_messages"
        ).fetchall() == [("legacy-update", "telegram-default", "2026-07-15T00:00:00+00:00")]
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (9,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = '_telegram_seen_messages_v10'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_applied_v10_checksum_drift_is_rejected_without_writes() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:10]) == tuple(range(1, 11))
        connection.execute(
            """
            INSERT INTO telegram_seen_messages (instance_id, update_id, seen_at)
            VALUES ('telegram-one', 'stable', '2026-07-15T00:00:00+00:00')
            """
        )
        connection.commit()
        drifted = (*MIGRATIONS[:9], replace(MIGRATIONS[9], checksum="f" * 64))

        with pytest.raises(MigrationStateError, match="checksum drift"):
            pending_migrations(connection, drifted)

        assert connection.execute(
            "SELECT instance_id, update_id FROM telegram_seen_messages"
        ).fetchall() == [("telegram-one", "stable")]
    finally:
        connection.close()


def test_concurrent_v10_upgrade_executes_rebuild_once(tmp_path: Path) -> None:
    database = tmp_path / "v10-concurrent.sqlite3"
    setup = sqlite3.connect(database)
    try:
        assert apply_migrations(setup, MIGRATIONS[:9]) == tuple(range(1, 10))
    finally:
        setup.close()

    connection_a = sqlite3.connect(database, timeout=5, check_same_thread=False)
    connection_b = sqlite3.connect(database, timeout=5, check_same_thread=False)
    migration = MIGRATIONS[9]
    callback_entered = Event()
    release_callback = Event()
    calls_lock = Lock()
    callback_calls = 0
    results: dict[str, tuple[int, ...]] = {}
    errors: dict[str, BaseException] = {}

    def controlled_upgrade(active: MigrationConnection) -> None:
        nonlocal callback_calls
        with calls_lock:
            callback_calls += 1
        callback_entered.set()
        if not release_callback.wait(timeout=5):
            raise TimeoutError("test did not release v10 migration")
        migration.upgrade(active)

    controlled = Migration(
        version=migration.version,
        name=migration.name,
        checksum=migration.checksum,
        upgrade=controlled_upgrade,
    )
    definitions = (*MIGRATIONS[:9], controlled)

    def run(label: str, connection: sqlite3.Connection) -> None:
        try:
            results[label] = apply_migrations(connection, definitions)
        except BaseException as exc:
            errors[label] = exc

    thread_a = Thread(target=run, args=("a", connection_a))
    thread_b = Thread(target=run, args=("b", connection_b))
    try:
        thread_a.start()
        assert callback_entered.wait(timeout=5)
        thread_b.start()
        release_callback.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)

        assert not thread_a.is_alive()
        assert not thread_b.is_alive()
        assert errors == {}
        assert sorted(results.values()) == [(), (10,)]
        assert callback_calls == 1
        assert _primary_key(connection_a) == ((1, "instance_id"), (2, "update_id"))
        assert connection_a.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 10"
        ).fetchone() == (1,)
    finally:
        release_callback.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
        connection_a.close()
        connection_b.close()
