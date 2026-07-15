"""ApprovalCardHandler 单元测试 + build_approval_card / build_resolved_card。"""

from __future__ import annotations

from types import SimpleNamespace
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
from tianshu.models.principal import AuthenticationSource, ClientKind, PrincipalKind

_DECISION_1 = "01J00000000000000000000000"
_DECISION_2 = "01J00000000000000000000001"


def _record(*, action: str = "approve", actor: str = "feishu:feishu-default:ou_test"):
    return SimpleNamespace(
        request=SimpleNamespace(kind="tool", payload={"tool_name": "shell_exec"}),
        resolution=SimpleNamespace(
            action=action,
            actor_principal_id=actor,
            payload={"grant_scope": "once"},
        ),
    )


def _settings(home_channel: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x",
        app_secret="y",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=("ou_test",),
        home_channel=home_channel,
        encrypt_key="",
        verification_token="",
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.6,
        dedup_cache_size=2048,
    )


def test_build_approval_card_is_markdown_with_bilingual_hints():
    """v2: 卡片改为纯 markdown（飞书 ws 不支持卡片回调）；提示同时含中英命令。"""
    card = build_approval_card(
        decision_request_id=_DECISION_1,
        memorial_id="m1abc234",
        edict_id="e1234567890",
        tool_name="shell_exec",
        args_summary={"cmd": "ls", "cwd": "/tmp"},
        reason="auto_block",
    )
    assert card["header"]["template"] == "orange"
    # 不应有 action / button 元素
    has_action = any(el.get("tag") == "action" for el in card["elements"])
    assert not has_action
    md = card["elements"][0]["content"]
    # 中英命令双向提示
    for token in ("/approve", "/准", "/reject", "/驳", "edict", "always", "敕", "永"):
        assert token in md
    # 含 memorial 短 ID 引导（多 pending 时使用）
    assert _DECISION_1[:8] in md


def test_build_resolved_card():
    c = build_resolved_card(tool_name="bash", source="飞书", action="approve")
    assert c["header"]["template"] == "grey"
    assert "已批准" in c["header"]["title"]["content"]


def test_pending_command_list_excludes_delivery_placeholder(storage):
    assert storage.claim_feishu_pending_card(
        approval_id=_DECISION_1,
        instance_id="feishu-default",
        chat_id="oc_x",
        kind="tool.approval_required",
    )
    assert storage.list_feishu_pending_for_chat("oc_x") == []

    assert storage.finalize_feishu_pending_card(
        approval_id=_DECISION_1,
        instance_id="feishu-default",
        chat_id="oc_x",
        message_id="msg-1",
    )
    assert storage.list_feishu_pending_for_chat("oc_x") == [_DECISION_1]


@pytest.fixture
def handler(storage):
    bus = EventBus()
    approval = MagicMock()
    approval.resolve_tool_decision = AsyncMock(return_value=_record())
    approval.get_tool_decision = MagicMock(return_value=None)
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
        edict_id=edict.id,
        memorial_id="mem_1",
        payload={
            "decision_request_id": _DECISION_1,
            "tool_name": "shell_exec",
            "reason": "block",
            "args_summary": {"cmd": "rm -rf /"},
        },
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_awaited_once()
    pending = storage.pop_feishu_pending_card(_DECISION_1)
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
        edict_id=edict.id,
        memorial_id="mem_2",
        payload={"decision_request_id": _DECISION_1, "tool_name": "x", "reason": "y"},
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_awaited_once()
    args, kwargs = outbound.send_card.await_args
    assert args[0] == "oc_home"


@pytest.mark.asyncio
async def test_on_approval_required_skipped_when_no_chat(storage):
    """edict.metadata 无 chat_id 且 home_channel 为空 → 不下发卡片（兜底 web 端）。"""
    bus = EventBus()
    approval = MagicMock()
    outbound = MagicMock()
    outbound.send_card = AsyncMock()
    h = ApprovalCardHandler(
        settings=_settings(home_channel=""),  # 无兜底
        storage=storage,
        event_bus=bus,
        approval_manager=approval,
        outbound=outbound,
    )
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id,
        memorial_id="m_skip",
        payload={"decision_request_id": _DECISION_1, "tool_name": "x", "reason": "y"},
    )
    await h._on_approval_required(event)
    outbound.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_button_click_resolves_canonical_decision_with_webhook_auth(handler, storage):
    h, _, _, approval = handler
    storage.save_feishu_pending_card(
        approval_id=_DECISION_1,
        chat_id="oc_x",
        message_id="msg-card",
        kind="tool.approval_required",
    )
    action = FeishuCardAction(
        event_id="evt",
        chat_id="oc_x",
        message_id="msg-card",
        sender_open_id="ou_test",
        value={
            "decision_request_id": _DECISION_1,
            "action": "approve",
            "scope": "once",
            "actor": "forged",
        },
    )
    await h.handle_button_click(action)
    approval.resolve_tool_decision.assert_awaited_once_with(
        _DECISION_1,
        action="approve",
        grant_scope="once",
        auth=approval.resolve_tool_decision.await_args.kwargs["auth"],
    )
    auth = approval.resolve_tool_decision.await_args.kwargs["auth"]
    assert auth.principal.id == "feishu:feishu-default:ou_test"
    assert auth.principal.kind is PrincipalKind.WEBHOOK
    assert auth.principal.scopes == frozenset({"decision:resolve"})
    assert auth.source is AuthenticationSource.WEBHOOK
    assert auth.client_kind is ClientKind.WEBHOOK
    assert auth.correlation_id.startswith("approval-card:")


