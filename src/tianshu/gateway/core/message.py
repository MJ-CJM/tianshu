"""ChatMessage Protocol：通道无关的入站消息结构约束。

事实标准取自两端现有实现的字段交集：
- ``chat_id`` / ``text`` / ``message_id``：FeishuMessage、TelegramMessage 均为
  dataclass 普通字段。
- ``sender_open_id``：FeishuMessage 是普通字段；TelegramMessage 特意加了
  ``@property`` 对齐飞书命名（内部转发到 ``sender_id``）。Protocol 只关心可读，
  两种实现形态都满足只读 property 声明，故按更严格的一方（property）声明。
"""

from __future__ import annotations

from typing import Protocol


class ChatMessage(Protocol):
    chat_id: str
    text: str
    message_id: str

    @property
    def sender_open_id(self) -> str: ...


__all__ = ["ChatMessage"]
