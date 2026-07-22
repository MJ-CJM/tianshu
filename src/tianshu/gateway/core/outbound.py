"""OutboundEventBase：执行完成/失败事件的回执编排，feishu/telegram 共享核。

事件订阅管理、chat_id 反查（含实例路由隔离）、"取 memorial → 清 thinking →
分片下发"编排在本类；分片长度上限是构造参数（chunk_max_len），传输原语、
thinking 机制、每段拼接方式两端不同，留给子类钩子（均以
raise NotImplementedError 占位）。

convert_tables_to_lists 复用 core/markdown_compat（原 feishu/markdown_compat，
telegram 也已复用；现已迁入 core，消除此前 core→feishu 的潜伏循环 import）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from tianshu.gateway.core.markdown_compat import (
    DEFAULT_CHUNK_SIZE,
    convert_tables_to_lists,
    split_long,
)

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.models.events import EventEnvelope
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Outbound(Protocol):
    """两端 outbound 对外发送面的公共子集（按两端实际方法集定，详见报告）。

    send_card 的 payload 类型两端不同（飞书 dict 卡片 JSON，telegram
    ``(text, InlineKeyboardMarkup|None)`` 元组）。"thinking" 态建立/清除两端
    机制不同（飞书 reaction，telegram 占位消息），无同名方法对，不纳入本 Protocol。
    """

    async def send_text(self, chat_id: str, content: str) -> str | None: ...
    async def send_card(self, chat_id: str, card_payload: object) -> str | None: ...


class _OutboundSettings(Protocol):
    """OutboundEventBase 需要的 settings 字段（两端 Settings 类均满足）。"""

    home_channel: str


class OutboundEventBase:
    """执行完成/失败事件 → 回执投递的共享编排。传输原语留子类实现。"""

    def __init__(
        self,
        *,
        settings: _OutboundSettings,
        storage: Storage,
        event_bus: EventBus,
        instance_id: str,
        chunk_max_len: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._instance_id = instance_id
        self._chunk_max_len = chunk_max_len
        # 保存订阅时的 handler 引用：EventBus.off 用 `is` 比对，bound method
        # 每次取属性都是新对象，必须复用 start() 时的同一引用才能 off 掉。
        self._sub_completed = self._on_execution_completed
        self._sub_failed = self._on_execution_failed

    def start(self) -> None:
        """构造/重建传输客户端 + 注册 EventBus 订阅。仅在 XxxBot.start 时调用一次。"""
        self.rebuild_client()
        self._event_bus.on(
            "execution.completed",
            self._sub_completed,
            consumer_name=f"outbound.{self._instance_id}.execution_completed.v1",
            priority=200,
        )
        self._event_bus.on(
            "execution.failed",
            self._sub_failed,
            consumer_name=f"outbound.{self._instance_id}.execution_failed.v1",
            priority=200,
        )

    def stop(self) -> None:
        """取消 EventBus 订阅（实例停止时调用，避免已停实例继续投递）。"""
        self._event_bus.off("execution.completed", self._sub_completed)
        self._event_bus.off("execution.failed", self._sub_failed)

    # --- 事件订阅 handlers ---

    async def _on_execution_completed(self, event: EventEnvelope) -> None:
        """执行完成 → 清 thinking 态 + 分片下发完整内容。"""
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        memorial = self._storage.get_memorial(event.memorial_id) if event.memorial_id else None
        # 优先用 final_output（最终交付物，过滤掉规划/调研等中间过程），
        # 无则回退 result（兼容老 memorial / outer-loop 老路径）
        delivery = (memorial.final_output or memorial.result) if memorial else None

        await self._clear_thinking(event.memorial_id, chat_id)
        if not delivery:
            return

        body = convert_tables_to_lists(delivery)
        chunks = split_long(body, max_len=self._chunk_max_len)
        total = len(chunks)
        for idx, chunk in enumerate(chunks):
            await self._deliver_chunk(chat_id, chunk, idx, total)

    async def _on_execution_failed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        await self._clear_thinking_failed(event.memorial_id, chat_id)
        reason = (event.payload or {}).get("error", "未知错误")
        await self.send_text(chat_id, f"❌ 执行失败：{reason}")

    def _lookup_chat_id(self, event: EventEnvelope) -> str | None:
        """反查回执目标 chat_id。

        Fallback 链：
          1. edict.metadata.chat_id（通道原生发起的敕令）
          2. anchor 反查（web 创建敕令 + /select 切过去的场景）
          3. settings.home_channel（用户配置的兜底）
        """
        if not event.edict_id:
            return self._settings.home_channel or None
        edict = self._storage.get_edict(event.edict_id)
        if not edict:
            return self._settings.home_channel or None
        # 实例路由隔离：非本实例的敕令不由本 outbound 投递，避免交叉发送。
        # 存量敕令无 instance_id → 回退 {channel}-default；cron 等无 channel 敕令
        # （inst=None）仍走 home_channel 兜底，不被实例守卫拦截。
        inst = (edict.metadata or {}).get("instance_id")
        if inst is None:
            ch = (edict.metadata or {}).get("channel")
            inst = f"{ch}-default" if ch else None
        if inst is not None and inst != self._instance_id:
            return None  # 非本实例的敕令，不投递
        chat_id = (edict.metadata or {}).get("chat_id")
        if chat_id:
            return chat_id
        anchored = self._anchored_chats(event.edict_id)
        if anchored:
            return anchored[0]
        return self._settings.home_channel or None

    # --- 子类钩子：传输原语 / thinking 机制 / 数据访问差异 ---

    def rebuild_client(self) -> None:
        """构造/重建传输客户端（lark Client / telegram Bot）。子类实现。"""
        raise NotImplementedError

    def _anchored_chats(self, edict_id: str) -> list[str]:
        """anchor 反查该 edict 绑定的 chat 列表（两端 storage 方法名不同）。"""
        raise NotImplementedError

    async def _deliver_chunk(self, chat_id: str, chunk: str, idx: int, total: int) -> None:
        """下发单个分片（"## 续 i/n" 续接头的拼接/发送方式两端不同）。"""
        raise NotImplementedError

    async def _clear_thinking(self, memorial_id: str | None, chat_id: str) -> None:
        """清除 thinking 态（飞书移除 reaction；telegram 删除占位消息）。"""
        raise NotImplementedError

    async def _clear_thinking_failed(self, memorial_id: str | None, chat_id: str) -> None:
        """执行失败时的 thinking 清理；默认等同成功路径，飞书覆写以追加 CrossMark。"""
        await self._clear_thinking(memorial_id, chat_id)

    async def send_text(self, chat_id: str, content: str) -> str | None:
        """发送文本。子类实现（Outbound Protocol 成员）。"""
        raise NotImplementedError


__all__ = ["Outbound", "OutboundEventBase"]
