"""Telegram 会话锚 (chat_id → current_edict_id) 的薄封装。

与飞书 SessionAnchor 并列；额外把 delete 收进类（分支统一走 anchor.delete）。
"""
from __future__ import annotations

from tianshu.storage import Storage


class SessionAnchor:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def get(self, chat_id: str) -> str | None:
        return self._storage.get_telegram_anchor(chat_id)

    def set(self, chat_id: str, edict_id: str) -> None:
        self._storage.set_telegram_anchor(chat_id, edict_id)

    def delete(self, chat_id: str) -> None:
        self._storage.delete_telegram_anchor(chat_id)
