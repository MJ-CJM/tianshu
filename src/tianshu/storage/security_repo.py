"""Storage 锦衣卫领域 Mixin —— 急停单行状态(迭代 3)+ 影子快照台账(迭代 3.5)。"""

import sqlite3
import threading


class SecurityMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def get_estop_state(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM estop_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    # --- 影子快照台账(迭代 3.5「客卿」)---

    def save_shadow_snapshot(self, snap: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO shadow_snapshots
                   (id, edict_id, memorial_id, sha, label, work_tree, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    snap["id"],
                    snap["edict_id"],
                    snap.get("memorial_id"),
                    snap["sha"],
                    snap["label"],
                    snap["work_tree"],
                    snap["created_at"],
                ),
            )

    def list_shadow_snapshots(self, edict_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM shadow_snapshots WHERE edict_id = ? ORDER BY created_at DESC",
                (edict_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_shadow_work_tree(self, edict_id: str) -> str | None:
        """该 edict 的影子工作区路径(revert 时定位仓)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT work_tree FROM shadow_snapshots WHERE edict_id = ? LIMIT 1",
                (edict_id,),
            ).fetchone()
        return row["work_tree"] if row else None

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
