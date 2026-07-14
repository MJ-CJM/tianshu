"""Storage 连接生命周期基类 —— 建库/建表/迁移/关闭（_StorageBase，供领域 Mixin 共享）。"""

import fcntl
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.storage.migration_ledger import Migration, MigrationError, pending_migrations
from tianshu.storage.migrations import MIGRATIONS, run_migrations
from tianshu.storage.sqlite_backup import create_online_backup, remove_backup

_SENSITIVE_MIGRATION_NAMES = frozenset({"0008_encrypt_mcp_secret_mappings"})


class SensitiveMigrationWALCheckpointError(RuntimeError):
    """Raised when plaintext WAL frames cannot be truncated before a migration."""


def _has_sensitive_migration(pending: tuple[Migration, ...]) -> bool:
    return any(migration.name in _SENSITIVE_MIGRATION_NAMES for migration in pending)


def _truncate_sensitive_migration_wal(conn: sqlite3.Connection) -> None:
    result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result is None or int(result[0]) != 0:
        raise SensitiveMigrationWALCheckpointError("sensitive migration WAL checkpoint is busy")


def _new_migration_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.pre-migration-recovery.legacy-sensitive.bak")


@contextmanager
def _migration_startup_lock(path: Path) -> Iterator[None]:
    """Serialize one database's pending-check, backup, and migration across processes."""

    lock_path = path.with_name(f".{path.name}.migration.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class _StorageBase:
    # 类型声明供 Mixin 共享（标准 Mixin 模式）
    _conn: sqlite3.Connection
    _lock: threading.Lock
    _db_path: str
    _fts_available: bool

    if TYPE_CHECKING:
        # 仅供类型检查器可见：真实实现在 PersonaMixin（persona_repo.py），
        # 通过 Storage 的多重继承在运行时解析；此处声明避免遮蔽 MRO 解析。
        def _seed_departments(self) -> None: ...

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._lock = threading.Lock()
        # _conn 对外（Mixin）声明为非 Optional 供跨文件复用；此处仅是 init_db() 前的瞬时占位。
        self._conn = None  # type: ignore[assignment]  # TODO(治理): 全面 Optional 化需级联标注所有 15 个 Mixin，超出本次任务范围

    def init_db(self) -> None:
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_memory = self._db_path == ":memory:"
        startup_lock = nullcontext() if is_memory else _migration_startup_lock(path)
        with startup_lock:
            existing_disk_database = not is_memory and path.is_file()
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn = conn
            backup_path: Path | None = None
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                pending = pending_migrations(conn, MIGRATIONS)
                sensitive_pending = _has_sensitive_migration(pending)
                sensitive_backup_path = (
                    _new_migration_backup_path(path) if existing_disk_database else None
                )
                sensitive_cleanup_required = (
                    sensitive_backup_path is not None and sensitive_backup_path.exists()
                )
                if existing_disk_database and sensitive_pending:
                    _truncate_sensitive_migration_wal(conn)
                if existing_disk_database and pending:
                    assert sensitive_backup_path is not None
                    backup_path = sensitive_backup_path
                    create_online_backup(conn, backup_path)
                try:
                    run_migrations(conn)
                except MigrationError as exc:
                    if backup_path is not None:
                        exc.backup_path = backup_path  # type: ignore[attr-defined]
                        exc.add_note(f"pre-migration backup: {backup_path}")
                    raise
                if existing_disk_database and (sensitive_pending or sensitive_cleanup_required):
                    assert sensitive_backup_path is not None
                    _truncate_sensitive_migration_wal(conn)
                    remove_backup(sensitive_backup_path)
                elif backup_path is not None:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    remove_backup(backup_path)
                conn.execute("PRAGMA journal_mode=WAL")
                self._seed_departments()
                self._init_fts()
            except BaseException:
                conn.close()
                self._conn = None  # type: ignore[assignment]  # see __init__ lifecycle note
                raise

    def _init_fts(self) -> None:
        """Initialize FTS5 full-text search for memory entries."""
        from tianshu.memory.fts import create_fts_table

        self._fts_available = create_fts_table(self._conn)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]  # TODO(治理): 同上，close() 后瞬时置空
