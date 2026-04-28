"""ApprovalCardHandler 单元测试 + build_approval_card / build_resolved_card。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.approval_card import (
    ApprovalCardHandler,
    build_approval_card,
    build_resolved_card,
)
from tianshu.gateway.feishu.dispatcher import FeishuCardAction
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope


def _settings(home_channel: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=("ou_test",), home_channel=home_channel,
        encrypt_key="", verification_token="", bot_open_id="", bot_name="",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_build_approval_card_contains_all_buttons():
    card = build_approval_card(
        memorial_id="m1", edict_id="e1234567890",
        tool_name="shell_exec",
        args_summary={"cmd": "ls", "cwd": "/tmp"},
        reason="auto_block",
    )
    assert card["header"]["template"] == "orange"
    # 4 个按钮
    actions = card["elements"][1]["actions"]
    assert len(actions) == 4
    scopes = [a.get("value", {}).get("scope") for a in actions if a.get("value", {}).get("action") == "approve"]
    assert set(scopes) == {"once", "edict", "always"}


def test_build_resolved_card():
    c = build_resolved_card(tool_name="bash", source="飞书", action="approve")
    assert c["header"]["template"] == "grey"
    assert "已批准" in c["header"]["title"]["content"]


@pytest.fixture
def handler(storage):
    bus = EventBus(storage=storage)
    approval = MagicMock()
    approval.submit_tool_decision = AsyncMock()
    outbound = MagicMock()
    outbound.send_card = AsyncMock(return_value="msg_card_1")
    outbound.update_card = AsyncMock(return_value=True)
    h = ApprovalCardHandler(
        settings=_settings(home_channel="oc_home"),
        storage=storage,
        event_bus=bus,
        approval_manager=approval,
        outbound=outbound,
    )
    return h, bus, outbound, approval


@pytest.mark.asyncio
async def test_on_approval_required_sends_card(handler, storage):
    h, _, outbound, _ = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id, memorial_id="mem_1",
        payload={"tool_name": "shell_exec", "reason": "block",
                 "args_summary": {"cmd": "rm -rf /"}},
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_awaited_once()
    pending = storage.pop_feishu_pending_card("mem_1")
    assert pending and pending["chat_id"] == "oc_x"
    assert pending["message_id"] == "msg_card_1"


@pytest.mark.asyncio
async def test_on_approval_required_falls_back_to_home(handler, storage):
    """edict.metadata 无 chat_id → 用 home_channel。"""
    h, _, outbound, _ = handler
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id, memorial_id="mem_2",
        payload={"tool_name": "x", "reason": "y"},
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_awaited_once()
    args, kwargs = outbound.send_card.await_args
    assert args[0] == "oc_home"


@pytest.mark.asyncio
async def test_on_approval_required_skipped_when_no_chat(storage):
    """edict.metadata 无 chat_id 且 home_channel 为空 → 不下发卡片（兜底 web 端）。"""
    bus = EventBus(storage=storage)
    approval = MagicMock()
    outbound = MagicMock()
    outbound.send_card = AsyncMock()
    h = ApprovalCardHandler(
        settings=_settings(home_channel=""),  # 无兜底
        storage=storage, event_bus=bus,
        approval_manager=approval, outbound=outbound,
    )
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id, memorial_id="m_skip",
        payload={"tool_name": "x", "reason": "y"},
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_button_click_calls_submit(handler):
    h, _, _, approval = handler
    action = FeishuCardAction(
        event_id="evt", chat_id="oc_x", sender_open_id="ou_test",
        value={"memorial_id": "m1", "action": "approve", "scope": "once"},
    )
    await h.handle_button_click(action)
    approval.submit_tool_decision.assert_awaited_once_with(
        memorial_id="m1", action="approve", grant_scope="once",
        actor="feishu:ou_test",
    )


@pytest.mark.asyncio
async def test_handle_button_click_idempotent_when_already_resolved(handler):
    """已被 web 端响应（ApprovalManager 抛 ValueError）→ 静默忽略。"""
    h, _, _, approval = handler
    approval.submit_tool_decision = AsyncMock(side_effect=ValueError("not pending"))
    action = FeishuCardAction(
        event_id="e", chat_id="c", sender_open_id="ou_test",
        value={"memorial_id": "m1", "action": "approve", "scope": "once"},
    )
    # 不应抛异常
    await h.handle_button_click(action)


@pytest.mark.asyncio
async def test_handle_button_click_malformed_value(handler):
    """value 缺字段 → 静默丢弃。"""
    h, _, _, approval = handler
    action = FeishuCardAction(
        event_id="e", chat_id="c", sender_open_id="ou_test",
        value={"action": "approve"},  # 缺 memorial_id
    )
    await h.handle_button_click(action)
    approval.submit_tool_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decree_resolved_refreshes_card(handler, storage):
    """决策落下 → pending 卡片刷新为"已响应"。"""
    h, _, outbound, _ = handler
    storage.save_feishu_pending_card(
        approval_id="m1", chat_id="oc_x", message_id="msg_X", kind="tool.approval_required",
    )
    event = EventEnvelope(
        event_type="decree.approved", memorial_id="m1",
        payload={"tool_name": "shell_exec", "actor": "web"},
    )
    await h._on_decree_resolved(event)
    outbound.update_card.assert_awaited_once()
    args, kwargs = outbound.update_card.await_args
    assert args[0] == "msg_X"


@pytest.mark.asyncio
async def test_on_decree_resolved_no_pending_skips(handler):
    """无 pending 卡片 → 不调 update_card。"""
    h, _, outbound, _ = handler
    event = EventEnvelope(
        event_type="decree.approved", memorial_id="m_unknown",
        payload={"tool_name": "x"},
    )
    await h._on_decree_resolved(event)
    outbound.update_card.assert_not_awaited()
