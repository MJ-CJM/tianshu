"""inline keyboard 按钮点击分发：ea: → 审批；cmd: → 合成命令重入 ModeRouter。

镜像 feishu/card_action_dispatcher.py。callback_data 约定：
  "ea:<action>:<scope>:<memorial_id>"  审批（交 ApprovalKeyboardHandler）
  "cmd:list" / "cmd:budget" / "cmd:clear" / "cmd:help" / "cmd:select:<id8>"  合成命令
所有分支结束都 answer_callback 消除 loading。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.gateway.telegram.approval_kb import ApprovalKeyboardHandler
    from tianshu.gateway.telegram.dispatcher import TelegramCallback
    from tianshu.gateway.telegram.mode_router import ModeRouter
    from tianshu.gateway.telegram.outbound import TelegramOutbound

logger = logging.getLogger(__name__)

# cmd:<name> → 合成的命令文本
_CMD_TEXT = {
    "list": "/list",
    "budget": "/budget",
    "clear": "/clear",
    "help": "/help",
    "menu": "/menu",
}


class CallbackDispatcher:
    def __init__(
        self,
        *,
        mode_router: "ModeRouter",
        approval_kb: "ApprovalKeyboardHandler",
        outbound: "TelegramOutbound",
    ) -> None:
        self._mode_router = mode_router
        self._approval_kb = approval_kb
        self._outbound = outbound

    async def handle(self, cb: "TelegramCallback") -> None:
        data = cb.data or ""
        popup = ""
        try:
            if data.startswith("ea:"):
                popup = await self._approval_kb.handle_callback(cb)
            elif data.startswith("cmd:"):
                await self._handle_command(cb, data[len("cmd:"):])
            else:
                logger.debug("[telegram/callback] unknown data=%s", data)
        except Exception:
            logger.exception("[telegram/callback] handle failed data=%s", data)
        finally:
            await self._outbound.answer_callback(cb.callback_id, popup)

    async def _handle_command(self, cb: "TelegramCallback", rest: str) -> None:
        """把按钮合成为一条 TelegramMessage 文本命令，重入 ModeRouter。"""
        from tianshu.gateway.telegram.dispatcher import TelegramMessage

        if rest.startswith("select:"):
            short_id = rest[len("select:"):]
            text = f"/select {short_id}"
        else:
            text = _CMD_TEXT.get(rest, "")
            if not text:
                return
        synthetic = TelegramMessage(
            update_id="",  # 合成消息不参与去重
            chat_id=cb.chat_id,
            chat_type="private",
            sender_id=cb.sender_id,
            text=text,
            raw={},
            message_id=cb.message_id,
            directed=True,
        )
        await self._mode_router.dispatch(synthetic)


__all__ = ["CallbackDispatcher"]
