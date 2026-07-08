"""Storage 锦衣卫领域 Mixin —— 急停单行状态(迭代 3「深防御」)。"""

import sqlite3
import threading


class SecurityMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def get_estop_state(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM estop_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    def save_estop_state(self, state: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO estop_state
                   (id, kill_all, network_kill, frozen_tools_json, updated_at, reason)
                   VALUES (1, ?, ?, ?, ?, ?)""",
                (
                    1 if state.get("kill_all") else 0,
                    1 if state.get("network_kill") else 0,
                    state.get("frozen_tools_json") or "[]",
                    state.get("updated_at"),
                    state.get("reason"),
                ),
            )
