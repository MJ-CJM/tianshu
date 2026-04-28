"""Feishu (Lark) 机器人接入：双向入口 + 双通道审批。

设计文档：docs/superpowers/specs/2026-04-28-feishu-bot-design.md
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.connection import WebhookConnection
from tianshu.gateway.feishu.dispatcher import Dispatcher, FeishuCardAction, FeishuMessage
from tianshu.gateway.feishu.settings import FeishuSettings

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tianshu.bus.event_bus import EventBus
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.notifier.notifier import Notifier
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuBot:
    """飞书机器人门面 —— 协调 connection / dispatcher / outbound。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        event_bus: "EventBus",
        approval_manager: "ApprovalManager",
        notifier: "Notifier",
        settings: FeishuSettings,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._notifier = notifier
        self._settings = settings
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._connection: WebhookConnection | None = None
        self._dispatcher: Dispatcher | None = None

    async def start(self) -> None:
        logger.info(
            "[feishu] starting (mode=%s, app=%s)",
            self._settings.connection_mode,
            self._settings.app_id,
        )
        if self._settings.connection_mode == "webhook":
            self._connection = WebhookConnection(
                settings=self._settings,
                storage=self._storage,
                inbound_queue=self._inbound,
            )
        else:
            raise NotImplementedError("websocket mode 待 Step 6 实现")
        await self._connection.start()

        self._dispatcher = Dispatcher(
            settings=self._settings,
            inbound_queue=self._inbound,
            message_handler=self._on_message,
            card_handler=self._on_card,
        )
        await self._dispatcher.start()

    async def stop(self) -> None:
        logger.info("[feishu] stopping")
        if self._dispatcher:
            await self._dispatcher.stop()
        if self._connection:
            await self._connection.stop()

    def attach_webhook_router(self, app: "FastAPI") -> None:
        """Webhook 模式：把路由挂到 FastAPI app。"""
        if self._connection and isinstance(self._connection, WebhookConnection):
            app.include_router(self._connection.router)

    async def _on_message(self, msg: FeishuMessage) -> None:
        logger.info(
            "[feishu/inbound] chat=%s sender=%s text=%.80s",
            msg.chat_id,
            msg.sender_open_id,
            msg.text,
        )

    async def _on_card(self, action: FeishuCardAction) -> None:
        logger.info("[feishu/card] chat=%s value=%s", action.chat_id, action.value)


__all__ = ["FeishuBot"]
