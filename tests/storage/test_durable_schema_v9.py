"""Live v9 durable Edict-ingress schema and transaction-boundary contracts."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from tianshu.storage import Storage
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS


@pytest.fixture
def storage() -> Storage:
    database = Storage(":memory:")
    database.init_db()
    try:
        yield database
    finally:
        database.close()


def test_live_migration_ninth_definition_remains_v9_durable_edict_ingress() -> None:
    assert tuple(migration.version for migration in MIGRATIONS[:9]) == tuple(range(1, 10))
    assert (MIGRATIONS[8].version, MIGRATIONS[8].name) == (
        9,
        "0009_durable_edict_ingress",
    )


def test_fresh_schema_contains_complete_v9_submission_objects(storage: Storage) -> None:
    objects = {
        (row[0], row[1])
        for row in storage._conn.execute(  # noqa: SLF001 - schema contract
            """
            SELECT name, type
            FROM sqlite_master
            WHERE name IN (
                'submission_idempotency', 'outbox_events', 'outbox_consumptions',
                'idx_outbox_claim', 'idx_outbox_edict'
            )
            """
        )
    }

    assert objects == {
        ("submission_idempotency", "table"),
        ("outbox_events", "table"),
        ("outbox_consumptions", "table"),
        ("idx_outbox_claim", "index"),
        ("idx_outbox_edict", "index"),
    }
    assert tuple(
        row[1]
        for row in storage._conn.execute(  # noqa: SLF001 - schema contract
            "PRAGMA table_info(outbox_events)"
        )
    ) == (
        "event_id",
        "event_type",
        "aggregate_type",
        "edict_id",
        "memorial_id",
        "producer",
        "payload_json",
        "occurred_at",
        "available_at",
        "status",
        "attempt_count",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "last_error_json",
        "published_at",
        "version",
        "correlation_id",
    )


def test_v8_to_v9_upgrade_preserves_existing_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:8]) == tuple(range(1, 9))
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("legacy-edict", "preserve me", "2026-07-14T00:00:00+00:00"),
        )
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS[:9]) == (9,)

        assert connection.execute(
            "SELECT goal FROM edicts WHERE id = 'legacy-edict'"
        ).fetchone() == ("preserve me",)
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (9, "0009_durable_edict_ingress")
    finally:
        connection.close()


def test_unit_of_work_uses_the_live_connection_and_rejects_nesting(storage: Storage) -> None:
    with storage.unit_of_work() as unit_of_work, pytest.raises(RuntimeError, match="nested"):
        assert unit_of_work.connection is storage._conn  # noqa: SLF001 - identity contract
        with storage.unit_of_work():
            pass

    assert storage._conn.in_transaction is False  # noqa: SLF001 - transaction contract


def test_storage_uses_a_reentrant_lock(storage: Storage) -> None:
    assert isinstance(storage._lock, type(threading.RLock()))  # noqa: SLF001
