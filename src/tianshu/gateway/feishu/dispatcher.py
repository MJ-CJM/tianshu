"""入站流水线：security → group_gate → batcher → command → router。"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tianshu.gateway.feishu.security import is_allowed_user
from tianshu.gateway.feishu.settings import FeishuSettings

logger = logging.getLogger(__name__)


@dataclass
class FeishuMessage:
    """归一化后的入站消息。"""
    event_id: str
    chat_id: str
    chat_type: str          # p2p | group
    sender_open_id: str
    text: str
    raw: dict


@dataclass
class FeishuCardAction:
    """卡片按钮点击。"""
    event_id: str
    chat_id: str
    sender_open_id: str
    value: dict


class Dispatcher:
    """消费 inbound_queue，分流到 message handler / card handler。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        inbound_queue: asyncio.Queue,
        message_handler: Callable[[FeishuMessage], Awaitable[None]],
        card_handler: Callable[[FeishuCardAction], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._queue = inbound_queue
        self._message_handler = message_handler
        self._card_handler = card_handler
        self._task: asyncio.Task | None = None
        self._chat_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _consume_loop(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._dispatch(payload)
            except Exception:
                logger.exception("[feishu/dispatcher] failed payload=%.300s", json.dumps(payload)[:300])

    async def _dispatch(self, payload: dict) -> None:
        header = payload.get("header") or {}
        event_type = header.get("event_type", "")
        event_id = header.get("event_id", "")
        event = payload.get("event") or {}

        if event_type == "im.message.receive_v1":
            await self._handle_message_event(event_id, event)
        elif event_type == "card.action.trigger":
            await self._handle_card_event(event_id, event)
        else:
            logger.debug("[feishu/dispatcher] ignored event_type=%s", event_type)

    async def _handle_message_event(self, event_id: str, event: dict) -> None:
        msg = (event.get("message") or {})
        sender = (event.get("sender") or {}).get("sender_id") or {}
        sender_open_id = sender.get("open_id", "")
        chat_id = msg.get("chat_id", "")
        chat_type = msg.get("chat_type", "p2p")

        if not is_allowed_user(sender_open_id, self._settings.allowed_users):
            logger.info("[feishu/inbound] rejected non-allowlist sender=%s", sender_open_id)
            return

        mentions = msg.get("mentions") or []
        if chat_type == "group" and not self._is_bot_mentioned(mentions):
            return

        text = self._extract_text(msg)
        if not text:
            return

        fmsg = FeishuMessage(
            event_id=event_id, chat_id=chat_id, chat_type=chat_type,
            sender_open_id=sender_open_id, text=text, raw=event,
        )

        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            await self._message_handler(fmsg)

    async def _handle_card_event(self, event_id: str, event: dict) -> None:
        action = event.get("action") or {}
        value = action.get("value") or {}
        operator = event.get("operator") or {}
        sender_open_id = operator.get("open_id", "")
        if not is_allowed_user(sender_open_id, self._settings.allowed_users):
            return
        chat_id = (event.get("context") or {}).get("open_chat_id", "")
        await self._card_handler(FeishuCardAction(
            event_id=event_id, chat_id=chat_id,
            sender_open_id=sender_open_id, value=value,
        ))

    def _is_bot_mentioned(self, mentions: list[dict]) -> bool:
        bot_id = self._settings.bot_open_id
        bot_name = self._settings.bot_name
        for m in mentions:
            mid = (m.get("id") or {}).get("open_id", "")
            mname = m.get("name", "")
            if bot_id and mid == bot_id:
                return True
            if bot_name and mname == bot_name:
                return True
        return False

    @staticmethod
    def _extract_text(msg: dict) -> str:
        content_str = msg.get("content", "")
        try:
            content = json.loads(content_str)
        except Exception:
            return ""
        msg_type = msg.get("message_type", "")
        if msg_type == "text":
            return (content.get("text") or "").strip()
        if msg_type == "post":
            lines = []
            for row in content.get("content", []):
                line = "".join(seg.get("text", "") for seg in row if seg.get("tag") == "text")
                if line:
                    lines.append(line)
            return "\n".join(lines).strip()
        return ""
