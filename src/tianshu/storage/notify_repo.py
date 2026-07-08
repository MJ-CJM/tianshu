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
