"""Feishu (Lark) 机器人接入：双向入口 + 双通道审批。

设计文档：docs/superpowers/specs/2026-04-28-feishu-bot-design.md
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.approval_card import ApprovalCardHandler
from tianshu.gateway.feishu.connection import WebhookConnection, WebSocketConnection
from tianshu.gateway.feishu.dispatcher import Dispatcher, FeishuCardAction, FeishuMessage
from tianshu.gateway.feishu.edict_bridge import EdictBridge, EdictBusyError
from tianshu.gateway.feishu.outbound import FeishuOutbound
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tianshu.bus.event_bus import EventBus
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.executor.executor import Executor
    from tianshu.notifier.notifier import Notifier
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class FeishuBot:
    """飞书机器人门面 —— 协调 connection / dispatcher / outbound。"""

    def __init__(
        self,
        *,
        storage: "Storage",
        event_bus: "EventBus",
        approval_manager: "ApprovalManager",
        executor: "Executor",
        notifier: "Notifier",
        settings: FeishuSettings,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._executor = executor
        self._notifier = notifier
        self._settings = settings
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._connection: WebhookConnection | WebSocketConnection | None = None
        self._dispatcher: Dispatcher | None = None
        self._anchor = SessionAnchor(storage)
        self._edict_bridge = EdictBridge(
            storage=storage,
            event_bus=event_bus,
            executor=executor,
            anchor=self._anchor,
        )
        self._outbound = FeishuOutbound(
            settings=settings,
            storage=storage,
            event_bus=event_bus,
        )
        self._approval_card = ApprovalCardHandler(
            settings=settings,
            storage=storage,
            event_bus=event_bus,
            approval_manager=approval_manager,
            outbound=self._outbound,
        )

    async def start(self) -> None:
        logger.info(
            "[feishu] starting (mode=%s, app=%s)",
            self._settings.connection_mode,
            self._settings.app_id,
        )
        self._acquire_app_lock()
        if self._settings.connection_mode == "websocket":
            loop = asyncio.get_running_loop()
            self._connection = WebSocketConnection(
                settings=self._settings,
                storage=self._storage,
                inbound_queue=self._inbound,
                loop=loop,
            )
        else:
            self._connection = WebhookConnection(
                settings=self._settings,
                storage=self._storage,
                inbound_queue=self._inbound,
            )
        await self._connection.start()

        self._dispatcher = Dispatcher(
            settings=self._settings,
            inbound_queue=self._inbound,
            message_handler=self._on_message,
            card_handler=self._on_card,
        )
        await self._dispatcher.start()
        self._outbound.start()
        self._approval_card.start()

    async def stop(self) -> None:
        logger.info("[feishu] stopping")
        if self._dispatcher:
            await self._dispatcher.stop()
        if self._connection:
            await self._connection.stop()
        self._release_app_lock()

    def _acquire_app_lock(self) -> None:
        """启动时占进程锁，避免双开同一 app_id。"""
        lock_path = Path.home() / ".tianshu" / f"feishu_app_lock.{self._settings.app_id}"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text().strip())
            except (ValueError, OSError):
                pid = 0
            if pid > 0:
                try:
                    os.kill(pid, 0)  # 检查进程是否存活
                    raise RuntimeError(
                        f"Another tianshu process (pid={pid}) is using "
                        f"feishu app {self._settings.app_id}; "
                        f"lock file: {lock_path}"
                    )
                except ProcessLookupError:
                    # 锁文件残留，清理
                    logger.warning(
                        "[feishu] stale lock file detected (pid=%d not alive), cleaning up",
                        pid,
                    )
        lock_path.write_text(str(os.getpid()))
        self._lock_path = lock_path

    def _release_app_lock(self) -> None:
        lock_path = getattr(self, "_lock_path", None)
        if lock_path is not None and lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                logger.exception("[feishu] failed to remove app lock file")

    def attach_webhook_router(self, app: "FastAPI") -> None:
        """Webhook 模式：把路由挂到 FastAPI app。"""
        if self._connection and isinstance(self._connection, WebhookConnection):
            app.include_router(self._connection.router)

    async def _on_message(self, msg: FeishuMessage) -> None:
        logger.info("[feishu/inbound] chat=%s sender=%s text=%.80s",
                    msg.chat_id, msg.sender_open_id, msg.text)
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0] if parts else ""

        if cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            if not goal:
                await self._reply(msg.chat_id, "用法：/new <目标描述>")
                return
            edict_id = await self._edict_bridge.create_new(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, goal=goal,
            )
            await self._reply(msg.chat_id, f"✅ 新敕令 #{edict_id[:8]} 已创建")
            return

        if cmd == "/status":
            target = parts[1].strip() if len(parts) > 1 else (self._anchor.get(msg.chat_id) or "")
            if not target:
                await self._reply(msg.chat_id, "当前会话无活跃敕令。用 /new 创建一个。")
                return
            edict = self._storage.get_edict(target)
            if not edict:
                await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
                return
            await self._reply(
                msg.chat_id,
                f"敕令 #{edict.id[:8]}\n标题：{edict.title}\n状态：{edict.status}",
            )
            return

        if cmd == "/cancel":
            target = parts[1].strip() if len(parts) > 1 else (self._anchor.get(msg.chat_id) or "")
            if not target:
                await self._reply(msg.chat_id, "用法：/cancel [edict_id]")
                return
            edict = self._storage.get_edict(target)
            if not edict:
                await self._reply(msg.chat_id, f"敕令 #{target[:8]} 不存在")
                return
            edict.status = EdictStatus.CANCELLED
            self._storage.save_edict(edict)
            await self._reply(msg.chat_id, f"✅ 敕令 #{edict.id[:8]} 已取消")
            return

        if cmd == "/set-home":
            await self._reply(
                msg.chat_id,
                f"当前 chat_id = `{msg.chat_id}`\n"
                f"请将其设置到 `TIANSHU_FEISHU_HOME_CHANNEL` 环境变量后重启服务。",
            )
            return

        if cmd == "/help":
            await self._reply(
                msg.chat_id,
                "可用命令：\n"
                "- `/new <目标>` 显式新建敕令\n"
                "- `/status [敕令id]` 查看当前/指定敕令状态\n"
                "- `/cancel [敕令id]` 取消敕令\n"
                "- `/set-home` 显示当前 chat_id（用于配置 home channel）\n"
                "- `/help` 显示帮助\n\n"
                "默认行为：纯文本消息会续接当前会话锚定的敕令。",
            )
            return

        if cmd.startswith("/"):
            await self._reply(msg.chat_id, f"未知命令：{cmd}。输入 /help 查看帮助。")
            return

        # 默认：续接或自动新建
        try:
            edict_id = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_open_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._reply(msg.chat_id, f"✅ 已收到（敕令 #{edict_id[:8]}）")

    async def _on_card(self, action: FeishuCardAction) -> None:
        logger.info("[feishu/card] chat=%s value=%s", action.chat_id, action.value)
        await self._approval_card.handle_button_click(action)

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)


__all__ = ["FeishuBot"]
