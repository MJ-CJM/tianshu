"""Storage Memory 领域 Mixin —— 记忆条目 CRUD 与检索（含 FTS5 惰性回退）。"""

import json
import sqlite3
import threading

from tianshu.storage.mappers import _row_to_memory_entry


class MemoryMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Memory ---

    def save_memory_entry(self, entry) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO memory_entries
                   (id, persona_id, edict_id, memorial_id, category, content,
                    source, confidence, entity_refs_json, created_at, expires_at, access_level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.persona_id,
                    entry.edict_id,
                    entry.memorial_id,
                    entry.category,
                    entry.content,
                    entry.source,
                    entry.confidence,
                    json.dumps(entry.entity_refs),
                    entry.created_at.isoformat(),
                    entry.expires_at.isoformat() if entry.expires_at else None,
                    entry.access_level,
                ),
            )

    def search_memory(
        self,
        persona_id: str,
        query: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_shared: bool = False,
    ) -> list:
        # Try FTS5 first if available and query is provided
        if query and getattr(self, "_fts_available", False):
            from tianshu.memory.fts import fts_search

            with self._lock:
                fts_ids = fts_search(self._conn, query, persona_id=persona_id, limit=limit)
            if fts_ids:
                placeholders = ",".join("?" for _ in fts_ids)
                extra_conditions = []
                extra_params: list = list(fts_ids)
                if include_shared:
                    extra_conditions.append(
                        "(persona_id = ? OR access_level IN ('shared', 'court'))"
                    )
                    extra_params.append(persona_id)
                if category:
                    extra_conditions.append("category = ?")
                    extra_params.append(category)
                where_extra = f" AND {' AND '.join(extra_conditions)}" if extra_conditions else ""
                with self._lock:
                    rows = self._conn.execute(
                        f"SELECT * FROM memory_entries WHERE id IN ({placeholders}){where_extra} ORDER BY created_at DESC LIMIT ?",
                        (*extra_params, limit),
                    ).fetchall()
                return [_row_to_memory_entry(r) for r in rows]

        # Fallback to LIKE search
        conditions = []
        params: list = []

        if include_shared:
            conditions.append("(persona_id = ? OR access_level IN ('shared', 'court'))")
            params.append(persona_id)
        else:
            conditions.append("persona_id = ?")
            params.append(persona_id)

        if category:
            conditions.append("category = ?")
            params.append(category)
        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memory_entries{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_row_to_memory_entry(r) for r in rows]

    def list_memory_by_persona(self, persona_id: str, limit: int = 50) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_entries WHERE persona_id = ? ORDER BY created_at DESC LIMIT ?",
                (persona_id, limit),
            ).fetchall()
        return [_row_to_memory_entry(r) for r in rows]

    def delete_memory_entry(self, entry_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
            return cursor.rowcount > 0

    def delete_memory_entries_batch(self, entry_ids: list[str]) -> int:
        """Delete multiple memory entries by ID. Returns count of deleted rows."""
        if not entry_ids:
            return 0
        placeholders = ",".join("?" for _ in entry_ids)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"DELETE FROM memory_entries WHERE id IN ({placeholders})",
                entry_ids,
            )
            return cursor.rowcount
