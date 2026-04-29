"""助手模式（anchor=NULL）命令路由。

支持命令：/new /list /select /budget /menu /help /status /cancel
不支持的纯文本：先 IntentParser 解析（若启用），失败则回 silent_reply。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.card_builder import format_status_label
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from tianshu.gateway.feishu.card_builder import CardBuilder
    from tianshu.gateway.feishu.dispatcher import FeishuMessage
    from tianshu.gateway.feishu.edict_bridge import EdictBridge
    from tianshu.gateway.feishu.intent_parser import IntentParser
    from tianshu.gateway.feishu.mode_router import ModeContext
    from tianshu.gateway.feishu.outbound import FeishuOutbound
    from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
    from tianshu.gateway.feishu.session_anchor import SessionAnchor
    from tianshu.models.edict import Edict
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class AssistantBranch:
    """助手模式命令分发器。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        anchor: "SessionAnchor",
        edict_bridge: "EdictBridge",
        outbound: "FeishuOutbound",
        renderer: "PersonaRenderer",
        card_builder: "CardBuilder",
        intent_parser: "IntentParser | None",
    ) -> None:
        self._storage = storage
        self._anchor = anchor
        self._edict_bridge = edict_bridge
        self._outbound = outbound
        self._renderer = renderer
        self._card_builder = card_builder
        self._intent_parser = intent_parser  # None = LLM fallback 禁用

    def set_renderer(self, renderer: "PersonaRenderer") -> None:
        """支持 reload 时切换 persona。"""
        self._renderer = renderer

    async def handle(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        """主入口：解析命令 → 调对应实现。"""
        text = msg.text.strip()
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
        elif cmd == "/help":
            await self._reply(msg.chat_id, self._renderer.help_assistant())
        elif cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_status(msg, ctx, target)
        elif cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_cancel(msg, ctx, target)
        elif cmd.startswith("/"):
            await self._reply(
                msg.chat_id,
                self._renderer.unknown_command_reply(self._renderer.assistant_tag(), cmd),
            )
        else:
            await self._handle_natural_language(msg, ctx, text)

    # --- 命令实现 ---

    async def _cmd_new(self, msg: "FeishuMessage", ctx: "ModeContext", goal: str) -> None:
        if not goal:
            await self._reply(msg.chat_id, "用法：/new <目标描述>")
            return
        edict_id = await self._edict_bridge.create_new(
            chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
        )
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        await self._reply(msg.chat_id, self._renderer.edict_created_reply(edict_id, title))

    async def _cmd_list(self, msg: "FeishuMessage", ctx: "ModeContext", filter_arg: str) -> None:
        status_filter = self._parse_filter(filter_arg)
        status_value = status_filter.value if status_filter is not None else None
        edicts, _total = self._storage.list_edicts(
            status=status_value, limit=10, offset=0,
        )
        if not edicts:
            await self._reply(
                msg.chat_id,
                f"{self._renderer.assistant_tag()} 暂无敕令。输入 /new <目标> 颁布第一道",
            )
            return
        card = self._card_builder.build_list_card(
            edicts=edicts, current_anchor=ctx.edict_id,
        )
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_select(self, msg: "FeishuMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(msg.chat_id, "用法：/select <敕令 ID 前缀（≥6 字符）>")
            return
        if len(target) < 6:
            await self._reply(msg.chat_id, "ID 前缀至少 6 字符以避免歧义")
            return
        edicts, _total = self._storage.list_edicts(limit=200, offset=0)
        matches = [e for e in edicts if e.id.startswith(target)]
        if not matches:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}，输入 /list 查看")
            return
        if len(matches) > 1:
            ids_preview = ", ".join(f"#{e.id[:12]}" for e in matches[:5])
            await self._reply(
                msg.chat_id,
                f"短 ID '{target}' 有多个匹配：{ids_preview}，请用更长前缀",
            )
            return
        edict = matches[0]
        self._anchor.set(msg.chat_id, edict.id)
        await self._reply(
            msg.chat_id,
            self._renderer.edict_selected_reply(edict.id, edict.title or "(无标题)"),
        )

    async def _cmd_budget(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        card = await self._card_builder.build_budget_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_menu(self, msg: "FeishuMessage", ctx: "ModeContext") -> None:
        card = self._card_builder.build_menu_card()
        await self._outbound.send_card(msg.chat_id, card)

    async def _cmd_status(self, msg: "FeishuMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(
                msg.chat_id,
                "助手模式下 /status 需要指定敕令 ID。用法：/status <id>",
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

    async def _cmd_cancel(self, msg: "FeishuMessage", ctx: "ModeContext", target: str) -> None:
        if not target:
            await self._reply(
                msg.chat_id,
                "助手模式下 /cancel 需要指定敕令 ID。用法：/cancel <id>",
            )
            return
        edict = self._find_by_prefix(target)
        if not edict:
            await self._reply(msg.chat_id, f"未找到敕令 #{target}")
            return
        if edict.status in (EdictStatus.COMPLETED.value, EdictStatus.CANCELLED.value):
            await self._reply(
                msg.chat_id, f"敕令 #{edict.id[:8]} 已 {format_status_label(edict.status)}，无需取消",
            )
            return
        self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
        await self._reply(msg.chat_id, self._renderer.edict_cancel_reply(edict.id))

    # --- 纯文本（自然语言）---

    async def _handle_natural_language(
        self, msg: "FeishuMessage", ctx: "ModeContext", text: str,
    ) -> None:
        if self._intent_parser is None:
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        intent_result = await self._intent_parser.parse(text)
        intent = intent_result.get("intent", "unknown")
        args = intent_result.get("args", {}) or {}
        if intent == "unknown":
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        # 把意图映射回命令调用
        synthesized = self._synthesize_command(intent, args)
        if synthesized is None:
            await self._reply(msg.chat_id, self._renderer.assistant_silent_reply())
            return
        # 提示用户我们理解的意图，然后执行
        await self._reply(msg.chat_id, self._renderer.llm_intent_hint(synthesized))
        # 重写 msg.text 后递归调 handle
        new_msg = replace(msg, text=synthesized)
        await self.handle(new_msg, ctx)

    @staticmethod
    def _synthesize_command(intent: str, args: dict) -> str | None:
        if intent == "list":
            f = args.get("filter") or "open"
            return f"/list {f}"
        if intent == "new":
            goal = args.get("goal") or ""
            if not goal:
                return None
            return f"/new {goal}"
        if intent == "status":
            tid = args.get("target") or args.get("edict_id") or ""
            return f"/status {tid}".strip()
        if intent == "cancel":
            tid = args.get("target") or args.get("edict_id") or ""
            return f"/cancel {tid}".strip()
        if intent == "budget":
            return "/budget"
        if intent == "help":
            return "/help"
        return None

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
        edicts, _total = self._storage.list_edicts(limit=200, offset=0)
        for e in edicts:
            if e.id.startswith(prefix):
                return e
        return None

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)


__all__ = ["AssistantBranch"]
