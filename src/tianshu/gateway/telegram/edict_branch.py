"""敕令模式（anchor=业务敕令）命令路由：telegram 钩子实现。

命令分发、全部 `_cmd_*` 已上移 core.edict_branch.EdictBranchBase；本文件只
保留 telegram 特有的 thinking 占位消息实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tianshu.gateway.core.edict_branch import EdictBranchBase

if TYPE_CHECKING:
    from tianshu.gateway.telegram.dispatcher import TelegramMessage


class EdictBranch(EdictBranchBase):
    """Telegram 敕令模式命令分发器。"""

    _default_instance_id = "telegram-default"

    async def _send_thinking(
        self,
        msg: TelegramMessage,
        edict_id: str,
        memorial_id: str,
        instruction: str,
    ) -> None:
        """发 ⏳ 占位消息，execution 完成时由 outbound 删除。"""
        mid = await self._outbound.send_thinking(msg.chat_id)
        if mid:
            self._storage.save_telegram_thinking(
                memorial_id=memorial_id,
                chat_id=msg.chat_id,
                message_id=mid,
            )


__all__ = ["EdictBranch"]
