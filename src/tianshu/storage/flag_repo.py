"""Storage feature-flag 领域 Mixin —— 灰度开关(迭代 6「演化 2.0」)。

自研(spec P2-H:OpenFeature Python SDK 未 GA、provider 生态薄,不接)。已过门禁
未全量的进化产物挂 flag 按 cohort 灰度、秒级回退,与自重部署互补。
"""

import sqlite3
import threading


class FlagMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def get_flag(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM feature_flags WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def set_flag(
        self, key: str, *, enabled: bool, rollout_pct: int, description: str | None, updated_at: str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO feature_flags
                   (key, enabled, rollout_pct, description, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, 1 if enabled else 0, int(rollout_pct), description, updated_at),
            )

    def list_flags(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM feature_flags ORDER BY key ASC").fetchall()
        return [dict(r) for r in rows]

    def delete_flag(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM feature_flags WHERE key = ?", (key,))
