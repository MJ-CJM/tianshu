"""Telegram 审批双语命令处理。

复用 core 的纯解析函数 parse_approval_command / ApprovalCommand（feishu/telegram
共享，同 core/mode_router.py、core/outbound.py 的处理方式）；命令语义（prefix
匹配 / 降级提示 / 回复文案）已上移 core.approval.ApprovalCommandHandler，本类
只保留 telegram 侧 pending 反查实现（telegram_pending_buttons 表）+ actor 前缀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.approval import ApprovalCommand, parse_approval_command
from tianshu.gateway.core.approval import ApprovalCommandHandler as _CoreApprovalCommandHandler

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
            instance_id=instance_id,
        )

    def _list_pending_for_chat(self, chat_id: str) -> list[str]:
        return self._storage.list_telegram_pending_for_chat(chat_id, instance_id=self._instance_id)


__all__ = [
    "TelegramApprovalCommandHandler",
    "parse_approval_command",
    "ApprovalCommand",
]
