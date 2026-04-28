"""Feishu (Lark) 机器人接入：双向入口 + 双通道审批。

设计文档：docs/superpowers/specs/2026-04-28-feishu-bot-design.md
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.settings import FeishuSettings

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.executor.approvals import ApprovalManager
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
        notifier: "Notifier",
        settings: FeishuSettings,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._notifier = notifier
        self._settings = settings
        # 后续 step 会在这里挂 connection / dispatcher / outbound

    async def start(self) -> None:
        """生命周期启动：启动连接 + 注册事件订阅 + 初始化 anchor 表。"""
        logger.info(
            "[feishu] starting bot (mode=%s, app=%s)",
            self._settings.connection_mode,
            self._settings.app_id,
        )
        # Step 2 起补全

    async def stop(self) -> None:
        logger.info("[feishu] stopping bot")
        # Step 6 起补全


__all__ = ["FeishuBot"]
