"""敕令模式（anchor=eid）命令路由。

支持命令：
- /exit                   退出敕令模式 → 助手模式
- /new <goal>             自动 /exit + /new
- /status [id]            查状态（默认当前 anchor）
- /cancel [id]            取消（默认当前 anchor）
- /list /budget /menu     查询类，委托给 AssistantBranch，不动 anchor
- /help                   敕令模式帮助
- 纯文本                   续接当前敕令（v1 现有行为）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.card_builder import format_status_label
from tianshu.gateway.feishu.edict_bridge import EdictBusyError
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.gateway.feishu.assistant_branch import AssistantBranch
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_bridge import EdictBridge
    from tianshu.gateway.feishu.mode_router import ModeContext
    from tianshu.gateway.feishu.outbound import FeishuOutbound
    from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
    from tianshu.gateway.feishu.session_anchor import SessionAnchor
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class EdictBranch:
    """敕令模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        anchor: "SessionAnchor",
        edict_bridge: "EdictBridge",
        outbound: "FeishuOutbound",
        renderer: "PersonaRenderer",
        assistant_branch: "AssistantBranch",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._assistant = assistant_branch  # 用于查询类命令复用

    def set_renderer(self, renderer: "PersonaRenderer") -> None:
        self._renderer = renderer

    async def handle(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        edict_id = ctx.edict_id or ""

        if cmd == "/exit":
            await self._cmd_exit(msg, ctx)
        elif cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_new_with_exit(msg, ctx, goal)
        elif cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else edict_id
            await self._cmd_status(msg, target)
        elif cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else edict_id
            await self._cmd_cancel(msg, target)
        elif cmd in ("/list", "/budget", "/menu"):
            # 委托给 AssistantBranch 的对应实现，但不动 anchor
            await self._assistant.handle(msg, ctx)
        elif cmd == "/help":
            await self._reply(msg.chat_id, self._renderer.help_edict(edict_id))
        elif cmd.startswith("/"):
            await self._reply(
                msg.chat_id,
                self._renderer.unknown_command_reply(
                    self._renderer.edict_tag(edict_id), cmd,
                ),
            )
        else:
            await self._continue_edict(msg, ctx, text)

    # --- 命令实现 ---

    async def _cmd_exit(self, msg, ctx) -> None:
        self._storage.delete_feishu_anchor(msg.chat_id)
        await self._reply(msg.chat_id, self._renderer.edict_exit_reply())

    async def _cmd_new_with_exit(self, msg, ctx, goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        # 先退出当前敕令模式
        self._storage.delete_feishu_anchor(msg.chat_id)
        # 再新建
        edict_id = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        await self._reply(
            msg.chat_id,
            f"{self._renderer.edict_tag(ctx.edict_id or '')} → {self._renderer.edict_created_reply(edict_id, title)}",
        )

    async def _cmd_status(self, msg, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/status [id]")
            return
        edict = self._storage.get_edict(target)
        if not edict:
            await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
            return
        await self._reply(
            msg.chat_id,
            f"{self._renderer.edict_tag(edict.id)} 标题：{edict.title or '(无)'}\n状态：{format_status_label(edict.status)}",
        )

    async def _cmd_cancel(self, msg, target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/cancel [id]")
            return
        edict = self._storage.get_edict(target)
        if not edict:
            await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
            return
        # edict.status 是 str（数据库列）；EdictStatus enum 用 .value 比对
        if edict.status in (EdictStatus.COMPLETED.value, EdictStatus.CANCELLED.value):
            await self._reply(msg.chat_id, f"敕令 #{edict.id[:8]} 已 {format_status_label(edict.status)}，无需取消")
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        # 如果取消的是当前 anchor 敕令，清 anchor
        if edict.id == self._anchor.get(msg.chat_id):
            self._storage.delete_feishu_anchor(msg.chat_id)
            await self._reply(
                msg.chat_id,
                f"{self._renderer.edict_cancel_reply(edict.id)}（已自动退出敕令模式）",
            )
        else:
            await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    async def _continue_edict(self, msg, ctx, text: str) -> None:
        """v1 续接行为：依赖 EdictBridge.continue_or_create。"""
        try:
            edict_id = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._reply(msg.chat_id, self._renderer.edict_received_reply(edict_id))

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)


__all__ = ["EdictBranch"]
