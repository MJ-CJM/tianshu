"""Storage 连接生命周期基类 —— 建库/建表/迁移/关闭（_StorageBase，供领域 Mixin 共享）。"""

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.storage.migrations import run_migrations
from tianshu.storage.schema import (
    SCHEMA_SQL_CHANNELS,
    SCHEMA_SQL_CORE,
    SCHEMA_SQL_FEISHU,
    SCHEMA_SQL_TELEGRAM,
)


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
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._migrate()
        self._init_fts()

    def _init_fts(self) -> None:
        """Initialize FTS5 full-text search for memory entries."""
        from tianshu.memory.fts import create_fts_table

        self._fts_available = create_fts_table(self._conn)

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.executescript(SCHEMA_SQL_CORE)
            self._conn.executescript(SCHEMA_SQL_FEISHU)
            self._conn.executescript(SCHEMA_SQL_TELEGRAM)
            self._conn.executescript(SCHEMA_SQL_CHANNELS)

    def _migrate(self) -> None:
        run_migrations(self._conn)

        # 2026-06-04: 多 bot 实例 —— 会话/审批表加 instance_id 维度（幂等）
        self._migrate_session_tables_add_instance()

        # Seed departments from existing personas (one-time)
        self._seed_departments()

    def _migrate_session_tables_add_instance(self) -> None:
        """为存量 DB 的飞书/telegram 会话表补 instance_id 维度（幂等）。

        - anchor 表：PK 改为 (instance_id, chat_id)，存量行回填 '<channel>-default'。
        - pending/seen 表：ALTER ADD COLUMN instance_id（带 default）。
        - thinking 表不动（memorial_id 全局唯一）。
        """
        cols = {
            r[1]
            for r in self._conn.execute("PRAGMA table_info(telegram_session_anchor)").fetchall()
        }
        if "instance_id" in cols:
            return  # 已迁移

        # anchor 表：SQLite 不支持改 PK，需重建 + 拷贝。
        for channel in ("feishu", "telegram"):
            default_iid = f"{channel}-default"
            table = f"{channel}_session_anchor"
            self._conn.executescript(
                f"""
                CREATE TABLE {table}_new (
                    instance_id      TEXT NOT NULL,
                    chat_id          TEXT NOT NULL,
                    current_edict_id TEXT,
                    updated_at       TIMESTAMP NOT NULL,
                    PRIMARY KEY (instance_id, chat_id)
                );
                INSERT INTO {table}_new (instance_id, chat_id, current_edict_id, updated_at)
                    SELECT '{default_iid}', chat_id, current_edict_id, updated_at FROM {table};
                DROP TABLE {table};
                ALTER TABLE {table}_new RENAME TO {table};
                """
            )

        # pending / seen 表：ALTER ADD COLUMN（带 default，存量行自动回填）。
        for sql in (
            "ALTER TABLE feishu_pending_cards ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'feishu-default'",
            "ALTER TABLE telegram_pending_buttons ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'telegram-default'",
            "ALTER TABLE feishu_seen_messages ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'feishu-default'",
            "ALTER TABLE telegram_seen_messages ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'telegram-default'",
        ):
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]  # TODO(治理): 同上，close() 后瞬时置空
