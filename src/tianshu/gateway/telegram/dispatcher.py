"""Telegram 入站流水线：dedup → allowlist → 群门控 → 命令/批处理 → handler。

镜像 feishu/dispatcher.py。归一化由 connection 层完成（Update → TelegramMessage/TelegramCallback），
本模块负责去重、白名单、群 @ 门控、命令直发 vs 文本批处理合并。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from tianshu.gateway.core.batcher import InboundBatcher
from tianshu.gateway.telegram.security import is_allowed_user
from tianshu.gateway.telegram.settings import TelegramSettings
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_GROUP_TYPES = ("group", "supergroup")


@dataclass
class TelegramMessage:
    """归一化后的入站文本消息。"""

    update_id: str
    chat_id: str
    chat_type: str  # private | group | supergroup | channel
    sender_id: str  # Telegram user_id（str 归一，内部转 int 校验）
    text: str
    raw: dict = field(default_factory=dict)
    message_id: str = ""  # 原消息 id（用于回复/编辑）
    directed: bool = True  # 群里是否 @bot 或回复 bot（私聊恒 True）

    # 与飞书 FeishuMessage 字段对齐（branch 复用时读 sender_open_id）
    @property
    def sender_open_id(self) -> str:
        return self.sender_id

    @property
    def ingress_id(self) -> str:
        return self.update_id or self.message_id


@dataclass
class TelegramCallback:
    """inline keyboard 按钮点击（callback_query）。"""

    update_id: str
    callback_id: str  # answerCallbackQuery 用
    chat_id: str
    sender_id: str
    message_id: str  # 按钮所在消息 id（编辑/去按钮用）
    data: str  # callback_data 字符串

    @property
    def sender_open_id(self) -> str:
        return self.sender_id


class Dispatcher:
    """去重/白名单/门控 + 命令/文本批处理分流。"""

    def __init__(
        self,
        *,
        settings: TelegramSettings,
        storage: Storage,
        message_handler: Callable[[TelegramMessage], Awaitable[None]],
        callback_handler: Callable[[TelegramCallback], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._message_handler = message_handler
        self._callback_handler = callback_handler
        self._batcher = InboundBatcher(
            batch_delay=lambda: self._settings.text_batch_delay,
            process=self._process_batch,
        )

    async def stop(self) -> None:
        await self._batcher.stop()

    # --- 入站文本 ---

    async def handle_message(self, msg: TelegramMessage) -> None:
        # 去重（webhook 重试 / 偶发重投）
        if msg.update_id and self._storage.is_telegram_update_seen(msg.update_id):
            return
        if msg.update_id:
            self._storage.mark_telegram_update_seen(msg.update_id, self._settings.dedup_cache_size)

        if not self._allowed(msg.sender_id):
            logger.info("[telegram/inbound] rejected non-allowlist sender=%s", msg.sender_id)
            return

        # 群门控：群里需 @bot 或回复 bot 才响应（私聊恒处理）
        if msg.chat_type in _GROUP_TYPES and not msg.directed:
            return

        if not msg.text:
            return

        logger.info(
            "[telegram/inbound] chat=%s sender=%s text=%.80s",
            msg.chat_id,
            msg.sender_id,
            msg.text,
        )

        await self._batcher.handle(msg)

    async def _process_batch(
        self, chat_id: str, merged_text: str, message: TelegramMessage
    ) -> None:
        merged_msg = replace(message, text=merged_text)
        await self._message_handler(merged_msg)

    # --- 入站按钮点击 ---

    async def handle_callback(self, cb: TelegramCallback) -> None:
        if cb.update_id and self._storage.is_telegram_update_seen(cb.update_id):
            return
        if cb.update_id:
            self._storage.mark_telegram_update_seen(cb.update_id, self._settings.dedup_cache_size)
        if not self._allowed(cb.sender_id):
            logger.info("[telegram/callback] rejected non-allowlist sender=%s", cb.sender_id)
            return
        logger.info("[telegram/callback] chat=%s data=%s", cb.chat_id, cb.data)
        await self._callback_handler(cb)

    # --- 工具 ---

    def _allowed(self, sender_id: str) -> bool:
        try:
            uid = int(sender_id)
        except (TypeError, ValueError):
            return False
        return is_allowed_user(uid, self._settings.allowed_users)
