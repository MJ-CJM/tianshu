"""飞书审批双语命令解析与路由（中英对照）。

ApprovalCommand / parse_approval_command 已迁至 core/approval.py（feishu/telegram
共享的纯函数，同 core/mode_router.py、core/outbound.py 的处理方式），此处保留
re-export 以维持既有导入路径不变（telegram/approval_commands.py 等既有调用点）。
本模块只保留飞书特有的 ApprovalCommandHandler（feishu_pending_cards 反查）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.approval import (  # noqa: F401
    ApprovalCommand,
    parse_approval_command,
)
from tianshu.gateway.core.approval import (
    ApprovalCommandHandler as _CoreApprovalCommandHandler,
)

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.storage import Storage


class ApprovalCommandHandler(_CoreApprovalCommandHandler):
    """根据已解析的 ApprovalCommand 调用 ApprovalManager 执行审批（飞书数据访问）。

    命令语义（prefix 匹配 / 降级提示 / 回复文案）已上移
    core.approval.ApprovalCommandHandler；本类只保留飞书侧的 pending 反查
    实现 + actor 前缀。
    """

    def __init__(
        self,
        *,
        storage: Storage,
        approval_manager: ApprovalManager,
        instance_id: str = "feishu-default",
    ) -> None:
        self._storage = storage
        self._instance_id = instance_id
        super().__init__(
            approval_manager=approval_manager,
            list_pending=self._list_pending_for_chat,
            actor_prefix="feishu",
            instance_id=instance_id,
        )

    def _list_pending_for_chat(self, chat_id: str) -> list[str]:
        """从 feishu_pending_cards 反查该 chat 下尚未响应的 memorial_id。"""
        return self._storage.list_feishu_pending_for_chat(chat_id, self._instance_id)


__all__ = [
    "ApprovalCommand",
    "ApprovalCommandHandler",
    "parse_approval_command",
]
