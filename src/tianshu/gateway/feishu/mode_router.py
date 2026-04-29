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
    from tianshu.gateway.feishu.edict_bridge import EdictBridge
    from tianshu.gateway.feishu.session_anchor import SessionAnchor
    from tianshu.gateway.feishu.settings import FeishuSettings
    from tianshu.storage import Storage

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
        edict_bridge: "EdictBridge",
        storage: "Storage",
        settings: "FeishuSettings",
    ) -> None:
        self._anchor = anchor
        self._assistant = assistant_branch
        self._edict = edict_branch
        self._edict_bridge = edict_bridge
        self._storage = storage
        self._settings = settings

    def resolve_mode(self, chat_id: str) -> ModeContext:
        """根据当前 anchor 状态构造 ModeContext。

        v2: anchor 永远存在（dispatch 中由 ensure_chat_edict 保证）。
        - anchor 指向 metadata.assistant_chat=true 敕令 → assistant 模式
        - anchor 指向其它（业务敕令）→ edict 模式
        """
        edict_id = self._anchor.get(chat_id)
        if not edict_id:
            # 防御性：理论上 dispatch 已 ensure，这里返回 assistant
            return ModeContext(
                mode="assistant", chat_id=chat_id,
                sender_open_id="", edict_id=None,
            )
        edict = self._storage.get_edict(edict_id)
        is_chat = bool(edict and edict.metadata and edict.metadata.get("assistant_chat"))
        return ModeContext(
            mode="assistant" if is_chat else "edict",
            chat_id=chat_id,
            sender_open_id="", edict_id=edict_id,
        )

    async def dispatch(self, msg: "FeishuMessage") -> None:
        """主入口：保证 anchor 存在 → 判断模式 → 转给对应分支。"""
        # v2: 首次接入自动建 chat 敕令（用通政司配置的 assistant_persona_id）
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
            "[feishu/mode] chat=%s mode=%s text=%.80s",
            msg.chat_id, ctx.mode, msg.text,
        )
        if ctx.mode == "assistant":
            await self._assistant.handle(msg, ctx)
        else:
            await self._edict.handle(msg, ctx)


__all__ = ["ModeRouter", "ModeContext", "Mode"]
