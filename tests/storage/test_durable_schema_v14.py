"""V14 durable execution-attempt ledger schema contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tianshu.models import Edict, Memorial
from tianshu.storage import Storage
from tianshu.storage.migration_ledger import Migration, MigrationConnection, MigrationExecutionError
from tianshu.storage.migrations import MIGRATIONS

_NOW = "2026-07-15T00:00:00+00:00"


def _seed_memorial(storage: Storage, *, memorial_id: str = "memorial-1") -> None:
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id=memorial_id, edict_id="edict-1"))


def _insert_attempt(
    connection: sqlite3.Connection,
    *,
    attempt_id: str = "attempt-1",
    memorial_id: str = "memorial-1",
    attempt_no: int = 1,
    status: str = "claimable",
    owner_id: str | None = None,
    fencing_token: int = 0,
    lease_expires_at: str | None = None,
    heartbeat_at: str | None = None,
    failure_json: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO execution_attempts (
            attempt_id, schema_version, memorial_id, attempt_no, status,
            owner_id, fencing_token, lease_expires_at, heartbeat_at,
            available_at, max_attempts, failure_json, version, created_at, updated_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 3, ?, 1, ?, ?)
        """,
        (
            attempt_id,
            memorial_id,
            attempt_no,
            status,
            owner_id,
            fencing_token,
            lease_expires_at,
            heartbeat_at,
            _NOW,
            failure_json,
            _NOW,
            _NOW,
        ),
    )


def test_v14_remains_frozen_below_live_v22_tail() -> None:
    assert tuple(item.version for item in MIGRATIONS) == tuple(range(1, 23))
    assert (MIGRATIONS[13].version, MIGRATIONS[13].name) == (
        14,
        "0014_execution_attempt_ledger",
    )


def test_v14_creates_strict_table_indexes_and_triggers(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "schema.db"))
    storage.init_db()
    try:
        table = storage._conn.execute(  # noqa: SLF001
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='execution_attempts'"
        ).fetchone()
        assert table is not None
        sql = str(table[0])
        for fragment in (
            "UNIQUE (memorial_id, attempt_no)",
            "attempt_no <= max_attempts",
            "json_valid(failure_json)",
            "status = 'claimed'",
            "fencing_token > 0",
        ):
            assert fragment in sql
        objects = {
            (row[0], row[1])
            for row in storage._conn.execute(  # noqa: SLF001
                "SELECT type, name FROM sqlite_master WHERE tbl_name='execution_attempts'"
            )
        }
        assert {
            ("index", "idx_execution_attempts_active_memorial"),
            ("index", "idx_execution_attempts_claim"),
            ("index", "idx_execution_attempts_memorial"),
            ("trigger", "execution_attempts_no_replace"),
            ("trigger", "execution_attempts_identity_immutable"),
        }.issubset(objects)
    finally:
        storage.close()


def test_v14_does_not_backfill_empty_or_existing_memorials(tmp_path: Path) -> None:
    database = tmp_path / "no-backfill.db"
    storage = Storage(str(database))
    storage.init_db()
    _seed_memorial(storage)
    storage.close()

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone() == (0,)
        assert connection.execute(
            "SELECT attempt FROM memorials WHERE id='memorial-1'"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_v14_foreign_key_cascade_and_partial_active_uniqueness(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "constraints.db"))
    storage.init_db()
    _seed_memorial(storage)
    connection = storage._conn  # noqa: SLF001
    try:
        _insert_attempt(connection)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _insert_attempt(connection, attempt_id="unknown", memorial_id="missing")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert_attempt(connection, attempt_id="attempt-2", attempt_no=2, status="suspended")
        connection.execute(
            "UPDATE execution_attempts SET status='succeeded' WHERE attempt_id='attempt-1'"
        )
        _insert_attempt(connection, attempt_id="attempt-2", attempt_no=2)
        connection.execute("DELETE FROM memorials WHERE id='memorial-1'")
        assert connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0
    finally:
        storage.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_id": " "},
        {"status": "claimed"},
        {"status": "claimable", "owner_id": "worker"},
        {"status": "failed", "failure_json": None},
        {"status": "succeeded", "failure_json": '{"code":"x"}'},
    ],
)
def test_v14_rejects_invalid_attempt_shapes(tmp_path: Path, overrides: dict[str, object]) -> None:
    storage = Storage(str(tmp_path / "invalid.db"))
    storage.init_db()
    _seed_memorial(storage)
    values: dict[str, object] = {
        "attempt_id": "attempt-1",
        "status": "claimable",
        "owner_id": None,
        "fencing_token": 0,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "failure_json": None,
    }
    values.update(overrides)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(storage._conn, **values)  # type: ignore[arg-type]  # noqa: SLF001
    finally:
        storage.close()


def test_v14_blocks_replace_and_identity_updates(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "immutable.db"))
    storage.init_db()
    _seed_memorial(storage)
    connection = storage._conn  # noqa: SLF001
    try:
        _insert_attempt(connection)
        with pytest.raises(sqlite3.IntegrityError, match="replacement"):
            connection.execute(
                "INSERT OR REPLACE INTO execution_attempts SELECT * FROM execution_attempts"
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            connection.execute(
                "UPDATE execution_attempts SET max_attempts=4 WHERE attempt_id='attempt-1'"
            )
    finally:
        storage.close()


def test_v14_callback_failure_rolls_back_schema_and_ledger(tmp_path: Path) -> None:
    database = tmp_path / "rollback.db"
    storage = Storage(str(database))
    storage.init_db()
    storage.close()
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TABLE execution_attempts")
        connection.execute("DELETE FROM schema_migrations WHERE version>=14")
        connection.commit()
        migration = MIGRATIONS[13]

        def fail_after_upgrade(active: MigrationConnection) -> None:
            migration.upgrade(active)
            raise RuntimeError("stop after v14")

        failing = Migration(
            version=14,
            name=migration.name,
            checksum=migration.checksum,
            upgrade=fail_after_upgrade,
        )
        from tianshu.storage.migration_ledger import apply_migrations

        with pytest.raises(MigrationExecutionError, match=migration.name):
            apply_migrations(connection, (*MIGRATIONS[:13], failing))
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='execution_attempts'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (13,)
    finally:
        connection.close()
