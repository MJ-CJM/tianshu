"""Telegram 审批双语命令处理。

复用飞书的纯解析函数 parse_approval_command / ApprovalCommand；命令语义
（prefix 匹配 / 降级提示 / 回复文案）已上移 core.approval.ApprovalCommandHandler，
本类只保留 telegram 侧 pending 反查实现（telegram_pending_buttons 表）+ actor 前缀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.approval import ApprovalCommandHandler as _CoreApprovalCommandHandler

# 纯函数复用（中英审批命令解析）
from tianshu.gateway.feishu.approval_commands import (  # noqa: F401
    ApprovalCommand,
    parse_approval_command,
)

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.storage import Storage


class TelegramApprovalCommandHandler(_CoreApprovalCommandHandler):
    """根据已解析的 ApprovalCommand 调 ApprovalManager 执行审批（telegram 数据访问）。"""

    def __init__(
        self,
        *,
        storage: Storage,
        approval_manager: ApprovalManager,
        instance_id: str = "telegram-default",
    ) -> None:
        self._storage = storage
        self._instance_id = instance_id
        super().__init__(
            approval_manager=approval_manager,
            list_pending=self._list_pending_for_chat,
            actor_prefix="telegram",
        )

    def _list_pending_for_chat(self, chat_id: str) -> list[str]:
        return self._storage.list_telegram_pending_for_chat(chat_id, instance_id=self._instance_id)


__all__ = [
    "TelegramApprovalCommandHandler",
    "parse_approval_command",
    "ApprovalCommand",
]
