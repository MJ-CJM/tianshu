"""Storage 通知领域 Mixin —— 免打扰待发通知(迭代 5「执行 2.0」通知三级制)。

免打扰时段(默认 23:00–08:00)的 normal 通知不即时外发,落此表攒起来;非免打扰
时段来新通知时 notifier 懒 flush 补推——"睡觉干活,醒来补推,不丢"。
"""

import json
import sqlite3
import threading


class NotifyMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def save_pending_notification(self, pending: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO pending_notifications
                   (id, edict_id, memorial_id, message_json, channels_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    pending["id"],
                    pending.get("edict_id"),
                    pending.get("memorial_id"),
                    json.dumps(pending["message"], ensure_ascii=False, default=str),
                    json.dumps(pending["channels"], ensure_ascii=False),
                    pending["created_at"],
                ),
            )

    def list_pending_notifications(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_notifications ORDER BY created_at ASC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "edict_id": r["edict_id"],
                "memorial_id": r["memorial_id"],
                "message": json.loads(r["message_json"]),
                "channels": json.loads(r["channels_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_pending_notification(self, pending_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pending_notifications WHERE id = ?", (pending_id,))

    # --- steer 中途注入(迭代 5「执行 2.0」)---

    def save_steer(self, steer_id: str, edict_id: str, note: str, created_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pending_steers (id, edict_id, note, created_at) VALUES (?, ?, ?, ?)",
                (steer_id, edict_id, note, created_at),
            )

    def list_and_clear_steers(self, edict_id: str) -> list[str]:
        """取出该 edict 的待注入 steer 并删除(取即消费,不重复注入)。"""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, note FROM pending_steers WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
            notes = [r["note"] for r in rows]
            if rows:
                self._conn.execute("DELETE FROM pending_steers WHERE edict_id = ?", (edict_id,))
        return notes
