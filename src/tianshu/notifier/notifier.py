"""Notifier — WebSocket broadcast + webhook delivery."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import WebSocket

from tianshu.models.events import EventEnvelope
from tianshu.notifier.renderer import render_dingtalk, render_email, render_feishu, render_status
from tianshu.security.redact import redact_mapping, redact_text
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.5


class Notifier:
    """Manages WebSocket connections, webhook delivery, and external channels."""

    def __init__(
        self,
        storage: Storage,
        channel_registry: object | None = None,
        quiet_hours_start: int = 23,
        quiet_hours_end: int = 8,
    ) -> None:
        self._storage = storage
        self._channel_registry = channel_registry
        self._ws_clients: set[WebSocket] = set()
        self._debounce_timers: dict[str, asyncio.Task] = {}
        # 通知三级制免打扰时段(迭代 5,D2):start==end 关闭
        self._quiet_start = quiet_hours_start
        self._quiet_end = quiet_hours_end

    def _in_quiet_hours(self, hour: int) -> bool:
        """当前小时是否落在免打扰时段(支持跨午夜,如 23–8)。"""
        s, e = self._quiet_start, self._quiet_end
        if s == e:
            return False
        return s <= hour < e if s < e else (hour >= s or hour < e)

    def _now_hour(self) -> int:
        """当前本地小时(抽成方法便于测试注入)。"""
        from datetime import datetime

        return datetime.now().hour

    def register_ws(self, ws: WebSocket) -> None:
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: WebSocket) -> None:
        self._ws_clients.discard(ws)

    async def broadcast_ws(self, message: dict) -> None:
        """Send message to all connected WebSocket clients.

        锦衣卫·出站脱敏(迭代 3):WS 是最宽的出站面(含流式 delta),统一在此
        redact。流式把 secret 切进两个 chunk 时单片匹配不到,属已知局限。
        """
        if not self._ws_clients:
            return
        data = json.dumps(redact_mapping(message), default=str)
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
                resp = await client.post(url, json=redact_mapping(payload))
                if resp.status_code >= 400:
                    logger.warning("Webhook to %s returned %s", url, resp.status_code)
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

        # Check edict priority — urgent skips debounce
        edict = self._storage.get_edict(event.edict_id) if event.edict_id else None
        if edict and edict.priority == "urgent":
            await self.broadcast_ws(message)
        else:
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

    async def handle_outer_loop_event(self, event: EventEnvelope) -> None:
        """长任务 outer loop 事件透传到 WebSocket（不走 debounce，实时推送给前端）。"""
        await self.broadcast_ws(
            {
                "type": event.event_type,
                "edict_id": event.edict_id,
                "memorial_id": event.memorial_id,
                "payload": event.payload,
            }
        )

    async def _dispatch_external(self, event, memorial, message: dict) -> None:
        """通知三级制外发(迭代 5,D2):urgent 穿透免打扰 / normal 免打扰时段攒起来
        醒后补推 / low 不即时外发入 digest。WS 广播不受影响(前端可见,不打扰手机)。"""
        if not self._channel_registry:
            return
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict or not edict.dispatch or not edict.dispatch.channels:
            return

        quiet = self._in_quiet_hours(self._now_hour())
        # 非免打扰时段:先补推之前攒下的(懒 flush,醒来第一条通知触发)
        if not quiet:
            await self._flush_pending()

        priority = getattr(edict, "priority", "normal")
        channels = list(edict.dispatch.channels)
        if priority == "low":
            return  # 低优先不即时外发,digest 兜底
        if priority == "normal" and quiet:
            self._save_pending(edict_id, getattr(memorial, "id", None), message, channels)
            return
        # urgent 穿透 / normal 非免打扰 → 立即外发
        await self._do_dispatch_channels(memorial, message, channels)

    async def _do_dispatch_channels(self, memorial, message: dict, channel_names: list) -> None:
        renderers = {
            "feishu": render_feishu,
            "dingtalk": render_dingtalk,
            "email": render_email,
        }
        for ch_name in channel_names:
            renderer = renderers.get(ch_name, render_feishu)
            rendered = redact_text(renderer(memorial))
            channel = self._channel_registry.get(ch_name)  # type: ignore[union-attr]
            if channel:
                try:
                    await channel.send(redact_mapping(message), rendered)
                except Exception:
                    logger.exception("External channel %s failed", ch_name)

    def _save_pending(self, edict_id, memorial_id, message: dict, channels: list) -> None:
        from datetime import UTC, datetime

        from ulid import ULID

        self._storage.save_pending_notification(
            {
                "id": str(ULID()),
                "edict_id": edict_id,
                "memorial_id": memorial_id,
                "message": message,
                "channels": channels,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    async def _flush_pending(self) -> None:
        """补推免打扰时段攒下的通知(用当前 memorial 重新渲染)。"""
        for p in self._storage.list_pending_notifications():
            memorial = (
                self._storage.get_memorial(p["memorial_id"]) if p.get("memorial_id") else None
            )
            if memorial:
                await self._do_dispatch_channels(memorial, p["message"], p["channels"])
            self._storage.delete_pending_notification(p["id"])

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


class WebSocketStreamCallback:
    """StreamCallback implementation that pushes deltas to WebSocket clients."""

    def __init__(self, notifier: Notifier, edict_id: str) -> None:
        self._notifier = notifier
        self._edict_id = edict_id

    async def on_delta(self, text: str) -> None:
        await self._notifier.broadcast_ws(
            {
                "type": "stream.delta",
                "edict_id": self._edict_id,
                "text": text,
            }
        )

    async def on_tool_call_start(self, name: str) -> None:
        await self._notifier.broadcast_ws(
            {
                "type": "stream.tool_start",
                "edict_id": self._edict_id,
                "tool_name": name,
            }
        )

    async def on_tool_call_end(self, name: str, result: object) -> None:
        await self._notifier.broadcast_ws(
            {
                "type": "stream.tool_end",
                "edict_id": self._edict_id,
                "tool_name": name,
                "is_error": getattr(result, "is_error", False),
            }
        )
