"""Telegram 审批 inline keyboard：出站审批消息 + 按钮点击 + 双通道作废刷新。

镜像 feishu/approval_card.py，但用 InlineKeyboardMarkup + callback_query 取代 lark 卡片。
callback_data 协议（≤64 字节，memorial_id 为 26 字符 ULID，总长可容纳）：
  "ea:approve:once:<memorial_id>" / ":edict:" / ":always:" / "ea:reject::<memorial_id>"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.telegram.settings import TelegramSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.gateway.telegram.dispatcher import TelegramCallback
    from tianshu.gateway.telegram.outbound import TelegramOutbound

logger = logging.getLogger(__name__)


def build_approval_message(
    *,
    memorial_id: str,
    edict_id: str,
    tool_name: str,
    args_summary: dict | None,
    reason: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """构造审批消息文本 + 4 按钮键盘。"""
    summary_lines = []
    if args_summary:
        for k, v in list(args_summary.items())[:5]:
            summary_lines.append(f"- **{k}**：`{v}`")
    summary_md = "\n".join(summary_lines) or "_(无参数摘要)_"
    body = (
        f"🛡️ **待审批：{tool_name}**\n\n"
        f"**敕令** `#{edict_id[:8]}` · **memorial** `#{memorial_id[:8]}`\n"
        f"**原因**：{reason}\n\n{summary_md}\n\n"
        f"_也可用文本命令：_ `/approve` `/准` `/准敕` `/准永` `/reject` `/驳`"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 单次", callback_data=f"ea:approve:once:{memorial_id}"),
                InlineKeyboardButton("📋 本敕令", callback_data=f"ea:approve:edict:{memorial_id}"),
            ],
            [
                InlineKeyboardButton("♾️ 总是", callback_data=f"ea:approve:always:{memorial_id}"),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"ea:reject::{memorial_id}"),
            ],
        ]
    )
    return body, kb


class ApprovalKeyboardHandler:
    def __init__(
        self,
        *,
        settings: TelegramSettings,
        storage: Storage,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        outbound: TelegramOutbound,
        instance_id: str = "telegram-default",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._approval = approval_manager
        self._outbound = outbound
        self._instance_id = instance_id
        # 保存订阅引用：EventBus.off 用 `is` 比对，bound method 每次取属性都是新对象。
        self._sub_approval_required = self._on_approval_required
        self._sub_decree_resolved = self._on_decree_resolved

    def start(self) -> None:
        self._event_bus.on("tool.approval_required", self._sub_approval_required, priority=200)
        self._event_bus.on("decree.approved", self._sub_decree_resolved, priority=200)
        self._event_bus.on("decree.rejected", self._sub_decree_resolved, priority=200)

    def stop(self) -> None:
        """取消 EventBus 订阅（实例停止时调用）。"""
        self._event_bus.off("tool.approval_required", self._sub_approval_required)
        self._event_bus.off("decree.approved", self._sub_decree_resolved)
        self._event_bus.off("decree.rejected", self._sub_decree_resolved)

    async def _on_approval_required(self, event: EventEnvelope) -> None:
        edict_id = event.edict_id
        memorial_id = event.memorial_id
        if not (edict_id and memorial_id):
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            return
        # 实例路由隔离：非本实例的敕令不在本 handler 推审批（避免与飞书/其它实例重复/错投）。
        # 存量敕令无 instance_id → 回退 {channel}-default；无 channel 敕令（inst=None）
        # 不被实例守卫拦截，仍走 home_channel 兜底。
        inst = (edict.metadata or {}).get("instance_id")
        if inst is None:
            ch = (edict.metadata or {}).get("channel")
            inst = f"{ch}-default" if ch else None
        if inst is not None and inst != self._instance_id:
            return
        chat_id = (edict.metadata or {}).get("chat_id") or self._settings.home_channel
        if not chat_id:
            anchored = self._storage.list_telegram_chats_anchored_to(
                edict_id, instance_id=self._instance_id
            )
            if anchored:
                chat_id = anchored[0]
        if not chat_id:
            logger.warning(
                "[telegram/approval] no chat_id for edict %s; approval not delivered "
                "to telegram (web 端仍可处理)",
                edict_id,
            )
            return
        payload = event.payload or {}
        card = build_approval_message(
            memorial_id=memorial_id,
            edict_id=edict_id,
            tool_name=payload.get("tool_name", "unknown"),
            args_summary=payload.get("args_summary"),
            reason=payload.get("reason", ""),
        )
        message_id = await self._outbound.send_card(chat_id, card)
        if message_id:
            self._storage.save_telegram_pending_button(
                approval_id=memorial_id,
                chat_id=chat_id,
                message_id=message_id,
                kind="tool.approval_required",
                instance_id=self._instance_id,
            )
            logger.info(
                "[telegram/approval] sent edict=%s memorial=%s chat=%s msg=%s",
                edict_id,
                memorial_id,
                chat_id,
                message_id,
            )

    async def handle_callback(self, cb: TelegramCallback) -> str:
        """处理 ea: 按钮点击 → submit_tool_decision → 刷新消息。返回 answer 弹窗文本。"""
        # data: ea:<action>:<scope>:<memorial_id>
        parts = cb.data.split(":", 3)
        if len(parts) < 4:
            return "无效操作"
        _, action, scope, memorial_id = parts
        if action not in ("approve", "reject"):
            return "无效操作"
        try:
            await self._approval.submit_tool_decision(
                memorial_id=memorial_id,
                action=action,
                grant_scope=scope if (action == "approve" and scope) else None,
                actor=f"telegram:{cb.sender_id}",
            )
        except ValueError as exc:
            logger.info("[telegram/approval] submit skipped: %s", exc)
            # 仍刷新消息去掉按钮（已被其他通道响应）
            await self._refresh_resolved(memorial_id, cb, "已被其他通道响应", "approve")
            return "已被其他通道响应"
        label = "已批准" if action == "approve" else "已拒绝"
        await self._refresh_resolved(memorial_id, cb, label, action)
        return f"✅ {label}" if action == "approve" else f"❌ {label}"

    async def _refresh_resolved(
        self,
        memorial_id: str,
        cb: TelegramCallback,
        label: str,
        action: str,
    ) -> None:
        """编辑原审批消息：去按钮 + 标注结果。"""
        self._storage.pop_telegram_pending_button(memorial_id)
        icon = "✅" if action == "approve" else "❌"
        await self._outbound.edit_message(
            cb.chat_id,
            cb.message_id,
            f"{icon} **{label}** · memorial `#{memorial_id[:8]}`\n_已在 Telegram 处响应。_",
            reply_markup=None,
        )

    async def _on_decree_resolved(self, event: EventEnvelope) -> None:
        """另一通道（web/飞书）响应 → 刷新本侧审批消息为已响应。"""
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        pending = self._storage.pop_telegram_pending_button(memorial_id)
        if not pending:
            return
        action = "approve" if event.event_type == "decree.approved" else "reject"
        payload = event.payload or {}
        actor = payload.get("actor") or ""
        if actor.startswith("telegram:"):
            source = "Telegram"
        elif actor.startswith("feishu:"):
            source = "飞书"
        else:
            source = "web"
        icon = "✅" if action == "approve" else "❌"
        label = "已批准" if action == "approve" else "已拒绝"
        await self._outbound.edit_message(
            pending["chat_id"],
            pending["message_id"],
            f"{icon} **{label}** · memorial `#{memorial_id[:8]}`\n_已在 **{source}** 处响应。_",
            reply_markup=None,
        )


__all__ = ["ApprovalKeyboardHandler", "build_approval_message"]
