"""SQLite storage layer - system truth source."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from ulid import ULID

from tianshu.models import Edict, EdictStatus, Memorial, TaskStatus, UsageSummary


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def init_db(self) -> None:
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._migrate()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS edicts (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    context TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memorials (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL REFERENCES edicts(id),
                    status TEXT NOT NULL,
                    summary TEXT,
                    result TEXT,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL,
                    memorial_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memorials_edict_id
                    ON memorials(edict_id);
                CREATE INDEX IF NOT EXISTS idx_events_edict_id
                    ON events(edict_id);
            """)

    def _migrate(self) -> None:
        migrations = [
            "ALTER TABLE edicts ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
            "ALTER TABLE memorials ADD COLUMN instruction TEXT",
        ]
        for sql in migrations:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Edict ---

    def save_edict(self, edict: Edict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO edicts (id, goal, context, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (edict.id, edict.goal, edict.context, edict.status.value, edict.created_at.isoformat()),
            )

    def get_edict(self, edict_id: str) -> Edict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM edicts WHERE id = ?", (edict_id,)
            ).fetchone()
        return self._row_to_edict(row) if row else None

    def list_edicts(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Edict], int]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM edicts WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM edicts WHERE status = ?",
                    (status,),
                ).fetchone()[0]
            else:
                rows = self._conn.execute(
                    "SELECT * FROM edicts ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = self._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0]
        return [self._row_to_edict(r) for r in rows], total

    def update_edict_status(self, edict_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE edicts SET status = ? WHERE id = ?",
                (status, edict_id),
            )

    # --- Memorial ---

    def save_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO memorials
                   (id, edict_id, instruction, status, summary, result, usage_json,
                    error, created_at, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._memorial_to_params(memorial),
            )

    def update_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE memorials SET
                   status=?, summary=?, result=?, usage_json=?, error=?,
                   started_at=?, completed_at=?
                   WHERE id=?""",
                (
                    memorial.status.value,
                    memorial.summary,
                    memorial.result,
                    memorial.usage.model_dump_json(),
                    memorial.error,
                    memorial.started_at.isoformat() if memorial.started_at else None,
                    memorial.completed_at.isoformat() if memorial.completed_at else None,
                    memorial.id,
                ),
            )

    def get_memorial(self, memorial_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE id = ?", (memorial_id,)
            ).fetchone()
        return self._row_to_memorial(row) if row else None

    def get_memorial_by_edict(self, edict_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
        return self._row_to_memorial(row) if row else None

    def list_memorials_by_edict(self, edict_id: str) -> list[Memorial]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [self._row_to_memorial(r) for r in rows]

    def list_memorials(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Memorial], int]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM memorials WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM memorials WHERE status = ?", (status,)
                ).fetchone()[0]
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memorials ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = self._conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0]
        return [self._row_to_memorial(r) for r in rows], total

    # --- Events ---

    def append_event(
        self,
        edict_id: str,
        memorial_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO events
                   (id, edict_id, memorial_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(ULID()),
                    edict_id,
                    memorial_id,
                    event_type,
                    json.dumps(payload, default=str),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_events(self, edict_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "edict_id": r["edict_id"],
                "memorial_id": r["memorial_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # --- Helpers ---

    @staticmethod
    def _row_to_edict(row: sqlite3.Row) -> Edict:
        return Edict(
            id=row["id"],
            goal=row["goal"],
            context=row["context"],
            status=EdictStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_memorial(row: sqlite3.Row) -> Memorial:
        usage_data = json.loads(row["usage_json"]) if row["usage_json"] else {}
        return Memorial(
            id=row["id"],
            edict_id=row["edict_id"],
            instruction=row["instruction"],
            status=TaskStatus(row["status"]),
            summary=row["summary"],
            result=row["result"],
            usage=UsageSummary(**usage_data),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )

    @staticmethod
    def _memorial_to_params(m: Memorial) -> tuple:
        return (
            m.id,
            m.edict_id,
            m.instruction,
            m.status.value,
            m.summary,
            m.result,
            m.usage.model_dump_json(),
            m.error,
            m.created_at.isoformat(),
            m.started_at.isoformat() if m.started_at else None,
            m.completed_at.isoformat() if m.completed_at else None,
        )
