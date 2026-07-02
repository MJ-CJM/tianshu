"""DrawerStore — SQLite-backed drawer storage with FTS5 BM25 search."""

from __future__ import annotations

import contextlib
import math
import sqlite3
import threading
from datetime import UTC, datetime

from tianshu.memory.drawer import Drawer, DrawerResult


def _escape_fts5_query(query: str) -> str:
    """Escape user input for FTS5 MATCH to avoid syntax errors on special chars.

    Splits on whitespace and wraps each non-empty token as a phrase ("..."),
    doubling any internal quote. Tokens become phrases joined with implicit AND,
    which also covers inputs like "C++", "foo(bar)", and `hello"world`.
    """
    tokens = [t for t in query.split() if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class DrawerStore:
    """Default MemoryBackend implementation using SQLite + FTS5."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS drawers (
                id TEXT PRIMARY KEY,
                wing TEXT NOT NULL,
                room TEXT NOT NULL,
                content TEXT NOT NULL,
                source_edict_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'W',
                confidence REAL NOT NULL DEFAULT 1.0,
                chunk_index INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_drawers_wing ON drawers(wing);
            CREATE INDEX IF NOT EXISTS idx_drawers_wing_room ON drawers(wing, room);
        """)
        # FTS5 virtual table for BM25 search
        with contextlib.suppress(sqlite3.OperationalError):  # FTS5 not available
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts
                USING fts5(id, wing, room, content, tokenize='unicode61');
            """)
        self._conn.commit()

    async def store_drawer(self, drawer: Drawer) -> str:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO drawers
                   (id, wing, room, content, source_edict_id, timestamp, category, confidence, chunk_index)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    drawer.id,
                    drawer.wing,
                    drawer.room,
                    drawer.content,
                    drawer.source_edict_id,
                    drawer.timestamp,
                    drawer.category,
                    drawer.confidence,
                    drawer.chunk_index,
                ),
            )
            # Sync to FTS
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute(
                    "INSERT OR REPLACE INTO drawers_fts (id, wing, room, content) VALUES (?, ?, ?, ?)",
                    (drawer.id, drawer.wing, drawer.room, drawer.content),
                )
            self._conn.commit()
        return drawer.id

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 10,
    ) -> list[DrawerResult]:
        # Try FTS5 BM25 first
        try:
            return self._fts5_search(query, wing, room, n_results)
        except sqlite3.OperationalError:
            return self._fallback_search(query, wing, room, n_results)

    def _fts5_search(
        self,
        query: str,
        wing: str | None,
        room: str | None,
        n: int,
    ) -> list[DrawerResult]:
        fts_query = _escape_fts5_query(query)
        if not fts_query:
            return []
        where_parts: list[str] = []
        params: list[str] = [fts_query]

        if wing:
            where_parts.append("d.wing = ?")
            params.append(wing)
        if room:
            where_parts.append("d.room = ?")
            params.append(room)

        where_clause = ""
        if where_parts:
            where_clause = "AND " + " AND ".join(where_parts)

        sql = f"""
            SELECT d.id, d.content, d.wing, d.room,
                   rank AS fts_rank
            FROM drawers_fts f
            JOIN drawers d ON d.id = f.id
            WHERE drawers_fts MATCH ? {where_clause}
            ORDER BY rank
            LIMIT ?
        """
        params.append(str(n))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        results: list[DrawerResult] = []
        for row in rows:
            raw_rank = abs(row["fts_rank"]) if row["fts_rank"] else 0
            score = 1.0 / (1.0 + raw_rank) if raw_rank else 0.5
            results.append(
                DrawerResult(
                    drawer_id=row["id"],
                    content=row["content"],
                    wing=row["wing"],
                    room=row["room"],
                    score=score,
                    matched_via="bm25",
                )
            )
        return results

    def _fallback_search(
        self,
        query: str,
        wing: str | None,
        room: str | None,
        n: int,
    ) -> list[DrawerResult]:
        where_parts = ["content LIKE ?"]
        params: list[str] = [f"%{query}%"]
        if wing:
            where_parts.append("wing = ?")
            params.append(wing)
        if room:
            where_parts.append("room = ?")
            params.append(room)

        sql = f"""
            SELECT id, content, wing, room
            FROM drawers
            WHERE {" AND ".join(where_parts)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(str(n))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            DrawerResult(
                drawer_id=row["id"],
                content=row["content"],
                wing=row["wing"],
                room=row["room"],
                score=0.5,
                matched_via="fallback",
            )
            for row in rows
        ]

    async def get_drawers(
        self,
        wing: str,
        room: str | None = None,
        limit: int = 100,
    ) -> list[Drawer]:
        with self._lock:
            if room:
                rows = self._conn.execute(
                    "SELECT * FROM drawers WHERE wing = ? AND room = ? ORDER BY timestamp DESC LIMIT ?",
                    (wing, room, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM drawers WHERE wing = ? ORDER BY timestamp DESC LIMIT ?",
                    (wing, limit),
                ).fetchall()
        return [self._row_to_drawer(r) for r in rows]

    async def delete_drawer(self, drawer_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM drawers WHERE id = ?", (drawer_id,))
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("DELETE FROM drawers_fts WHERE id = ?", (drawer_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def get_l1(self, wing: str, max_chars: int = 3200) -> str:
        """Generate L1 critical facts for a wing.

        Scores drawers by confidence x recency_decay, picks Top-K, groups by room.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM drawers WHERE wing = ? ORDER BY timestamp DESC LIMIT 2000",
                (wing,),
            ).fetchall()

        if not rows:
            return ""

        now = datetime.now(UTC)
        scored: list[tuple[float, dict]] = []
        for row in rows:
            drawer = dict(row)
            ts = datetime.fromisoformat(drawer["timestamp"])
            age_days = (now - ts).total_seconds() / 86400
            recency = math.exp(-0.693 * age_days / 30)  # half-life = 30 days
            score = drawer["confidence"] * recency
            scored.append((score, drawer))

        scored.sort(key=lambda x: -x[0])
        top = scored[:15]

        # Group by room
        by_room: dict[str, list[str]] = {}
        total = 0
        for _, d in top:
            room = d["room"]
            line = f"  - {d['content'][:200]}"
            if total + len(line) > max_chars:
                break
            by_room.setdefault(room, []).append(line)
            total += len(line) + 1

        if not by_room:
            return ""

        parts = [f"## L1 — 关键事实 ({wing})\n"]
        for room, lines in by_room.items():
            parts.append(f"[{room}]")
            parts.extend(lines)
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _row_to_drawer(row: sqlite3.Row) -> Drawer:
        return Drawer(
            id=row["id"],
            wing=row["wing"],
            room=row["room"],
            content=row["content"],
            source_edict_id=row["source_edict_id"],
            timestamp=row["timestamp"],
            category=row["category"],
            confidence=row["confidence"],
            chunk_index=row["chunk_index"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
