"""出站审批卡片 + 入站 card.action.trigger 处理 + 双通道作废。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.feishu.dispatcher import FeishuCardAction
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.gateway.feishu.outbound import FeishuOutbound

logger = logging.getLogger(__name__)


def build_approval_card(
    *,
    memorial_id: str,
    edict_id: str,
    tool_name: str,
    args_summary: dict | None,
    reason: str,
) -> dict:
    """构造审批卡片 payload。按钮 value 包含 memorial_id（v1 用 memorial_id 作 approval 标识）。"""
    summary_lines = []
    if args_summary:
        for k, v in list(args_summary.items())[:5]:
            summary_lines.append(f"- **{k}**：`{v}`")
    summary_md = "\n".join(summary_lines) or "_(无参数摘要)_"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"🛡️ 审批：{tool_name}"},
        },
        "elements": [
            {"tag": "markdown",
             "content": f"**敕令** `#{edict_id[:8]}`\n**原因**：{reason}\n\n{summary_md}"},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "✅ 单次允许"},
                 "type": "primary",
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "once"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🔄 本敕令允许"},
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "edict"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "♾️ 总是允许"},
                 "value": {"memorial_id": memorial_id, "action": "approve", "scope": "always"}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                 "type": "danger",
                 "value": {"memorial_id": memorial_id, "action": "reject"}},
            ]},
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
        outbound: "FeishuOutbound",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._approval = approval_manager
        self._outbound = outbound

    def start(self) -> None:
        """订阅 EventBus：tool.approval_required → 下发卡片；decree.* → 刷新。

        注意：tool.approval_required 在 PolicyHook 内 fire 到 EventBus（修订 1）。
        """
        self._event_bus.on("tool.approval_required", self._on_approval_required, priority=200)
        self._event_bus.on("decree.approved", self._on_decree_resolved, priority=200)
        self._event_bus.on("decree.rejected", self._on_decree_resolved, priority=200)

    async def _on_approval_required(self, event: EventEnvelope) -> None:
        """tool.approval_required → 找 chat_id → 下发卡片 → 记录 pending_card。"""
        edict_id = event.edict_id
        memorial_id = event.memorial_id
        if not (edict_id and memorial_id):
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            return
        chat_id = (edict.metadata or {}).get("chat_id") or self._settings.home_channel
        if not chat_id:
            logger.debug("[feishu/approval] no chat_id for edict %s, skip card", edict_id)
            return
        payload = event.payload or {}
        card = build_approval_card(
            memorial_id=memorial_id,
            edict_id=edict_id,
            tool_name=payload.get("tool_name", "unknown"),
            args_summary=payload.get("args_summary"),
            reason=payload.get("reason", ""),
        )
        message_id = await self._outbound.send_card(chat_id, card)
        if message_id:
            self._storage.save_feishu_pending_card(
                approval_id=memorial_id, chat_id=chat_id,
                message_id=message_id, kind="tool.approval_required",
            )
            logger.info("[feishu/approval] card sent edict=%s memorial=%s chat=%s msg=%s",
                        edict_id, memorial_id, chat_id, message_id)

    async def handle_button_click(self, action: FeishuCardAction) -> None:
        """入站按钮点击 → submit_tool_decision。"""
        value = action.value or {}
        memorial_id = value.get("memorial_id")
        act = value.get("action")
        scope = value.get("scope")
        if not (memorial_id and act in ("approve", "reject")):
            logger.warning("[feishu/card] malformed value=%s", value)
            return
        try:
            await self._approval.submit_tool_decision(
                memorial_id=memorial_id,
                action=act,
                grant_scope=scope if act == "approve" else None,
                actor=f"feishu:{action.sender_open_id}",
            )
            logger.info("[feishu/approval] resolved memorial=%s action=%s scope=%s",
                        memorial_id, act, scope)
        except ValueError as e:
            # 没有 pending → 已被 web 端响应（幂等场景）
            logger.info("[feishu/card] submit_tool_decision skipped: %s", e)

    async def _on_decree_resolved(self, event: EventEnvelope) -> None:
        """web 或飞书响应 → 刷新另一侧（或本侧）卡片为"已响应"状态。"""
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        pending = self._storage.pop_feishu_pending_card(memorial_id)
        if not pending:
            return
        action = "approve" if event.event_type == "decree.approved" else "reject"
        payload = event.payload or {}
        actor = payload.get("actor") or ""
        # actor 缺失时默认源 = "web"（修订 4：兼容旧路径）
        source = "飞书" if actor.startswith("feishu:") else "web"
        tool_name = payload.get("tool_name", "")
        new_card = build_resolved_card(tool_name=tool_name, source=source, action=action)
        await self._outbound.update_card(pending["message_id"], new_card)
        logger.info("[feishu/approval] card refreshed memorial=%s source=%s action=%s",
                    memorial_id, source, action)
