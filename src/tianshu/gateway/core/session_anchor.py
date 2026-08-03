"""Feishu 会话锚 (chat_id → current_edict_id) 的薄封装。"""

from __future__ import annotations

from tianshu.storage import Storage


class SessionAnchor:
    def __init__(self, storage: Storage, instance_id: str = "feishu-default") -> None:
        self._storage = storage
        self._instance_id = instance_id

    def get(self, chat_id: str) -> str | None:
        return self._storage.get_feishu_anchor(chat_id, instance_id=self._instance_id)

    def set(self, chat_id: str, edict_id: str) -> None:
        self._storage.set_feishu_anchor(chat_id, edict_id, instance_id=self._instance_id)

    def delete(self, chat_id: str) -> None:
        self._storage.delete_feishu_anchor(chat_id, instance_id=self._instance_id)

    def list_chats(self) -> list[str]:
        """本实例所有仍挂着 anchor 的 chat_id。"""
        return self._storage.list_active_anchor_chats(instance_id=self._instance_id)
