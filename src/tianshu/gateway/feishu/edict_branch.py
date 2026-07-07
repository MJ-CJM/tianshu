"""敕令模式（anchor=eid）命令路由：飞书钩子实现。

命令分发、全部 `_cmd_*` 已上移 core.edict_branch.EdictBranchBase；本文件只
保留飞书特有的 thinking 态实现（给用户原消息加 Typing reaction）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.edict_branch import EdictBranchBase

if TYPE_CHECKING:
    from tianshu.gateway.feishu.dispatcher import FeishuMessage


class EdictBranch(EdictBranchBase):
    """飞书敕令模式命令分发器。"""

    _default_instance_id = "feishu-default"

    async def _send_thinking(
        self,
        msg: FeishuMessage,
        edict_id: str,
        memorial_id: str,
        instruction: str,
    ) -> None:
        """给用户原消息加 typing reaction，由 outbound 在 execution 完成时移除。"""
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


__all__ = ["EdictBranch"]
