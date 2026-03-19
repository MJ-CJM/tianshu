"""Notifier — WebSocket broadcast + webhook delivery."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Set

import httpx
from fastapi import WebSocket

from tianshu.models.events import EventEnvelope
from tianshu.notifier.renderer import render_feishu, render_dingtalk, render_email, render_status
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.5


class Notifier:
    """Manages WebSocket connections, webhook delivery, and external channels."""

    def __init__(
        self,
        storage: Storage,
        channel_registry: object | None = None,
    ) -> None:
        self._storage = storage
        self._channel_registry = channel_registry
        self._ws_clients: set[WebSocket] = set()
        self._debounce_timers: dict[str, asyncio.Task] = {}

    def register_ws(self, ws: WebSocket) -> None:
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: WebSocket) -> None:
        self._ws_clients.discard(ws)

    async def broadcast_ws(self, message: dict) -> None:
        """Send message to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        data = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    async def send_webhook(self, url: str, payload: dict) -> None:
        """Send a webhook POST request."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "Webhook to %s returned %s", url, resp.status_code
                    )
        except Exception:
            logger.exception("Webhook delivery failed for %s", url)

    async def handle_audit_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for audit.completed."""
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        memorial = self._storage.get_memorial(memorial_id)
        if not memorial:
            return

        message = {
            "type": "audit.completed",
            "edict_id": event.edict_id,
            **render_status(memorial),
        }
        await self._debounced_broadcast(memorial_id, message)

        # Dispatch to external channels
        await self._dispatch_external(event, memorial, message)

    async def handle_execution_failed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.failed."""
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        memorial = self._storage.get_memorial(memorial_id)
        if not memorial:
            return

        message = {
            "type": "execution.failed",
            "edict_id": event.edict_id,
            **render_status(memorial),
        }
        await self.broadcast_ws(message)

        # Dispatch to external channels
        await self._dispatch_external(event, memorial, message)

    async def _dispatch_external(self, event, memorial, message: dict) -> None:
        """Dispatch to external notification channels based on edict dispatch config."""
        if not self._channel_registry:
            return

        edict_id = event.edict_id
        if not edict_id:
            return

        edict = self._storage.get_edict(edict_id)
        if not edict or not edict.dispatch or not edict.dispatch.channels:
            return

        # Render per-channel
        channel_names = edict.dispatch.channels
        renderers = {
            "feishu": render_feishu,
            "dingtalk": render_dingtalk,
            "email": render_email,
        }

        for ch_name in channel_names:
            renderer = renderers.get(ch_name, render_feishu)
            rendered = renderer(memorial)
            channel = self._channel_registry.get(ch_name)
            if channel:
                try:
                    await channel.send(message, rendered)
                except Exception:
                    logger.exception("External channel %s failed", ch_name)

    async def _debounced_broadcast(self, key: str, message: dict) -> None:
        """Debounce broadcasts for the same memorial within DEBOUNCE_SECONDS."""
        existing = self._debounce_timers.get(key)
        if existing and not existing.done():
            existing.cancel()

        async def _send() -> None:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await self.broadcast_ws(message)
            self._debounce_timers.pop(key, None)

        self._debounce_timers[key] = asyncio.create_task(_send())
