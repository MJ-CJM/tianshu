"""Feishu 连接层：WebSocket (Step 6) + Webhook (本步)。共享 inbound_queue。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Protocol

import lark_oapi as lark
from fastapi import APIRouter, Request, Response
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from tianshu.gateway.feishu.security import DedupChecker, verify_signature, verify_token
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuConnection(Protocol):
    inbound_queue: asyncio.Queue
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class WebhookConnection:
    """Webhook 模式：挂到 FastAPI router 上。POST {webhook_path}。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._dedup = DedupChecker(storage, max_entries=settings.dedup_cache_size)
        self.router = APIRouter()
        self.router.post(settings.webhook_path)(self._handle_request)

    async def _handle_request(self, request: Request) -> Response:
        body_bytes = await request.body()
        if len(body_bytes) > 1024 * 1024:
            return Response("body too large", status_code=413)

        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify_signature(headers, body_bytes, self._settings.encrypt_key):
            return Response("invalid signature", status_code=401)

        try:
            payload = json.loads(body_bytes)
        except Exception:
            return Response("bad json", status_code=400)

        if payload.get("type") == "url_verification":
            return Response(
                content=json.dumps({"challenge": payload.get("challenge", "")}),
                media_type="application/json",
            )

        if not verify_token(payload, self._settings.verification_token):
            return Response("invalid token", status_code=401)

        event_id = ((payload.get("header") or {}).get("event_id")) or ""
        if event_id and not self._dedup.check_and_mark(event_id):
            return Response("ok", status_code=200)

        await self.inbound_queue.put(payload)
        return Response("ok", status_code=200)

    async def start(self) -> None:
        logger.info("[feishu/webhook] route registered at %s", self._settings.webhook_path)

    async def stop(self) -> None:
        pass


class WebSocketConnection:
    """lark-oapi SDK 反向长连。SDK 内部跑独立线程，事件 dispatch 回主 loop。

    注意：lark.ws.Client 没有公开的 stop API，且 SDK 内置 auto_reconnect。
    daemon thread 模式下，进程退出时 SDK 自动终止。
    """

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._loop = loop
        self._client: object | None = None
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        # domain 是 string URL（不是 lark.Client 那种 builder 风格）
        domain = (
            lark.LARK_DOMAIN if self._settings.domain == "lark"
            else lark.FEISHU_DOMAIN
        )
        handler = (
            lark.EventDispatcherHandler.builder(
                self._settings.encrypt_key,
                self._settings.verification_token,
            )
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_card_action_trigger(self._on_card)
            .build()
        )
        self._client = lark.ws.Client(
            self._settings.app_id,
            self._settings.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
            domain=domain,
            auto_reconnect=True,
        )
        # client.start() 是阻塞同步方法 → daemon thread
        self._thread = threading.Thread(
            target=self._client.start, daemon=True, name="feishu-ws-client",
        )
        self._thread.start()
        logger.info(
            "[feishu/ws] started (app=%s, domain=%s)",
            self._settings.app_id, self._settings.domain,
        )

    async def stop(self) -> None:
        # lark.ws.Client 无公开 stop API；daemon thread 随主进程退出而终止
        logger.info("[feishu/ws] stop requested (daemon thread will exit on process termination)")

    def _on_message(self, event: P2ImMessageReceiveV1) -> None:
        """SDK 在独立线程触发，需要 thread-safe schedule 到主 loop。"""
        try:
            payload = self._sdk_message_to_payload(event)
            asyncio.run_coroutine_threadsafe(
                self.inbound_queue.put(payload), self._loop,
            )
        except Exception:
            logger.exception("[feishu/ws] _on_message failed")

    def _on_card(self, event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """SDK 卡片回调：必须返回 P2CardActionTriggerResponse。"""
        try:
            payload = self._sdk_card_to_payload(event)
            asyncio.run_coroutine_threadsafe(
                self.inbound_queue.put(payload), self._loop,
            )
        except Exception:
            logger.exception("[feishu/ws] _on_card failed")
        # 空响应让 SDK 不更新原卡片（卡片刷新由 outbound.update_card 主动触发）
        return P2CardActionTriggerResponse({})

    @staticmethod
    def _sdk_message_to_payload(event: P2ImMessageReceiveV1) -> dict:
        """把 SDK 事件对象转成 webhook 兼容字典，让 dispatcher 处理逻辑无需感知连接模式。

        防御式访问：SDK 字段缺失时 fallback 空字符串/空列表。
        """
        ev = getattr(event, "event", None)
        if ev is None:
            return {"header": {"event_type": "im.message.receive_v1", "event_id": ""}, "event": {}}
        msg = getattr(ev, "message", None) or object()
        sender = getattr(ev, "sender", None) or object()
        sender_id = getattr(sender, "sender_id", None) or object()
        mentions_raw = getattr(msg, "mentions", None) or []
        return {
            "header": {
                "event_type": "im.message.receive_v1",
                "event_id": getattr(getattr(event, "header", None), "event_id", ""),
            },
            "event": {
                "sender": {"sender_id": {
                    "open_id": getattr(sender_id, "open_id", "") or "",
                    "user_id": getattr(sender_id, "user_id", "") or "",
                    "union_id": getattr(sender_id, "union_id", "") or "",
                }},
                "message": {
                    "message_id": getattr(msg, "message_id", "") or "",
                    "chat_id": getattr(msg, "chat_id", "") or "",
                    "chat_type": getattr(msg, "chat_type", "p2p") or "p2p",
                    "message_type": getattr(msg, "message_type", "text") or "text",
                    "content": getattr(msg, "content", "") or "",
                    "mentions": [
                        {
                            "id": {"open_id": getattr(getattr(m, "id", None), "open_id", "") or ""},
                            "name": getattr(m, "name", "") or "",
                        }
                        for m in mentions_raw
                    ],
                },
            },
        }

    @staticmethod
    def _sdk_card_to_payload(event: P2CardActionTrigger) -> dict:
        """把 SDK 卡片事件转成 webhook 兼容字典。"""
        ev = getattr(event, "event", None) or object()
        operator = getattr(ev, "operator", None) or object()
        action = getattr(ev, "action", None) or object()
        context = getattr(ev, "context", None) or object()
        return {
            "header": {
                "event_type": "card.action.trigger",
                "event_id": getattr(getattr(event, "header", None), "event_id", ""),
            },
            "event": {
                "operator": {"open_id": getattr(operator, "open_id", "") or ""},
                "action": {"value": getattr(action, "value", {}) or {}},
                "context": {"open_chat_id": getattr(context, "open_chat_id", "") or ""},
            },
        }