@pytest.mark.asyncio
async def test_handle_button_click_idempotent_when_already_resolved(handler, storage):
    """已被 web 端响应（ApprovalManager 抛 ValueError）→ 静默忽略。"""
    h, _, _, approval = handler
    approval.resolve_tool_decision = AsyncMock(side_effect=ValueError("not pending"))
    storage.save_feishu_pending_card(
        approval_id=_DECISION_1,
        chat_id="c",
        message_id="msg-card",
        kind="tool.approval_required",
    )
    action = FeishuCardAction(
        event_id="e",
        chat_id="c",
        message_id="msg-card",
        sender_open_id="ou_test",
        value={"decision_request_id": _DECISION_1, "action": "approve", "scope": "once"},
    )
    # 不应抛异常
    await h.handle_button_click(action)


@pytest.mark.asyncio
async def test_handle_button_click_malformed_value(handler):
    """value 缺字段 → 静默丢弃。"""
    h, _, _, approval = handler
    action = FeishuCardAction(
        event_id="e",
        chat_id="c",
        sender_open_id="ou_test",
        value={"action": "approve"},  # 缺 decision_request_id
    )
    await h.handle_button_click(action)
    approval.resolve_tool_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decree_resolved_refreshes_card(handler, storage):
    """决策落下 → pending 卡片刷新为"已响应"。"""
    h, _, outbound, approval = handler
    approval.get_tool_decision.return_value = _record()
    storage.save_feishu_pending_card(
        approval_id=_DECISION_1,
        chat_id="oc_x",
        message_id="msg_X",
        kind="tool.approval_required",
    )
    event = EventEnvelope(
        event_type="decree.approved",
        memorial_id="m1",
        payload={
            "decision_request_id": _DECISION_1,
            "tool_name": "shell_exec",
            "actor": "web",
        },
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
        event_type="decree.approved",
        memorial_id="m_unknown",
        payload={"tool_name": "x"},
    )
    await h._on_decree_resolved(event)
    outbound.update_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_decision_id_is_not_actionable_and_does_not_pop_other_card(handler, storage):
    h, _, outbound, _ = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    storage.save_feishu_pending_card(
        approval_id=_DECISION_2,
        chat_id="oc_x",
        message_id="msg-other",
        kind="tool.approval_required",
    )

    await h._on_approval_required(
        EventEnvelope(
            event_type="tool.approval_required",
            edict_id=edict.id,
            memorial_id="same-memorial",
            payload={"tool_name": "shell_exec"},
        )
    )
    await h._on_decree_resolved(
        EventEnvelope(
            event_type="decree.approved",
            memorial_id="same-memorial",
            payload={"tool_name": "shell_exec"},
        )
    )

    outbound.send_card.assert_not_awaited()
    outbound.update_card.assert_not_awaited()
    assert storage.pop_feishu_pending_card(_DECISION_2) is not None


@pytest.mark.asyncio
async def test_same_memorial_decisions_keep_independent_pending_cards(handler, storage):
    h, _, outbound, approval = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    outbound.send_card.side_effect = ["msg-1", "msg-2"]

    for decision_id in (_DECISION_1, _DECISION_2):
        await h._on_approval_required(
            EventEnvelope(
                event_type="tool.approval_required",
                edict_id=edict.id,
                memorial_id="same-memorial",
                payload={"decision_request_id": decision_id, "tool_name": "shell_exec"},
            )
        )

    approval.get_tool_decision.return_value = _record()
    await h._on_decree_resolved(
        EventEnvelope(
            event_type="decree.approved",
            memorial_id="same-memorial",
            payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
        )
    )

    assert storage.pop_feishu_pending_card(_DECISION_1) is None
    assert storage.pop_feishu_pending_card(_DECISION_2) is not None


