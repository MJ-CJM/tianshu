"""Storage Telegram 领域 Mixin —— 会话锚点、去重、思考占位消息、待处理审批按钮。"""

import sqlite3
import threading


class TelegramMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Telegram session anchor（与飞书并列）---

    def get_telegram_anchor(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_telegram_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "telegram-default"
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT INTO telegram_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_telegram_anchor(self, chat_id: str, instance_id: str = "telegram-default") -> None:
        self._conn.execute(
            "DELETE FROM telegram_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def list_telegram_active_anchor_chats(self, instance_id: str = "telegram-default") -> list[str]:
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def list_telegram_chats_anchored_to(
        self, edict_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """反查：哪些 telegram chat 的 anchor 指向该 edict（出站定位回执目标）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Telegram dedup ---

    def is_telegram_update_seen(
        self, update_id: str, instance_id: str = "telegram-default"
    ) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM telegram_seen_messages WHERE update_id = ? AND instance_id = ?",
            (update_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_telegram_update_seen(
        self,
        update_id: str,
        max_entries: int = 2048,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO telegram_seen_messages (update_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (update_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM telegram_seen_messages WHERE instance_id = ? AND update_id IN ("
            "  SELECT update_id FROM telegram_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM telegram_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    # --- Telegram thinking 占位消息（替代飞书 typing reaction）---

    def save_telegram_thinking(
        self,
        *,
        memorial_id: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        """登记一条 ⏳ 占位消息，等 execution 完成时 delete。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_thinking_messages "
            "(memorial_id, chat_id, message_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (memorial_id, chat_id, message_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_telegram_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1]}

    # --- Telegram pending buttons（审批 inline keyboard 反查）---

    def save_telegram_pending_button(
        self,
        *,
        approval_id: str,
        chat_id: str,
        message_id: str,
        kind: str,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_pending_buttons "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def claim_telegram_pending_button(
        self,
        *,
        approval_id: str,
        instance_id: str,
        chat_id: str,
        kind: str,
    ) -> bool:
        """Atomically reserve one outbound approval artifact before delivery."""

        from datetime import UTC, datetime

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO telegram_pending_buttons "
                    "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
                    "VALUES (?, ?, ?, '', ?, ?)",
                    (
                        approval_id,
                        instance_id,
                        chat_id,
                        kind,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                claimed = cursor.rowcount == 1
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return claimed

    def finalize_telegram_pending_button(
        self,
        *,
        approval_id: str,
        instance_id: str,
        chat_id: str,
        message_id: str,
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE telegram_pending_buttons SET message_id = ? "
            "WHERE approval_id = ? AND instance_id = ? AND chat_id = ? AND message_id = ''",
            (message_id, approval_id, instance_id, chat_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def release_telegram_pending_button_claim(self, approval_id: str, instance_id: str) -> None:
        self._conn.execute(
            "DELETE FROM telegram_pending_buttons "
            "WHERE approval_id = ? AND instance_id = ? AND message_id = ''",
            (approval_id, instance_id),
        )
        self._conn.commit()

    def pop_telegram_pending_button(
        self,
        approval_id: str,
        *,
        instance_id: str | None = None,
    ) -> dict | None:
        predicate = "approval_id = ? AND message_id <> ''"
        params: tuple[str, ...] = (approval_id,)
        if instance_id is not None:
            predicate += " AND instance_id = ?"
            params = (approval_id, instance_id)
        row = self._conn.execute(
            f"SELECT chat_id, message_id, kind FROM telegram_pending_buttons WHERE {predicate}",
            params,
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            f"DELETE FROM telegram_pending_buttons WHERE {predicate}",
            params,
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def get_telegram_pending_button(
        self,
        approval_id: str,
        *,
        instance_id: str | None = None,
    ) -> dict | None:
        """只读查询（不删除）：callback 处理时先看 pending 是否还在。"""
        if instance_id is None:
            row = self._conn.execute(
                "SELECT chat_id, message_id, kind FROM telegram_pending_buttons "
                "WHERE approval_id = ? AND message_id <> ''",
                (approval_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT chat_id, message_id, kind FROM telegram_pending_buttons "
                "WHERE approval_id = ? AND instance_id = ? AND message_id <> ''",
                (approval_id, instance_id),
            ).fetchone()
        if not row:
            return None
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def list_telegram_pending_for_chat(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """该 chat 下尚未响应的待审批 memorial_id（approval 文本命令用）。"""
        rows = self._conn.execute(
            "SELECT approval_id FROM telegram_pending_buttons "
            "WHERE instance_id = ? AND chat_id = ? AND kind = 'tool.approval_required' "
            "ORDER BY created_at ASC",
            (instance_id, chat_id),
        ).fetchall()
        return [r[0] for r in rows]
