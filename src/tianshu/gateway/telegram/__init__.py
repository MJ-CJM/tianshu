"""Telegram 机器人接入：与飞书并列的双向入口 + 双模式（助手 / 敕令）+ inline keyboard 审批。

设计文档：docs/superpowers/plans/2026-05-29-telegram-bot.md

架构：镜像 gateway/feishu/ 结构，复用平台无关核心（EdictBridge / PersonaRenderer /
parse_approval_command / markdown_compat / ApprovalManager / Executor / Storage），
重写平台层（python-telegram-bot 连接、出站、inline keyboard 审批/卡片）。
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

# 复用平台无关核心
from tianshu.gateway.feishu.edict_bridge import EdictBridge, EdictBusyError
from tianshu.gateway.feishu.persona_renderer import PersonaRenderer
from tianshu.gateway.telegram.approval_commands import TelegramApprovalCommandHandler
from tianshu.gateway.telegram.approval_kb import ApprovalKeyboardHandler
from tianshu.gateway.telegram.assistant_branch import AssistantBranch
from tianshu.gateway.telegram.card_action_dispatcher import CallbackDispatcher
from tianshu.gateway.telegram.card_builder import TelegramCardBuilder
from tianshu.gateway.telegram.connection import TelegramConnection
from tianshu.gateway.telegram.dispatcher import (
    Dispatcher,
    TelegramCallback,
    TelegramMessage,
)
from tianshu.gateway.telegram.edict_branch import EdictBranch
from tianshu.gateway.telegram.mode_router import ModeRouter
from tianshu.gateway.telegram.outbound import TelegramOutbound
from tianshu.gateway.telegram.session_anchor import SessionAnchor
from tianshu.gateway.telegram.settings import TelegramSettings
from tianshu.models.common import EdictStatus

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tianshu.bus.event_bus import EventBus
    from tianshu.cost.manager import CostManager
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.executor.executor import Executor
    from tianshu.notifier.notifier import Notifier
    from tianshu.persona.loader import PersonaLoader
    from tianshu.providers.manager import ProviderManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram 机器人门面 —— 协调 connection / dispatcher / outbound / 双模式分支。"""

    def __init__(
        self,
        *,
        storage: Storage,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        executor: Executor,
        notifier: Notifier,
        settings: TelegramSettings,
        persona_loader: PersonaLoader | None = None,
        provider_manager: ProviderManager | None = None,
        cost_manager: CostManager | None = None,
        instance_id: str = "telegram-default",
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._executor = executor
        self._notifier = notifier
        self._settings = settings
        self._persona_loader = persona_loader
        self._provider_manager = provider_manager
        self._cost_manager = cost_manager
        self._instance_id = instance_id

        self._connection: TelegramConnection | None = None
        self._anchor = SessionAnchor(storage, instance_id=instance_id)
        self._edict_bridge = EdictBridge(
            storage=storage,
            event_bus=event_bus,
            executor=executor,
            anchor=self._anchor,
            channel="telegram",
            instance_id=instance_id,
            user_meta_key="telegram_user",
            chat_title_prefix="Telegram 助手对话",
        )
        self._outbound = TelegramOutbound(
            settings=settings, storage=storage, event_bus=event_bus,
            instance_id=instance_id,
        )
        self._approval_commands = TelegramApprovalCommandHandler(
            storage=storage, approval_manager=approval_manager,
            instance_id=instance_id,
        )

        persona = None
        if persona_loader is not None:
            persona = persona_loader.get(settings.assistant_persona_id)
        self._renderer = PersonaRenderer(persona)

        self._card_builder = TelegramCardBuilder(
            storage=storage, cost_manager=cost_manager,
        )

        self._assistant_branch = AssistantBranch(
            storage=storage,
            anchor=self._anchor,
            edict_bridge=self._edict_bridge,
            outbound=self._outbound,
            renderer=self._renderer,
            card_builder=self._card_builder,
            approval_commands=self._approval_commands,
            assistant_persona_id=settings.assistant_persona_id,
            instance_id=instance_id,
        )
        self._edict_branch = EdictBranch(
            storage=storage,
            anchor=self._anchor,
            edict_bridge=self._edict_bridge,
            outbound=self._outbound,
            renderer=self._renderer,
            assistant_branch=self._assistant_branch,
            approval_commands=self._approval_commands,
            assistant_persona_id=settings.assistant_persona_id,
            instance_id=instance_id,
        )
        self._mode_router = ModeRouter(
            anchor=self._anchor,
            assistant_branch=self._assistant_branch,
            edict_branch=self._edict_branch,
            edict_bridge=self._edict_bridge,
            storage=storage,
            settings=settings,
        )
        self._approval_kb = ApprovalKeyboardHandler(
            settings=settings,
            storage=storage,
            event_bus=event_bus,
            approval_manager=approval_manager,
            outbound=self._outbound,
            instance_id=instance_id,
        )
        self._callback_dispatcher = CallbackDispatcher(
            mode_router=self._mode_router,
            approval_kb=self._approval_kb,
            outbound=self._outbound,
        )
        self._dispatcher = Dispatcher(
            settings=settings,
            storage=storage,
            message_handler=self._on_message,
            callback_handler=self._on_callback,
        )

    # --- 生命周期 ---

    async def start(self) -> None:
        logger.info(
            "[telegram] starting (mode=%s)", self._settings.connection_mode,
        )
        if not self._settings.allowed_users:
            logger.warning(
                "[telegram] allowlist is empty — bot will respond to ANY user. "
                "For production, set TIANSHU_TELEGRAM_ALLOWED_USERS or fill 「允许用户」"
                "on the 通政司 page.",
            )
        if self._settings.disable_assistant_mode:
            logger.warning(
                "[telegram] disable_assistant_mode=True — running in legacy mode "
                "(no ModeRouter / AssistantBranch). For emergency escape only.",
            )
        self._acquire_app_lock()
        self._connection = TelegramConnection(
            settings=self._settings, dispatcher=self._dispatcher,
        )
        await self._connection.start()
        self._outbound.start()
        self._approval_kb.start()

    async def stop(self) -> None:
        logger.info("[telegram] stopping")
        if self._connection:
            await self._connection.stop()
        await self._dispatcher.stop()
        self._release_app_lock()
        # 取消 EventBus 订阅，避免已停实例继续投递回执 / 审批消息
        self._outbound.stop()
        self._approval_kb.stop()

    async def reload(self, new_settings: TelegramSettings) -> None:
        """热加载新 settings：重建 connection；保持 outbound/approval_kb 订阅。"""
        logger.info(
            "[telegram] reloading (old_mode=%s -> new_mode=%s)",
            self._settings.connection_mode, new_settings.connection_mode,
        )
        if self._connection:
            await self._connection.stop()
            self._connection = None

        old_token = self._settings.bot_token
        if old_token != new_settings.bot_token:
            self._release_app_lock()

        self._settings = new_settings
        if old_token != new_settings.bot_token:
            self._acquire_app_lock()

        # 重建 connection（dispatcher 复用同实例，但需切 settings 引用）
        self._dispatcher._settings = new_settings  # type: ignore[attr-defined]
        self._connection = TelegramConnection(
            settings=new_settings, dispatcher=self._dispatcher,
        )
        await self._connection.start()

        # outbound：仅重建 Bot，不重订阅 EventBus
        self._outbound._settings = new_settings  # type: ignore[attr-defined]
        self._outbound.rebuild_client()
        self._approval_kb._settings = new_settings  # type: ignore[attr-defined]

        # persona renderer 切换
        new_persona = None
        if self._persona_loader is not None:
            new_persona = self._persona_loader.get(new_settings.assistant_persona_id)
        new_renderer = PersonaRenderer(new_persona)
        self._renderer = new_renderer
        self._assistant_branch.set_renderer(new_renderer)
        self._edict_branch.set_renderer(new_renderer)
        self._assistant_branch.set_assistant_persona_id(new_settings.assistant_persona_id)
        self._edict_branch.set_assistant_persona_id(new_settings.assistant_persona_id)
        self._mode_router._settings = new_settings  # type: ignore[attr-defined]
        logger.info("[telegram] reload complete (mode=%s)", new_settings.connection_mode)

    def attach_webhook_router(self, app: FastAPI) -> None:
        """Webhook 模式：把路由挂到 FastAPI app。"""
        if self._connection and self._settings.connection_mode == "webhook":
            app.include_router(self._connection.router)

    # --- 入站 handlers ---

    async def _on_message(self, msg: TelegramMessage) -> None:
        if self._settings.disable_assistant_mode:
            await self._on_message_legacy(msg)
            return
        await self._mode_router.dispatch(msg)

    async def _on_callback(self, cb: TelegramCallback) -> None:
        await self._callback_dispatcher.handle(cb)

    # --- legacy fallback（仅 disable_assistant_mode=True）---

    async def _on_message_legacy(self, msg: TelegramMessage) -> None:
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""

        if cmd == "/new":
            goal = parts[1].strip() if len(parts) > 1 else ""
            if not goal:
                await self._reply(msg.chat_id, "用法：/new <目标描述>")
                return
            result = await self._edict_bridge.create_new(
                chat_id=msg.chat_id, sender_open_id=msg.sender_id, goal=goal,
            )
            await self._reply(msg.chat_id, f"✅ 新敕令 #{result.edict_id[:8]} 已创建")
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
                msg.chat_id, f"敕令 #{edict.id[:8]}\n标题：{edict.title}\n状态：{edict.status}",
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
            self._storage.update_edict_status(edict.id, EdictStatus.CANCELLED.value)
            self._storage.update_edict_lifecycle_phase(edict.id, "complete")
            await self._reply(msg.chat_id, f"✅ 敕令 #{edict.id[:8]} 已取消")
            return
        if cmd in ("/help", "/start"):
            await self._reply(
                msg.chat_id,
                "可用命令：\n- `/new <目标>` 新建敕令\n- `/status [id]` 查状态\n"
                "- `/cancel [id]` 取消\n- `/help` 帮助\n\n默认：纯文本续接当前敕令。",
            )
            return
        if cmd.startswith("/"):
            await self._reply(msg.chat_id, f"未知命令：{cmd}。输入 /help 查看帮助。")
            return
        try:
            result = await self._edict_bridge.continue_or_create(
                chat_id=msg.chat_id, sender_open_id=msg.sender_id, text=text,
            )
        except EdictBusyError as exc:
            await self._reply(msg.chat_id, str(exc))
            return
        await self._reply(msg.chat_id, f"✅ 已收到（敕令 #{result.edict_id[:8]}）")

    async def _reply(self, chat_id: str, text: str) -> None:
        await self._outbound.send_text(chat_id, text)

    # --- app lock（避免双开同一 bot token 轮询冲突 409）---

    def _acquire_app_lock(self) -> None:
        token_hash = hashlib.sha256(self._settings.bot_token.encode()).hexdigest()[:12]
        lock_path = Path.home() / ".tianshu" / f"telegram_app_lock.{token_hash}"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text().strip())
            except (ValueError, OSError):
                pid = 0
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    raise RuntimeError(
                        f"Another tianshu process (pid={pid}) is using this telegram "
                        f"bot token; lock file: {lock_path}"
                    )
                except ProcessLookupError:
                    logger.warning(
                        "[telegram] stale lock file detected (pid=%d not alive), cleaning up",
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
                logger.exception("[telegram] failed to remove app lock file")


__all__ = ["TelegramBot"]
