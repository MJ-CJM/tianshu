"""Feishu 连接层：WebSocket (Step 6) + Webhook (本步)。共享 inbound_queue。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol

from fastapi import APIRouter, Request, Response

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
