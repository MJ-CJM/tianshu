"""出站审批卡片 + 入站 card.action.trigger 处理 + 双通道作废。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.core.approval import (
    build_webhook_decision_auth,
    is_canonical_decision_request_id,
)
from tianshu.gateway.feishu.dispatcher import FeishuCardAction
from tianshu.gateway.feishu.security import is_allowed_user
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.gateway.feishu.outbound import FeishuOutbound

logger = logging.getLogger(__name__)


def build_approval_card(
    *,
    decision_request_id: str,
    memorial_id: str,
    edict_id: str,
    tool_name: str,
    args_summary: dict | None,
    reason: str,
) -> dict:
    """构造审批卡片 payload（v2 极简模型：纯 markdown，飞书 ws 不支持卡片回调）。

    用户用文本命令响应：/approve /reject 或中文 /准 /驳。
    chat 内多 pending 时，命令需附带 memorial_id 前缀。
    """
    summary_lines = []
    if args_summary:
        for k, v in list(args_summary.items())[:5]:
            summary_lines.append(f"- **{k}**：`{v}`")
    summary_md = "\n".join(summary_lines) or "_(无参数摘要)_"

    short_id = decision_request_id[:8]
    body = (
        f"**敕令** `#{edict_id[:8]}` · **memorial** `#{memorial_id[:8]}`\n"
        f"**原因**：{reason}\n\n{summary_md}\n\n"
        f"---\n"
        f"**请回复**（中英任选）：\n"
        f"- `/approve` 或 `/准` — 单次允许\n"
        f"- `/approve edict` 或 `/准敕` — 本敕令允许\n"
        f"- `/approve always` 或 `/准永` — 总是允许\n"
        f"- `/reject` 或 `/驳` — 拒绝\n\n"
        f"_chat 内多个待审批时，请附裁决短 ID：_ `/approve {short_id}`"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"🛡️ 待审批：{tool_name}"},
        },
        "elements": [
            {"tag": "markdown", "content": body},
        ],
    }


def build_resolved_card(*, tool_name: str, source: str, action: str) -> dict:
    """已响应的卡片：按钮置灰，标题刷新。"""
    icon = "✅" if action == "approve" else "❌"
    label = "批准" if action == "approve" else "拒绝"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": f"{icon} 已{label}：{tool_name}"},
        },
        "elements": [
            {"tag": "markdown", "content": f"_已在 **{source}** 处响应。_"},
        ],
    }


class ApprovalCardHandler:
    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        outbound: FeishuOutbound,
        instance_id: str = "feishu-default",
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
        """订阅 EventBus：tool.approval_required → 下发卡片；decree.* → 刷新。

        注意：tool.approval_required 在 PolicyHook 内 fire 到 EventBus（修订 1）。
        """
        self._event_bus.on(
            "tool.approval_required",
            self._sub_approval_required,
            consumer_name=f"feishu.approval.{self._instance_id}.required.v1",
            priority=200,
        )
        self._event_bus.on(
            "decree.approved",
            self._sub_decree_resolved,
            consumer_name=f"feishu.approval.{self._instance_id}.resolved.v1",
            priority=200,
        )
        self._event_bus.on(
            "decree.rejected",
            self._sub_decree_resolved,
            consumer_name=f"feishu.approval.{self._instance_id}.resolved.v1",
            priority=200,
        )

    def stop(self) -> None:
        """取消 EventBus 订阅（实例停止时调用）。"""
        self._event_bus.off("tool.approval_required", self._sub_approval_required)
        self._event_bus.off("decree.approved", self._sub_decree_resolved)
        self._event_bus.off("decree.rejected", self._sub_decree_resolved)

    async def _on_approval_required(self, event: EventEnvelope) -> None:
        """tool.approval_required → 找 chat_id → 下发卡片 → 记录 pending_card。

        chat_id fallback 链：
          1. edict.metadata.chat_id（飞书发起的敕令）
          2. settings.home_channel（用户配置）
          3. storage.list_active_anchor_chats() 首个（兜底：让 web 创建的敕令也能审批）
        """
        edict_id = event.edict_id
        memorial_id = event.memorial_id
        if not (edict_id and memorial_id):
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            return
        # 实例路由隔离：非本实例的敕令不由本 handler 下卡片，避免交叉投递。
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
            # 精准反查：哪个 chat 当前 anchor 指向这个 edict（web 创建 + 飞书 /select 场景）
            anchored = self._storage.list_chats_anchored_to(edict_id, instance_id=self._instance_id)
            if anchored:
                chat_id = anchored[0]
                logger.info(
                    "[feishu/approval] edict %s has no chat_id, fallback to anchored chat=%s",
                    edict_id,
                    chat_id,
                )
        if not chat_id:
            logger.warning(
                "[feishu/approval] no chat_id for edict %s (no metadata, no home_channel, "
                "no active anchor); approval card not delivered to feishu (web 端仍可处理)",
                edict_id,
            )
            return
        payload = event.payload or {}
        decision_request_id = payload.get("decision_request_id")
        if not is_canonical_decision_request_id(decision_request_id):
            logger.warning("[feishu/approval] non-actionable event without canonical decision id")
            return
        card = build_approval_card(
            decision_request_id=decision_request_id,
            memorial_id=memorial_id,
            edict_id=edict_id,
            tool_name=payload.get("tool_name", "unknown"),
            args_summary=payload.get("args_summary"),
            reason=payload.get("reason", ""),
        )
        message_id = await self._outbound.send_card(chat_id, card)
        if message_id:
            self._storage.save_feishu_pending_card(
                approval_id=decision_request_id,
                chat_id=chat_id,
                message_id=message_id,
                kind="tool.approval_required",
                instance_id=self._instance_id,
            )
            logger.info(
                "[feishu/approval] card sent edict=%s decision=%s chat=%s msg=%s",
                edict_id,
                decision_request_id,
                chat_id,
                message_id,
            )

    async def handle_button_click(self, action: FeishuCardAction) -> None:
        """Resolve a verified legacy Feishu button by durable decision ID.

        v1.1：仅处理审批专属 value（含 decision_request_id + action）；
              含 'command' 字段的按钮由 CardActionDispatcher 处理。
        """
        value = action.value or {}
        if "command" in value:
            # 这是 v1.1 通用协议按钮，本 handler 不处理
            # （FeishuBot._on_card 会路由到 CardActionDispatcher）
            return
        decision_request_id = value.get("decision_request_id")
        act = value.get("action")
        scope = value.get("scope")
        sender = action.sender_open_id
        if (
            not is_canonical_decision_request_id(decision_request_id)
            or act not in ("approve", "reject")
            or (act == "approve" and scope not in (None, "once", "edict", "always"))
            or not sender
            or not is_allowed_user(sender, self._settings.allowed_users)
        ):
            logger.warning("[feishu/card] malformed value=%s", value)
            return
        try:
            record = await self._approval.resolve_tool_decision(
                decision_request_id,
                action=act,
                grant_scope=scope if act == "approve" else None,
                auth=build_webhook_decision_auth(
                    channel="feishu",
                    instance_id=self._instance_id,
                    chat_id=action.chat_id,
                    sender_id=sender,
                    decision_request_id=decision_request_id,
                    correlation_prefix="approval-card",
                ),
            )
            winner = record.resolution.action if record.resolution is not None else "pending"
            logger.info(
                "[feishu/approval] resolved decision=%s winner=%s scope=%s",
                decision_request_id,
                winner,
                scope,
            )
        except (ValueError, RuntimeError) as e:
            # 没有 pending → 已被 web 端响应（幂等场景）
            logger.info("[feishu/card] submit_tool_decision skipped: %s", e)

    async def _on_decree_resolved(self, event: EventEnvelope) -> None:
        """web 或飞书响应 → 刷新另一侧（或本侧）卡片为"已响应"状态。"""
        payload = event.payload or {}
        decision_request_id = payload.get("decision_request_id")
        if not is_canonical_decision_request_id(decision_request_id):
            return
        pending = self._storage.pop_feishu_pending_card(decision_request_id)
        if not pending:
            return
        action = "approve" if event.event_type == "decree.approved" else "reject"
        actor = payload.get("actor") or ""
        source = (
            "飞书"
            if actor.startswith("feishu:")
            else "Telegram"
            if actor.startswith("telegram:")
            else "web"
        )
        tool_name = payload.get("tool_name", "")
        new_card = build_resolved_card(tool_name=tool_name, source=source, action=action)
        await self._outbound.update_card(pending["message_id"], new_card)
        logger.info(
            "[feishu/approval] card refreshed decision=%s source=%s action=%s",
            decision_request_id,
            source,
            action,
        )
