"""FTS5 full-text search support for memory entries."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def create_fts_table(conn: sqlite3.Connection) -> bool:
    """Create FTS5 virtual table for memory search. Returns True if created."""
    try:
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                id,
                persona_id,
                category,
                content,
                content=memory_entries,
                content_rowid=rowid
            );

            CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_entries BEGIN
                INSERT INTO memory_fts(rowid, id, persona_id, category, content)
                VALUES (new.rowid, new.id, new.persona_id, new.category, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, id, persona_id, category, content)
                VALUES ('delete', old.rowid, old.id, old.persona_id, old.category, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, id, persona_id, category, content)
                VALUES ('delete', old.rowid, old.id, old.persona_id, old.category, old.content);
                INSERT INTO memory_fts(rowid, id, persona_id, category, content)
                VALUES (new.rowid, new.id, new.persona_id, new.category, new.content);
            END;
        """)
        logger.info("FTS5 table created for memory_entries")
        return True
    except Exception:
        logger.warning("FTS5 not available, falling back to LIKE search")
        return False


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    persona_id: str | None = None,
    limit: int = 20,
    persona_ids: "list[str] | None" = None,
) -> list[str]:
    """Search memory via FTS5. Returns list of matching entry IDs.

    persona_ids 优先级高于 persona_id：
      - persona_ids = ["wym", "court", "_dept_neige"] → 限定多 ID 集合
      - persona_id = "wym"（旧用法）→ 单 ID
      - 都未提供 → 跨 persona 检索（原行为）
    """
    try:
        if persona_ids:
            placeholders = ",".join("?" for _ in persona_ids)
            rows = conn.execute(
                f"""SELECT id FROM memory_fts
                    WHERE memory_fts MATCH ? AND persona_id IN ({placeholders})
                    ORDER BY rank
                    LIMIT ?""",
                (query, *persona_ids, limit),
            ).fetchall()
        elif persona_id:
            rows = conn.execute(
                """SELECT id FROM memory_fts
                   WHERE memory_fts MATCH ? AND persona_id = ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, persona_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id FROM memory_fts
                   WHERE memory_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        logger.debug("FTS5 search failed, returning empty results")
        return []
