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
from tianshu.gateway.feishu.markdown_compat import convert_tables_to_lists, split_long
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
        instance_id: str = "feishu-default",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._instance_id = instance_id
        self._client: lark.Client | None = None
        # 保存订阅时的 handler 引用：EventBus.off 用 `is` 比对，bound method
        # 每次取属性都是新对象，必须复用 start() 时的同一引用才能 off 掉。
        self._sub_completed = self._on_execution_completed
        self._sub_failed = self._on_execution_failed

    def start(self) -> None:
        """构造 lark client + 注册 EventBus 订阅。仅在 FeishuBot.start 时调用一次。"""
        self._client = self._build_client()
        # 注意：事件名是 execution.completed（不是 memorial.completed）
        self._event_bus.on("execution.completed", self._sub_completed, priority=200)
        self._event_bus.on("execution.failed", self._sub_failed, priority=200)

    def stop(self) -> None:
        """取消 EventBus 订阅（实例停止时调用，避免已停实例继续投递）。"""
        self._event_bus.off("execution.completed", self._sub_completed)
        self._event_bus.off("execution.failed", self._sub_failed)

    def rebuild_client(self) -> None:
        """仅重建 lark client（用于热加载切换 app_id/secret）；**不重订阅 EventBus**。"""
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

    # --- 事件订阅 handlers ---

    async def _on_execution_completed(self, event: EventEnvelope) -> None:
        """执行完成 → 移除 typing reaction + post 富文本下发完整内容。

        post tag=md 支持嵌套列表 / 标题 / 加粗 / 行内代码；表格转列表；
        超长按段落拆段连续下发。
        """
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        memorial = self._storage.get_memorial(event.memorial_id) if event.memorial_id else None
        # 优先用 final_output（最终交付物，过滤掉规划/调研等中间过程），
        # 无则回退 result（兼容老 memorial / outer-loop 老路径）
        delivery = memorial.final_output or memorial.result if memorial else None
        if not delivery:
            # 没结果也移除 typing 反应，免得用户原消息上一直挂着
            if event.memorial_id:
                pending = self._storage.pop_feishu_thinking(event.memorial_id)
                if pending and pending.get("source_message_id"):
                    await self.remove_reaction(
                        pending["source_message_id"],
                        pending["reaction_id"],
                    )
            return

        pending = (
            self._storage.pop_feishu_thinking(event.memorial_id) if event.memorial_id else None
        )
        if pending and pending.get("source_message_id"):
            await self.remove_reaction(
                pending["source_message_id"],
                pending["reaction_id"],
            )

        body = convert_tables_to_lists(delivery)
        chunks = split_long(body)
        for idx, chunk in enumerate(chunks):
            if len(chunks) == 1:
                await self._send_post(chat_id, chunk)
            else:
                head = f"## 续 {idx + 1}/{len(chunks)}" if idx > 0 else ""
                await self._send_post(chat_id, f"{head}\n\n{chunk}".strip())

    async def _on_execution_failed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        reason = (event.payload or {}).get("error", "未知错误")
        pending = (
            self._storage.pop_feishu_thinking(event.memorial_id) if event.memorial_id else None
        )
        # typing → CrossMark：先去掉 typing，再加红叉
        if pending and pending.get("source_message_id"):
            await self.remove_reaction(
                pending["source_message_id"],
                pending["reaction_id"],
            )
            await self.add_reaction(pending["source_message_id"], "CrossMark")
        await self.send_text(chat_id, f"❌ 执行失败：{reason}")

    def _lookup_chat_id(self, event: EventEnvelope) -> str | None:
        """反查回执目标 chat_id。

        Fallback 链：
          1. edict.metadata.chat_id（飞书原生发起的敕令）
          2. anchor 反查（web 创建敕令 + 飞书 /select 切过去的场景）
          3. settings.home_channel（用户配置的兜底）
        """
        if not event.edict_id:
            return self._settings.home_channel or None
        edict = self._storage.get_edict(event.edict_id)
        if not edict:
            return self._settings.home_channel or None
        # 实例路由隔离：非本实例的敕令不由本 outbound 投递，避免交叉发送。
        # 存量敕令无 instance_id → 回退 {channel}-default；cron 等无 channel 敕令
        # （inst=None）仍走 home_channel 兜底，不被实例守卫拦截。
        inst = (edict.metadata or {}).get("instance_id")
        if inst is None:
            ch = (edict.metadata or {}).get("channel")
            inst = f"{ch}-default" if ch else None
        if inst is not None and inst != self._instance_id:
            return None  # 非本实例的敕令，不投递
        chat_id = (edict.metadata or {}).get("chat_id")
        if chat_id:
            return chat_id
        anchored = self._storage.list_chats_anchored_to(
            event.edict_id, instance_id=self._instance_id
        )
        if anchored:
            return anchored[0]
        return self._settings.home_channel or None
