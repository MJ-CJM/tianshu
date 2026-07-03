"""助手模式（anchor=NULL）命令路由：飞书钩子实现。

命令分发、全部 `_cmd_*`、纯文本续接、工具方法已上移
core.assistant_branch.AssistantBranchBase；本文件只保留飞书特有的 thinking
态实现（给用户原消息加 Typing reaction）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.assistant_branch import AssistantBranchBase

if TYPE_CHECKING:
    from tianshu.gateway.feishu.dispatcher import FeishuMessage


class AssistantBranch(AssistantBranchBase):
    """飞书助手模式命令分发器。"""

    _default_instance_id = "feishu-default"

    async def _send_thinking(
        self,
        msg: FeishuMessage,
        edict_id: str,
        memorial_id: str,
        instruction: str,
    ) -> None:
        """给用户原消息加 typing reaction 表示"正在思考"，登记到 db。

        execution 完成时 outbound 反查 + remove reaction + 发完整 post 富文本。
        若用户消息无 message_id（rare），降级跳过 reaction。
        """
        if not msg.message_id:
            return
        reaction_id = await self._outbound.add_reaction(msg.message_id, "Typing")
        if reaction_id:
            self._storage.save_feishu_thinking(
                memorial_id=memorial_id,
                chat_id=msg.chat_id,
                reaction_id=reaction_id,
                source_message_id=msg.message_id,
            )


__all__ = ["AssistantBranch"]
