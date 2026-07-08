"""Storage 请旨奏折领域 Mixin —— 自进化「请旨解锁」(迭代 6「演化 2.0」,ADR-0004)。

自进化默认关。行为层达阈值后系统主动上一道奏折请旨,用户批红(grant)后开启行为层演化;
代码层演化风险更高,永不自动请旨。奏折 status:pending → granted / dismissed。
同一 kind 至多一条 pending,避免重复上奏(check_and_petition 幂等靠它)。
"""

import sqlite3
import threading


class PetitionMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def create_petition(
        self, *, petition_id: str, kind: str, reason: str, plan: str, created_at: str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO evolution_petitions
                   (id, kind, status, reason, plan, created_at)
                   VALUES (?, ?, 'pending', ?, ?, ?)""",
                (petition_id, kind, reason, plan, created_at),
            )

    def get_pending_petition(self, kind: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM evolution_petitions
                   WHERE kind = ? AND status = 'pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (kind,),
            ).fetchone()
        return dict(row) if row else None

    def get_petition(self, petition_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evolution_petitions WHERE id = ?", (petition_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_petitions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evolution_petitions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_petition(self, petition_id: str, status: str, resolved_at: str) -> None:
        """置为终态(granted/dismissed);只动 pending 行,重复调用幂等。"""
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE evolution_petitions SET status = ?, resolved_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (status, resolved_at, petition_id),
            )
