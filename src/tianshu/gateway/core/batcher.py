"""InboundBatcher：0.6s 静默期文本合批，feishu/telegram dispatcher 共享核。

命令（`/` 开头）跳过合批直接派发；纯文本按 chat 维度缓冲，静默期内每来一条
新文本就重置计时，到期后合并派发。两端消息类型不同，用 core/message.py 的
ChatMessage Protocol 约束；合并结果经 process(chat_id, merged_text, message)
回调交还调用方重建具体消息对象（message 是触发本次派发的最后一条原始消息，
直发时即它自身）。

batch_delay 用取值函数而非定值：两端 settings 支持热加载原地替换，取值函数
保持与重构前"每次 flush 都读 self._settings.text_batch_delay"一致的实时语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.gateway.core.message import ChatMessage

# process(chat_id, merged_text, message)：message 是触发本次派发的最后一条原始
# 消息（直发时就是它自己），调用方用它重建具体的 FeishuMessage/TelegramMessage。
ProcessFn = Callable[[str, str, "ChatMessage"], Awaitable[None]]


class InboundBatcher:
    """按 chat 维度合批：命令直发，文本静默期后合并派发。"""

    def __init__(self, *, batch_delay: Callable[[], float], process: ProcessFn) -> None:
        self._batch_delay = batch_delay
        self._process = process
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._batch_buffers: dict[str, list[ChatMessage]] = {}
        self._batch_timers: dict[str, asyncio.Task] = {}

    async def handle(self, msg: ChatMessage) -> None:
        """命令（`/` 开头）跳过批处理直接派发；纯文本走批处理。"""
        if msg.text.startswith("/"):
            lock = self._chat_locks.setdefault(msg.chat_id, asyncio.Lock())
            async with lock:
                await self._process(msg.chat_id, msg.text, msg)
        else:
            await self.enqueue_for_batch(msg)

    async def enqueue_for_batch(self, msg: ChatMessage) -> None:
        """文本消息进批处理缓冲。静默期后合并 flush。"""
        buf = self._batch_buffers.setdefault(msg.chat_id, [])
        buf.append(msg)
        existing = self._batch_timers.get(msg.chat_id)
        if existing and not existing.done():
            existing.cancel()

        async def _flush() -> None:
            try:
                await asyncio.sleep(self._batch_delay())
            except asyncio.CancelledError:
                return
            buffered = self._batch_buffers.pop(msg.chat_id, [])
            self._batch_timers.pop(msg.chat_id, None)
            if not buffered:
                return
            merged_text = "\n".join(m.text for m in buffered)
            lock = self._chat_locks.setdefault(msg.chat_id, asyncio.Lock())
            async with lock:
                await self._process(msg.chat_id, merged_text, buffered[-1])

        self._batch_timers[msg.chat_id] = asyncio.create_task(_flush())

    async def stop(self) -> None:
        """取消所有 pending batch flush。"""
        for t in list(self._batch_timers.values()):
            if not t.done():
                t.cancel()
        self._batch_timers.clear()
        self._batch_buffers.clear()


__all__ = ["InboundBatcher"]
