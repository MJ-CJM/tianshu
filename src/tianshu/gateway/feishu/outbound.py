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
from tianshu.gateway.core.outbound import OutboundEventBase
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_MD_HINT_RE = re.compile(r"(\n#+\s|\n\s*[-*]\s|\*\*|`{3}|\[.+\]\(.+\))")


class FeishuOutbound(OutboundEventBase):
    """事件订阅 + 飞书消息发送。Step 5 起会扩展卡片下行。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        event_bus: EventBus,
        instance_id: str = "feishu-default",
    ) -> None:
        self._client: lark.Client | None = None
        super().__init__(
            settings=settings, storage=storage, event_bus=event_bus, instance_id=instance_id
        )

    def rebuild_client(self) -> None:
        """构造/重建 lark client（start() 首次调用，或热加载切换 app_id/secret 时）；

        **不重订阅 EventBus**。
        """
        self._client = self._build_client()

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

    async def add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        """给指定消息加 emoji reaction（飞书原生 typing 气泡的实现方式）。

        emoji_type 走飞书内置常量名："Typing" / "CrossMark" / "Heart" 等。
        返回 reaction_id（删除时需要），失败返 None。
        """
        if self._client is None or not message_id or not emoji_type:
            return None
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
            )

            req = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type({"emoji_type": emoji_type})
                    .build()
                )
                .build()
            )
            resp = await self._client.im.v1.message_reaction.acreate(req)
            if resp.success() and resp.data and resp.data.reaction_id:
                return resp.data.reaction_id
            logger.debug(
                "[feishu/outbound] add_reaction rejected emoji=%s msg=%s code=%s msg_text=%s",
                emoji_type,
                message_id,
                getattr(resp, "code", None),
                getattr(resp, "msg", None),
            )
        except Exception:
            logger.exception("[feishu/outbound] add_reaction crashed")
        return None

    async def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        if self._client is None or not message_id or not reaction_id:
            return False
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

            req = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            resp = await self._client.im.v1.message_reaction.adelete(req)
            return bool(resp.success())
        except Exception:
            logger.exception("[feishu/outbound] remove_reaction crashed")
            return False

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

    # --- core hooks：事件编排已上移 OutboundEventBase，这里只留飞书差异 ---

    def _anchored_chats(self, edict_id: str) -> list[str]:
        return self._storage.list_chats_anchored_to(edict_id, instance_id=self._instance_id)

    async def _deliver_chunk(self, chat_id: str, chunk: str, idx: int, total: int) -> None:
        """post tag=md 支持嵌套列表 / 标题 / 加粗 / 行内代码；超长按段落拆段连续下发。"""
        if total == 1:
            await self._send_post(chat_id, chunk)
        else:
            head = f"## 续 {idx + 1}/{total}" if idx > 0 else ""
            await self._send_post(chat_id, f"{head}\n\n{chunk}".strip())

    async def _clear_thinking(self, memorial_id: str | None, chat_id: str) -> None:
        """移除 typing reaction（没结果也要移除，免得用户原消息上一直挂着）。"""
        if not memorial_id:
            return
        pending = self._storage.pop_feishu_thinking(memorial_id)
        if pending and pending.get("source_message_id"):
            await self.remove_reaction(pending["source_message_id"], pending["reaction_id"])

    async def _clear_thinking_failed(self, memorial_id: str | None, chat_id: str) -> None:
        """typing → CrossMark：先去掉 typing，再加红叉。"""
        if not memorial_id:
            return
        pending = self._storage.pop_feishu_thinking(memorial_id)
        if pending and pending.get("source_message_id"):
            await self.remove_reaction(pending["source_message_id"], pending["reaction_id"])
            await self.add_reaction(pending["source_message_id"], "CrossMark")
