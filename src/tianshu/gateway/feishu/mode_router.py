"""ModeRouter：基于 SessionAnchor 状态判定模式，分发命令到对应分支。

状态机：
- anchor 不存在 / current_edict_id is None → 助手模式 → AssistantBranch
- anchor.current_edict_id 非空 → 敕令模式 → EdictBranch
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tianshu.gateway.feishu.assistant_branch import AssistantBranch
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_branch import EdictBranch
    from tianshu.gateway.feishu.session_anchor import SessionAnchor

logger = logging.getLogger(__name__)

Mode = Literal["assistant", "edict"]


@dataclass(frozen=True)
class ModeContext:
    """每条消息处理时的模式上下文。"""
    mode: Mode
    chat_id: str
    sender_open_id: str
    edict_id: str | None  # 敕令模式时为绑定的 edict_id


class ModeRouter:
    """读 anchor 决定走哪个分支。"""

    def __init__(
        self,
        *,
        anchor: "SessionAnchor",
        assistant_branch: "AssistantBranch",
        edict_branch: "EdictBranch",
    ) -> None:
        self._anchor = anchor
        self._assistant = assistant_branch
        self._edict = edict_branch

    def resolve_mode(self, chat_id: str) -> ModeContext:
        """根据当前 anchor 状态构造 ModeContext。"""
        edict_id = self._anchor.get(chat_id)
        if edict_id:
            return ModeContext(
                mode="edict", chat_id=chat_id,
                sender_open_id="", edict_id=edict_id,
            )
        return ModeContext(
            mode="assistant", chat_id=chat_id,
            sender_open_id="", edict_id=None,
        )

    async def dispatch(self, msg: "FeishuMessage") -> None:
        """主入口：消息进来 → 判断模式 → 转给对应分支。"""
        ctx = self.resolve_mode(msg.chat_id)
        ctx = ModeContext(
            mode=ctx.mode, chat_id=ctx.chat_id,
            sender_open_id=msg.sender_open_id, edict_id=ctx.edict_id,
        )
        logger.info(
            "[feishu/mode] chat=%s mode=%s text=%.80s",
            msg.chat_id, ctx.mode, msg.text,
        )
        if ctx.mode == "assistant":
            await self._assistant.handle(msg, ctx)
        else:
            await self._edict.handle(msg, ctx)


__all__ = ["ModeRouter", "ModeContext", "Mode"]
