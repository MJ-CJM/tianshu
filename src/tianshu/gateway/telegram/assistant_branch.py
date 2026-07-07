"""助手模式（anchor=chat 敕令）命令路由：telegram 钩子实现。

命令分发、全部 `_cmd_*`、纯文本续接、工具方法已上移
core.assistant_branch.AssistantBranchBase；本文件只保留 telegram 特有部分：
thinking 占位消息 + `/start` 别名（扩展 `_HELP_ALIASES`，不复制 handle()）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from tianshu.gateway.core.assistant_branch import AssistantBranchBase

if TYPE_CHECKING:
    from tianshu.gateway.telegram.dispatcher import TelegramMessage
    from tianshu.persona.loader import AgentPersona  # noqa: F401


class AssistantBranch(AssistantBranchBase):
    """Telegram 助手模式命令分发器。"""

    _default_instance_id = "telegram-default"
    # telegram 专属：/start 是 bot 首次对话的平台默认命令，等同 /help。
    _HELP_ALIASES: ClassVar[tuple[str, ...]] = ("/help", "/start")

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


__all__ = ["AssistantBranch"]
