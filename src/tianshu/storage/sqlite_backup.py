"""Safe backup and offline restore primitives for SQLite databases."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import suppress
from pathlib import Path


class SQLiteBackupError(RuntimeError):
    """Raised when a backup is not a valid SQLite database."""


def _validate_database(path: Path) -> None:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            result = connection.execute("PRAGMA quick_check").fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise SQLiteBackupError(f"SQLite backup failed quick_check: {path}") from exc
    if not result or any(str(row[0]).lower() != "ok" for row in result):
        raise SQLiteBackupError(f"SQLite backup failed quick_check: {path}: {result!r}")


def _remove_database_files(path: Path) -> None:
    path.unlink(missing_ok=True)
    for candidate in _sidecars(path):
        candidate.unlink(missing_ok=True)


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _replace_database_file(source: Path, destination: Path) -> None:
    """Replace a database after moving stale sidecars out of SQLite's namespace."""

    sidecars = _sidecars(destination)
    for sidecar in sidecars:
        if sidecar.exists() and not sidecar.is_file():
            raise SQLiteBackupError(f"SQLite sidecar is not a regular file: {sidecar}")

    staged: list[tuple[Path, Path]] = []
    try:
        for sidecar in sidecars:
            if not sidecar.exists():
                continue
            descriptor, staging_name = tempfile.mkstemp(
                prefix=f".{sidecar.name}.", suffix=".stale", dir=destination.parent
            )
            os.close(descriptor)
            staging_path = Path(staging_name)
            try:
                os.replace(sidecar, staging_path)
            except BaseException:
                staging_path.unlink(missing_ok=True)
                raise
            staged.append((sidecar, staging_path))
    except BaseException:
        for sidecar, staging_path in reversed(staged):
            os.replace(staging_path, sidecar)
        raise

    try:
        os.replace(source, destination)
    except BaseException:
        for sidecar, staging_path in reversed(staged):
            os.replace(staging_path, sidecar)
        raise

    # The destination is now a complete standalone database and no stale sidecar
    # remains at a SQLite-recognized path.  Failure to remove a hidden stale copy
    # must not turn a successful atomic replacement into a reported failed restore.
    for _sidecar, staging_path in staged:
        with suppress(OSError):
            staging_path.unlink(missing_ok=True)


def create_online_backup(source: sqlite3.Connection, destination: Path) -> Path:
    """Back up an open SQLite connection, including committed WAL content."""
    destination = Path(destination)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        backup = sqlite3.connect(temporary_path)
        try:
            source.backup(backup)
        finally:
            backup.close()
        _validate_database(temporary_path)
        temporary_path.chmod(0o600)
        _replace_database_file(temporary_path, destination)
    finally:
        _remove_database_files(temporary_path)
    return destination


def remove_backup(path: Path) -> None:
    """Remove a completed migration's transient recovery backup and sidecars."""

    _remove_database_files(Path(path))


def restore_from_backup(backup_path: Path, target_path: Path) -> Path:
    """Restore a validated SQLite backup while the target database is offline."""
    backup_path = Path(backup_path)
    target_path = Path(target_path)
    _validate_database(backup_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copyfile(backup_path, temporary_path)
        _validate_database(temporary_path)
        temporary_path.chmod(0o600)
        _replace_database_file(temporary_path, target_path)
    finally:
        _remove_database_files(temporary_path)
    return target_path
