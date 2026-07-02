"""敕令模式（anchor=业务敕令）命令路由。镜像 feishu/edict_branch.py。

适配点同 assistant_branch：thinking 占位消息、anchor.delete、查询类委托 AssistantBranch。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.card_builder import format_status_label
from tianshu.gateway.feishu.edict_bridge import EdictBusyError
from tianshu.gateway.telegram.approval_commands import parse_approval_command
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.gateway.telegram.approval_commands import TelegramApprovalCommandHandler
    from tianshu.gateway.telegram.assistant_branch import AssistantBranch
    from tianshu.gateway.telegram.dispatcher import TelegramMessage
    from tianshu.gateway.telegram.mode_router import ModeContext
    from tianshu.gateway.telegram.outbound import TelegramOutbound
    from tianshu.gateway.telegram.session_anchor import SessionAnchor
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class EdictBranch:
    """敕令模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: Storage,
        anchor: SessionAnchor,
        edict_bridge,
        outbound: TelegramOutbound,
        renderer,
        assistant_branch: AssistantBranch,
        approval_commands: TelegramApprovalCommandHandler | None = None,
        assistant_persona_id: str = "tongzheng",
        instance_id: str = "telegram-default",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._assistant = assistant_branch
        self._approval_commands = approval_commands
        self._assistant_persona_id = assistant_persona_id
        self._instance_id = instance_id

    def set_renderer(self, renderer) -> None:
        self._renderer = renderer

    def set_assistant_persona_id(self, persona_id: str) -> None:
        self._assistant_persona_id = persona_id

    async def handle(self, msg: TelegramMessage, ctx: ModeContext) -> None:
        text = msg.text.strip()

        approval_cmd = parse_approval_command(text)
        if approval_cmd is not None and self._approval_commands is not None:
            reply = await self._approval_commands.handle(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
                command=approval_cmd,
            )
            await self._reply(msg.chat_id, reply)
            return

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
        self._anchor.delete(msg.chat_id)
        new_eid = await self._edict_bridge.ensure_chat_edict(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
            assistant_persona_id=self._assistant_persona_id,
        )
        await self._reply(
            msg.chat_id,
            f"{self._renderer.edict_exit_reply()}（已切回助手 #{new_eid[:8]}）",
        )

    async def _cmd_new_with_exit(self, msg, ctx, goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        self._anchor.delete(msg.chat_id)
        result = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        await self._send_thinking(msg, result.edict_id, result.memorial_id, goal)

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
        if edict.status in (EdictStatus.COMPLETED.value, EdictStatus.CANCELLED.value):
            await self._reply(
                msg.chat_id,
                f"敕令 #{edict.id[:8]} 已 {format_status_label(edict.status)}，无需取消",
            )
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        self._storage.update_edict_lifecycle_phase(edict.id, "complete")
        if edict.id == self._anchor.get(msg.chat_id):
            self._anchor.delete(msg.chat_id)
            await self._reply(
                msg.chat_id,
                f"{self._renderer.edict_cancel_reply(edict.id)}（已自动退出敕令模式）",
            )
        else:
            await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    async def _continue_edict(self, msg, ctx, text: str) -> None:
        try:
            result = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._send_thinking(msg, result.edict_id, result.memorial_id, text)

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)

    async def _send_thinking(
        self, msg: TelegramMessage, edict_id: str, memorial_id: str, instruction: str,
    ) -> None:
        mid = await self._outbound.send_thinking(msg.chat_id)
        if mid:
            self._storage.save_telegram_thinking(
                memorial_id=memorial_id, chat_id=msg.chat_id, message_id=mid,
            )


__all__ = ["EdictBranch"]
