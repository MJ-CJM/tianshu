"""Telegram 审批 inline keyboard：出站审批消息 + 按钮点击 + 双通道作废刷新。

镜像 feishu/approval_card.py，但用 InlineKeyboardMarkup + callback_query 取代 lark 卡片。
callback_data 协议（≤64 字节，decision_request_id 为 26 字符 ULID）：
  "ea:approve:once:<decision_id>" / ":edict:" / ":always:" / "ea:reject::<decision_id>"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.core.approval import (
    build_webhook_decision_auth,
    is_canonical_decision_request_id,
)
from tianshu.gateway.telegram.security import is_allowed_user
from tianshu.gateway.telegram.settings import TelegramSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.gateway.telegram.dispatcher import TelegramCallback
    from tianshu.gateway.telegram.outbound import TelegramOutbound

logger = logging.getLogger(__name__)


def build_approval_message(
    *,
    decision_request_id: str,
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
                InlineKeyboardButton(
                    "✅ 单次", callback_data=f"ea:approve:once:{decision_request_id}"
                ),
                InlineKeyboardButton(
                    "📋 本敕令", callback_data=f"ea:approve:edict:{decision_request_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "♾️ 总是", callback_data=f"ea:approve:always:{decision_request_id}"
                ),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"ea:reject::{decision_request_id}"),
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
        self._event_bus.on(
            "tool.approval_required",
            self._sub_approval_required,
            consumer_name=f"telegram.approval.{self._instance_id}.required.v1",
            priority=200,
        )
        self._event_bus.on(
            "decree.approved",
            self._sub_decree_resolved,
            consumer_name=f"telegram.approval.{self._instance_id}.resolved.v1",
            priority=200,
        )
        self._event_bus.on(
            "decree.rejected",
            self._sub_decree_resolved,
            consumer_name=f"telegram.approval.{self._instance_id}.resolved.v1",
            priority=200,
        )

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
        decision_request_id = payload.get("decision_request_id")
        if not isinstance(decision_request_id, str) or not is_canonical_decision_request_id(
            decision_request_id
        ):
            logger.warning("[telegram/approval] non-actionable event without canonical decision id")
            return
        card = build_approval_message(
            decision_request_id=decision_request_id,
            memorial_id=memorial_id,
            edict_id=edict_id,
            tool_name=payload.get("tool_name", "unknown"),
            args_summary=payload.get("args_summary"),
            reason=payload.get("reason", ""),
        )
        claimed = self._storage.claim_telegram_pending_button(
            approval_id=decision_request_id,
            instance_id=self._instance_id,
            chat_id=chat_id,
            kind="tool.approval_required",
        )
        if not claimed:
            return
        try:
            message_id = await self._outbound.send_card(chat_id, card)
        except BaseException:
            self._storage.release_telegram_pending_button_claim(
                decision_request_id, self._instance_id
            )
            raise
        if not message_id:
            self._storage.release_telegram_pending_button_claim(
                decision_request_id, self._instance_id
            )
            return
        finalized = self._storage.finalize_telegram_pending_button(
            approval_id=decision_request_id,
            instance_id=self._instance_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        if finalized:
            logger.info(
                "[telegram/approval] sent edict=%s decision=%s chat=%s msg=%s",
                edict_id,
                decision_request_id,
                chat_id,
                message_id,
            )
            await self._refresh_durable_decision(decision_request_id)
        else:
            logger.error(
                "[telegram/approval] lost pending-button claim decision=%s",
                decision_request_id,
            )

    async def handle_callback(self, cb: TelegramCallback) -> str:
        """Resolve an inline button by durable decision ID and show the winner."""
        if not cb.sender_id or len(cb.data.encode("utf-8")) > 64:
            return "无效操作"
        try:
            sender_id = int(cb.sender_id)
        except (TypeError, ValueError):
            return "无效操作"
        if not is_allowed_user(sender_id, self._settings.allowed_users):
            return "无效操作"
        # data: ea:<action>:<scope>:<decision_request_id>
        parts = cb.data.split(":", 3)
        if len(parts) < 4:
            return "无效操作"
        _, action, scope, decision_request_id = parts
        if (
            not isinstance(decision_request_id, str)
            or not is_canonical_decision_request_id(decision_request_id)
            or action not in ("approve", "reject")
            or (action == "approve" and scope not in ("once", "edict", "always"))
            or (action == "reject" and scope)
        ):
            return "无效操作"
        pending = self._storage.get_telegram_pending_button(
            decision_request_id,
            instance_id=self._instance_id,
        )
        if (
            pending is None
            or pending["kind"] != "tool.approval_required"
            or pending["chat_id"] != cb.chat_id
            or pending["message_id"] != cb.message_id
        ):
            return "无效操作"
        try:
            record = await self._approval.resolve_tool_decision(
                decision_request_id,
                action=action,
                grant_scope=scope if (action == "approve" and scope) else None,
                auth=build_webhook_decision_auth(
                    channel="telegram",
                    instance_id=self._instance_id,
                    chat_id=cb.chat_id,
                    sender_id=cb.sender_id,
                    decision_request_id=decision_request_id,
                    correlation_prefix="approval-button",
                ),
            )
        except (ValueError, RuntimeError) as exc:
            logger.info("[telegram/approval] submit skipped: %s", exc)
            return "已被其他通道响应"
        resolution = record.resolution
        if resolution is None:
            return "裁决尚未完成"
        winner_action = resolution.action
        source = self._decision_source(resolution.actor_principal_id)
        label = "已批准" if winner_action == "approve" else "已拒绝"
        await self._refresh_resolved(decision_request_id, cb, label, winner_action, source)
        if winner_action != action:
            return f"已被其他通道{'批准' if winner_action == 'approve' else '拒绝'}"
        if (
            action == "approve"
            and scope == "always"
            and resolution.payload.get("grant_scope") == "once"
        ):
            return "✅ 已批准（已降级为单次）"
        return f"✅ {label}" if winner_action == "approve" else f"❌ {label}"

    async def _refresh_resolved(
        self,
        decision_request_id: str,
        cb: TelegramCallback,
        label: str,
        action: str,
        source: str,
    ) -> None:
        """编辑原审批消息：去按钮 + 标注结果。"""
        pending = self._storage.pop_telegram_pending_button(
            decision_request_id,
            instance_id=self._instance_id,
        )
        if pending is None:
            return
        icon = "✅" if action == "approve" else "❌"
        await self._outbound.edit_message(
            pending["chat_id"],
            pending["message_id"],
            f"{icon} **{label}** · 裁决 `#{decision_request_id[:8]}`\n_已在 **{source}** 处响应。_",
            reply_markup=None,
        )

    async def _on_decree_resolved(self, event: EventEnvelope) -> None:
        """另一通道（web/飞书）响应 → 刷新本侧审批消息为已响应。"""
        payload = event.payload or {}
        decision_request_id = payload.get("decision_request_id")
        if not isinstance(decision_request_id, str) or not is_canonical_decision_request_id(
            decision_request_id
        ):
            return
        await self._refresh_durable_decision(
            decision_request_id,
            expected_event_type=event.event_type,
        )

    async def _refresh_durable_decision(
        self,
        decision_request_id: str,
        *,
        expected_event_type: str | None = None,
    ) -> None:
        """Project the durable winner after an artifact becomes addressable."""

        record = self._approval.get_tool_decision(decision_request_id)
        if record is None or record.resolution is None:
            return
        action = record.resolution.action
        expected_event = {
            "approve": "decree.approved",
            "reject": "decree.rejected",
        }.get(action)
        if expected_event is None or (
            expected_event_type is not None and expected_event != expected_event_type
        ):
            return
        pending = self._storage.pop_telegram_pending_button(
            decision_request_id,
            instance_id=self._instance_id,
        )
        if not pending:
            return
        actor = record.resolution.actor_principal_id
        source = self._decision_source(actor)
        icon = "✅" if action == "approve" else "❌"
        label = "已批准" if action == "approve" else "已拒绝"
        await self._outbound.edit_message(
            pending["chat_id"],
            pending["message_id"],
            f"{icon} **{label}** · 裁决 `#{decision_request_id[:8]}`\n_已在 **{source}** 处响应。_",
            reply_markup=None,
        )

    @staticmethod
    def _decision_source(actor_principal_id: str) -> str:
        if actor_principal_id.startswith("telegram:"):
            return "Telegram"
        if actor_principal_id.startswith("feishu:"):
            return "飞书"
        return "web"


__all__ = ["ApprovalKeyboardHandler", "build_approval_message"]
