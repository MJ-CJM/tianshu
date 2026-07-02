"""Telegram 审批双语命令处理。

复用飞书的纯解析函数 parse_approval_command / ApprovalCommand；
handler 镜像飞书 ApprovalCommandHandler，pending 反查改用 telegram_pending_buttons 表。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

# 纯函数复用（中英审批命令解析）
from tianshu.gateway.feishu.approval_commands import (  # noqa: F401
    ApprovalCommand,
    parse_approval_command,
)

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class TelegramApprovalCommandHandler:
    """根据已解析的 ApprovalCommand 调 ApprovalManager 执行审批，返回回复文本。"""

    def __init__(
        self,
        *,
        storage: Storage,
        approval_manager: ApprovalManager,
        instance_id: str = "telegram-default",
    ) -> None:
        self._storage = storage
        self._approval = approval_manager
        self._instance_id = instance_id

    async def handle(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        command: ApprovalCommand,
    ) -> str:
        pending = self._storage.list_telegram_pending_for_chat(
            chat_id, instance_id=self._instance_id
        )
        if not pending:
            return "🛡️ 当前 chat 无待审批工具调用。"

        if command.target_prefix:
            matches = [p for p in pending if p.startswith(command.target_prefix)]
            if not matches:
                return f"未找到待审批 #{command.target_prefix}，输入命令查看 chat 内 pending"
            if len(matches) > 1:
                preview = ", ".join(f"#{m[:12]}" for m in matches[:5])
                return f"前缀 '{command.target_prefix}' 匹配多个：{preview}，请用更长前缀"
            memorial_id = matches[0]
        else:
            if len(pending) > 1:
                lines = [f"⚠️ chat 内有 {len(pending)} 个待审批，请指定短 ID："]
                for m in pending[:10]:
                    lines.append(f"  - `/approve {m[:8]}` 或 `/准 {m[:8]}`")
                return "\n".join(lines)
            memorial_id = pending[0]

        try:
            decree = await self._approval.submit_tool_decision(
                memorial_id=memorial_id,
                action=command.action,
                grant_scope=command.scope if command.action == "approve" else None,
                actor=f"telegram:{sender_open_id}",
            )
        except ValueError as exc:
            logger.info("[telegram/approval] submit skipped: %s", exc)
            return f"敕令 #{memorial_id[:8]} 已被其他通道响应。"

        if command.action == "reject":
            return f"❌ 已拒绝 #{memorial_id[:8]}"
        actual_scope = decree.grant_scope or "once"
        scope_label = {"once": "单次", "edict": "本敕令", "always": "总是"}.get(
            actual_scope, "单次"
        )
        if command.scope and command.scope != actual_scope:
            requested_label = {
                "once": "单次",
                "edict": "本敕令",
                "always": "总是",
            }.get(command.scope, command.scope)
            return (
                f"✅ 已批准 #{memorial_id[:8]}（{scope_label}，"
                f"原请求 {requested_label} 因安全策略降级 —— "
                f"shell_exec 等高危工具不可永久放行）"
            )
        return f"✅ 已批准 #{memorial_id[:8]}（{scope_label}）"


__all__ = [
    "TelegramApprovalCommandHandler",
    "parse_approval_command",
    "ApprovalCommand",
]
