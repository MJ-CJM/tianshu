"""Feishu 出站：lark-oapi 客户端 + 事件订阅 → 飞书消息。"""
from __future__ import annotations

import json
import logging
import re

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_MD_HINT_RE = re.compile(r"(\n#+\s|\n\s*[-*]\s|\*\*|`{3}|\[.+\]\(.+\))")


class FeishuOutbound:
    """事件订阅 + 飞书消息发送。Step 5 起会扩展卡片下行。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        event_bus: EventBus,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._client: lark.Client | None = None

    def start(self) -> None:
        """构造 lark client + 注册 EventBus 订阅。"""
        self._client = self._build_client()
        # 注意：事件名是 execution.completed（不是 memorial.completed）
        self._event_bus.on(
            "execution.completed", self._on_execution_completed, priority=200
        )
        self._event_bus.on(
            "execution.failed", self._on_execution_failed, priority=200
        )

    def _build_client(self) -> lark.Client:
        builder = (
            lark.Client.builder()
            .app_id(self._settings.app_id)
            .app_secret(self._settings.app_secret)
        )
        if self._settings.domain == "lark":
            builder = builder.domain(lark.LARK_DOMAIN)
        else:
            builder = builder.domain(lark.FEISHU_DOMAIN)
        return builder.log_level(lark.LogLevel.WARNING).build()

    # --- 公共 API ---

    async def send_text(self, chat_id: str, content: str) -> str | None:
        """发送文本/post（自动选择）。返回 message_id，失败返 None。"""
        if not chat_id or not content:
            return None
        if _MD_HINT_RE.search(content):
            return await self._send_post(chat_id, content)
        return await self._send_plain_text(chat_id, content)

    async def send_card(self, chat_id: str, card_payload: dict) -> str | None:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card_payload, ensure_ascii=False))
                .build()
            )
            .build()
        )
        return await self._send(req)

    async def update_card(self, message_id: str, card_payload: dict) -> bool:
        if self._client is None:
            return False
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(json.dumps(card_payload, ensure_ascii=False))
                .build()
            )
            .build()
        )
        try:
            resp = await self._client.im.v1.message.apatch(req)
            ok = resp.success()
            if not ok:
                logger.warning(
                    "[feishu/outbound] patch failed code=%s msg=%s",
                    resp.code,
                    resp.msg,
                )
            return ok
        except Exception:
            logger.exception("[feishu/outbound] patch crashed")
            return False

    # --- 内部 ---

    async def _send_plain_text(self, chat_id: str, text: str) -> str | None:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        return await self._send(req)

    async def _send_post(self, chat_id: str, markdown: str) -> str | None:
        post_payload = {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "md", "text": markdown}]],
            }
        }
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("post")
                .content(json.dumps(post_payload, ensure_ascii=False))
                .build()
            )
            .build()
        )
        msg_id = await self._send(req)
        if msg_id:
            return msg_id
        plain = re.sub(r"[*_`#>]", "", markdown)
        return await self._send_plain_text(chat_id, plain)

    async def _send(self, req) -> str | None:
        if self._client is None:
            logger.warning("[feishu/outbound] client not initialized, skipping send")
            return None
        try:
            resp = await self._client.im.v1.message.acreate(req)
            if not resp.success():
                logger.warning(
                    "[feishu/outbound] send failed code=%s msg=%s",
                    resp.code,
                    resp.msg,
                )
                return None
            return resp.data.message_id if resp.data else None
        except Exception:
            logger.exception("[feishu/outbound] send crashed")
            return None

    # --- 事件订阅 handlers ---

    async def _on_execution_completed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        memorial = (
            self._storage.get_memorial(event.memorial_id)
            if event.memorial_id
            else None
        )
        if not memorial or not memorial.result:
            return
        title = (event.payload or {}).get("title", "")
        snippet = memorial.result[:500] + ("…" if len(memorial.result) > 500 else "")
        await self.send_text(chat_id, f"✅ **{title or '完成'}**\n\n{snippet}")

    async def _on_execution_failed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        reason = (event.payload or {}).get("error", "未知错误")
        await self.send_text(chat_id, f"❌ 执行失败：{reason}")

    def _lookup_chat_id(self, event: EventEnvelope) -> str | None:
        """根据 edict.metadata.chat_id 反查；没有 → 兜底 home_channel。"""
        if not event.edict_id:
            return self._settings.home_channel or None
        edict = self._storage.get_edict(event.edict_id)
        if not edict:
            return self._settings.home_channel or None
        chat_id = (edict.metadata or {}).get("chat_id")
        return chat_id or (self._settings.home_channel or None)
