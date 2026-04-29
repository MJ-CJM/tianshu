"""卡片按钮通用分发器。

按钮 value 协议（v1.1）：
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel",
  "edict_id"?: str,
  "goal"?: str,
  "filter"?: str,
}

转换：把按钮点击合成为等价的文本命令（如 /select <id>），重发给 ModeRouter。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.gateway.feishu.dispatcher import FeishuCardAction
    from tianshu.gateway.feishu.mode_router import ModeRouter

logger = logging.getLogger(__name__)


class CardActionDispatcher:
    def __init__(self, *, mode_router: "ModeRouter") -> None:
        self._mode_router = mode_router

    async def handle(self, action: "FeishuCardAction") -> None:
        """把卡片按钮 value 转成文本命令再走 ModeRouter。"""
        value = action.value or {}
        command = value.get("command")
        if not command:
            logger.warning("[feishu/card-dispatch] missing command in value=%s", value)
            return

        synthesized = self._synthesize(command, value)
        if synthesized is None:
            logger.warning("[feishu/card-dispatch] unknown command=%s", command)
            return

        # 构造一个伪 FeishuMessage 喂给 ModeRouter
        from tianshu.gateway.feishu.dispatcher import FeishuMessage
        fake_msg = FeishuMessage(
            event_id=action.event_id,
            chat_id=action.chat_id,
            chat_type="p2p",  # 卡片按钮一般在 chat 内点击；group 也走同流程
            sender_open_id=action.sender_open_id,
            text=synthesized,
            raw={"_from_card_button": True, "value": value},
        )
        await self._mode_router.dispatch(fake_msg)

    @staticmethod
    def _synthesize(command: str, value: dict) -> str | None:
        if command == "select":
            eid = value.get("edict_id") or ""
            return f"/select {eid}".strip() if eid else None
        if command == "list":
            f = value.get("filter") or "open"
            return f"/list {f}"
        if command == "budget":
            return "/budget"
        if command == "help":
            return "/help"
        if command == "new":
            goal = value.get("goal") or ""
            return f"/new {goal}".strip() if goal else None
        if command == "cancel":
            eid = value.get("edict_id") or ""
            return f"/cancel {eid}".strip() if eid else "/cancel"
        return None


__all__ = ["CardActionDispatcher"]