@pytest.mark.asyncio
async def test_replayed_approval_required_sends_one_card(handler, storage):
    h, _, outbound, _ = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id,
        memorial_id="memorial",
        payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
    )

    await h._on_approval_required(event)
    await h._on_approval_required(event)

    outbound.send_card.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_card_delivery_releases_claim_for_retry(handler, storage):
    h, _, outbound, _ = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id,
        memorial_id="memorial",
        payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
    )
    outbound.send_card.side_effect = [RuntimeError("delivery failed"), "msg-retry"]

    with pytest.raises(RuntimeError, match="delivery failed"):
        await h._on_approval_required(event)
    await h._on_approval_required(event)

    assert outbound.send_card.await_count == 2
    assert storage.get_feishu_pending_card(_DECISION_1) is not None


@pytest.mark.asyncio
async def test_button_must_match_pending_instance_chat_and_message(handler, storage):
    h, _, _, approval = handler
    storage.save_feishu_pending_card(
        approval_id=_DECISION_1,
        chat_id="oc_other",
        message_id="msg-other",
        kind="tool.approval_required",
        instance_id="feishu-other",
    )
    action = FeishuCardAction(
        event_id="evt",
        chat_id="oc_x",
        message_id="msg-forged",
        sender_open_id="ou_test",
        value={"decision_request_id": _DECISION_1, "action": "approve", "scope": "once"},
    )

    await h.handle_button_click(action)

    approval.resolve_tool_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolved_event_uses_durable_winner_and_current_instance(handler, storage):
    h, _, outbound, approval = handler
    approval.get_tool_decision.return_value = _record(
        action="approve", actor="telegram:telegram-a:7"
    )
    storage.save_feishu_pending_card(
        approval_id=_DECISION_1,
        chat_id="oc_x",
        message_id="msg-current",
        kind="tool.approval_required",
        instance_id="feishu-default",
    )
    storage.save_feishu_pending_card(
        approval_id=_DECISION_2,
        chat_id="oc_other",
        message_id="msg-other",
        kind="tool.approval_required",
        instance_id="feishu-other",
    )

    await h._on_decree_resolved(
        EventEnvelope(
            event_type="decree.approved",
            memorial_id="memorial",
            payload={
                "decision_request_id": _DECISION_1,
                "tool_name": "forged",
                "actor": "web:forged",
            },
        )
    )

    card = outbound.update_card.await_args.args[1]
    assert "Telegram" in card["elements"][0]["content"]
    assert storage.pop_feishu_pending_card(_DECISION_2, instance_id="feishu-other") is not None


@pytest.mark.asyncio
async def test_resolution_during_card_send_is_refreshed_after_finalize(handler, storage):
    h, _, outbound, approval = handler
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    resolved_event = EventEnvelope(
        event_type="decree.approved",
        memorial_id="memorial",
        payload={"decision_request_id": _DECISION_1},
    )

    async def resolve_while_sending(*_args, **_kwargs):
        approval.get_tool_decision.return_value = _record()
        await h._on_decree_resolved(resolved_event)
        return "msg-race"

    outbound.send_card.side_effect = resolve_while_sending
    await h._on_approval_required(
        EventEnvelope(
            event_type="tool.approval_required",
            edict_id=edict.id,
            memorial_id="memorial",
            payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
        )
    )

    outbound.update_card.assert_awaited_once()
    assert outbound.update_card.await_args.args[0] == "msg-race"
    assert storage.get_feishu_pending_card(_DECISION_1) is None


@pytest.mark.asyncio
async def test_unbound_edict_can_deliver_once_per_configured_instance(storage):
    approval = MagicMock()
    approval.get_tool_decision = MagicMock(return_value=None)
    outbound_a = MagicMock()
    outbound_a.send_card = AsyncMock(return_value="msg-a")
    outbound_a.update_card = AsyncMock(return_value=True)
    outbound_b = MagicMock()
    outbound_b.send_card = AsyncMock(return_value="msg-b")
    outbound_b.update_card = AsyncMock(return_value=True)
    handler_a = ApprovalCardHandler(
        settings=_settings(home_channel="oc-a"),
        storage=storage,
        event_bus=EventBus(),
        approval_manager=approval,
        outbound=outbound_a,
        instance_id="feishu-a",
    )
    handler_b = ApprovalCardHandler(
        settings=_settings(home_channel="oc-b"),
        storage=storage,
        event_bus=EventBus(),
        approval_manager=approval,
        outbound=outbound_b,
        instance_id="feishu-b",
    )
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="tool.approval_required",
        edict_id=edict.id,
        memorial_id="memorial",
        payload={"decision_request_id": _DECISION_1, "tool_name": "shell_exec"},
    )

    await handler_a._on_approval_required(event)
    await handler_b._on_approval_required(event)

    outbound_a.send_card.assert_awaited_once()
    outbound_b.send_card.assert_awaited_once()
    assert storage.get_feishu_pending_card(_DECISION_1, instance_id="feishu-a") is not None
    assert storage.get_feishu_pending_card(_DECISION_1, instance_id="feishu-b") is not None
