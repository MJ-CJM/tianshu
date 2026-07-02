"""ModeRouter：基于 SessionAnchor 状态判定模式，分发到对应分支。

镜像 feishu/mode_router.py（逻辑零飞书耦合，仅类型注解换 telegram）。
状态机：anchor 指向 assistant_chat 敕令 → 助手模式；指向业务敕令 → 敕令模式。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tianshu.gateway.telegram.assistant_branch import AssistantBranch
    from tianshu.gateway.telegram.dispatcher import TelegramMessage
    from tianshu.gateway.telegram.edict_branch import EdictBranch
    from tianshu.gateway.telegram.session_anchor import SessionAnchor
    from tianshu.gateway.telegram.settings import TelegramSettings
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

Mode = Literal["assistant", "edict"]


@dataclass(frozen=True)
class ModeContext:
    mode: Mode
    chat_id: str
    sender_open_id: str
    edict_id: str | None


class ModeRouter:
    def __init__(
        self,
        *,
        anchor: SessionAnchor,
        assistant_branch: AssistantBranch,
        edict_branch: EdictBranch,
        edict_bridge,
        storage: Storage,
        settings: TelegramSettings,
    ) -> None:
        self._anchor = anchor
        self._assistant = assistant_branch
        self._edict = edict_branch
        self._edict_bridge = edict_bridge
        self._storage = storage
        self._settings = settings

    def resolve_mode(self, chat_id: str) -> ModeContext:
        edict_id = self._anchor.get(chat_id)
        if not edict_id:
            return ModeContext(
                mode="assistant", chat_id=chat_id, sender_open_id="", edict_id=None,
            )
        edict = self._storage.get_edict(edict_id)
        is_chat = bool(edict and edict.metadata and edict.metadata.get("assistant_chat"))
        return ModeContext(
            mode="assistant" if is_chat else "edict",
            chat_id=chat_id, sender_open_id="", edict_id=edict_id,
        )

    async def dispatch(self, msg: TelegramMessage) -> None:
        """主入口：保证 anchor 存在 → 判断模式 → 转给对应分支。"""
        await self._edict_bridge.ensure_chat_edict(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
            assistant_persona_id=self._settings.assistant_persona_id,
        )
        ctx = self.resolve_mode(msg.chat_id)
        ctx = ModeContext(
            mode=ctx.mode, chat_id=ctx.chat_id,
            sender_open_id=msg.sender_open_id, edict_id=ctx.edict_id,
        )
        logger.info(
            "[telegram/mode] chat=%s mode=%s text=%.80s",
            msg.chat_id, ctx.mode, msg.text,
        )
        if ctx.mode == "assistant":
            await self._assistant.handle(msg, ctx)
        else:
            await self._edict.handle(msg, ctx)


__all__ = ["ModeRouter", "ModeContext", "Mode"]
