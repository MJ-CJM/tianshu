from __future__ import annotations

import sqlite3
import stat
import threading
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

import tianshu.storage as storage_package
import tianshu.storage._base as storage_base
from tianshu.cli.commands import secrets as secrets_command
from tianshu.cli.commands.secrets import app as secrets_app
from tianshu.storage import Storage, sqlite_backup
from tianshu.storage.migration_ledger import MigrationExecutionError
from tianshu.storage.migrations import MIGRATIONS
from tianshu.storage.sqlite_backup import (
    SQLiteBackupError,
    create_online_backup,
    restore_from_backup,
)


def _read_values(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as connection:
        return [row[0] for row in connection.execute("SELECT value FROM records")]


def _read_values_immutable(path: Path) -> list[str]:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return [row[0] for row in connection.execute("SELECT value FROM records")]


def _write_database(path: Path, *values: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.executemany("INSERT INTO records VALUES (?)", [(value,) for value in values])
        connection.commit()


def _create_preledger_database(path: Path) -> None:
    storage = Storage(str(path))
    storage.init_db()
    storage._conn.executescript(
        """
        CREATE TABLE backup_sentinel (value TEXT NOT NULL);
        INSERT INTO backup_sentinel VALUES ('before-migration');
        DROP TABLE schema_migrations;
        """
    )
    storage.close()


def _migration_backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.pre-migration-*.bak"))


def _insert_credential(path: Path, key: str) -> bytes:
    encrypted = Fernet(key.encode()).encrypt(b"secret-before-rotation")
    storage = Storage(str(path))
    storage.init_db()
    storage.insert_credential(
        cred_id="credential-1",
        name="credential",
        host_pattern="example.com",
        header_template="Authorization: Bearer {value}",
        extra_headers_json="{}",
        encrypted_value=encrypted,
        now_iso=datetime.now(UTC).isoformat(),
    )
    storage.close()
    return encrypted


def _tracking_storage_class(instances: list[Storage]) -> type[Storage]:
    class TrackingStorage(Storage):
        def __init__(self, db_path: str) -> None:
            super().__init__(db_path)
            instances.append(self)

    return TrackingStorage


def test_online_backup_includes_committed_wal_data(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backups" / "source.sqlite3.bak"
    backup_path.parent.mkdir()

    source = sqlite3.connect(source_path)
    try:
        assert source.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE records (value TEXT NOT NULL)")
        source.execute("INSERT INTO records VALUES ('committed-in-wal')")
        source.commit()
        assert source_path.with_name(f"{source_path.name}-wal").exists()

        result = create_online_backup(source, backup_path)
    finally:
        source.close()

    assert result == backup_path
    with closing(sqlite3.connect(backup_path)) as backup:
        rows = backup.execute("SELECT value FROM records").fetchall()
    assert rows == [("committed-in-wal",)]


def test_online_backup_is_private_and_atomically_replaces_destination(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    destination = tmp_path / "source.sqlite3.bak"
    _write_database(source_path, "new")
    _write_database(destination, "old")
    destination.chmod(0o644)

    with closing(sqlite3.connect(source_path)) as source:
        create_online_backup(source, destination)

    assert _read_values(destination) == ["new"]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert set(tmp_path.iterdir()) == {destination, source_path}


class _CorruptingBackupConnection(sqlite3.Connection):
    def backup(self, target: sqlite3.Connection, **kwargs: object) -> None:
        super().backup(target, **kwargs)
        target.execute("PRAGMA writable_schema=ON")
        target.execute("UPDATE sqlite_schema SET rootpage=999999 WHERE name='records'")
        target.commit()


def test_invalid_online_backup_does_not_replace_existing_destination(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    destination = tmp_path / "source.sqlite3.bak"
    _write_database(destination, "old")
    source = sqlite3.connect(source_path, factory=_CorruptingBackupConnection)
    try:
        source.execute("CREATE TABLE records (value TEXT NOT NULL)")
        source.execute("INSERT INTO records VALUES ('new')")
        source.commit()

        with pytest.raises(SQLiteBackupError, match="quick_check"):
            create_online_backup(source, destination)
    finally:
        source.close()

    assert _read_values(destination) == ["old"]
    assert set(tmp_path.iterdir()) == {destination, source_path}


def test_failed_online_backup_removes_temporary_file(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    destination = tmp_path / "source.sqlite3.bak"
    _write_database(destination, "old")
    source = sqlite3.connect(source_path)
    source.close()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        create_online_backup(source, destination)

    assert _read_values(destination) == ["old"]
    assert set(tmp_path.iterdir()) == {destination, source_path}


def test_backup_mutate_and_offline_restore_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "source.sqlite3.bak"
    _write_database(database_path, "before")
    with closing(sqlite3.connect(database_path)) as source:
        create_online_backup(source, backup_path)
    with closing(sqlite3.connect(database_path)) as database:
        database.execute("DELETE FROM records")
        database.execute("INSERT INTO records VALUES ('after')")
        database.commit()

    result = restore_from_backup(backup_path, database_path)

    assert result == database_path
    assert _read_values(database_path) == ["before"]
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_corrupt_backup_is_rejected_without_changing_target(tmp_path: Path) -> None:
    backup_path = tmp_path / "corrupt.sqlite3.bak"
    target_path = tmp_path / "target.sqlite3"
    backup_path.write_bytes(b"not a sqlite database")
    _write_database(target_path, "keep-me")

    with pytest.raises(SQLiteBackupError, match="quick_check"):
        restore_from_backup(backup_path, target_path)

    assert _read_values(target_path) == ["keep-me"]
    assert set(tmp_path.iterdir()) == {backup_path, target_path}


def test_restore_copy_failure_cleans_temp_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_path = tmp_path / "valid.sqlite3.bak"
    target_path = tmp_path / "target.sqlite3"
    _write_database(backup_path, "restore-me")
    _write_database(target_path, "keep-me")

    def fail_after_partial_copy(source: Path, destination: Path) -> None:
        del source
        Path(destination).write_bytes(b"partial")
        raise OSError("simulated disk full")

    monkeypatch.setattr(sqlite_backup.shutil, "copyfile", fail_after_partial_copy)

    with pytest.raises(OSError, match="disk full"):
        restore_from_backup(backup_path, target_path)

    assert _read_values(target_path) == ["keep-me"]
    assert set(tmp_path.iterdir()) == {backup_path, target_path}


def test_successful_restore_removes_stale_target_wal_and_shm(tmp_path: Path) -> None:
    backup_path = tmp_path / "valid.sqlite3.bak"
    target_path = tmp_path / "target.sqlite3"
    wal_path = Path(f"{target_path}-wal")
    shm_path = Path(f"{target_path}-shm")
    _write_database(backup_path, "restore-me")
    _write_database(target_path, "replace-me")
    wal_path.write_bytes(b"stale wal")
    shm_path.write_bytes(b"stale shm")

    restore_from_backup(backup_path, target_path)

    assert _read_values(target_path) == ["restore-me"]
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_restore_sidecar_preflight_failure_preserves_target(tmp_path: Path) -> None:
    backup_path = tmp_path / "valid.sqlite3.bak"
    target_path = tmp_path / "target.sqlite3"
    wal_path = Path(f"{target_path}-wal")
    _write_database(backup_path, "restore-me")
    _write_database(target_path, "keep-me")
    wal_path.mkdir()

    with pytest.raises(SQLiteBackupError, match="sidecar"):
        restore_from_backup(backup_path, target_path)

    assert _read_values_immutable(target_path) == ["keep-me"]
    assert wal_path.is_dir()


def test_storage_only_backs_up_existing_database_with_pending_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "storage.sqlite3"

    fresh = Storage(str(database_path))
    fresh.init_db()
    fresh.close()
    assert _migration_backups(database_path) == []

    current = Storage(str(database_path))
    current.init_db()
    current.close()
    assert _migration_backups(database_path) == []

    memory = Storage(":memory:")
    memory.init_db()
    memory.close()
    assert _migration_backups(database_path) == []


def test_successful_migration_removes_pre_migration_recovery_backup(tmp_path: Path) -> None:
    database_path = tmp_path / "storage.sqlite3"
    _create_preledger_database(database_path)

    storage = Storage(str(database_path))
    storage.init_db()
    storage.close()

    assert _migration_backups(database_path) == []

    reopened = Storage(str(database_path))
    reopened.init_db()
    reopened.close()
    assert _migration_backups(database_path) == []


def test_concurrent_storage_startup_creates_one_true_pre_migration_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "storage.sqlite3"
    _create_preledger_database(database_path)
    real_pending = storage_base.pending_migrations
    both_read_pending = threading.Barrier(2)

    def synchronized_pending(
        connection: sqlite3.Connection, migrations: object
    ) -> tuple[object, ...]:
        pending = real_pending(connection, migrations)  # type: ignore[arg-type]
        if pending:
            with suppress(threading.BrokenBarrierError):
                both_read_pending.wait(timeout=0.25)
        return pending

    monkeypatch.setattr(storage_base, "pending_migrations", synchronized_pending)
    errors: list[BaseException] = []

    def initialize() -> None:
        storage = Storage(str(database_path))
        try:
            storage.init_db()
        except BaseException as exc:  # pragma: no cover - assertion reports the exception
            errors.append(exc)
        finally:
            storage.close()

    threads = [threading.Thread(target=initialize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert _migration_backups(database_path) == []
    with closing(sqlite3.connect(database_path)) as current:
        assert current.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == [(migration.version, migration.name) for migration in MIGRATIONS]


def test_backup_failure_stops_migration_without_changing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "storage.sqlite3"
    _create_preledger_database(database_path)

    def fail_backup(source: sqlite3.Connection, destination: Path) -> Path:
        del source, destination
        raise OSError("backup disk unavailable")

    monkeypatch.setattr(storage_base, "create_online_backup", fail_backup, raising=False)
    storage = Storage(str(database_path))

    with pytest.raises(OSError, match="backup disk unavailable"):
        storage.init_db()

    assert storage._conn is None
    assert _migration_backups(database_path) == []
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("SELECT value FROM backup_sentinel").fetchone() == (
            "before-migration",
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            is None
        )


def test_migration_failure_reports_unique_backup_and_backup_can_restore(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "storage.sqlite3"
    _create_preledger_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("CREATE TABLE _supervision_reports_new (sentinel TEXT)")
        connection.commit()

    reported_paths: list[Path] = []
    for _ in range(2):
        storage = Storage(str(database_path))
        with pytest.raises(MigrationExecutionError, match="0001_adopt_v042_baseline") as error:
            storage.init_db()
        assert storage._conn is None
        backup_path = Path(error.value.backup_path)  # type: ignore[attr-defined]
        assert backup_path.exists()
        assert backup_path.parent == database_path.parent
        assert str(backup_path) in "\n".join(error.value.__notes__)
        reported_paths.append(backup_path)

    assert reported_paths[0] == reported_paths[1]
    assert _migration_backups(database_path) == [reported_paths[0]]
    assert "legacy-sensitive" in reported_paths[0].name
    assert stat.S_IMODE(reported_paths[0].stat().st_mode) == 0o600

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("CREATE TABLE post_failure_mutation (value TEXT)")
        connection.commit()
    restore_from_backup(reported_paths[0], database_path)
    with closing(sqlite3.connect(database_path)) as restored:
        tables = {
            str(row[0])
            for row in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "_supervision_reports_new" in tables
        assert "post_failure_mutation" not in tables
        assert "schema_migrations" not in tables


def test_rotate_master_key_uses_online_backup_and_closes_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "secrets.sqlite3"
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_ciphertext = _insert_credential(database_path, old_key)
    instances: list[Storage] = []
    backup_calls: list[tuple[sqlite3.Connection, Path]] = []
    real_backup = sqlite_backup.create_online_backup
    expected_backup = tmp_path / "explicit-unique-rotation.bak"

    def tracking_backup(source: sqlite3.Connection, destination: Path) -> Path:
        backup_calls.append((source, destination))
        return real_backup(source, destination)

    monkeypatch.setattr(storage_package, "Storage", _tracking_storage_class(instances))
    monkeypatch.setattr(sqlite_backup, "create_online_backup", tracking_backup)
    monkeypatch.setattr(
        secrets_command,
        "_new_rotation_backup_path",
        lambda _path: expected_backup,
        raising=False,
    )
    monkeypatch.setenv("TIANSHU_DB_PATH", str(database_path))

    result = CliRunner().invoke(
        secrets_app,
        ["rotate-master-key", "--new-key", new_key, "--old-key", old_key, "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert len(backup_calls) == 1
    assert len(instances) == 1
    assert instances[0]._conn is None
    backup_path = backup_calls[0][1]
    assert backup_path == expected_backup
    assert backup_path.exists()
    with closing(sqlite3.connect(backup_path)) as backup:
        assert (
            backup.execute(
                "SELECT encrypted_value FROM network_credentials WHERE id='credential-1'"
            ).fetchone()[0]
            == old_ciphertext
        )


def test_rotation_backup_paths_do_not_collide_at_the_same_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2026, 7, 11, 5, 6, 7, 890123, tzinfo=UTC)

    class FixedDateTime:
        @classmethod
        def now(cls, _timezone: object) -> datetime:
            return fixed_now

    monkeypatch.setattr(secrets_command, "datetime", FixedDateTime, raising=False)
    database_path = tmp_path / "secrets.sqlite3"

    first = secrets_command._new_rotation_backup_path(database_path)
    second = secrets_command._new_rotation_backup_path(database_path)

    assert first != second
    assert first.parent == database_path.parent
    assert second.parent == database_path.parent
    assert "20260711T050607.890123Z" in first.name
    assert first.suffix == second.suffix == ".bak"


def test_rotate_master_key_closes_storage_on_no_credentials_early_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "secrets.sqlite3"
    storage = Storage(str(database_path))
    storage.init_db()
    storage.close()
    instances: list[Storage] = []
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(storage_package, "Storage", _tracking_storage_class(instances))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(database_path))

    result = CliRunner().invoke(
        secrets_app,
        ["rotate-master-key", "--new-key", new_key, "--old-key", old_key, "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "库中无凭证" in result.output
    assert len(instances) == 1
    assert instances[0]._conn is None
