"""Storage Feishu 领域 Mixin —— 会话锚点、去重、思考占位反应、待处理审批卡片。"""

import sqlite3
import threading


class FeishuMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Feishu session anchor ---

    def get_feishu_anchor(self, chat_id: str, instance_id: str = "feishu-default") -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_feishu_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "feishu-default"
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT INTO feishu_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_feishu_anchor(self, chat_id: str, instance_id: str = "feishu-default") -> None:
        """`/exit` 用：清除该 chat 的 anchor，回到助手模式。"""
        self._conn.execute(
            "DELETE FROM feishu_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def has_sent_upgrade_notice(self, chat_id: str, version_tag: str) -> bool:
        """幂等检查：是否已对此 chat 发过该版本的升级通告。"""
        row = self._conn.execute(
            "SELECT 1 FROM feishu_pending_cards WHERE approval_id = ? AND kind = ?",
            (chat_id, f"upgrade_notice_{version_tag}"),
        ).fetchone()
        return row is not None

    def mark_upgrade_notice_sent(self, chat_id: str, version_tag: str) -> None:
        """标记已发送升级通告（用于幂等）。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_pending_cards "
            "(approval_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, chat_id, "", f"upgrade_notice_{version_tag}", datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def list_active_anchor_chats(self, instance_id: str = "feishu-default") -> list[str]:
        """列出所有有活跃 anchor 的 chat（用于升级通告下发）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu typing reaction（替代 v1 的 "🤔 思考中" 卡片）---
    #
    # 沿用旧表 feishu_thinking_messages：`message_id` 列存 reaction_id，
    # `source_message_id` 列存用户原消息 id（reaction API 必需）。

    def save_feishu_thinking(
        self,
        *,
        memorial_id: str,
        chat_id: str,
        reaction_id: str,
        source_message_id: str,
    ) -> None:
        """登记一条 typing reaction，等 execution 完成时 remove。"""
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_thinking_messages "
            "(memorial_id, chat_id, message_id, source_message_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memorial_id, chat_id, reaction_id, source_message_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, source_message_id "
            "FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {
            "chat_id": row[0],
            "reaction_id": row[1],
            "source_message_id": row[2],
        }

    def list_chats_anchored_to(
        self, edict_id: str, instance_id: str = "feishu-default"
    ) -> list[str]:
        """反查：哪些飞书 chat 的 anchor 当前指向该 edict。

        用于飞书 outbound 在 edict.metadata.chat_id 缺失（web 创建敕令）时
        定位回执目标 —— 精准送回到 /select 切过来的那个 chat。
        """
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu dedup ---

    def is_feishu_message_seen(self, message_id: str, instance_id: str = "feishu-default") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM feishu_seen_messages WHERE message_id = ? AND instance_id = ?",
            (message_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_feishu_message_seen(
        self,
        message_id: str,
        max_entries: int = 2048,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_seen_messages (message_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (message_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM feishu_seen_messages WHERE instance_id = ? AND message_id IN ("
            "  SELECT message_id FROM feishu_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM feishu_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    def claim_feishu_message_seen(
        self,
        message_id: str,
        max_entries: int = 2048,
        instance_id: str = "feishu-default",
    ) -> bool:
        """Atomically claim an inbound event id; only the first caller succeeds."""
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO feishu_seen_messages "
                    "(message_id, instance_id, seen_at) VALUES (?, ?, ?)",
                    (message_id, instance_id, now),
                )
                claimed = cursor.rowcount == 1
                if claimed:
                    self._conn.execute(
                        "DELETE FROM feishu_seen_messages "
                        "WHERE instance_id = ? AND message_id IN ("
                        "  SELECT message_id FROM feishu_seen_messages WHERE instance_id = ? "
                        "  ORDER BY seen_at ASC "
                        "  LIMIT MAX(0, "
                        "    (SELECT COUNT(*) FROM feishu_seen_messages WHERE instance_id = ?) - ?"
                        "  )"
                        ")",
                        (instance_id, instance_id, instance_id, max_entries),
                    )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return claimed

    # --- Feishu pending cards (Step 5 用) ---

    def save_feishu_pending_card(
        self,
        approval_id: str,
        chat_id: str,
        message_id: str,
        kind: str,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_pending_cards "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_pending_card(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM feishu_pending_cards WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_pending_cards WHERE approval_id = ?",
            (approval_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}
