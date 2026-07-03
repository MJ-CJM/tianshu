"""ApprovalCommandHandler：审批双语命令执行核心，feishu/telegram 共享。

pending 反查与 actor 前缀由子类通过构造参数注入；本类只负责命令语义：
prefix 匹配 / 多 pending 提示 / scope 降级提示 / 批准或拒绝文案。

ApprovalCommand/parse_approval_command 仍在 feishu/approval_commands.py（历史
遗留，telegram 早已复用它；此处按 TYPE_CHECKING 引用，同 core/mode_router.py
对 EdictBridge 的处理方式）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.gateway.feishu.approval_commands import ApprovalCommand

logger = logging.getLogger(__name__)


class ApprovalCommandHandler:
    """根据已解析的 ApprovalCommand 调用 ApprovalManager 执行审批，返回回复文本。"""

    def __init__(
        self,
        *,
        approval_manager: ApprovalManager,
        list_pending: Callable[[str], list[str]],
        actor_prefix: str,
    ) -> None:
        self._approval = approval_manager
        self._list_pending = list_pending
        self._actor_prefix = actor_prefix

    async def handle(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        command: ApprovalCommand,
    ) -> str:
        pending = self._list_pending(chat_id)
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
                actor=f"{self._actor_prefix}:{sender_open_id}",
            )
        except ValueError as exc:
            # pending 已被 web 端响应（幂等）
            logger.info("[%s/approval] submit skipped: %s", self._actor_prefix, exc)
            return f"敕令 #{memorial_id[:8]} 已被其他通道响应。"

        if command.action == "reject":
            return f"❌ 已拒绝 #{memorial_id[:8]}"
        # 用 decree 的实际 scope（可能被安全降级，如 shell_exec 的 always→once）
        actual_scope = decree.grant_scope or "once"
        scope_label = {"once": "单次", "edict": "本敕令", "always": "总是"}.get(
            actual_scope, "单次"
        )
        # 用户请求的 scope 与实际不一致（如 always→once）→ 显式提示降级，
        # 避免用户以为永久放行了，下次又遇到同一审批时困惑
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


__all__ = ["ApprovalCommandHandler"]
