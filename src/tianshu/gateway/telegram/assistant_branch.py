"""助手模式（anchor=chat 敕令）命令路由。镜像 feishu/assistant_branch.py。

适配点（相对飞书）：
- thinking：发 ⏳ 占位消息（outbound.send_thinking）+ 记 telegram_thinking 表
- 清 anchor：anchor.delete（而非 storage.delete_feishu_anchor）
- send_card：card 为 (text, InlineKeyboardMarkup) 元组
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.card_builder import format_status_label
from tianshu.gateway.telegram.approval_commands import parse_approval_command
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.gateway.telegram.approval_commands import TelegramApprovalCommandHandler
    from tianshu.gateway.telegram.card_builder import TelegramCardBuilder
    from tianshu.gateway.telegram.dispatcher import TelegramMessage
    from tianshu.gateway.telegram.mode_router import ModeContext
    from tianshu.gateway.telegram.outbound import TelegramOutbound
    from tianshu.gateway.telegram.session_anchor import SessionAnchor
    from tianshu.models.edict import Edict
    from tianshu.persona.loader import AgentPersona  # noqa: F401
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class AssistantBranch:
    """助手模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        anchor: "SessionAnchor",
        edict_bridge,
        outbound: "TelegramOutbound",
        renderer,
        card_builder: "TelegramCardBuilder",
        approval_commands: "TelegramApprovalCommandHandler | None" = None,
        assistant_persona_id: str = "tongzheng",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._card_builder = card_builder
        self._approval_commands = approval_commands
        self._assistant_persona_id = assistant_persona_id

    def set_renderer(self, renderer) -> None:
        self._renderer = renderer

    def set_assistant_persona_id(self, persona_id: str) -> None:
        self._assistant_persona_id = persona_id

    async def handle(self, msg: "TelegramMessage", ctx: "ModeContext") -> None:
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

        if cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_new(msg, ctx, goal)
        elif cmd == "/list":
            filter_arg = parts[1].strip().lower() if len(parts) > 1 else "open"
            await self._cmd_list(msg, ctx, filter_arg)
        elif cmd == "/select":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_select(msg, ctx, target)
        elif cmd == "/budget":
            await self._cmd_budget(msg, ctx)
        elif cmd == "/menu":
            await self._cmd_menu(msg, ctx)
        elif cmd in ("/help", "/start"):
            await self._reply(msg.chat_id, self._renderer.help_assistant())
        elif cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_status(msg, ctx, target)
        elif cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_cancel(msg, ctx, target)
        elif cmd == "/clear":
            await self._cmd_clear(msg, ctx)
        elif cmd == "/exit":
            await self._reply(
                msg.chat_id,
                f"{self._renderer.assistant_tag()} 您已在助手模式。开新对话用 `/clear`，"
                f"创建任务用 `/new <目标>`",
            )
        elif cmd.startswith("/"):
            await self._reply(
                msg.chat_id,
                self._renderer.unknown_command_reply(self._renderer.assistant_tag(), cmd),
            )
        else:
            await self._handle_natural_language(msg, ctx, text)

    # --- 命令实现 ---

    async def _cmd_new(self, msg: "TelegramMessage", ctx: "ModeContext", goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        result = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        await self._send_thinking(msg, result.edict_id, result.memorial_id, goal)

    async def _cmd_list(self, msg: "TelegramMessage", ctx: "ModeContext", filter_arg: str) -> None:
        status_filter = self._parse_filter(filter_arg)
        status_value = status_filter.value if status_filter is not None else None
        edicts, _total = self._storage.list_edicts(
            status=status_value, limit=10, offset=0, exclude_assistant_chat=True,
        )
        if not edicts:
            await self._reply(
                msg.chat_id,
                f"{self._renderer.assistant_tag()} 暂无敕令。输入 /new <目标> 颁布第一道",
            )
            return
        card = self._card_builder.build_list_card(edicts=edicts, current_anchor=ctx.edict_id)
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_select(self, msg: "TelegramMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/select <敕令 ID 前缀（≥6 字符）>")
            return
        if len(target) < 6:
            await self._reply(msg.chat_id, "ID 前缀至少 6 字符以避免歧义")
            return
        edicts, _total = self._storage.list_edicts(
            limit=200, offset=0, exclude_assistant_chat=True,
        )
        matches = [e for e in edicts if e.id.startswith(target)]
        if not matches:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}，输入 /list 查看")
            return
        if len(matches) > 1:
            ids_preview = ", ".join(f"#{e.id[:12]}" for e in matches[:5])
            await self._reply(
                msg.chat_id, f"短 ID '{target}' 有多个匹配：{ids_preview}，请用更长前缀",
            )
            return
        edict = matches[0]
        self._anchor.set(msg.chat_id, edict.id)
        await self._reply(
            msg.chat_id,
            self._renderer.edict_selected_reply(edict.id, edict.title or "(无标题)"),
        )

    async def _cmd_budget(self, msg: "TelegramMessage", ctx: "ModeContext") -> None:
        card = await self._card_builder.build_budget_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_menu(self, msg: "TelegramMessage", ctx: "ModeContext") -> None:
        card = self._card_builder.build_menu_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_status(self, msg: "TelegramMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(
                msg.chat_id, "助手模式下 /status 需要指定敕令 ID。用法：/status <id>",
            )
            return
        edict = self._find_by_prefix(target)
        if not edict:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}")
            return
        await self._reply(
            msg.chat_id,
            f"📋 #{edict.id[:8]} 标题：{edict.title or '(无)'}\n状态：{format_status_label(edict.status)}",
        )

    async def _cmd_cancel(self, msg: "TelegramMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(
                msg.chat_id, "助手模式下 /cancel 需要指定敕令 ID。用法：/cancel <id>",
            )
            return
        edict = self._find_by_prefix(target)
        if not edict:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}")
            return
        if edict.status in (EdictStatus.COMPLETED.value, EdictStatus.CANCELLED.value):
            await self._reply(
                msg.chat_id,
                f"敕令 #{edict.id[:8]} 已 {format_status_label(edict.status)}，无需取消",
            )
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        self._storage.update_edict_lifecycle_phase(edict.id, "complete")
        await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    async def _cmd_clear(self, msg: "TelegramMessage", ctx: "ModeContext") -> None:
        if not ctx.edict_id:
            await self._reply(
                msg.chat_id, f"{self._renderer.assistant_tag()} 当前无活跃聊天会话",
            )
            return
        edict = self._storage.get_edict(ctx.edict_id)
        if not edict or not (edict.metadata and edict.metadata.get("assistant_chat")):
            await self._reply(
                msg.chat_id,
                "/clear 仅在聊天会话中可用。当前是业务敕令，请用 /exit 退出后再 /clear。",
            )
            return
        self._storage.update_edict_status(edict.id, EdictStatus.COMPLETED.value)
        self._anchor.delete(msg.chat_id)
        new_eid = await self._edict_bridge.ensure_chat_edict(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id,
            assistant_persona_id=self._assistant_persona_id,
        )
        await self._reply(
            msg.chat_id,
            f"{self._renderer.assistant_tag()} 已归档上轮对话 #{edict.id[:8]}，开启新会话 #{new_eid[:8]}",
        )

    # --- 纯文本（自然语言）---

    async def _handle_natural_language(
        self, msg: "TelegramMessage", ctx: "ModeContext", text: str,
    ) -> None:
        from tianshu.gateway.feishu.edict_bridge import EdictBusyError
        try:
            result = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._send_thinking(msg, result.edict_id, result.memorial_id, text)

    # --- 工具方法 ---

    @staticmethod
    def _parse_filter(s: str) -> EdictStatus | None:
        s = s.lower()
        if s in ("open", "active", ""):
            return EdictStatus.OPEN
        if s == "completed":
            return EdictStatus.COMPLETED
        if s == "cancelled":
            return EdictStatus.CANCELLED
        if s == "all":
            return None
        return EdictStatus.OPEN

    def _find_by_prefix(self, prefix: str) -> "Edict | None":
        if len(prefix) < 6:
            return None
        edicts, _total = self._storage.list_edicts(
            limit=200, offset=0, exclude_assistant_chat=True,
        )
        for e in edicts:
            if e.id.startswith(prefix):
                return e
        return None

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)

    async def _send_thinking(
        self, msg: "TelegramMessage", edict_id: str, memorial_id: str, instruction: str,
    ) -> None:
        """发 ⏳ 占位消息，execution 完成时由 outbound 删除。"""
        mid = await self._outbound.send_thinking(msg.chat_id)
        if mid:
            self._storage.save_telegram_thinking(
                memorial_id=memorial_id, chat_id=msg.chat_id, message_id=mid,
            )


__all__ = ["AssistantBranch"]
