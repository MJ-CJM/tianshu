"""Feishu 会话锚 (chat_id → current_edict_id) 的薄封装。"""
from __future__ import annotations

from tianshu.storage import Storage


class SessionAnchor:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def get(self, chat_id: str) -> str | None:
        return self._storage.get_feishu_anchor(chat_id)

    def set(self, chat_id: str, edict_id: str) -> None:
        self._storage.set_feishu_anchor(chat_id, edict_id)
